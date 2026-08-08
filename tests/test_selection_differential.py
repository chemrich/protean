"""Differential tests: our MolScript vs Mol*'s bundled PyMOL transpiler.

Both compile the same PyMOL selection; both are evaluated by the same Mol*
query engine against 4HHB. Where they agree, we have a regression signal for
free. Where they disagree, the divergence is asserted explicitly — those are
the cases from PLAN.md decision 5 where the bundled transpiler silently
answers nothing.

Absolute expected counts are asserted too, so that a *mutual* failure (both
returning 0) cannot masquerade as agreement.

Requires a real browser and is opt-in:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_selection_differential.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import aiohttp
import pytest

from protean_mcp.connection import ViewerBridge
from protean_mcp.fetch import fetch_structure_data
from protean_mcp.selections import to_molscript

from .conftest import free_port


def _find_chrome() -> str | None:
    """Locate a Chrome binary: explicit override, then the usual suspects.

    CI runners are Linux and have no /Applications, so the macOS path alone
    would silently skip the whole suite there.
    """
    override = os.environ.get("PROTEAN_CHROME")
    if override:
        return override if Path(override).exists() else None
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return next((c for c in candidates if Path(c).exists()), None)


CHROME = _find_chrome()

# Headless CI needs software WebGL; locally we want a real window.
CHROME_FLAGS = [f for f in os.environ.get("PROTEAN_CHROME_FLAGS", "").split(" ") if f]
STATIC = Path(__file__).resolve().parents[1] / "src" / "protean_mcp" / "static"
FIXTURE = "4hhb"

# Structure classes whose entity typing differs enough to break selectors that
# look fine on a plain globular protein. Glycans in particular are a separate
# mmCIF entity type ("branched"), not non-polymer.
CLASS_FIXTURES: dict[str, dict[str, str]] = {
    "1bna": {  # B-DNA dodecamer: no protein at all
        "nucleic": "nonzero",
        "protein": "zero",
        "polymer": "nonzero",
        "backbone": "zero",  # our backbone is protein N/CA/C/O
    },
    "5fji": {  # glycoprotein: branched oligosaccharide entities
        "protein": "nonzero",
        "glycan": "nonzero",
        "nucleic": "zero",
    },
    "1ca2": {  # carbonic anhydrase: a single catalytic Zn
        "metals": "nonzero",
        "protein": "nonzero",
        "ion": "nonzero",
        "glycan": "zero",
    },
}

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("PROTEAN_DIFFERENTIAL") != "1",
        reason="needs a browser; set PROTEAN_DIFFERENTIAL=1 to run",
    ),
    pytest.mark.skipif(CHROME is None, reason="no Chrome binary found"),
    pytest.mark.skipif(
        not (STATIC / "index.html").exists(),
        reason="viewer not built (npm run build in viewer/)",
    ),
]

# Ground truth on 4HHB, cross-checked by hand:
#   4 protein chains + 4 HEM + 221 waters = 4779 atoms
#   574 residues x 4 backbone atoms = 2296
EXPECTED: dict[str, int] = {
    "all": 4779,
    "none": 0,
    "polymer": 4384,
    "solvent": 221,
    "hetatm": 395,
    "organic": 172,
    "inorganic": 2,
    "chain A": 1168,
    "chain A or chain B": 2392,
    "not chain A": 3611,
    "resi 50": 34,
    "resi 50-60": 308,
    "resi 50+60+70": 90,
    "resi 50-60 and chain A": 77,
    "name CA": 574,
    "name CA+CB": 1108,
    "elem C": 2954,
    "elem Fe": 4,
    "resn HEM": 172,
    "not solvent": 4558,
    "backbone": 2296,
    "sidechain": 2088,
    "hydro": 0,
    "b > 50": 271,
    "b < 20": 2284,
    "chain A and not backbone": 604,
    "byres (chain A within 4 of chain B)": 130,
    "(chain A or chain B) and resi 1-50 and polymer": 782,
}

# Selections where the bundled transpiler is wrong. Value is the correct count;
# the test asserts we produce it AND that the transpiler does not.
DIVERGENCES: dict[str, int] = {
    "metals": 4,  # their keyword table has a @desc but no implementation
    "chain A and not hydro": 1168,  # their `not` collapses on an empty operand
    "within 5 of resn HEM": 535,  # they require an explicit left operand
    "first chain A": 1,  # silently 0
    "bychain resi 50": 4384,  # silently 0
    # Prefix-operator precedence. PyMOL binds `byres` tighter than `and`, so
    # this is (byres X) and Y = 295. The transpiler swallows the `and` across
    # the parenthesis boundary and computes byres (X and Y) = 502 — which is
    # the right answer to a different question. Confirmed by the disambiguated
    # control above, where both engines return 295.
    "byres (polymer within 4 of resn HEM) and sidechain": 295,
    # ...and when you write that other question explicitly, they return 0.
    "byres ((polymer within 4 of resn HEM) and sidechain)": 502,
}

# Cross-checked against the transpiler but without an independently derived
# count. Useful for isolating where a composite selection starts to drift.
AGREEMENT_ONLY: tuple[str, ...] = (
    "polymer within 4 of resn HEM",
    "byres (polymer within 4 of resn HEM)",
    "chain A within 5 of resn HEM",
    "resn HEM expand 5",
    "byres name CA",
    # Fully disambiguated, both engines agree — the control for the precedence
    # divergence below.
    "(byres (polymer within 4 of resn HEM)) and sidechain",
)

CORPUS = list(EXPECTED) + list(AGREEMENT_ONLY) + list(DIVERGENCES)

_EVAL_JS = r"""(async () => {
  const p = window.__protean.plugin;
  const struct = p.managers.structure.hierarchy.current.structures[0];
  const ref = struct.cell.transform.ref;
  const out = [];
  for (const [key, language, expression] of %s) {
    const entry = { key };
    let sel = null;
    try {
      sel = await p.builders.structure.tryCreateComponent(ref, {
        type: { name: 'script', params: { language, expression } },
        nullIfEmpty: false, label: 'diff',
      }, 'diff-key');
      const data = sel && (sel.data || (sel.cell && sel.cell.obj && sel.cell.obj.data));
      if (data) entry.count = data.elementCount;
      else if (sel && sel.cell && sel.cell.status === 'error')
        entry.error = String(sel.cell.errorText).slice(0, 200);
      else entry.count = 0;
    } catch (err) {
      entry.error = String(err && err.message ? err.message : err).slice(0, 200);
    }
    try { if (sel && sel.ref) await p.state.data.build().delete(sel.ref).commit(); }
    catch (err) { /* leave the tree alone; next iteration overwrites */ }
    out.push(entry);
  }
  return JSON.stringify(out);
})()"""


async def _cdp_eval(port: int, url: str, expression: str):
    async with aiohttp.ClientSession() as session:
        for _ in range(60):
            try:
                async with session.get(f"http://127.0.0.1:{port}/json") as resp:
                    targets = await resp.json()
            except Exception:
                await asyncio.sleep(0.3)
                continue
            pages = [
                t
                for t in targets
                if t.get("type") == "page"
                and t.get("url", "").rstrip("/") == url.rstrip("/")
            ]
            if pages:
                break
            await asyncio.sleep(0.3)
        else:
            raise RuntimeError("viewer page never appeared on the CDP endpoint")

        async with session.ws_connect(
            pages[0]["webSocketDebuggerUrl"], max_msg_size=64 * 1024 * 1024
        ) as ws:
            await ws.send_json(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
            async for msg in ws:
                payload = json.loads(msg.data)
                if payload.get("id") == 1:
                    result = payload.get("result", {}).get("result", {})
                    if "value" not in result:
                        raise RuntimeError(f"CDP evaluate failed: {str(payload)[:400]}")
                    return json.loads(result["value"])
    raise RuntimeError("no CDP reply")


async def _evaluate(pdb_id: str, cases: list[list[str]]) -> dict[str, dict[str, object]]:
    """Load *pdb_id* in a throwaway browser and evaluate every case in it."""
    structure = await fetch_structure_data(pdb_id)
    bridge = ViewerBridge(port=free_port(), static_dir=STATIC)
    viewer_port = await bridge.start()
    url = f"http://127.0.0.1:{viewer_port}/"
    cdp_port = free_port()
    profile = tempfile.mkdtemp(prefix="protean-diff-")

    chrome = CHROME
    assert chrome is not None  # guaranteed by the module-level skipif
    proc = subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={cdp_port}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await bridge.wait_for_viewer(40)
        await bridge.request(
            "load_structure",
            {"name": pdb_id, "format": structure.format, "data": structure.data},
            timeout=120,
        )
        raw = await _cdp_eval(cdp_port, url, _EVAL_JS % json.dumps(cases))
        return {entry["key"]: entry for entry in raw}
    finally:
        proc.terminate()
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={profile}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await bridge.stop()
        shutil.rmtree(profile, ignore_errors=True)


@pytest.fixture(scope="module")
async def counts() -> dict[str, dict[str, object]]:
    """Every corpus selection, compiled both ways, evaluated on 4HHB."""
    cases: list[list[str]] = []
    for selection in CORPUS:
        cases.append([f"ours::{selection}", "mol-script", to_molscript(selection)])
        cases.append([f"theirs::{selection}", "pymol", selection])
    return await _evaluate(FIXTURE, cases)


@pytest.fixture(scope="module")
async def class_counts() -> dict[str, dict[str, dict[str, object]]]:
    """Class-specific selectors evaluated on one fixture per structure class."""
    out: dict[str, dict[str, dict[str, object]]] = {}
    for pdb_id, expectations in CLASS_FIXTURES.items():
        cases = [
            [f"ours::{selection}", "mol-script", to_molscript(selection)]
            for selection in expectations
        ]
        out[pdb_id] = await _evaluate(pdb_id, cases)
    return out


def _count(counts, prefix: str, selection: str) -> int:
    entry = counts[f"{prefix}::{selection}"]
    if "error" in entry:
        pytest.fail(f"{prefix} '{selection}' errored: {entry['error']}")
    count: int = entry["count"]
    return count


@pytest.mark.parametrize("selection", sorted(EXPECTED))
async def test_matches_ground_truth(counts, selection):
    """Our compiler produces the hand-checked atom count."""
    assert _count(counts, "ours", selection) == EXPECTED[selection]


@pytest.mark.parametrize("selection", sorted(EXPECTED) + sorted(AGREEMENT_ONLY))
async def test_agrees_with_bundled_transpiler(counts, selection):
    """Independent implementations agreeing is a strong regression signal."""
    assert _count(counts, "ours", selection) == _count(counts, "theirs", selection)


@pytest.mark.parametrize("selection", sorted(DIVERGENCES))
async def test_beats_bundled_transpiler(counts, selection):
    """Cases where Mol*'s transpiler is silently wrong and we are not.

    Asserting *both* halves keeps this honest: if upstream ever fixes one of
    these, the test fails and we retire the divergence rather than carrying a
    stale claim.
    """
    expected = DIVERGENCES[selection]
    ours = _count(counts, "ours", selection)
    theirs = _count(counts, "theirs", selection)
    assert ours == expected
    assert theirs != expected, (
        f"Mol*'s transpiler now returns {theirs} for {selection!r}; "
        "move it out of DIVERGENCES"
    )


@pytest.mark.parametrize(
    ("pdb_id", "selection", "expectation"),
    [(p, s, e) for p, sels in CLASS_FIXTURES.items() for s, e in sels.items()],
)
async def test_class_selectors(class_counts, pdb_id, selection, expectation):
    """Selectors that only a non-globular-protein structure can exercise.

    Entity typing is where these break: glycans are `branched`, not
    `non-polymer`, so a protein-only fixture would never catch it.
    """
    entry = class_counts[pdb_id][f"ours::{selection}"]
    if "error" in entry:
        pytest.fail(f"'{selection}' on {pdb_id} errored: {entry['error']}")
    count = entry["count"]
    if expectation == "nonzero":
        assert count > 0, f"'{selection}' found nothing in {pdb_id}"
    else:
        assert count == 0, f"'{selection}' unexpectedly matched {count} atoms in {pdb_id}"
