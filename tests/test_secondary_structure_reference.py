"""What `ss` is measured against, and what the old reference number really was.

Backlog item 10 recorded protean as ~18% short of "PyMOL and Mol\\*", on the
strength of the two of them agreeing exactly at 132 helix atoms and 274 strand
atoms on 1UBQ. Neither was computing an assignment. Both were reading
``struct_conf`` and ``struct_sheet_range`` out of the deposited mmCIF — one
depositor's opinion, counted twice. PyMOL's own computed assignment
(``cmd.dss()``) is a third answer again, 135 and 266.

So this module pins the two things that claim rested on:

  the header      parsing the deposited records out of 1UBQ reproduces 132 and
                  274 exactly. That is the evidence the old reference was a
                  file annotation rather than an implementation, and it needs
                  only the network.

  real DSSP       what `mkdssp` actually says, which nothing in this repo had
                  ever measured. Opt-in, because DSSP is deliberately *not* a
                  dependency of protean — it is a scoring tool here, the same
                  way PyMOL is:

                      PROTEAN_DSSP=1 uv run pytest \\
                          tests/test_secondary_structure_reference.py

The headline result, and the reason the item was misjudged: on strand protean
assigns exactly as much as DSSP does (26 residues, 217 atoms), and on helix it
is within one residue of DSSP's alpha-helix (89 atoms against 98). The rest of
the apparent gap is DSSP's 3-10 helices, which P-SEA has no class for at all.

Equal totals are not the same as equal placement, and the difference matters:
protean and DSSP agree on only 20 of those 26 strand residues. Both facts are
asserted below so neither can be quoted without the other.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest
from biotite.structure import (
    annotate_sse,
    filter_amino_acids,
    get_residue_starts,
    spread_residue_wise,
)
from biotite.structure.io.pdbx import CIFFile

from protean_mcp.fetch import fetch_structure_data
from protean_mcp.selections_numpy import load_structure, select_mask

FIXTURE = "1ubq"

# Measured 2026-08-12 with mkdssp 4.6.1 and biotite 1.7.1. Atom counts are over
# whole residues, which is how `ss` resolves: a residue in the class contributes
# all of its atoms.
DEPOSITED = {"H": 132, "S": 274}
PSEA = {"H": 89, "S": 217}
DSSP_ATOMS = {
    "H": 98,  # alpha-helix alone
    "HGI": 148,  # every helix class, which is what PyMOL's `ss H` means
    "E": 203,  # extended strand alone
    "EB": 217,  # strand plus isolated bridge, the closest analogue of `ss S`
}
# Out of 76 amino-acid residues. Every pair disagrees by about the same amount,
# which is the point: 82% is what two *accepted* assignments score against each
# other, so protean's 82% against the header was never evidence of a defect.
AGREEMENT = {("psea", "dssp"): 57, ("psea", "deposited"): 62, ("dssp", "deposited"): 62}

RESIDUES = 76

# Every test here fetches 1UBQ from RCSB, so the whole module sits behind the
# same gate the rest of the network-touching suite uses. The fast `pytest -q`
# job must stay offline.
pytestmark = pytest.mark.skipif(
    os.environ.get("PROTEAN_DIFFERENTIAL") != "1",
    reason="fetches from RCSB; set PROTEAN_DIFFERENTIAL=1 to run",
)

needs_dssp = pytest.mark.skipif(
    os.environ.get("PROTEAN_DSSP") != "1" or shutil.which("mkdssp") is None,
    reason="needs a runnable mkdssp; set PROTEAN_DSSP=1 to run",
)


def _atoms_of(array, residue_ids: set[int]) -> int:
    """Atoms belonging to *residue_ids*, counting amino acids only.

    Waters carry residue numbers from the same space as the protein, so an
    unfiltered ``isin`` folds solvent into a secondary-structure count. On 1UBQ
    the two ranges happen not to overlap, which is why the filter needs a test
    of its own below — without one it is a guard nothing can see.
    """
    return int(
        (np.isin(array.res_id, sorted(residue_ids)) & filter_amino_acids(array)).sum()
    )


def _deposited(cif: CIFFile) -> dict[str, set[int]]:
    """Helix and strand residue ranges as the depositor recorded them."""
    block = cif.block
    helix: set[int] = set()
    strand: set[int] = set()
    conf = block["struct_conf"]
    for begin, end, kind in zip(
        conf["beg_label_seq_id"].as_array(int),
        conf["end_label_seq_id"].as_array(int),
        conf["conf_type_id"].as_array(str),
        strict=True,
    ):
        if "HELX" in kind:
            helix.update(range(begin, end + 1))
    sheet = block["struct_sheet_range"]
    for begin, end in zip(
        sheet["beg_label_seq_id"].as_array(int),
        sheet["end_label_seq_id"].as_array(int),
        strict=True,
    ):
        strand.update(range(begin, end + 1))
    return {"H": helix, "S": strand}


def _run_dssp(text: str) -> dict[int, str]:
    """One DSSP class per residue number, from the classic tabular output.

    Column 16 is the summary class and column 13 is a chain-break marker; the
    table starts after the `  #  RESIDUE` header line.
    """
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "in.cif"
        source.write_text(text)
        out = Path(tmp) / "out.dssp"
        subprocess.run(
            ["mkdssp", "--output-format", "dssp", str(source), str(out)],
            check=True,
            capture_output=True,
        )
        lines = out.read_text().splitlines()

    codes: dict[int, str] = {}
    started = False
    for line in lines:
        if line.startswith("  #  RESIDUE"):
            started = True
            continue
        if not started or len(line) < 17 or line[13] == "!":
            continue
        codes[int(line[5:10])] = line[16]
    return codes


def _three_class(code: str) -> str:
    """DSSP's eight classes folded onto the three `ss` offers."""
    if code in "HGI":
        return "H"
    if code in "EB":
        return "S"
    return "-"


@pytest.fixture(scope="module")
async def fixture_text() -> str:
    return (await fetch_structure_data(FIXTURE)).data


@pytest.fixture(scope="module")
def parsed(fixture_text):
    return load_structure(fixture_text, "mmcif", "asymmetric").array


async def test_the_old_reference_number_is_the_deposited_header(fixture_text, parsed):
    """132 and 274 come out of the file, not out of an algorithm.

    This is the whole correction to backlog item 10. If this test fails, the
    claim that PyMOL and Mol* were echoing the same annotation is wrong and the
    item should be re-argued rather than patched.
    """
    ranges = _deposited(CIFFile.deserialize(fixture_text))
    assert _atoms_of(parsed, ranges["H"]) == DEPOSITED["H"]
    assert _atoms_of(parsed, ranges["S"]) == DEPOSITED["S"]


async def test_counting_a_solvent_residue_number_counts_nothing(parsed):
    """The guard inside `_atoms_of`, exercised where the fixture cannot.

    1UBQ numbers its waters 77-134, clear of the protein's 1-76, so every other
    assertion here passes with or without the amino-acid filter. Asking for a
    water's residue number directly is the only thing that tells them apart.
    """
    waters = parsed[~filter_amino_acids(parsed)]
    assert len(waters) > 0
    solvent_id = int(waters.res_id[0])
    assert _atoms_of(parsed, {solvent_id}) == 0
    assert int(np.isin(parsed.res_id, [solvent_id]).sum()) > 0


async def test_our_assignment_is_unchanged(parsed):
    """The counts every other assertion here is relative to."""
    assert int(select_mask("ss H", parsed).sum()) == PSEA["H"]
    assert int(select_mask("ss S", parsed).sum()) == PSEA["S"]


@needs_dssp
async def test_strand_matches_dssp_in_amount_but_not_in_placement(fixture_text, parsed):
    """protean assigns exactly as much strand as DSSP, in partly other places.

    The amount is why "18% short on strand" was wrong; the placement is why
    "protean's strand is correct" would be too strong.
    """
    codes = _run_dssp(fixture_text)
    strand = {r for r, c in codes.items() if c in "EB"}
    assert _atoms_of(parsed, strand) == DSSP_ATOMS["EB"] == PSEA["S"]

    starts: np.ndarray = get_residue_starts(parsed)
    amino = filter_amino_acids(parsed)[starts]
    sse: np.ndarray = np.asarray(annotate_sse(parsed))
    ours = {
        int(rid)
        for rid, code, is_amino in zip(parsed.res_id[starts], sse, amino, strict=True)
        if is_amino and code == "b"
    }
    assert len(ours) == len(strand) == 26
    assert len(ours & strand) == 20


@needs_dssp
async def test_the_helix_gap_is_three_ten_helix(fixture_text, parsed):
    """P-SEA has no 3-10 class, and that is nearly the entire helix shortfall.

    Against DSSP's alpha-helix alone protean is one residue short. Against every
    helix class it is 59 atoms short, and 50 of those are the 3-10 segments.
    """
    codes = _run_dssp(fixture_text)
    alpha = {r for r, c in codes.items() if c == "H"}
    every = {r for r, c in codes.items() if c in "HGI"}
    assert _atoms_of(parsed, alpha) == DSSP_ATOMS["H"]
    assert _atoms_of(parsed, every) == DSSP_ATOMS["HGI"]
    assert _atoms_of(parsed, every - alpha) == DSSP_ATOMS["HGI"] - DSSP_ATOMS["H"]
    assert PSEA["H"] < DSSP_ATOMS["H"] < DSSP_ATOMS["HGI"]


@needs_dssp
async def test_every_assignment_disagrees_with_every_other_by_about_as_much(
    fixture_text, parsed
):
    """The measurement that retires "protean is the outlier".

    DSSP against the header scores the same 82% protean does. An 18% spread is
    what independent assignments of this structure cost, not a protean defect.
    """
    codes = _run_dssp(fixture_text)
    ranges = _deposited(CIFFile.deserialize(fixture_text))
    starts: np.ndarray = get_residue_starts(parsed)
    amino = filter_amino_acids(parsed)[starts]
    sse: np.ndarray = np.asarray(annotate_sse(parsed))

    rows: dict[str, list[str]] = {"psea": [], "dssp": [], "deposited": []}
    for rid, code, is_amino in zip(parsed.res_id[starts], sse, amino, strict=True):
        if not is_amino:
            continue
        number = int(rid)
        rows["psea"].append({"a": "H", "b": "S", "c": "-"}.get(code, "-"))
        rows["dssp"].append(_three_class(codes.get(number, "-")))
        rows["deposited"].append(
            "H" if number in ranges["H"] else ("S" if number in ranges["S"] else "-")
        )

    assert len(rows["psea"]) == RESIDUES
    for (left, right), expected in AGREEMENT.items():
        matched = sum(a == b for a, b in zip(rows[left], rows[right], strict=True))
        assert matched == expected, f"{left} vs {right}"


@needs_dssp
def test_spread_residue_wise_is_what_makes_these_atom_counts_comparable(parsed):
    """A guard on the comparison itself, not on either assignment.

    Every number in this module is atoms-per-whole-residue. If `ss` ever
    resolved to backbone atoms only, each count here would shrink and the
    module would still pass while comparing two different quantities.
    """
    sse: np.ndarray = np.asarray(annotate_sse(parsed))
    per_atom = np.asarray(spread_residue_wise(parsed, sse))
    assert int((per_atom == "b").sum()) == PSEA["S"]
    assert int(select_mask("ss S", parsed).sum()) == int((per_atom == "b").sum())
