"""Draw a capture again in ink, after the renderer has finished with it.

Mol\\* has no hatching, stippling or halftone: its whole post-processing
vocabulary is antialiasing, background, bloom, depth of field, occlusion,
outline, shadow and sharpening, checked across the tree rather than inferred.
Adding one would mean a custom render pass, which means building Mol\\* from
source, which this project does not do.

So this happens afterwards, on the pixels, which is where engravers worked
too. Tone becomes line: the image is banded by luminance and each band is
filled with strokes, more of them where it is darker. That is the whole idea,
and it is old — a woodcut has no greys either.

The cost of doing it here rather than in the renderer is that **the viewer
cannot show it**. There is no live preview and a caller sees it only in the
file, which is why `snapshot()` says the finish was applied after the capture
rather than leaving it to be inferred from the picture.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

__all__ = ["FINISHES", "apply_finish", "ink_fraction", "ink_mask", "validate_finish"]

# A pixel is part of the drawing if it is more opaque than not. Captures are
# antialiased, so the edge is a gradient and something has to decide where the
# molecule stops; half is the least arbitrary place to put it.
_OPAQUE_ENOUGH = 0.5

# An RGB colour is three channels, each a byte. Named so the check below reads
# as the rule it is rather than as two loose numbers.
_CHANNELS = 3
_FULL = 255

# Brighter than this is paper, and takes no ink at all.
#
# Not a nicety. A "white" ground is rarely 255 — the publication preset's is
# about 252 — so without a cutoff the lightest band catches the whole
# background and sprinkles strokes across the empty half of the frame. It
# looked like dirt on the plate.
_PAPER = 0.96

# Darkness is raised to this power before it is banded, which lightens the
# middle of the range without touching either end.
#
# Straight proportion put a mid grey two-thirds of the way to solid, so a
# cartoon whose shading sits mostly in the middle came out as a black mass
# with a few holes. Engravers do the same thing by hand and for the same
# reason: the eye reads an evenly-spaced open hatch as "mid tone" long before
# half the paper is covered.
_TONE_CURVE = 1.7


@dataclass(frozen=True)
class _Finish:
    """One engraving style: which way the strokes run, and how they thicken."""

    angles: tuple[float, ...]
    """Stroke directions in degrees. More than one crosses them."""

    cumulative: bool
    """Do darker bands *add* an angle, or reuse the same one more heavily?

    This is the difference between the two styles rather than a tuning knob.
    Cross-hatching lays a second and third set of strokes across the first as
    the tone deepens; a hedcut keeps its one direction throughout and thickens
    the line instead, which is what makes it read as engraved rather than as
    sketched.
    """

    bands: int
    """How many tones to quantise to. Fewer reads as a woodcut, more as a wash.

    A checked claim rather than a tuning knob: a finish must come back with
    exactly `bands + 1` distinguishable ink levels — the paper, plus one per
    band — and the suite asserts that. A finish whose bands do not separate is
    not converting tone into line, whatever it looks like.
    """

    paper: tuple[int, int, int] = (255, 255, 255)
    """The ground this finish prints on."""

    ink: tuple[int, int, int] = (0, 0, 0)
    """The colour the marks are made in.

    Declared per finish rather than assumed black, because the route is about
    to carry finishes that are not. Everything downstream — the ink fraction
    reported to a caller who cannot look at the file, and the test that two
    finishes do not draw the same picture — reads these two fields rather than
    testing for black, which was only ever black by coincidence.
    """

    def __post_init__(self) -> None:
        """Refuse a malformed colour where it is written, not where it prints.

        Checked here rather than in `validate_finish` because construction is
        the earliest moment it can be caught, and the alternative is a
        `ValueError` about broadcasting shapes — or an `OverflowError` on a
        channel of 300 — arriving from inside numpy *after* a
        figure-resolution capture has already been paid for. Which is the same
        cost this module moved the name check to avoid.
        """
        for role, colour in (("paper", self.paper), ("ink", self.ink)):
            if len(colour) != _CHANNELS or not all(
                isinstance(c, int) and 0 <= c <= _FULL for c in colour
            ):
                raise ValueError(
                    f"A finish's {role} must be three channels of 0-255, not {colour!r}"
                )


FINISHES: dict[str, _Finish] = {
    "cross-hatch": _Finish(angles=(45.0, -45.0, 90.0), cumulative=True, bands=4),
    "hedcut": _Finish(angles=(75.0,), cumulative=False, bands=6),
}


def validate_finish(finish: str) -> None:
    """Refuse an unknown finish, naming the ones that exist.

    Split out of `apply_finish` so `snapshot()` can call it *before* it asks
    the viewer for a capture. A finish is applied last, so a mistyped name used
    to cost a full figure-resolution render — up to a hundred seconds — before
    anything looked at the string. The check is free and the render is not.

    `ValueError`, not `KeyError`, because `str(KeyError(msg))` is `repr(msg)` —
    so the caller had to strip the quotes back off, and a name containing one
    came out mangled. Measured: a finish named `a'b` reached the model as
    `Unknown finish "a\\'b"`, backslash and all.
    """
    if finish not in FINISHES:
        raise ValueError(
            f"Unknown finish {finish!r}. Available: {', '.join(sorted(FINISHES))}"
        )


def _strokes(
    shape: tuple[int, int], angle: float, spacing: float, width: float
) -> np.ndarray:
    """A field of parallel lines: True on the ink, False between.

    A line is where the distance along the stroke normal comes back near zero
    modulo the spacing, which draws the whole field at once rather than a
    stroke at a time.
    """
    height, columns = shape
    y, x = np.ogrid[0:height, 0:columns]
    radians = np.deg2rad(angle)
    projected = x * np.cos(radians) + y * np.sin(radians)
    strokes: np.ndarray = (projected % spacing) < width
    return strokes


def apply_finish(
    image: Image.Image,
    finish: str,
    *,
    spacing: float | None = None,
) -> Image.Image:
    """Redraw *image* as strokes on paper, in the named style.

    The paper and the ink are the finish's own — white and black for the two
    engraving styles, and not necessarily for anything else.

    Transparent pixels stay transparent: a capture on a transparent canvas has
    no tone to engrave where there is nothing drawn, and filling it with the
    lightest band would put a hatched rectangle behind the molecule.

    spacing: pixels between strokes. Scaled from the image by default, so the
      result looks the same at 300 dpi and 600 rather than four times finer.
    """
    validate_finish(finish)
    style = FINISHES[finish]

    rgba = image.convert("RGBA")
    pixels = np.asarray(rgba, dtype=np.float64)
    alpha = pixels[:, :, 3] / 255.0
    # Rec. 601 luma: the weights the eye uses, so a saturated red reads as the
    # mid-tone it looks like rather than as the light one its red channel says.
    luma = (
        0.299 * pixels[:, :, 0] + 0.587 * pixels[:, :, 1] + 0.114 * pixels[:, :, 2]
    ) / 255.0

    # Strokes want to be visibly apart: at 240ths of the frame they came out
    # about three pixels apart, and four crossed directions at that spacing is
    # not cross-hatching but a dot screen. Looked at, not calculated.
    gap = spacing if spacing is not None else max(4.0, round(max(rgba.size) / 110))
    shape = (rgba.height, rgba.width)

    # Band 0 is the lightest and takes no ink at all; the darkest band is
    # solid. Everything between is the number of strokes it earns.
    darkness = np.power(np.clip(1.0 - luma, 0.0, 1.0), _TONE_CURVE)
    band = np.clip((darkness * style.bands).astype(int), 0, style.bands)
    band[luma >= _PAPER] = 0

    marks = np.zeros(shape, dtype=bool)
    for level in range(1, style.bands + 1):
        here = band == level
        if not here.any():
            continue
        if style.cumulative:
            # Each darker band adds another direction across the last.
            for angle in style.angles[: min(level, len(style.angles))]:
                marks |= here & _strokes(shape, angle, gap, max(1.0, gap * 0.30))
            if level >= style.bands:
                marks |= here  # solid, past what crossing can carry
        else:
            # One direction throughout, the stroke swelling with the tone —
            # at the last band it closes up into solid ink on its own.
            width = gap * (level / style.bands)
            marks |= here & _strokes(shape, style.angles[0], gap, width)

    out = np.empty((*shape, 4), dtype=np.uint8)
    out[:, :, :3] = np.where(
        marks[:, :, None],
        np.array(style.ink, dtype=np.uint8),
        np.array(style.paper, dtype=np.uint8),
    )
    out[:, :, 3] = np.where(alpha > _OPAQUE_ENOUGH, 255, 0).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def _rgba(image: Image.Image) -> Image.Image:
    """The image as RGBA, without copying one that already is.

    `convert` copies unconditionally, and these run over whole figures — a
    capture at the 120 MP ceiling is half a gigabyte before anything is asked
    of it.
    """
    return image if image.mode == "RGBA" else image.convert("RGBA")


def ink_mask(engraved: Image.Image, finish: str) -> np.ndarray:
    """Which drawn pixels carry ink: the ones that are not this finish's paper.

    Exact, with no tolerance, and that is a property of the route rather than
    luck. `apply_finish` writes each pixel as either the paper or the ink and
    nothing between — asserted by the suite — so "not the paper" recovers the
    mask it drew, bit for bit, rather than estimating it. An earlier version
    compared against black within a tolerance, which was both a guess and a
    guess about the wrong colour.

    Defined once because the tests need the same answer, and a second copy of a
    rule agrees with the first because it was copied, not because either is
    right.
    """
    validate_finish(finish)
    paper = np.array(FINISHES[finish].paper, dtype=np.uint8)
    pixels = np.asarray(_rgba(engraved))
    off_paper: np.ndarray = (pixels[:, :, :3] != paper).any(axis=2)
    return off_paper & (pixels[:, :, 3] > 0)


def ink_fraction(engraved: Image.Image, finish: str) -> float:
    """How much of the drawn area came back inked rather than bare paper.

    Reported because the caller is usually a model, which cannot look. A
    near-black ground engraves to almost solid ink — the molecule shows as a
    few light strokes in a filled rectangle — and the difference between that
    and a good print is not visible in a file size or a pixel count. A number
    near 1 says the tone had nowhere to go.

    Measured against *this finish's* paper, which is why it needs the name.
    The first version asked whether the red channel was zero, which is a test
    for black wearing a disguise: it happened to be right for both engraving
    styles because both print black on white, and it would have reported a
    finish printing in any other colour as a blank page or a solid one, at
    random, through the one number whose whole job is to tell those apart.
    """
    rgba = _rgba(engraved)
    drawn = np.asarray(rgba)[:, :, 3] > 0
    if not drawn.any():
        return 0.0
    return round(float(ink_mask(rgba, finish)[drawn].mean()), 3)
