#!/usr/bin/env python3
"""Run the Mol* capture benchmark once, against one Mol* build, and print JSON.

Deliberately standalone: stdlib only, no protean import, no pytest, no venv.
Backlog 40 needs to measure Mol* 4.18.0, which protean's own viewer cannot even
compile against, so the harness has to be able to run without protean.

The page reports its own result by POSTing to the server that served it, so
there is no CDP endpoint and no WebSocket here. That removes the failure mode
that cost this project a day in PR 89 — a reply lost on a socket that closed
mid-capture — because there is no long-lived socket for a capture to outlive.

Usage:

    python3 run_bench.py --molstar-dir <dir with molstar.js> --out result.json

`--molstar-dir` is where a version's prebuilt bundle was unpacked; its
`molstar.js` and `molstar.css` are what the page loads. Everything else is
served from this file's own directory.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import shader_swap

HERE = Path(__file__).resolve().parent

# The same flags the browser job uses. Headless Chrome has no GPU, and Mol* will
# not build a representation without a working GL context, so software WebGL is
# not a convenience here — it is the only way the page runs at all. Kept as the
# default rather than only read from the environment so that a local run
# measures the same renderer CI does; a laptop's real GPU would make every
# number here incomparable with the job this exists to explain.
DEFAULT_CHROME_FLAGS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
]

CHROME_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def find_chrome() -> str:
    explicit = os.environ.get("PROTEAN_CHROME")
    if explicit:
        return explicit
    for candidate in CHROME_CANDIDATES:
        found = shutil.which(candidate)
        if not found and Path(candidate).exists():
            found = candidate
        if found:
            return found
    raise SystemExit(
        "no Chrome found; set PROTEAN_CHROME to a browser binary "
        "(this is the same variable the browser test job uses)"
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ResultServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, directory: Path, label: str) -> None:
        self.directory = directory
        self.label = label
        self.result: dict | None = None
        self.arrived = threading.Event()
        self.started = time.monotonic()
        super().__init__(("127.0.0.1", port), _Handler)


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, request, client_address, server, **kwargs):
        # `self.server` is not set until BaseRequestHandler.__init__ runs, and
        # that call is also what serves the request — so the directory has to be
        # read off the server argument here, before the chain starts.
        super().__init__(
            request, client_address, server, directory=str(server.directory), **kwargs
        )

    # Named for what http.server dispatches on, not for PEP 8.
    def do_POST(self) -> None:
        if self.path not in ("/__bench_result", "/__bench_progress"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        server: ResultServer = self.server  # type: ignore[assignment]
        if self.path == "/__bench_progress":
            # Printed as it arrives, with the elapsed time, so a phase that is
            # slow and one that is stuck can be told apart while the job is
            # still running rather than afterwards from a timeout.
            try:
                note = json.loads(raw.decode("utf-8")).get("note", "")
            except Exception as exc:
                note = f"unparseable progress: {exc}"
            elapsed = time.monotonic() - server.started
            print(f"  {elapsed:6.1f}s  {server.label}: {note}", flush=True)
            self.send_response(204)
            self.end_headers()
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            payload = {"ok": False, "error": f"unparseable result: {exc}"}
        # First result wins. A page that somehow reported twice would otherwise
        # let a later error overwrite a good measurement.
        if server.result is None:
            server.result = payload
            server.arrived.set()
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # The page fetches a handful of files; the access log is noise that
        # would bury the one line that matters in a CI log.
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--molstar-dir",
        required=True,
        type=Path,
        help="directory holding the molstar.js / molstar.css to test",
    )
    ap.add_argument("--out", type=Path, help="write the JSON result here")
    ap.add_argument("--label", default="", help="version label carried into the result")
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--height", type=int, default=600)
    ap.add_argument("--repeats", type=int, default=8)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--levels", default="4,1")
    ap.add_argument(
        "--full-path",
        type=int,
        default=2,
        help="repeats through helper.getImageDataUri, protean's real path",
    )
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument(
        "--postprocessing",
        default="default",
        choices=["default", "no-occlusion", "no-antialiasing", "none"],
        help="turn a postprocessing pass off, to test which one got expensive",
    )
    ap.add_argument(
        "--structure",
        default="./1ubq.pdb",
        help="./1ubq.pdb, whose waters give the scene a transparent half, or "
        "./1ubq-apo.pdb, the same protein with none",
    )
    ap.add_argument(
        "--occlusion-samples",
        type=int,
        default=0,
        help="override the SSAO main kernel size (0 leaves it alone)",
    )
    ap.add_argument(
        "--occlusion-blur",
        type=int,
        default=0,
        help="override the SSAO blur kernel size (0 leaves it alone)",
    )
    ap.add_argument(
        "--zoom",
        type=float,
        default=1.0,
        help="pull the camera to this fraction of the fitted distance, to vary "
        "how much of the frame is background",
    )
    ap.add_argument(
        "--shader-swap",
        action="append",
        default=[],
        metavar="SHADER=SOURCE",
        help="splice one shader from another release (SOURCE=5.5.0) or from a "
        "file (SOURCE=@path.frag) into the bundle under test, so one file's "
        "contribution to a step can be measured instead of inferred. Repeatable",
    )
    ap.add_argument(
        "--bundles-root",
        type=Path,
        default=Path("bundles"),
        help="where --shader-swap looks for other releases' unpacked bundles",
    )
    ap.add_argument("--window-size", default="1000,800")
    ap.add_argument("--keep-profile", action="store_true")
    args = ap.parse_args()

    molstar_dir: Path = args.molstar_dir.resolve()
    for needed in ("molstar.js", "molstar.css"):
        if not (molstar_dir / needed).is_file():
            raise SystemExit(f"{molstar_dir} has no {needed}")

    chrome = find_chrome()
    flags = os.environ.get("PROTEAN_CHROME_FLAGS")
    chrome_flags = (
        [f for f in flags.split(" ") if f] if flags else list(DEFAULT_CHROME_FLAGS)
    )

    # Serve one directory: the page's own files plus the version under test.
    # Copied rather than symlinked so a bundle cannot be swapped underneath a
    # run that is already in flight — nineteen of these run back to back.
    serve_dir = Path(tempfile.mkdtemp(prefix="molstar-bench-"))
    for name in (
        "bench.html",
        "bench.js",
        "raf-pump.js",
        "1ubq.pdb",
        "1ubq-apo.pdb",
    ):
        shutil.copy2(HERE / name, serve_dir / name)
    for name in ("molstar.js", "molstar.css"):
        shutil.copy2(molstar_dir / name, serve_dir / name)

    # The transplant happens on the *copy*, never on the unpacked bundle, so a
    # patched row cannot leave a patched bundle behind for the next row in the
    # sweep to measure by accident. `apply_swaps` raises rather than warns, and
    # the raise is not caught: a run that silently measured the unswapped bundle
    # would produce a row saying "no effect" for the wrong reason, which is the
    # exact failure this benchmark exists to avoid making.
    swaps = shader_swap.apply_swaps(
        serve_dir / "molstar.js", args.shader_swap, args.bundles_root.resolve()
    )
    for record in swaps:
        print(
            f"  swapped {record['shader']} from {record['source']}: "
            f"{record['fromDigest']} -> {record['toDigest']} "
            f"({record['fromLength']} -> {record['toLength']} bytes, "
            f"interface {'unchanged' if record['interfaceUnchanged'] else 'CHANGED'})",
            flush=True,
        )

    port = free_port()
    server = ResultServer(port, serve_dir, args.label or "unknown")
    threading.Thread(target=server.serve_forever, daemon=True).start()

    query = (
        f"?width={args.width}&height={args.height}&repeats={args.repeats}"
        f"&warmup={args.warmup}&levels={args.levels}&fullPath={args.full_path}"
        f"&label={args.label or 'unknown'}&postprocessing={args.postprocessing}"
        f"&zoom={args.zoom}"
        f"&occlusionSamples={args.occlusion_samples}"
        f"&occlusionBlur={args.occlusion_blur}"
        f"&structure={args.structure}"
    )
    url = f"http://127.0.0.1:{port}/bench.html{query}"

    profile = tempfile.mkdtemp(prefix="molstar-bench-profile-")
    log_path = Path(profile) / "chrome.log"
    log = log_path.open("wb")
    proc = subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={args.window_size}",
            *chrome_flags,
            url,
        ],
        stdout=log,
        stderr=log,
    )

    started = time.monotonic()
    try:
        while not server.arrived.wait(timeout=2.0):
            if time.monotonic() - started > args.timeout:
                break
            if proc.poll() is not None:
                # Chrome exited without the page reporting. Waiting out the rest
                # of the timeout would only delay the same conclusion.
                time.sleep(1.0)
                break
        elapsed = time.monotonic() - started
        result = server.result
        if result is None:
            log.flush()
            tail = log_path.read_text(errors="replace")[-4000:]
            result = {
                "label": args.label,
                "ok": False,
                "error": (
                    f"page never reported after {elapsed:.0f}s "
                    f"(chrome exit={proc.poll()})"
                ),
                "chromeLogTail": tail,
            }
        result["harness"] = {
            "shaderSwaps": swaps,
            "molstarDir": str(molstar_dir),
            "chrome": chrome,
            "chromeFlags": chrome_flags,
            "windowSize": args.window_size,
            "url": url,
            "wallClockSeconds": round(elapsed, 2),
        }
    finally:
        proc.terminate()
        # Chrome respawns helpers that outlive terminate(); nineteen versions in
        # one job would otherwise leave nineteen sets of them competing for the
        # runner's four cores, which is exactly the contention this benchmark is
        # built to avoid.
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={profile}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        log.close()
        server.shutdown()
        server.server_close()
        shutil.rmtree(serve_dir, ignore_errors=True)
        if not args.keep_profile:
            shutil.rmtree(profile, ignore_errors=True)

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        print(text)

    if not result.get("ok"):
        print(f"BENCH FAILED ({args.label}): {result.get('error')}", file=sys.stderr)
        if result.get("chromeLogTail"):
            print(result["chromeLogTail"], file=sys.stderr)
        return 1

    # One line per run, so a nineteen-version job is readable without opening
    # the artifact.
    for entry in result.get("levels", []):
        s = entry["stats"]
        print(
            f"{args.label:>10}  level {entry['sampleLevel']}  "
            f"median {s['median']:8.1f} ms  p25 {s['p25']:8.1f}  p75 {s['p75']:8.1f}  "
            f"n={s['n']}"
        )
    fp = result.get("fullPath", {}).get("stats")
    if fp:
        print(
            f"{args.label:>10}  getImageDataUri  "
            f"median {fp['median']:8.1f} ms  n={fp['n']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
