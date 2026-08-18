"""Server tool tests against the mock viewer."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom
from biotite.structure import array as atom_array
from biotite.structure import stack as atom_stack
from PIL import Image as PILImage

import protean_mcp.server as server_mod
from protean_mcp.analysis.electrostatics import read_dx
from protean_mcp.connection import ViewerError
from protean_mcp.handles import summarise
from protean_mcp.selections_numpy import (
    LoadedStructure,
    conformer_state,
    load_structure,
    select_mask,
)
from protean_mcp.server import (
    background,
    capabilities,
    clear_viewer,
    color_by_conservation,
    color_by_potential,
    combine,
    conservation,
    effects,
    electrostatics,
    fetch_structure,
    interface,
    lighting,
    load_session,
    load_volume,
    material,
    mcp,
    near,
    opacity,
    path_trace,
    preset,
    record_trajectory,
    rmsd_series,
    rmsf,
    save_session,
    screenshot,
    select,
    shading,
    show,
    snapshot,
    spin,
    turntable,
)

from .test_volumes import write_mrc

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


# -- the viewer/analysis atom-count invariant ----------------------------------


def _loaded(atoms: int, surplus: int = 0) -> LoadedStructure:
    """A LoadedStructure of *atoms* atoms carrying *surplus* hidden conformers."""
    array = atom_array(
        [
            Atom(
                [float(i), 0.0, 0.0],
                chain_id="A",
                res_id=1,
                ins_code="",
                res_name="GLY",
                atom_name="CA",
                element="C",
                hetero=False,
                b_factor=0.0,
                occupancy=1.0,
                atom_id=i,
            )
            for i in range(atoms)
        ]
    )
    return LoadedStructure(
        array=array, assembly="asymmetric", copies=1, altloc_surplus=surplus
    )


def test_matching_counts_are_reported_as_agreement():
    note = server_mod._assembly_note(_loaded(100), {"atom_count": 100})
    assert "100 atoms in both viewer and analysis" in note
    assert "MISMATCH" not in note


def test_a_conformer_sized_difference_is_now_a_real_mismatch():
    """The explained-difference branch is gone, deliberately.

    It existed because biotite kept one conformer per site while Mol* drew all
    of them, so a gap of exactly the surplus was the same molecule counted two
    defensible ways. Both halves now load every conformer, so that gap can only
    mean the loading failed -- and calling it explained would be the silence
    this note exists to break.
    """
    note = server_mod._assembly_note(_loaded(15712, 217), {"atom_count": 15929})
    assert "MISMATCH" in note
    assert "unreliable" in note


def test_a_mismatch_still_says_how_much_of_it_is_conformers():
    """Not an excuse for the difference, but a lead on where it came from."""
    note = server_mod._assembly_note(_loaded(15712, 217), {"atom_count": 16000})
    assert "MISMATCH" in note
    assert "unreliable" in note
    assert "217 rows of the file are alternate conformers" in note


def test_a_difference_with_no_conformers_to_explain_it_is_a_mismatch():
    note = server_mod._assembly_note(_loaded(2396), {"atom_count": 4792})
    assert "MISMATCH" in note
    assert "unreliable" in note
    assert "alternate conformers" not in note


def test_a_viewer_that_reports_no_count_is_not_called_agreement():
    note = server_mod._assembly_note(_loaded(100, 5), {})
    assert "the viewer reported no count" in note
    assert "MISMATCH" not in note


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
    altloc: str = " ",
    occupancy: float = 1.0,
) -> str:
    x, y, z = xyz
    return (
        f"ATOM  {serial:5d} {name:^4s}{altloc:1s}{resname:3s} {chain:1s}{resseq:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{occupancy:6.2f}  0.00          {element:>2s}"
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


# -- near() and its radius -----------------------------------------------------


async def _load_with_handle(wired_bridge, tmp_path) -> None:
    """A loaded structure with one handle, `sele`, holding chain A."""
    await _load(wired_bridge, _two_chain_pdb(tmp_path / "pair.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    await select("chain A", name="sele")
    await task


@pytest.mark.parametrize("radius", [0.0, -1.0, -0.001, float("nan"), float("inf")])
async def test_near_refuses_a_non_positive_radius(wired_bridge, tmp_path, radius):
    """It used to answer 0 atoms and no complaint.

    An empty set is the one answer that looks like a real result, so the
    request that can only produce one is refused rather than served. `nan`
    slips past a bare `<= 0`, and `inf` is not a question either.
    """
    await _load_with_handle(wired_bridge, tmp_path)
    with pytest.raises(ViewerError, match="radius must be greater than 0"):
        await near("sele", radius=radius)


async def test_near_still_answers_a_real_radius(wired_bridge, tmp_path):
    """Guards the test above: refusing every radius would satisfy it."""
    await _load_with_handle(wired_bridge, tmp_path)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    summary = await near("sele", radius=5.0, name="shell")
    await task
    assert summary["atom_count"] > 0
    assert "shell" in server_mod._handles.names()


async def test_near_names_the_radius_it_rejected(wired_bridge, tmp_path):
    await _load_with_handle(wired_bridge, tmp_path)
    with pytest.raises(ViewerError, match=r"got -2\.5"):
        await near("sele", radius=-2.5)


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


def _water_only_pdb(path: Path) -> Path:
    """No polymer at all, so a view that needs one has nothing to draw."""
    atoms = [
        (1, "O", "HOH", 101, (0.0, 0.0, 0.0), "O"),
        (2, "O", "HOH", 102, (3.0, 0.0, 0.0), "O"),
        (3, "O", "HOH", 103, (0.0, 3.0, 0.0), "O"),
    ]
    path.write_text(
        "\n".join(_pdb_line(s, n, r, "A", i, xyz, e) for s, n, r, i, xyz, e in atoms)
        + "\nEND\n"
    )
    return path


def _protein_and_water_pdb(path: Path) -> Path:
    """The tiny protein with three waters around it, for the solvent keyword."""
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
        (10, "O", "HOH", 101, (0.0, 5.0, 0.0), "O"),
        (11, "O", "HOH", 102, (2.0, 6.5, 0.0), "O"),
        (12, "O", "HOH", 103, (4.0, 7.0, 0.0), "O"),
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


def _peptide_pdb(path: Path, n: int = 12) -> Path:
    """A single chain long enough to clear the minimum sequence length."""
    lines = []
    serial = 1
    for res in range(1, n + 1):
        for i, (name, elem) in enumerate(
            (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"))
        ):
            lines.append(
                _pdb_line(
                    serial, name, "ALA", "A", res, (float(res) * 4, float(i), 0.0), elem
                )
            )
            serial += 1
    path.write_text("\n".join(lines) + "\nEND\n")
    return path


def _peptide_with_alternate_pdb(
    path: Path, n: int = 12, alt_at: tuple[int, ...] = (2, 11)
) -> Path:
    """The same chain, with two residues' CAs modelled in two positions.

    Both positions are load-bearing and they test different things.

    Residue 2 is early, so every atom after it sits at a different index in the
    resolved-state array than in the full one — that is what makes a handle
    built in the wrong index space land on the wrong *residue* rather than
    merely on the wrong atom of the right one.

    Residue 11 is inside the conserved tail the graded alignment produces, so
    the handle covering it must hold one conformer rather than both. Without
    it, a handle built over whole residues of the full array — the obvious way
    to "simplify" this code — passes every residue-level assertion while
    quietly carrying two rows for one atom.
    """
    lines = []
    serial = 1
    for res in range(1, n + 1):
        for i, (name, elem) in enumerate(
            (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"))
        ):
            xyz = (float(res) * 4, float(i), 0.0)
            if res in alt_at and name == "CA":
                # Highest occupancy wins, so state resolution keeps A and drops
                # B — one row shorter than the array the viewer holds.
                lines.append(
                    _pdb_line(serial, name, "ALA", "A", res, xyz, elem, "A", 0.7)
                )
                serial += 1
                lines.append(
                    _pdb_line(
                        serial,
                        name,
                        "ALA",
                        "A",
                        res,
                        (xyz[0], xyz[1] + 0.4, 0.0),
                        elem,
                        "B",
                        0.3,
                    )
                )
            else:
                lines.append(_pdb_line(serial, name, "ALA", "A", res, xyz, elem))
            serial += 1
    path.write_text("\n".join(lines) + "\nEND\n")
    return path


def _fake_msa(monkeypatch, sequence_length: int, depth: int = 20) -> None:
    """Stand in for the MMseqs2 search: column 0 conserved, the rest varied."""
    query = "A" * sequence_length
    varied = "A" + "W" * (sequence_length - 1)
    a3m = f">query\n{query}\n" + "".join(
        f">h{i}\n{query if i % 2 else varied}\n" for i in range(depth)
    )

    async def fake_fetch(sequence, cache_dir, **kwargs):
        return a3m, "search"

    monkeypatch.setattr(server_mod, "_fetch_msa", fake_fetch)


def _fake_msa_graded(monkeypatch, sequence_length: int) -> None:
    """An alignment whose entropy falls monotonically along the chain.

    Column *i* disagrees with the query in ``n-i`` of the ``2n`` homologs, so
    the first column is an even split — maximum entropy — and the last is
    nearly invariant. The conserved quartile is therefore the tail of the
    chain, which is what the test needs: only a residue *after* the alternate
    site sits at a different index in the two arrays.

    Built from the mismatch counts rather than from whole sequences because
    entropy is a function of the column, and a construction that reads
    left-to-right along each homolog produced a U — low at both ends, since a
    column that disagrees in almost every sequence is as ordered as one that
    agrees in almost every sequence.
    """
    query = "A" * sequence_length
    mismatches = [sequence_length - i for i in range(sequence_length)]
    homologs = [
        "".join("W" if j < m else "A" for m in mismatches)
        for j in range(2 * sequence_length)
    ]
    a3m = f">query\n{query}\n" + "".join(f">h{j}\n{h}\n" for j, h in enumerate(homologs))

    async def fake_fetch(sequence, cache_dir, **kwargs):
        return a3m, "search"

    monkeypatch.setattr(server_mod, "_fetch_msa", fake_fetch)


async def test_conservation_handles_name_the_residues_the_scores_name(
    wired_bridge, tmp_path, monkeypatch
):
    """The scores and the handles must describe the same residues.

    `conservation` scores a resolved conformer state, which is shorter than
    the array the viewer holds, while `_register` and `_display` resolve
    indices against the full one. Indexing the second with positions computed
    in the first shifts every residue past the alternate site, and nothing
    downstream can notice: the scores are right, the atom count is right, and
    the handle draws a neighbouring residue.
    """
    await _load(wired_bridge, _peptide_with_alternate_pdb(tmp_path / "alt.pdb"))
    _fake_msa_graded(monkeypatch, sequence_length=12)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await conservation()
    await task

    low = payload["conserved_below_entropy"]
    scored_conserved = {r["seq"] for r in payload["residues"] if r["entropy"] <= low}
    # Guard the guard: if the cutoff ever swept in the whole chain, or only
    # residues before the alternate site, this test could not fail.
    assert 0 < len(scored_conserved) < payload["residues_scored"]
    assert min(scored_conserved) > 2, scored_conserved
    # ...and it has to reach a residue that carries an alternate, or the
    # one-state assertion below is vacuous.
    assert 11 in scored_conserved, scored_conserved

    indices = server_mod._handles.get("conserved").indices
    drawn = summarise(server_mod._structure, indices)
    assert {r["seq"] for r in drawn["residues"]} == scored_conserved
    # One conformer per site, not both rows of residue 11's CA. Four atoms per
    # residue is the fixture's own shape, so this counts what was kept rather
    # than restating the implementation.
    assert drawn["atom_count"] == 4 * len(scored_conserved)
    assert bool(np.all(conformer_state(server_mod._structure)[indices]))


async def test_conservation_refuses_if_the_structure_changed_while_it_waited(
    wired_bridge, tmp_path, monkeypatch
):
    """Indices computed before a minutes-long await must not be applied after it.

    Nothing in the server locks `_structure`, and the alignment fetch is the
    longest await in the codebase. A `fetch_structure` landing inside it would
    leave the handle indices addressing the previous molecule while
    `_summarise` reports a believable atom count for whatever they now hit.
    """
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb"))
    a3m = f">query\n{'A' * 12}\n>h0\n{'A' * 12}\n"

    async def swap_then_fetch(sequence, cache_dir, **kwargs):
        # Stand in for a concurrent fetch_structure completing mid-await.
        monkeypatch.setattr(
            server_mod,
            "_structure",
            load_structure(
                _peptide_pdb(tmp_path / "other.pdb", n=9).read_text(), "pdb"
            ).array,
        )
        return a3m, "search"

    monkeypatch.setattr(server_mod, "_fetch_msa", swap_then_fetch)

    with pytest.raises(ViewerError, match="structure changed"):
        await conservation()
    assert "conserved" not in server_mod._handles.names()


async def test_conservation_registers_conserved_and_variable_handles(
    wired_bridge, tmp_path, monkeypatch
):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb"))
    _fake_msa(monkeypatch, sequence_length=12)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(2)
    payload = await conservation()
    await task

    assert payload["handles"] == {"conserved": "conserved", "variable": "variable"}
    assert payload["chain"] == "A"
    assert payload["residues_scored"] == 12
    assert payload["msa_depth"] == 21
    assert set(server_mod._handles.names()) >= {"conserved", "variable"}
    # Residue 1 is the invariant column, so it must land in the conserved set.
    conserved = summarise(
        server_mod._structure, server_mod._handles.get("conserved").indices
    )
    assert 1 in [r["seq"] for r in conserved["residues"]]


async def test_conservation_handles_compose_with_other_handles(
    wired_bridge, tmp_path, monkeypatch
):
    """The exit criterion's shape: the conserved part of another set."""
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb"))
    _fake_msa(monkeypatch, sequence_length=12)
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(4)
    await conservation()
    await select("resi 1-4", name="patch")
    result = await combine("intersect", ["patch", "conserved"], "hot")
    await task
    assert result["atom_count"] > 0
    assert all(r["seq"] <= 4 for r in result["residues"])


async def test_conservation_without_a_structure_asks_for_one(bridge, monkeypatch):
    monkeypatch.setattr(server_mod, "_bridge", bridge)
    monkeypatch.setattr(server_mod, "_structure", None)
    monkeypatch.setattr(server_mod, "_structure_error", None)
    with pytest.raises(ViewerError, match="No structure loaded"):
        await conservation()


@contextlib.asynccontextmanager
async def _serving(viewer, **handlers):
    """Answer whatever the viewer is asked, for as long as the block runs.

    serve(n) blocks forever if fewer than n requests arrive, and the exact
    count for a multi-step tool is an implementation detail no test should
    have to predict.
    """
    for action, handler in handlers.items():
        viewer.handlers[action] = handler
    task = viewer.serve(10_000)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# -- colouring by a scalar field -----------------------------------------------


def test_ramp_interpolates_between_the_stops():
    ramp = server_mod._ramp("conservation", 7)
    assert len(ramp) == 7
    assert ramp[0] == "#2c7bb6" and ramp[-1] == "#d7191c"
    assert ramp[3] == "#ffffbf", "the middle stop should land on the middle band"
    assert all(c.startswith("#") and len(c) == 7 for c in ramp)


def test_ramp_of_one_band_is_the_first_stop():
    """Guards a divide-by-zero in the interpolation."""
    assert server_mod._ramp("conservation", 1) == ["#2c7bb6"]


def test_unknown_palette_lists_the_known_ones():
    with pytest.raises(ViewerError, match="Available:"):
        server_mod._ramp("chartreuse", 5)


async def test_color_by_potential_without_a_grid_says_what_to_run(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="Run electrostatics"):
        await color_by_potential(handle="sele", path=str(tmp_path / "absent.dx"))


async def test_color_by_potential_sends_the_grid(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    grid = tmp_path / "p.dx"
    await electrostatics(method="coulombic", spacing=2.0, padding=6.0, path=str(grid))

    sent = {}
    wired_bridge.handlers["color_by_volume"] = lambda args: (
        sent.update(args) or {"components": 1}
    )
    task = wired_bridge.serve(1)
    out = await color_by_potential(handle="sele", path=str(grid), domain=[-5.0, 5.0])
    await task
    assert sent["domain"] == [-5.0, 5.0]
    assert "object 1 class gridpositions" in sent["volume"], "the OpenDX itself is sent"
    assert out["dx_path"] == str(grid)


async def test_color_by_potential_rejects_a_malformed_domain(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    grid = tmp_path / "p.dx"
    await electrostatics(method="coulombic", spacing=2.0, padding=6.0, path=str(grid))
    with pytest.raises(ViewerError, match="min, max"):
        await color_by_potential(handle="sele", path=str(grid), domain=[1.0, 2.0, 3.0])


async def test_color_by_conservation_needs_scores_first(wired_bridge, tmp_path):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb"))
    with pytest.raises(ViewerError, match="call conservation"):
        await color_by_conservation()


async def test_conservation_bands_cover_every_scored_residue(
    wired_bridge, tmp_path, monkeypatch
):
    """The top band must include its own upper edge.

    A half-open last band drops the single most variable residue, which is
    exactly the residue someone colouring by conservation is looking for.
    """
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(wired_bridge, select=nothing, show=nothing, hide=nothing):
        await conservation()
        banded = await color_by_conservation(
            mode="bands", bins=4, representation="spacefill"
        )

    covered = sum(b["residues"] for b in banded["bands"])
    assert covered == 12, f"bands covered {covered} of 12 residues"
    assert banded["most_conserved_first"] is True
    assert banded["bands"][0]["color"] == "#2c7bb6"


async def test_conservation_bands_are_registered_as_handles(
    wired_bridge, tmp_path, monkeypatch
):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(wired_bridge, select=nothing, show=nothing, hide=nothing):
        await conservation()
        banded = await color_by_conservation(
            mode="bands", bins=3, representation="spacefill"
        )
    for band in banded["bands"]:
        assert band["handle"] in server_mod._handles.names()


# -- the continuous conservation gradient --------------------------------------


async def _gradient(
    wired_bridge: Any, tmp_path: Path, monkeypatch: Any, **kwargs: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run conservation then the gradient, capturing what the viewer was sent."""
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)
    sent: dict[str, Any] = {}

    def on_load(args: dict[str, Any]) -> dict[str, Any]:
        sent.update(args)
        return {"loaded": args["name"], "atom_count": 48}

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(
        wired_bridge, load_structure=on_load, select=nothing, show=nothing, hide=nothing
    ):
        await conservation()
        result = await color_by_conservation(mode="gradient", **kwargs)
    return result, sent


async def test_gradient_sends_entropy_in_the_b_factor_column(
    wired_bridge, tmp_path, monkeypatch
):
    """The whole mechanism: Mol* ramps over B-factor, so entropy has to be it.

    Column 0 of the fake alignment is invariant, so residue 1 must arrive at
    the conserved end of the scale and the rest above it.
    """
    result, sent = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert result["reloaded"] is True
    assert sent["format"] == "mmcif"

    array = load_structure(sent["data"], "mmcif", "asymmetric").array
    first = array.b_factor[array.res_id == 1]
    rest = array.b_factor[array.res_id > 1]
    assert first.max() == pytest.approx(0.0, abs=1e-6), "invariant residue is conserved"
    assert rest.min() > first.max(), "every varied residue ranks above it"


async def test_gradient_reloads_as_the_asymmetric_unit(
    wired_bridge, tmp_path, monkeypatch
):
    """These coordinates are already whatever assembly was chosen.

    Letting the viewer expand them a second time would duplicate the molecule,
    which is the bug decision 9 exists to prevent.
    """
    _, sent = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert sent["assembly"] == "asymmetric"
    assert "pdbx_struct_assembly" not in sent["data"]


async def test_gradient_discloses_that_b_factor_now_means_something_else(
    wired_bridge, tmp_path, monkeypatch
):
    """A B-factor column quietly holding conservation would be read as temperature."""
    result, _ = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert "B-factor" in result["note"]
    assert "conservation" in result["note"]


async def test_relative_and_absolute_scales_differ(wired_bridge, tmp_path, monkeypatch):
    """Relative stretches this protein's range; absolute keeps 0-1 comparable."""
    _, relative = await _gradient(wired_bridge, tmp_path, monkeypatch, scale="relative")
    _, absolute = await _gradient(wired_bridge, tmp_path, monkeypatch, scale="absolute")
    rel = load_structure(relative["data"], "mmcif", "asymmetric").array.b_factor
    ab = load_structure(absolute["data"], "mmcif", "asymmetric").array.b_factor
    assert rel.max() == pytest.approx(100.0, abs=1e-6)
    assert ab.max() < rel.max(), "absolute leaves headroom above the observed range"


async def test_handles_survive_the_reload(wired_bridge, tmp_path, monkeypatch):
    """Atom indices stay valid because the atom order does not change."""
    result, _ = await _gradient(wired_bridge, tmp_path, monkeypatch)
    assert result["handles_redisplayed"] >= 2, "conserved and variable at least"
    assert "conserved" in server_mod._handles.names()


async def test_unknown_mode_is_refused(wired_bridge, tmp_path, monkeypatch):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    with pytest.raises(ViewerError, match="Unknown mode"):
        await color_by_conservation(mode="rainbow")


async def test_unknown_scale_is_refused(wired_bridge, tmp_path, monkeypatch):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "pep.pdb", n=12))
    _fake_msa(monkeypatch, sequence_length=12)

    def nothing(args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async with _serving(wired_bridge, select=nothing):
        await conservation()
        with pytest.raises(ViewerError, match="Unknown scale"):
            await color_by_conservation(mode="gradient", scale="logarithmic")


# -- displaying a superposition ------------------------------------------------


def _shifted_pdb(path: Path, offset: float, chain: str = "A", n: int = 6) -> Path:
    """A short peptide, optionally translated along x."""
    lines = []
    serial = 1
    for res in range(1, n + 1):
        for i, (name, elem) in enumerate(
            (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"))
        ):
            xyz = (float(res) * 4 + offset, float(i), 0.0)
            lines.append(_pdb_line(serial, name, "ALA", chain, res, xyz, elem))
            serial += 1
    path.write_text("\n".join(lines) + "\nEND\n")
    return path


class _FakeFetched:
    def __init__(self, path: Path) -> None:
        self.data = path.read_text()
        self.format = "pdb"


class _FakeResult:
    """A superposition result carrying a known transform."""

    def __init__(self, matrix: Any) -> None:
        self.transform = matrix


async def _combine(
    wired_bridge: Any,
    tmp_path: Path,
    matrix: Any,
    suffix: str = "_2",
    chain: str = "A",
) -> tuple[dict[str, Any], dict[str, Any]]:
    mobile = _FakeFetched(_shifted_pdb(tmp_path / "mob.pdb", offset=50.0, chain=chain))
    target = _FakeFetched(_shifted_pdb(tmp_path / "tar.pdb", offset=0.0))
    sent: dict[str, Any] = {}

    def on_load(args: dict[str, Any]) -> dict[str, Any]:
        sent.update(args)
        return {"loaded": args["name"], "atom_count": 48}

    async with _serving(wired_bridge, load_structure=on_load):
        out = await server_mod._display_superposition(
            mobile, target, "mob", "tar", _FakeResult(matrix), suffix
        )
    return out, sent


async def test_the_transform_is_applied_to_the_coordinates(wired_bridge, tmp_path):
    """The bug this fixes: a matrix that nothing ever applied.

    The mobile copy starts 50 A away; a matrix that translates by -50 has to
    bring it exactly onto the target, not merely near it.
    """
    matrix = np.eye(4)
    matrix[0, 3] = -50.0
    await _combine(wired_bridge, tmp_path, matrix)

    array = server_mod._structure
    fixed = array[array.chain_id == "A"]
    moved = array[array.chain_id == "A_2"]
    assert moved.array_length() == fixed.array_length()
    np.testing.assert_allclose(moved.coord, fixed.coord, atol=1e-4)


async def test_an_unapplied_transform_would_leave_them_apart(wired_bridge, tmp_path):
    """Guards the test above: identity must *not* superpose them."""
    await _combine(wired_bridge, tmp_path, np.eye(4))
    array = server_mod._structure
    fixed = array[array.chain_id == "A"]
    moved = array[array.chain_id == "A_2"]
    assert np.abs(moved.coord - fixed.coord).max() == pytest.approx(50.0, abs=1e-4)


async def test_colliding_chain_ids_are_renamed_and_reported(wired_bridge, tmp_path):
    out, _ = await _combine(wired_bridge, tmp_path, np.eye(4))
    assert out["renamed_chains"] == {"A": "A_2"}
    assert out["mobile_chains_shown"] == ["A_2"]
    assert out["target_chains_shown"] == ["A"]


async def test_a_non_colliding_chain_keeps_its_name(wired_bridge, tmp_path):
    out, _ = await _combine(wired_bridge, tmp_path, np.eye(4), chain="Z")
    assert out["renamed_chains"] == {}
    assert out["mobile_chains_shown"] == ["Z"]


async def test_the_pair_becomes_one_selectable_structure(wired_bridge, tmp_path):
    """Both halves have to be addressable, or the picture is all you get."""
    await _combine(wired_bridge, tmp_path, np.eye(4))
    both = select_mask("chain A or chain A_2", server_mod._structure)
    assert both.sum() == server_mod._structure.array_length()


async def test_atom_ids_match_what_the_viewer_will_parse(wired_bridge, tmp_path):
    """The transport reads atom.id, and biotite renumbers on write.

    Two structures concatenated both start at id 1, so without renumbering a
    handle would name one atom in each half.
    """
    _, sent = await _combine(wired_bridge, tmp_path, np.eye(4))
    ours = np.asarray(server_mod._structure.atom_id)
    assert len(np.unique(ours)) == len(ours), "ids must be unique across the pair"
    theirs = load_structure(sent["data"], "mmcif", "asymmetric").array.atom_id
    np.testing.assert_array_equal(ours, theirs)


async def test_the_combined_structure_is_sent_as_the_asymmetric_unit(
    wired_bridge, tmp_path
):
    _, sent = await _combine(wired_bridge, tmp_path, np.eye(4))
    assert sent["assembly"] == "asymmetric"


async def test_stale_handles_do_not_survive_a_new_structure(wired_bridge, tmp_path):
    await _load(wired_bridge, _peptide_pdb(tmp_path / "old.pdb", n=6))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    await select("all", name="stale")
    await task
    assert "stale" in server_mod._handles.names()

    await _combine(wired_bridge, tmp_path, np.eye(4))
    assert server_mod._handles.names() == [], "indices into the old structure"


def test_renumbering_makes_ids_unique_and_one_based():
    atoms = [
        Atom(
            [0.0, 0.0, float(i)],
            chain_id="A",
            res_id=1,
            ins_code="",
            res_name="ALA",
            atom_name="CA",
            element="C",
            hetero=False,
            b_factor=1.0,
            occupancy=1.0,
            atom_id=7,  # every atom claiming the same id
        )
        for i in range(5)
    ]
    array = atom_array(atoms)
    server_mod._renumber_for_viewer(array)
    assert array.atom_id.tolist() == [1, 2, 3, 4, 5]


# -- background and opacity ----------------------------------------------------


async def test_background_refuses_a_call_that_would_change_nothing():
    """Both arguments omitted is a no-op, and a no-op reporting success is a lie."""
    with pytest.raises(
        ViewerError, match="at least one of color, transparent, gradient, image or skybox"
    ):
        await background()


async def test_background_sends_only_what_was_asked_for(wired_bridge):
    """An unmentioned argument must not travel as an explicit default.

    Sending `transparent: false` on a colour-only call would silently undo a
    transparent background someone set a moment earlier.
    """
    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"background": "#ff0000", "transparent": False}
    )
    task = wired_bridge.serve(1)
    await background(color="#ff0000")
    await task

    assert sent == {"color": "#ff0000"}


async def test_background_passes_transparency_through(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"screenshot_transparent": True}
    )
    task = wired_bridge.serve(1)
    out = await background(transparent=True)
    await task

    assert sent == {"transparent": True}
    # The reply is the viewer's read-back, returned to the caller unaltered.
    assert out["screenshot_transparent"] is True


async def test_opacity_reaches_the_viewer(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["opacity"] = lambda args: (
        sent.update(args) or {"representations": 1}
    )
    task = wired_bridge.serve(1)
    await opacity(0.3, name="surf")
    await task

    assert sent == {"name": "surf", "opacity": 0.3}


async def test_show_carries_opacity_alongside_the_representation(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    sent: dict[str, Any] = {}
    wired_bridge.handlers["show"] = lambda args: (
        sent.update(args) or {"name": "sele", "representation": "molecular-surface"}
    )
    task = wired_bridge.serve(1)
    await show(representation="molecular-surface", selection="all", opacity=0.25)
    await task

    assert sent["opacity"] == 0.25
    assert sent["representation"] == "molecular-surface"


async def test_show_omits_opacity_when_it_was_not_asked_for(wired_bridge, tmp_path):
    """So a plain show() cannot quietly pin alpha to a default of its own."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    sent: dict[str, Any] = {}
    wired_bridge.handlers["show"] = lambda args: (
        sent.update(args) or {"name": "sele", "representation": "cartoon"}
    )
    task = wired_bridge.serve(1)
    await show(selection="all")
    await task

    assert "opacity" not in sent


async def test_lighting_sends_the_rig_and_omits_unmentioned_knobs(wired_bridge):
    """An unmentioned knob must not travel as a default and overwrite a setting."""
    sent: dict[str, Any] = {}
    wired_bridge.handlers["lighting"] = lambda args: (
        sent.update(args) or {"rig": args["rig"], "lights": 3}
    )
    task = wired_bridge.serve(1)
    out = await lighting(rig="three-point")
    await task

    assert sent == {"rig": "three-point"}
    assert out["lights"] == 3


async def test_lighting_passes_its_knobs_through(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["lighting"] = lambda args: sent.update(args) or {"rig": "ring"}
    task = wired_bridge.serve(1)
    await lighting(rig="ring", intensity=1.5, ambient=0.2, exposure=1.2)
    await task

    assert sent == {"rig": "ring", "intensity": 1.5, "ambient": 0.2, "exposure": 1.2}


# -- effects, shading and gradients --------------------------------------------


async def test_effects_refuses_a_call_that_would_change_nothing():
    with pytest.raises(ViewerError, match="at least one effect"):
        await effects()


async def test_effects_sends_only_what_was_asked_for(wired_bridge):
    """Effects compose across calls, so an unmentioned one must not travel.

    Sending `bloom: false` on an outline-only call would switch off an effect
    the caller never mentioned.
    """
    sent: dict[str, Any] = {}
    wired_bridge.handlers["effects"] = lambda args: sent.update(args) or {"outline": True}
    task = wired_bridge.serve(1)
    await effects(outline=True)
    await task

    assert sent == {"outline": True}


async def test_effects_carries_a_false_through_rather_than_dropping_it(wired_bridge):
    """False is a value here, not an omission — the guard against `if value:`."""
    sent: dict[str, Any] = {}
    wired_bridge.handlers["effects"] = lambda args: sent.update(args) or {"bloom": False}
    task = wired_bridge.serve(1)
    await effects(bloom=False)
    await task

    assert sent == {"bloom": False}


async def test_shading_passes_the_style_and_handle(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["shading"] = lambda args: (
        sent.update(args) or {"style": args["style"], "representations": 1}
    )
    task = wired_bridge.serve(1)
    await shading("xray-inverted", name="surf")
    await task

    assert sent == {"name": "surf", "style": "xray-inverted"}


async def test_shading_carries_cel_steps_when_given(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["shading"] = lambda args: sent.update(args) or {"style": "cel"}
    task = wired_bridge.serve(1)
    await shading("cel", cel_steps=4)
    await task

    assert sent["cel_steps"] == 4


async def test_background_gradient_colours_need_a_gradient_to_sit_on():
    with pytest.raises(ViewerError, match="Pass gradient="):
        await background(gradient_from="#ff0000")


async def test_background_sends_the_gradient_and_its_stops(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"gradient": "radialGradient"}
    )
    task = wired_bridge.serve(1)
    out = await background(
        gradient="radial", gradient_from="#000000", gradient_to="#ffffff"
    )
    await task

    assert sent == {
        "gradient": "radial",
        "gradient_from": "#000000",
        "gradient_to": "#ffffff",
    }
    # Mol*'s own variant name comes back, so a mapping slip is visible here.
    assert out["gradient"] == "radialGradient"


async def test_material_sends_the_finish_and_omits_unmentioned_knobs(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["material"] = lambda args: (
        sent.update(args) or {"finish": args["finish"], "representations": 1}
    )
    task = wired_bridge.serve(1)
    await material(finish="chrome", name="surf")
    await task

    assert sent == {"name": "surf", "finish": "chrome"}


async def test_material_carries_an_emissive_of_zero_rather_than_dropping_it(wired_bridge):
    """0.0 is a value — it is how you stop something glowing — not an omission."""
    sent: dict[str, Any] = {}
    wired_bridge.handlers["material"] = lambda args: (
        sent.update(args) or {"finish": "matte"}
    )
    task = wired_bridge.serve(1)
    await material(emissive=0.0)
    await task

    assert sent["emissive"] == 0.0


# -- path tracing --------------------------------------------------------------


async def test_path_trace_sends_quality_and_reports_samples(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["path_trace"] = lambda args: (
        sent.update(args) or {"enabled": True, "quality": args["quality"], "samples": 128}
    )
    task = wired_bridge.serve(1)
    out = await path_trace(quality="high")
    await task

    assert sent == {"enabled": True, "quality": "high"}
    assert out["samples"] == 128


async def test_screenshot_waits_longer_once_tracing_is_on(wired_bridge, tmp_path):
    """The timeout follows the *canvas*, not the argument.

    A traced capture runs the tracer inside the request and takes seconds to
    minutes. Leaving the ordinary timeout in place would abort work that was
    going to succeed and report it as a stalled viewer.
    """
    wired_bridge.handlers["path_trace"] = lambda args: {"enabled": True}
    wired_bridge.handlers["screenshot"] = lambda args: {
        "data_uri": f"data:image/png;base64,{PNG_B64}",
        "traced_ms": 4100,
    }

    timeouts: list[float] = []
    original = server_mod.get_bridge().request

    async def record(action, args=None, timeout=60):
        timeouts.append(timeout)
        return await original(action, args, timeout)

    task = wired_bridge.serve(2)
    await path_trace(enabled=True)
    server_mod.get_bridge().request = record  # type: ignore[method-assign]
    try:
        result = await screenshot(path=str(tmp_path / "traced.png"))
    finally:
        server_mod.get_bridge().request = original  # type: ignore[method-assign]
    await task

    assert timeouts == [server_mod._TRACED_SCREENSHOT_TIMEOUT]
    # The cost is reported back, since it is what decides whether to ask for more.
    assert any("path traced in 4.1s" in str(item) for item in result)


async def test_a_refused_enable_does_not_leave_screenshots_waiting(wired_bridge):
    """The viewer's answer wins over the request.

    If the canvas refuses to path trace, every later screenshot is an ordinary
    one and must not sit on the ten-minute timeout.
    """
    wired_bridge.handlers["path_trace"] = lambda args: {"enabled": False}
    task = wired_bridge.serve(1)
    await path_trace(enabled=True)
    await task

    assert server_mod._path_tracing is False


# -- snapshot ------------------------------------------------------------------


def _snapshot_handler(sent: dict[str, Any], width: int, height: int):
    """A viewer that returns a real PNG of the size it was asked for."""

    def handle(args):
        sent.update(args)
        buffer = io.BytesIO()
        PILImage.new("RGBA", (width, height), (10, 20, 30, 255)).save(
            buffer, format="PNG"
        )
        encoded = base64.b64encode(buffer.getvalue()).decode()
        return {
            "data_uri": f"data:image/png;base64,{encoded}",
            "requested_width": args["width"],
            "cropped": args.get("crop", False),
        }

    return handle


@pytest.mark.parametrize(
    ("column", "dpi", "pixels"),
    [
        ("single", 300, 1051),  # 89 mm
        ("single", 600, 2102),
        ("double", 300, 2161),  # 183 mm
        ("double", 600, 4323),
    ],
)
async def test_column_and_dpi_decide_the_pixel_count(
    wired_bridge, tmp_path, column, dpi, pixels
):
    """The arithmetic a model must never be asked to do itself.

    A "600 dpi" figure that is 900 pixels wide is a wrong answer that looks
    entirely right, and nothing downstream would catch it.
    """
    sent: dict[str, Any] = {}
    wired_bridge.handlers["snapshot"] = _snapshot_handler(sent, pixels, 800)
    task = wired_bridge.serve(1)
    out = await snapshot(str(tmp_path / "fig"), column=column, dpi=dpi)
    await task

    assert sent["width"] == pixels
    assert out["pixels"] == [pixels, 800]


async def test_width_mm_is_the_escape_hatch(wired_bridge, tmp_path):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["snapshot"] = _snapshot_handler(sent, 1181, 900)
    task = wired_bridge.serve(1)
    await snapshot(str(tmp_path / "fig"), width_mm=100.0, dpi=300)
    await task

    assert sent["width"] == 1181  # 100 mm at 300 dpi


# -- what a capture is allowed to cost -----------------------------------------


def _spy_on_budgets(monkeypatch) -> dict[str, list[float]]:
    """Record the timeout every request is actually given, per action.

    The timeout never goes on the wire and no reply reflects it, so a correct
    `_capture_timeout` wired to nothing would satisfy an assertion about the
    helper alone and leave the behaviour exactly as it was.

    Every call is kept rather than the last one. A turntable orbits once per
    frame and once more to close the loop, so keeping only the last would let
    that final call paper over a wrong budget on all the others — which it
    did, until a mutation that should have failed this suite did not.
    """
    budgets: dict[str, list[float]] = {}
    bridge = server_mod._bridge
    assert bridge is not None  # the wired_bridge fixture installed it
    original = bridge.request

    async def spy(action: str, args: Any = None, timeout: float = 60.0) -> Any:
        budgets.setdefault(action, []).append(timeout)
        return await original(action, args, timeout)

    monkeypatch.setattr(bridge, "request", spy)
    return budgets


def test_a_small_capture_keeps_a_flat_budget():
    """Below a megapixel, the render is not where the time goes."""
    assert server_mod._capture_timeout(1200) == server_mod._CAPTURE_TIMEOUT_FLOOR


def test_a_bigger_capture_gets_a_bigger_budget():
    assert server_mod._capture_timeout(6000) > server_mod._capture_timeout(4323)
    assert server_mod._capture_timeout(4323) > server_mod._capture_timeout(1200)


def test_the_journal_figure_gets_more_than_the_budget_that_failed_it():
    """The capture a fixed number was replaced for.

    183 mm at 600 dpi is 4323 px, and against the old flat 300 s it timed out
    in CI on one run and finished on the next, from the same commit. Measured
    at 105 s under SwiftShader on the development machine, and a CI runner is
    about three times slower again, so the budget has to clear ~315 s by
    enough that a slow runner is not a coin toss.
    """
    assert server_mod._capture_timeout(4323) > 900


@pytest.mark.parametrize("width", [400, 3000, 3163, 4323, 8000])
def test_path_tracing_is_never_given_less_time_than_the_same_capture_without_it(
    width,
):
    """Strictly more expensive work must not get a strictly smaller budget.

    The traced budget was a flat 600 s taken *instead of* the size-derived one,
    and above 3163 px the size-derived one is larger — so a journal figure got
    1121 s with the tracer off and 600 s with it on, at exactly the sizes this
    budget exists for.
    """
    assert server_mod._capture_timeout(width, traced=True) >= server_mod._capture_timeout(
        width
    )
    assert (
        server_mod._capture_timeout(width, traced=True)
        >= server_mod._TRACED_SCREENSHOT_TIMEOUT
    )


async def test_a_frame_sequence_refuses_a_width_beyond_what_can_be_captured(
    wired_bridge, tmp_path
):
    """`snapshot` is guarded by _snapshot_pixels; this path never went through it.

    It mattered little against a flat 300 s. Against a size-derived budget a
    mistyped width buys hours per frame instead of failing in minutes:
    turntable(width=20000) would allow 6.7 h for each one.

    Handlers are registered so that removing the guard fails this test rather
    than hanging it: without them the capture waits out its own 24,000 s budget
    and takes the suite with it.
    """
    calls: list[tuple[str, dict[str, Any]]] = []
    _frame_handlers(wired_bridge, calls)
    task = wired_bridge.serve(40)
    try:
        with pytest.raises(ViewerError, match="megapixels"):
            await turntable(str(tmp_path / "turn"), frames=2, width=20000)
        assert calls == [], "refused before anything reached the viewer"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_the_budget_that_reaches_the_bridge_follows_the_size_asked_for(
    wired_bridge, tmp_path, monkeypatch
):
    budgets = _spy_on_budgets(monkeypatch)
    sent: dict[str, Any] = {}
    wired_bridge.handlers["snapshot"] = _snapshot_handler(sent, 4323, 1860)
    task = wired_bridge.serve(1)
    await snapshot(str(tmp_path / "fig"), column="double", dpi=600)
    await task

    assert sent["width"] == 4323
    assert budgets["snapshot"] == [server_mod._capture_timeout(4323)]
    assert budgets["snapshot"][0] > server_mod._CAPTURE_TIMEOUT_FLOOR


async def test_positioning_the_scene_is_not_charged_as_a_capture(
    wired_bridge, tmp_path, monkeypatch
):
    """A camera move borrowed the capture's budget when there was only one.

    Keeping them apart is the point: a figure-sized capture is allowed minutes
    now, and an orbit that never answers should not be.

    Captured wide enough to clear the floor deliberately. At a small width the
    two budgets are both 300 s and the assertions below hold whichever way the
    call sites are wired, which is a test that cannot fail.
    """
    budgets = _spy_on_budgets(monkeypatch)
    calls: list[tuple[str, dict[str, Any]]] = []
    _frame_handlers(wired_bridge, calls)
    task = wired_bridge.serve(40)
    await turntable(str(tmp_path / "turn"), frames=2, width=3000)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert set(budgets["orbit"]) == {server_mod._VIEWER_ACTION_TIMEOUT}
    assert set(budgets["snapshot"]) == {server_mod._capture_timeout(3000)}
    assert set(budgets["snapshot"]) != set(budgets["orbit"])


async def test_snapshot_needs_exactly_one_width():
    with pytest.raises(ViewerError, match="exactly one of column"):
        await snapshot("/tmp/x.png")
    with pytest.raises(ViewerError, match="exactly one of column"):
        await snapshot("/tmp/x.png", column="single", width_mm=100.0)


async def test_snapshot_refuses_a_size_beyond_what_can_be_captured():
    with pytest.raises(ViewerError, match="megapixels"):
        await snapshot("/tmp/x.png", column="double", dpi=4800)


async def test_jpeg_and_transparency_are_refused_together():
    """JPEG has no alpha channel; flattening silently would lose the request."""
    with pytest.raises(ViewerError, match="JPEG has no alpha channel"):
        await snapshot("/tmp/x.jpg", column="single", format="jpeg", transparent=True)


async def test_unknown_format_is_refused():
    with pytest.raises(ViewerError, match="Unknown format 'bmp'"):
        await snapshot("/tmp/x", column="single", format="bmp")


@pytest.mark.parametrize(
    ("fmt", "suffix", "mode"),
    [("png", ".png", "RGBA"), ("tiff", ".tiff", "RGBA"), ("jpeg", ".jpg", "RGB")],
)
async def test_every_format_is_written_with_its_dpi(
    wired_bridge, tmp_path, fmt, suffix, mode
):
    """The DPI has to survive in the *file*, not just in the reply.

    A figure that is 600 dpi only in the prose around it is exactly the failure
    this tool exists to prevent, so the file is reopened and asked.
    """
    sent: dict[str, Any] = {}
    wired_bridge.handlers["snapshot"] = _snapshot_handler(sent, 400, 300)
    task = wired_bridge.serve(1)
    out = await snapshot(str(tmp_path / "fig"), column="single", dpi=600, format=fmt)
    await task

    written = Path(out["path"])
    assert written.suffix == suffix
    with PILImage.open(written) as reopened:
        assert reopened.mode == mode
        assert reopened.info["dpi"][0] == pytest.approx(600, rel=1e-3)
    assert out["dpi"] == 600.0
    assert out["bytes"] == written.stat().st_size


async def test_the_reported_width_follows_the_pixels_that_came_back(
    wired_bridge, tmp_path
):
    """Cropping trims the frame, so the requested millimetres stop being true.

    Repeating the request back would state a physical width the file does not
    have.
    """
    sent: dict[str, Any] = {}
    # Asked for 2161 px (183 mm at 300 dpi); a crop returns fewer.
    wired_bridge.handlers["snapshot"] = _snapshot_handler(sent, 1000, 800)
    task = wired_bridge.serve(1)
    out = await snapshot(str(tmp_path / "fig"), column="double", dpi=300, crop=True)
    await task

    assert sent["crop"] is True
    assert out["requested_width_mm"] == 183.0
    assert out["width_mm"] == pytest.approx(1000 / 300 * 25.4, abs=0.01)
    assert out["width_mm"] < out["requested_width_mm"]


# -- presets -------------------------------------------------------------------


def _record(viewer, calls: list[tuple[str, dict[str, Any]]]) -> None:
    """Answer every display action, remembering the order they arrived in."""
    for action in (
        "background",
        "lighting",
        "effects",
        "shading",
        "material",
        "opacity",
        "show",
        "label",
        "focus",
        "select",
        "hide",
        "reset_view",
    ):

        def handle(args, action=action):
            calls.append((action, args))
            return {"ok": True, "name": args.get("name", ""), "representations": 1}

        viewer.handlers[action] = handle


async def test_unknown_preset_is_refused_with_the_real_list(wired_bridge):
    with pytest.raises(ViewerError, match=r"Unknown preset 'noir'.*active-site"):
        await preset("noir")


async def test_a_preset_reports_the_calls_it_made(wired_bridge, tmp_path):
    """A preset is a composition, not a black box.

    Listing the calls is what makes it adjustable afterwards rather than an
    all-or-nothing style someone has to accept whole.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    calls: list[tuple[str, dict[str, Any]]] = []
    _record(wired_bridge, calls)
    task = wired_bridge.serve(20)
    out = await preset("publication-cartoon")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert out["preset"] == "publication-cartoon"
    assert [action for action, _ in calls] == [
        "background",
        "lighting",
        "effects",
        "shading",
        "material",
    ]
    assert len(out["steps"]) == len(calls)


async def test_ghost_surface_draws_under_its_own_handle(wired_bridge, tmp_path):
    """The scoping this preset exists to get right.

    Showing a surface under the *same* handle rebuilds that component, so the
    cartoon meant to be visible inside the ghost would silently vanish. The
    surface has to be its own component over the same atoms.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    calls: list[tuple[str, dict[str, Any]]] = []
    _record(wired_bridge, calls)

    task = wired_bridge.serve(20)
    await select("all", name="site")
    out = await preset("ghost-surface", handle="site")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    shown = [args for action, args in calls if action == "show"]
    assert shown, "the preset never drew a surface"
    assert shown[0]["name"] == "site_ghost"
    assert shown[0]["name"] != "site"
    assert shown[0]["representation"] == "molecular-surface"
    assert shown[0]["opacity"] == 0.25
    # And the original handle still exists, pointing at the same atoms.
    assert out["applied_to"] == "site"


async def test_ghost_surface_over_the_whole_scene_leaves_the_solvent_out(
    wired_bridge, tmp_path
):
    """A molecular surface is per atom, so an isolated water gets its own blob.

    1UBQ drew fifty-eight of them — detached spheres floating around the fold,
    14% of everything on screen, and the first thing anyone looking at the view
    asked about. Ligands and ions stay: they are part of the molecule's shape.
    """
    await _load(wired_bridge, _protein_and_water_pdb(tmp_path / "wet.pdb"))
    _record(wired_bridge, [])
    task = wired_bridge.serve(20)
    try:
        await preset("ghost-surface")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    ghost = server_mod._handles.get("auto_ghost")
    array = server_mod._structure
    assert len(ghost) > 0
    assert len(ghost) < array.array_length(), "the whole structure was wrapped"
    assert "HOH" not in {str(r) for r in array[ghost.indices].res_name}


async def test_ghost_surface_refuses_a_structure_that_is_only_solvent(
    wired_bridge, tmp_path
):
    await _load(wired_bridge, _water_only_pdb(tmp_path / "wet.pdb"))
    with pytest.raises(ViewerError, match="nothing but solvent"):
        await preset("ghost-surface")


async def test_ghost_surface_covers_the_same_atoms_as_its_source(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    _record(wired_bridge, [])
    task = wired_bridge.serve(20)
    await select("name CA", name="site")
    await preset("ghost-surface", handle="site")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    source = server_mod._handles.get("site")
    ghost = server_mod._handles.get("site_ghost")
    assert ghost.indices.tolist() == source.indices.tolist()


async def test_ghost_surface_refuses_a_handle_that_does_not_exist(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="No selection named 'nope'"):
        await preset("ghost-surface", handle="nope")


async def test_active_site_insists_on_being_told_which_site(wired_bridge, tmp_path):
    """A site preset applied to everything is not a site preset."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="needs a handle"):
        await preset("active-site")


# -- the style presets from docs/views.md §5.1 ---------------------------------
#
# The six borrowed from MCPymol. Four of them decide what is drawn rather than
# only restyling it, which is the part with a way to go wrong: `auto` belongs to
# the viewer's load preset, so a view has to hide it and take the scene over, and
# two views in a row have to replace each other rather than stack.

_DRAWING_PRESETS = ["textbook", "bfactor", "putty", "hydrophobic-surface", "pointillist"]


async def _preset_calls(
    wired_bridge, tmp_path, name: str, handle: str | None = None
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Apply a preset against a recording viewer and hand back what it sent."""
    calls: list[tuple[str, dict[str, Any]]] = []
    _record(wired_bridge, calls)
    task = wired_bridge.serve(40)
    try:
        out = await preset(name, handle=handle)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return out, calls


@pytest.mark.parametrize("name", _DRAWING_PRESETS)
async def test_a_drawing_preset_takes_the_scene_over(wired_bridge, tmp_path, name):
    """Hide what the viewer drew, then draw under one shared handle.

    Without the hide, the new representation is coincident with the load
    preset's own and the picture does not visibly change — which is exactly how
    the probe that planned this work convinced itself five separate primitives
    were broken.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    out, calls = await _preset_calls(wired_bridge, tmp_path, name)

    hidden = [args for action, args in calls if action == "hide"]
    assert hidden and hidden[0]["name"] == "auto", "the load preset's scene stayed up"

    shown = [args for action, args in calls if action == "show"]
    assert shown, f"{name} never drew anything"
    assert shown[0]["name"] == "auto_view"
    assert out["applied_to"] == "auto"
    assert [action for action, _ in calls].index("hide") < [
        action for action, _ in calls
    ].index("show"), "drew before hiding, so the two are coincident"


@pytest.mark.parametrize("name", _DRAWING_PRESETS)
async def test_a_drawing_preset_reports_every_call_it_made(wired_bridge, tmp_path, name):
    """The invariant the first preset established, extended to the new ones.

    `steps` is what makes a preset adjustable rather than an opaque style, so a
    call that does not appear there is one nobody can undo. Paired by tool name
    in order rather than counted, because a count matches just as happily when a
    step describes the wrong call as the right one — and because `select` is
    resolved in Python, registering the handle that show() then builds the
    component from, so it is a step with no viewer call of its own.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    out, calls = await _preset_calls(wired_bridge, tmp_path, name)

    reported = [step.split("(")[0] for step in out["steps"]]
    assert [tool for tool in reported if tool != "select"] == [
        action for action, _ in calls
    ]


@pytest.mark.parametrize("name", _DRAWING_PRESETS)
async def test_a_reported_step_carries_the_arguments_that_were_sent(
    wired_bridge, tmp_path, name
):
    """A step you cannot replay is not the call it claims to be.

    Three of these were written out by hand beside the call they described, and
    drifted from it: `effects(...)` dropped a toggle it had just set, so
    replaying the reported steps produced a different picture than the preset
    did. Both now come from one dict, and this is what says so.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    out, calls = await _preset_calls(wired_bridge, tmp_path, name)

    steps = [step for step in out["steps"] if not step.startswith("select(")]
    for step, (action, args) in zip(steps, calls, strict=True):
        assert step.startswith(f"{action}("), f"{step!r} does not describe {action}"
        for key, value in args.items():
            # `expression` is molscript the viewer needs and a caller never
            # writes; `limit` shapes the reply rather than the picture.
            if key in ("expression", "limit"):
                continue
            # show() takes `handle` and sends it as `name`; it is the only
            # argument whose tool spelling differs from its wire spelling.
            name = "handle" if (action == "show" and key == "name") else key
            rendered = (
                f'{name}="{value}"' if isinstance(value, str) else f"{name}={value}"
            )
            assert rendered in step, f"{step!r} omits {rendered}"


async def test_a_second_view_replaces_the_first_rather_than_stacking(
    wired_bridge, tmp_path
):
    """Both draw through the same handle, so Mol* rebuilds one component.

    Under two handles the tube and the surface would both be on screen at once,
    and a switcher built on this would accumulate a view per click.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    _, first = await _preset_calls(wired_bridge, tmp_path, "putty")
    _, second = await _preset_calls(wired_bridge, tmp_path, "hydrophobic-surface")

    drew = [args["name"] for action, args in first + second if action == "show"]
    assert drew == ["auto_view", "auto_view"]


async def test_a_view_on_a_handle_redraws_that_handle_and_leaves_the_scene(
    wired_bridge, tmp_path
):
    """A named target says what to restyle, so there is nothing to take over."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    await select("all", name="site")
    await task

    out, calls = await _preset_calls(wired_bridge, tmp_path, "putty", handle="site")

    assert not [args for action, args in calls if action == "hide"]
    shown = [args for action, args in calls if action == "show"]
    assert shown and shown[0]["name"] == "site"
    assert out["applied_to"] == "site"


@pytest.mark.parametrize("name", ["textbook", "illustrative", "hydrophobic-surface"])
async def test_a_preset_states_every_effect_rather_than_inheriting_one(
    wired_bridge, tmp_path, name
):
    """`cinematic` is the only preset that turns depth of field on.

    effects() leaves anything omitted exactly as it was — right for a tool
    composing calls, wrong for a recipe declaring a whole look. These three
    never mentioned depth of field, so after cinematic the flat outlined
    diagram came out with a shallow-focus blur and reported success. A preset
    states all six toggles or it does not control its own picture.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    await _preset_calls(wired_bridge, tmp_path, "cinematic")
    _, calls = await _preset_calls(wired_bridge, tmp_path, name)

    applied = [args for action, args in calls if action == "effects"]
    assert applied, f"{name} set no effects at all"
    assert applied[0].get("depth_of_field") is False, (
        f"{name} left depth of field wherever cinematic put it"
    )
    assert set(server_mod._PRESET_EFFECTS) <= set(applied[0])


async def test_a_refused_view_leaves_the_scene_alone(wired_bridge, tmp_path):
    """A refusal that blanks the viewer first is worse than the drawing it refused.

    The scene handle was hidden and rebuilt before anything checked the
    selection had matched, so refusing left an empty viewer, a zero-atom
    `auto_view` in the handle table, and an error mentioning none of it — and
    the stale handle then captured every later styling preset.
    """
    await _load(wired_bridge, _water_only_pdb(tmp_path / "wet.pdb"))
    calls: list[tuple[str, dict[str, Any]]] = []
    _record(wired_bridge, calls)
    task = wired_bridge.serve(20)
    try:
        with pytest.raises(ViewerError, match="scene is untouched"):
            await preset("textbook")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls == [], "the viewer was touched before the refusal"
    assert "auto_view" not in server_mod._handles.names()


async def test_styling_after_a_view_follows_the_scene_it_drew(wired_bridge, tmp_path):
    """The silent-success trap this repo keeps meeting, in a new costume.

    Once a view has hidden `auto`, styling `auto` still succeeds and still
    changes nothing anyone can see. Style what is on screen.
    """
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    await _preset_calls(wired_bridge, tmp_path, "putty")
    _, calls = await _preset_calls(wired_bridge, tmp_path, "cinematic")

    styled = [args["name"] for action, args in calls if action in ("shading", "material")]
    assert styled == ["auto_view", "auto_view"]


async def test_textbook_composes_illustrative_rather_than_repeating_it(
    wired_bridge, tmp_path
):
    """One styling recipe, called twice, so the two cannot drift apart."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    _, styled = await _preset_calls(wired_bridge, tmp_path, "illustrative")
    _, drawn = await _preset_calls(wired_bridge, tmp_path, "textbook")

    want = [action for action, _ in styled]
    got = [action for action, _ in drawn]
    starts = got.index(want[0])
    assert got[starts : starts + len(want)] == want


async def test_a_view_refuses_an_empty_handle_rather_than_drawing_nothing(
    wired_bridge, tmp_path
):
    """Nothing to draw is a refusal, not a blank canvas reported as success."""
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    wired_bridge.handlers["select"] = lambda args: {}
    task = wired_bridge.serve(1)
    await select("resn ZZZ", name="nothing")
    await task

    with pytest.raises(ViewerError, match=r"is empty.*report success"):
        await preset("putty", handle="nothing")


async def test_a_view_refuses_a_handle_that_does_not_exist(wired_bridge, tmp_path):
    await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    with pytest.raises(ViewerError, match="No selection named 'nope'"):
        await preset("putty", handle="nope")


async def test_pointillist_leaves_the_solvent_out(wired_bridge, tmp_path):
    """Waters are most of the atoms in a crystal structure and none of the shape."""
    await _load(wired_bridge, _protein_and_water_pdb(tmp_path / "wet.pdb"))
    await _preset_calls(wired_bridge, tmp_path, "pointillist")

    scene = server_mod._handles.get("auto_view")
    array = server_mod._structure
    assert len(scene) > 0
    assert "HOH" not in {str(r) for r in array[scene.indices].res_name}


async def test_capabilities_reports_the_presets(wired_bridge):
    wired_bridge.handlers["capabilities"] = lambda args: {"representations": ["cartoon"]}
    task = wired_bridge.serve(1)
    out = await capabilities()
    await task

    assert "ghost-surface" in out["presets"]
    assert out["presets"] == sorted(out["presets"])


# -- image and skybox backgrounds ----------------------------------------------


def _write_image(path: Path, colour: tuple[int, int, int], size=(16, 16)) -> Path:
    PILImage.new("RGB", size, colour).save(path)
    return path


async def test_a_local_image_travels_inline(wired_bridge, tmp_path):
    """Read and encoded here, because the viewer has no filesystem.

    Sending the path would leave Mol* fetching a URL the browser cannot see,
    which draws nothing and reports no error.
    """
    source = _write_image(tmp_path / "bg.png", (10, 20, 30))
    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"gradient": "image"}
    )
    task = wired_bridge.serve(1)
    await background(image=str(source))
    await task

    assert sent["image"].startswith("data:image/png;base64,")
    assert str(source) not in sent["image"]


async def test_a_remote_url_is_passed_through_untouched(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"gradient": "image"}
    )
    task = wired_bridge.serve(1)
    await background(image="https://example.org/sky.jpg")
    await task

    assert sent["image"] == "https://example.org/sky.jpg"


async def test_a_file_that_is_not_an_image_is_refused(tmp_path):
    """Mol* draws nothing for a URL it cannot load, and says nothing about it."""
    fake = tmp_path / "notreally.png"
    fake.write_text("this is not a PNG")
    with pytest.raises(ViewerError, match="not an image Pillow can read"):
        await background(image=str(fake))


async def test_a_missing_image_is_refused(tmp_path):
    with pytest.raises(ViewerError, match="No image at"):
        await background(image=str(tmp_path / "absent.png"))


async def test_an_oversized_image_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "_MAX_BACKGROUND_IMAGE_BYTES", 128)
    source = _write_image(tmp_path / "big.png", (1, 2, 3), size=(256, 256))
    with pytest.raises(ViewerError, match="Downscale it"):
        await background(image=str(source))


async def test_a_skybox_collects_all_six_faces(wired_bridge, tmp_path):
    folder = tmp_path / "sky"
    folder.mkdir()
    for index, face in enumerate(("nx", "ny", "nz", "px", "py", "pz")):
        _write_image(folder / f"{face}.png", (index * 10, 0, 0))

    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"gradient": "skybox"}
    )
    task = wired_bridge.serve(1)
    await background(skybox=str(folder))
    await task

    assert sorted(sent["skybox"]) == ["nx", "ny", "nz", "px", "py", "pz"]
    assert all(uri.startswith("data:image/") for uri in sent["skybox"].values())
    # Six distinct faces, not the same one six times.
    assert len(set(sent["skybox"].values())) == 6


async def test_a_skybox_missing_faces_says_which(tmp_path):
    """A cube map with five faces is not a cube map, and the gap is invisible."""
    folder = tmp_path / "sky"
    folder.mkdir()
    for face in ("nx", "ny", "nz", "px"):
        _write_image(folder / f"{face}.png", (1, 1, 1))

    with pytest.raises(ViewerError, match="missing skybox faces: py, pz"):
        await background(skybox=str(folder))


async def test_a_skybox_accepts_mixed_suffixes(wired_bridge, tmp_path):
    folder = tmp_path / "sky"
    folder.mkdir()
    for face in ("nx", "ny", "nz"):
        _write_image(folder / f"{face}.png", (2, 2, 2))
    for face in ("px", "py", "pz"):
        _write_image(folder / f"{face}.jpg", (3, 3, 3))

    sent: dict[str, Any] = {}
    wired_bridge.handlers["background"] = lambda args: (
        sent.update(args) or {"gradient": "skybox"}
    )
    task = wired_bridge.serve(1)
    await background(skybox=str(folder))
    await task

    assert sent["skybox"]["nx"].startswith("data:image/png")
    assert sent["skybox"]["px"].startswith("data:image/jpeg")


async def test_the_three_background_variants_are_mutually_exclusive(tmp_path):
    """They are one slot in Mol*, so two would mean one silently winning."""
    source = _write_image(tmp_path / "bg.png", (0, 0, 0))
    with pytest.raises(ViewerError, match="at most one of gradient, image or skybox"):
        await background(gradient="radial", image=str(source))


async def test_a_truncated_image_is_refused(tmp_path):
    """The case that makes verify() earn its place.

    Image.open() reads only the header, so a half-written PNG opens cleanly and
    reports the right format and size — it is verify() that reads the data and
    finds it short. Mol* would load the URL, fail, and draw nothing about it.
    """
    whole = tmp_path / "whole.png"
    PILImage.new("RGB", (64, 64), (1, 2, 3)).save(whole)
    truncated = tmp_path / "truncated.png"
    truncated.write_bytes(whole.read_bytes()[: whole.stat().st_size // 2])

    with pytest.raises(ViewerError, match="not an image Pillow can read"):
        await background(image=str(truncated))


# -- turntable and spin --------------------------------------------------------


def _frame_handlers(viewer, calls: list[tuple[str, dict[str, Any]]], size=(8, 6)) -> None:
    def on_orbit(args):
        calls.append(("orbit", args))
        return {"degrees": args["degrees"]}

    def on_snapshot(args):
        calls.append(("snapshot", args))
        buffer = io.BytesIO()
        PILImage.new("RGBA", size, (5, 6, 7, 255)).save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode()
        return {"data_uri": f"data:image/png;base64,{encoded}", "transparent": False}

    viewer.handlers["orbit"] = on_orbit
    viewer.handlers["snapshot"] = on_snapshot


async def test_a_turntable_writes_numbered_frames(wired_bridge, tmp_path):
    calls: list[tuple[str, dict[str, Any]]] = []
    _frame_handlers(wired_bridge, calls)
    task = wired_bridge.serve(40)
    out = await turntable(str(tmp_path / "turn"), frames=4, width=320)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    written = sorted(Path(out["directory"]).glob("frame_*.png"))
    assert [f.name for f in written] == [
        "frame_0000.png",
        "frame_0001.png",
        "frame_0002.png",
        "frame_0003.png",
    ]
    assert out["frames"] == 4
    assert out["step_degrees"] == 90.0


async def test_the_first_frame_is_captured_before_any_rotation(wired_bridge, tmp_path):
    """Otherwise the sequence starts one step past where the camera was left."""
    calls: list[tuple[str, dict[str, Any]]] = []
    _frame_handlers(wired_bridge, calls)
    task = wired_bridge.serve(40)
    await turntable(str(tmp_path / "turn"), frames=3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls[0][0] == "snapshot"


async def test_a_turntable_returns_the_camera_to_where_it_started(wired_bridge, tmp_path):
    """A sequence that leaves the camera turned is a trap for whatever runs next.

    Three frames over 360 degrees means steps of 120: two rotations during the
    sequence and a final 120 to close the loop.
    """
    calls: list[tuple[str, dict[str, Any]]] = []
    _frame_handlers(wired_bridge, calls)
    task = wired_bridge.serve(40)
    await turntable(str(tmp_path / "turn"), frames=3, degrees=360.0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    turns = [args["degrees"] for action, args in calls if action == "orbit"]
    assert turns == pytest.approx([120.0, 120.0, 120.0])
    assert sum(turns) == pytest.approx(360.0)


async def test_a_half_turn_also_comes_back(wired_bridge, tmp_path):
    calls: list[tuple[str, dict[str, Any]]] = []
    _frame_handlers(wired_bridge, calls)
    task = wired_bridge.serve(40)
    await turntable(str(tmp_path / "turn"), frames=4, degrees=180.0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    turns = [args["degrees"] for action, args in calls if action == "orbit"]
    assert sum(turns) == pytest.approx(180.0)


async def test_a_turntable_refuses_a_sequence_that_is_not_one():
    with pytest.raises(ViewerError, match="at least 2 frames"):
        await turntable("/tmp/turn", frames=1)


async def test_a_turntable_refuses_a_runaway_frame_count():
    with pytest.raises(ViewerError, match="beyond the 720"):
        await turntable("/tmp/turn", frames=5000)


async def test_a_turntable_refuses_an_incomplete_frame(wired_bridge, tmp_path):
    """The same guard snapshot() makes, per frame.

    A sequence is where this matters most: one bad frame in thirty-six is a
    flicker nobody notices until the movie is assembled.
    """

    def on_snapshot(args):
        buffer = io.BytesIO()
        # A frame with a hole in it, as a renderer out of room produces.
        image = PILImage.new("RGBA", (8, 6), (5, 6, 7, 255))
        image.putpixel((0, 0), (0, 0, 0, 0))
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode()
        return {"data_uri": f"data:image/png;base64,{encoded}", "transparent": False}

    wired_bridge.handlers["orbit"] = lambda args: {"degrees": args["degrees"]}
    wired_bridge.handlers["snapshot"] = on_snapshot
    task = wired_bridge.serve(40)
    with pytest.raises(ViewerError, match="Frame 0 came back incomplete"):
        await turntable(str(tmp_path / "turn"), frames=3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_spin_passes_its_mode_through(wired_bridge):
    sent: dict[str, Any] = {}
    wired_bridge.handlers["spin"] = lambda args: (
        sent.update(args) or {"mode": args["mode"]}
    )
    task = wired_bridge.serve(1)
    await spin(mode="rock", angle=25)
    await task

    assert sent == {"mode": "rock", "angle": 25}


# -- trajectory measurements ---------------------------------------------------


async def test_rmsf_needs_a_trajectory_first():
    with pytest.raises(ViewerError, match="No trajectory loaded"):
        await rmsf()


async def test_rmsd_series_needs_a_trajectory_first():
    with pytest.raises(ViewerError, match="No trajectory loaded"):
        await rmsd_series()


async def test_rmsf_groups_by_residue_and_ranks_by_motion(
    wired_bridge, tmp_path, monkeypatch
):
    """Structured numbers, ranked — the form the plan asks for rather than a plot."""
    template = _peptide_array()
    frames = np.stack(
        [
            template.coord
            + np.linspace(0, step, template.array_length())[:, None] * [0, 1.0, 0]
            for step in range(6)
        ]
    )
    monkeypatch.setattr(server_mod, "_trajectory", _stack_from(template, frames))

    out = await rmsf(per="residue")
    assert out["frames"] == 6
    assert out["count"] == len(set(template.res_id.tolist()))
    ranked = [entry["rmsf"] for entry in out["most_mobile"]]
    assert ranked == sorted(ranked, reverse=True)
    assert out["max"] >= out["mean"] >= out["min"]


async def test_rmsf_per_atom_reports_every_atom(wired_bridge, monkeypatch):
    template = _peptide_array()
    frames = np.stack([template.coord] * 3)
    monkeypatch.setattr(server_mod, "_trajectory", _stack_from(template, frames))

    out = await rmsf(per="atom")
    assert out["count"] == template.array_length()


async def test_rmsf_refuses_an_unknown_grouping(wired_bridge, monkeypatch):
    template = _peptide_array()
    monkeypatch.setattr(
        server_mod, "_trajectory", _stack_from(template, np.stack([template.coord] * 2))
    )
    with pytest.raises(ViewerError, match="Unknown grouping"):
        await rmsf(per="chain")


async def test_rmsd_series_reports_one_value_per_frame(wired_bridge, monkeypatch):
    template = _peptide_array()
    frames = np.stack([template.coord] * 4)
    monkeypatch.setattr(server_mod, "_trajectory", _stack_from(template, frames))

    out = await rmsd_series()
    assert out["frames"] == 4
    assert len(out["rmsd"]) == 4
    assert out["rmsd"][0] == pytest.approx(0.0, abs=1e-4)


def _peptide_array() -> Any:
    """Three residues of two atoms each, as a template for coordinate frames."""
    atoms: list[Any] = []
    for residue in range(3):
        for name in ("N", "CA"):
            atoms.append(
                Atom(
                    [float(residue), 0.0, 0.0],
                    chain_id="A",
                    res_id=residue + 1,
                    ins_code="",
                    res_name="ALA",
                    atom_name=name,
                    element="C",
                    hetero=False,
                    b_factor=0.0,
                    occupancy=1.0,
                    atom_id=len(atoms) + 1,
                )
            )
    return atom_array(atoms)


def _stack_from(template: Any, frames: Any) -> Any:
    built = []
    for coord in frames:
        copy = template.copy()
        copy.coord = coord.astype(np.float32)
        built.append(copy)
    return atom_stack(built)


async def test_rmsf_reports_internal_motion_not_bulk_drift(wired_bridge, monkeypatch):
    """The tool superposes, not just the module underneath it.

    A molecule that slid across the box rigidly has fluctuated not at all.
    Measured on raw frames it reads as hugely mobile everywhere — a confident
    wrong answer — so this pins the correction at the tool boundary rather than
    trusting that whoever wired it up called the right helper.
    """
    template = _peptide_array()
    slide = np.array([0.0, 12.0, 0.0])
    drifted = np.stack([template.coord + slide * step for step in range(5)])
    monkeypatch.setattr(server_mod, "_trajectory", _stack_from(template, drifted))

    out = await rmsf(per="atom")
    assert out["max"] == pytest.approx(0.0, abs=1e-2)


async def test_record_trajectory_honours_stride(wired_bridge, tmp_path, monkeypatch):
    """Every nth frame, which is how a long run becomes a short movie."""
    template = _peptide_array()
    frames = np.stack([template.coord] * 10)
    monkeypatch.setattr(server_mod, "_trajectory", _stack_from(template, frames))

    seen: list[int] = []

    def on_frame(args):
        seen.append(args["index"])
        return {"index": args["index"], "frames": 10}

    wired_bridge.handlers["frame"] = on_frame
    wired_bridge.handlers["snapshot"] = _snapshot_handler({}, 32, 24)
    task = wired_bridge.serve(60)
    out = await record_trajectory(str(tmp_path / "frames"), width=32, stride=3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert out["frames"] == 4
    assert out["of"] == 10
    # 0, 3, 6, 9 captured, then back to 0.
    assert seen == [0, 3, 6, 9, 0]
    assert len(sorted(Path(out["directory"]).glob("frame_*.png"))) == 4


# -- a new structure ends the session before it ---------------------------------
#
# The trajectory's frames are one molecule's atoms in one order, and a saved
# camera was framed on that molecule. Kept across a load, both keep answering
# about something that is no longer here. `rmsf` reads the trajectory's own
# first frame rather than the loaded structure, so nothing mismatches and
# nothing complains — the analysis simply describes the wrong molecule while
# the viewer shows the right one.


def _toy_pdb(tmp_path: Any, name: str = "toy") -> str:
    f = tmp_path / f"{name}.pdb"
    f.write_text(
        "ATOM      1  N   MET A   1      11.104   6.134  -6.504  1.00  0.00           N\n"
        "END\n"
    )
    return str(f)


async def _load_toy(wired_bridge: Any, path: str) -> Any:
    wired_bridge.handlers["load_structure"] = lambda args: {"loaded": args["name"]}
    task = wired_bridge.serve(1)
    try:
        return await fetch_structure(path)
    finally:
        await task


async def test_loading_a_structure_discards_the_previous_trajectory(
    wired_bridge, tmp_path, monkeypatch
):
    """The product claim, asserted through the tool a caller would actually hit."""
    template = _peptide_array()
    frames = np.stack([template.coord] * 4)
    monkeypatch.setattr(server_mod, "_trajectory", _stack_from(template, frames))

    await _load_toy(wired_bridge, _toy_pdb(tmp_path))

    assert server_mod._trajectory is None
    with pytest.raises(ViewerError, match="No trajectory loaded"):
        await rmsf()
    with pytest.raises(ViewerError, match="No trajectory loaded"):
        await rmsd_series()


async def test_the_reply_says_the_trajectory_was_discarded(
    wired_bridge, tmp_path, monkeypatch
):
    """Silently dropping it would be a smaller version of the same bug: the
    caller finds out from a refusal several calls later instead."""
    template = _peptide_array()
    monkeypatch.setattr(
        server_mod, "_trajectory", _stack_from(template, np.stack([template.coord] * 2))
    )
    message = await _load_toy(wired_bridge, _toy_pdb(tmp_path))
    assert "discarded" in message and "trajectory" in message


async def test_loading_a_structure_discards_saved_keyframes(
    wired_bridge, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        server_mod,
        "_keyframes",
        {"a": {"position": [0, 0, 1]}, "b": {"position": [0, 1, 0]}},
    )
    message = await _load_toy(wired_bridge, _toy_pdb(tmp_path))
    assert server_mod._keyframes == {}
    assert "2 keyframes" in message


async def test_a_clean_load_says_nothing_about_discarding(
    wired_bridge, tmp_path, monkeypatch
):
    """The usual case must not grow a confusing sentence about things that
    were never there."""
    monkeypatch.setattr(server_mod, "_trajectory", None)
    monkeypatch.setattr(server_mod, "_keyframes", {})
    message = await _load_toy(wired_bridge, _toy_pdb(tmp_path))
    assert "discarded" not in message


async def test_one_keyframe_is_not_pluralised(wired_bridge, tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "_keyframes", {"only": {"position": [0, 0, 1]}})
    message = await _load_toy(wired_bridge, _toy_pdb(tmp_path))
    assert "1 keyframe" in message and "1 keyframes" not in message


# -- volume provenance ---------------------------------------------------------


def _volume_reply(args: dict[str, Any]) -> dict[str, Any]:
    """What the viewer answers a load_volume with. Shape only; stats are §1.4's."""
    return {
        "name": args["name"],
        "format": args.get("format", "ccp4"),
        "provenance": args.get("provenance", "unknown"),
        "dimensions": [2, 2, 2],
        "voxels": 8,
        "min": 0.0,
        "max": 1.0,
        "mean": 0.5,
        "sigma": 0.25,
        "stated": {"min": None, "max": None, "mean": None, "sigma": None},
    }


async def test_load_volume_never_infers_provenance_from_the_filename(
    wired_bridge, tmp_path
):
    """The invariant, tested where the filename is actually visible.

    The viewer never sees the path — only a handle and a URL — so it *cannot*
    guess, and a test driving the viewer action directly cannot fail for this
    reason. The server is the only place with the filename, so this is the only
    place the guess could happen and the only place worth asserting it does not.

    The fixture is named to bait exactly that guess.
    """
    baited = write_mrc(tmp_path / "emd_30913_deepemhancer_sharpened.map")

    sent: dict[str, Any] = {}

    def on_load(args: dict[str, Any]) -> dict[str, Any]:
        sent.update(args)
        return _volume_reply(args)

    async with _serving(wired_bridge, load_volume=on_load):
        result = await load_volume(str(baited))

    assert sent["provenance"] == "unknown", (
        f"the filename says deepemhancer and sharpened; neither is evidence, and "
        f"a guessed label is believed where a missing one prompts a question: {sent}"
    )
    assert result["provenance"] == "unknown"
    assert "UNKNOWN" in result["caveat"]


async def test_a_declared_provenance_reaches_the_viewer_with_its_caveat(
    wired_bridge, tmp_path
):
    sent: dict[str, Any] = {}

    def on_load(args: dict[str, Any]) -> dict[str, Any]:
        sent.update(args)
        return _volume_reply(args)

    async with _serving(wired_bridge, load_volume=on_load):
        result = await load_volume(
            str(write_mrc(tmp_path / "m.map")), provenance="nn_enhanced"
        )

    assert sent["provenance"] == "nn_enhanced"
    assert "NETWORK-ENHANCED" in result["caveat"]


async def test_an_unknown_provenance_is_refused_before_anything_is_read(
    wired_bridge, tmp_path
):
    """Refused ahead of the file, so a bad declaration cannot half-load a map."""
    missing = tmp_path / "does-not-exist.map"

    with pytest.raises(ViewerError, match="unknown provenance"):
        await load_volume(str(missing), provenance="deepemhancer")

    # And the refusal is about the provenance, not the missing file — proof the
    # check ran first rather than the read failing for its own reasons.
    assert not missing.exists()
