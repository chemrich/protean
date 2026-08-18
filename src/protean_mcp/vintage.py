"""Which build of protean is actually running, and whether it is stale.

An MCP server is long-lived: it keeps running the code it loaded at start,
while serving the viewer page off disk. So a rebuilt page can meet a server
from three days ago, and nothing anywhere says so. That happened on
2026-08-15 and cost twenty minutes and a hand-rolled WebSocket to diagnose —
backlog 22.

**Version numbers do not answer this, which is why they are not what this
module reports.** `__version__` has read `0.1.0.dev0` for every build there
has ever been, and `PROTOCOL_VERSION` has been `1` since the first Phase 1
commit — including across the change that caused the incident, where the
handshake gained a required token and the number did not move. Two servers
three days apart agree on both numbers and disagree about the protocol.

What distinguishes them is the code itself, so that is what is fingerprinted:
every `.py` in the package, by size and modification time, as they were when
this process imported them. Compare against disk and a stale process is
obvious without anyone having remembered to bump anything.

Content is deliberately not hashed. The question is "is this process running
what is on disk", and mtime plus size answers it for the case that occurs —
a rebuild or a `git checkout` under a running server — while reading every
file on every call to answer it more precisely would be a cost paid forever
against a case that does not happen.

For an installed wheel this never fires, because nothing rewrites the files
under it. That is correct rather than a limitation: there is no stale process
to warn about when the code cannot change.
"""

from __future__ import annotations

import time
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent


def _fingerprint() -> dict[str, tuple[int, int]]:
    """Every source file in the package, by size and mtime."""
    return {
        str(path.relative_to(_PACKAGE)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(_PACKAGE.rglob("*.py"))
        if path.is_file()
    }


#: Taken once, at import, which is the only moment it describes.
_LOADED = _fingerprint()
_LOADED_AT = time.time()


def changed_since_load() -> list[str]:
    """Source files that differ from what this process imported.

    Empty means the running code is the code on disk. Anything else names what
    a restart would pick up, which is the sentence the incident needed.
    """
    now = _fingerprint()
    return sorted(
        set(_LOADED) ^ set(now)
        | {name for name, was in _LOADED.items() if now.get(name) != was}
    )


def running_since() -> str:
    """When this process loaded its code, local time, to the minute."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(_LOADED_AT))
