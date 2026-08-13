"""Superposition analysis: pure Python, no browser.

Most of this runs offline against synthetic coordinates. The section at the
bottom fetches two real globins, because the case that justifies structural
mode cannot be built by hand — it needs two proteins whose sequences have
diverged while their fold has not. It shares CI's network gate:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_superposition.py
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from protean_mcp.analysis.superposition import (
    SuperpositionError,
    parse_structure,
    protein_atoms,
    superpose,
)
from protean_mcp.fetch import fetch_structure_data
from protean_mcp.selections_numpy import load_structure, resolve_conformers

NEEDS_NETWORK = pytest.mark.skipif(
    os.environ.get("PROTEAN_DIFFERENTIAL") != "1",
    reason="fetches from RCSB; set PROTEAN_DIFFERENTIAL=1 to run",
)

# Two residues of a minimal poly-alanine backbone, enough for biotite to parse
# and for chain/format handling to be exercised without a network fetch.
TINY_PDB = """\
ATOM      1  N   ALA A   1      0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1      1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1      2.009   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1      1.251   2.390   0.000  1.00  0.00           O
ATOM      5  N   GLY A   2      3.332   1.552   0.000  1.00  0.00           N
ATOM      6  CA  GLY A   2      3.993   2.849   0.000  1.00  0.00           C
ATOM      7  C   GLY A   2      5.500   2.700   0.000  1.00  0.00           C
ATOM      8  O   GLY A   2      6.000   1.580   0.000  1.00  0.00           O
ATOM      9  N   HOH B   3      9.000   9.000   9.000  1.00  0.00           N
END
"""


def test_parses_pdb_text():
    array = parse_structure(TINY_PDB, "pdb")
    assert array.array_length() == 9


def test_malformed_coordinates_raise_our_error():
    with pytest.raises(SuperpositionError, match="Could not parse"):
        parse_structure("ATOM  nonsense\nEND\n", "pdb")


def test_unknown_format_is_refused():
    with pytest.raises(SuperpositionError, match="Unsupported format"):
        parse_structure(TINY_PDB, "mol2")


# A site whose *second* letter has the higher occupancy, which is the only
# fixture that can tell "highest occupancy" from "first letter". Both rules
# keep the shared N/C/O; they disagree about which CA.
BACKWARDS_OCCUPANCY_PDB = """\
ATOM      1  N   ALA A   1      0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA AALA A   1      1.458   0.000   0.000  0.30  0.00           C
ATOM      3  CA BALA A   1      1.458   0.900   0.000  0.70  0.00           C
ATOM      4  C   ALA A   1      2.009   1.420   0.000  1.00  0.00           C
ATOM      5  O   ALA A   1      1.251   2.390   0.000  1.00  0.00           O
END
"""


def test_parse_structure_resolves_the_state_the_loader_would():
    """Both parsers must reach the same conformer state from the same bytes.

    `parse_structure` requested no `extra_fields`, so occupancy was absent and
    `conformer_state` fell back to `np.zeros`: every alternate at a site tied
    and the winner was decided by sort order, i.e. the letter. The atom counts
    matched either way, so `interface("5fji", ...)` standalone and
    `interface(...)` after `fetch_structure("5fji")` computed buried areas over
    different atoms while both reported the same totals.

    Here B carries the higher occupancy, so the two rules disagree: occupancy
    says B, alphabetical says A.
    """
    here = parse_structure(BACKWARDS_OCCUPANCY_PDB, "pdb")
    loaded = load_structure(BACKWARDS_OCCUPANCY_PDB, "pdb", "asymmetric").array

    assert here.array_length() == loaded.array_length()
    assert resolve_conformers(here)[1] == resolve_conformers(loaded)[1] == "B"


def test_protein_atoms_drops_non_amino_acids():
    array = parse_structure(TINY_PDB, "pdb")
    protein = protein_atoms(array, None, "test")
    assert protein.array_length() == 8  # the water nitrogen is excluded
    assert set(protein.res_name.tolist()) == {"ALA", "GLY"}


def test_missing_chain_names_the_available_ones():
    array = parse_structure(TINY_PDB, "pdb")
    with pytest.raises(SuperpositionError, match="chains present: A, B"):
        protein_atoms(array, "Z", "mobile")


def test_chainless_structure_is_refused():
    # Reuse the line from TINY_PDB rather than hand-writing PDB columns.
    only_water = (
        "\n".join(
            line for line in TINY_PDB.splitlines() if "HOH" in line or line == "END"
        )
        + "\n"
    )
    with pytest.raises(SuperpositionError, match="no amino acids"):
        protein_atoms(parse_structure(only_water, "pdb"), None, "mobile")


# -- fixtures with a known answer --------------------------------------------


def _shifted(text: str, offset: float) -> str:
    """Translate every atom, so the correct superposition is exactly recoverable."""
    out = []
    for line in text.splitlines():
        if line.startswith("ATOM"):
            x = float(line[30:38]) + offset
            out.append(f"{line[:30]}{x:8.3f}{line[38:]}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


@pytest.fixture
def helix_pdb():
    """A 12-residue helix with a varied sequence.

    Deliberately not poly-alanine: anchors come from a sequence alignment, and
    a homopolymer has no unique register, so identical structures can align
    off-by-one and score a non-zero RMSD.
    """
    residues = [
        "ALA",
        "GLY",
        "SER",
        "VAL",
        "LEU",
        "THR",
        "ILE",
        "PRO",
        "PHE",
        "TYR",
        "TRP",
        "MET",
    ]
    lines = []
    n = 1
    for i in range(12):
        angle = np.deg2rad(100 * i)
        z = 1.5 * i
        for name, element, radius in (
            ("N", "N", 1.4),
            ("CA", "C", 2.3),
            ("C", "C", 2.0),
            ("O", "O", 2.6),
        ):
            x = radius * np.cos(angle)
            y = radius * np.sin(angle)
            lines.append(
                f"ATOM  {n:5d}  {name:<3s} {residues[i]} A{i + 1:4d}    "
                f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           {element}"
            )
            n += 1
    return "\n".join(lines) + "\nEND\n"


def test_identical_structures_superpose_exactly(helix_pdb):
    result = superpose(helix_pdb, "pdb", helix_pdb, "pdb")
    assert result.rmsd == pytest.approx(0.0, abs=1e-4)
    assert result.sequence_identity == pytest.approx(1.0)
    # superimpose_homologs iteratively discards outlier anchors, so even a
    # structure against itself keeps most rather than all of them.
    assert 8 <= result.aligned_residues <= 12


def test_a_pure_translation_is_undone(helix_pdb):
    """The fit must recover a known offset, not merely report a small number."""
    moved = _shifted(helix_pdb, 25.0)
    result = superpose(moved, "pdb", helix_pdb, "pdb")
    assert result.rmsd == pytest.approx(0.0, abs=1e-4)


def test_transform_is_a_four_by_four_matrix(helix_pdb):
    result = superpose(helix_pdb, "pdb", helix_pdb, "pdb")
    assert len(result.transform) == 4
    assert all(len(row) == 4 for row in result.transform)


def test_outliers_are_ordered_worst_first(helix_pdb):
    result = superpose(_shifted(helix_pdb, 3.0), "pdb", helix_pdb, "pdb")
    deviations = [o.deviation for o in result.outliers]
    assert deviations == sorted(deviations, reverse=True)


def test_result_dict_rounds_for_reporting(helix_pdb):
    payload = superpose(helix_pdb, "pdb", helix_pdb, "pdb").as_dict()
    assert set(payload) == {
        "mode",
        "rmsd",
        "aligned_residues",
        "sequence_identity",
        "transform",
        "mobile_chains",
        "target_chains",
        "outliers",
    }
    assert payload["mobile_chains"] == ["A"]


def test_unrelated_structures_are_refused(helix_pdb):
    with pytest.raises(SuperpositionError):
        superpose(TINY_PDB, "pdb", helix_pdb, "pdb")


# -- correspondence modes ------------------------------------------------------


def test_an_unknown_mode_is_refused_with_the_valid_ones(helix_pdb):
    with pytest.raises(SuperpositionError, match="Unknown mode 'cealign'"):
        superpose(helix_pdb, "pdb", helix_pdb, "pdb", mode="cealign")


@pytest.mark.parametrize("mode", ["sequence", "structural"])
def test_the_result_says_which_mode_produced_it(helix_pdb, mode):
    """A number whose method is not stated cannot be compared with another."""
    result = superpose(helix_pdb, "pdb", helix_pdb, "pdb", mode=mode)
    assert result.mode == mode
    assert result.as_dict()["mode"] == mode


@pytest.mark.parametrize("mode", ["sequence", "structural"])
def test_a_pure_translation_is_undone_in_either_mode(helix_pdb, mode):
    """Both modes must recover a known offset, not merely report a small number."""
    moved = _shifted(helix_pdb, 25.0)
    result = superpose(moved, "pdb", helix_pdb, "pdb", mode=mode)
    assert result.rmsd == pytest.approx(0.0, abs=1e-4)


def test_the_modes_are_not_the_same_computation(helix_pdb):
    """Guards the tests above: two aliases for one function would pass them all.

    Sequence mode discards anchors as outliers even on a structure against
    itself; structural mode matches on backbone shape and keeps the whole
    helix. That difference in coverage is the observable one.
    """
    by_sequence = superpose(helix_pdb, "pdb", helix_pdb, "pdb", mode="sequence")
    by_shape = superpose(helix_pdb, "pdb", helix_pdb, "pdb", mode="structural")
    assert by_shape.aligned_residues > by_sequence.aligned_residues


# -- the case structural mode exists for --------------------------------------

# Haemoglobin's alpha and beta chains: the textbook remote homologs. Same fold,
# ~140 residues each, sequences diverged far enough that aligning them anchors
# less than half the chain. Structural mode should recover most of the fold.
_GLOBIN_MINIMUM = 120


@pytest.fixture(scope="module")
async def globins() -> tuple[str, str]:
    structure = await fetch_structure_data("1hho")
    return structure.data, structure.format


@NEEDS_NETWORK
async def test_structural_mode_superposes_the_fold_a_sequence_alignment_misses(globins):
    """The gap this mode was added to close.

    Both numbers are asserted: structural mode covering more of the chain is
    the claim, and sequence mode covering less of it is what makes the claim
    worth anything.
    """
    data, fmt = globins
    by_sequence = superpose(
        data, fmt, data, fmt, mobile_chain="A", target_chain="B", mode="sequence"
    )
    by_shape = superpose(
        data, fmt, data, fmt, mobile_chain="A", target_chain="B", mode="structural"
    )
    assert by_sequence.aligned_residues < _GLOBIN_MINIMUM
    assert by_shape.aligned_residues >= _GLOBIN_MINIMUM
    # A fold match, not a coincidence: the two globins really do superpose.
    assert by_shape.rmsd < 3.0


@NEEDS_NETWORK
async def test_structural_mode_pays_for_that_coverage_in_rmsd(globins):
    """It is not strictly better, and the reply should not imply that it is.

    Structural mode maximises what it can superpose, so over the residues it
    keeps it fits worse than sequence mode does over the conserved core it
    picks. Both are honest answers to different questions.
    """
    data, fmt = globins
    by_sequence = superpose(
        data, fmt, data, fmt, mobile_chain="A", target_chain="B", mode="sequence"
    )
    by_shape = superpose(
        data, fmt, data, fmt, mobile_chain="A", target_chain="B", mode="structural"
    )
    assert by_shape.rmsd > by_sequence.rmsd
