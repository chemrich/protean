"""Shared fixtures: a running bridge plus a mock viewer peer."""

from __future__ import annotations

import asyncio
import json
import socket

import aiohttp
import pytest

from protean_mcp.connection import ViewerBridge


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def bridge():
    b = ViewerBridge(port=free_port())
    await b.start()
    yield b
    await b.stop()


class MockViewer:
    """Fake viewer peer speaking the protean wire protocol."""

    def __init__(self, session: aiohttp.ClientSession, ws: aiohttp.ClientWebSocketResponse):
        self.session = session
        self.ws = ws
        self.handlers: dict = {}

    async def handshake(self):
        await self.ws.send_json({"action": "protean_ping", "version": 1})
        pong = json.loads((await self.ws.receive()).data)
        assert pong["action"] == "protean_pong"
        return pong

    async def serve_one(self):
        """Answer a single request using registered handlers."""
        msg = json.loads((await self.ws.receive()).data)
        action, args, rid = msg["action"], msg.get("args", {}), msg["id"]
        handler = self.handlers.get(action)
        if handler is None:
            await self.ws.send_json({"id": rid, "ok": False, "error": f"no handler: {action}"})
        else:
            try:
                result = handler(args)
                await self.ws.send_json({"id": rid, "ok": True, "result": result})
            except Exception as exc:
                await self.ws.send_json({"id": rid, "ok": False, "error": str(exc)})

    def serve(self, n: int = 1) -> asyncio.Task:
        async def loop():
            for _ in range(n):
                await self.serve_one()

        return asyncio.create_task(loop())

    async def close(self):
        await self.ws.close()
        await self.session.close()


@pytest.fixture
async def viewer(bridge):
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(f"ws://127.0.0.1:{bridge.port}/ws")
    v = MockViewer(session, ws)
    await v.handshake()
    await bridge.wait_for_viewer(timeout=5)
    yield v
    await v.close()
