"""WebSocket bridge between the MCP server and the Mol* viewer.

The Python side hosts an HTTP+WebSocket server (browsers can't listen on
sockets, so the direction is inverted relative to MCPymol's plugin pattern).
The viewer page connects to ``/ws`` and identifies itself with a
``protean_ping`` handshake; the server then issues RPC requests as
``{id, action, args}`` and the viewer answers ``{id, ok, result|error}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from . import DEFAULT_PORT, PORT_ENV_VAR, PORT_SCAN_RANGE, PROTOCOL_VERSION

logger = logging.getLogger(__name__)

PLACEHOLDER_HTML = """<!doctype html>
<html><head><title>protean</title></head>
<body style="background:#111;color:#ddd;font-family:sans-serif;padding:2em">
<h2>protean viewer is not built</h2>
<p>Run <code>npm install &amp;&amp; npm run build</code> in the <code>viewer/</code>
directory, then reload this page.</p>
</body></html>"""


class ViewerError(RuntimeError):
    """Raised when the viewer is unavailable or reports an error."""


class ViewerBridge:
    """Hosts the viewer page and manages the RPC channel to it."""

    def __init__(self, port: int | None = None, static_dir: Path | None = None):
        self._requested_port = port or int(os.environ.get(PORT_ENV_VAR, DEFAULT_PORT))
        self.port: int | None = None
        self.static_dir = static_dir
        self._ws: web.WebSocketResponse | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._connected = asyncio.Event()
        self._runner: web.AppRunner | None = None
        self._visibility: str | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> int:
        """Start the HTTP/WS server, scanning ports upward on conflict."""
        if self._runner is not None:
            assert self.port is not None
            return self.port

        app = web.Application()
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_get("/", self._index_handler)
        app.router.add_get("/{filename:.+}", self._file_handler)

        last_error: OSError | None = None
        for offset in range(PORT_SCAN_RANGE):
            candidate = self._requested_port + offset
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", candidate)
            try:
                await site.start()
            except OSError as exc:
                last_error = exc
                await runner.cleanup()
                logger.info("Port %d busy, trying %d", candidate, candidate + 1)
                continue
            self._runner = runner
            self.port = candidate
            logger.info("protean bridge listening on 127.0.0.1:%d", candidate)
            return candidate
        raise ViewerError(
            f"No free port in {self._requested_port}-"
            f"{self._requested_port + PORT_SCAN_RANGE - 1}: {last_error}"
        )

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        self._connected.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self.port = None

    @property
    def running(self) -> bool:
        return self._runner is not None

    @property
    def viewer_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    @property
    def viewer_visibility(self) -> str | None:
        """Last reported ``document.visibilityState``, or None if unknown.

        A hidden tab has its animation frames paused, which stalls any Mol*
        work that needs the render loop — worth surfacing when things go wrong.
        """
        return self._visibility if self.viewer_connected else None

    async def wait_for_viewer(self, timeout: float = 15) -> None:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError:
            raise ViewerError(
                f"No viewer connected within {timeout}s. Is the browser tab open?"
            ) from None

    # -- RPC -----------------------------------------------------------------

    async def request(
        self, action: str, args: dict[str, Any] | None = None, timeout: float = 60
    ) -> Any:
        """Send an action to the viewer and await its reply."""
        if not self.viewer_connected:
            raise ViewerError("No viewer connected — call open_viewer first.")
        assert self._ws is not None
        rid = uuid.uuid4().hex
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._ws.send_json({"id": rid, "action": action, "args": args or {}})
            reply = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            raise ViewerError(
                f"Viewer timed out on '{action}' after {timeout}s.{self._stall_hint()}"
            ) from None
        finally:
            self._pending.pop(rid, None)
        if not reply.get("ok"):
            raise ViewerError(reply.get("error", f"Viewer error on '{action}'"))
        return reply.get("result")

    def _stall_hint(self) -> str:
        """Explain a timeout when the tab being hidden is the likely cause."""
        if self._visibility is None:
            return (
                " The viewer did not report its visibility; if its tab is in the "
                "background, bring it to the front and retry."
            )
        if self._visibility != "visible":
            return (
                f" The viewer tab is {self._visibility}: browsers pause "
                "requestAnimationFrame in background tabs, which Mol* needs to "
                "build representations. Bring the protean tab to the front and retry."
            )
        return ""

    # -- handlers ------------------------------------------------------------

    async def _index_handler(self, request: web.Request) -> web.StreamResponse:
        if self.static_dir and (self.static_dir / "index.html").exists():
            return web.FileResponse(self.static_dir / "index.html")
        return web.Response(text=PLACEHOLDER_HTML, content_type="text/html")

    async def _file_handler(self, request: web.Request) -> web.StreamResponse:
        if self.static_dir is None:
            raise web.HTTPNotFound()
        root = self.static_dir.resolve()
        target = (root / request.match_info["filename"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(target)

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)
        registered = False
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                logger.warning("Non-JSON message from viewer, ignoring")
                continue

            if data.get("action") == "protean_ping":
                # Handshake: this connection is a protean viewer.
                if self._ws is not None and self._ws is not ws and not self._ws.closed:
                    # Tell the displaced viewer it lost the connection on
                    # purpose. Without this it reconnects on its timer, wins the
                    # handshake back, and the two tabs trade the socket forever.
                    with contextlib.suppress(ConnectionResetError):
                        await self._ws.send_json({"action": "protean_superseded"})
                    await self._ws.close()
                self._ws = ws
                registered = True
                self._visibility = data.get("visibility")
                self._connected.set()
                await ws.send_json(
                    {"action": "protean_pong", "version": PROTOCOL_VERSION}
                )
                continue

            if data.get("action") == "protean_visibility":
                if ws is self._ws:
                    self._visibility = data.get("visibility")
                    logger.debug("Viewer visibility: %s", self._visibility)
                continue

            rid = data.get("id")
            fut = self._pending.get(rid) if rid else None
            if fut is not None and not fut.done():
                fut.set_result(data)
            else:
                logger.debug("Unmatched viewer message: %s", data)

        if registered and self._ws is ws:
            self._ws = None
            self._visibility = None
            self._connected.clear()
            logger.info("Viewer disconnected")
        return ws
