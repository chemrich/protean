"""Reading trajectories, and refusing the pairings that would look fine.

A trajectory file is coordinates and nothing else. Everything that makes those
coordinates mean something — atom names, chains, elements — comes from a
separate structure, so the one check that matters is whether the two belong
together, and the only evidence available is the atom count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom
from biotite.structure import array as atom_array
from biotite.structure.io.dcd import DCDFile
from biotite.structure.io.xtc import XTCFile

from protean_mcp.analysis.trajectory import (
    TrajectoryError,
    read,
    rmsd_series,
    rmsf,
    superpose_frames,
    supported_formats,
)


def _template(atoms: int) -> Any:
    return atom_array(
        [
            Atom(
                [float(i), 0.0, 0.0],
                chain_id="A",
                res_id=i + 1,
                ins_code="",
                res_name="ALA",
                atom_name="CA",
                element="C",
                hetero=False,
                b_factor=0.0,
                occupancy=1.0,
                atom_id=i + 1,
            )
            for i in range(atoms)
        ]
    )


def _write(path: Path, frames: np.ndarray, cls=XTCFile) -> Path:
    handle = cls()
    handle.set_coord(frames.astype(np.float32))
    handle.write(str(path))
    return path


def _drifting(atoms: int, frames: int) -> np.ndarray:
    """Frames where atom 0 stays put and the last atom moves furthest."""
    base = _template(atoms).coord
    return np.stack(
        [
            base + np.linspace(0, step, atoms)[:, None] * [0.0, 1.0, 0.0]
            for step in range(frames)
        ]
    )


@pytest.mark.parametrize(("cls", "suffix"), [(XTCFile, ".xtc"), (DCDFile, ".dcd")])
def test_a_trajectory_round_trips_onto_its_template(tmp_path, cls, suffix):
    template = _template(5)
    frames = _drifting(5, 4)
    path = _write(tmp_path / f"run{suffix}", frames, cls)

    stack = read(str(path), template)
    assert stack.stack_depth() == 4
    assert stack.array_length() == 5
    # Atom identity comes from the template, coordinates from the file.
    assert stack.atom_name[0] == "CA"
    assert stack.coord[3][4] == pytest.approx(frames[3][4], abs=1e-3)


def test_a_trajectory_for_a_different_structure_is_refused(tmp_path):
    """The failure this whole module is arranged around.

    Six coordinates laid onto a five-atom structure parse cleanly and animate
    smoothly. The count is the only thing that can catch it, so it is checked
    rather than assumed.
    """
    path = _write(tmp_path / "wrong.xtc", _drifting(6, 3))
    with pytest.raises(
        TrajectoryError, match="6 atoms per frame but the loaded structure has 5"
    ):
        read(str(path), _template(5))


def test_stride_takes_every_nth_frame(tmp_path):
    path = _write(tmp_path / "run.xtc", _drifting(4, 10))
    assert read(str(path), _template(4), stride=3).stack_depth() == 4


def test_a_limit_truncates_rather_than_failing(tmp_path):
    path = _write(tmp_path / "run.xtc", _drifting(4, 10))
    assert read(str(path), _template(4), limit=3).stack_depth() == 3


def test_stride_and_limit_compose(tmp_path):
    path = _write(tmp_path / "run.xtc", _drifting(4, 20))
    assert read(str(path), _template(4), stride=2, limit=4).stack_depth() == 4


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(TrajectoryError, match="No trajectory at"):
        read(str(tmp_path / "absent.xtc"), _template(3))


def test_an_unreadable_format_says_what_it_does_read(tmp_path):
    unsupported = tmp_path / "run.mdcrd"
    unsupported.write_text("not a trajectory")
    with pytest.raises(TrajectoryError, match=r"Cannot read '.mdcrd'.*\.dcd"):
        read(str(unsupported), _template(3))


def test_a_zero_stride_is_refused(tmp_path):
    path = _write(tmp_path / "run.xtc", _drifting(4, 4))
    with pytest.raises(TrajectoryError, match="Stride must be 1 or more"):
        read(str(path), _template(4), stride=0)


def test_supported_formats_are_reported_sorted():
    assert supported_formats() == sorted(supported_formats())
    assert ".xtc" in supported_formats()


# -- RMSF ----------------------------------------------------------------------


def test_rmsf_is_zero_for_a_structure_that_never_moves(tmp_path):
    still = np.stack([_template(4).coord] * 5)
    path = _write(tmp_path / "still.xtc", still)
    assert rmsf(read(str(path), _template(4))) == pytest.approx(np.zeros(4), abs=1e-4)


def test_rmsf_rises_with_how_far_an_atom_moves(tmp_path):
    """The claim worth making: it ranks atoms by motion, not just by being nonzero."""
    path = _write(tmp_path / "run.xtc", _drifting(5, 8))
    values = rmsf(read(str(path), _template(5)))

    assert values[0] == pytest.approx(0.0, abs=1e-4)
    assert list(values) == sorted(values)
    assert values[4] > values[2] > values[1]


def test_rmsf_needs_more_than_one_frame(tmp_path):
    path = _write(tmp_path / "one.xtc", _drifting(4, 1))
    with pytest.raises(TrajectoryError, match="at least two frames"):
        rmsf(read(str(path), _template(4)))


def test_rmsf_is_measured_about_the_mean_position(tmp_path):
    """Pins the value, not just the ranking.

    An atom oscillating between -1 and +1 sits a distance of exactly 1 from its
    mean in every frame, so its RMSF is 1.0. Measured about the *first* frame
    instead — a plausible-looking mistake — the same motion reads as sqrt(2),
    and every ordering test still passes because the ranking is unchanged.
    """
    template = _template(2)
    still = template.coord.copy()
    swinging = np.stack(
        [
            still + np.array([[0.0, 0.0, 0.0], [0.0, -1.0, 0.0]]),
            still + np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        ]
    )
    path = _write(tmp_path / "swing.xtc", swinging)

    values = rmsf(read(str(path), template))
    assert values[0] == pytest.approx(0.0, abs=1e-4)
    assert values[1] == pytest.approx(1.0, abs=1e-3)


# -- superposition and RMSD ----------------------------------------------------


def test_superposing_removes_bulk_drift(tmp_path):
    """The correction that decides whether RMSF means anything.

    A molecule that merely slid across the box has moved enormously and
    fluctuated not at all. Measured without superposing, every atom reads as
    highly mobile — a confident wrong answer rather than an obviously broken
    one.
    """
    template = _template(6)
    slide = np.array([0.0, 10.0, 0.0])
    drifted = np.stack([template.coord + slide * step for step in range(5)])
    path = _write(tmp_path / "drift.xtc", drifted)
    stack = read(str(path), template)

    raw = rmsf(stack)
    corrected = rmsf(superpose_frames(stack))

    assert raw.max() > 5.0
    assert corrected.max() == pytest.approx(0.0, abs=1e-3)


def test_rmsd_is_zero_against_an_unchanging_frame(tmp_path):
    still = np.stack([_template(4).coord] * 4)
    path = _write(tmp_path / "still.xtc", still)
    assert rmsd_series(read(str(path), _template(4))) == pytest.approx(
        np.zeros(4), abs=1e-4
    )


def test_rmsd_grows_as_the_structure_departs(tmp_path):
    """Rigid drift superposes away, so the departure has to be a real change."""
    template = _template(6)
    frames = []
    for step in range(5):
        coord = template.coord.copy()
        coord[3:] += [0.0, float(step), 0.0]  # half the atoms move, half stay
        frames.append(coord)
    path = _write(tmp_path / "open.xtc", np.stack(frames))

    series = rmsd_series(read(str(path), template))
    assert series[0] == pytest.approx(0.0, abs=1e-4)
    assert list(series) == sorted(series)
    assert series[4] > series[1]


def test_a_reference_frame_outside_the_run_is_refused(tmp_path):
    path = _write(tmp_path / "run.xtc", _drifting(4, 3))
    with pytest.raises(TrajectoryError, match=r"outside 0\.\.2"):
        rmsd_series(read(str(path), _template(4)), reference=7)


def test_rmsd_can_be_measured_against_any_frame(tmp_path):
    """The last frame as reference puts the largest departure at the start."""
    template = _template(6)
    frames = []
    for step in range(4):
        coord = template.coord.copy()
        coord[3:] += [0.0, float(step), 0.0]
        frames.append(coord)
    path = _write(tmp_path / "open.xtc", np.stack(frames))
    stack = read(str(path), template)

    series = rmsd_series(stack, reference=3)
    assert series[3] == pytest.approx(0.0, abs=1e-4)
    assert series[0] > series[2]
