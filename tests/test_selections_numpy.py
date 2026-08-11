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


# -- the nucleic backbone ------------------------------------------------------


@pytest.fixture
def dna() -> AtomArray[Any]:
    """One deoxyguanosine: phosphate, full sugar ring, and a slice of the base.

    Named as a real nucleotide is, because the whole classification is by atom
    name — a fixture with invented names would agree with anything.
    """
    backbone = [
        ("P", "P"),
        ("OP1", "O"),
        ("OP2", "O"),
        ("O5'", "O"),
        ("C5'", "C"),
        ("C4'", "C"),
        ("O4'", "O"),
        ("C3'", "C"),
        ("O3'", "O"),
        ("C2'", "C"),
        ("C1'", "C"),
    ]
    base = [("N9", "N"), ("C8", "C"), ("N7", "N"), ("C5", "C"), ("O6", "O")]
    atoms = [
        _atom("A", 1, "DG", name, element, [float(i), 0.0, 0.0])
        for i, (name, element) in enumerate(backbone + base)
    ]
    return atom_array(atoms)


def test_nucleic_backbone_is_the_sugar_phosphate(dna):
    """11 backbone atoms: phosphate plus the whole ribose, base excluded."""
    assert count("backbone", dna) == 11


def test_nucleic_sidechain_is_the_base_not_the_whole_molecule(dna):
    """The bug this fixture exists for.

    `sidechain` was "polymer and not protein-backbone", so on a nucleic acid
    it returned every atom and read as a real answer.
    """
    assert count("sidechain", dna) == 5
    assert count("sidechain", dna) != count("polymer", dna)


def test_backbone_and_sidechain_partition_the_nucleic_polymer(dna):
    """Neither overlapping nor leaving atoms unclaimed."""
    assert count("backbone", dna) + count("sidechain", dna) == count("polymer", dna)
    assert count("backbone and sidechain", dna) == 0


def test_legacy_atom_name_spellings_are_still_backbone():
    """Pre-remediation files write primes as asterisks and OP1/OP2 as O1P/O2P.

    A file old enough to use them parses fine and would otherwise have its
    entire backbone classified as base.
    """
    names = [("O1P", "O"), ("O2P", "O"), ("O5*", "O"), ("C5*", "C"), ("C1*", "C")]
    array = atom_array(
        [
            _atom("A", 1, "DG", name, element, [float(i), 0.0, 0.0])
            for i, (name, element) in enumerate(names)
        ]
    )
    assert count("backbone", array) == len(names)
    assert count("sidechain", array) == 0


def test_the_two_name_sets_do_not_leak_into_each_other():
    """Each polymer's backbone is judged only by its own names.

    A single merged set of names would classify this serine's P and O3' as
    backbone and this nucleotide's CA as backbone, both wrongly. Neither atom
    name is realistic — that is the point: only the residue decides which
    vocabulary applies.
    """
    array = atom_array(
        [
            _atom("A", 1, "SER", "N", "N", [0.0, 0.0, 0.0]),
            _atom("A", 1, "SER", "P", "P", [1.0, 0.0, 0.0]),
            _atom("A", 1, "SER", "O3'", "O", [2.0, 0.0, 0.0]),
            _atom("B", 2, "DG", "P", "P", [3.0, 0.0, 0.0]),
            _atom("B", 2, "DG", "CA", "C", [4.0, 0.0, 0.0]),
        ]
    )
    assert count("backbone", array) == 2  # the serine N and the DG phosphate
    assert count("sidechain", array) == 3


def test_protein_backbone_is_unchanged(mixed):
    """The protein answer must not move: it is pinned to hand-checked counts."""
    assert count("backbone", mixed) == 5
    assert count("sidechain", mixed) == 2


# -- element symbols are a closed set ------------------------------------------


def test_an_element_that_does_not_exist_is_refused(mixed):
    """`elem Zz` used to be 0 atoms and no complaint.

    Which reads as "this structure has none of those" rather than "you
    misspelled it".
    """
    with pytest.raises(SelectionError, match="No such element"):
        count("elem Zz", mixed)


def test_the_refusal_names_the_symbol_it_rejected(mixed):
    with pytest.raises(SelectionError, match="'QQ'"):
        count("elem Qq", mixed)


def test_a_near_miss_is_offered_a_correction(mixed):
    with pytest.raises(SelectionError, match="Did you mean 'ZN'"):
        count("elem Znn", mixed)


def test_one_bad_symbol_refuses_the_whole_list(mixed):
    """`elem C+Zz` cannot quietly answer with just the carbons."""
    with pytest.raises(SelectionError, match="'ZZ'"):
        count("elem C+Zz", mixed)


def test_a_real_element_that_is_absent_still_answers_zero(mixed):
    """ "This structure has no iron" is a true statement, not a mistake.

    The check must separate a symbol that cannot exist from one that merely
    is not here, or it turns every honest empty answer into an error.
    """
    assert count("elem Fe", mixed) == 0
    assert count("elem He", mixed) == 0


def test_element_matching_stays_case_insensitive(mixed):
    assert count("elem zn", mixed) == count("elem ZN", mixed) == 1


def test_a_symbol_the_table_has_never_heard_of_is_kept_if_the_file_uses_it():
    """The escape hatch: a refusal must never swallow a real match.

    Files do carry symbols outside the periodic table. Validating against the
    structure as well as the table means such a file still answers, and only a
    symbol that is neither real nor present is refused.
    """
    array = atom_array(
        [
            _atom("A", 1, "UNK", "X1", "XX", [0.0, 0.0, 0.0], hetero=True),
            _atom("A", 1, "UNK", "C1", "C", [1.0, 0.0, 0.0], hetero=True),
        ]
    )
    assert count("elem XX", array) == 1
    with pytest.raises(SelectionError, match="No such element"):
        count("elem YY", array)


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


# -- bond topology -------------------------------------------------------------


@pytest.fixture
def dipeptide() -> AtomArray[Any]:
    """Two bonded residues and a free water, with real atom names.

    Bonds come from residue templates, so the names have to be the ones the
    templates use — invented names would leave every atom unbonded and make
    the whole thing agree with a broken implementation.
    """
    atoms = [
        _atom("A", 1, "GLY", "N", "N", [0.0, 0.0, 0.0]),
        _atom("A", 1, "GLY", "CA", "C", [1.5, 0.0, 0.0]),
        _atom("A", 1, "GLY", "C", "C", [2.4, 1.2, 0.0]),
        _atom("A", 1, "GLY", "O", "O", [2.0, 2.3, 0.0]),
        _atom("A", 2, "ALA", "N", "N", [3.7, 1.0, 0.0]),
        _atom("A", 2, "ALA", "CA", "C", [4.7, 2.1, 0.0]),
        _atom("A", 2, "ALA", "CB", "C", [6.1, 1.6, 0.0]),
        _atom("A", 2, "ALA", "C", "C", [4.5, 3.0, 1.2]),
        _atom("A", 2, "ALA", "O", "O", [3.5, 3.7, 1.3]),
        # Same chain as the peptide on purpose: if the water sat in its own
        # chain, widening over chain id would give the same answer as
        # widening over connectivity and the test would prove nothing.
        _atom("A", 3, "HOH", "O", "O", [30.0, 0.0, 0.0], hetero=True),
    ]
    for i, atom in enumerate(atoms, start=1):
        atom.atom_id = i
    return atom_array(atoms)


def test_neighbor_is_the_bonded_atoms_without_the_source(dipeptide):
    """Glycine's CA is bonded to its own N and C, and to nothing else."""
    assert count("neighbor (resi 1 and name CA)", dipeptide) == 2


def test_neighbor_excludes_the_source_even_when_it_is_bonded_to_itself(dipeptide):
    """The whole of residue 1 is internally bonded.

    A single atom cannot catch a missing self-exclusion, because no atom is
    bonded to itself. A whole residue can: without it, every atom of residue 1
    comes back as its own neighbour.
    """
    assert count("neighbor (resi 1)", dipeptide) == 1  # only the next residue's N
    assert count("neighbor (resi 1) and resi 1", dipeptide) == 0


def test_bound_to_names_the_same_set_as_neighbor(dipeptide):
    """PyMOL treats them as synonyms; a caller should not have to guess."""
    for selection in ("resi 1 and name CA", "resi 2"):
        assert count(f"neighbor ({selection})", dipeptide) == count(
            f"bound_to ({selection})", dipeptide
        )


def test_extend_keeps_the_source_and_grows_by_bonds(dipeptide):
    """One bond out from CA reaches N and C; two reaches the peptide bond."""
    assert count("(resi 1 and name CA) extend 1", dipeptide) == 3
    assert count("(resi 1 and name CA) extend 2", dipeptide) == 5


def test_extend_stops_when_the_molecule_runs_out(dipeptide):
    """It cannot leak across a break: the water is bonded to nothing here."""
    everything = count("(resi 1 and name CA) extend 50", dipeptide)
    assert everything == count("polymer", dipeptide)
    assert count("resn HOH", dipeptide) == 1


def test_extend_counts_bonds_not_angstroms(dipeptide):
    """The water sits 30 A away, so distance and topology cannot be confused.

    A cheap `expand` in disguise would still exclude it; what this pins is
    that the peptide's own atoms are reached by bond count rather than by
    being nearby.
    """
    by_bonds = select_mask("(resi 1 and name N) extend 3", dipeptide)
    by_distance = select_mask("(resi 1 and name N) expand 3", dipeptide)
    assert not np.array_equal(by_bonds, by_distance)


def test_bymolecule_takes_the_whole_connected_molecule(dipeptide):
    """One atom of the peptide pulls in both residues, and not the water."""
    assert count("bymolecule (resi 1 and name CA)", dipeptide) == count(
        "polymer", dipeptide
    )
    assert count("bymolecule (resi 1 and name CA) and resn HOH", dipeptide) == 0


def test_bymolecule_leaves_an_unbonded_atom_alone(dipeptide):
    """A lone water is its own molecule, not part of the nearest one."""
    assert count("bymolecule resn HOH", dipeptide) == 1


def test_rank_is_the_position_in_the_array(dipeptide):
    """Distinct from `index`, which is the file's own atom id."""
    assert count("rank 0", dipeptide) == 1
    assert dipeptide[select_mask("rank 0", dipeptide)].atom_name[0] == "N"
    assert count("rank 0-2", dipeptide) == 3
    assert count("rank 9", dipeptide) == 1


def test_rank_and_index_are_not_the_same_field(dipeptide):
    """Here atom_id starts at 1 and rank at 0, so they differ by one."""
    assert count("rank 0", dipeptide) == 1
    assert count("index 0", dipeptide) == 0
    assert count("index 1", dipeptide) == 1


def test_a_rank_beyond_the_end_matches_nothing(dipeptide):
    """Like a residue number out of range: a true answer, not a mistake."""
    assert count("rank 1000000", dipeptide) == 0


def test_unsupported_constructs_still_raise(mixed):
    with pytest.raises(SelectionError, match="not supported"):
        select_mask("last chain A", mixed)


# -- secondary structure -------------------------------------------------------


@pytest.fixture
def ideal_helix() -> AtomArray[Any]:
    """16 residues on an ideal alpha-helical path.

    Real geometry rather than invented coordinates: secondary structure is
    assigned from backbone shape, so a fixture that was not helix-shaped would
    agree with any implementation, right or wrong.
    """
    lines, serial = [], 1
    for i in range(16):
        angle = np.deg2rad(100.0 * i)
        for name, element, radius, offset in (
            ("N", "N", 1.5, -0.4),
            ("CA", "C", 2.3, 0.0),
            ("C", "C", 1.9, 0.4),
            ("O", "O", 2.0, 0.6),
        ):
            turn = angle + offset
            x, y = radius * np.cos(turn), radius * np.sin(turn)
            z = 1.5 * i + offset * 1.5
            lines.append(
                f"ATOM  {serial:5d}  {name:<3s} ALA A{i + 1:4d}    "
                f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           {element}"
            )
            serial += 1
    return load_structure("\n".join(lines) + "\nEND\n", "pdb", "asymmetric").array


def test_a_helix_is_selectable_as_one(ideal_helix):
    """`ss H` used to be refused outright."""
    assert count("ss H", ideal_helix) > 0
    assert count("ss S", ideal_helix) == 0


def test_the_classes_do_not_overlap_or_leak(ideal_helix):
    """Every residue is helix, strand or loop, and none is two of them."""
    total = ideal_helix.array_length()
    assert count("ss H", ideal_helix) + count("ss L", ideal_helix) == total
    assert count("ss H and ss L", ideal_helix) == 0


def test_the_long_names_mean_the_same_as_the_letters(ideal_helix):
    """A model writing `ss helix` should not be told that is a typo."""
    assert count("ss helix", ideal_helix) == count("ss H", ideal_helix)
    assert count("ss h", ideal_helix) == count("ss H", ideal_helix)
    assert count("ss strand", ideal_helix) == count("ss S", ideal_helix)


def test_several_classes_can_be_asked_for_at_once(ideal_helix):
    assert count("ss H+L", ideal_helix) == count("ss H", ideal_helix) + count(
        "ss L", ideal_helix
    )


def test_an_unknown_class_is_refused(ideal_helix):
    """Like `elem`, the vocabulary is closed, so a typo is not a query."""
    with pytest.raises(SelectionError, match="Unknown secondary structure"):
        count("ss Q", ideal_helix)


def test_secondary_structure_is_empty_on_a_molecule_that_has_none(mixed):
    """Two residues and an ion: nothing long enough to be an element."""
    assert count("ss H", mixed) == 0
    assert count("ss S", mixed) == 0


def test_secondary_structure_composes_with_other_selectors(ideal_helix):
    assert count("ss H and chain A", ideal_helix) == count("ss H", ideal_helix)
    assert count("ss H and chain Z", ideal_helix) == 0


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


# -- alternate conformers ------------------------------------------------------

_CIF_COLUMNS = [
    "group_PDB",
    "id",
    "type_symbol",
    "label_atom_id",
    "label_alt_id",
    "label_comp_id",
    "label_asym_id",
    "label_entity_id",
    "label_seq_id",
    "pdbx_PDB_ins_code",
    "Cartn_x",
    "Cartn_y",
    "Cartn_z",
    "occupancy",
    "B_iso_or_equiv",
    "auth_seq_id",
    "auth_asym_id",
    "auth_comp_id",
    "auth_atom_id",
    "pdbx_PDB_model_num",
]


def _cif(rows: list[tuple[str, str, str, int, int]]) -> str:
    """A minimal mmCIF from (element, atom name, altloc, residue, model) rows."""
    header = "data_test\nloop_\n" + "".join(
        f"_atom_site.{column}\n" for column in _CIF_COLUMNS
    )
    lines = [
        f"ATOM {i} {element} {name} {alt} GLY A 1 {res} ? "
        f"{float(i)} 0.0 0.0 1.00 10.0 {res} A GLY {name} {model}"
        for i, (element, name, alt, res, model) in enumerate(rows, start=1)
    ]
    return header + "\n".join(lines) + "\n"


# One residue whose CB has two conformers and one whose OG has three: five rows
# in the file, two of them surplus, three atoms once resolved.
_TWO_AND_THREE = [
    ("N", "N", ".", 1, 1),
    ("C", "CB", "A", 1, 1),
    ("C", "CB", "B", 1, 1),
    ("O", "OG", "A", 2, 1),
    ("O", "OG", "B", 2, 1),
    ("O", "OG", "C", 2, 1),
]


def test_altloc_surplus_counts_the_conformers_analysis_drops():
    """The count the viewer holds and the analysis does not.

    Stated as the invariant that matters: what biotite parsed plus the surplus
    is every row in the file, which is what Mol* draws.
    """
    loaded = load_structure(_cif(_TWO_AND_THREE), "mmcif", "asymmetric")
    assert loaded.altloc_surplus == 3
    assert loaded.array.array_length() + loaded.altloc_surplus == len(_TWO_AND_THREE)


def test_altloc_surplus_is_zero_when_every_atom_has_one_conformer():
    rows = [("N", "N", ".", 1, 1), ("C", "CA", ".", 1, 1)]
    loaded = load_structure(_cif(rows), "mmcif", "asymmetric")
    assert loaded.altloc_surplus == 0
    assert loaded.array.array_length() == len(rows)


def test_altloc_surplus_ignores_models_after_the_first():
    """Only model 1 is parsed, so only model 1's conformers may be counted.

    An NMR ensemble repeats every atom per model; counting all of them would
    invent a surplus many times the real one and explain away a genuine
    mismatch.
    """
    rows = _TWO_AND_THREE + [(e, n, a, r, 2) for e, n, a, r, _ in _TWO_AND_THREE]
    loaded = load_structure(_cif(rows), "mmcif", "asymmetric")
    assert loaded.altloc_surplus == 3
    assert loaded.array.array_length() + loaded.altloc_surplus == len(_TWO_AND_THREE)


def test_altloc_surplus_separates_atoms_that_share_a_conformer_letter():
    """Two residues' CB, both labelled A, are not conformers of each other.

    Same atom name, same letter, different residue — so a site identity that
    leaves the residue out folds them together and invents a surplus.
    """
    rows = [("C", "CB", "A", 1, 1), ("C", "CB", "A", 2, 1)]
    loaded = load_structure(_cif(rows), "mmcif", "asymmetric")
    assert loaded.altloc_surplus == 0
    assert loaded.array.array_length() == 2


def test_altloc_surplus_reads_pdb_columns():
    """Two conformers of residue 1's CB, and one unambiguous CB in residue 2.

    Residue 2 repeats both the atom name and the conformer letter, so a site
    identity that reads only the name folds it into residue 1 and counts a
    conformer that does not exist.
    """
    text = (
        "ATOM      1  N   MET A   1      11.104   6.134  -6.504  1.00  0.00           N\n"
        "ATOM      2  CB AMET A   1      12.104   6.134  -6.504  0.50  0.00           C\n"
        "ATOM      3  CB BMET A   1      12.204   6.134  -6.504  0.50  0.00           C\n"
        "ATOM      4  CB ASER A   2      15.104   6.134  -6.504  1.00  0.00           C\n"
        "END\n"
    )
    loaded = load_structure(text, "pdb", "asymmetric")
    assert loaded.altloc_surplus == 1
    assert loaded.array.array_length() + loaded.altloc_surplus == 4


_ASSEMBLY_OF_TWO = """
loop_
_pdbx_struct_assembly_gen.assembly_id
_pdbx_struct_assembly_gen.oper_expression
_pdbx_struct_assembly_gen.asym_id_list
1 '(1,2)' A
loop_
_pdbx_struct_oper_list.id
_pdbx_struct_oper_list.type
_pdbx_struct_oper_list.matrix[1][1]
_pdbx_struct_oper_list.matrix[1][2]
_pdbx_struct_oper_list.matrix[1][3]
_pdbx_struct_oper_list.matrix[2][1]
_pdbx_struct_oper_list.matrix[2][2]
_pdbx_struct_oper_list.matrix[2][3]
_pdbx_struct_oper_list.matrix[3][1]
_pdbx_struct_oper_list.matrix[3][2]
_pdbx_struct_oper_list.matrix[3][3]
_pdbx_struct_oper_list.vector[1]
_pdbx_struct_oper_list.vector[2]
_pdbx_struct_oper_list.vector[3]
1 'identity operation' 1 0 0 0 1 0 0 0 1 0 0 0
2 'point symmetry operation' 1 0 0 0 1 0 0 0 1 30 0 0
"""


def test_altloc_surplus_scales_with_the_assembly():
    """Each symmetry copy carries the file's conformers again.

    The viewer expands the assembly from the same rows, so a surplus counted
    once explains only half a two-copy assembly and the rest reads as a
    mismatch that is not there.
    """
    rows = [("N", "N", ".", 1, 1), ("C", "CB", "A", 1, 1), ("C", "CB", "B", 1, 1)]
    loaded = load_structure(_cif(rows) + _ASSEMBLY_OF_TWO, "mmcif", "biological")
    assert loaded.copies == 2
    assert loaded.altloc_surplus == 2
    assert loaded.array.array_length() + loaded.altloc_surplus == len(rows) * 2
