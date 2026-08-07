"""Fetch tests: local files, PDB/AlphaFold resolution, caching, error paths."""

from __future__ import annotations

import httpx
import pytest

from protean_mcp.fetch import (
    ALPHAFOLD_URL,
    FetchError,
    fetch_structure_data,
)

FAKE_CIF = "data_test\n_entry.id TEST\n"


def make_transport(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "missing" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, text=FAKE_CIF)

    return httpx.MockTransport(handler)


async def test_local_pdb_file(tmp_path):
    f = tmp_path / "model.pdb"
    f.write_text("ATOM      1  N   MET A   1\n")
    s = await fetch_structure_data(str(f), cache_dir=tmp_path / "cache")
    assert (s.name, s.format, s.source) == ("model", "pdb", "file")


async def test_local_cif_file(tmp_path):
    f = tmp_path / "model.cif"
    f.write_text(FAKE_CIF)
    s = await fetch_structure_data(str(f), cache_dir=tmp_path / "cache")
    assert s.format == "mmcif"


async def test_unsupported_extension(tmp_path):
    f = tmp_path / "model.xyz"
    f.write_text("nope")
    with pytest.raises(FetchError, match="Unsupported file extension"):
        await fetch_structure_data(str(f), cache_dir=tmp_path / "cache")


async def test_missing_file_with_explicit_source(tmp_path):
    with pytest.raises(FetchError, match="File not found"):
        await fetch_structure_data("/no/such/file.pdb", source="file")


async def test_pdb_id_fetch_and_cache(tmp_path):
    calls: list = []
    transport = make_transport(calls)
    s = await fetch_structure_data("1UBQ", cache_dir=tmp_path, transport=transport)
    assert (s.name, s.format, s.source) == ("1ubq", "mmcif", "pdb")
    assert s.data == FAKE_CIF
    assert len(calls) == 1

    s2 = await fetch_structure_data("1ubq", cache_dir=tmp_path, transport=transport)
    assert s2.source == "cache"
    assert len(calls) == 1  # no second network call


async def test_alphafold_accession(tmp_path):
    calls: list = []
    s = await fetch_structure_data(
        "P69905", cache_dir=tmp_path, transport=make_transport(calls)
    )
    assert (s.name, s.source) == ("AF-P69905", "alphafold")
    assert calls == [ALPHAFOLD_URL.format(accession="P69905")]


async def test_unresolvable_identifier(tmp_path):
    with pytest.raises(FetchError, match="Could not resolve"):
        await fetch_structure_data("not_a_thing_123", cache_dir=tmp_path)


async def test_unknown_source(tmp_path):
    with pytest.raises(FetchError, match="Unknown source"):
        await fetch_structure_data("1ubq", source="wat", cache_dir=tmp_path)


async def test_upstream_404(tmp_path):
    # 4-char ID routed to PDB but upstream 404s; use 'missing'-triggering ID.
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    with pytest.raises(FetchError, match="Not found upstream"):
        await fetch_structure_data(
            "9zzz", cache_dir=tmp_path, transport=httpx.MockTransport(handler)
        )
