# Security

protean runs a local HTTP/WebSocket server, drives a browser tab, reads and
writes files a model names, and shells out to ffmpeg and APBS. That is more
surface than an ordinary library, so this file says what the trust model
actually is rather than asserting that there is one.

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability), or email **emrich@gmail.com** — the
same address `pyproject.toml` already carries.

Please do not open a public issue for anything exploitable. There is no bounty
and no formal SLA; this is one person's project, and a report will be read.

Nothing is released yet — there are no versions, supported or otherwise. Fixes
land on `main`.

## What protean assumes

**The model is trusted with what the user is trusted with.** protean is an MCP
server: a model calls its tools, and those tools run as the user. There is no
sandbox between them, and adding one is not a goal. If a model can call
`screenshot`, it can write a PNG wherever the user could write one.

**The browser tab is trusted only after it proves it is ours.** The bridge
binds `127.0.0.1`, and that is not authentication — see below.

**Files named in a tool call are not trusted.** A structure, a volume, a
trajectory or a session may come from anywhere, so each is validated before it
reaches the viewer or the analysis.

## What the bridge does about the browser

**Loopback keeps out the network; it does not keep out the user's own
browser.** A WebSocket is not subject to the same-origin policy — there is no
CORS preflight for `new WebSocket(...)` — so any page the user happens to be
visiting can open a socket to `127.0.0.1` on a guessable port. protean's
handshake was also *designed* to displace: a ping from a new connection takes
the socket from the incumbent, so two tabs cannot fight over it.

Before this was fixed, a page on any origin could take the viewer over and
answer for it, which defeats the one guarantee the project makes — that the
picture and the numbers describe the same molecule.

What holds now:

- A per-process token (`secrets.token_urlsafe(32)`), required on the handshake
  and compared with `secrets.compare_digest` — **as bytes**, because
  `compare_digest` raises on non-ASCII `str` and the query string arrives
  percent-decoded.
- An `Origin` check that refuses a present-but-foreign origin.
- Both run **before** `ws.prepare()`, so a refused caller never reaches the
  message loop and cannot land a displacing ping on the way past.
- One accessor builds the viewer URL, so no caller can open a page its own
  socket would refuse.

## What protean does about files

- **A session file is untrusted input**, because the format exists to be
  shared. `load_session` refuses one that names a URL other than this bridge's
  own volume route, refuses any transformer `save_session` never writes,
  bounds decompression, and refuses a malformed document rather than raising
  through it. Without those, a `.protean` file could make the browser fetch
  from a host of its author's choosing and draw the result, while the call
  returned normally.
- **The static route is containment-checked** with `.resolve()` before
  `is_relative_to`, which rejects a symlink planted inside the served
  directory as well as `..` and absolute paths.
- **A write will not change what a file is.** An existing file is replaced only
  when it already holds what that tool writes; `overwrite=True` asks for
  anything else.
- **Subprocesses take argv lists**, never `shell=True`, and build their
  filenames themselves inside a temp directory. No caller-supplied path reaches
  a position where ffmpeg would read it as an option or a protocol handler.

## Known limits, stated rather than implied

These are deliberate, and they are the first things worth attacking:

- **The handshake token is a credential in a URL.** It is kept out of tool
  replies by default, but it is in the page's address bar and will be in a
  screenshot of it. `open_viewer(reveal_url=True)` hands it over on purpose.
- **An absent `Origin` is allowed**, because non-browser clients send none. So
  the Origin check is no backstop for a leaked token — anything that can reach
  loopback and knows it can drive the viewer.
- **The token is per-process.** Restarting the server invalidates a tab that is
  still open; the page says so rather than retrying forever. Persisting it
  would put the secret on disk, outliving the process, and is not done.
- **`overwrite=True` is not a barrier against a hostile tool call.** Anything
  that can set the path can set the flag. What it buys is that a destructive
  write is something a caller asks for by name, in a call a reader can see.
- **Analysis is only as trustworthy as its inputs, and does not always say
  so.** `pdb2pqr` rebuilds missing sidechains before an APBS solve, and a
  rebuilt sidechain changes the energies — protean silences pdb2pqr's per-atom
  warnings and does not report the repairs in the reply. The reply names the
  method that ran, not what the preparation changed.

## What has been attacked, and what has not

A security pass ran on 2026-08-15 before this repository was made public. What
it covered, and what it found, is recorded in
[docs/going-public.md](docs/going-public.md) §3.1 — including the findings it
fixed and the two it left as decisions. Two claims from that pass are
worth repeating here:

- The static route was attacked with 15 traversal attempts, including symlinks
  planted inside the served directory. All returned 404.
- History was scanned for secrets by pattern **and** by entropy, across every
  non-merge commit on all refs plus the merge-only content a default scan
  skips, with the scanner canary-tested first so that "nothing found" is
  evidence rather than silence.

**Not covered:** the model's own behaviour. protean does not attempt to detect
a prompt-injected tool call, and cannot. It tries to make the consequences of
one visible and bounded, which is a different and smaller claim.
