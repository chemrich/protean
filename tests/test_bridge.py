"""Bridge tests: handshake, RPC round-trips, port scan, error paths."""

from __future__ import annotations

import asyncio
import json
import socket

import aiohttp
import pytest

from protean_mcp import DEFAULT_PORT
from protean_mcp.connection import ViewerBridge, ViewerError

from .conftest import free_port


async def test_handshake_marks_viewer_connected(bridge, viewer):
    assert bridge.viewer_connected


async def test_request_roundtrip(bridge, viewer):
    viewer.handlers["echo"] = lambda args: {"echo": args}
    task = viewer.serve(1)
    result = await bridge.request("echo", {"x": 1})
    await task
    assert result == {"echo": {"x": 1}}


async def test_viewer_error_propagates(bridge, viewer):
    def boom(args):
        raise RuntimeError("kaboom")

    viewer.handlers["explode"] = boom
    task = viewer.serve(1)
    with pytest.raises(ViewerError, match="kaboom"):
        await bridge.request("explode")
    await task


async def test_unknown_action_errors(bridge, viewer):
    task = viewer.serve(1)
    with pytest.raises(ViewerError, match="no handler"):
        await bridge.request("nonexistent")
    await task


async def test_request_without_viewer_raises(bridge):
    with pytest.raises(ViewerError, match="No viewer connected"):
        await bridge.request("anything")


async def test_request_timeout(bridge, viewer):
    # Viewer never answers.
    with pytest.raises(ViewerError, match="timed out"):
        await bridge.request("slow", timeout=0.2)


async def test_port_scan_increments_on_conflict():
    base = free_port()
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", base))
    blocker.listen(1)
    try:
        bridge = ViewerBridge(port=base)
        port = await bridge.start()
        assert port == base + 1
        await bridge.stop()
    finally:
        blocker.close()


async def test_default_port_constant():
    bridge = ViewerBridge()
    assert bridge._requested_port == DEFAULT_PORT


async def test_disconnect_clears_state(bridge, viewer):
    await viewer.close()
    await asyncio.sleep(0.1)
    assert not bridge.viewer_connected


async def test_new_viewer_replaces_old(bridge, viewer):
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(f"ws://127.0.0.1:{bridge.port}/ws")
    await ws.send_json({"action": "protean_ping", "version": 1})
    await ws.receive()  # pong
    await asyncio.sleep(0.1)
    assert bridge.viewer_connected
    await ws.close()
    await session.close()


async def test_displaced_viewer_is_told_it_was_superseded(bridge, viewer):
    """Otherwise the old tab reconnects on its timer, takes the socket back,
    and the two tabs trade it forever."""
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(f"ws://127.0.0.1:{bridge.port}/ws")
    await ws.send_json({"action": "protean_ping", "version": 1})
    await ws.receive()  # pong for the newcomer

    # The original viewer should be told why it is losing the connection.
    message = json.loads((await viewer.ws.receive()).data)
    assert message["action"] == "protean_superseded"

    await ws.close()
    await session.close()


async def test_handshake_records_visibility(bridge):
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(f"ws://127.0.0.1:{bridge.port}/ws")
    from .conftest import MockViewer

    v = MockViewer(session, ws)
    await v.handshake(visibility="hidden")
    await bridge.wait_for_viewer(timeout=5)
    assert bridge.viewer_visibility == "hidden"
    await v.close()


async def test_visibility_updates_are_tracked(bridge, viewer):
    await viewer.report_visibility("hidden")
    await asyncio.sleep(0.1)
    assert bridge.viewer_visibility == "hidden"
    await viewer.report_visibility("visible")
    await asyncio.sleep(0.1)
    assert bridge.viewer_visibility == "visible"


async def test_visibility_is_none_when_disconnected(bridge, viewer):
    await viewer.report_visibility("visible")
    await asyncio.sleep(0.1)
    await viewer.close()
    await asyncio.sleep(0.1)
    assert bridge.viewer_visibility is None


async def test_timeout_explains_hidden_tab(bridge, viewer):
    """A stalled hidden tab is the common case — the error must say so."""
    await viewer.report_visibility("hidden")
    await asyncio.sleep(0.1)
    with pytest.raises(ViewerError, match="requestAnimationFrame"):
        await bridge.request("slow", timeout=0.2)


async def test_timeout_notes_unknown_visibility(bridge, viewer):
    with pytest.raises(ViewerError, match="did not report its visibility"):
        await bridge.request("slow", timeout=0.2)


async def test_timeout_has_no_hint_when_visible(bridge, viewer):
    await viewer.report_visibility("visible")
    await asyncio.sleep(0.1)
    with pytest.raises(ViewerError) as excinfo:
        await bridge.request("slow", timeout=0.2)
    assert "requestAnimationFrame" not in str(excinfo.value)


async def test_placeholder_page_when_not_built(bridge):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{bridge.port}/") as resp:
            assert resp.status == 200
            assert "not built" in await resp.text()
