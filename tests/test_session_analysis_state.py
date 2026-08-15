"""After load_session, the analysis must not answer about the previous molecule.

Found by the going-public security pass (backlog 20), though it needs no
attacker: `load_session` restored the viewer and left the Python side holding
whatever was loaded before, so every count, distance and selection afterwards
described a different structure from the picture — silently. Measured at the
time: viewer 100 atoms, `_structure` 660, `_structure_identifier` still
'1ubq', and nothing anywhere reporting a discrepancy.
"""

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from protean_mcp import server
from protean_mcp.connection import ViewerError
from protean_mcp.server import SESSION_FORMAT, SESSION_VERSION, load_session


class FakeBridge:
    """A viewer that accepts a restore, so the test is about the Python side."""

    viewer_connected = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request(self, action: str, args: Any = None, timeout: float = 0) -> Any:
        self.calls.append(action)
        return {"restored": ["auto"], "dropped": [], "atom_count": 100}


@pytest.fixture
def viewer(monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(server, "_bridge", bridge)
    monkeypatch.setattr(server, "get_bridge", lambda: bridge)
    return bridge


def write_session(path: Path) -> Path:
    document = {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "created": "2026-08-15T00:00:00+00:00",
        "handles": {},
        "molstar": {
            "data": {
                "tree": {
                    "transforms": [
                        {
                            "transformer": "ms-plugin.raw-data",
                            "params": {"data": "data_x\n"},
                        }
                    ]
                }
            }
        },
    }
    path.write_bytes(gzip.compress(json.dumps(document).encode()))
    return path


async def test_the_previous_structure_does_not_survive_a_session_load(
    tmp_path, viewer, monkeypatch
):
    """The bug itself: analysis kept answering about the molecule before."""
    monkeypatch.setattr(server, "_structure", object())
    monkeypatch.setattr(server, "_structure_identifier", "1ubq")

    await load_session(str(write_session(tmp_path / "s.protean")))

    assert server._structure is None
    assert server._structure_identifier is None


async def test_analysis_refuses_rather_than_answering_about_the_wrong_molecule(
    tmp_path, viewer, monkeypatch
):
    """A refusal is the point: the alternative is a confident wrong number."""
    monkeypatch.setattr(server, "_structure", object())
    monkeypatch.setattr(server, "_structure_identifier", "1ubq")

    await load_session(str(write_session(tmp_path / "s.protean")))

    with pytest.raises(ViewerError, match="No structure loaded"):
        server._require_structure()


async def test_the_reply_says_the_analysis_was_cleared(tmp_path, viewer, monkeypatch):
    """Silently clearing it would be a smaller version of the same bug.

    A caller who had a structure loaded should learn it is gone from the reply
    rather than from a later refusal.
    """
    monkeypatch.setattr(server, "_structure", object())
    monkeypatch.setattr(server, "_structure_identifier", "1ubq")

    reply = await load_session(str(write_session(tmp_path / "s.protean")))

    assert server._structure is None  # or the message is a lie
    assert "cleared" in reply["analysis"]
    assert "1ubq" in reply["analysis"]
    assert "fetch_structure" in reply["analysis"]


async def test_a_trajectory_does_not_survive_either(tmp_path, viewer, monkeypatch):
    """It belongs to the molecule it was recorded for, exactly as on a fetch."""
    monkeypatch.setattr(server, "_structure", object())
    monkeypatch.setattr(server, "_trajectory", object())

    reply = await load_session(str(write_session(tmp_path / "s.protean")))

    assert server._trajectory is None
    assert "trajectory" in reply["analysis"]


async def test_the_viewer_still_restores(tmp_path, viewer, monkeypatch):
    """Clearing the analysis must not cost the scene it was called for."""
    reply = await load_session(str(write_session(tmp_path / "s.protean")))

    assert viewer.calls == ["load_session"]
    assert reply["atom_count"] == 100
    assert reply["restored"] == ["auto"]
