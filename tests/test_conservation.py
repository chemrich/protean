"""Conservation: sequence extraction, A3M parsing, entropy, and the MSA fetch.

Offline. The MMseqs2 server is a mock transport, so nothing here calls
ColabFold's public API — that would make the suite depend on a free third-party
service and send sequences outbound on every push. One live test exists and is
opt-in:

    PROTEAN_MSA_LIVE=1 uv run pytest tests/test_conservation.py -k live
"""

from __future__ import annotations

import io
import math
import os
import tarfile
from typing import Any

import httpx
import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

from protean_mcp.analysis.conservation import (
    ConservationError,
    chain_sequence,
    fetch_msa,
    parse_a3m,
    score,
    shannon_entropy,
)
from protean_mcp.analysis.superposition import parse_structure
from protean_mcp.fetch import fetch_structure_data

MAX_ENTROPY = math.log2(20)


def _residue(chain: str, res_id: int, res_name: str, ins_code: str = "") -> list[Atom]:
    """One residue as its backbone atoms — enough for filter_amino_acids."""
    return [
        Atom(
            [float(res_id), float(i), 0.0],
            chain_id=chain,
            res_id=res_id,
            ins_code=ins_code,
            res_name=res_name,
            atom_name=name,
            element=name[0],
            hetero=False,
            b_factor=10.0,
            occupancy=1.0,
            atom_id=res_id * 10 + i,
        )
        for i, name in enumerate(("N", "CA", "C", "O"))
    ]


@pytest.fixture
def peptide() -> AtomArray[Any]:
    """A five-residue chain A, plus a chain B that must not be scored."""
    atoms: list[Atom] = []
    for i, comp in enumerate(("ALA", "CYS", "ASP", "GLU", "PHE"), start=1):
        atoms += _residue("A", i, comp)
    atoms += _residue("B", 1, "GLY")
    return atom_array(atoms)


# -- sequence extraction -------------------------------------------------------


def test_chain_sequence_reads_one_letter_codes(peptide):
    sequence, residues = chain_sequence(peptide, "A")
    assert sequence == "ACDEF"
    assert [r[1] for r in residues] == [1, 2, 3, 4, 5]
    assert {r[0] for r in residues} == {"A"}


def test_chain_sequence_ignores_other_chains(peptide):
    sequence, _ = chain_sequence(peptide, "B")
    assert sequence == "G"


def test_unknown_residue_still_occupies_a_column():
    """Dropping it would shift every later score onto the wrong residue."""
    atoms = _residue("A", 1, "ALA") + _residue("A", 2, "MSE") + _residue("A", 3, "CYS")
    sequence, residues = chain_sequence(atom_array(atoms), "A")
    assert len(sequence) == len(residues) == 3
    assert sequence[0] == "A" and sequence[2] == "C"


def test_insertion_coded_residues_are_distinct():
    atoms = _residue("A", 52, "ALA") + _residue("A", 52, "CYS", ins_code="A")
    sequence, residues = chain_sequence(atom_array(atoms), "A")
    assert sequence == "AC"
    assert [r[2] for r in residues] == ["", "A"]


def test_missing_chain_names_the_available_ones(peptide):
    with pytest.raises(ConservationError, match="chains present: A, B"):
        chain_sequence(peptide, "Z")


# -- alignment parsing ---------------------------------------------------------


def test_a3m_insertions_are_stripped():
    """Lower case marks residues inserted relative to the query."""
    msa = parse_a3m(">query\nACDEF\n>hit\nACDdefEF\n")
    assert msa == [list("ACDEF"), list("ACDEF")]


def test_a3m_handles_wrapped_sequence_lines():
    msa = parse_a3m(">query\nACD\nEF\n>hit\nACDEF\n")
    assert msa[0] == list("ACDEF")


# -- entropy -------------------------------------------------------------------


def test_identical_column_is_perfectly_conserved():
    assert shannon_entropy([list("AAA"), list("AAA")]) == [0.0, 0.0, 0.0]


def test_even_split_matches_the_hand_computed_value():
    """Two residues at 50/50 is exactly one bit, normalised by log2(20)."""
    entropy = shannon_entropy([list("A"), list("C")])
    assert entropy[0] == pytest.approx(1.0 / MAX_ENTROPY)


def test_twenty_way_split_is_maximum():
    msa = [[aa] for aa in "ACDEFGHIKLMNPQRSTVWY"]
    assert shannon_entropy(msa)[0] == pytest.approx(1.0)


def test_gaps_do_not_count_as_variation():
    """A column that is half absent is not thereby variable."""
    with_gaps = shannon_entropy([list("A"), list("A"), list("-"), list("-")])
    assert with_gaps[0] == 0.0


def test_all_gap_column_is_reported_as_uninformative():
    assert shannon_entropy([list("-"), list("-")]) == [1.0]


# -- scoring -------------------------------------------------------------------


def _a3m(*sequences: str) -> str:
    return "".join(f">s{i}\n{s}\n" for i, s in enumerate(sequences))


def test_score_attaches_entropy_to_the_right_residues(peptide):
    # Column 0 identical, the rest varied.
    result = score(peptide, "A", _a3m("ACDEF", "AWWWW"), source="test")
    assert result.msa_depth == 2
    assert [s.seq for s in result.scores] == [1, 2, 3, 4, 5]
    assert result.scores[0].comp == "ALA"
    assert result.scores[0].entropy == 0.0
    assert result.scores[0].conservation == 1.0
    assert all(s.entropy > 0 for s in result.scores[1:])


def test_score_refuses_a_single_sequence_alignment(peptide):
    with pytest.raises(ConservationError, match="at least two"):
        score(peptide, "A", _a3m("ACDEF"))


def test_score_refuses_when_the_alignment_is_shorter_than_the_chain(peptide):
    """The failure this guard exists for: silently scoring the wrong residues.

    A truncated alignment would otherwise assign column i to residue i and
    leave the tail unscored or misaligned, with every number looking sane.
    """
    with pytest.raises(ConservationError, match="wrong residues"):
        score(peptide, "A", _a3m("ACD", "AWW"))


def test_shallow_alignment_is_flagged_in_the_result(peptide):
    payload = score(peptide, "A", _a3m("ACDEF", "AWWWW"), source="test").as_dict()
    assert "warning" in payload
    assert "shallow" in payload["warning"]


def test_deep_alignment_carries_no_warning(peptide):
    deep = _a3m("ACDEF", *["AWWWW"] * 20)
    assert "warning" not in score(peptide, "A", deep, source="test").as_dict()


# -- fetching ------------------------------------------------------------------


def _tar_gz(a3m: str) -> bytes:
    buf = io.BytesIO()
    raw = a3m.encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("uniref.a3m")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def _server(a3m: str, calls: list[str]) -> httpx.MockTransport:
    """A mock MMseqs2 server: submit, one PENDING poll, then COMPLETE."""
    polls = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/ticket/msa":
            return httpx.Response(200, json={"id": "t123"})
        if request.url.path == "/ticket/t123":
            polls["n"] += 1
            status = "COMPLETE" if polls["n"] > 1 else "PENDING"
            return httpx.Response(200, json={"status": status})
        if request.url.path == "/result/download/t123":
            return httpx.Response(200, content=_tar_gz(a3m))
        return httpx.Response(404)

    return httpx.MockTransport(handle)


SEQUENCE = "ACDEFGHIKLMNPQRSTVWY"


async def test_fetch_submits_polls_and_unpacks(tmp_path, monkeypatch):
    monkeypatch.setattr("protean_mcp.analysis.conservation._POLL_INTERVAL", 0.0)
    calls: list[str] = []
    a3m, source = await fetch_msa(
        SEQUENCE, tmp_path, transport=_server(_a3m(SEQUENCE, SEQUENCE), calls)
    )
    assert source == "search"
    assert ">s0" in a3m
    assert calls == [
        "/ticket/msa",
        "/ticket/t123",
        "/ticket/t123",
        "/result/download/t123",
    ]


async def test_second_call_is_served_from_disk(tmp_path, monkeypatch):
    """The search is the slow part, so the cache has to survive the process."""
    monkeypatch.setattr("protean_mcp.analysis.conservation._POLL_INTERVAL", 0.0)
    first: list[str] = []
    await fetch_msa(SEQUENCE, tmp_path, transport=_server(_a3m(SEQUENCE), first))

    second: list[str] = []
    a3m, source = await fetch_msa(
        SEQUENCE, tmp_path, transport=_server(_a3m("WRONG"), second)
    )
    assert source == "cache"
    assert second == [], "the cached alignment should not have hit the server"
    assert "WRONG" not in a3m


async def test_force_refresh_goes_back_to_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr("protean_mcp.analysis.conservation._POLL_INTERVAL", 0.0)
    calls: list[str] = []
    await fetch_msa(SEQUENCE, tmp_path, transport=_server(_a3m(SEQUENCE), calls))
    await fetch_msa(
        SEQUENCE,
        tmp_path,
        force_refresh=True,
        transport=_server(_a3m(SEQUENCE), calls),
    )
    assert calls.count("/ticket/msa") == 2


async def test_env_and_all_modes_cache_separately(tmp_path, monkeypatch):
    """They are different searches, so one must not be served for the other."""
    monkeypatch.setattr("protean_mcp.analysis.conservation._POLL_INTERVAL", 0.0)
    calls: list[str] = []
    await fetch_msa(
        SEQUENCE, tmp_path, use_env=True, transport=_server(_a3m(SEQUENCE), calls)
    )
    _, source = await fetch_msa(
        SEQUENCE, tmp_path, use_env=False, transport=_server(_a3m(SEQUENCE), calls)
    )
    assert source == "search"


async def test_short_sequence_is_refused_before_any_request(tmp_path):
    calls: list[str] = []
    with pytest.raises(ConservationError, match="at least"):
        await fetch_msa("ACDEF", tmp_path, transport=_server("", calls))
    assert calls == []


async def test_server_error_surfaces_as_conservation_error(tmp_path, monkeypatch):
    monkeypatch.setattr("protean_mcp.analysis.conservation._POLL_INTERVAL", 0.0)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ticket/msa":
            return httpx.Response(200, json={"id": "t123"})
        return httpx.Response(200, json={"status": "ERROR"})

    with pytest.raises(ConservationError, match="failed"):
        await fetch_msa(SEQUENCE, tmp_path, transport=httpx.MockTransport(handle))


async def test_result_without_an_alignment_is_an_error(tmp_path, monkeypatch):
    """An empty tar would otherwise become an empty MSA and score everything 1.0."""
    monkeypatch.setattr("protean_mcp.analysis.conservation._POLL_INTERVAL", 0.0)
    empty = io.BytesIO()
    with tarfile.open(fileobj=empty, mode="w:gz"):
        pass

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ticket/msa":
            return httpx.Response(200, json={"id": "t123"})
        if request.url.path.startswith("/ticket/"):
            return httpx.Response(200, json={"status": "COMPLETE"})
        return httpx.Response(200, content=empty.getvalue())

    with pytest.raises(ConservationError, match="no alignment"):
        await fetch_msa(SEQUENCE, tmp_path, transport=httpx.MockTransport(handle))


# -- opt-in live check ---------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("PROTEAN_MSA_LIVE") != "1",
    reason="calls ColabFold's public API; set PROTEAN_MSA_LIVE=1 to run",
)
async def test_live_colabfold_search_returns_a_usable_alignment(tmp_path):
    """The one test that proves the mock matches reality. Minutes, not seconds."""
    structure = await fetch_structure_data("1ubq")
    array = parse_structure(structure.data, structure.format)
    sequence, residues = chain_sequence(array, "A")

    a3m, source = await fetch_msa(sequence, tmp_path)
    assert source == "search"
    result = score(array, "A", a3m)
    assert result.msa_depth > 1
    assert len(result.scores) == len(residues)
    assert all(0.0 <= s.entropy <= 1.0 for s in result.scores)
