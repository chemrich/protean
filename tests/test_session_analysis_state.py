"""After load_session, the analysis and the viewer must hold the same molecule.

Backlog 20, found by the going-public security pass though it needs no
attacker: `load_session` restored the viewer and left the Python side holding
whatever was loaded before, so every count, distance and selection afterwards
described a different structure from the picture — silently. Measured at the
time: viewer 100 atoms, `_structure` 660, `_structure_identifier` still
'1ubq', and nothing anywhere reporting a discrepancy.

The session embeds its structure, so both halves can be restored from the file.
What decides *how* to build it is the viewer's own atom count: the same
deposited text assembles two ways (1HHO reads 4792 biological, 2396
asymmetric) and nothing in the file records which was chosen, so the reading
that matches the picture is the one that describes it.
"""

import gzip
import io
import json
from pathlib import Path
from typing import Any

import biotite.structure as struc
import numpy as np
import pytest
from biotite.structure.io import pdbx

from protean_mcp import server
from protean_mcp.connection import ViewerError
from protean_mcp.server import (
    SESSION_FORMAT,
    SESSION_VERSION,
    _embedded_structure,
    load_session,
)

ATOMS = 12


def cif_text(count: int = ATOMS) -> str:
    """A small but real structure, so the restore path is exercised for real."""
    array: Any = struc.AtomArray(count)
    array.coord = np.arange(count * 3, dtype=float).reshape(count, 3)
    array.chain_id = np.array(["A"] * count)
    array.res_id = np.arange(1, count + 1)
    array.res_name = np.array(["GLY"] * count)
    array.atom_name = np.array(["CA"] * count)
    array.element = np.array(["C"] * count)
    handle = pdbx.CIFFile()
    pdbx.set_structure(handle, array)
    text = io.StringIO()
    handle.write(text)
    return text.getvalue()


class FakeBridge:
    """A viewer that accepts a restore and reports an atom count."""

    viewer_connected = True

    def __init__(self, atom_count: int = ATOMS) -> None:
        self.calls: list[str] = []
        self.atom_count = atom_count

    async def request(self, action: str, args: Any = None, timeout: float = 0) -> Any:
        self.calls.append(action)
        return {"restored": ["auto"], "dropped": [], "atom_count": self.atom_count}


@pytest.fixture
def viewer(monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(server, "_bridge", bridge)
    monkeypatch.setattr(server, "get_bridge", lambda: bridge)
    return bridge


def write_session(path: Path, *, data: str | None = None, fmt: str = "mmcif") -> Path:
    transforms: list[dict[str, Any]] = [{"transformer": "build-in.root"}]
    if data is not None:
        transforms += [
            {"transformer": "ms-plugin.raw-data", "params": {"data": data}},
            {
                "transformer": (
                    "ms-plugin.trajectory-from-pdb"
                    if fmt == "pdb"
                    else "ms-plugin.trajectory-from-mmcif"
                )
            },
        ]
    document = {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "created": "2026-08-15T00:00:00+00:00",
        "handles": {},
        "molstar": {"data": {"tree": {"transforms": transforms}}},
    }
    path.write_bytes(gzip.compress(json.dumps(document).encode()))
    return path


async def test_the_analysis_is_restored_from_the_sessions_own_copy(
    tmp_path, viewer, monkeypatch
):
    monkeypatch.setattr(server, "_structure", object())
    monkeypatch.setattr(server, "_structure_identifier", "1ubq")

    reply = await load_session(
        str(write_session(tmp_path / "s.protean", data=cif_text()))
    )

    assert server._structure is not None
    assert int(server._structure.array_length()) == ATOMS
    assert reply["analysis_atoms"] == ATOMS
    assert reply["agrees_with_viewer"] is True
    assert "restored" in reply["analysis"]


async def test_selections_work_again_after_a_restore(tmp_path, viewer):
    """The point of restoring rather than clearing: analysis is usable."""
    await load_session(str(write_session(tmp_path / "s.protean", data=cif_text())))

    assert server._require_structure() is not None


async def test_the_previous_structure_never_survives(tmp_path, viewer, monkeypatch):
    """The original bug: analysis kept answering about the molecule before."""
    monkeypatch.setattr(server, "_structure", object())
    monkeypatch.setattr(server, "_structure_identifier", "1ubq")

    session = write_session(tmp_path / "s.protean", data=cif_text())
    await load_session(str(session))

    assert int(server._structure.array_length()) == ATOMS
    # Naming the session, not merely *not* naming the old molecule: asserting
    # only the latter also passes when the identifier is left empty.
    assert server._structure_identifier == str(session)


async def test_a_structure_that_disagrees_with_the_viewer_is_not_kept(
    tmp_path, viewer, monkeypatch
):
    """Neither reading matches, so analysis is left empty rather than wrong.

    This is the whole point: a structure that disagrees with the picture is the
    failure this change exists to remove, and keeping it with a caveat attached
    would be that failure with a note on it.
    """
    monkeypatch.setattr(server, "_structure", object())
    viewer.atom_count = 999

    reply = await load_session(
        str(write_session(tmp_path / "s.protean", data=cif_text()))
    )

    assert server._structure is None
    assert reply["agrees_with_viewer"] is False
    assert "999" in reply["analysis"] and str(ATOMS) in reply["analysis"]
    with pytest.raises(ViewerError):
        server._require_structure()


async def test_a_session_with_no_structure_says_so(tmp_path, viewer, monkeypatch):
    monkeypatch.setattr(server, "_structure", object())

    reply = await load_session(str(write_session(tmp_path / "s.protean")))

    assert server._structure is None
    assert "no structure" in reply["analysis"]


async def test_an_unparseable_structure_leaves_the_viewer_alone(
    tmp_path, viewer, monkeypatch
):
    """Mol* is more tolerant than this parser, so the scene must still restore."""
    monkeypatch.setattr(server, "_structure", object())

    reply = await load_session(
        str(write_session(tmp_path / "s.protean", data="not a structure\n"))
    )

    assert server._structure is None
    assert "could not be parsed" in reply["analysis"]
    assert reply["atom_count"] == ATOMS  # the viewer restored regardless


async def test_a_trajectory_does_not_survive(tmp_path, viewer, monkeypatch):
    """It belongs to the molecule it was recorded for, exactly as on a fetch."""
    monkeypatch.setattr(server, "_trajectory", object())

    reply = await load_session(
        str(write_session(tmp_path / "s.protean", data=cif_text()))
    )

    assert server._trajectory is None
    assert "trajectory" in reply["analysis"]


async def test_the_viewer_still_restores(tmp_path, viewer):
    """Whatever happens to the analysis must not cost the scene."""
    reply = await load_session(
        str(write_session(tmp_path / "s.protean", data=cif_text()))
    )

    assert viewer.calls == ["load_session"]
    assert reply["restored"] == ["auto"]


def test_the_format_follows_the_transform_molstar_used():
    """A .pdb reaches Mol* through trajectory-from-pdb, an mmCIF through -mmcif.

    Reading it back from the tree rather than sniffing the text means the
    analysis parses what the viewer parsed, in the format the viewer used.
    """

    def tree(*names: str, data: str = "x") -> dict[str, Any]:
        transforms: list[dict[str, Any]] = [
            {"transformer": "ms-plugin.raw-data", "params": {"data": data}}
        ]
        transforms += [{"transformer": n} for n in names]
        return {"data": {"tree": {"transforms": transforms}}}

    assert _embedded_structure(tree("ms-plugin.trajectory-from-pdb")) == ("x", "pdb")
    assert _embedded_structure(tree("ms-plugin.trajectory-from-mmcif")) == ("x", "mmcif")
    assert _embedded_structure(tree()) == ("x", "mmcif")


def test_a_session_with_no_raw_data_has_no_structure_to_restore():
    """A volume travels by URL, so a session can legitimately hold none."""
    volume_only = {
        "data": {
            "tree": {
                "transforms": [
                    {
                        "transformer": "ms-plugin.download",
                        "params": {"url": "/volumes/v"},
                    },
                    {"transformer": "ms-plugin.volume-from-ccp4"},
                ]
            }
        }
    }
    assert _embedded_structure(volume_only) is None
