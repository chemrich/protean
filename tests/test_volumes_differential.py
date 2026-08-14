"""Does a volume that loaded cleanly actually hold the numbers we sent?

``tests/test_volumes.py`` proves the Python half: the right bytes are read,
gzip is unwrapped, a format is identified. None of that says Mol\\* parsed
anything. A volume has several ways to arrive as *nothing* while every call
returns success — the wrong parser accepts the buffer and yields an empty
grid, a 404 body parses as zero voxels, an endianness mismatch produces a grid
of garbage — and the reply is a dict either way.

So every claim here is read back off the grid Mol\\* built.

**The trap this file is written around.** An MRC header *carries* DMIN, DMAX,
DMEAN and RMS as stored fields. If Mol\\* echoed those, a test that wrote them
and then asserted them would be comparing the file to itself and would pass
over a parser that never looked at a single voxel. So the fixtures below write
**deliberately false** header statistics and require the reported numbers to
match the *data*. If Mol\\* is reporting header fields, these tests fail and
say so, which is the answer we want either way.

Requires a real browser and the network:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_volumes_differential.py
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from protean_mcp.connection import ViewerError

from .browser import BROWSER_MARKS, viewer_session

pytestmark = BROWSER_MARKS

# Deliberately unequal, and all different from each other, so a transposed or
# swapped axis order shows up as the wrong dimensions rather than as the same
# three numbers in a different order. A cube could not fail that way.
NX, NY, NZ = 5, 7, 9

# Sentinels written into the header's statistics fields. None of them can arise
# from the data below, so seeing one reported is proof the viewer read the
# header instead of the voxels.
FALSE_DMIN = -999.0
FALSE_DMAX = 999.0
FALSE_DMEAN = 42.0
FALSE_RMS = 7.0


def _grid() -> np.ndarray:
    """A deterministic, non-uniform grid, indexed ``[z][y][x]``.

    Non-uniform matters: a constant grid has a sigma of zero, which is also
    what an unparsed grid has, so it could not distinguish them.
    """
    rng = np.random.default_rng(20260814)
    return rng.normal(loc=0.5, scale=0.25, size=(NZ, NY, NX)).astype("<f4")


def _write_mrc(path, data: np.ndarray, *, lie_in_header: bool = True) -> bytes:
    """Write a valid MODE 2 (float32) MRC/CCP4 file and return its bytes.

    Only the fields a reader needs are set; the rest of the 1024-byte header
    stays zero, which is what a real file's unused words look like too.
    """
    header = bytearray(1024)
    struct.pack_into("<iii", header, 0, NX, NY, NZ)  # columns, rows, sections
    struct.pack_into("<i", header, 12, 2)  # MODE 2 = float32
    struct.pack_into("<iii", header, 28, NX, NY, NZ)  # MX, MY, MZ sampling
    struct.pack_into("<fff", header, 40, float(NX), float(NY), float(NZ))  # cell A
    struct.pack_into("<fff", header, 52, 90.0, 90.0, 90.0)  # cell angles
    struct.pack_into("<iii", header, 64, 1, 2, 3)  # MAPC, MAPR, MAPS

    if lie_in_header:
        stats = (FALSE_DMIN, FALSE_DMAX, FALSE_DMEAN)
        rms = FALSE_RMS
    else:
        stats = (float(data.min()), float(data.max()), float(data.mean()))
        rms = float(data.std())
    struct.pack_into("<fff", header, 76, *stats)  # DMIN, DMAX, DMEAN
    struct.pack_into("<i", header, 88, 1)  # ISPG
    struct.pack_into("<i", header, 92, 0)  # NSYMBT: no symmetry records
    header[208:212] = b"MAP "
    header[212:216] = b"\x44\x44\x00\x00"  # MACHST: little-endian
    struct.pack_into("<f", header, 216, rms)
    struct.pack_into("<i", header, 220, 0)  # NLABL

    # X fastest, then Y, then Z — which is C order over an array shaped
    # (nz, ny, nx), the way `_grid` builds it.
    raw = bytes(header) + data.tobytes(order="C")
    path.write_bytes(raw)
    return raw


async def _load(session, name: str, raw: bytes, fmt: str = "ccp4"):
    """Publish bytes on the bridge and have the viewer fetch and parse them."""
    url = session.bridge.publish_volume(name, raw)
    return await session.request("load_volume", {"name": name, "url": url, "format": fmt})


# 1COI is 747 atoms, the smallest fixture this repo keeps. The structure is
# incidental — `viewer_session` loads one — so the cheapest is the right one.
STRUCTURE = "1coi"


@pytest.mark.asyncio
async def test_the_reported_statistics_are_the_data_not_the_header(tmp_path):
    """The whole point: statistics must come from the voxels.

    Every asserted number is computed here by numpy and independently by Mol\\*
    from the bytes it downloaded. They can only agree if Mol\\* actually walked
    the grid.
    """
    data = _grid()
    raw = _write_mrc(tmp_path / "lying.map", data)

    async with viewer_session(STRUCTURE) as session:
        reply = await _load(session, "lying", raw)

    assert reply["dimensions"] == [NX, NY, NZ], reply

    # The header claims -999/999/42/7. If any of those comes back, the viewer
    # is echoing stored fields and has told us nothing about the data.
    for field, claimed in (
        ("min", FALSE_DMIN),
        ("max", FALSE_DMAX),
        ("mean", FALSE_DMEAN),
        ("sigma", FALSE_RMS),
    ):
        assert reply[field] != pytest.approx(claimed), (
            f"{field} came back as the header's false value {claimed}, so the "
            f"reply describes the header rather than the volume: {reply}"
        )

    assert reply["min"] == pytest.approx(float(data.min()), abs=1e-5), reply
    assert reply["max"] == pytest.approx(float(data.max()), abs=1e-5), reply
    assert reply["mean"] == pytest.approx(float(data.mean()), abs=1e-5), reply
    assert reply["sigma"] == pytest.approx(float(data.std()), abs=1e-5), reply


@pytest.mark.asyncio
async def test_a_volume_that_parsed_to_nothing_is_not_reported_as_loaded(tmp_path):
    """Garbage must raise, not load as an empty grid.

    This is the failure the module docstring names. Bytes that are not a volume
    at all are handed over with a format that claims they are.
    """
    async with viewer_session(STRUCTURE) as session:
        with pytest.raises(ViewerError, match="parsed to nothing"):
            await _load(session, "junk", b"not a volume, not even close" * 64)

        # And it must not be left listed as though it had worked.
        listed = await session.request("list_volumes")
        assert [v["name"] for v in listed["volumes"]] == [], listed


@pytest.mark.asyncio
async def test_the_viewer_lists_what_it_holds_and_forgets_what_it_removes(tmp_path):
    """`list_volumes` and `remove_volume` describe the viewer, not our bookkeeping."""
    data = _grid()
    raw = _write_mrc(tmp_path / "one.map", data)

    async with viewer_session(STRUCTURE) as session:
        await _load(session, "one", raw)
        await _load(session, "two", raw)

        listed = await session.request("list_volumes")
        assert sorted(v["name"] for v in listed["volumes"]) == ["one", "two"], listed

        await session.request("remove_volume", {"name": "one"})
        after = await session.request("list_volumes")
        assert [v["name"] for v in after["volumes"]] == ["two"], after

        # And the one still held must still describe its own data, not a
        # stale copy of the removed one's.
        info = await session.request("volume_info", {"name": "two"})
        assert info["dimensions"] == [NX, NY, NZ], info
        assert info["sigma"] == pytest.approx(float(data.std()), abs=1e-5), info
