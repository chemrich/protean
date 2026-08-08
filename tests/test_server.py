"""Server tool tests against the mock viewer."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import Any

import pytest

import protean_mcp.server as server_mod
from protean_mcp.analysis.electrostatics import read_dx
from protean_mcp.connection import ViewerError
from protean_mcp.handles import summarise
from protean_mcp.server import (
    clear_viewer,
    electrostatics,
    fetch_structure,
    interface,
    load_session,
    mcp,
    save_session,
    screenshot,
    select,
)

# 1x1 transparent PNG
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


@pytest.fixture
def wired_bridge(bridge, viewer, monkeypatch):
    """Point the server module's global bridge at the test bridge."""
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    return viewer


async def test_tools_registered():
    tools = {t.name for t in await mcp.list_tools()}
    assert {"open_viewer", "fetch_structure", "screenshot", "clear_viewer"} <= tools


async def test_fetch_structure_tool(wired_bridge, tmp_path, monkeypatch):
    f = tmp_path / "toy.pdb"
    f.write_text(
        "ATOM      1  N   MET A   1      11.104   6.134  -6.504  1.00  0.00           N\n"
        "END\n"
    )
    loaded = {}

    def on_load(args):
        loaded.update(args)
        return {"loaded": args["name"]}

    wired_bridge.handlers["load_structure"] = on_load
    task = wired_bridge.serve(1)
    msg = await fetch_structure(str(f))
    await task
    assert "toy" in msg and "local file" in msg
    assert loaded["format"] == "pdb"


async def test_fetch_structure_without_viewer(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    with pytest.raises(ViewerError, match="No viewer connected"):
        await fetch_structure("1ubq")


async def test_clear_tool(wired_bridge):
    wired_bridge.handlers["clear"] = lambda args: {}
    task = wired_bridge.serve(1)
    assert "cleared" in (await clear_viewer()).lower()
    await task


async def test_screenshot_tool(wired_bridge, tmp_path):
    wired_bridge.handlers["screenshot"] = lambda args: {
        "data_uri": f"data:image/png;base64,{PNG_B64}"
    }
    task = wired_bridge.serve(1)
    out = tmp_path / "shot.png"
    result = await screenshot(path=str(out))
    await task
    assert out.read_bytes() == base64.b64decode(PNG_B64)
    assert any("Saved to" in str(item) for item in result)


# -- sessions ----------------------------------------------------------------


async def test_save_session_writes_a_gzipped_document(wired_bridge, tmp_path):
    wired_bridge.handlers["save_session"] = lambda args: {
        "snapshot": {"id": "snap", "data": "x" * 5000},
        "handles": {"prot": ["ref-1"], "hemes": ["ref-2"]},
    }
    task = wired_bridge.serve(1)
    out = await save_session(str(tmp_path / "demo"))
    await task

    written = Path(out["path"])
    assert written.name == "demo.protean"  # suffix supplied when omitted
    assert out["handles"] == ["hemes", "prot"]
    # Sessions are mostly embedded mmCIF; compression is the point.
    assert out["bytes"] < out["uncompressed_bytes"]

    document = json.loads(gzip.decompress(written.read_bytes()))
    assert document["format"] == "protean-session"
    assert document["version"] == 1
    assert document["molstar"]["id"] == "snap"
    assert document["handles"]["prot"] == ["ref-1"]


async def test_session_round_trip(wired_bridge, tmp_path):
    wired_bridge.handlers["save_session"] = lambda args: {
        "snapshot": {"id": "snap"},
        "handles": {"prot": ["ref-1"]},
    }
    sent = {}

    def on_load(args):
        sent.update(args)
        return {"restored": ["prot"], "dropped": [], "atom_count": 4779}

    wired_bridge.handlers["load_session"] = on_load

    task = wired_bridge.serve(2)
    saved = await save_session(str(tmp_path / "s.protean"))
    result = await load_session(saved["path"])
    await task

    assert sent["snapshot"] == {"id": "snap"}
    assert sent["handles"] == {"prot": ["ref-1"]}
    assert result["restored"] == ["prot"]
    assert result["atom_count"] == 4779


async def test_load_session_missing_file(wired_bridge, tmp_path):
    with pytest.raises(ViewerError, match="No session file"):
        await load_session(str(tmp_path / "nope.protean"))


async def test_load_session_rejects_non_gzip(wired_bridge, tmp_path):
    bad = tmp_path / "bad.protean"
    bad.write_text("this is not a session")
    with pytest.raises(ViewerError, match="not a readable protean session"):
        await load_session(str(bad))


async def test_load_session_rejects_foreign_format(wired_bridge, tmp_path):
    other = tmp_path / "other.protean"
    other.write_bytes(gzip.compress(json.dumps({"format": "pymol-session"}).encode()))
    with pytest.raises(ViewerError, match="not a protean session"):
        await load_session(str(other))


async def test_load_session_rejects_future_version(wired_bridge, tmp_path):
    future = tmp_path / "future.protean"
    future.write_bytes(
        gzip.compress(
            json.dumps(
                {"format": "protean-session", "version": 99, "molstar": {}}
            ).encode()
        )
    )
    with pytest.raises(ViewerError, match="session version 99"):
        await load_session(str(future))


async def test_unparseable_coordinates_still_load_but_flag_analysis(
    wired_bridge, tmp_path
):
    """Mol* renders files our analysis parser rejects. Display should survive;
    analysis should degrade visibly rather than silently matching nothing."""
    f = tmp_path / "broken.pdb"
    f.write_text("ATOM      1  N   MET A   1  garbage\nEND\n")
    wired_bridge.handlers["load_structure"] = lambda args: {"loaded": args["name"]}
    task = wired_bridge.serve(1)
    message = await fetch_structure(str(f))
    await task
    assert "analysis unavailable" in message

    with pytest.raises(ViewerError, match="could not be parsed for analysis"):
        await select("chain A")


def _pdb_line(
    serial: int,
    name: str,
    resname: str,
    chain: str,
    resseq: int,
    xyz: tuple[float, float, float],
    element: str,
) -> str:
    x, y, z = xyz
    return (
        f"ATOM  {serial:5d} {name:^4s} {resname:3s} {chain:1s}{resseq:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {element:>2s}"
    )


def _two_chain_pdb(path: Path) -> Path:
    """An aspartate facing an arginine across a chain boundary."""
    atoms = [
        (1, "CA", "ASP", "A", 1, (0.0, 0.0, 0.0), "C"),
        (2, "CG", "ASP", "A", 1, (1.5, 0.0, 0.0), "C"),
        (3, "OD1", "ASP", "A", 1, (2.2, 1.0, 0.0), "O"),
        (4, "OD2", "ASP", "A", 1, (2.2, -1.0, 0.0), "O"),
        (5, "CA", "ARG", "B", 2, (8.0, 0.0, 0.0), "C"),
        (6, "CZ", "ARG", "B", 2, (6.5, 0.0, 0.0), "C"),
        (7, "NH1", "ARG", "B", 2, (5.2, 1.0, 0.0), "N"),
        (8, "NH2", "ARG", "B", 2, (5.2, -1.0, 0.0), "N"),
    ]
    path.write_text("\n".join(_pdb_line(*a) for a in atoms) + "\nEND\n")
    return path


async def _load(wired_bridge, path: Path) -> None:
    wired_bridge.handlers["load_structure"] = lambda args: {"loaded": args["name"]}
    task = wired_bridge.serve(1)
    await fetch_structure(str(path))
    await task


async def test_interface_registers_handles_on_the_loaded_structure(
    wired_bridge, tmp_path
):
    """The point of the change: the computed set is addressable afterwards."""
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "pair.pdb"))

    shown: list[str] = []

    def on_select(args: dict[str, Any]) -> dict[str, Any]:
        shown.append(args["name"])
        return {}

    wired_bridge.handlers["select"] = on_select
    task = wired_bridge.serve(2)
    payload = await interface("A", "B")
    await task

    assert payload["handles"] == {"a": "iface_a", "b": "iface_b"}
    # The handles hold whole residues, not just the atoms that lost surface.
    assert len(server_mod._handles.get("iface_a")) == 4
    assert len(server_mod._handles.get("iface_b")) == 4
    # And the viewer was told about both, so they are visible as named sets.
    assert shown == ["iface_a", "iface_b"]


async def test_interface_handles_name_the_residues_that_were_reported(
    wired_bridge, tmp_path
):
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "pair.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await interface("A", "B")
    await task

    summary = summarise(server_mod._structure, server_mod._handles.get("iface_a").indices)
    assert summary["chains"] == ["A"]
    assert [r["comp"] for r in summary["residues"]] == ["ASP"]
    assert [(r["chain"], r["seq"]) for r in summary["residues"]] == [
        (r["chain"], r["seq"]) for r in payload["interface_residues_a"]
    ]


async def test_custom_handle_names_are_used(wired_bridge, tmp_path):
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "pair.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await interface("A", "B", name_a="epitope", name_b="paratope")
    await task

    assert payload["handles"] == {"a": "epitope", "b": "paratope"}
    assert "epitope" in server_mod._handles.names()


async def test_interface_on_a_different_structure_says_why_it_has_no_handles(
    wired_bridge, tmp_path
):
    """A missing key would read as 'no interface'. Say which claim is being made."""
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "loaded.pdb"))
    other = _two_chain_pdb(tmp_path / "other.pdb")

    payload = await interface("A", "B", identifier=str(other))

    assert payload["handles"] is None
    assert "not the loaded structure" in payload["handles_note"]
    assert server_mod._handles.names() == []
    # The analysis itself still ran.
    assert payload["buried_area"] > 0


async def test_naming_the_loaded_structure_still_registers_handles(
    wired_bridge, tmp_path
):
    """Passing the identifier explicitly must not lose the handles."""
    path = _two_chain_pdb(tmp_path / "pair.pdb")
    await _load(wired_bridge, path)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await interface("A", "B", identifier=str(path))
    await task
    assert payload["handles"] == {"a": "iface_a", "b": "iface_b"}


async def test_interface_without_a_structure_asks_for_one(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    monkeypatch.setattr(server_mod, "_structure", None)
    monkeypatch.setattr(server_mod, "_structure_error", None)
    with pytest.raises(ViewerError, match="No structure loaded"):
        await interface("A", "B")


def _tiny_protein_pdb(path: Path) -> Path:
    """Two glycines with a complete C-terminus, so pdb2pqr will charge them."""
    atoms = [
        (1, "N", "GLY", 1, (0.0, 0.0, 0.0), "N"),
        (2, "CA", "GLY", 1, (1.46, 0.0, 0.0), "C"),
        (3, "C", "GLY", 1, (2.0, 1.42, 0.0), "C"),
        (4, "O", "GLY", 1, (1.25, 2.39, 0.0), "O"),
        (5, "N", "GLY", 2, (3.33, 1.5, 0.0), "N"),
        (6, "CA", "GLY", 2, (4.0, 2.78, 0.0), "C"),
        (7, "C", "GLY", 2, (5.5, 2.65, 0.0), "C"),
        (8, "O", "GLY", 2, (6.1, 1.58, 0.0), "O"),
        (9, "OXT", "GLY", 2, (6.05, 3.75, 0.0), "O"),
    ]
    path.write_text(
        "\n".join(_pdb_line(s, n, r, "A", i, xyz, e) for s, n, r, i, xyz, e in atoms)
        + "\nEND\n"
    )
    return path


async def test_electrostatics_reports_which_method_ran(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    out = await electrostatics(
        method="coulombic", spacing=2.0, padding=6.0, path=str(tmp_path / "p.dx")
    )
    assert "screened Coulomb" in out["method"]
    assert out["units"] == "kT/e"
    assert "caveat" in out, "the approximation must carry its own caveat"
    assert out["charges"]["forcefield"] == "AMBER"
    assert Path(out["dx_path"]).is_file()


async def test_written_dx_is_readable_back(wired_bridge, tmp_path):
    """The file is the artifact the viewer will consume, so it has to parse."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    out = await electrostatics(
        method="coulombic", spacing=2.0, padding=6.0, path=str(tmp_path / "p.dx")
    )
    grid = read_dx(Path(out["dx_path"]).read_text())
    assert list(grid.shape) == out["grid_shape"]


async def test_unknown_method_is_refused(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="Unknown method"):
        await electrostatics(method="magic")


async def test_electrostatics_samples_a_handle_per_residue(wired_bridge, tmp_path):
    """Answers 'is this patch acidic?' with no rendering involved."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    await select("all", name="whole")
    await task

    out = await electrostatics(
        method="coulombic",
        spacing=1.0,
        padding=8.0,
        handle="whole",
        path=str(tmp_path / "p.dx"),
    )
    sampled = out["sampled"]
    assert sampled["handle"] == "whole"
    assert sampled["residues_sampled"] == 2
    assert sampled["atoms_outside_grid"] == 0
    assert {r["comp"] for r in sampled["most_negative"]} == {"GLY"}
    # Sorted by potential, so the two ends bracket each other.
    assert (
        sampled["most_negative"][0]["potential"]
        <= sampled["most_positive"][0]["potential"]
    )


async def test_sampling_an_unknown_handle_names_the_known_ones(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="No selection named"):
        await electrostatics(method="coulombic", spacing=2.0, handle="nope")


async def test_electrostatics_without_a_structure_asks_for_one(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    monkeypatch.setattr(server_mod, "_structure", None)
    monkeypatch.setattr(server_mod, "_structure_error", None)
    with pytest.raises(ViewerError, match="No structure loaded"):
        await electrostatics()
