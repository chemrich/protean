"""Server tool tests against the mock viewer."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import pytest

import protean_mcp.server as server_mod
from protean_mcp.connection import ViewerError
from protean_mcp.server import (
    clear_viewer,
    fetch_structure,
    load_session,
    mcp,
    save_session,
    screenshot,
)

# 1x1 transparent PNG
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


@pytest.fixture
def wired_bridge(bridge, viewer, monkeypatch):
    """Point the server module's global bridge at the test bridge."""
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    return viewer


async def test_tools_registered():
    tools = {t.name for t in await mcp.list_tools()}
    assert {"open_viewer", "fetch_structure", "screenshot", "clear_viewer"} <= tools


async def test_fetch_structure_tool(wired_bridge, tmp_path, monkeypatch):
    f = tmp_path / "toy.pdb"
    f.write_text("ATOM      1  N   MET A   1\n")
    loaded = {}

    def on_load(args):
        loaded.update(args)
        return {"loaded": args["name"]}

    wired_bridge.handlers["load_structure"] = on_load
    task = wired_bridge.serve(1)
    msg = await fetch_structure(str(f))
    await task
    assert "toy" in msg and "local file" in msg
    assert loaded["format"] == "pdb"


async def test_fetch_structure_without_viewer(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    with pytest.raises(ViewerError, match="No viewer connected"):
        await fetch_structure("1ubq")


async def test_clear_tool(wired_bridge):
    wired_bridge.handlers["clear"] = lambda args: {}
    task = wired_bridge.serve(1)
    assert "cleared" in (await clear_viewer()).lower()
    await task


async def test_screenshot_tool(wired_bridge, tmp_path):
    wired_bridge.handlers["screenshot"] = lambda args: {
        "data_uri": f"data:image/png;base64,{PNG_B64}"
    }
    task = wired_bridge.serve(1)
    out = tmp_path / "shot.png"
    result = await screenshot(path=str(out))
    await task
    assert out.read_bytes() == base64.b64decode(PNG_B64)
    assert any("Saved to" in str(item) for item in result)


# -- sessions ----------------------------------------------------------------


async def test_save_session_writes_a_gzipped_document(wired_bridge, tmp_path):
    wired_bridge.handlers["save_session"] = lambda args: {
        "snapshot": {"id": "snap", "data": "x" * 5000},
        "handles": {"prot": ["ref-1"], "hemes": ["ref-2"]},
    }
    task = wired_bridge.serve(1)
    out = await save_session(str(tmp_path / "demo"))
    await task

    written = Path(out["path"])
    assert written.name == "demo.protean"  # suffix supplied when omitted
    assert out["handles"] == ["hemes", "prot"]
    # Sessions are mostly embedded mmCIF; compression is the point.
    assert out["bytes"] < out["uncompressed_bytes"]

    document = json.loads(gzip.decompress(written.read_bytes()))
    assert document["format"] == "protean-session"
    assert document["version"] == 1
    assert document["molstar"]["id"] == "snap"
    assert document["handles"]["prot"] == ["ref-1"]


async def test_session_round_trip(wired_bridge, tmp_path):
    wired_bridge.handlers["save_session"] = lambda args: {
        "snapshot": {"id": "snap"},
        "handles": {"prot": ["ref-1"]},
    }
    sent = {}

    def on_load(args):
        sent.update(args)
        return {"restored": ["prot"], "dropped": [], "atom_count": 4779}

    wired_bridge.handlers["load_session"] = on_load

    task = wired_bridge.serve(2)
    saved = await save_session(str(tmp_path / "s.protean"))
    result = await load_session(saved["path"])
    await task

    assert sent["snapshot"] == {"id": "snap"}
    assert sent["handles"] == {"prot": ["ref-1"]}
    assert result["restored"] == ["prot"]
    assert result["atom_count"] == 4779


async def test_load_session_missing_file(wired_bridge, tmp_path):
    with pytest.raises(ViewerError, match="No session file"):
        await load_session(str(tmp_path / "nope.protean"))


async def test_load_session_rejects_non_gzip(wired_bridge, tmp_path):
    bad = tmp_path / "bad.protean"
    bad.write_text("this is not a session")
    with pytest.raises(ViewerError, match="not a readable protean session"):
        await load_session(str(bad))


async def test_load_session_rejects_foreign_format(wired_bridge, tmp_path):
    other = tmp_path / "other.protean"
    other.write_bytes(gzip.compress(json.dumps({"format": "pymol-session"}).encode()))
    with pytest.raises(ViewerError, match="not a protean session"):
        await load_session(str(other))


async def test_load_session_rejects_future_version(wired_bridge, tmp_path):
    future = tmp_path / "future.protean"
    future.write_bytes(
        gzip.compress(
            json.dumps(
                {"format": "protean-session", "version": 99, "molstar": {}}
            ).encode()
        )
    )
    with pytest.raises(ViewerError, match="session version 99"):
        await load_session(str(future))
