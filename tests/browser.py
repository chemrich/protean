"""Driving a real browser: one copy of the launch, the wait, and the teardown.

Extracted from the selection differential suite when the render suite needed
the same thing. The mechanics here are almost entirely accumulated scar
tissue — the exact-URL match, keeping Chrome's own log, the pkill in the
finally — and a second hand-copied version would drift away from those fixes
one edit at a time.

Everything is opt-in behind PROTEAN_DIFFERENTIAL=1, because it needs a browser
and the network:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_render_differential.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from protean_mcp.connection import ViewerBridge
from protean_mcp.fetch import fetch_structure_data

from .conftest import free_port


def find_chrome() -> str | None:
    """Locate a Chrome binary: explicit override, then the usual suspects.

    CI runners are Linux and have no /Applications, so the macOS path alone
    would silently skip the whole suite there.
    """
    override = os.environ.get("PROTEAN_CHROME")
    if override:
        return override if Path(override).exists() else None
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    return next((c for c in candidates if Path(c).exists()), None)


CHROME = find_chrome()

# Headless CI needs software WebGL; locally we want a real window.
CHROME_FLAGS = [f for f in os.environ.get("PROTEAN_CHROME_FLAGS", "").split(" ") if f]
STATIC = Path(__file__).resolve().parents[1] / "src" / "protean_mcp" / "static"

BROWSER_MARKS = [
    pytest.mark.skipif(
        os.environ.get("PROTEAN_DIFFERENTIAL") != "1",
        reason="needs a browser; set PROTEAN_DIFFERENTIAL=1 to run",
    ),
    pytest.mark.skipif(CHROME is None, reason="no Chrome binary found"),
    pytest.mark.skipif(
        not (STATIC / "index.html").exists(),
        reason="viewer not built (npm run build in viewer/)",
    ),
]


# Path tracing needs a real GPU. Under the SwiftShader flags CI uses, all four
# WebGL extensions the tracer requires are present — so it reports as supported
# — and a single capture then fails to finish inside 60s. It is gated
# separately for that reason, and CI does not run it. Locally:
#
#     PROTEAN_DIFFERENTIAL=1 PROTEAN_PATHTRACE=1 \
#     PROTEAN_CHROME_FLAGS="--headless=new --no-sandbox --window-size=800,600" \
#     uv run pytest tests/test_render_differential.py
#
# Note the absence of --use-angle=swiftshader: that is the whole point.
PATHTRACE_MARKS = [
    *BROWSER_MARKS,
    pytest.mark.skipif(
        os.environ.get("PROTEAN_PATHTRACE") != "1",
        reason="path tracing needs a real GPU; set PROTEAN_PATHTRACE=1 to run",
    ),
]


async def cdp_eval(port: int, url: str, expression: str) -> Any:
    """Evaluate JS in the viewer page and return its JSON-decoded result."""
    async with aiohttp.ClientSession() as session:
        for _ in range(60):
            try:
                async with session.get(f"http://127.0.0.1:{port}/json") as resp:
                    targets = await resp.json()
            except Exception:
                await asyncio.sleep(0.3)
                continue
            # Exact URL match: a substring match also picks up the second tab,
            # which has no plugin on it and fails in a way that reads like the
            # page never loaded.
            pages = [
                t
                for t in targets
                if t.get("type") == "page"
                and t.get("url", "").rstrip("/") == url.rstrip("/")
            ]
            if pages:
                break
            await asyncio.sleep(0.3)
        else:
            raise RuntimeError("viewer page never appeared on the CDP endpoint")

        async with session.ws_connect(
            pages[0]["webSocketDebuggerUrl"], max_msg_size=64 * 1024 * 1024
        ) as ws:
            await ws.send_json(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
            async for msg in ws:
                payload = json.loads(msg.data)
                if payload.get("id") == 1:
                    result = payload.get("result", {}).get("result", {})
                    if "value" not in result:
                        raise RuntimeError(f"CDP evaluate failed: {str(payload)[:400]}")
                    return json.loads(result["value"])
    raise RuntimeError("no CDP reply")


@dataclass
class Session:
    """A loaded viewer, reachable both ways: over the bridge and over CDP."""

    bridge: ViewerBridge
    url: str
    cdp_port: int

    async def request(
        self, action: str, args: dict[str, Any] | None = None, timeout: float = 60
    ) -> Any:
        """Call a viewer action through the production bridge."""
        return await self.bridge.request(action, args or {}, timeout=timeout)

    async def evaluate(self, expression: str) -> Any:
        """Reach into Mol* internals, for claims the bridge cannot make."""
        return await cdp_eval(self.cdp_port, self.url, expression)


@asynccontextmanager
async def viewer_session(
    pdb_id: str, assembly: str = "asymmetric"
) -> AsyncIterator[Session]:
    """Launch a throwaway browser with *pdb_id* loaded, and clean up after."""
    structure = await fetch_structure_data(pdb_id)
    bridge = ViewerBridge(port=free_port(), static_dir=STATIC)
    viewer_port = await bridge.start()
    url = f"http://127.0.0.1:{viewer_port}/"
    cdp_port = free_port()
    profile = tempfile.mkdtemp(prefix="protean-diff-")

    chrome = CHROME
    assert chrome is not None  # guaranteed by BROWSER_MARKS
    # Chrome's own output is the only clue when the page never connects, so
    # keep it rather than sending it to /dev/null.
    log_path = Path(profile) / "chrome.log"
    log = log_path.open("wb")
    proc = subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={cdp_port}",
            *CHROME_FLAGS,
            url,
        ],
        stdout=log,
        stderr=log,
    )
    try:
        await bridge.wait_for_viewer(40)
        await bridge.request(
            "load_structure",
            {
                "name": pdb_id,
                "format": structure.format,
                "data": structure.data,
                "assembly": assembly,
            },
            timeout=120,
        )
        yield Session(bridge=bridge, url=url, cdp_port=cdp_port)
    finally:
        proc.terminate()
        # Chrome respawns helpers that outlive terminate(); without this they
        # accumulate across a run until the machine notices.
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={profile}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.close()
        await bridge.stop()
        shutil.rmtree(profile, ignore_errors=True)
