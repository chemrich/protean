"""Shared fixtures: a running bridge plus a mock viewer peer."""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

import aiohttp
import pytest

import protean_mcp.server as server_mod
from protean_mcp.connection import ViewerBridge


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
async def bridge():
    b = ViewerBridge(port=free_port())
    await b.start()
    yield b
    await b.stop()


class MockViewer:
    """Fake viewer peer speaking the protean wire protocol."""

    def __init__(
        self, session: aiohttp.ClientSession, ws: aiohttp.ClientWebSocketResponse
    ):
        self.session = session
        self.ws = ws
        self.handlers: dict[str, Any] = {}

    async def handshake(self, visibility: str | None = None) -> dict[str, Any]:
        ping = {"action": "protean_ping", "version": 1}
        if visibility is not None:
            ping["visibility"] = visibility
        await self.ws.send_json(ping)
        pong: dict[str, Any] = json.loads((await self.ws.receive()).data)
        assert pong["action"] == "protean_pong"
        return pong

    async def report_visibility(self, visibility: str) -> None:
        await self.ws.send_json(
            {"action": "protean_visibility", "visibility": visibility}
        )

    async def serve_one(self) -> None:
        """Answer a single request using registered handlers."""
        msg = json.loads((await self.ws.receive()).data)
        action, args, rid = msg["action"], msg.get("args", {}), msg["id"]
        handler = self.handlers.get(action)
        if handler is None:
            await self.ws.send_json(
                {"id": rid, "ok": False, "error": f"no handler: {action}"}
            )
        else:
            try:
                result = handler(args)
                await self.ws.send_json({"id": rid, "ok": True, "result": result})
            except Exception as exc:
                await self.ws.send_json({"id": rid, "ok": False, "error": str(exc)})

    def serve(self, n: int = 1) -> asyncio.Task[None]:
        async def loop() -> None:
            for _ in range(n):
                await self.serve_one()

        return asyncio.create_task(loop())

    async def close(self) -> None:
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


# -- session state ------------------------------------------------------------


# `server.py` keeps the session in module globals: what is loaded, what
# trajectory is on it, which handles exist. A test that calls the real tools
# leaves those set, and the next test inherits them.
#
# That is not hypothetical. `test_render_differential.py` loads a trajectory
# and sorts before `test_server.py`, so under PROTEAN_DIFFERENTIAL=1 two tests
# asserting "no trajectory loaded" stopped refusing and failed — on `main`, for
# as long as anyone had been running the whole suite locally. CI never saw it:
# the fast job skips the polluting file for want of the gate, and the browser
# job does not run `test_server.py` beside it. See backlog item 12.
#
# Restored rather than reset-to-empty, so this cannot quietly become a
# different kind of pollution: a test that arranges state in a module-scoped
# fixture still finds it there.
#
# `_bridge` is deliberately absent. It is a connection, not session state, and
# `test_server.py` installs it through module-scoped fixtures that a
# function-scoped reset would tear down underneath them.
_SESSION_GLOBALS = (
    "_structure",
    "_structure_error",
    "_structure_identifier",
    "_trajectory",
    "_path_tracing",
)

# Containers rebound by nothing and mutated in place, so restoring the object
# restores nothing — their *contents* have to be snapshotted.
_SESSION_CONTAINERS = ("_keyframes", "_conservation_scores")


@pytest.fixture(autouse=True)
def _isolate_session_state():
    """Give every test the session state it started with."""
    scalars = {name: getattr(server_mod, name) for name in _SESSION_GLOBALS}
    containers = {name: dict(getattr(server_mod, name)) for name in _SESSION_CONTAINERS}
    handles = dict(server_mod._handles.handles)

    yield

    for name, value in scalars.items():
        setattr(server_mod, name, value)
    for name, value in containers.items():
        container = getattr(server_mod, name)
        container.clear()
        container.update(value)
    server_mod._handles.handles.clear()
    server_mod._handles.handles.update(handles)
