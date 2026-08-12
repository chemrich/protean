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

from protean_mcp.analysis import secondary_structure
from protean_mcp.analysis.secondary_structure import _bridges
from protean_mcp.selections import SelectionError
from protean_mcp.selections_numpy import (
    _SS_CLASSES,
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


def test_the_terminal_carboxylate_oxygen_is_backbone():
    """OXT hangs off the same carbonyl carbon as O.

    It was landing in `sidechain`, where it is certainly not — four atoms per
    structure, one per chain, and the reason PyMOL and protean disagreed about
    `backbone` on every structure with a modelled C-terminus.
    """
    array = atom_array(
        [
            _atom("A", 1, "ALA", "N", "N", [0.0, 0.0, 0.0]),
            _atom("A", 1, "ALA", "CA", "C", [1.5, 0.0, 0.0]),
            _atom("A", 1, "ALA", "C", "C", [2.5, 1.0, 0.0]),
            _atom("A", 1, "ALA", "O", "O", [2.5, 2.0, 0.0]),
            _atom("A", 1, "ALA", "OXT", "O", [3.5, 0.8, 0.0]),
            _atom("A", 1, "ALA", "CB", "C", [1.5, -1.5, 0.0]),
        ]
    )
    assert count("backbone", array) == 5
    assert count("sidechain", array) == 1  # CB alone
    assert count("name OXT and sidechain", array) == 0


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


# Residues 22-36 of 1UBQ, backbone only: the structure's one alpha-helix with a
# residue of loop either side. Real deposited coordinates, embedded so the fast
# suite stays offline.
#
# **These replaced a generated fixture, and the reason matters.** The old one
# placed N, CA, C and O on four concentric cylinders, which traces a helical
# *path* and satisfied P-SEA, because P-SEA reads backbone shape. It has no
# peptide geometry at all — the C=O vectors point nowhere in particular — so no
# hydrogen bond forms and DSSP correctly finds nothing in it. A fixture that is
# only shaped like the answer tests the previous implementation, not the
# molecule.
_UBIQUITIN_HELIX = (
    "ATOM      1  N   THR A  22      31.510  18.936  12.852  1.00  0.00           N",
    "ATOM      2  CA  THR A  22      31.398  19.064  14.286  1.00  0.00           C",
    "ATOM      3  C   THR A  22      31.593  20.553  14.655  1.00  0.00           C",
    "ATOM      4  O   THR A  22      32.159  21.311  13.861  1.00  0.00           O",
    "ATOM      5  N   ILE A  23      31.113  20.863  15.860  1.00  0.00           N",
    "ATOM      6  CA  ILE A  23      31.288  22.201  16.417  1.00  0.00           C",
    "ATOM      7  C   ILE A  23      32.776  22.519  16.577  1.00  0.00           C",
    "ATOM      8  O   ILE A  23      33.233  23.659  16.384  1.00  0.00           O",
    "ATOM      9  N   GLU A  24      33.548  21.526  16.950  1.00  0.00           N",
    "ATOM     10  CA  GLU A  24      35.031  21.722  17.069  1.00  0.00           C",
    "ATOM     11  C   GLU A  24      35.615  22.190  15.759  1.00  0.00           C",
    "ATOM     12  O   GLU A  24      36.532  23.046  15.724  1.00  0.00           O",
    "ATOM     13  N   ASN A  25      35.139  21.624  14.662  1.00  0.00           N",
    "ATOM     14  CA  ASN A  25      35.590  21.945  13.302  1.00  0.00           C",
    "ATOM     15  C   ASN A  25      35.238  23.382  12.920  1.00  0.00           C",
    "ATOM     16  O   ASN A  25      36.066  24.109  12.333  1.00  0.00           O",
    "ATOM     17  N   VAL A  26      34.007  23.745  13.250  1.00  0.00           N",
    "ATOM     18  CA  VAL A  26      33.533  25.097  12.978  1.00  0.00           C",
    "ATOM     19  C   VAL A  26      34.441  26.099  13.684  1.00  0.00           C",
    "ATOM     20  O   VAL A  26      34.883  27.090  13.093  1.00  0.00           O",
    "ATOM     21  N   LYS A  27      34.734  25.822  14.949  1.00  0.00           N",
    "ATOM     22  CA  LYS A  27      35.596  26.715  15.736  1.00  0.00           C",
    "ATOM     23  C   LYS A  27      36.975  26.826  15.107  1.00  0.00           C",
    "ATOM     24  O   LYS A  27      37.579  27.926  15.159  1.00  0.00           O",
    "ATOM     25  N   ALA A  28      37.499  25.743  14.571  1.00  0.00           N",
    "ATOM     26  CA  ALA A  28      38.794  25.761  13.880  1.00  0.00           C",
    "ATOM     27  C   ALA A  28      38.728  26.591  12.611  1.00  0.00           C",
    "ATOM     28  O   ALA A  28      39.704  27.346  12.277  1.00  0.00           O",
    "ATOM     29  N   LYS A  29      37.633  26.543  11.867  1.00  0.00           N",
    "ATOM     30  CA  LYS A  29      37.471  27.391  10.668  1.00  0.00           C",
    "ATOM     31  C   LYS A  29      37.441  28.882  11.052  1.00  0.00           C",
    "ATOM     32  O   LYS A  29      38.020  29.772  10.382  1.00  0.00           O",
    "ATOM     33  N   ILE A  30      36.811  29.170  12.192  1.00  0.00           N",
    "ATOM     34  CA  ILE A  30      36.731  30.570  12.645  1.00  0.00           C",
    "ATOM     35  C   ILE A  30      38.148  30.981  13.069  1.00  0.00           C",
    "ATOM     36  O   ILE A  30      38.544  32.150  12.856  1.00  0.00           O",
    "ATOM     37  N   GLN A  31      38.883  30.110  13.713  1.00  0.00           N",
    "ATOM     38  CA  GLN A  31      40.269  30.508  14.115  1.00  0.00           C",
    "ATOM     39  C   GLN A  31      41.092  30.808  12.851  1.00  0.00           C",
    "ATOM     40  O   GLN A  31      41.828  31.808  12.681  1.00  0.00           O",
    "ATOM     41  N   ASP A  32      41.001  29.878  11.931  1.00  0.00           N",
    "ATOM     42  CA  ASP A  32      41.718  30.022  10.643  1.00  0.00           C",
    "ATOM     43  C   ASP A  32      41.399  31.338   9.967  1.00  0.00           C",
    "ATOM     44  O   ASP A  32      42.260  32.036   9.381  1.00  0.00           O",
    "ATOM     45  N   LYS A  33      40.117  31.750   9.988  1.00  0.00           N",
    "ATOM     46  CA  LYS A  33      39.808  32.994   9.233  1.00  0.00           C",
    "ATOM     47  C   LYS A  33      39.837  34.271   9.995  1.00  0.00           C",
    "ATOM     48  O   LYS A  33      40.164  35.323   9.345  1.00  0.00           O",
    "ATOM     49  N   GLU A  34      39.655  34.335  11.285  1.00  0.00           N",
    "ATOM     50  CA  GLU A  34      39.676  35.547  12.072  1.00  0.00           C",
    "ATOM     51  C   GLU A  34      40.675  35.527  13.200  1.00  0.00           C",
    "ATOM     52  O   GLU A  34      40.814  36.528  13.911  1.00  0.00           O",
    "ATOM     53  N   GLY A  35      41.317  34.393  13.432  1.00  0.00           N",
    "ATOM     54  CA  GLY A  35      42.345  34.269  14.431  1.00  0.00           C",
    "ATOM     55  C   GLY A  35      41.949  34.076  15.842  1.00  0.00           C",
    "ATOM     56  O   GLY A  35      42.829  34.000  16.739  1.00  0.00           O",
    "ATOM     57  N   ILE A  36      40.642  33.916  16.112  1.00  0.00           N",
    "ATOM     58  CA  ILE A  36      40.226  33.716  17.509  1.00  0.00           C",
    "ATOM     59  C   ILE A  36      40.449  32.278  17.945  1.00  0.00           C",
    "ATOM     60  O   ILE A  36      39.936  31.336  17.315  1.00  0.00           O",
)


@pytest.fixture
def ideal_helix() -> AtomArray[Any]:
    """1UBQ's alpha-helix, residues 22-36, backbone only."""
    return load_structure(
        "\n".join(_UBIQUITIN_HELIX) + "\nEND\n", "pdb", "asymmetric"
    ).array


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


def test_the_helix_types_partition_ss_h(ideal_helix):
    """`ss H` is every helix type, and the three do not overlap.

    The point of assigning with DSSP rather than P-SEA: alpha, 3-10 and pi are
    separately addressable. P-SEA had two classes and could not express two of
    these at all.
    """
    parts = (
        count("ss alpha", ideal_helix)
        + count("ss 3-10", ideal_helix)
        + count("ss pi", ideal_helix)
    )
    assert parts == count("ss H", ideal_helix)
    assert count("ss alpha and ss 3-10", ideal_helix) == 0
    assert count("ss alpha and ss pi", ideal_helix) == 0


def test_this_fixture_is_alpha_and_nothing_else(ideal_helix):
    """Guards the test above from passing on a fixture with no helix at all.

    Summing three zeroes also equals a zero `ss H`, so the partition test alone
    would pass against an assignment that found nothing.
    """
    assert count("ss alpha", ideal_helix) > 0
    assert count("ss 3-10", ideal_helix) == 0
    assert count("ss pi", ideal_helix) == 0


def test_the_helix_type_aliases_agree(ideal_helix):
    """DSSP's letters are deep in a model's prior; the long names are clearer."""
    assert count("ss G", ideal_helix) == count("ss 3-10", ideal_helix)
    assert count("ss 310", ideal_helix) == count("ss 3-10", ideal_helix)
    assert count("ss helix_310", ideal_helix) == count("ss 3-10", ideal_helix)
    assert count("ss I", ideal_helix) == count("ss pi", ideal_helix)
    assert count("ss helix_alpha", ideal_helix) == count("ss alpha", ideal_helix)


def test_ss_s_is_sheet_not_bend(ideal_helix):
    """The one collision in the vocabulary, and it is a silent one.

    S is PyMOL's letter for strand and DSSP's letter for bend. A caller writing
    `ss S` means strand, so `bend` gets a name of its own — and the two must not
    resolve to the same atoms.
    """
    assert count("ss S", ideal_helix) == count("ss extended", ideal_helix) + count(
        "ss bridge", ideal_helix
    )
    # Asserted against the vocabulary rather than against this fixture, which
    # is helix and loop and so has neither strand nor bend: equal counts of
    # nothing would pass whichever class `ss S` resolved to.
    assert _SS_CLASSES["S"] == frozenset("EB")
    assert _SS_CLASSES["BEND"] == frozenset("S")
    assert _SS_CLASSES["S"].isdisjoint(_SS_CLASSES["BEND"])


def test_loop_covers_everything_that_is_not_helix_or_strand(ideal_helix):
    """`ss L` has to include turns and bends, or the classes leak.

    A caller who asks for helix, strand and loop expects the whole polymer
    back. Turn and bend are DSSP classes with no PyMOL equivalent, so they have
    to fall somewhere, and loop is where PyMOL puts them.
    """
    total = ideal_helix.array_length()
    assert count("ss H+S+L", ideal_helix) == total


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


# -- secondary structure: caching and the bridge scan --------------------------


def _brute_force_bridges(
    bonds: set[tuple[int, int]], n_residues: int
) -> list[tuple[int, int, str]]:
    """The all-pairs scan `_bridges` replaced, kept as the oracle."""
    has = bonds.__contains__
    found: list[tuple[int, int, str]] = []
    for i in range(1, n_residues - 1):
        for j in range(i + 3, n_residues - 1):
            parallel = (has((i - 1, j)) and has((j, i + 1))) or (
                has((j - 1, i)) and has((i, j + 1))
            )
            anti = (has((i, j)) and has((j, i))) or (
                has((i - 1, j + 1)) and has((j - 1, i + 1))
            )
            if parallel:
                found.append((i, j, "P"))
            elif anti:
                found.append((i, j, "A"))
    return found


@pytest.mark.parametrize("seed", range(12))
def test_reading_bridges_off_the_bonds_finds_exactly_what_scanning_all_pairs_did(seed):
    """The optimisation must lose nothing, including the ordering.

    Candidates are read out of the hydrogen-bond set instead of every residue
    pair being tested. That is only valid because every bridge rule is a
    conjunction of bonds among {i-1,i,i+1} x {j-1,j,j+1}, so a pair with no
    bond nearby cannot satisfy one. A missed rule position would show up as a
    dropped bridge on some seed and nowhere else — and `_ladders` chains
    bridges in arrival order, so the sequence has to match too, not just the
    set.
    """
    rng = np.random.default_rng(seed)
    n = 40
    pairs = rng.integers(0, n, size=(300, 2))
    bonds = {(int(a), int(b)) for a, b in pairs if abs(int(a) - int(b)) >= 2}

    assert _bridges(bonds, n) == _brute_force_bridges(bonds, n)


def test_the_assignment_is_computed_once_for_a_structure(monkeypatch, ideal_helix):
    """`ss` re-derives per node, so `ss H or ss S` paid for it twice."""
    secondary_structure._cache.clear()
    calls = 0
    original = secondary_structure._assign_uncached

    def counted(array):
        nonlocal calls
        calls += 1
        return original(array)

    monkeypatch.setattr(secondary_structure, "_assign_uncached", counted)
    count("ss H or ss S or ss L", ideal_helix)
    assert calls == 1


def test_moving_the_atoms_is_not_served_from_the_cache(ideal_helix):
    """Keyed on content, not on `id(array)`.

    A trajectory sets coordinates in place on the same object, so an identity
    key would answer with the previous frame's helices for this frame's
    geometry — a wrong answer that looks like a fast one.
    """
    secondary_structure._cache.clear()
    assert count("ss H", ideal_helix) > 0

    # In place, on the same object. Handing this a fresh array would pass
    # against an `id(array)` key too, which is exactly the bug being guarded.
    ideal_helix.coord = ideal_helix.coord * 3.0
    assert count("ss H", ideal_helix) == 0


def test_a_caller_cannot_corrupt_the_cached_assignment(ideal_helix):
    """The cache hands out copies, or the first caller's edits become truth."""
    secondary_structure._cache.clear()
    secondary_structure.assign(ideal_helix)  # populate
    served = secondary_structure.assign(ideal_helix)  # a cache *hit*
    served[:] = "Z"
    assert (secondary_structure.assign(ideal_helix) == "Z").sum() == 0


def test_the_cache_does_not_grow_without_bound(ideal_helix):
    """Each entry holds a copy of the coordinates it was keyed on."""
    secondary_structure._cache.clear()
    for scale in range(1, 12):
        moved = ideal_helix.copy()
        moved.coord = moved.coord * (1.0 + scale)
        secondary_structure.assign(moved)
    assert len(secondary_structure._cache) <= secondary_structure._CACHE_ENTRIES
