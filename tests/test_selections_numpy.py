"""The Python selection evaluator, on synthetic structures with known answers.

Runs offline: no browser, no network. Agreement with the MolScript engine on
real structures is covered by the differential suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

from protean_mcp.selections import SelectionError
from protean_mcp.selections_numpy import select_mask


def _atom(
    chain: str,
    res_id: int,
    res_name: str,
    atom_name: str,
    element: str,
    coord: list[float],
    hetero: bool = False,
    b_factor: float = 10.0,
) -> Atom:
    return Atom(
        coord,
        chain_id=chain,
        res_id=res_id,
        ins_code="",
        res_name=res_name,
        atom_name=atom_name,
        element=element,
        hetero=hetero,
        b_factor=b_factor,
        occupancy=1.0,
        atom_id=0,
    )


@pytest.fixture
def mixed() -> AtomArray[Any]:
    """Two protein residues, a zinc, and a water — one of each kind."""
    atoms = [
        _atom("A", 1, "ALA", "N", "N", [0.0, 0.0, 0.0]),
        _atom("A", 1, "ALA", "CA", "C", [1.5, 0.0, 0.0]),
        _atom("A", 1, "ALA", "C", "C", [2.5, 1.0, 0.0]),
        _atom("A", 1, "ALA", "O", "O", [2.5, 2.0, 0.0]),
        _atom("A", 1, "ALA", "CB", "C", [1.5, -1.5, 0.0], b_factor=80.0),
        _atom("A", 2, "SER", "CA", "C", [4.0, 0.0, 0.0]),
        _atom("A", 2, "SER", "OG", "O", [5.0, 1.0, 0.0]),
        _atom("B", 3, "ZN", "ZN", "ZN", [6.0, 0.0, 0.0], hetero=True),
        _atom("B", 4, "HOH", "O", "O", [20.0, 0.0, 0.0], hetero=True),
    ]
    for i, atom in enumerate(atoms, start=1):
        atom.atom_id = i
    return atom_array(atoms)


def count(selection: str, array: AtomArray[Any]) -> int:
    return int(select_mask(selection, array).sum())


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("all", 9),
        ("none", 0),
        ("protein", 7),
        ("polymer", 7),
        ("solvent", 1),
        ("hetatm", 2),
        ("metals", 1),
        ("backbone", 5),  # ALA N/CA/C/O plus SER CA
        ("sidechain", 2),  # ALA CB and SER OG
        ("hydro", 0),
        ("chain A", 7),
        ("chain B", 2),
        ("resi 1", 5),
        ("resi 1-2", 7),
        ("resn ALA", 5),
        ("name CA", 2),
        ("elem O", 3),
        ("elem C", 4),
    ],
)
def test_leaf_selectors(mixed, selection, expected):
    assert count(selection, mixed) == expected


def test_boolean_operators(mixed):
    assert count("chain A and name CA", mixed) == 2
    assert count("chain A or chain B", mixed) == 9
    assert count("not chain A", mixed) == 2
    assert count("protein and not backbone", mixed) == 2


def test_comparison_uses_b_factor(mixed):
    assert count("b > 50", mixed) == 1
    assert count("b < 50", mixed) == 8


def test_byres_widens_to_whole_residues(mixed):
    # One sidechain atom pulls in all five atoms of its residue.
    assert count("name CB", mixed) == 1
    assert count("byres name CB", mixed) == 5


def test_bychain_widens_over_the_same_id_that_chain_selects(mixed):
    """The MolScript backend widens over Mol*'s chain key, which follows
    label_asym_id, so there `chain` and `bychain` can disagree about what a
    chain is. Here they cannot."""
    assert count("bychain resi 3", mixed) == count("chain B", mixed)


def test_first_returns_a_single_atom(mixed):
    assert count("first chain A", mixed) == 1
    assert count("first none", mixed) == 0


def test_within_selects_from_the_left_operand(mixed):
    # The zinc at x=6 is 1 A from SER OG at x=5,y=1 -> sqrt(2).
    assert count("protein within 2 of resn ZN", mixed) >= 1
    assert count("solvent within 2 of resn ZN", mixed) == 0


def test_bare_within_is_implicitly_all(mixed):
    explicit = count("all within 2 of resn ZN", mixed)
    assert count("within 2 of resn ZN", mixed) == explicit


def test_around_excludes_the_source(mixed):
    around = select_mask("resn ZN around 3", mixed)
    zinc = select_mask("resn ZN", mixed)
    assert not (around & zinc).any()


def test_expand_keeps_the_source(mixed):
    expanded = select_mask("resn ZN expand 3", mixed)
    zinc = select_mask("resn ZN", mixed)
    # Containment, not equality: every source atom survives the expansion.
    assert not (zinc & ~expanded).any()
    assert expanded.sum() > zinc.sum()


def test_unsupported_constructs_still_raise(mixed):
    with pytest.raises(SelectionError, match="not supported"):
        select_mask("ss H", mixed)


def test_unknown_keyword_raises(mixed):
    with pytest.raises(SelectionError, match="Unknown selection keyword"):
        select_mask("banana", mixed)
