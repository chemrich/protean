"""Bridge tests: handshake, RPC round-trips, port scan, error paths."""

from __future__ import annotations

import asyncio
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


async def test_placeholder_page_when_not_built(bridge):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{bridge.port}/") as resp:
            assert resp.status == 200
            assert "not built" in await resp.text()
