"""Tests for reading volume files and serving them to the viewer.

The failure this file is written against is a volume that arrives as nothing
while every call returns cleanly: a mislabelled format handed to the wrong
parser, a gzipped file passed through undecompressed, or a URL the viewer
fetches and gets a 404 body from. None of those raises on its own.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import aiohttp
import pytest

from protean_mcp.connection import ViewerBridge
from protean_mcp.volumes import VolumeError, read_volume


def write_mrc(path: Path, *, magic: bytes = b"MAP ", payload: int = 8) -> Path:
    """A minimal MRC: a 1024-byte header with the magic in place, plus data."""
    header = bytearray(1024)
    struct.pack_into("<iii", header, 0, 2, 2, 2)  # nx, ny, nz
    header[208:212] = magic
    path.write_bytes(bytes(header) + b"\x00" * payload)
    return path


# -- format detection ------------------------------------------------------


def test_the_mrc_magic_is_trusted_over_the_extension(tmp_path):
    """`.map` is used for other things, and a mislabelled file handed to the
    wrong parser produces an empty volume rather than an error."""
    volume = read_volume(write_mrc(tmp_path / "not-really.dx"))
    assert volume.format == "ccp4"


def test_an_extension_is_used_when_there_is_no_magic(tmp_path):
    path = tmp_path / "potential.dx"
    path.write_text("# OpenDX\nobject 1 class gridpositions counts 2 2 2\n")
    assert read_volume(path).format == "dx"


def test_a_gzipped_map_is_decompressed(tmp_path):
    """EMDB ships emd_XXXXX.map.gz — this is the common case, not an edge one."""
    plain = write_mrc(tmp_path / "emd_30913.map")
    raw = plain.read_bytes()
    packed = tmp_path / "emd_30913.map.gz"
    packed.write_bytes(gzip.compress(raw))

    volume = read_volume(packed)
    assert volume.was_compressed
    assert volume.format == "ccp4"
    assert volume.data == raw, "the viewer must get the decompressed bytes"


def test_the_magic_is_read_after_decompressing_not_before(tmp_path):
    """A .gz file's first bytes are gzip's, so sniffing the compressed stream
    would find no magic and fall through to the extension."""
    packed = tmp_path / "unlabelled.gz"
    packed.write_bytes(gzip.compress(write_mrc(tmp_path / "src.mrc").read_bytes()))
    assert read_volume(packed).format == "ccp4"


def test_an_unidentifiable_file_says_so_rather_than_guessing(tmp_path):
    path = tmp_path / "mystery.bin"
    path.write_bytes(b"\x00" * 4096)
    with pytest.raises(VolumeError, match="cannot tell what format"):
        read_volume(path)


def test_an_explicit_format_overrides_detection(tmp_path):
    assert read_volume(write_mrc(tmp_path / "m.mrc"), format="ccp4").format == "ccp4"


def test_an_unknown_explicit_format_lists_the_known_ones(tmp_path):
    with pytest.raises(VolumeError, match="ccp4"):
        read_volume(write_mrc(tmp_path / "m.mrc"), format="mrc2014")


def test_a_missing_file_is_an_error_not_an_empty_volume(tmp_path):
    with pytest.raises(VolumeError, match="is not a file"):
        read_volume(tmp_path / "absent.mrc")


# -- serving ---------------------------------------------------------------


async def fetch(bridge, url: str):
    """GET a path off the running bridge, as the viewer's browser would."""
    async with (
        aiohttp.ClientSession() as session,
        session.get(f"http://127.0.0.1:{bridge.port}{url}") as response,
    ):
        return response.status, await response.read(), dict(response.headers)


async def test_bytes_are_served_verbatim(bridge):
    url = bridge.publish_volume("m", b"\x01\x02\x03")

    status, body, headers = await fetch(bridge, url)
    assert status == 200
    assert body == b"\x01\x02\x03"
    assert headers["Content-Type"] == "application/octet-stream"


async def test_a_path_is_streamed_from_disk(bridge, tmp_path):
    path = write_mrc(tmp_path / "m.mrc")
    url = bridge.publish_volume("m", path)

    status, body, _ = await fetch(bridge, url)
    assert status == 200
    assert body == path.read_bytes()


async def test_the_volume_route_is_not_swallowed_by_the_static_catch_all(bridge):
    """`/{filename:.+}` also matches `/volumes/m`, and _file_handler 404s.

    This asserts the outcome, not a mechanism: aiohttp 3.14 resolves by
    longest static prefix rather than by registration order, so the specific
    route wins either way. Written the other way round — asserting the routes
    are registered in a particular order — the test would pass for a reason
    that is not the reason, and would keep passing if the ordering stopped
    mattering.
    """
    bridge.publish_volume("m", b"\x01")
    status, body, _ = await fetch(bridge, "/volumes/m")
    assert status == 200 and body == b"\x01"


async def test_an_unknown_handle_is_a_404_not_an_empty_body(bridge):
    """An empty 200 is the dangerous answer: Mol* parses it to nothing and the
    caller sees a volume that loaded and shows no density."""
    status, _, _ = await fetch(bridge, "/volumes/never-loaded")
    assert status == 404


async def test_a_registered_file_that_vanished_is_a_404(bridge, tmp_path):
    path = write_mrc(tmp_path / "m.mrc")
    bridge.publish_volume("m", path)
    path.unlink()

    status, _, _ = await fetch(bridge, "/volumes/m")
    assert status == 404


async def test_forgetting_a_volume_stops_serving_it(bridge):
    bridge.publish_volume("m", b"\x01")
    bridge.forget_volume("m")

    status, _, _ = await fetch(bridge, "/volumes/m")
    assert status == 404


def test_forgetting_an_unknown_volume_is_not_an_error():
    ViewerBridge().forget_volume("never-registered")


async def test_a_handle_with_a_slash_is_encoded_into_the_url(bridge):
    """An unquoted `/` builds a URL the `{handle}` route cannot match.

    It falls through to the static catch-all and 404s, which the viewer then
    reports as a parse failure rather than as the bad name it is — so the URL
    is percent-encoded while the dict keeps the raw handle.
    """
    url = bridge.publish_volume("run 1/final", b"\x01\x02")
    assert url == "/volumes/run%201%2Ffinal"

    status, body, _ = await fetch(bridge, url)
    assert status == 200
    assert body == b"\x01\x02"


async def test_forget_all_releases_every_published_volume(bridge):
    """`clear_viewer` must drop the bytes, not only the viewer's handles.

    Nothing can fetch them once the viewer has cleared, so keeping them is
    retention with no reader — three 400-cubed maps is ~750 MB of it.
    """
    bridge.publish_volume("one", b"\x01")
    bridge.publish_volume("two", b"\x02")

    bridge.forget_all_volumes()

    assert (await fetch(bridge, "/volumes/one"))[0] == 404
    assert (await fetch(bridge, "/volumes/two"))[0] == 404


def test_forgetting_all_volumes_on_an_empty_bridge_is_not_an_error():
    ViewerBridge().forget_all_volumes()
