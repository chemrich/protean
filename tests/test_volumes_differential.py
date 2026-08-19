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
from protean_mcp.fetch import fetch_structure_data

from .browser import BROWSER_MARKS, viewer_session
from .pixels import Render, decode, difference

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
async def test_loading_a_structure_does_not_leave_a_volume_handle_behind(tmp_path):
    """A handle must not outlive the state tree it points into.

    `load_structure` calls `plugin.clear()`, which deletes every volume node.
    The handle map lives in the dispatcher and Mol\\* cannot know about it, so
    without an explicit forget the statistics keep answering — computed from a
    ``data`` object the map itself holds alive — for a volume the viewer no
    longer has. Full marks, plausible numbers, nothing there.

    **Order is the whole test.** The volume has to be loaded *after* the session
    is up, because ``viewer_session`` loads its structure during setup, and
    structure-then-volume is the one ordering that cannot fail.
    """
    data = _grid()
    raw = _write_mrc(tmp_path / "orphan.map", data)

    async with viewer_session(STRUCTURE) as session:
        await _load(session, "orphan", raw)
        assert (await session.request("volume_info", {"name": "orphan"}))["voxels"] == (
            NX * NY * NZ
        )

        structure = await fetch_structure_data(STRUCTURE)
        await session.request(
            "load_structure",
            {
                "name": STRUCTURE,
                "format": structure.format,
                "data": structure.data,
                "assembly": "asymmetric",
            },
            timeout=120,
        )

        listed = await session.request("list_volumes")
        assert listed["volumes"] == [], (
            f"the volume survived a structure load as a handle into a state tree "
            f"that no longer contains it: {listed}"
        )
        with pytest.raises(ViewerError, match="No volume named"):
            await session.request("volume_info", {"name": "orphan"})


@pytest.mark.asyncio
async def test_provenance_is_carried_by_the_handle_and_defaults_to_unknown(tmp_path):
    """The viewer half: a declared provenance sticks to the handle.

    **What this test cannot check**, despite the baited filename below: that the
    filename is never used as evidence. The viewer is only ever sent a handle
    and a URL — it never sees the path — so it *cannot* guess, and a mutation
    that makes it try passes this test unchanged. That was verified, not
    assumed. The invariant is asserted in
    ``test_server.py::test_load_volume_never_infers_provenance_from_the_filename``,
    which drives the real tool, where the filename is visible and a guess is
    therefore possible.

    What this does check: an undeclared volume reads `unknown` rather than
    absent, a declared one survives into `volume_info` and `list_volumes`, and
    two volumes keep their own answers rather than the last one written.
    """
    data = _grid()
    baited = tmp_path / "emd_30913_deepemhancer_sharpened.map"
    _write_mrc(baited, data)

    async with viewer_session(STRUCTURE) as session:
        url = session.bridge.publish_volume("baited", baited.read_bytes())
        undeclared = await session.request(
            "load_volume", {"name": "baited", "url": url, "format": "ccp4"}
        )
        assert undeclared["provenance"] == "unknown", (
            f"an undeclared volume must read 'unknown' rather than come back "
            f"without the field, so a caller cannot mistake silence for a "
            f"measurement: {undeclared}"
        )

        # A declared one is carried through, and survives into volume_info —
        # the handle has to remember it, not just the call that set it.
        url2 = session.bridge.publish_volume("declared", baited.read_bytes())
        await session.request(
            "load_volume",
            {
                "name": "declared",
                "url": url2,
                "format": "ccp4",
                "provenance": "nn_enhanced",
            },
        )
        info = await session.request("volume_info", {"name": "declared"})
        assert info["provenance"] == "nn_enhanced", info

        listed = await session.request("list_volumes")
        by_name = {v["name"]: v["provenance"] for v in listed["volumes"]}
        assert by_name == {"baited": "unknown", "declared": "nn_enhanced"}, listed


@pytest.mark.asyncio
async def test_a_handle_with_a_slash_still_reaches_the_viewer(tmp_path):
    """The handle is percent-encoded into the URL.

    An unquoted `/` builds `/volumes/run 1/final`, which the `{handle}` route
    cannot match; the request falls through to the static catch-all, 404s, and
    surfaces as a parse failure rather than as the bad name it is.
    """
    data = _grid()
    raw = _write_mrc(tmp_path / "slashed.map", data)

    async with viewer_session(STRUCTURE) as session:
        reply = await _load(session, "run 1/final", raw)
        assert reply["dimensions"] == [NX, NY, NZ], reply
        assert reply["sigma"] == pytest.approx(float(data.std()), abs=1e-5), reply

        info = await session.request("volume_info", {"name": "run 1/final"})
        assert info["voxels"] == NX * NY * NZ, info


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


# -- contouring ------------------------------------------------------------

# Big enough to be worth looking at. The stats fixtures above are 5x7x9 voxels
# in a 5x7x9 A box, which is correct for arithmetic and invisible on a canvas
# next to a 747-atom peptide — an isosurface of it could be perfectly right and
# draw nothing the camera can see, which is a test that fails for the wrong
# reason.
BLOB_N = 32
BLOB_CELL = 64.0


def _blob() -> np.ndarray:
    """A single Gaussian blob, indexed ``[z][y][x]``.

    A blob rather than noise: contouring noise at 1 sigma paints most of the
    box and at 3 sigma paints speckle, and neither reads as a shape. This has
    an unambiguous inside.
    """
    axis = np.arange(BLOB_N) - (BLOB_N - 1) / 2
    z, y, x = np.meshgrid(axis, axis, axis, indexing="ij")
    blob: np.ndarray = np.exp(-(x**2 + y**2 + z**2) / (2 * 5.0**2)).astype("<f4")
    return blob


def _write_blob(path) -> bytes:
    data = _blob()
    header = bytearray(1024)
    struct.pack_into("<iii", header, 0, BLOB_N, BLOB_N, BLOB_N)
    struct.pack_into("<i", header, 12, 2)
    struct.pack_into("<iii", header, 28, BLOB_N, BLOB_N, BLOB_N)
    struct.pack_into("<fff", header, 40, BLOB_CELL, BLOB_CELL, BLOB_CELL)
    struct.pack_into("<fff", header, 52, 90.0, 90.0, 90.0)
    struct.pack_into("<iii", header, 64, 1, 2, 3)
    # Header statistics deliberately false, as everywhere else in this file.
    struct.pack_into("<fff", header, 76, FALSE_DMIN, FALSE_DMAX, FALSE_DMEAN)
    struct.pack_into("<i", header, 88, 1)
    header[208:212] = b"MAP "
    header[212:216] = b"\x44\x44\x00\x00"
    struct.pack_into("<f", header, 216, FALSE_RMS)
    raw = bytes(header) + data.tobytes(order="C")
    path.write_bytes(raw)
    return raw


async def _shot(session) -> Render:
    return decode((await session.request("screenshot", {}))["data_uri"])


@pytest.mark.asyncio
async def test_an_isosurface_draws_and_its_units_agree(tmp_path):
    """The headline claim, and the regression §5 of docs/cryoem.md asks for.

    Contouring at a sigma level and at the *same* level expressed absolutely
    must put identical geometry on the canvas. That is the test that would have
    caught the EMD-30913 trap, where a published absolute level typed in as
    sigma contours noise and looks like an ordinary bad map.
    """
    data = _blob()
    raw = _write_blob(tmp_path / "blob.map")
    sigma, mean = float(data.std()), float(data.mean())

    async with viewer_session(STRUCTURE) as session:
        await _load(session, "blob", raw)
        await session.request("reset_view")
        before = await _shot(session)

        in_sigma = await session.request(
            "isosurface",
            {"name": "blob", "level": 3.0, "unit": "sigma", "style": "surface"},
        )
        await session.request("reset_view")
        drawn = await _shot(session)

        # It drew *something*: a successful call over a blank canvas is the
        # default failure for this feature, not an unlucky one.
        assert difference(before, drawn) > 0.005, (
            f"contouring changed {difference(before, drawn):.4%} of the frame, "
            f"which is indistinguishable from having drawn nothing"
        )

        # The conversion used the voxels, not the header's -999/999/42/7.
        assert in_sigma["absolute"] == pytest.approx(3.0 * sigma + mean, rel=1e-4)
        assert in_sigma["sigma"] == pytest.approx(sigma, abs=1e-5)
        assert in_sigma["stated_absolute"] == pytest.approx(3.0 * FALSE_RMS + FALSE_DMEAN)
        assert abs(in_sigma["stated_absolute"] - in_sigma["absolute"]) > 1.0, in_sigma

        # Now the same contour, named in the other unit.
        await session.request(
            "isosurface",
            {
                "name": "blob",
                "level": in_sigma["absolute"],
                "unit": "absolute",
                "style": "surface",
            },
        )
        await session.request("reset_view")
        same = await _shot(session)

    assert difference(drawn, same) < 0.002, (
        f"the same contour named in sigma and in absolute drew differently "
        f"({difference(drawn, same):.4%} of the frame), so one of the two "
        f"conversions is wrong"
    )


@pytest.mark.asyncio
async def test_a_volume_in_an_emptied_viewer_is_framed(tmp_path):
    """The case the first-fit branch exists for, which could not run.

    protean takes the camera off Mol\\*'s automatic fitting (backlog 26), and
    the only explicit fit is inside `load_structure`. So anything drawn without
    loading a structure — a map into an emptied viewer — depends on the
    dispatcher noticing that nothing has ever framed this scene.

    That check tested `radiusMax`, which `Camera.createDefaultSnapshot` sets to
    **10**, so it was never true and the branch never ran: geometry built,
    camera left where it was, and `isosurface` reporting success. A code review
    found it; `radius` is the field that defaults to 0.

    **Asserted on the camera having moved, not on it being non-zero.** The
    first version of this test checked `radius > 0` and passed against the dead
    guard, because clearing the viewer does not reset the camera — the radius
    left over from the structure this session loaded satisfied it. What
    separates fitted-to-the-map from pointing-wherever-it-was is that the fit
    changes it.
    """
    async with viewer_session(STRUCTURE) as session:
        await session.request("clear")
        before = await session.request("camera_state")

        raw = _write_mrc(tmp_path / "alone.map", _grid())
        await _load(session, "alone", raw)
        await session.request(
            "isosurface",
            {"name": "alone", "level": 1.0, "unit": "sigma", "style": "surface"},
        )
        after = await session.request("camera_state")

    assert before["radius"], "nothing had framed anything, so this proves nothing"
    # The *target*, specifically. Radius alone moves either way: bounding the
    # camera sets `radiusMax`, and the camera clamps its radius to it — so a
    # version that never framed anything still changed that number. Where the
    # camera is *pointing* is what separates fitted-to-the-map from
    # left-on-the-structure-that-was-cleared.
    assert after["target"] != before["target"], (
        "the camera still points where the cleared structure was, so the map "
        f"was drawn somewhere off to one side: {before} -> {after}"
    )
