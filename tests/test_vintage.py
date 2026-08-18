"""Which build answered, and whether it is the one on disk — backlog 22.

The incident: a server started three days earlier served that day's viewer page
off disk, and nothing anywhere said so. Twenty minutes and a hand-rolled
WebSocket went into finding out.

The backlog prescribed comparing version numbers, and these tests exist partly
to record why that would not have worked. `__version__` has read `0.1.0.dev0`
for every build there has been, and `PROTOCOL_VERSION` has been 1 since the
first commit — including across the change that caused the incident, where the
handshake gained a required token and neither number moved.
"""

from __future__ import annotations

import os
from pathlib import Path

import protean_mcp.server as server_mod
from protean_mcp import PROTOCOL_VERSION, __version__, vintage


def _touch(path: Path):
    """Move a file's mtime and put it back, the way a rebuild would."""
    before = path.stat().st_mtime_ns
    path.touch()
    return lambda: os.utime(path, ns=(before, before))


def test_a_process_running_the_code_on_disk_says_nothing_about_staleness():
    """Silence is the common case and must stay quiet, or nobody reads it."""
    assert vintage.changed_since_load() == []
    assert "older code" not in server_mod._vintage_note()


def test_a_process_whose_source_changed_underneath_it_says_so():
    """The whole point: a rebuild under a long-lived server is now visible."""
    restore = _touch(Path(server_mod.__file__))
    try:
        assert vintage.changed_since_load() == ["server.py"]
        note = server_mod._vintage_note()
        assert "older code than the files on disk" in note
        assert "server.py" in note
        assert "restart" in note.lower()
    finally:
        restore()
    assert vintage.changed_since_load() == []


def test_the_note_always_names_the_build_and_when_it_started():
    """The version alone cannot identify a build, and is reported anyway.

    Two machines comparing notes is the case it serves. Two *moments* is the
    case it cannot serve, which is why staleness carries the information.
    """
    note = server_mod._vintage_note()
    assert __version__ in note
    assert "running since" in note


def test_version_numbers_would_not_have_caught_the_incident():
    """Recorded as a test so the claim cannot quietly stop being true.

    If either number ever starts moving per build, this fails and the reasoning
    in vintage.py's docstring needs revisiting — which is the point.
    """
    assert __version__ == "0.1.0.dev0", "a version that moves would change the argument"
    assert PROTOCOL_VERSION == 1, "a protocol number that moves would change the argument"
