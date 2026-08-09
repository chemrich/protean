"""Electrostatics: charges, the screened-Coulomb field, OpenDX, and sampling.

Offline and fast. The physics is checked against closed-form values rather
than against a previous run of the same code, and the OpenDX round trip is
checked on a deliberately non-cubic grid, because a transposed field is
numerically perfect and completely wrong.

APBS is exercised only where a binary exists; that path is opt-in:

    PROTEAN_APBS=1 uv run pytest tests/test_electrostatics.py -k apbs
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom
from biotite.structure import array as atom_array

from protean_mcp.analysis.electrostatics import (
    BOLTZMANN_KCAL,
    COULOMB_KCAL,
    DEFAULT_TEMPERATURE,
    MAX_GRID_POINTS,
    SOLVENT_DIELECTRIC,
    ElectrostaticsError,
    PotentialGrid,
    PreparedStructure,
    apbs_binary,
    coulombic,
    debye_length,
    grid_axes,
    parse_pqr,
    prepare,
    read_dx,
    run_apbs,
    sample,
    write_dx,
)

# kT/e per (e / angstrom) in bulk water at 298 K.
UNIT_POTENTIAL = COULOMB_KCAL / (
    SOLVENT_DIELECTRIC * BOLTZMANN_KCAL * DEFAULT_TEMPERATURE
)


def _charge(*charges: tuple[float, tuple[float, float, float]]) -> PreparedStructure:
    return PreparedStructure(
        coords=np.array([xyz for _, xyz in charges], dtype=float),
        charges=np.array([q for q, _ in charges], dtype=float),
        radii=np.ones(len(charges)),
        pqr_text="",
        forcefield="test",
        ph=7.0,
    )


def _centre(grid: PotentialGrid) -> tuple[int, int, int]:
    nx, ny, nz = grid.shape
    return nx // 2, ny // 2, nz // 2


# -- screening -----------------------------------------------------------------


def test_debye_length_matches_the_textbook_values():
    """~7.9 A at physiological salt, ~30.4 A at 10 mM."""
    assert debye_length(0.15) == pytest.approx(7.85, abs=0.05)
    assert debye_length(0.01) == pytest.approx(30.4, abs=0.1)


def test_no_salt_means_no_screening():
    assert debye_length(0.0) == float("inf")


def test_screening_shortens_as_salt_rises():
    assert debye_length(1.0) < debye_length(0.1) < debye_length(0.01)


# -- the field itself ----------------------------------------------------------


def test_point_charge_matches_the_closed_form():
    """The whole approximation is one equation; this is that equation."""
    grid = coulombic(_charge((1.0, (0.0, 0.0, 0.0))), spacing=1.0, ionic_strength=0.0)
    i, j, k = _centre(grid)
    for distance in (3, 5, 8):
        assert grid.values[i + distance, j, k] == pytest.approx(
            UNIT_POTENTIAL / distance, rel=1e-5
        )


def test_screening_attenuates_by_exactly_exp_minus_kappa_r():
    unscreened = coulombic(
        _charge((1.0, (0.0, 0.0, 0.0))), spacing=1.0, ionic_strength=0.0
    )
    screened = coulombic(
        _charge((1.0, (0.0, 0.0, 0.0))), spacing=1.0, ionic_strength=0.15
    )
    i, j, k = _centre(unscreened)
    distance = 8.0
    ratio = screened.values[i + 8, j, k] / unscreened.values[i + 8, j, k]
    assert ratio == pytest.approx(np.exp(-distance / debye_length(0.15)), rel=1e-4)


def test_opposite_charges_cancel_on_their_midplane():
    grid = coulombic(
        _charge((1.0, (-6.0, 0.0, 0.0)), (-1.0, (6.0, 0.0, 0.0))),
        spacing=1.0,
        ionic_strength=0.0,
    )
    i, j, k = _centre(grid)
    assert grid.values[i, j, k] == pytest.approx(0.0, abs=1e-6)


def test_sign_follows_the_charge():
    positive = coulombic(_charge((1.0, (0.0, 0.0, 0.0))), spacing=1.0)
    negative = coulombic(_charge((-1.0, (0.0, 0.0, 0.0))), spacing=1.0)
    assert positive.values.max() > 0 and negative.values.min() < 0
    assert positive.values == pytest.approx(-negative.values)


def test_potential_is_finite_at_the_charge_itself():
    """A grid point can land on a nucleus; 1/r there must not be inf or NaN."""
    grid = coulombic(_charge((1.0, (0.0, 0.0, 0.0))), spacing=1.0)
    assert np.isfinite(grid.values).all()


def test_method_string_says_it_is_not_poisson_boltzmann():
    """The caveat travels with the number or it does not travel."""
    grid = coulombic(_charge((1.0, (0.0, 0.0, 0.0))))
    assert "not a Poisson-Boltzmann" in grid.method


# -- grid geometry -------------------------------------------------------------


def test_grid_encloses_the_molecule_with_padding():
    origin, counts = grid_axes(np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]), 1.0, 5.0)
    assert origin == (-5.0, -5.0, -5.0)
    assert counts[0] == 21  # -5 .. 15 inclusive


def test_absurd_grids_are_refused_with_the_numbers():
    with pytest.raises(ElectrostaticsError, match="points"):
        grid_axes(np.array([[0.0, 0.0, 0.0], [500.0, 500.0, 500.0]]), 0.1, 10.0)


def test_the_refusal_names_the_limit():
    """A refusal that does not say the budget leaves nothing to act on."""
    with pytest.raises(ElectrostaticsError, match=f"{MAX_GRID_POINTS:,}"):
        grid_axes(np.array([[0.0, 0.0, 0.0], [500.0, 500.0, 500.0]]), 0.1, 10.0)


# -- OpenDX --------------------------------------------------------------------


def _asymmetric_grid() -> PotentialGrid:
    """Distinct values and three different axis lengths.

    A cubic grid of symmetric data round-trips through a transposed writer
    without complaint, which is exactly why this one is neither.
    """
    values = np.arange(3 * 4 * 5, dtype=float).reshape(3, 4, 5)
    return PotentialGrid(
        origin=(-1.0, -2.0, -3.0),
        spacing=(0.5, 0.5, 0.5),
        values=values,
        method="test",
    )


def test_dx_round_trip_preserves_the_field_exactly():
    original = _asymmetric_grid()
    restored = read_dx(write_dx(original))
    assert restored.shape == original.shape
    np.testing.assert_allclose(restored.values, original.values)


def test_dx_round_trip_preserves_geometry():
    original = _asymmetric_grid()
    restored = read_dx(write_dx(original))
    assert restored.origin == pytest.approx(original.origin)
    assert restored.spacing == pytest.approx(original.spacing)


def test_dx_declares_the_point_count_it_carries():
    text = write_dx(_asymmetric_grid())
    assert f"items {3 * 4 * 5} data follows" in text


def test_dx_with_a_lying_item_count_is_refused():
    """Reshaping a mismatched buffer would scramble the field silently."""
    text = write_dx(_asymmetric_grid()).replace("counts 3 4 5", "counts 3 4 6", 1)
    with pytest.raises(ElectrostaticsError, match="declares"):
        read_dx(text)


def test_dx_without_a_grid_definition_is_refused():
    with pytest.raises(ElectrostaticsError, match="Malformed"):
        read_dx("# nothing here\n")


# -- sampling ------------------------------------------------------------------


def test_sampling_reproduces_the_analytic_value():
    grid = coulombic(_charge((1.0, (0.0, 0.0, 0.0))), spacing=1.0, ionic_strength=0.0)
    assert sample(grid, [[5.0, 0.0, 0.0]])[0] == pytest.approx(
        UNIT_POTENTIAL / 5.0, rel=1e-5
    )


def test_sampling_interpolates_between_grid_points():
    grid = _asymmetric_grid()
    # Halfway along the last axis between values 0 and 1.
    value = sample(grid, [[-1.0, -2.0, -3.0 + 0.25]])[0]
    assert value == pytest.approx(0.5)


def test_points_outside_the_box_are_nan_not_clamped():
    """A clamped value is a real number in the wrong place."""
    grid = coulombic(_charge((1.0, (0.0, 0.0, 0.0))), spacing=1.0)
    assert np.isnan(sample(grid, [[500.0, 0.0, 0.0]])[0])


# -- charge assignment ---------------------------------------------------------


def test_parse_pqr_reads_coordinates_charges_and_radii():
    text = (
        "REMARK  1 PQR\n"
        "ATOM      1  N   MET A   1      27.340  24.430   2.614  0.1592 1.8240\n"
        "ATOM      2  CA  MET A   1      26.266  25.413   2.842 -0.0221 1.9080\n"
    )
    coords, charges, radii = parse_pqr(text)
    assert coords.shape == (2, 3)
    assert coords[0] == pytest.approx([27.340, 24.430, 2.614])
    assert charges == pytest.approx([0.1592, -0.0221])
    assert radii == pytest.approx([1.8240, 1.9080])


def test_an_empty_pqr_is_an_error_not_an_empty_field():
    """pdb2pqr fails this way on mmCIF input, and it fails quietly."""
    with pytest.raises(ElectrostaticsError, match="no atoms"):
        parse_pqr("# nothing\n")


@pytest.fixture
def dipeptide() -> Any:
    """Two glycines with a complete C-terminus.

    OXT is not optional here: pdb2pqr refuses a structure missing more than
    10% of its heavy atoms rather than quietly charging a broken one, and one
    absent terminal oxygen out of nine crosses that line.
    """
    atoms = []
    coords = {
        1: {
            "N": (0.0, 0.0, 0.0),
            "CA": (1.46, 0.0, 0.0),
            "C": (2.0, 1.42, 0.0),
            "O": (1.25, 2.39, 0.0),
        },
        2: {
            "N": (3.33, 1.5, 0.0),
            "CA": (4.0, 2.78, 0.0),
            "C": (5.5, 2.65, 0.0),
            "O": (6.1, 1.58, 0.0),
            "OXT": (6.05, 3.75, 0.0),
        },
    }
    serial = 1
    for res_id, names in coords.items():
        for name, xyz in names.items():
            atoms.append(
                Atom(
                    list(xyz),
                    chain_id="A",
                    res_id=res_id,
                    ins_code="",
                    res_name="GLY",
                    atom_name=name,
                    element=name[0],
                    hetero=False,
                    b_factor=10.0,
                    occupancy=1.0,
                    atom_id=serial,
                )
            )
            serial += 1
    return atom_array(atoms)


def test_prepare_assigns_charges_and_adds_hydrogens(dipeptide):
    prepared = prepare(dipeptide)
    assert len(prepared.charges) > dipeptide.array_length(), "hydrogens should be added"
    assert prepared.coords.shape[0] == len(prepared.charges)
    assert np.abs(prepared.charges).sum() > 0


def test_prepared_summary_states_the_forcefield_and_ph(dipeptide):
    summary = prepare(dipeptide, ph=6.5).as_dict()
    assert summary["forcefield"] == "AMBER"
    assert summary["ph"] == 6.5
    assert summary["atoms"] > 0


# -- APBS ----------------------------------------------------------------------


def test_apbs_is_reported_absent_when_no_binary_exists(monkeypatch):
    monkeypatch.setattr(
        "protean_mcp.analysis.electrostatics.shutil.which", lambda _: None
    )
    assert apbs_binary() is None


def test_apbs_is_reported_absent_when_the_binary_cannot_run(monkeypatch):
    """A package manager dropping a shared library leaves exactly this state."""
    monkeypatch.setattr(
        "protean_mcp.analysis.electrostatics.shutil.which", lambda _: "/nope/apbs"
    )

    def explode(*args, **kwargs):
        raise OSError("dyld: library not loaded")

    monkeypatch.setattr("protean_mcp.analysis.electrostatics.subprocess.run", explode)
    assert apbs_binary() is None


def test_run_apbs_without_a_binary_says_how_to_get_one(monkeypatch):
    monkeypatch.setattr(
        "protean_mcp.analysis.electrostatics.shutil.which", lambda _: None
    )
    with pytest.raises(ElectrostaticsError, match="conda-forge"):
        run_apbs(_charge((1.0, (0.0, 0.0, 0.0))))


@pytest.mark.skipif(
    os.environ.get("PROTEAN_APBS") != "1" or apbs_binary() is None,
    reason="needs a runnable APBS; set PROTEAN_APBS=1 to run",
)
def test_apbs_agrees_with_the_approximation_on_surface_shape(dipeptide):
    """The claim the default rests on: same shape, different magnitude."""
    prepared = prepare(dipeptide)
    approximate = coulombic(prepared, spacing=1.0)
    exact = run_apbs(prepared, spacing=1.0)

    shell = prepared.coords + np.array([0.0, 0.0, 4.0])
    a = sample(exact, shell)
    c = sample(approximate, shell)
    usable = ~np.isnan(a) & ~np.isnan(c)
    assert usable.sum() > 3
    assert np.corrcoef(a[usable], c[usable])[0, 1] > 0.8


def test_probing_for_apbs_leaves_no_files_behind(tmp_path, monkeypatch):
    """`apbs --version` writes an io.mc log into the working directory.

    Asking whether an optional tool is installed must not litter the caller's
    repository, so the probe runs somewhere disposable.
    """
    monkeypatch.chdir(tmp_path)
    apbs_binary()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(
    os.environ.get("PROTEAN_APBS") != "1" or apbs_binary() is None,
    reason="needs a runnable APBS; set PROTEAN_APBS=1 to run",
)
def test_running_apbs_leaves_no_files_behind(tmp_path, monkeypatch, dipeptide):
    monkeypatch.chdir(tmp_path)
    run_apbs(prepare(dipeptide), spacing=2.0, padding=6.0)
    assert list(tmp_path.iterdir()) == []
