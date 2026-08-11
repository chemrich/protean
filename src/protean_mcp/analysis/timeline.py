"""Interpolating a camera between keyframes.

Kept as pure functions over plain lists so the motion can be tested without a
browser: a camera path that cuts through the molecule or eases wrongly is a
geometry mistake, and geometry mistakes are much easier to see in numbers than
in a movie.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

Vector = list[float]

EASINGS = ("linear", "ease-in-out", "ease-in", "ease-out")

# Angles below this are the same direction as far as a camera is concerned, and
# the slerp weights go singular at both ends.
_COINCIDENT = 1e-6
# How parallel an axis has to be before it is a poor choice of perpendicular.
_TOO_PARALLEL = 0.9
# Two of each: fewer keyframes is a still, fewer frames is not a move.
_MIN_KEYFRAMES = 2
_MIN_FRAMES = 2


class TimelineError(Exception):
    """A timeline could not be built or played."""


def ease(fraction: float, kind: str = "ease-in-out") -> float:
    """Reshape 0..1 so a move starts and stops without a jolt.

    Linear motion between keyframes reads as mechanical precisely at the cuts,
    which is where the eye is already looking.
    """
    t = min(max(fraction, 0.0), 1.0)
    if kind == "linear":
        return t
    if kind == "ease-in":
        return t * t
    if kind == "ease-out":
        return t * (2 - t)
    if kind == "ease-in-out":
        # Smoothstep: zero first derivative at both ends.
        return t * t * (3 - 2 * t)
    raise TimelineError(f"Unknown easing {kind!r}. Available: {', '.join(EASINGS)}")


def slerp(start: Vector, end: Vector, fraction: float) -> Vector:
    """Interpolate a direction along the sphere, not through it.

    Interpolating camera positions linearly walks the camera along a chord —
    which for a half-turn passes through the middle of the molecule, and for
    any turn changes the distance on the way. Rotating along the arc keeps the
    subject the same size throughout, which is what makes a move read as a
    camera rather than a zoom.
    """
    a = np.asarray(start, dtype=float)
    b = np.asarray(end, dtype=float)
    length_a = float(np.linalg.norm(a))
    length_b = float(np.linalg.norm(b))
    if length_a == 0 or length_b == 0:
        raise TimelineError("Cannot interpolate a direction of zero length")

    unit_a, unit_b = a / length_a, b / length_b
    dot = float(np.clip(np.dot(unit_a, unit_b), -1.0, 1.0))
    angle = math.acos(dot)
    # Radius is interpolated separately, so a dolly and an orbit compose.
    radius = length_a + (length_b - length_a) * fraction

    if angle < _COINCIDENT:
        return list(unit_a * radius)
    if abs(angle - math.pi) < _COINCIDENT:
        # Antipodal: every arc is equally short, so pick one deterministically
        # rather than letting floating point choose a different plane per frame.
        axis = np.array([1.0, 0.0, 0.0])
        if abs(float(np.dot(axis, unit_a))) > _TOO_PARALLEL:
            axis = np.array([0.0, 1.0, 0.0])
        perpendicular = np.cross(unit_a, axis)
        perpendicular /= np.linalg.norm(perpendicular)
        turned = unit_a * math.cos(math.pi * fraction) + perpendicular * math.sin(
            math.pi * fraction
        )
        return list(turned * radius)

    sin_angle = math.sin(angle)
    weight_a = math.sin((1 - fraction) * angle) / sin_angle
    weight_b = math.sin(fraction * angle) / sin_angle
    return list((unit_a * weight_a + unit_b * weight_b) * radius)


def lerp(start: Vector, end: Vector, fraction: float) -> Vector:
    a = np.asarray(start, dtype=float)
    b = np.asarray(end, dtype=float)
    return list(a + (b - a) * fraction)


def between(
    first: dict[str, Any], second: dict[str, Any], fraction: float
) -> dict[str, Any]:
    """One camera state between two keyframes.

    The position is treated as an offset from the target and rotated along the
    arc; the target and up vector move straight. That combination is what makes
    a two-keyframe move look like a camera swinging around a subject rather
    than sliding past it.
    """
    target = lerp(first["target"], second["target"], fraction)
    offset_a = list(np.asarray(first["position"]) - np.asarray(first["target"]))
    offset_b = list(np.asarray(second["position"]) - np.asarray(second["target"]))
    offset = slerp(offset_a, offset_b, fraction)
    return {
        "position": list(np.asarray(target) + np.asarray(offset)),
        "target": target,
        "up": lerp(first["up"], second["up"], fraction),
    }


def path(
    keyframes: list[dict[str, Any]], frames: int, easing: str = "ease-in-out"
) -> list[dict[str, Any]]:
    """Every camera state for a run through *keyframes*.

    Frames are spread evenly across the segments rather than across the
    keyframes, so two keyframes far apart move faster than two close together —
    which is the behaviour a timeline of positions implies.
    """
    if len(keyframes) < _MIN_KEYFRAMES:
        raise TimelineError("A timeline needs at least two keyframes")
    if frames < _MIN_FRAMES:
        raise TimelineError(f"A timeline needs at least 2 frames, got {frames}")
    ease(0.0, easing)  # reject an unknown easing before rendering anything

    segments = len(keyframes) - 1
    states: list[dict[str, Any]] = []
    for index in range(frames):
        # The last frame lands exactly on the final keyframe rather than one
        # step short of it, so a loop closes and a still matches its keyframe.
        overall = index / (frames - 1)
        scaled = min(overall * segments, segments - 1e-9)
        segment = int(scaled)
        states.append(
            between(
                keyframes[segment], keyframes[segment + 1], ease(scaled - segment, easing)
            )
        )
    return states
