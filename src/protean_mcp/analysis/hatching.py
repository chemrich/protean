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

__all__ = ["FINISHES", "apply_finish", "ink_fraction"]

# A pixel is part of the drawing if it is more opaque than not. Captures are
# antialiased, so the edge is a gradient and something has to decide where the
# molecule stops; half is the least arbitrary place to put it.
_OPAQUE_ENOUGH = 0.5

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
    """How many tones to quantise to. Fewer reads as a woodcut, more as a wash."""


FINISHES: dict[str, _Finish] = {
    "cross-hatch": _Finish(angles=(45.0, -45.0, 90.0), cumulative=True, bands=4),
    "hedcut": _Finish(angles=(75.0,), cumulative=False, bands=6),
}


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
    """Redraw *image* as strokes on white, in the named style.

    Transparent pixels stay transparent: a capture on a transparent canvas has
    no tone to engrave where there is nothing drawn, and filling it with the
    lightest band would put a hatched rectangle behind the molecule.

    spacing: pixels between strokes. Scaled from the image by default, so the
      result looks the same at 300 dpi and 600 rather than four times finer.
    """
    if finish not in FINISHES:
        raise KeyError(
            f"Unknown finish {finish!r}. Available: {', '.join(sorted(FINISHES))}"
        )
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

    ink = np.zeros(shape, dtype=bool)
    for level in range(1, style.bands + 1):
        here = band == level
        if not here.any():
            continue
        if style.cumulative:
            # Each darker band adds another direction across the last.
            for angle in style.angles[: min(level, len(style.angles))]:
                ink |= here & _strokes(shape, angle, gap, max(1.0, gap * 0.30))
            if level >= style.bands:
                ink |= here  # solid, past what crossing can carry
        else:
            # One direction throughout, the stroke swelling with the tone —
            # at the last band it closes up into solid ink on its own.
            width = gap * (level / style.bands)
            ink |= here & _strokes(shape, style.angles[0], gap, width)

    out = np.full((*shape, 4), 255, dtype=np.uint8)
    out[:, :, :3] = np.where(ink[:, :, None], 0, 255)
    out[:, :, 3] = np.where(alpha > _OPAQUE_ENOUGH, 255, 0).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA")


def ink_fraction(engraved: Image.Image) -> float:
    """How much of the drawn area came back black.

    Reported because the caller is usually a model, which cannot look. A
    near-black ground engraves to almost solid ink — the molecule shows as a
    few light strokes in a filled rectangle — and the difference between that
    and a good print is not visible in a file size or a pixel count. A number
    near 1 says the tone had nowhere to go.
    """
    pixels = np.asarray(engraved)
    drawn = pixels[:, :, 3] > 0
    if not drawn.any():
        return 0.0
    return round(float((pixels[:, :, 0][drawn] == 0).mean()), 3)
