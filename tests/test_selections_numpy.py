"""The Python selection evaluator, on synthetic structures with known answers.

Runs offline: no browser, no network. Agreement with Mol*'s bundled PyMOL
transpiler on real structures is covered by the differential suite.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

from protean_mcp.selections import SelectionError
from protean_mcp.selections_numpy import (
    _residue_keys,
    load_structure,
    residue_labels,
    select_mask,
)


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


def test_insertion_code_selects_only_the_inserted_residue():
    """Antibody numbering: resi 52A is a different residue from resi 52.

    Insertion codes are the case where a selector can look right and quietly
    answer for the wrong residue, so both directions are asserted.
    """
    atoms = [
        Atom(
            [float(i), 0.0, 0.0],
            chain_id="H",
            res_id=res_id,
            ins_code=ins,
            res_name="ALA",
            atom_name=name,
            element="C",
            hetero=False,
            b_factor=10.0,
            occupancy=1.0,
            atom_id=i,
        )
        for i, (res_id, ins, name) in enumerate(
            [(52, "", "CA"), (52, "", "CB"), (52, "A", "CA"), (53, "", "CA")], start=1
        )
    ]
    array = atom_array(atoms)

    assert select_mask("resi 52A", array).sum() == 1
    assert array[select_mask("resi 52A", array)].ins_code[0] == "A"
    # Lower case is the same residue; PDB insertion codes are upper case.
    assert select_mask("resi 52a", array).sum() == 1


# -- assemblies ----------------------------------------------------------------


def _with_copies(n_copies: int) -> AtomArray[Any]:
    """One alanine, repeated as *n_copies* symmetry copies.

    The copies share chain id and residue number, exactly as biotite's
    assembly expansion produces them, so anything keying on those alone will
    fold them together.
    """
    atoms = []
    for copy in range(n_copies):
        for i, name in enumerate(("N", "CA", "C", "O")):
            atoms.append(
                _atom("A", 1, "ALA", name, "C", [float(i), float(copy * 30), 0.0])
            )
    array = atom_array(atoms)
    array.set_annotation("sym_id", np.repeat(np.arange(n_copies), 4))
    return array


def test_residue_labels_separate_symmetry_copies():
    labels = residue_labels(_with_copies(2))
    assert len(set(labels.tolist())) == 2, "two copies must not share one label"


def test_residue_labels_omit_the_copy_when_there_is_only_one():
    """A single-copy structure should read exactly as it did before."""
    labels = residue_labels(_with_copies(1))
    assert all("#0" in label for label in labels.tolist())


def test_residue_keys_do_not_merge_copies():
    assert len(set(_residue_keys(_with_copies(3)).tolist())) == 3


def test_unknown_assembly_is_refused():
    with pytest.raises(SelectionError, match="Unknown assembly"):
        load_structure("", "mmcif", "supercell")


def test_pdb_input_says_its_assembly_was_not_read():
    """PDB keeps assemblies in REMARK 350, which biotite does not parse.

    Falling back silently would leave the viewer showing a molecule the
    analysis has never seen.
    """
    text = (
        "ATOM      1  N   MET A   1      11.104   6.134  -6.504  1.00  0.00           N\n"
        "END\n"
    )
    loaded = load_structure(text, "pdb", "biological")
    assert loaded.assembly == "asymmetric"
    assert "REMARK 350" in loaded.note


def test_asymmetric_pdb_load_carries_no_note():
    text = (
        "ATOM      1  N   MET A   1      11.104   6.134  -6.504  1.00  0.00           N\n"
        "END\n"
    )
    assert load_structure(text, "pdb", "asymmetric").note == ""
