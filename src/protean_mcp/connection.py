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
import secrets
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
        #: Minted per bridge, and required on the WebSocket handshake.
        #:
        #: Without it any page the user happens to be visiting can open
        #: `ws://127.0.0.1:<port>/ws` — a WebSocket is not subject to the
        #: same-origin policy, the port is `DEFAULT_PORT` plus a small scan
        #: range and so guessable, and the handshake is *designed* to displace:
        #: a `protean_ping` closes the incumbent and takes the socket. Every
        #: action would then go to that page and every reply the model reads
        #: would come from it, which defeats the one guarantee this project
        #: exists to make — that the viewer and the analysis describe the same
        #: molecule. Demonstrated before this was added, with a socket carrying
        #: `Origin: https://evil.example`: it was accepted and the real viewer
        #: received `protean_superseded` and a close.
        self.token = secrets.token_urlsafe(32)
        self._ws: web.WebSocketResponse | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._connected = asyncio.Event()
        self._runner: web.AppRunner | None = None
        self._visibility: str | None = None
        #: Volumes the viewer may fetch, by handle. Values are either a path
        #: on disk (streamed) or bytes held in memory.
        #:
        #: Structures travel inline in the RPC message; volumes do not. A 110³
        #: float32 reconstruction is ~5 MB and a 400³ one ~256 MB, and
        #: base64 through a JSON WebSocket frame is the wrong pipe for that.
        #: This server already serves the viewer's own files, so a volume gets
        #: a URL and the viewer downloads it. The two paths differ because of
        #: size, not because of format.
        self._volumes: dict[str, Path | bytes] = {}
        #: What a `protean_invoke` from the page runs, registered by the server.
        #:
        #: The page cannot reach a tool; it names a view and this decides what
        #: that means. Held as a callback rather than imported because the
        #: server imports *this* module, and because it keeps the rule visible:
        #: everything a click can do is whatever was handed in here.
        self._invoke: Callable[[str], Awaitable[str]] | None = None
        #: Invocations still running, kept so they are not garbage collected
        #: mid-flight. asyncio holds only weak references to tasks.
        self._invocations: set[asyncio.Task[None]] = set()

    def on_invoke(self, handler: Callable[[str], Awaitable[str]]) -> None:
        """Say what a view request from the page should run."""
        self._invoke = handler

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> int:
        """Start the HTTP/WS server, scanning ports upward on conflict."""
        if self._runner is not None:
            assert self.port is not None
            return self.port

        app = web.Application()
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_get("/", self._index_handler)
        # Registered before the catch-all for readability, not for
        # correctness: aiohttp 3.14 indexes routes by their longest static
        # prefix, so `/volumes/{handle}` wins over `/{filename:.+}` whichever
        # order they go in — checked, because the obvious assumption
        # (registration order) is wrong and would have been a comment nobody
        # could falsify by reading.
        app.router.add_get("/volumes/{handle}", self._volume_handler)
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
        # Before the close, not after: closing the socket wakes the handler,
        # which would fail these as an ordinary disconnect and bury the more
        # specific reason.
        self._fail_pending("The viewer bridge was shut down while this was in flight.")
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
    def viewer_url(self) -> str:
        """The page URL, carrying the token the socket will demand.

        One place builds this so no caller can open a viewer that cannot then
        connect. The page reads the token out of its own query string and
        appends it to the WebSocket URL.

        **This value is a credential.** Anything holding it can drive the
        viewer: the Origin check is no second line of defence here, because
        `_allowed_origin` allows an *absent* Origin so that non-browser clients
        can connect at all. Hand it to a browser, not to a reply — see
        `display_url` for the one that is safe to show.
        """
        if self.port is None:
            raise ViewerError("bridge is not running, so it has no URL yet")
        return f"http://127.0.0.1:{self.port}/?token={quote(self.token, safe='')}"

    @property
    def display_url(self) -> str:
        """The same address with the token left off, for showing to a caller.

        A reply from an MCP tool is read by a model, kept in a transcript and
        often written to a log, so a token in one is a credential in all three.
        Opening this URL gives a viewer that cannot connect, which is the
        correct failure: the page says so rather than half-working.
        """
        if self.port is None:
            raise ViewerError("bridge is not running, so it has no URL yet")
        return f"http://127.0.0.1:{self.port}/"

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

    async def _run_invoke(self, ws: web.WebSocketResponse, data: dict[str, Any]) -> None:
        """Run a view the page asked for, and tell it what happened.

        The page gets the refusal text verbatim, so a click on a structure that
        cannot take the view fails the way the tool does rather than in a
        dialect of its own. It is a reply and not a log line because a control
        that cannot report failure is a control that reports success.
        """
        rid = data.get("id")
        view = data.get("view")
        try:
            if self._invoke is None:
                raise ViewerError("This viewer accepts no view requests from the page")
            if not isinstance(view, str):
                raise ViewerError("A view request has to name a view")
            applied = await self._invoke(view)
            reply = {"action": "protean_invoked", "id": rid, "ok": True, "view": applied}
        except Exception as exc:  # the page is told either way
            logger.info("Refused a view request from the page: %s", exc)
            reply = {
                "action": "protean_invoked",
                "id": rid,
                "ok": False,
                "error": str(exc),
            }
        with contextlib.suppress(ConnectionResetError, RuntimeError):
            if not ws.closed:
                await ws.send_json(reply)

    def _fail_pending(self, reason: str, keep: set[str] | None = None) -> None:
        """Fail in-flight requests whose reply can no longer arrive.

        A pending request is keyed by an id only the page that received it
        knows, so when that page is gone the reply is not late — it is never
        coming. Nothing here noticed, and the request sat until its own timeout
        and then blamed a stall: the caller waited minutes for news available
        at once, and the news named the wrong cause.

        *keep* is what a newly connected page says it still owes an answer for.
        Those are left alone; everything else pending is failed, because the
        page that could have answered it is gone.

        **A closed socket is deliberately not one of these moments.** A long
        action blocks the page's main thread, the socket can die inside that
        window, and the page reconnects and delivers the held reply once the
        work finishes — so failing on disconnect would destroy a reply that was
        on its way. What ends that wait instead is a page reconnecting without
        claiming the work, or the request's own budget.
        """
        for rid, fut in list(self._pending.items()):
            if keep is not None and rid in keep:
                continue
            if not fut.done():
                fut.set_exception(ViewerError(reason))
            self._pending.pop(rid, None)

    async def _register_viewer(
        self, ws: web.WebSocketResponse, data: dict[str, Any]
    ) -> None:
        """Take the handshake: this connection is the viewer from now on."""
        displacing = self._ws is not None and self._ws is not ws and not self._ws.closed
        if displacing:
            assert self._ws is not None
            # Tell the displaced viewer it lost the connection on purpose.
            # Without this it reconnects on its timer, wins the handshake back,
            # and the two tabs trade the socket forever.
            with contextlib.suppress(ConnectionResetError):
                await self._ws.send_json({"action": "protean_superseded"})
            await self._ws.close()

        self._ws = ws
        self._visibility = data.get("visibility")
        self._connected.set()

        # Whatever this page says it still owes an answer for survives; anything
        # else pending belonged to a page that is gone, and no reply for it can
        # arrive. A page that reconnects mid-render claims its work and keeps
        # waiting; one that reloaded claims nothing, and the caller hears so at
        # once rather than at the end of a figure-sized budget.
        claimed = data.get("inflight")
        keep = {str(rid) for rid in claimed} if isinstance(claimed, list) else None
        if keep is not None or displacing:
            self._fail_pending(
                "The viewer answering this is gone — the tab reloaded, or "
                "another protean tab took the connection. The reply is lost; "
                "retry against the tab connected now.",
                keep=keep,
            )
        await ws.send_json({"action": "protean_pong", "version": PROTOCOL_VERSION})

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

    def publish_volume(self, handle: str, source: Path | bytes) -> str:
        """Make a volume fetchable by the viewer. Returns the URL to fetch.

        Bytes are held in memory; a path is streamed from disk instead. Nothing
        is decompressed here: callers hand over data the viewer can parse, which
        is why `load_volume` takes the bytes branch — it has already had to
        gunzip in order to identify the format.

        The handle is percent-encoded into the URL and kept raw as the dict key.
        A handle containing `/` would otherwise build a URL that `{handle}`
        cannot match, so the request would fall through to the static catch-all
        and 404 — which the viewer reports as a parse failure rather than as the
        bad name it is. aiohttp decodes `match_info` on the way back in, so the
        two halves still meet.
        """
        self._volumes[handle] = source
        return f"/volumes/{quote(handle, safe='')}"

    def forget_volume(self, handle: str) -> None:
        """Stop serving a volume. Unknown handles are not an error."""
        self._volumes.pop(handle, None)

    def forget_all_volumes(self) -> None:
        """Stop serving every volume, releasing the bytes held for them.

        Clearing the viewer leaves nothing that could fetch these, so keeping
        them is pure retention: three 400-cubed maps is ~750 MB held by a server
        displaying nothing.
        """
        self._volumes.clear()

    async def _volume_handler(self, request: web.Request) -> web.StreamResponse:
        source = self._volumes.get(request.match_info["handle"])
        if source is None:
            raise web.HTTPNotFound()
        if isinstance(source, bytes):
            return web.Response(body=source, content_type="application/octet-stream")
        if not source.is_file():
            # Registered and then moved or deleted. Say so rather than serving
            # nothing, which the viewer would report as an empty volume.
            raise web.HTTPNotFound()
        return web.FileResponse(
            source, headers={"Content-Type": "application/octet-stream"}
        )

    async def _file_handler(self, request: web.Request) -> web.StreamResponse:
        if self.static_dir is None:
            raise web.HTTPNotFound()
        root = self.static_dir.resolve()
        target = (root / request.match_info["filename"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(target)

    def _allowed_origin(self, origin: str | None) -> bool:
        """Is this Origin one our own page could have been served from?

        Absent is allowed: a non-browser client (the test harness, a script)
        sends no Origin, and the token is what authenticates it. A *present*
        Origin that is not ours is a browser on some other site, and no such
        page has any business here.
        """
        if origin is None:
            return True
        return origin in {
            f"http://127.0.0.1:{self.port}",
            f"http://localhost:{self.port}",
            f"http://[::1]:{self.port}",
        }

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        # Both checks happen *before* prepare(), so a rejected caller gets an
        # HTTP error and never reaches the message loop — it cannot send a
        # `protean_ping` and displace the real viewer on the way past.
        if not self._allowed_origin(request.headers.get("Origin")):
            logger.warning(
                "Refused a viewer socket from origin %r", request.headers.get("Origin")
            )
            raise web.HTTPForbidden(text="origin not permitted")
        # compare_digest, not ==: the comparison is against a secret and a
        # timing side channel is free to avoid here.
        #
        # Compared as bytes, because compare_digest raises TypeError on str
        # arguments carrying non-ASCII — and the query string is percent-decoded
        # before it reaches here, so `?token=%C3%A9` is one request away. That
        # made the refusal path an attacker controls the one returning 500 and a
        # traceback, where a merely wrong token returned a clean 403.
        offered = request.query.get("token", "").encode()
        if not secrets.compare_digest(offered, self.token.encode()):
            logger.warning("Refused a viewer socket with a bad or missing token")
            raise web.HTTPForbidden(text="bad or missing token")

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
                await self._register_viewer(ws, data)
                registered = True
                continue

            if data.get("action") == "protean_invoke":
                # Deliberately a task, and this is the whole subtlety of the
                # page-initiated path. The handler drives the viewer, so it
                # calls `request()` and waits for a reply — which arrives as a
                # message *this loop* has to read. Awaiting it here would mean
                # the loop is inside the handler while the handler waits on the
                # loop, and the click would hang until its own timeout and then
                # blame the viewer. The reply to the page goes back from the
                # task, once there is something to say.
                task = asyncio.create_task(self._run_invoke(ws, data))
                self._invocations.add(task)
                task.add_done_callback(self._invocations.discard)
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
            # Nothing pending is failed here on purpose: see _fail_pending. The
            # page may be mid-render with the socket dead under it, and it
            # delivers the held reply when it reconnects.
            logger.info("Viewer disconnected")
        return ws
