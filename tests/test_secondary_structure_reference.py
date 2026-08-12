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

That measurement is what motivated porting DSSP in-tree. protean no longer uses
P-SEA, so this module now does double duty: it keeps the record of what the old
reference number was, and it scores the port against `mkdssp` residue by
residue across a corpus.
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

from protean_mcp.analysis.secondary_structure import assign_per_residue
from protean_mcp.fetch import fetch_structure_data
from protean_mcp.selections_numpy import load_structure, select_mask

FIXTURE = "1ubq"

# Measured 2026-08-12 with mkdssp 4.6.1 and biotite 1.7.1. Atom counts are over
# whole residues, which is how `ss` resolves: a residue in the class contributes
# all of its atoms.
DEPOSITED = {"H": 132, "S": 274}

# What P-SEA used to answer, kept so the improvement is pinned rather than
# asserted. It is no longer what protean does.
PSEA = {"H": 89, "S": 217}

# What `ss` answers now, from the in-tree DSSP port.
OURS = {
    "ss H": 148,  # every helix class, which is what PyMOL's `ss H` means
    "ss S": 217,  # strand plus isolated bridge
    "ss alpha": 98,
    "ss 3-10": 50,
    "ss pi": 0,  # 1UBQ has none; 4HHB is the fixture that does
    "ss extended": 203,
    "ss bridge": 14,
}

# Out of 76 amino-acid residues. Every pair disagrees by about the same amount,
# which is the point: 82% is what two *accepted* assignments score against each
# other, so P-SEA's 82% against the header was never evidence of a defect.
AGREEMENT = {("psea", "dssp"): 57, ("psea", "deposited"): 62, ("dssp", "deposited"): 62}

RESIDUES = 76

# (matched, total) residues against mkdssp 4.6.1, measured 2026-08-12. Pinned
# exactly rather than as a threshold: a port that drifts to 99% should fail,
# not pass quietly. 2LYZ is the one structure with a residual, two 3-10
# residues mkdssp calls turn.
CORPUS = {
    "1ubq": (76, 76),
    "1crn": (46, 46),
    "2lyz": (127, 129),
    "1ca2": (256, 256),
    "4hhb": (574, 574),
}

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


def _run_dssp(text: str) -> dict[int | tuple[str, int, str], str]:
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

    codes: dict[int | tuple[str, int, str], str] = {}
    started = False
    for line in lines:
        if line.startswith("  #  RESIDUE"):
            started = True
            continue
        if not started or len(line) < 17 or line[13] == "!":
            continue
        number = line[5:10].strip()
        if not number:
            continue
        code = line[16] if line[16] != " " else "-"
        # DSSP 4's polyproline II helix, which the port does not implement and
        # which never overrides a structured class — it competes with T, S and
        # blank. Folded to unstructured so the comparison is like for like.
        if code == "P":
            code = "-"
        # Keyed both ways: by residue number for the single-chain 1UBQ
        # comparisons, and by (chain, number, insertion) for everything else.
        # 4HHB has four chains that all number from 1.
        codes[int(number)] = code
        codes[(line[11], int(number), line[10].strip())] = code
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


async def test_what_ss_answers_now(parsed):
    """The counts every other assertion here is relative to.

    `ss H` is 148 where P-SEA gave 89. The difference is not a bug fix on the
    same quantity: `ss H` now means every helix class, and 3-10 helices were
    previously unreachable rather than mis-assigned.
    """
    for selection, expected in OURS.items():
        assert int(select_mask(selection, parsed).sum()) == expected, selection


async def test_the_helix_types_partition_the_helix(parsed):
    """The feature this port exists for: alpha, 3-10 and pi are addressable."""
    parts = sum(
        int(select_mask(s, parsed).sum()) for s in ("ss alpha", "ss 3-10", "ss pi")
    )
    assert parts == int(select_mask("ss H", parsed).sum()) == OURS["ss H"]
    assert int(select_mask("ss alpha and ss 3-10", parsed).sum()) == 0


async def test_we_now_beat_what_we_replaced(parsed):
    """P-SEA on the same structure, kept so the improvement is measured.

    Not a tautology: it asserts the old assignment still computes what it
    always did, so the comparison is against a fixed point rather than against
    whatever biotite does today.
    """
    sse: np.ndarray = np.asarray(annotate_sse(parsed))
    per_atom: np.ndarray = np.asarray(spread_residue_wise(parsed, sse))
    assert int((per_atom == "a").sum()) == PSEA["H"]
    assert int((per_atom == "b").sum()) == PSEA["S"]
    # P-SEA could not express either of the two rarer helix types at all.
    assert PSEA["H"] < OURS["ss H"]
    assert OURS["ss 3-10"] > 0


@needs_dssp
async def test_our_assignment_matches_dssp_residue_for_residue(fixture_text, parsed):
    """The port against the reference, on the structure everything else uses."""
    codes = _run_dssp(fixture_text)
    ours = assign_per_residue(parsed)
    compared = matched = 0
    for (chain, number, insertion, _name), code in ours.items():
        reference = codes.get((str(chain), number, insertion))
        if reference is None:
            continue
        compared += 1
        matched += reference == code
    assert compared == RESIDUES
    assert matched == RESIDUES


@needs_dssp
@pytest.mark.parametrize("identifier", sorted(CORPUS))
async def test_the_port_matches_dssp_across_a_corpus(identifier):
    """Eleven structures were measured; five are pinned here.

    Exact (matched, total) rather than a percentage floor: a port that drifts
    from 100% to 99% on 4HHB should fail rather than pass a threshold. The one
    structure with a residual is 2LYZ, where mkdssp calls two of our 3-10
    residues turn.
    """
    text = (await fetch_structure_data(identifier)).data
    array = load_structure(text, "mmcif", "asymmetric").array
    codes = _run_dssp(text)
    ours = assign_per_residue(array)

    compared = matched = 0
    for (chain, number, insertion, _name), code in ours.items():
        reference = codes.get((str(chain), number, insertion))
        if reference is None:
            continue
        compared += 1
        matched += reference == code
    assert (matched, compared) == CORPUS[identifier]


@needs_dssp
async def test_pi_helix_is_found_where_one_exists():
    """1UBQ has no pi-helix, so it cannot show that I is ever assigned.

    4HHB has 22 residues of it, and getting them right needed the priority
    reversal DSSP 4 introduced — with alpha ranked first, 15 of these come out
    as H and every count still looks plausible.
    """
    text = (await fetch_structure_data("4hhb")).data
    array = load_structure(text, "mmcif", "asymmetric").array
    ours = assign_per_residue(array)
    pi = {key for key, code in ours.items() if code == "I"}
    assert len(pi) == 22

    codes = _run_dssp(text)
    theirs = {key for key in pi if codes.get((str(key[0]), key[1], key[2])) == "I"}
    assert theirs == pi


@needs_dssp
async def test_every_assignment_disagrees_with_every_other_by_about_as_much(
    fixture_text, parsed
):
    """The measurement that retired "protean is the outlier".

    DSSP against the header scores the same 82% P-SEA did. An 18% spread is
    what independent assignments of this structure cost, and it was never
    evidence of a defect.
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
