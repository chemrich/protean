"""Solvent accessibility, relative exposure and burial depth.

Built structures rather than fetched ones, like `test_contacts.py`: the
geometry is the thing under test, and a synthetic shell around a synthetic
core states the expected answer in the fixture instead of trusting a PDB entry
to still contain what it contained last year.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

import protean_mcp.server as server_mod
from protean_mcp.analysis.exposure import (
    MAX_ASA,
    _nearest_distance,
    residue_exposure,
)
from protean_mcp.connection import ViewerError

# Backbone plus a beta carbon, which is what ProtOr needs to give an alanine
# real radii rather than falling back.
_ALA = (("N", "N"), ("CA", "C"), ("CB", "C"), ("C", "C"), ("O", "O"))


def _residue(
    chain: str, seq: int, resn: str, centre: tuple[float, float, float]
) -> list[Atom]:
    """One residue as five atoms in a tight clump around *centre*."""
    offsets = [(0, 0, 0), (1.5, 0, 0), (2.0, 1.4, 0), (0.7, -1.3, 0.4), (-1.1, 0.9, 0.6)]
    return [
        Atom(
            [centre[0] + dx, centre[1] + dy, centre[2] + dz],
            chain_id=chain,
            res_id=seq,
            ins_code="",
            res_name=resn,
            atom_name=name,
            element=element,
            hetero=resn not in MAX_ASA,
        )
        for (name, element), (dx, dy, dz) in zip(_ALA, offsets, strict=True)
    ]


def _shell(radius: float = 6.0, count: int = 80) -> AtomArray[Any]:
    """One residue at the origin, buried under a sphere of others.

    The radius is measured, not chosen. At 9 A the shell is a loose cage and
    the "buried" core reads 1.65 — more exposed than a free tripeptide — so a
    test written against it would have asserted nothing. At 6 A with 80
    residues the core reaches zero and sits 5.4 A deep, against a shell whose
    deepest is 1.9 A.
    """
    atoms = _residue("A", 1, "ALA", (0.0, 0.0, 0.0))
    # Fibonacci sphere, so the covering is even and the core is genuinely
    # enclosed rather than enclosed on the side the loop happened to favour.
    golden = np.pi * (3.0 - np.sqrt(5.0))
    for i in range(count):
        y = 1.0 - 2.0 * i / (count - 1)
        r = np.sqrt(max(0.0, 1.0 - y * y))
        theta = golden * i
        atoms += _residue(
            "A",
            i + 2,
            "ALA",
            (radius * np.cos(theta) * r, radius * y, radius * np.sin(theta) * r),
        )
    return atom_array(atoms)


@pytest.fixture
def buried_core() -> AtomArray[Any]:
    return _shell()


def test_the_enclosed_residue_is_buried_and_the_shell_is_not(buried_core):
    """The floor: burial has to track geometry, not residue type."""
    rows = residue_exposure(buried_core)
    core = next(r for r in rows if r["seq"] == 1)
    shell = [r for r in rows if r["seq"] != 1]

    assert core["relative"] < 0.05, f"the enclosed residue reads {core['relative']}"
    assert min(r["relative"] for r in shell) > core["relative"]


def test_the_enclosed_residue_is_the_deepest_one(buried_core):
    """Depth is a second measurement, and it must agree with the first.

    Worth its own test because depth and area are computed differently — one
    from the probe, one from a distance — and a sign error in either would
    still leave both looking like plausible numbers.
    """
    rows = residue_exposure(buried_core)
    core = next(r for r in rows if r["seq"] == 1)
    shell = [r for r in rows if r["seq"] != 1]

    assert core["depth_a"] > max(r["depth_a"] for r in shell)
    # Not zero for the shell, which is worth stating: a shell residue has
    # inward-facing atoms that no probe reaches either, so it carries a real
    # depth of its own. The claim is the ordering, not that everything outside
    # the core is at the surface.
    assert max(r["depth_a"] for r in shell) > 0.0


def test_relative_is_null_where_there_is_no_reference(buried_core):
    """A ligand has no Gly-X-Gly maximum, and inventing one would draw a lie."""
    with_ligand = atom_array(
        list(buried_core) + _residue("A", 999, "LIG", (0.0, 0.0, 20.0))
    )
    rows = residue_exposure(with_ligand)
    ligand = next(r for r in rows if r["resn"] == "LIG")

    assert ligand["relative"] is None
    assert ligand["area_a2"] > 0
    assert ligand["depth_a"] is not None


def test_water_is_removed_before_the_probe_is_rolled(buried_core):
    """Waters sit on the surface; counting them buries the protein under them."""
    drowned = atom_array(
        list(buried_core)
        + [
            Atom(
                [0.0, 0.0, 11.0 + 0.1 * i],
                chain_id="A",
                res_id=2000 + i,
                ins_code="",
                res_name="HOH",
                atom_name="O",
                element="O",
                hetero=True,
            )
            for i in range(30)
        ]
    )

    assert [r["resn"] for r in residue_exposure(drowned)].count("HOH") == 0
    assert residue_exposure(drowned) == residue_exposure(buried_core)


def test_nothing_reachable_reports_rather_than_dividing_by_it():
    """A single atom inside nothing still has to answer."""
    lonely = atom_array(_residue("A", 1, "ALA", (0.0, 0.0, 0.0)))
    rows = residue_exposure(lonely)

    assert len(rows) == 1
    assert rows[0]["depth_a"] == 0.0


def test_a_bigger_probe_covers_more_ground_and_reaches_into_less():
    """Both directions, because the obvious one is wrong.

    "A coarser probe reaches less" was the first version of this test and it
    failed: solvent-accessible area is measured at the *probe centre*, so a
    larger probe rolls on a wider surface and the total goes **up** — measured
    on this shell, 1218 A^2 at probe 1.0 and 1732 A^2 at probe 3.0.

    What does fall is what the probe can squeeze into. The enclosed residue
    reads 0.0133 under a 1.0 A probe and exactly 0 under 1.4 A and above,
    because the gaps between the shell residues stop admitting it. That is the
    property anyone raising the probe radius is actually reaching for.
    """
    core = _shell()
    areas = {
        probe: sum(r["area_a2"] for r in residue_exposure(core, probe_radius=probe))
        for probe in (1.0, 1.4, 2.0, 3.0)
    }
    assert list(areas.values()) == sorted(areas.values())

    def core_exposure(probe: float) -> float:
        rows = residue_exposure(core, probe_radius=probe)
        return float(next(r["relative"] for r in rows if r["seq"] == 1))

    assert core_exposure(1.0) > core_exposure(1.4)
    assert core_exposure(3.0) == 0.0


class TestNearestDistance:
    """The hand-rolled replacement for a KD-tree, checked against brute force.

    scipy is not a declared dependency, so this arithmetic is protean's own —
    which means the chunking is protean's bug to have. The chunk is 512, so the
    sizes below straddle it deliberately.
    """

    @pytest.mark.parametrize("count", [1, 3, 511, 512, 513, 1100])
    def test_matches_a_naive_computation(self, count):
        rng = np.random.default_rng(0)
        points = rng.normal(size=(count, 3)) * 10
        targets = rng.normal(size=(max(1, count // 3), 3)) * 10

        naive = np.array([np.min(np.linalg.norm(targets - p, axis=1)) for p in points])
        assert np.allclose(_nearest_distance(points, targets), naive)

    def test_a_point_that_is_its_own_target_is_at_zero(self):
        points = np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
        assert list(_nearest_distance(points, points)) == [0.0, 0.0]


class TestTheTool:
    """`sasa()` itself, which adds selection handling and the listing rules."""

    @pytest.fixture(autouse=True)
    def loaded(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_structure", _shell())
        monkeypatch.setattr(server_mod, "_structure_identifier", "shell")
        return server_mod

    async def test_lists_every_residue_by_default(self, loaded):
        """The default has to be all of them, because define_field is next.

        A truncated field covers part of the molecule and looks entirely
        deliberate on screen, which is the failure this default exists to
        avoid — `rmsf` and `conservation` both cap at 50 and 200.
        """
        out = await loaded.sasa()

        assert out["count"] == 81
        assert len(out["residues"]) == 81
        assert out["truncated"] is False

    async def test_a_limit_truncates_and_says_so(self, loaded):
        out = await loaded.sasa(limit=5)

        assert len(out["residues"]) == 5
        assert out["truncated"] is True
        assert out["count"] == 81

    async def test_lists_the_most_exposed_first(self, loaded):
        out = await loaded.sasa(limit=3)
        listed = [r["relative"] for r in out["residues"]]

        assert listed == sorted(listed, reverse=True)
        assert 1 not in [r["seq"] for r in out["residues"]]

    async def test_the_entries_are_what_define_field_eats(self, loaded):
        """The seam the docstring promises, checked rather than asserted in prose."""
        out = await loaded.sasa()
        entry = out["residues"][0]

        assert {"chain", "seq"} <= set(entry)
        # Three numbers per entry, so `key` is not optional here — and this is
        # the call the docstring tells the caller to make.
        assert loaded._field_value(entry, key="relative") == entry["relative"]
        with pytest.raises(ViewerError, match="one number"):
            loaded._field_value(entry)

    async def test_a_selection_narrows_the_calculation(self, loaded):
        whole = await loaded.sasa()
        part = await loaded.sasa(selection="resi 1")

        assert part["count"] == 1
        assert part["measured_over"] == "resi 1"
        # And the point worth stating in the docstring: burial is a property of
        # the whole molecule, so the core measured alone is not buried at all.
        core_alone = part["residues"][0]["relative"]
        core_in_place = next(r for r in whole["residues"] if r["seq"] == 1)["relative"]
        assert core_alone > core_in_place

    async def test_a_selection_matching_nothing_is_refused(self, loaded):
        with pytest.raises(ViewerError, match="matches no atoms"):
            await loaded.sasa(selection="resi 9999")

    async def test_a_negative_probe_is_refused(self, loaded):
        with pytest.raises(ViewerError, match="probe_radius must be positive"):
            await loaded.sasa(probe_radius=0)
