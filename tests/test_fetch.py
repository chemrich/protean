"""Fetch tests: local files, PDB/AlphaFold resolution, caching, error paths."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from protean_mcp.fetch import (
    ALPHAFOLD_API,
    FetchError,
    fetch_structure_data,
)

FAKE_CIF = "data_test\n_entry.id TEST\n"

#: What the AlphaFold API actually answers, in the shape it actually uses.
#: Taken from a live response rather than invented: the `cifUrl` is the whole
#: reason this is a request and not a format string.
AF_CIF_URL = "https://alphafold.ebi.ac.uk/files/AF-P69905-F1-model_v6.cif"


def make_transport(
    calls: list[Any], cif_url: str = AF_CIF_URL, api: Any = None
) -> httpx.MockTransport:
    """A transport that tells the AlphaFold API apart from a file download.

    The previous version answered every request with the same mmCIF body,
    which is fine for RCSB and cannot model AlphaFold at all: that path asks a
    JSON API where the file is and then fetches what it is told.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "missing" in url:
            return httpx.Response(404)
        if "/api/prediction/" in url:
            if api is not None:
                answered: httpx.Response = api(request)
                return answered
            return httpx.Response(200, json=[{"cifUrl": cif_url, "latestVersion": 6}])
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
    calls: list[Any] = []
    transport = make_transport(calls)
    s = await fetch_structure_data("1UBQ", cache_dir=tmp_path, transport=transport)
    assert (s.name, s.format, s.source) == ("1ubq", "mmcif", "pdb")
    assert s.data == FAKE_CIF
    assert len(calls) == 1

    s2 = await fetch_structure_data("1ubq", cache_dir=tmp_path, transport=transport)
    assert s2.source == "cache"
    assert len(calls) == 1  # no second network call


async def test_alphafold_asks_the_database_where_the_file_is(tmp_path):
    """Two requests: where is it, then fetch it.

    The URL used to be built from a template pinned to `model_v4`, which the
    database retired — so every AlphaFold fetch failed with "Not found
    upstream", which reads as "no such protein".
    """
    calls: list[Any] = []
    s = await fetch_structure_data(
        "P69905", cache_dir=tmp_path, transport=make_transport(calls)
    )

    assert (s.name, s.source) == ("AF-P69905", "alphafold")
    assert calls == [ALPHAFOLD_API.format(accession="P69905"), AF_CIF_URL]


async def test_alphafold_fetches_whatever_url_the_database_names(tmp_path):
    """Not a version substitution — the id scheme varies too.

    P0DTC2, the SARS-CoV-2 spike, is served as
    `AF-0000000365840314-model_v1.cif`: an internal numeric id, no `-F1`
    fragment, version 1 while its neighbours are on 6. No amount of bumping a
    version number in a template reaches that file, which is why the backlog's
    reading that some accessions were "genuinely absent" was itself a symptom.
    """
    odd = "https://alphafold.ebi.ac.uk/files/AF-0000000365840314-model_v1.cif"
    calls: list[Any] = []
    s = await fetch_structure_data(
        "P0DTC2", cache_dir=tmp_path, transport=make_transport(calls, cif_url=odd)
    )

    assert s.source == "alphafold"
    assert calls[-1] == odd


async def test_a_cached_alphafold_model_asks_nothing_at_all(tmp_path):
    """The extra request is the cost of not going stale, and it is paid once."""
    calls: list[Any] = []
    await fetch_structure_data(
        "P69905", cache_dir=tmp_path, transport=make_transport(calls)
    )
    assert len(calls) == 2

    again = await fetch_structure_data(
        "P69905", cache_dir=tmp_path, transport=make_transport(calls)
    )
    assert again.source == "cache"
    assert len(calls) == 2, "a cached model went back to the network"


async def test_an_accession_the_database_does_not_have_says_so(tmp_path):
    calls: list[Any] = []
    transport = make_transport(calls, api=lambda _r: httpx.Response(404))
    with pytest.raises(FetchError, match="no prediction for Q0AAA0"):
        await fetch_structure_data("Q0AAA0", cache_dir=tmp_path, transport=transport)


async def test_an_empty_listing_is_not_a_crash(tmp_path):
    """The API answers 200 with `[]` rather than 404 for some accessions."""
    calls: list[Any] = []
    transport = make_transport(calls, api=lambda _r: httpx.Response(200, json=[]))
    with pytest.raises(FetchError, match="no prediction for P69905"):
        await fetch_structure_data("P69905", cache_dir=tmp_path, transport=transport)


async def test_a_listing_with_no_cif_url_reports_what_it_did_get(tmp_path):
    """If the API changes shape, say which keys arrived rather than KeyError."""
    calls: list[Any] = []
    transport = make_transport(
        calls,
        api=lambda _r: httpx.Response(
            200, json=[{"pdbUrl": "x", "uniprotAccession": "P69905"}]
        ),
    )
    with pytest.raises(FetchError, match=r"gave no mmCIF URL.*pdbUrl"):
        await fetch_structure_data("P69905", cache_dir=tmp_path, transport=transport)


async def test_unresolvable_identifier(tmp_path):
    with pytest.raises(FetchError, match="Could not resolve"):
        await fetch_structure_data("not_a_thing_123", cache_dir=tmp_path)


async def test_unknown_source(tmp_path):
    with pytest.raises(FetchError, match="Unknown source"):
        await fetch_structure_data("1ubq", source="wat", cache_dir=tmp_path)


async def test_upstream_404(tmp_path):
    # 4-char ID routed to PDB but upstream 404s; use 'missing'-triggering ID.
    calls: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    with pytest.raises(FetchError, match="Not found upstream"):
        await fetch_structure_data(
            "9zzz", cache_dir=tmp_path, transport=httpx.MockTransport(handler)
        )


#: The live check, opt-in. Everything above mocks the upstream, which is right
#: for a suite that has to run offline and is exactly why the v4 breakage sat
#: undetected: **the whole file passed while every real AlphaFold fetch 404'd.**
#: A mock can only prove protean does what protean expects.
network = pytest.mark.skipif(
    os.environ.get("PROTEAN_NETWORK") != "1",
    reason="talks to AlphaFold DB; set PROTEAN_NETWORK=1 to run",
)


@network
async def test_the_database_really_serves_what_we_ask_it_for(tmp_path):
    """Against the real API. The one test that could have caught the pin."""
    s = await fetch_structure_data("P69905", cache_dir=tmp_path)

    assert s.source == "alphafold"
    assert s.data.startswith("data_AF-P69905")
    assert "_atom_site." in s.data


@network
async def test_an_accession_the_old_template_could_not_reach(tmp_path):
    """P0DTC2 is served under an internal id with no `-F1` fragment."""
    s = await fetch_structure_data("P0DTC2", cache_dir=tmp_path)

    assert s.source == "alphafold"
    assert "_atom_site." in s.data
