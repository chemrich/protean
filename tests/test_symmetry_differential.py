"""Addressing one symmetry copy: does the handle the viewer draws mean what
the analysis computed?

A biological assembly is the asymmetric unit repeated by symmetry operators.
Every copy carries the **same** chain ids, residue numbers and atom ids, so an
atom-id predicate alone matches the named atom in all of them. Naming a copy
needs the operator, and the two sides enumerate operators independently:
biotite annotates ``sym_id`` 0..n-1, Mol* names them ``ASM_1..ASM_n``.

Nothing here can be checked by counting. Every copy has the same number of
atoms, the same residues and the same chains, so a permuted or offset mapping
gives identical counts, identical residue lists and a picture that looks
entirely normal. **Only coordinates distinguish the copies**, which is why
every claim below is a centroid.

Requires a real browser and the network:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_symmetry_differential.py
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from protean_mcp.fetch import fetch_structure_data
from protean_mcp.handles import operator_name
from protean_mcp.handles import to_molscript as indices_to_molscript
from protean_mcp.selections_numpy import load_structure

from .browser import BROWSER_MARKS, viewer_session

pytestmark = BROWSER_MARKS

# 1HHO is the natural case: haemoglobin, 2 copies, operator `y,x,-z`, so the
# copies are nowhere near each other. 1COI is a coiled-coil trimer and earns
# its place by having **three** copies -- a 2-copy structure cannot tell
# "correct" apart from "consistently reversed".
FIXTURES = {
    "1hho": {"copies": 2, "atoms_per_copy": 2396},
    "1coi": {"copies": 3, "atoms_per_copy": 249},
}

# Atom count and centroid of whatever an expression matched, read back from the
# structure Mol* actually built. Coordinates come through unit.conformation,
# which applies the symmetry operator -- reading the model arrays directly
# would give identical coordinates for every copy, since copies share one model
# and differ only by the matrix. That is the whole failure mode, so reading the
# untransformed arrays here would make this test unable to fail.
_EVAL_JS = r"""(async () => {
  const p = window.__protean.plugin;
  const struct = p.managers.structure.hierarchy.current.structures[0];
  const ref = struct.cell.transform.ref;

  const measure = (data) => {
    let n = 0, x = 0, y = 0, z = 0;
    for (const u of data.units) {
      const els = u.elements;
      for (let i = 0; i < els.length; i++) {
        const e = els[i];
        n += 1;
        x += u.conformation.x(e);
        y += u.conformation.y(e);
        z += u.conformation.z(e);
      }
    }
    return { count: n, centroid: n ? [x / n, y / n, z / n] : null };
  };

  const out = [];
  // The whole assembly, grouped by the operator Mol* attached to each unit.
  // This is Mol*'s own opinion of which atoms are which copy, independent of
  // any query, and the thing the MolScript predicate has to agree with.
  const whole = struct.cell.obj.data;
  const byOperator = {};
  for (const u of whole.units) {
    const key = u.conformation.operator.name;
    const a = byOperator[key] || (byOperator[key] = { n: 0, x: 0, y: 0, z: 0 });
    const els = u.elements;
    for (let i = 0; i < els.length; i++) {
      const e = els[i];
      a.n += 1;
      a.x += u.conformation.x(e);
      a.y += u.conformation.y(e);
      a.z += u.conformation.z(e);
    }
  }
  const operators = {};
  for (const [k, a] of Object.entries(byOperator)) {
    operators[k] = { count: a.n, centroid: [a.x / a.n, a.y / a.n, a.z / a.n] };
  }
  out.push({ key: '__operators__', operators, total: whole.elementCount });

  for (const [key, expression] of %s) {
    const entry = { key };
    let sel = null;
    try {
      sel = await p.builders.structure.tryCreateComponent(ref, {
        type: { name: 'script', params: { language: 'mol-script', expression } },
        nullIfEmpty: false, label: 'sym',
      }, 'sym-key');
      const data = sel && (sel.data || (sel.cell && sel.cell.obj && sel.cell.obj.data));
      if (data) Object.assign(entry, measure(data));
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


def _copy_expression(sym_id: int) -> str:
    """The operator predicate on its own, with no atom-id test."""
    return f"(sel.atom.atom-groups :atom-test (= atom.op-name `{operator_name(sym_id)}`))"


@pytest.fixture(scope="module", params=sorted(FIXTURES))
async def symmetry(request) -> dict[str, object]:
    """One browser session per fixture, carrying every claim about it."""
    pdb_id = request.param
    structure = await fetch_structure_data(pdb_id)
    loaded = load_structure(structure.data, structure.format, "biological")
    array = loaded.array
    sym = np.asarray(array.sym_id)
    coords = np.asarray(array.coord)
    copies = sorted({int(v) for v in sym})

    cases: list[list[str]] = []
    for k in copies:
        cases.append([f"copy::{k}", _copy_expression(k)])
        # The production path: an atom set resolved in Python, emitted by the
        # real emitter. On a set confined to one copy this is what item 7 was
        # unable to express at all.
        one_copy = np.flatnonzero(sym == k)
        cases.append([f"handle::{k}", indices_to_molscript(array, one_copy)])
    # An operator that does not exist. Mol* reports success and matches
    # nothing, so this pins the shape of the failure rather than trusting it.
    cases.append(["ghost", _copy_expression(max(copies) + 50)])
    # A set that is symmetric across every copy still has to survive.
    cases.append(
        ["symmetric", indices_to_molscript(array, np.arange(array.array_length()))]
    )
    # A handle inside one copy that is NOT the whole copy: the first 10 atoms
    # of the last copy. Without the operator clause this matches 10 atoms in
    # every copy, so the count alone catches this one.
    last = copies[-1]
    cases.append(
        ["partial", indices_to_molscript(array, np.flatnonzero(sym == last)[:10])]
    )

    async with viewer_session(pdb_id, assembly="biological") as session:
        raw = await session.evaluate(_EVAL_JS % json.dumps(cases))
    results = json.loads(raw) if isinstance(raw, str) else raw
    by_key = {entry["key"]: entry for entry in results}

    return {
        "pdb_id": pdb_id,
        "copies": copies,
        "python_centroids": {
            k: coords[sym == k].mean(axis=0).astype(float) for k in copies
        },
        "python_counts": {k: int((sym == k).sum()) for k in copies},
        "total": array.array_length(),
        "viewer": by_key,
        "operators": by_key["__operators__"]["operators"],
        "viewer_total": by_key["__operators__"]["total"],
    }


def _centroid(entry: dict[str, object]) -> np.ndarray:
    assert "error" not in entry, f"viewer errored: {entry.get('error')}"
    assert entry.get("centroid") is not None, f"matched nothing: {entry}"
    return np.asarray(entry["centroid"], dtype=float)


# -- the mapping (plan section 5.1) -------------------------------------------


async def test_the_expected_number_of_copies_is_present(symmetry):
    """Guards the fixtures themselves: a 1-copy structure would make every
    claim below pass without testing anything."""
    expected = FIXTURES[symmetry["pdb_id"]]
    assert len(symmetry["copies"]) == expected["copies"]
    assert len(symmetry["operators"]) == expected["copies"]
    for k in symmetry["copies"]:
        assert symmetry["python_counts"][k] == expected["atoms_per_copy"]


async def test_copy_centroids_are_distinguishable(symmetry):
    """If two copies sat on top of each other, matching them by centroid would
    prove nothing. Assert they are genuinely apart before relying on it."""
    centroids = list(symmetry["python_centroids"].values())
    for i, a in enumerate(centroids):
        for b in centroids[i + 1 :]:
            assert float(np.linalg.norm(a - b)) > 1.0


async def test_sym_id_k_is_operator_asm_k_plus_one(symmetry):
    """The claim the whole item rests on, proven by geometry.

    biotite's ``sym_id`` is 0-based; Mol* pre-increments its operator index, so
    the first operator is ``ASM_1``. Nothing about the counts would change if
    this were off by one or permuted -- only the coordinates move.
    """
    for k in symmetry["copies"]:
        name = operator_name(k)
        assert name in symmetry["operators"], (
            f"{name} absent; Mol* has {sorted(symmetry['operators'])}"
        )
        theirs = np.asarray(symmetry["operators"][name]["centroid"], dtype=float)
        ours = symmetry["python_centroids"][k]
        assert float(np.linalg.norm(theirs - ours)) < 1e-3


async def test_each_copy_is_nearer_its_own_operator_than_any_other(symmetry):
    """The mapping is not merely close, it is the closest.

    A structure whose copies are related by a small displacement could satisfy
    the tolerance above against the wrong operator; this cannot.
    """
    for k in symmetry["copies"]:
        ours = symmetry["python_centroids"][k]
        distances = {
            name: float(np.linalg.norm(np.asarray(v["centroid"], dtype=float) - ours))
            for name, v in symmetry["operators"].items()
        }
        assert min(distances, key=lambda n: distances[n]) == operator_name(k)


async def test_the_operator_predicate_selects_the_copy_it_names(symmetry):
    """Mol* answering about its own units is one claim; the MolScript
    predicate returning those same atoms is another."""
    for k in symmetry["copies"]:
        entry = symmetry["viewer"][f"copy::{k}"]
        assert entry["count"] == symmetry["python_counts"][k]
        assert (
            float(np.linalg.norm(_centroid(entry) - symmetry["python_centroids"][k]))
            < 1e-3
        )


# -- copies partition the structure (plan section 5.2) ------------------------


async def test_the_copies_together_are_the_whole_structure(symmetry):
    total = sum(symmetry["viewer"][f"copy::{k}"]["count"] for k in symmetry["copies"])
    assert total == symmetry["viewer_total"] == symmetry["total"]


async def test_a_nonexistent_operator_matches_nothing_and_says_so(symmetry):
    """Documents the silent-success shape this item exists to avoid: an
    operator name that names no copy is not an error, it is an empty
    selection reported as a success. An off-by-one in the mapping would land
    exactly here on one copy and nowhere else."""
    ghost = symmetry["viewer"]["ghost"]
    assert "error" not in ghost
    assert ghost["count"] == 0


# -- the production path (plan section 4.1) -----------------------------------


async def test_a_handle_confined_to_one_copy_transports_exactly(symmetry):
    """The payoff. A set covering exactly one copy, resolved in Python and sent
    through the real emitter, must draw that copy and not its twin."""
    for k in symmetry["copies"]:
        entry = symmetry["viewer"][f"handle::{k}"]
        assert entry["count"] == symmetry["python_counts"][k]
        assert (
            float(np.linalg.norm(_centroid(entry) - symmetry["python_centroids"][k]))
            < 1e-3
        )


async def test_a_partial_handle_does_not_leak_into_other_copies(symmetry):
    """Ten atoms of one copy. Before the operator clause this matched ten atoms
    in *every* copy, so unlike the rest of this file the count alone fails."""
    entry = symmetry["viewer"]["partial"]
    assert entry["count"] == 10


async def test_a_symmetric_handle_still_covers_every_copy(symmetry):
    """The whole structure is symmetric across copies, so it must keep meaning
    "every copy" -- a selection with no copy named must not narrow to one."""
    entry = symmetry["viewer"]["symmetric"]
    assert entry["count"] == symmetry["total"]
