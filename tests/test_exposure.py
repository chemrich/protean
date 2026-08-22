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
    reference_area,
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
    rows = residue_exposure(buried_core).residues
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
    rows = residue_exposure(buried_core).residues
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
    rows = residue_exposure(with_ligand).residues
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

    assert [r["resn"] for r in residue_exposure(drowned).residues].count("HOH") == 0
    assert residue_exposure(drowned).residues == residue_exposure(buried_core).residues


def test_nothing_reachable_reports_rather_than_dividing_by_it():
    """A single atom inside nothing still has to answer."""
    lonely = atom_array(_residue("A", 1, "ALA", (0.0, 0.0, 0.0)))
    rows = residue_exposure(lonely).residues

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
        probe: sum(
            r["area_a2"] for r in residue_exposure(core, probe_radius=probe).residues
        )
        for probe in (1.0, 1.4, 2.0, 3.0)
    }
    assert list(areas.values()) == sorted(areas.values())

    def core_exposure(probe: float) -> float:
        rows = residue_exposure(core, probe_radius=probe).residues
        return float(next(r["relative"] for r in rows if r["seq"] == 1))

    assert core_exposure(1.0) > core_exposure(1.4)
    assert core_exposure(3.0) == 0.0


def test_hydrogens_do_not_push_a_surface_residue_underground():
    """Depth is measured only over atoms that have a radius.

    A hydrogen is never "reached" — ProtOr gives it no radius at all — but it
    is still an atom of its residue, so averaging depth over every atom made
    each hydrogen contribute its own ~1 A distance to the nearest heavy atom.
    Measured on 1L2Y, where half the 304 atoms are hydrogens: ASN1 is 81%
    exposed and read 0.52 A deep before this, against 0.0 with the hydrogens
    stripped. It hit exactly the protonated NMR and MD files the trajectory
    tools load.
    """
    core = _shell()
    # Inserted *inside* residue 2's run of atoms, not appended. Residues are
    # contiguous blocks, so a hydrogen tacked onto the end of the array is a
    # separate residue that merely shares a number — which is what the first
    # version of this test measured.
    atoms = list(core)
    protonated = atom_array(
        [
            *atoms[:10],
            Atom(
                [1.0, 1.0, 6.6],
                chain_id="A",
                res_id=2,
                ins_code="",
                res_name="ALA",
                atom_name="HB1",
                element="H",
                hetero=False,
            ),
            *atoms[10:],
        ]
    )

    plain = {r["seq"]: r["depth_a"] for r in residue_exposure(core).residues}
    withH = {r["seq"]: r["depth_a"] for r in residue_exposure(protonated).residues}

    assert withH[2] == plain[2], "a hydrogen changed its residue's depth"


def test_hydrogens_stay_out_of_depth_under_the_fallback_radii_too():
    """The fallback gives hydrogens a radius, which reopens the bug above.

    ProtOr has no radius for a hydrogen, so it drops out of the depth pass on
    its own. The `Single` fallback measures by element and hands one back — and
    a single ligand the dictionary has never seen switches the *whole*
    structure to `Single`. So the same file, plus one unknown ligand, quietly
    went back to inflating depth for every protonated residue.
    """
    core = list(_shell())
    hydrogen = Atom(
        [1.0, 1.0, 6.6],
        chain_id="A",
        res_id=2,
        ins_code="",
        res_name="ALA",
        atom_name="HB1",
        element="H",
        hetero=False,
    )
    # An invented ligand forces the fallback for everything.
    ligand = _residue("A", 900, "LIG", (0.0, 0.0, 40.0))

    without_h = residue_exposure(atom_array([*core, *ligand]))
    with_h = residue_exposure(atom_array([*core[:10], hydrogen, *core[10:], *ligand]))

    assert without_h.radii == "single", "the ligand did not force the fallback"
    assert with_h.radii == "single"

    plain = {r["seq"]: r["depth_a"] for r in without_h.residues}
    protonated = {r["seq"]: r["depth_a"] for r in with_h.residues}
    assert protonated[2] == plain[2], "a hydrogen changed depth under Single radii"


def test_symmetry_copies_fold_into_one_row_per_residue():
    """A biological assembly repeats a chain, and copies share chain and number.

    `biological` is the *default* load path, and a per-occurrence listing gives
    two rows naming one residue — which `define_field` refuses by design,
    because the second would silently replace the first. Measured on 1HHO
    before this: 584 rows for 292 residues, and the call the tool's docstring
    recommends raised outright.
    """
    one = _shell()
    doubled = atom_array([*one, *one])

    single = residue_exposure(one).residues
    folded = residue_exposure(doubled).residues

    keys = [(r["chain"], r["seq"]) for r in folded]
    assert len(keys) == len(set(keys)), "a residue is listed more than once"
    assert len(folded) == len(single)
    assert all(r["copies"] == 2 for r in folded)


def test_a_folded_residue_reports_its_exposure_not_the_sum_of_its_copies():
    """Averaged, not summed: each copy is one residue, not half of a bigger one."""
    one = _shell()
    doubled = atom_array([*one, *one])

    single = {r["seq"]: r["area_a2"] for r in residue_exposure(one).residues}
    folded = {r["seq"]: r["area_a2"] for r in residue_exposure(doubled).residues}

    # Coincident copies occlude each other, so the areas are not identical —
    # the claim is that folding does not double them.
    for seq, area in folded.items():
        assert area <= single[seq] + 0.01, f"residue {seq} grew when copied"


def test_an_unmeasured_residue_is_null_rather_than_zero():
    """biotite drops monoatomic ions, and 0 would read as maximally buried.

    A fully exposed zinc reported `area_a2: 0.0`, which sorts and colours as
    the most buried thing in the structure. Null says "not measured", which is
    what happened.
    """
    with_ion = atom_array(
        [
            *_shell(),
            Atom(
                [0.0, 0.0, 40.0],
                chain_id="A",
                res_id=500,
                ins_code="",
                res_name="ZN",
                atom_name="ZN",
                element="ZN",
                hetero=True,
            ),
        ]
    )
    ion = next(r for r in residue_exposure(with_ion).residues if r["resn"] == "ZN")

    assert ion["area_a2"] is None
    assert ion["relative"] is None
    assert ion["depth_a"] is None


def test_selenomethionine_is_measured_like_the_methionine_it_is():
    """One MSE used to make the documented call fail for the whole structure.

    `define_field` refuses a null, and MSE — which is how a large share of
    crystal structures were phased — had no reference area, so a single one
    anywhere left `relative` null and took the call down.
    """
    assert reference_area("MSE") == MAX_ASA["MET"]
    assert reference_area("HIE") == MAX_ASA["HIS"]
    assert reference_area("CYX") == MAX_ASA["CYS"]
    # And a genuine unknown still has no answer, which is the point of null.
    assert reference_area("LIG") is None


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


class TestTheDefineFieldHandoff:
    """The call the docstring recommends, checked as far as the residue key.

    A review found this broken and the existing test could not see it: it
    exercised `_field_value` and stopped, while the failure was one line later
    in the *key* `define_field` builds. `ins_code: None` formatted as the
    string "None", so the key came out `A|76|None` against the viewer's
    `A|76|`, and the recommended call matched zero residues on every structure
    without insertion codes — which is nearly all of them.
    """

    @pytest.fixture(autouse=True)
    def loaded(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_structure", _shell())
        monkeypatch.setattr(server_mod, "_structure_identifier", "shell")
        return server_mod

    async def test_entries_carry_no_ins_code_when_there_is_none(self, loaded):
        out = await loaded.sasa()

        assert all("ins_code" not in r for r in out["residues"])

    async def test_the_key_define_field_builds_is_the_one_the_viewer_uses(self, loaded):
        """The exact expression from both sides, compared."""
        entry = (await loaded.sasa())["residues"][0]

        built = f"{entry['chain']}|{int(entry['seq'])}|{entry.get('ins_code', '')}"
        # dispatch.ts builds `${chain}|${seq}|${ins || ''}` from the viewer's
        # own residues, so an entry with no insertion code has to end in a bare
        # separator.
        assert built.endswith("|")
        assert "None" not in built

    async def test_an_unscored_residue_is_refused_by_name_not_by_TypeError(self, loaded):
        """A ligand has a null `relative`, and `float(None)` is not an answer."""
        with pytest.raises(ViewerError, match="has no 'relative' value"):
            loaded._field_value({"chain": "A", "seq": 9, "relative": None}, "relative")

    async def test_area_and_depth_are_answerable_for_everything(self, loaded):
        """Which is what the refusal above tells the caller to fall back to."""
        out = await loaded.sasa()

        for row in out["residues"]:
            assert loaded._field_value(row, "area_a2") == row["area_a2"]
            assert loaded._field_value(row, "depth_a") == row["depth_a"]
