"""Server tool tests against the mock viewer."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom
from biotite.structure import array as atom_array

import protean_mcp.server as server_mod
from protean_mcp.analysis.electrostatics import read_dx
from protean_mcp.connection import ViewerError
from protean_mcp.handles import summarise
from protean_mcp.selections_numpy import load_structure, select_mask
from protean_mcp.server import (
    background,
    clear_viewer,
    color_by_conservation,
    color_by_potential,
    combine,
    conservation,
    electrostatics,
    fetch_structure,
    interface,
    load_session,
    mcp,
    opacity,
    save_session,
    screenshot,
    select,
    show,
)

# 1x1 transparent PNG
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


@pytest.fixture
def wired_bridge(bridge, viewer, monkeypatch):
    """Point the server module's global bridge at the test bridge."""
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    return viewer


async def test_tools_registered():
    tools = {t.name for t in await mcp.list_tools()}
    assert {"open_viewer", "fetch_structure", "screenshot", "clear_viewer"} <= tools


async def test_fetch_structure_tool(wired_bridge, tmp_path, monkeypatch):
    f = tmp_path / "toy.pdb"
    f.write_text(
        "ATOM      1  N   MET A   1      11.104   6.134  -6.504  1.00  0.00           N\n"
        "END\n"
    )
    loaded = {}

    def on_load(args):
        loaded.update(args)
        return {"loaded": args["name"]}

    wired_bridge.handlers["load_structure"] = on_load
    task = wired_bridge.serve(1)
    msg = await fetch_structure(str(f))
    await task
    assert "toy" in msg and "local file" in msg
    assert loaded["format"] == "pdb"


async def test_fetch_structure_without_viewer(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    with pytest.raises(ViewerError, match="No viewer connected"):
        await fetch_structure("1ubq")


async def test_clear_tool(wired_bridge):
    wired_bridge.handlers["clear"] = lambda args: {}
    task = wired_bridge.serve(1)
    assert "cleared" in (await clear_viewer()).lower()
    await task


async def test_screenshot_tool(wired_bridge, tmp_path):
    wired_bridge.handlers["screenshot"] = lambda args: {
        "data_uri": f"data:image/png;base64,{PNG_B64}"
    }
    task = wired_bridge.serve(1)
    out = tmp_path / "shot.png"
    result = await screenshot(path=str(out))
    await task
    assert out.read_bytes() == base64.b64decode(PNG_B64)
    assert any("Saved to" in str(item) for item in result)


# -- sessions ----------------------------------------------------------------


async def test_save_session_writes_a_gzipped_document(wired_bridge, tmp_path):
    wired_bridge.handlers["save_session"] = lambda args: {
        "snapshot": {"id": "snap", "data": "x" * 5000},
        "handles": {"prot": ["ref-1"], "hemes": ["ref-2"]},
    }
    task = wired_bridge.serve(1)
    out = await save_session(str(tmp_path / "demo"))
    await task

    written = Path(out["path"])
    assert written.name == "demo.protean"  # suffix supplied when omitted
    assert out["handles"] == ["hemes", "prot"]
    # Sessions are mostly embedded mmCIF; compression is the point.
    assert out["bytes"] < out["uncompressed_bytes"]

    document = json.loads(gzip.decompress(written.read_bytes()))
    assert document["format"] == "protean-session"
    assert document["version"] == 1
    assert document["molstar"]["id"] == "snap"
    assert document["handles"]["prot"] == ["ref-1"]


async def test_session_round_trip(wired_bridge, tmp_path):
    wired_bridge.handlers["save_session"] = lambda args: {
        "snapshot": {"id": "snap"},
        "handles": {"prot": ["ref-1"]},
    }
    sent = {}

    def on_load(args):
        sent.update(args)
        return {"restored": ["prot"], "dropped": [], "atom_count": 4779}

    wired_bridge.handlers["load_session"] = on_load

    task = wired_bridge.serve(2)
    saved = await save_session(str(tmp_path / "s.protean"))
    result = await load_session(saved["path"])
    await task

    assert sent["snapshot"] == {"id": "snap"}
    assert sent["handles"] == {"prot": ["ref-1"]}
    assert result["restored"] == ["prot"]
    assert result["atom_count"] == 4779


async def test_load_session_missing_file(wired_bridge, tmp_path):
    with pytest.raises(ViewerError, match="No session file"):
        await load_session(str(tmp_path / "nope.protean"))


async def test_load_session_rejects_non_gzip(wired_bridge, tmp_path):
    bad = tmp_path / "bad.protean"
    bad.write_text("this is not a session")
    with pytest.raises(ViewerError, match="not a readable protean session"):
        await load_session(str(bad))


async def test_load_session_rejects_foreign_format(wired_bridge, tmp_path):
    other = tmp_path / "other.protean"
    other.write_bytes(gzip.compress(json.dumps({"format": "pymol-session"}).encode()))
    with pytest.raises(ViewerError, match="not a protean session"):
        await load_session(str(other))


async def test_load_session_rejects_future_version(wired_bridge, tmp_path):
    future = tmp_path / "future.protean"
    future.write_bytes(
        gzip.compress(
            json.dumps(
                {"format": "protean-session", "version": 99, "molstar": {}}
            ).encode()
        )
    )
    with pytest.raises(ViewerError, match="session version 99"):
        await load_session(str(future))


async def test_unparseable_coordinates_still_load_but_flag_analysis(
    wired_bridge, tmp_path
):
    """Mol* renders files our analysis parser rejects. Display should survive;
    analysis should degrade visibly rather than silently matching nothing."""
    f = tmp_path / "broken.pdb"
    f.write_text("ATOM      1  N   MET A   1  garbage\nEND\n")
    wired_bridge.handlers["load_structure"] = lambda args: {"loaded": args["name"]}
    task = wired_bridge.serve(1)
    message = await fetch_structure(str(f))
    await task
    assert "analysis unavailable" in message

    with pytest.raises(ViewerError, match="could not be parsed for analysis"):
        await select("chain A")


def _pdb_line(
    serial: int,
    name: str,
    resname: str,
    chain: str,
    resseq: int,
    xyz: tuple[float, float, float],
    element: str,
) -> str:
    x, y, z = xyz
    return (
        f"ATOM  {serial:5d} {name:^4s} {resname:3s} {chain:1s}{resseq:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {element:>2s}"
    )


def _two_chain_pdb(path: Path) -> Path:
    """An aspartate facing an arginine across a chain boundary."""
    atoms = [
        (1, "CA", "ASP", "A", 1, (0.0, 0.0, 0.0), "C"),
        (2, "CG", "ASP", "A", 1, (1.5, 0.0, 0.0), "C"),
        (3, "OD1", "ASP", "A", 1, (2.2, 1.0, 0.0), "O"),
        (4, "OD2", "ASP", "A", 1, (2.2, -1.0, 0.0), "O"),
        (5, "CA", "ARG", "B", 2, (8.0, 0.0, 0.0), "C"),
        (6, "CZ", "ARG", "B", 2, (6.5, 0.0, 0.0), "C"),
        (7, "NH1", "ARG", "B", 2, (5.2, 1.0, 0.0), "N"),
        (8, "NH2", "ARG", "B", 2, (5.2, -1.0, 0.0), "N"),
    ]
    path.write_text("\n".join(_pdb_line(*a) for a in atoms) + "\nEND\n")
    return path


async def _load(wired_bridge, path: Path) -> None:
    wired_bridge.handlers["load_structure"] = lambda args: {"loaded": args["name"]}
    task = wired_bridge.serve(1)
    await fetch_structure(str(path))
    await task


async def test_interface_registers_handles_on_the_loaded_structure(
    wired_bridge, tmp_path
):
    """The point of the change: the computed set is addressable afterwards."""
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "pair.pdb"))

    shown: list[str] = []

    def on_select(args: dict[str, Any]) -> dict[str, Any]:
        shown.append(args["name"])
        return {}

    wired_bridge.handlers["select"] = on_select
    task = wired_bridge.serve(2)
    payload = await interface("A", "B")
    await task

    assert payload["handles"] == {"a": "iface_a", "b": "iface_b"}
    # The handles hold whole residues, not just the atoms that lost surface.
    assert len(server_mod._handles.get("iface_a")) == 4
    assert len(server_mod._handles.get("iface_b")) == 4
    # And the viewer was told about both, so they are visible as named sets.
    assert shown == ["iface_a", "iface_b"]


async def test_interface_handles_name_the_residues_that_were_reported(
    wired_bridge, tmp_path
):
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "pair.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await interface("A", "B")
    await task

    summary = summarise(server_mod._structure, server_mod._handles.get("iface_a").indices)
    assert summary["chains"] == ["A"]
    assert [r["comp"] for r in summary["residues"]] == ["ASP"]
    assert [(r["chain"], r["seq"]) for r in summary["residues"]] == [
        (r["chain"], r["seq"]) for r in payload["interface_residues_a"]
    ]


async def test_custom_handle_names_are_used(wired_bridge, tmp_path):
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "pair.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await interface("A", "B", name_a="epitope", name_b="paratope")
    await task

    assert payload["handles"] == {"a": "epitope", "b": "paratope"}
    assert "epitope" in server_mod._handles.names()


async def test_interface_on_a_different_structure_says_why_it_has_no_handles(
    wired_bridge, tmp_path
):
    """A missing key would read as 'no interface'. Say which claim is being made."""
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "loaded.pdb"))
    other = _two_chain_pdb(tmp_path / "other.pdb")

    payload = await interface("A", "B", identifier=str(other))

    assert payload["handles"] is None
    assert "not the loaded structure" in payload["handles_note"]
    assert server_mod._handles.names() == []
    # The analysis itself still ran.
    assert payload["buried_area"] > 0


async def test_naming_the_loaded_structure_still_registers_handles(
    wired_bridge, tmp_path
):
    """Passing the identifier explicitly must not lose the handles."""
    path = _two_chain_pdb(tmp_path / "pair.pdb")
    await _load(wired_bridge, path)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await interface("A", "B", identifier=str(path))
    await task
    assert payload["handles"] == {"a": "iface_a", "b": "iface_b"}


async def test_interface_without_a_structure_asks_for_one(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    monkeypatch.setattr(server_mod, "_structure", None)
    monkeypatch.setattr(server_mod, "_structure_error", None)
    with pytest.raises(ViewerError, match="No structure loaded"):
        await interface("A", "B")


def _tiny_protein_pdb(path: Path) -> Path:
    """Two glycines with a complete C-terminus, so pdb2pqr will charge them."""
    atoms = [
        (1, "N", "GLY", 1, (0.0, 0.0, 0.0), "N"),
        (2, "CA", "GLY", 1, (1.46, 0.0, 0.0), "C"),
        (3, "C", "GLY", 1, (2.0, 1.42, 0.0), "C"),
        (4, "O", "GLY", 1, (1.25, 2.39, 0.0), "O"),
        (5, "N", "GLY", 2, (3.33, 1.5, 0.0), "N"),
        (6, "CA", "GLY", 2, (4.0, 2.78, 0.0), "C"),
        (7, "C", "GLY", 2, (5.5, 2.65, 0.0), "C"),
        (8, "O", "GLY", 2, (6.1, 1.58, 0.0), "O"),
        (9, "OXT", "GLY", 2, (6.05, 3.75, 0.0), "O"),
    ]
    path.write_text(
        "\n".join(_pdb_line(s, n, r, "A", i, xyz, e) for s, n, r, i, xyz, e in atoms)
        + "\nEND\n"
    )
    return path


async def test_electrostatics_reports_which_method_ran(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    out = await electrostatics(
        method="coulombic", spacing=2.0, padding=6.0, path=str(tmp_path / "p.dx")
    )
    assert "screened Coulomb" in out["method"]
    assert out["units"] == "kT/e"
    assert "caveat" in out, "the approximation must carry its own caveat"
    assert out["charges"]["forcefield"] == "AMBER"
    assert Path(out["dx_path"]).is_file()


async def test_written_dx_is_readable_back(wired_bridge, tmp_path):
    """The file is the artifact the viewer will consume, so it has to parse."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    out = await electrostatics(
        method="coulombic", spacing=2.0, padding=6.0, path=str(tmp_path / "p.dx")
    )
    grid = read_dx(Path(out["dx_path"]).read_text())
    assert list(grid.shape) == out["grid_shape"]


async def test_unknown_method_is_refused(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="Unknown method"):
        await electrostatics(method="magic")


async def test_electrostatics_samples_a_handle_per_residue(wired_bridge, tmp_path):
    """Answers 'is this patch acidic?' with no rendering involved."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    await select("all", name="whole")
    await task

    out = await electrostatics(
        method="coulombic",
        spacing=1.0,
        padding=8.0,
        handle="whole",
        path=str(tmp_path / "p.dx"),
    )
    sampled = out["sampled"]
    assert sampled["handle"] == "whole"
    assert sampled["residues_sampled"] == 2
    assert sampled["atoms_outside_grid"] == 0
    assert {r["comp"] for r in sampled["most_negative"]} == {"GLY"}
    # Sorted by potential, so the two ends bracket each other.
    assert (
        sampled["most_negative"][0]["potential"]
        <= sampled["most_positive"][0]["potential"]
    )


async def test_sampling_an_unknown_handle_names_the_known_ones(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="No selection named"):
        await electrostatics(method="coulombic", spacing=2.0, handle="nope")


async def test_electrostatics_without_a_structure_asks_for_one(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    monkeypatch.setattr(server_mod, "_structure", None)
    monkeypatch.setattr(server_mod, "_structure_error", None)
    with pytest.raises(ViewerError, match="No structure loaded"):
        await electrostatics()


def _peptide_pdb(path: Path, n: int = 12) -> Path:
    """A single chain long enough to clear the minimum sequence length."""
    lines = []
    serial = 1
    for res in range(1, n + 1):
        for i, (name, elem) in enumerate(
            (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"))
        ):
            lines.append(
                _pdb_line(
                    serial, name, "ALA", "A", res, (float(res) * 4, float(i), 0.0), elem
                )
            )
            serial += 1
    path.write_text("\n".join(lines) + "\nEND\n")
    return path


def _fake_msa(monkeypatch, sequence_length: int, depth: int = 20) -> None:
    """Stand in for the MMseqs2 search: column 0 conserved, the rest varied."""
    query = "A" * sequence_length
    varied = "A" + "W" * (sequence_length - 1)
    a3m = f">query\n{query}\n" + "".join(
        f">h{i}\n{query if i % 2 else varied}\n" for i in range(depth)
    )

    async def fake_fetch(sequence, cache_dir, **kwargs):
        return a3m, "search"

    monkeypatch.setattr(server_mod, "_fetch_msa", fake_fetch)


async def test_conservation_registers_conserved_and_variable_handles(
    wired_bridge, tmp_path, monkeypatch
):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb"))
    _fake_msa(monkeypatch, sequence_length=12)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await conservation()
    await task

    assert payload["handles"] == {"conserved": "conserved", "variable": "variable"}
    assert payload["chain"] == "A"
    assert payload["residues_scored"] == 12
    assert payload["msa_depth"] == 21
    assert set(server_mod._handles.names()) >= {"conserved", "variable"}
    # Residue 1 is the invariant column, so it must land in the conserved set.
    conserved = summarise(
        server_mod._structure, server_mod._handles.get("conserved").indices
    )
    assert 1 in [r["seq"] for r in conserved["residues"]]


async def test_conservation_handles_compose_with_other_handles(
    wired_bridge, tmp_path, monkeypatch
):
    """The exit criterion's shape: the conserved part of another set."""
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb"))
    _fake_msa(monkeypatch, sequence_length=12)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(4)
    await conservation()
    await select("resi 1-4", name="patch")
    result = await combine("intersect", ["patch", "conserved"], "hot")
    await task
    assert result["atom_count"] > 0
    assert all(r["seq"] <= 4 for r in result["residues"])


async def test_conservation_without_a_structure_asks_for_one(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    monkeypatch.setattr(server_mod, "_structure", None)
    monkeypatch.setattr(server_mod, "_structure_error", None)
    with pytest.raises(ViewerError, match="No structure loaded"):
        await conservation()


@contextlib.asynccontextmanager
async def _serving(viewer, **handlers):
    """Answer whatever the viewer is asked, for as long as the block runs.

    serve(n) blocks forever if fewer than n requests arrive, and the exact
    count for a multi-step tool is an implementation detail no test should
    have to predict.
    """
    for action, handler in handlers.items():
        viewer.handlers[action] = handler
    task = viewer.serve(10_000)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# -- colouring by a scalar field -----------------------------------------------


def test_ramp_interpolates_between_the_stops():
    ramp = server_mod._ramp("conservation", 7)
    assert len(ramp) == 7
    assert ramp[0] == "#2c7bb6" and ramp[-1] == "#d7191c"
    assert ramp[3] == "#ffffbf", "the middle stop should land on the middle band"
    assert all(c.startswith("#") and len(c) == 7 for c in ramp)


def test_ramp_of_one_band_is_the_first_stop():
    """Guards a divide-by-zero in the interpolation."""
    assert server_mod._ramp("conservation", 1) == ["#2c7bb6"]


def test_unknown_palette_lists_the_known_ones():
    with pytest.raises(ViewerError, match="Available:"):
        server_mod._ramp("chartreuse", 5)


async def test_color_by_potential_without_a_grid_says_what_to_run(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="Run electrostatics"):
        await color_by_potential(handle="sele", path=str(tmp_path / "absent.dx"))


async def test_color_by_potential_sends_the_grid(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    grid = tmp_path / "p.dx"
    await electrostatics(method="coulombic", spacing=2.0, padding=6.0, path=str(grid))

    sent = {}
    wired_bridge.handlers["color_by_volume"] = lambda args: (
        sent.update(args) or {"components": 1}
    )
    task = wired_bridge.serve(1)
    out = await color_by_potential(handle="sele", path=str(grid), domain=[-5.0, 5.0])
    await task
    assert sent["domain"] == [-5.0, 5.0]
    assert "object 1 class gridpositions" in sent["volume"], "the OpenDX itself is sent"
    assert out["dx_path"] == str(grid)


async def test_color_by_potential_rejects_a_malformed_domain(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    grid = tmp_path / "p.dx"
    await electrostatics(method="coulombic", spacing=2.0, padding=6.0, path=str(grid))
    with pytest.raises(ViewerError, match="min, max"):
        await color_by_potential(handle="sele", path=str(grid), domain=[1.0, 2.0, 3.0])


async def test_color_by_conservation_needs_scores_first(wired_bridge, tmp_path):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb"))
    with pytest.raises(ViewerError, match="call conservation"):
        await color_by_conservation()


async def test_conservation_bands_cover_every_scored_residue(
    wired_bridge, tmp_path, monkeypatch
):
    """The top band must include its own upper edge.

    A half-open last band drops the single most variable residue, which is
    exactly the residue someone colouring by conservation is looking for.
    """
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(wired_bridge, select=nothing, show=nothing, hide=nothing):
        await conservation()
        banded = await color_by_conservation(
            mode="bands", bins=4, representation="spacefill"
        )

    covered = sum(b["residues"] for b in banded["bands"])
    assert covered == 12, f"bands covered {covered} of 12 residues"
    assert banded["most_conserved_first"] is True
    assert banded["bands"][0]["color"] == "#2c7bb6"


async def test_conservation_bands_are_registered_as_handles(
    wired_bridge, tmp_path, monkeypatch
):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(wired_bridge, select=nothing, show=nothing, hide=nothing):
        await conservation()
        banded = await color_by_conservation(
            mode="bands", bins=3, representation="spacefill"
        )
    for band in banded["bands"]:
        assert band["handle"] in server_mod._handles.names()


# -- the continuous conservation gradient --------------------------------------


async def _gradient(
    wired_bridge: Any, tmp_path: Path, monkeypatch: Any, **kwargs: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run conservation then the gradient, capturing what the viewer was sent."""
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)
    sent: dict[str, Any] = {}

    def on_load(args: dict[str, Any]) -> dict[str, Any]:
        sent.update(args)
        return {"loaded": args["name"], "atom_count": 48}

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(
        wired_bridge, load_structure=on_load, select=nothing, show=nothing, hide=nothing
    ):
        await conservation()
        result = await color_by_conservation(mode="gradient", **kwargs)
    return result, sent


async def test_gradient_sends_entropy_in_the_b_factor_column(
    wired_bridge, tmp_path, monkeypatch
):
    """The whole mechanism: Mol* ramps over B-factor, so entropy has to be it.

    Column 0 of the fake alignment is invariant, so residue 1 must arrive at
    the conserved end of the scale and the rest above it.
    """
    result, sent = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert result["reloaded"] is True
    assert sent["format"] == "mmcif"

    array = load_structure(sent["data"], "mmcif", "asymmetric").array
    first = array.b_factor[array.res_id == 1]
    rest = array.b_factor[array.res_id > 1]
    assert first.max() == pytest.approx(0.0, abs=1e-6), "invariant residue is conserved"
    assert rest.min() > first.max(), "every varied residue ranks above it"


async def test_gradient_reloads_as_the_asymmetric_unit(
    wired_bridge, tmp_path, monkeypatch
):
    """These coordinates are already whatever assembly was chosen.

    Letting the viewer expand them a second time would duplicate the molecule,
    which is the bug decision 9 exists to prevent.
    """
    _, sent = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert sent["assembly"] == "asymmetric"
    assert "pdbx_struct_assembly" not in sent["data"]


async def test_gradient_discloses_that_b_factor_now_means_something_else(
    wired_bridge, tmp_path, monkeypatch
):
    """A B-factor column quietly holding conservation would be read as temperature."""
    result, _ = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert "B-factor" in result["note"]
    assert "conservation" in result["note"]


async def test_relative_and_absolute_scales_differ(wired_bridge, tmp_path, monkeypatch):
    """Relative stretches this protein's range; absolute keeps 0-1 comparable."""
    _, relative = await _gradient(wired_bridge, tmp_path, monkeypatch, scale="relative")
    _, absolute = await _gradient(wired_bridge, tmp_path, monkeypatch, scale="absolute")
    rel = load_structure(relative["data"], "mmcif", "asymmetric").array.b_factor
    ab = load_structure(absolute["data"], "mmcif", "asymmetric").array.b_factor
    assert rel.max() == pytest.approx(100.0, abs=1e-6)
    assert ab.max() < rel.max(), "absolute leaves headroom above the observed range"


async def test_handles_survive_the_reload(wired_bridge, tmp_path, monkeypatch):
    """Atom indices stay valid because the atom order does not change."""
    result, _ = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert result["handles_redisplayed"] >= 2, "conserved and variable at least"
    assert "conserved" in server_mod._handles.names()


async def test_unknown_mode_is_refused(wired_bridge, tmp_path, monkeypatch):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    with pytest.raises(ViewerError, match="Unknown mode"):
        await color_by_conservation(mode="rainbow")


async def test_unknown_scale_is_refused(wired_bridge, tmp_path, monkeypatch):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(wired_bridge, select=nothing):
        await conservation()
        with pytest.raises(ViewerError, match="Unknown scale"):
            await color_by_conservation(mode="gradient", scale="logarithmic")


# -- displaying a superposition ------------------------------------------------


def _shifted_pdb(path: Path, offset: float, chain: str = "A", n: int = 6) -> Path:
    """A short peptide, optionally translated along x."""
    lines = []
    serial = 1
    for res in range(1, n + 1):
        for i, (name, elem) in enumerate(
            (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"))
        ):
            xyz = (float(res) * 4 + offset, float(i), 0.0)
            lines.append(_pdb_line(serial, name, "ALA", chain, res, xyz, elem))
            serial += 1
    path.write_text("\n".join(lines) + "\nEND\n")
    return path


class _FakeFetched:
    def __init__(self, path: Path) -> None:
        self.data = path.read_text()
        self.format = "pdb"


class _FakeResult:
    """A superposition result carrying a known transform."""

    def __init__(self, matrix: Any) -> None:
        self.transform = matrix


async def _combine(
    wired_bridge: Any,
    tmp_path: Path,
    matrix: Any,
    suffix: str = "_2",
    chain: str = "A",
) -> tuple[dict[str, Any], dict[str, Any]]:
    mobile = _FakeFetched(_shifted_pdb(tmp_path / "mob.pdb", offset=50.0, chain=chain))
    target = _FakeFetched(_shifted_pdb(tmp_path / "tar.pdb", offset=0.0))
    sent: dict[str, Any] = {}

    def on_load(args: dict[str, Any]) -> dict[str, Any]:
        sent.update(args)
        return {"loaded": args["name"], "atom_count": 48}

    async with _serving(wired_bridge, load_structure=on_load):
        out = await server_mod._display_superposition(
            mobile, target, "mob", "tar", _FakeResult(matrix), suffix
        )
    return out, sent


async def test_the_transform_is_applied_to_the_coordinates(wired_bridge, tmp_path):
    """The bug this fixes: a matrix that nothing ever applied.

    The mobile copy starts 50 A away; a matrix that translates by -50 has to
    bring it exactly onto the target, not merely near it.
    """
    matrix = np.eye(4)
    matrix[0, 3] = -50.0
    await _combine(wired_bridge, tmp_path, matrix)

    array = server_mod._structure
    fixed = array[array.chain_id == "A"]
    moved = array[array.chain_id == "A_2"]
    assert moved.array_length() == fixed.array_length()
    np.testing.assert_allclose(moved.coord, fixed.coord, atol=1e-4)


async def test_an_unapplied_transform_would_leave_them_apart(wired_bridge, tmp_path):
    """Guards the test above: identity must *not* superpose them."""
    await _combine(wired_bridge, tmp_path, np.eye(4))
    array = server_mod._structure
    fixed = array[array.chain_id == "A"]
    moved = array[array.chain_id == "A_2"]
    assert np.abs(moved.coord - fixed.coord).max() == pytest.approx(50.0, abs=1e-4)


async def test_colliding_chain_ids_are_renamed_and_reported(wired_bridge, tmp_path):
    out, _ = await _combine(wired_bridge, tmp_path, np.eye(4))
    assert out["renamed_chains"] == {"A": "A_2"}
    assert out["mobile_chains_shown"] == ["A_2"]
    assert out["target_chains_shown"] == ["A"]


async def test_a_non_colliding_chain_keeps_its_name(wired_bridge, tmp_path):
    out, _ = await _combine(wired_bridge, tmp_path, np.eye(4), chain="Z")
    assert out["renamed_chains"] == {}
    assert out["mobile_chains_shown"] == ["Z"]


async def test_the_pair_becomes_one_selectable_structure(wired_bridge, tmp_path):
    """Both halves have to be addressable, or the picture is all you get."""
    await _combine(wired_bridge, tmp_path, np.eye(4))
    both = select_mask("chain A or chain A_2", server_mod._structure)
    assert both.sum() == server_mod._structure.array_length()


async def test_atom_ids_match_what_the_viewer_will_parse(wired_bridge, tmp_path):
    """The transport reads atom.id, and biotite renumbers on write.

    Two structures concatenated both start at id 1, so without renumbering a
    handle would name one atom in each half.
    """
    _, sent = await _combine(wired_bridge, tmp_path, np.eye(4))
    ours = np.asarray(server_mod._structure.atom_id)
    assert len(np.unique(ours)) == len(ours), "ids must be unique across the pair"
    theirs = load_structure(sent["data"], "mmcif", "asymmetric").array.atom_id
    np.testing.assert_array_equal(ours, theirs)


async def test_the_combined_structure_is_sent_as_the_asymmetric_unit(
    wired_bridge, tmp_path
):
    _, sent = await _combine(wired_bridge, tmp_path, np.eye(4))
    assert sent["assembly"] == "asymmetric"


async def test_stale_handles_do_not_survive_a_new_structure(wired_bridge, tmp_path):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "old.pdb", n=6))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    await select("all", name="stale")
    await task
    assert "stale" in server_mod._handles.names()

    await _combine(wired_bridge, tmp_path, np.eye(4))
    assert server_mod._handles.names() == [], "indices into the old structure"


def test_renumbering_makes_ids_unique_and_one_based():
    atoms = [
        Atom(
            [0.0, 0.0, float(i)],
            chain_id="A",
            res_id=1,
            ins_code="",
            res_name="ALA",
            atom_name="CA",
            element="C",
            hetero=False,
            b_factor=1.0,
            occupancy=1.0,
            atom_id=7,  # every atom claiming the same id
        )
        for i in range(5)
    ]
    array = atom_array(atoms)
    server_mod._renumber_for_viewer(array)
    assert array.atom_id.tolist() == [1, 2, 3, 4, 5]


# -- background and opacity ----------------------------------------------------


async def test_background_refuses_a_call_that_would_change_nothing():
    """Both arguments omitted is a no-op, and a no-op reporting success is a lie."""
    with pytest.raises(ViewerError, match="at least one of color or transparent"):
        await background()


async def test_background_sends_only_what_was_asked_for(wired_bridge):
    """An unmentioned argument must not travel as an explicit default.

    Sending `transparent: false` on a colour-only call would silently undo a
    transparent background someone set a moment earlier.
    """
    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"background": "#ff0000", "transparent": False}
    )
    task = wired_bridge.serve(1)
    await background(color="#ff0000")
    await task

    assert sent == {"color": "#ff0000"}


async def test_background_passes_transparency_through(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"screenshot_transparent": True}
    )
    task = wired_bridge.serve(1)
    out = await background(transparent=True)
    await task

    assert sent == {"transparent": True}
    # The reply is the viewer's read-back, returned to the caller unaltered.
    assert out["screenshot_transparent"] is True


async def test_opacity_reaches_the_viewer(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["opacity"] = lambda args: (
        sent.update(args) or {"representations": 1}
    )
    task = wired_bridge.serve(1)
    await opacity(0.3, name="surf")
    await task

    assert sent == {"name": "surf", "opacity": 0.3}


async def test_show_carries_opacity_alongside_the_representation(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    sent: dict[str, Any] = {}
    wired_bridge.handlers["show"] = lambda args: (
        sent.update(args) or {"name": "sele", "representation": "molecular-surface"}
    )
    task = wired_bridge.serve(1)
    await show(representation="molecular-surface", selection="all", opacity=0.25)
    await task

    assert sent["opacity"] == 0.25
    assert sent["representation"] == "molecular-surface"


async def test_show_omits_opacity_when_it_was_not_asked_for(wired_bridge, tmp_path):
    """So a plain show() cannot quietly pin alpha to a default of its own."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    sent: dict[str, Any] = {}
    wired_bridge.handlers["show"] = lambda args: (
        sent.update(args) or {"name": "sele", "representation": "cartoon"}
    )
    task = wired_bridge.serve(1)
    await show(selection="all")
    await task

    assert "opacity" not in sent
