"""Alternate conformers, against a real Mol*.

Two claims that only a browser can settle.

**The counts agree.** Every conformer is loaded on both sides now, so the
atom count is the same number rather than the same number plus an explained
surplus. Measured against what Mol* actually built, not against the file.

**A handle for one conformer draws that conformer.** Conformers of an atom
share a residue, a chain and an atom name, and differ only by a letter the
viewer never sees — a handle travels as `atom.id`. That is the shape of the
symmetry-copy bug from item 7, where copies shared `atom_id` and a set
covering one copy could not be drawn. It does *not* apply here, because every
conformer row carries its own `atom_site.id`. Asserting it anyway is what item
7 taught: the assumption was the bug.

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_altloc_differential.py
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from protean_mcp.fetch import fetch_structure_data
from protean_mcp.handles import to_molscript as indices_to_molscript
from protean_mcp.selections_numpy import (
    conformer_state,
    dominant_altloc,
    load_structure,
    select_mask,
)

from .browser import BROWSER_MARKS, viewer_session

pytestmark = BROWSER_MARKS

FIXTURE = "5fji"
# 206 sites with two conformers and 11 with a third: 217 alternate rows.
EXPECTED_ATOMS = 15929
EXPECTED_STATE = 15712
EXPECTED_LABELLED = 206

# Atom ids of whatever an expression matched, so a set can be compared by
# identity. A count can agree while naming different atoms, and "the right
# number of atoms, drawn in the wrong place" is the failure this file exists
# to rule out.
_ATOM_IDS_JS = r"""(async () => {
  const p = window.__protean.plugin;
  const struct = p.managers.structure.hierarchy.current.structures[0];
  const out = {};
  for (const [key, expression] of %s) {
    const sel = await p.builders.structure.tryCreateComponent(
      struct.cell.transform.ref,
      { type: { name: 'script', params: { language: 'mol-script', expression } },
        nullIfEmpty: false, label: 'alt' },
      'alt-' + key);
    const data = sel && (sel.data || (sel.cell && sel.cell.obj && sel.cell.obj.data));
    const ids = [];
    if (data) {
      for (const u of data.units) {
        const col = u.model.atomicConformation.atomId;
        const els = u.elements;
        for (let i = 0; i < els.length; i++) ids.push(col.value(els[i]));
      }
    }
    out[key] = ids;
    try { if (sel && sel.ref) await p.state.data.build().delete(sel.ref).commit(); }
    catch (err) { /* next iteration overwrites */ }
  }
  out.__total__ = struct.cell.obj.data.elementCount;
  return JSON.stringify(out);
})()"""


@pytest.fixture(scope="module")
async def conformers() -> dict[str, object]:
    """One browser session carrying every claim about the fixture."""
    structure = await fetch_structure_data(FIXTURE)
    loaded = load_structure(structure.data, structure.format, "asymmetric")
    array = loaded.array
    atom_ids = np.asarray(array.atom_id)
    letter = dominant_altloc(array)

    sets = {
        "state": np.flatnonzero(conformer_state(array, letter)),
        "labelled": np.flatnonzero(select_mask(f"alt {letter}", array)),
    }
    cases = [[key, indices_to_molscript(array, idx)] for key, idx in sets.items()]

    async with viewer_session(FIXTURE, assembly="asymmetric") as session:
        payload = await session.evaluate(_ATOM_IDS_JS % json.dumps(cases))

    return {
        "letter": letter,
        "ours": {key: {int(v) for v in atom_ids[idx]} for key, idx in sets.items()},
        "theirs": {
            key: {int(v) for v in value}
            for key, value in payload.items()
            if key != "__total__"
        },
        "viewer_total": int(payload["__total__"]),
        "python_total": int(array.array_length()),
        "surplus": loaded.altloc_surplus,
    }


async def test_both_halves_hold_the_same_atoms(conformers):
    """No surplus left to explain: the counts are simply equal."""
    assert conformers["python_total"] == EXPECTED_ATOMS
    assert conformers["viewer_total"] == EXPECTED_ATOMS


async def test_the_fixture_actually_has_alternates(conformers):
    """Guards every other test here: a structure with none would pass them all."""
    assert conformers["surplus"] == 217
    assert len(conformers["ours"]["labelled"]) == EXPECTED_LABELLED


async def test_a_conformer_state_transports_by_identity(conformers):
    """The item-7 hazard, checked rather than assumed.

    Conformers share a residue, a chain and an atom name; only `atom_site.id`
    tells them apart, and that is exactly what a handle travels as. If two
    conformers shared an id, this set would draw both — the count would still
    look plausible.
    """
    assert conformers["ours"]["state"] == conformers["theirs"]["state"]
    assert len(conformers["theirs"]["state"]) == EXPECTED_STATE


async def test_one_conformers_atoms_do_not_drag_in_the_other(conformers):
    """`alt A` is 206 atoms in the viewer too, not 412.

    The sharpest form of the same claim: these atoms have twins that share
    everything except their id and their position.
    """
    assert conformers["ours"]["labelled"] == conformers["theirs"]["labelled"]
    assert len(conformers["theirs"]["labelled"]) == EXPECTED_LABELLED


async def test_the_state_is_a_strict_subset_of_the_whole(conformers):
    """It must leave something out, or the filter is doing nothing."""
    assert EXPECTED_STATE < EXPECTED_ATOMS
    assert conformers["theirs"]["state"] < set(range(1, EXPECTED_ATOMS + 1))
