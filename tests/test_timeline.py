"""Camera interpolation, checked as numbers.

A camera path that cuts through the molecule, or changes its distance on the
way, is a geometry mistake — and geometry mistakes are far easier to see here
than in a movie, where they read as "something looks off".
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from protean_mcp.analysis.timeline import (
    TimelineError,
    between,
    ease,
    lerp,
    path,
    slerp,
)


def _length(vector: Any) -> float:
    return float(np.linalg.norm(np.asarray(vector, dtype=float)))


# -- easing --------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["linear", "ease-in", "ease-out", "ease-in-out"])
def test_every_easing_spans_zero_to_one(kind):
    assert ease(0.0, kind) == pytest.approx(0.0)
    assert ease(1.0, kind) == pytest.approx(1.0)


def test_ease_in_out_is_symmetric_and_slow_at_the_ends():
    """Smoothstep, which is what stops a move starting with a jolt."""
    assert ease(0.5) == pytest.approx(0.5)
    assert ease(0.1) < 0.1  # slower than linear at the start
    assert ease(0.9) > 0.9  # and at the end
    assert ease(0.25) + ease(0.75) == pytest.approx(1.0)


def test_easings_are_clamped_outside_the_unit_range():
    assert ease(-2.0) == pytest.approx(0.0)
    assert ease(4.0) == pytest.approx(1.0)


def test_an_unknown_easing_is_refused():
    with pytest.raises(TimelineError, match="Unknown easing"):
        ease(0.5, "bouncy")


# -- interpolation -------------------------------------------------------------


def test_slerp_keeps_the_camera_the_same_distance_away():
    """The reason this is not a lerp.

    Interpolating positions linearly walks the camera along a chord: a quarter
    turn between two points 10 away passes 7.07 away at the midpoint, so the
    subject swells and shrinks. Along the arc it stays put.
    """
    midpoint = slerp([10.0, 0.0, 0.0], [0.0, 0.0, 10.0], 0.5)
    assert _length(midpoint) == pytest.approx(10.0, abs=1e-9)

    chord = lerp([10.0, 0.0, 0.0], [0.0, 0.0, 10.0], 0.5)
    assert _length(chord) == pytest.approx(7.071, abs=1e-3)


def test_slerp_reaches_both_ends_exactly():
    start, end = [3.0, 0.0, 0.0], [0.0, 4.0, 0.0]
    assert slerp(start, end, 0.0) == pytest.approx(start)
    assert slerp(start, end, 1.0) == pytest.approx(end)


def test_slerp_turns_at_a_constant_rate():
    """Equal steps in fraction are equal steps in angle, which is what reads as
    a steady move rather than one that hurries through the middle."""
    start, end = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    angles = []
    for fraction in (0.25, 0.5, 0.75, 1.0):
        turned = np.asarray(slerp(start, end, fraction))
        angles.append(math.acos(float(np.clip(np.dot(turned, start), -1, 1))))
    steps = np.diff([0.0, *angles])
    assert steps == pytest.approx([math.pi / 8] * 4, abs=1e-9)


def test_slerp_interpolates_the_distance_as_well():
    """So a dolly and an orbit compose rather than one overriding the other."""
    assert _length(slerp([2.0, 0.0, 0.0], [0.0, 6.0, 0.0], 0.5)) == pytest.approx(4.0)


def test_slerp_handles_a_half_turn_deterministically():
    """Antipodal directions have infinitely many shortest arcs.

    Left to floating point the chosen plane can differ between frames, which
    shows up as a camera that jitters sideways through the middle of the move.
    """
    first = slerp([5.0, 0.0, 0.0], [-5.0, 0.0, 0.0], 0.5)
    second = slerp([5.0, 0.0, 0.0], [-5.0, 0.0, 0.0], 0.5)
    assert first == pytest.approx(second)
    assert _length(first) == pytest.approx(5.0)


def test_slerp_refuses_a_direction_of_no_length():
    with pytest.raises(TimelineError, match="zero length"):
        slerp([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], 0.5)


def test_between_swings_around_the_target_rather_than_past_it():
    """The composite claim: the subject stays framed throughout the move."""
    first = {
        "position": [10.0, 0.0, 0.0],
        "target": [0.0, 0.0, 0.0],
        "up": [0.0, 1.0, 0.0],
    }
    second = {
        "position": [0.0, 0.0, 10.0],
        "target": [0.0, 0.0, 0.0],
        "up": [0.0, 1.0, 0.0],
    }

    middle = between(first, second, 0.5)
    offset = np.asarray(middle["position"]) - np.asarray(middle["target"])
    assert _length(offset) == pytest.approx(10.0, abs=1e-9)


def test_between_moves_the_target_too():
    first = {
        "position": [10.0, 0.0, 0.0],
        "target": [0.0, 0.0, 0.0],
        "up": [0.0, 1.0, 0.0],
    }
    second = {
        "position": [14.0, 0.0, 0.0],
        "target": [4.0, 0.0, 0.0],
        "up": [0.0, 1.0, 0.0],
    }

    middle = between(first, second, 0.5)
    assert middle["target"] == pytest.approx([2.0, 0.0, 0.0])
    assert middle["position"] == pytest.approx([12.0, 0.0, 0.0])


# -- paths ---------------------------------------------------------------------


def _keyframe(position: Any, target: Any = (0.0, 0.0, 0.0)) -> dict[str, Any]:
    return {"position": list(position), "target": list(target), "up": [0.0, 1.0, 0.0]}


def test_a_path_starts_and_ends_on_its_keyframes():
    """So a still matches the keyframe it was set from, and a loop closes."""
    start = _keyframe((10.0, 0.0, 0.0))
    end = _keyframe((0.0, 0.0, 10.0))
    states = path([start, end], frames=9)

    assert states[0]["position"] == pytest.approx(start["position"])
    assert states[-1]["position"] == pytest.approx(end["position"], abs=1e-6)


def test_a_path_produces_exactly_the_frames_asked_for():
    assert len(path([_keyframe((5.0, 0, 0)), _keyframe((0, 0, 5.0))], frames=24)) == 24


def test_a_path_holds_the_distance_across_every_frame():
    """The invariant that separates an orbit from a slide, checked frame by
    frame rather than only at the midpoint."""
    states = path([_keyframe((8.0, 0, 0)), _keyframe((0, 0, 8.0))], frames=12)
    for state in states:
        offset = np.asarray(state["position"]) - np.asarray(state["target"])
        assert _length(offset) == pytest.approx(8.0, abs=1e-6)


def test_a_path_visits_every_keyframe_in_order():
    """Three keyframes, and the middle one has to actually appear."""
    keyframes = [
        _keyframe((10.0, 0.0, 0.0)),
        _keyframe((0.0, 10.0, 0.0)),
        _keyframe((0.0, 0.0, 10.0)),
    ]
    states = path(keyframes, frames=21)
    middle = states[10]["position"]
    assert middle == pytest.approx(keyframes[1]["position"], abs=1e-6)


def test_a_path_needs_two_keyframes():
    with pytest.raises(TimelineError, match="at least two keyframes"):
        path([_keyframe((1.0, 0, 0))], frames=10)


def test_a_path_needs_more_than_one_frame():
    with pytest.raises(TimelineError, match="at least 2 frames"):
        path([_keyframe((1.0, 0, 0)), _keyframe((0, 1.0, 0))], frames=1)


def test_a_path_refuses_an_unknown_easing_before_rendering_anything():
    with pytest.raises(TimelineError, match="Unknown easing"):
        path([_keyframe((1.0, 0, 0)), _keyframe((0, 1.0, 0))], frames=5, easing="springy")


def test_easing_changes_the_middle_but_not_the_ends():
    keyframes = [_keyframe((10.0, 0.0, 0.0)), _keyframe((0.0, 0.0, 10.0))]
    linear = path(keyframes, frames=11, easing="linear")
    eased = path(keyframes, frames=11, easing="ease-in-out")

    assert linear[0]["position"] == pytest.approx(eased[0]["position"])
    assert linear[-1]["position"] == pytest.approx(eased[-1]["position"], abs=1e-6)
    # A quarter of the way in, easing is still behind linear.
    assert linear[2]["position"] != pytest.approx(eased[2]["position"])
