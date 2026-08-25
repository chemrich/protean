"""Accumulate a sequence of frames into one long exposure.

A boil redraws the molecule every few frames with the atoms nudged, and how far
an atom wanders follows how sure the data is about it. Played back that is
motion; held open on one plate it is a **photograph of the motion** — the parts
the data is sure of stay sharp, and the parts it is guessing at smear.

Which turns a channel that could only be seen by watching into one that can be
seen at a glance, and measured off a single still. That is the whole reason
this exists: `boil`'s binding is real and proven, and it is invisible in any
one frame.

Decay rather than a flat average. A flat average is a long exposure with the
shutter open the whole time, and it makes a moving atom uniformly faint — the
smear has no direction and the eye reads it as blur rather than as travel.
Weighting the last pose most and fading backwards gives the smear a head and a
tail, which is what a phosphor does and what makes a trail read as one.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

__all__ = ["accumulate", "smear"]

# Below this weight a frame contributes less than a quantisation step to an
# 8-bit channel, so carrying it further only costs time.
_NEGLIGIBLE = 1.0 / 512.0

# A channel has to move by more than this before a pixel counts as trail rather
# than as the pose that made it. Eight of 255 is the same judgement the finish
# route reached for the same reason: below it, a difference is not a mark.
_MOVED = 8


def accumulate(
    frames: list[Image.Image], *, decay: float = 0.72, ground: str = "light"
) -> Image.Image:
    """One image: the last frame at full strength, the ones before it fading.

    decay: how much of its weight each step back into the past keeps. At 0.72
      the fifth frame back contributes about a fifth of the newest one, which
      is a trail long enough to read and short enough to keep the newest pose
      the subject.
    ground: "light" composites the way paint does, taking the darkest value
      each frame offers, so a trail on white paper reads as a smudge. "dark"
      takes the brightest, which is what a phosphor does on a black screen.
      Averaging instead would wash the newest pose out towards the mean of the
      whole sequence and lose the sharp head the trail needs.
    """
    if not frames:
        raise ValueError("A long exposure needs at least one frame")
    if not 0.0 < decay < 1.0:
        raise ValueError(f"decay must be between 0 and 1, exclusive, got {decay:g}")
    if ground not in ("light", "dark"):
        raise ValueError(f"ground must be 'light' or 'dark', got {ground!r}")

    size = frames[0].size
    for frame in frames:
        if frame.size != size:
            raise ValueError(
                f"The frames are not all the same size: {size} and {frame.size}"
            )

    # Newest first, so the weight is decay to the power of how old a frame is.
    newest = np.asarray(frames[-1].convert("RGBA"), dtype=np.float64)
    out = newest.copy()
    paper = 255.0 if ground == "light" else 0.0

    for age, frame in enumerate(reversed(frames[:-1]), start=1):
        weight = decay**age
        if weight < _NEGLIGIBLE:
            break
        pixels = np.asarray(frame.convert("RGBA"), dtype=np.float64)
        # Fade the frame towards the paper before compositing, so an old pose
        # arrives as a faint version of itself rather than as a solid one that
        # happens to lose the comparison.
        faded = pixels[:, :, :3] * weight + paper * (1.0 - weight)
        # Only where the older frame actually drew. A transparent capture
        # carries RGB (0, 0, 0) wherever nothing was drawn, and faded towards
        # white that is still dark enough to win a `minimum` — so an untouched
        # background composited its own blackness over the last pose and turned
        # a red atom to mud. Measured before the fix: `smear` read exactly 1.0
        # on a transparent sequence, meaning the whole drawing had changed,
        # which is what sent me looking.
        drew = (pixels[:, :, 3] > 0)[:, :, None]
        blend = np.minimum if ground == "light" else np.maximum
        out[:, :, :3] = np.where(drew, blend(out[:, :, :3], faded), out[:, :, :3])
        # Anything drawn in any frame is drawn in the exposure, or a trail
        # would be clipped to the silhouette of the last pose — which is
        # exactly the part of the picture this is trying to show.
        out[:, :, 3] = np.maximum(out[:, :, 3], pixels[:, :, 3] * weight)

    return Image.fromarray(out.round().clip(0, 255).astype(np.uint8), "RGBA")


def smear(exposure: Image.Image, sharp: Image.Image) -> float:
    """How much of the exposure is trail rather than the pose that made it.

    The number a caller gets who cannot look at the file. Near zero means
    nothing moved and the long exposure is the still — which is the honest
    result on a structure whose column is flat, and a thing worth being told
    rather than left to infer from a picture that looks fine.
    """
    a = np.asarray(exposure.convert("RGBA"), dtype=np.int16)
    b = np.asarray(sharp.convert("RGBA"), dtype=np.int16)
    if a.shape != b.shape:
        raise ValueError("The exposure and the frame are different sizes")
    drawn = (a[:, :, 3] > 0) | (b[:, :, 3] > 0)
    if not drawn.any():
        return 0.0
    moved = (np.abs(a[:, :, :3] - b[:, :, :3]).max(axis=2) > _MOVED) | (
        np.abs(a[:, :, 3] - b[:, :, 3]) > _MOVED
    )
    return round(float(moved[drawn].mean()), 4)
