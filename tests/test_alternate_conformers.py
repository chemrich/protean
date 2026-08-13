"""Alternate conformers: every one loaded, one state analysed.

An atom resolved in two positions is stored twice, tagged `A`/`B`, and the two
**never coexist** — each molecule in the crystal is in one state. Only the
atoms that actually differ carry a letter, so the states *overlap* in the
residue's shared atoms. That is the fact behind every test here, and the
reason alternate conformers could not become part of residue identity the way
symmetry copies did.

Offline: synthetic structures with hand-checked counts, plus the real
structures whose numbers the plan pinned. Transport to a real viewer is in
test_altloc_differential.py.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

from protean_mcp.analysis.contacts import interface
from protean_mcp.selections import SelectionError
from protean_mcp.selections_numpy import (
    _bond_pairs,
    conformer_state,
    conformers_used,
    dominant_altloc,
    has_altlocs,
    resolve_conformers,
    select_mask,
)


def _atom(
    res_id: int,
    name: str,
    altloc: str,
    coord: list[float],
    occupancy: float = 1.0,
    chain: str = "A",
    res_name: str = "SER",
) -> Atom:
    return Atom(
        coord,
        chain_id=chain,
        res_id=res_id,
        ins_code="",
        res_name=res_name,
        atom_name=name,
        element=name[0],
        hetero=False,
        occupancy=occupancy,
        b_factor=10.0,
        atom_id=0,
    )


def _with_altloc(atoms: list[Atom], letters: list[str]) -> AtomArray[Any]:
    """An array carrying the altloc annotation biotite would have parsed."""
    for i, atom in enumerate(atoms, start=1):
        atom.atom_id = i
    arr = atom_array(atoms)
    arr.set_annotation("altloc_id", np.array(letters, dtype="<U1"))
    return arr


@pytest.fixture
def two_states() -> AtomArray[Any]:
    """One serine whose backbone is shared and whose side chain is split.

    Modelled on SER320 of 5FJI: N/C/O carry no letter, CA/CB carry both. The
    shared atoms are what make the states overlap.
    """
    return _with_altloc(
        [
            _atom(1, "N", ".", [0.0, 0.0, 0.0]),
            _atom(1, "CA", "A", [1.5, 0.0, 0.0], 0.7),
            _atom(1, "CA", "B", [1.5, 0.3, 0.0], 0.3),
            _atom(1, "C", ".", [2.5, 1.0, 0.0]),
            _atom(1, "O", ".", [2.5, 2.0, 0.0]),
            _atom(1, "CB", "A", [1.5, -1.5, 0.0], 0.7),
            _atom(1, "CB", "B", [1.6, -1.4, 0.4], 0.3),
        ],
        [".", "A", "B", ".", ".", "A", "B"],
    )


# -- the states overlap --------------------------------------------------------


def test_a_state_is_the_shared_atoms_plus_one_letter(two_states):
    """The property that rules out altloc as a residue-identity field.

    State A and state B both contain N, C and O. They are not a partition, so
    keying a residue on the letter would split this serine into three
    fragments — shared, A, and B — none of them a residue.
    """
    state_a = conformer_state(two_states, "A")
    state_b = conformer_state(two_states, "B")
    assert int(state_a.sum()) == 5  # N, C, O, CA(A), CB(A)
    assert int(state_b.sum()) == 5
    shared = state_a & state_b
    assert int(shared.sum()) == 3  # N, C, O — in both states
    assert sorted(two_states.atom_name[shared].tolist()) == ["C", "N", "O"]


def test_every_atom_belongs_to_some_state(two_states):
    """Nothing is orphaned: the union of the states is the whole structure."""
    union = conformer_state(two_states, "A") | conformer_state(two_states, "B")
    assert int(union.sum()) == two_states.array_length()


def test_a_structure_without_alternates_is_one_whole_state():
    plain = _with_altloc(
        [_atom(1, "N", ".", [0.0, 0.0, 0.0]), _atom(1, "CA", ".", [1.5, 0.0, 0.0])],
        [".", "."],
    )
    assert not has_altlocs(plain)
    assert conformer_state(plain).all()


# -- choosing a state ----------------------------------------------------------


def test_the_dominant_conformer_is_the_one_with_most_occupancy(two_states):
    assert dominant_altloc(two_states) == "A"


def test_the_dominant_conformer_is_not_merely_the_first():
    """B at 0.9 beats A at 0.1, which is the point of reading occupancy.

    Choosing whichever came first in the file is what biotite's default does,
    and it is arbitrary where this is not.
    """
    atoms = [
        _atom(1, "N", ".", [0.0, 0.0, 0.0]),
        _atom(1, "CB", "A", [1.5, 0.0, 0.0], 0.1),
        _atom(1, "CB", "B", [1.5, 0.4, 0.0], 0.9),
    ]
    arr = _with_altloc(atoms, [".", "A", "B"])
    assert dominant_altloc(arr) == "B"


def test_a_tie_falls_back_to_the_first_letter():
    """1AKE's ARG167 is 0.5/0.5, so something has to be chosen and stated."""
    atoms = [
        _atom(1, "CB", "A", [1.5, 0.0, 0.0], 0.5),
        _atom(1, "CB", "B", [1.5, 0.4, 0.0], 0.5),
    ]
    assert dominant_altloc(_with_altloc(atoms, ["A", "B"])) == "A"


# -- `alt` is literal ----------------------------------------------------------


def test_alt_selects_only_the_labelled_atoms(two_states):
    """PyMOL's meaning: a fragment, not a conformer.

    `alt A` here is CA and CB — a side chain with no backbone. That is why the
    state is spelled `alt ''+A`, and why analysis does not use `alt` at all.
    """
    assert int(select_mask("alt A", two_states).sum()) == 2
    assert int(select_mask("alt B", two_states).sum()) == 2


def test_the_labels_are_disjoint(two_states):
    """The property the rejected "alt means the state" reading would destroy."""
    both = select_mask("alt A", two_states) & select_mask("alt B", two_states)
    assert int(both.sum()) == 0


def test_the_state_is_alt_empty_plus_a_letter(two_states):
    assert int(select_mask("alt ''+A", two_states).sum()) == 5
    assert np.array_equal(
        select_mask("alt ''+A", two_states), conformer_state(two_states, "A")
    )


def test_both_spellings_of_no_alternate_agree(two_states):
    """PyMOL writes '', biotite writes '.', and a caller may reach for either.

    The quotes survive tokenisation as part of the word, so `alt ''` arrives
    as a two-character string. Without stripping them the selection is empty
    and reads as "no unlabelled atoms", which is the silent-empty answer the
    grammar exists to prevent — it did exactly that until this was fixed.
    """
    empty = select_mask("alt ''", two_states)
    dot = select_mask("alt .", two_states)
    assert np.array_equal(empty, dot)
    assert int(empty.sum()) == 3


def test_alt_is_refused_where_there_are_no_alternates():
    plain = _with_altloc([_atom(1, "N", ".", [0.0, 0.0, 0.0])], ["."])
    with pytest.raises(SelectionError, match="single position"):
        select_mask("alt A", plain)


# -- topology must not cross states --------------------------------------------


def test_no_bond_joins_two_conformers(two_states):
    """Templates match by atom name and cannot tell conformers apart.

    On 1AKE they wire 16 such bonds, including ARG167/CD(A) to NE(B), leaving
    CD(A) with three bonds where it should have two. `extend` would then walk
    out of one conformer into another along a path no molecule has.
    """
    pairs = _bond_pairs(two_states)
    ids = np.asarray(two_states.get_annotation("altloc_id"))
    left, right = ids[pairs[:, 0]], ids[pairs[:, 1]]
    across = (left != ".") & (right != ".") & (left != right)
    assert int(across.sum()) == 0


def test_extend_one_bond_stays_inside_the_conformer(two_states):
    """The guarantee the bond filter buys, stated at its real strength.

    One bond out of CA(A) reaches the shared backbone and CB(A) — never
    CB(B), which it would have reached directly through the template bond the
    filter removes.
    """
    reached = select_mask("(alt A and name CA) extend 1", two_states)
    ids = np.asarray(two_states.get_annotation("altloc_id"))
    assert "B" not in set(ids[reached].tolist())


def test_extend_two_bonds_can_cross_through_a_shared_atom(two_states):
    """The limit of it, asserted so nobody assumes more than is true.

    N is bonded to CA(A) *and* CA(B) — the file says so, and dropping one of
    those would be inventing topology rather than filtering it. So a two-bond
    walk from CA(A) reaches CA(B) through N. Removing direct A-B bonds keeps
    `extend 1`, `bound_to` and `neighbor` inside one state; it cannot keep a
    longer walk there without making traversal conformer-aware, which would
    mean these selectors quietly answering about a subset of the structure
    they were given.

    Recorded as a caveat rather than fixed: analysis never walks bonds, so
    nothing computed downstream depends on it.
    """
    reached = select_mask("(alt A and name CA) extend 2", two_states)
    ids = np.asarray(two_states.get_annotation("altloc_id"))
    assert "B" in set(ids[reached].tolist())


# -- analysis resolves one state -----------------------------------------------


def test_interface_reports_which_conformer_it_used():
    """A number computed over one state while the picture shows both is the
    quiet mismatch this whole item is about, so the state is in the reply."""
    atoms = [
        _atom(1, "CA", ".", [0.0, 0.0, 0.0], chain="A"),
        _atom(1, "CB", "A", [1.0, 0.0, 0.0], 0.7, chain="A"),
        _atom(1, "CB", "B", [1.0, 0.5, 0.0], 0.3, chain="A"),
        _atom(2, "CA", ".", [4.0, 0.0, 0.0], chain="B"),
        _atom(2, "CB", ".", [3.0, 0.0, 0.0], chain="B"),
    ]
    arr = _with_altloc(atoms, [".", "A", "B", ".", "."])
    result = interface(arr, "A", "B")
    assert result.conformer == "A"
    assert result.as_dict()["conformer"] == "A"


def test_interface_does_not_count_a_residue_twice():
    """The failure that made loading every conformer dangerous.

    A residue's shared atoms carry no letter, so both states land in one
    residue entry and their buried areas sum. On 5FJI that inflates the worst
    residues by nearly half while the structure total barely moves — small
    where anyone would look, wrong where it matters.
    """
    atoms = [
        _atom(1, "CA", ".", [0.0, 0.0, 0.0], chain="A"),
        _atom(1, "CB", "A", [1.0, 0.0, 0.0], 0.5, chain="A"),
        _atom(1, "CB", "B", [1.0, 0.4, 0.0], 0.5, chain="A"),
        _atom(2, "CA", ".", [4.5, 0.0, 0.0], chain="B"),
        _atom(2, "CB", ".", [3.2, 0.0, 0.0], chain="B"),
    ]
    arr = _with_altloc(atoms, [".", "A", "B", ".", "."])
    result = interface(arr, "A", "B")
    labels = [(r["chain"], r["seq"]) for r in result.interface_residues_a]
    assert len(labels) == len(set(labels)), "a residue appears once per state"
    # and no atom of the rejected conformer is in the handle
    ids = np.asarray(arr.get_annotation("altloc_id"))
    assert "B" not in set(ids[result.indices_a].tolist())


# -- the state is resolved per site, not per structure --------------------------


def test_an_atom_with_no_counterpart_survives():
    """The bug a code review found, and the reason this is per site.

    Choosing one letter for the whole structure looks equivalent and is not.
    A partially occupied ion labelled `B` with no `A` counterpart -- a routine
    way to model one -- was **deleted from the geometry entirely**, silently,
    contributing no buried area and appearing in no handle. 5FJI has 11 atoms
    labelled `C` that a global `A` discards the same way.
    """
    atoms = [
        _atom(1, "CB", "A", [0.0, 0.0, 0.0], 0.9),
        _atom(2, "CB", "B", [4.0, 0.0, 0.0], 0.1),
        _atom(3, "ZN", "B", [8.0, 0.0, 0.0], 0.5, res_name="ZN"),
    ]
    arr = _with_altloc(atoms, ["A", "B", "B"])
    kept = conformer_state(arr)
    assert int(kept.sum()) == 3, "every site keeps its own best conformer"
    assert dominant_altloc(arr) == "A", "A still predominates by occupancy"


def test_each_site_keeps_exactly_one_conformer(two_states):
    """The other half of the claim: resolving must still resolve.

    Keeping everything would satisfy the test above and defeat the purpose.
    """
    kept = conformer_state(two_states)
    assert int(kept.sum()) == 5  # 3 shared + one CA + one CB
    names = two_states.atom_name[kept].tolist()
    assert sorted(names) == ["C", "CA", "CB", "N", "O"]


def test_a_site_keeps_its_highest_occupancy_conformer():
    """Per site, not globally: site 1 prefers A and site 2 prefers B."""
    atoms = [
        _atom(1, "CB", "A", [0.0, 0.0, 0.0], 0.8),
        _atom(1, "CB", "B", [0.5, 0.0, 0.0], 0.2),
        _atom(2, "CB", "A", [4.0, 0.0, 0.0], 0.3),
        _atom(2, "CB", "B", [4.5, 0.0, 0.0], 0.7),
    ]
    arr = _with_altloc(atoms, ["A", "B", "A", "B"])
    kept = np.flatnonzero(conformer_state(arr))
    ids = np.asarray(arr.get_annotation("altloc_id"))
    assert [str(ids[i]) for i in kept] == ["A", "B"]
    assert conformers_used(arr) == "A+B"


def test_naming_a_letter_is_still_literal(two_states):
    """An explicit request is a different question and keeps its old answer."""
    assert int(conformer_state(two_states, "B").sum()) == 5
    ids = np.asarray(two_states.get_annotation("altloc_id"))
    assert "A" not in set(ids[conformer_state(two_states, "B")].tolist())


# -- every path that reads coordinates resolves the same state -----------------


def test_the_conformer_reported_matches_the_one_announced():
    """The load message and the analysis must name the same letters.

    `interface` resolved *after* dropping solvent, and high-resolution
    structures model waters with alternates too — so the two could pick
    different letters and the reply would promise one conformer while
    reporting another.
    """
    atoms = [
        _atom(1, "CA", ".", [0.0, 0.0, 0.0], chain="A"),
        _atom(1, "CB", "A", [1.0, 0.0, 0.0], 0.6, chain="A"),
        _atom(1, "CB", "B", [1.0, 0.5, 0.0], 0.4, chain="A"),
        _atom(2, "CA", ".", [4.0, 0.0, 0.0], chain="B"),
        _atom(2, "CB", ".", [3.0, 0.0, 0.0], chain="B"),
        # a water whose alternates favour the other letter
        _atom(3, "O", "A", [20.0, 0.0, 0.0], 0.2, chain="W", res_name="HOH"),
        _atom(3, "O", "B", [20.5, 0.0, 0.0], 0.8, chain="W", res_name="HOH"),
    ]
    arr = _with_altloc(atoms, [".", "A", "B", ".", ".", "A", "B"])
    announced = conformers_used(arr)
    assert interface(arr, "A", "B").conformer == announced


def test_a_trajectory_template_is_one_state():
    """A trajectory carries one position per atom and no alternates.

    Matching it against every conformer refuses a trajectory that does belong
    to this structure, and tells the caller to load a different one.
    """
    atoms = [
        _atom(1, "N", ".", [0.0, 0.0, 0.0]),
        _atom(1, "CB", "A", [1.0, 0.0, 0.0], 0.7),
        _atom(1, "CB", "B", [1.0, 0.4, 0.0], 0.3),
    ]
    arr = _with_altloc(atoms, [".", "A", "B"])
    template, letter = resolve_conformers(arr)
    assert template.array_length() == 2, "one position per atom"
    assert arr.array_length() == 3, "the loaded structure still holds both"
    assert letter == "A"
