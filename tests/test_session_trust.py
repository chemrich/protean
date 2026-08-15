"""A session file is untrusted input, and load_session has to treat it as such.

The attack these guard against was demonstrated against a live viewer on
2026-08-15: a .protean file whose embedded Mol* state tree names a URL makes
the browser fetch it and draw whatever comes back, while load_session returns
a normal-looking reply. Mol* applies the tree as given — it re-runs any
transform whose `version` differs from the one in the current state — so the
file's author, not the user, chooses what is on screen.
"""

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from protean_mcp.connection import ViewerError
from protean_mcp.server import (
    SESSION_FORMAT,
    SESSION_VERSION,
    _decompress_session,
    _remote_references,
    load_session,
)


def write_session(path: Path, snapshot: dict[str, Any]) -> Path:
    document = {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "created": "2026-08-15T00:00:00+00:00",
        "handles": {},
        "molstar": snapshot,
    }
    path.write_bytes(gzip.compress(json.dumps(document).encode()))
    return path


def tree(*transforms: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"tree": {"transforms": list(transforms)}}}


def test_a_session_that_embeds_its_data_carries_no_references():
    """What save_session actually writes: raw-data, no URL anywhere."""
    snapshot = tree(
        {"transformer": "ms-plugin.raw-data", "params": {"data": "data_1UBQ\n"}},
        {"transformer": "ms-plugin.parse-cif", "params": {}},
    )
    assert _remote_references(snapshot) == []


def test_a_volume_route_is_this_bridge_and_is_allowed():
    """Measured from a real session: a loaded volume is a relative /volumes path.

    This is the reason the check cannot simply refuse every URL — sessions with
    a volume in them legitimately contain one.
    """
    snapshot = tree(
        {"transformer": "ms-plugin.download", "params": {"url": "/volumes/fixture"}}
    )
    assert _remote_references(snapshot) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example/beacon",
        "https://evil.example/beacon",
        "//evil.example/beacon",
        "file:///Users/someone/.ssh/id_rsa",
        "http://127.0.0.1:9878/volumes/fixture",  # absolute, so not necessarily us
        "/volumes/../../etc/passwd",
    ],
)
def test_a_url_pointing_anywhere_else_is_found(url):
    snapshot = tree({"transformer": "ms-plugin.download", "params": {"url": url}})
    assert _remote_references(snapshot) == [
        f"snapshot.data.tree.transforms[0].params.url = {url}"
    ]


def test_an_asset_url_is_found_through_its_wrapper():
    """Mol* also carries a URL as an Asset.Url, which nests it one deeper."""
    snapshot = tree(
        {
            "transformer": "ms-plugin.download",
            "params": {"url": {"kind": "url", "url": "http://evil.example/x"}},
        }
    )
    assert _remote_references(snapshot) == [
        "snapshot.data.tree.transforms[0].params.url.url = http://evil.example/x"
    ]


def test_the_mvs_uri_param_is_found_too():
    """The MVS extension names its URL `uri`; the prebuilt bundle registers it."""
    snapshot = tree({"transformer": "mvs-primitives", "params": {"uri": "http://x/y"}})
    assert _remote_references(snapshot) == [
        "snapshot.data.tree.transforms[0].params.uri = http://x/y"
    ]


def test_a_url_buried_in_a_list_is_found():
    """DownloadBlob takes a list of sources, so depth alone must not hide one."""
    snapshot = tree(
        {
            "transformer": "ms-plugin.download-blob",
            "params": {"sources": [{"id": "a", "url": "http://evil.example/z"}]},
        }
    )
    assert len(_remote_references(snapshot)) == 1


async def test_load_session_refuses_a_file_that_reaches_outside_itself(tmp_path):
    """The end-to-end refusal, and it must land before the viewer is called.

    No viewer is connected here, so if the guard did not run first this would
    fail with "No viewer connected" instead.
    """
    hostile = write_session(
        tmp_path / "hostile.protean",
        tree(
            {
                "transformer": "ms-plugin.download",
                "params": {"url": "http://evil.example/beacon"},
                "version": "any-new-version",
            }
        ),
    )
    with pytest.raises(ViewerError, match="fetch from somewhere else"):
        await load_session(str(hostile))


async def test_the_refusal_names_the_url_it_objected_to(tmp_path):
    """A refusal nobody can act on gets worked around rather than understood."""
    hostile = write_session(
        tmp_path / "hostile.protean",
        tree({"transformer": "ms-plugin.download", "params": {"url": "http://evil/x"}}),
    )
    with pytest.raises(ViewerError) as caught:
        await load_session(str(hostile))
    assert "http://evil/x" in str(caught.value)


def test_a_small_file_that_decompresses_enormously_is_refused(tmp_path):
    """9 kB of gzip reaches 2 GB; gzip.decompress() would allocate all of it."""
    bomb = tmp_path / "bomb.protean"
    bomb.write_bytes(gzip.compress(b"A" * (600 * 1024 * 1024)))
    assert bomb.stat().st_size < 1024 * 1024  # small on disk, by construction
    with pytest.raises(ViewerError, match="decompresses to more than"):
        _decompress_session(bomb)


async def test_the_bomb_is_refused_through_load_session_too(tmp_path):
    """The bound has to survive load_session's own except clause.

    ViewerError is a RuntimeError, so it passes through the (OSError,
    BadGzipFile, JSONDecodeError) handler around the parse rather than being
    reworded into "not a readable protean session".
    """
    bomb = tmp_path / "bomb.protean"
    bomb.write_bytes(gzip.compress(b"A" * (600 * 1024 * 1024)))
    with pytest.raises(ViewerError, match="decompresses to more than"):
        await load_session(str(bomb))


async def test_a_deeply_nested_file_is_refused_rather_than_traced(tmp_path):
    """155 bytes of brackets used to surface as an unhandled RecursionError."""
    deep = tmp_path / "deep.protean"
    body = '{"format": "protean-session", "version": 1, "handles": {}, "molstar": '
    deep.write_bytes(gzip.compress((body + "[" * 30000 + "]" * 30000 + "}").encode()))
    assert deep.stat().st_size < 1024
    with pytest.raises(ViewerError, match="nested too deeply"):
        await load_session(str(deep))


def test_an_ordinary_session_still_reads(tmp_path):
    """The bound must not be reachable by a real scene."""
    ordinary = tmp_path / "fine.protean"
    ordinary.write_bytes(gzip.compress(b'{"format": "protean-session"}'))
    assert json.loads(_decompress_session(ordinary))["format"] == "protean-session"
