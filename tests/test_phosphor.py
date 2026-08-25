"""The long exposure, on sequences whose answer we set.

Pure image processing, like `test_hatching.py` and for the same reason: the
claim is about what accumulating frames does, and a browser would only add ways
for the measurement to be wrong.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from PIL import Image

from protean_mcp.analysis.phosphor import accumulate, smear

STILL_AT = 30
MOVES_TO = 170
RADIUS = 12
SIZE = 200
#: Far enough apart that no two poses touch. At the 6 px steps this was first
#: written with, the probe discs for adjacent poses overlapped and two of the
#: three came back identical — so "the trail fades" could not be measured at
#: all, and the assertion that was supposed to check it passed on a flat
#: average. Poses 30 px apart with a 12 px radius leave clean gaps.
STEPS = (-90, -60, -30, 0)
#: Between the two subjects, so each half of the frame holds exactly one of
#: them. The first version of this test measured discs of radius 45 around each
#: centre, which **overlap** — the moving atom's trail reached into the still
#: atom's disc and read as 0.06 of trail on a subject that never moved. The
#: measurement was wrong, not the code.
DIVIDE = 55


def _frame(shift: int) -> Image.Image:
    """Two atoms on paper: one that never moves, one that travels."""
    pixels = np.full((SIZE, SIZE, 4), (250, 248, 244, 255), dtype=np.uint8)
    y, x = np.ogrid[0:SIZE, 0:SIZE]
    pixels[np.hypot(x - STILL_AT, y - 100) < RADIUS] = (40, 90, 60, 255)
    pixels[np.hypot(x - (MOVES_TO + shift), y - 100) < RADIUS] = (150, 50, 45, 255)
    return Image.fromarray(pixels, "RGBA")


def _travelling() -> list[Image.Image]:
    return [_frame(shift) for shift in STEPS]


def _trail(exposure: Image.Image, sharp: Image.Image) -> np.ndarray:
    """Where the exposure differs from the pose that ended it."""
    a = np.asarray(exposure)[:, :, :3].astype(int)
    b = np.asarray(sharp)[:, :, :3].astype(int)
    return np.asarray(np.abs(a - b).max(axis=2) > 8)


def _halves(mask: np.ndarray) -> tuple[float, float]:
    columns = np.broadcast_to(np.arange(SIZE)[None, :], mask.shape)
    return float(mask[columns < DIVIDE].mean()), float(mask[columns >= DIVIDE].mean())


def test_the_trail_lands_only_on_what_moved():
    """The claim, and the reason this exists at all.

    `boil`'s wobble follows how sure the data is about each atom, and that
    binding is real and separately tested — but it cannot be seen in any single
    frame, because one frame of a boil is just the molecule slightly displaced.
    Accumulating turns certainty into shape. So what has to be true is that the
    smear falls where the motion was and nowhere else.

    Measured on this fixture: the half holding the still atom reads **exactly
    zero** trail. Not a margin — a subject that did not move contributes
    nothing at all.
    """
    frames = _travelling()
    exposure = accumulate(frames)

    still, moving = _halves(_trail(exposure, frames[-1]))

    assert still == 0.0, f"an atom that never moved left a trail of {still}"
    assert moving > 0.01, f"the atom that travelled left almost nothing: {moving}"


def test_a_sequence_that_never_moves_reads_exactly_zero():
    """The control, and the honest answer on a structure whose column is flat.

    A boil with nothing to bind to draws the same pose every time. Its exposure
    is the still, and `smear` says so in a number rather than handing back a
    picture that looks fine and means nothing — which is the shape of this
    project's one retraction.
    """
    frozen = [_frame(0) for _ in range(4)]

    assert smear(accumulate(frozen), frozen[-1]) == 0.0


def test_the_newest_pose_stays_sharp():
    """A trail needs a head, or it reads as blur rather than as travel.

    This is what decay buys over a flat average. Averaging four frames leaves
    the last pose at a quarter strength and the subject is a smudge; weighting
    it fully keeps it the subject and puts the history behind it.
    """
    frames = _travelling()
    exposure = np.asarray(accumulate(frames))[:, :, :3].astype(int)
    last = np.asarray(frames[-1])[:, :, :3].astype(int)

    y, x = np.ogrid[0:SIZE, 0:SIZE]
    head = np.hypot(x - MOVES_TO, y - 100) < RADIUS - 4

    assert np.array_equal(exposure[head], last[head]), (
        "the last pose was diluted by the ones before it"
    )


def test_the_trail_fades_backwards():
    """Older poses contribute less, which is what makes the smear directional.

    Asserted as an ordering over the frames rather than on any one weight: the
    further back a pose is, the closer the exposure sits to the paper where
    only that pose drew.
    """
    frames = _travelling()
    exposure = np.asarray(accumulate(frames))[:, :, :3].astype(float)
    paper = np.array([250.0, 248.0, 244.0])

    distances = []
    for shift in STEPS[:-1]:
        y, x = np.ogrid[0:SIZE, 0:SIZE]
        only_here = np.hypot(x - (MOVES_TO + shift), y - 100) < RADIUS - 4
        distances.append(float(np.abs(exposure[only_here] - paper).mean()))

    # **Strictly** increasing, not merely sorted. `sorted()` accepts a run of
    # identical values, so a flat average — every pose composited at full
    # strength — passed this assertion while being exactly the thing it exists
    # to reject. That is the same trap `test_hatching.py` records in the
    # tone-ramp test, repeated here by the person who fixed it there.
    assert all(a < b for a, b in itertools.pairwise(distances)), (
        f"the trail does not fade with age: {[round(d, 1) for d in distances]}"
    )


def test_the_trail_is_not_clipped_to_the_last_pose():
    """A capture on a transparent canvas draws nothing outside the molecule,
    so the exposure has to carry alpha forward from every frame or the trail is
    cut to the silhouette of the pose that ended it — which is precisely the
    part of the picture this exists to show.

    Invisible on an opaque fixture, which is why the first version of this file
    could not see it: every frame was alpha 255 everywhere, so clipping to the
    last pose changed nothing and the mutation passed.
    """
    frames = []
    for shift in STEPS:
        pixels = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
        y, x = np.ogrid[0:SIZE, 0:SIZE]
        on = np.hypot(x - (MOVES_TO + shift), y - 100) < RADIUS
        pixels[on] = (150, 50, 45, 255)
        frames.append(Image.fromarray(pixels, "RGBA"))

    exposure = np.asarray(accumulate(frames))
    drawn = exposure[:, :, 3] > 0
    last = np.asarray(frames[-1])[:, :, 3] > 0

    assert drawn.sum() > last.sum() * 1.5, (
        f"the exposure covers {drawn.sum()} pixels against the last pose's "
        f"{last.sum()} — the trail was clipped away"
    )

    # And the last pose keeps its colour. A transparent capture carries RGB
    # (0, 0, 0) wherever nothing was drawn, so an older frame's *background*
    # used to composite its own blackness over the newest pose and turn a red
    # atom to mud — a real bug, and one this file could not see while every
    # fixture in it was opaque. `smear` read exactly 1.0, meaning the whole
    # drawing had changed, which is what gave it away.
    y, x = np.ogrid[0:SIZE, 0:SIZE]
    head = np.hypot(x - MOVES_TO, y - 100) < RADIUS - 3
    sharp = np.asarray(frames[-1])

    assert np.array_equal(exposure[head][:, :3], sharp[head][:, :3]), (
        f"the newest pose came back as {exposure[head][0][:3].tolist()} where "
        f"it was drawn {sharp[head][0][:3].tolist()}"
    )

    # Trail as a share of the *drawing*, not of the frame. Four poses, one
    # sharp: 0.75. Measured over the whole frame instead it reads 0.0437, and
    # a caller told "4% of this is trail" about a picture that is three
    # quarters trail has been told something false.
    assert smear(accumulate(frames), frames[-1]) == 0.75


def test_a_dark_ground_accumulates_the_other_way():
    """Ink darkens paper; phosphor brightens a screen. Compositing a dark scene
    the light way would take the *darkest* value everywhere and erase the
    trail into the background it is supposed to glow against."""
    size = 120
    frames = []
    for shift in (-12, -6, 0):
        pixels = np.zeros((size, size, 4), dtype=np.uint8)
        pixels[:, :, 3] = 255
        y, x = np.ogrid[0:size, 0:size]
        pixels[np.hypot(x - (70 + shift), y - 60) < 14] = (90, 220, 140, 255)
        frames.append(Image.fromarray(pixels, "RGBA"))

    on_dark = np.asarray(accumulate(frames, ground="dark"))[:, :, :3].astype(int)
    behind = np.asarray(frames[-1])[:, :, :3].astype(int)

    # Where an older pose drew and the last one did not: on a dark ground that
    # is trail, and it has to be *brighter* than the black behind it. The
    # light rule takes the darkest value at every pixel, so it returns the
    # background there and the trail is gone.
    y, x = np.ogrid[0:size, 0:size]
    older = np.asarray(np.hypot(x - 58, y - 60) < 10) & ~(np.hypot(x - 70, y - 60) < 14)

    assert on_dark[older].mean() > behind[older].mean() + 8, (
        f"the trail read {on_dark[older].mean():.1f} against a ground at "
        f"{behind[older].mean():.1f} — it was composited the wrong way and "
        "swallowed by the background"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"decay": 0.0}, "between 0 and 1"),
        ({"decay": 1.0}, "between 0 and 1"),
        ({"ground": "beige"}, "'light' or 'dark'"),
    ],
)
def test_a_bad_exposure_is_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        accumulate(_travelling(), **kwargs)


def test_an_empty_sequence_is_refused():
    with pytest.raises(ValueError, match="at least one frame"):
        accumulate([])


def test_frames_of_different_sizes_are_refused():
    """Silently resizing or cropping would compose a picture out of two
    different scenes and report a smear for it."""
    with pytest.raises(ValueError, match="not all the same size"):
        accumulate([_frame(0), _frame(0).resize((100, 100))])
