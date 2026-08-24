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

import itertools
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

# Hue is bucketed this finely when recovering an element's unshaded colour, and
# a bucket needs this many pixels before it is trusted to set a ceiling.
_HUE_BINS = 36
_MIN_HUE_SAMPLES = 64

# How many pixels the rank table is built from. The whole frame, up to this;
# beyond it a stride, because a figure-resolution capture is a hundred million
# pixels and the field only needs the shape of the distribution.
_RANK_SAMPLES = 400_000


@dataclass(frozen=True)
class _Frame:
    """One capture, reduced to what any finish needs to make marks from.

    Built once in `apply_finish` and handed to whichever style is drawing, so
    the two families cannot disagree about what the tone of a pixel is.
    """

    rgb: np.ndarray
    """Height x width x 3, 0-255, float."""

    luma: np.ndarray
    """Rec. 601 luma in 0-1: the weights the eye uses, so a saturated red reads
    as the mid-tone it looks like rather than as the light one its red channel
    says."""

    is_paper: np.ndarray
    """Where the capture is bright enough to take no ink at all."""

    shape: tuple[int, int]

    longest: float
    """The longer side, in pixels."""

    diagonal: float
    """`sqrt(width * height)`, in pixels.

    The better measure of "how big is this plate" for anything laid out in two
    dimensions. The longer side is right for a stroke field, which runs one
    way; it is wrong for a grain lattice, because a 1820x260 strip and a
    690x690 square have the same area and wildly different longest sides, so a
    lattice scaled by the longest side comes out seven times coarser on the
    strip. Both are scale-invariant, which is what the suite requires.
    """

    gap: float
    """Stroke spacing in pixels, either the caller's or scaled from the frame."""


@dataclass(frozen=True, kw_only=True)
class _Style:
    """What every finish declares, whatever it draws with."""

    bands: int
    """How many tones this finish separates.

    For an engraving that is the number of quantised bands; for a survey, the
    number of contour levels. Either way it is a checked claim rather than a
    tuning knob: the suite asserts the finish comes back with **at least**
    `bands + 1` distinguishable ink levels — the paper, plus one per band. A
    finish whose bands do not separate is not converting tone into line,
    whatever it looks like.

    A floor rather than an equality because a finish may grade finer than it
    promises: the engravings quantise and hit the number exactly, the survey
    grades continuously and measures in the hundreds.
    """

    paper: tuple[int, int, int] = (255, 255, 255)
    """The ground this finish prints on."""

    inks: tuple[tuple[int, int, int], ...] = ((0, 0, 0),)
    """The colours the marks are made in — a palette, not a colour.

    Declared per finish rather than assumed black, because the route carries
    finishes that are not. Everything downstream — the ink fraction reported to
    a caller who cannot look at the file, and the test that two finishes do not
    draw the same picture — reads this and the paper rather than testing for
    black, which was only ever black by coincidence.

    Plural because a print may lay down more than one. A finish that puts each
    ink on its own plate produces the overlaps as well, and the overlaps are
    where the third and fourth colours of a spot print come from — so what a
    finish may legitimately put on the page is not the list itself but
    `palette()`, which is derived from it.
    """

    @property
    def ink(self) -> tuple[int, int, int]:
        """The first ink, for the finishes that have only one."""
        return self.inks[0]

    def palette(self) -> frozenset[tuple[int, int, int]]:
        """Every colour this finish may legitimately print.

        The paper, each ink as itself, and a blend wherever two or more plates
        coincide — because that overlap is where a spot print's third and
        fourth colours come from. Derived rather than declared, so a finish
        cannot name one palette and print another, and so the suite can check
        the whole page against one rule.

        **An ink is laid as its own colour, not multiplied into the ground.**
        The first version of this multiplied every ink over the paper, which is
        right for a dark ink on white and wrong for `cyanotype`, whose ink is
        *lighter* than its ground — those white marks are unexposed paper
        showing through, not a transparent ink lying over blue. Caught by the
        suite: cyanotype started printing a colour its own palette disowned.
        Multiplying is reserved for where plates cross, which is the only place
        the physical model actually holds.

        For a finish with one ink this is exactly the two colours it always
        was: the paper, and the ink.
        """
        colours = {self.paper, *self.inks}
        for count in range(2, len(self.inks) + 1):
            for chosen in itertools.combinations(self.inks, count):
                colours.add(_multiply(chosen))
        return frozenset(colours)

    def __post_init__(self) -> None:
        """Refuse a malformed colour where it is written, not where it prints.

        Checked here rather than in `validate_finish` because construction is
        the earliest moment it can be caught, and the alternative is a
        `ValueError` about broadcasting shapes — or an `OverflowError` on a
        channel of 300 — arriving from inside numpy *after* a
        figure-resolution capture has already been paid for. Which is the same
        cost this module moved the name check to avoid.
        """
        roles: tuple[tuple[str, tuple[int, int, int]], ...] = (
            ("paper", self.paper),
            *((f"ink {n}", ink) for n, ink in enumerate(self.inks)),
        )
        if not self.inks:
            raise ValueError("A finish must declare at least one ink")
        for role, colour in roles:
            if len(colour) != _CHANNELS or not all(
                isinstance(c, int) and 0 <= c <= _FULL for c in colour
            ):
                raise ValueError(
                    f"A finish's {role} must be three channels of 0-255, not {colour!r}"
                )

    def marks(self, frame: _Frame) -> np.ndarray:
        """Where this finish puts ink. True is a mark."""
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class _Engraving(_Style):
    """Tone becomes line: the frame is banded, each band filled with strokes."""

    angles: tuple[float, ...]
    """Stroke directions in degrees. More than one crosses them."""

    cumulative: bool
    """Do darker bands *add* an angle, or reuse the same one more heavily?

    This is the difference between the two engraving styles rather than a
    tuning knob. Cross-hatching lays a second and third set of strokes across
    the first as the tone deepens; a hedcut keeps its one direction throughout
    and thickens the line instead, which is what makes it read as engraved
    rather than as sketched.
    """

    def marks(self, frame: _Frame) -> np.ndarray:
        # Band 0 is the lightest and takes no ink at all; the darkest band is
        # solid. Everything between is the number of strokes it earns.
        darkness = np.power(np.clip(1.0 - frame.luma, 0.0, 1.0), _TONE_CURVE)
        band = np.clip((darkness * self.bands).astype(int), 0, self.bands)
        band[frame.is_paper] = 0

        marks = np.zeros(frame.shape, dtype=bool)
        for level in range(1, self.bands + 1):
            here = band == level
            if not here.any():
                continue
            if self.cumulative:
                # Each darker band adds another direction across the last.
                for angle in self.angles[: min(level, len(self.angles))]:
                    marks |= here & _strokes(
                        frame.shape, angle, frame.gap, max(1.0, frame.gap * 0.30)
                    )
                if level >= self.bands:
                    marks |= here  # solid, past what crossing can carry
            else:
                # One direction throughout, the stroke swelling with the tone —
                # at the last band it closes up into solid ink on its own.
                width = frame.gap * (level / self.bands)
                marks |= here & _strokes(frame.shape, self.angles[0], frame.gap, width)
        return marks


@dataclass(frozen=True, kw_only=True)
class _Survey(_Style):
    """Tone becomes elevation: the frame is contoured like a landscape.

    On a spacefill model every atom is a lit dome, so its shading falls away
    from the highlight in nested rings, and tracing the boundaries between tone
    bands turns each atom into a contoured hill. The frame reads as a survey
    sheet of a landscape that happens to be a protein, which is not a picture
    any molecular viewer draws.

    Three kinds of mark, all in the same ink:

    - **contours**, isolines of elevation, drawn at constant width by dividing
      the distance to the level by the local slope, so a steep face gets a thin
      line rather than a fat smear;
    - **the shoreline**, every n-th contour drawn heavier, which is what makes
      the atoms read as separate hills rather than as one crumpled sheet;
    - **grain**, a jittered dot lattice whose dots swell with tone. This is the
      part that makes a *flat* tone grade, because a flat tone has no contours
      at all. A pure edge detector reads zero ink at every level of the ramp
      and fails the suite outright; the grain is the answer to that.
    """

    levels_index: int = 5
    """Every n-th contour is drawn heavy. At the default it is the outermost
    one alone: the shoreline where a dome meets what is behind it."""

    index_weight: float = 2.2
    """How much heavier a heavy contour is than an ordinary one."""

    line: float = 1 / 1200
    """Contour half-width, as a fraction of the frame."""

    halo: float = 1.8
    """Grain is cut back this many line-widths either side of a contour.

    The cartographer's mask. Without it the grain closes over the lines in the
    deep shadow between atoms, and the survey stops being readable exactly
    where it is most interesting.
    """

    pitch: float = 1 / 190
    """Grain lattice pitch, as a fraction of the frame. Fine on purpose: at
    three or four times this the dots read as a halftone screen and compete
    with the contours; here they read as a wash."""

    jitter: float = 0.22
    """Lattice jitter, as a fraction of the pitch. Keeps the grain off-grid."""

    lattice_angle: float = 33.0
    """Degrees. Off-axis, so the grain never resolves into scan lines."""

    smooth: float = 1 / 500
    """Blur applied before contouring, as a fraction of the frame. Looked at
    rather than derived: below this the rings break up on render noise, above
    it the smallest atoms lose their innermost ring."""

    tone_curve: float = 3.4
    """Darkness is raised to this power before it becomes grain coverage.

    Steep, on purpose. The grain is a supporting mark: at a mid grey it wants
    to be a tint rather than a fill, or it drowns the lines it is there to
    support.
    """

    brightest: float = 0.975
    """Shade is clipped here before the dome transform, whose slope runs away
    at 1 and would spray rings across every specular highlight."""

    flattest: float = 0.5
    """Ground that does not climb a whole band within this fraction of the
    frame draws no contour at all.

    Not a nicety. On perfectly flat ground the slope is zero and the residual
    is zero too, so the constant-width test reads zero-over-zero and floods the
    frame. Measured, on a grey square: the finish came back solid.
    """

    achromatic: float = 0.12
    """Below this saturation a pixel has no element colour to divide out."""

    def marks(self, frame: _Frame) -> np.ndarray:
        # Contouring the raw luma would count rings per element rather than per
        # atom: a spacefill render is coloured by element, so luma jumps at
        # every atom boundary and a blue atom spans twice the range of a green
        # one. Dividing out each element's own unshaded colour recovers the
        # lighting alone, which is what the elevation has to be.
        shade = _blur(_shade(frame.rgb, self.achromatic), frame.diagonal * self.smooth)

        # A lit sphere's shade is the cosine of the angle from the light, so
        # levels cut at equal brightness crowd into a solid rind at the rim and
        # leave the summit bare. Contouring the *radius* at which a level sits
        # on a sphere puts the rings an even distance apart instead. The
        # subject really is spheres, so this is a fact about the render.
        elevation = np.sqrt(1.0 - np.clip(shade, 0.0, self.brightest) ** 2)

        banded = elevation * self.bands
        nearest = np.rint(banded)
        gradient_y, gradient_x = np.gradient(banded)
        slope = np.hypot(gradient_x, gradient_y)

        # How far this pixel sits from the level crossing, in pixels: the
        # residual over how fast the ground is climbing. Constant line width
        # everywhere, and infinite where the ground is flat — which is why a
        # flat tone draws no contour and needs the grain below.
        with np.errstate(divide="ignore", invalid="ignore"):
            distance = np.abs(banded - nearest) / np.maximum(slope, 1e-9)

        line = max(0.75, frame.diagonal * self.line)
        heavy = (np.mod(nearest, self.levels_index) == 0) & (nearest > 0)
        half = np.where(heavy, line * self.index_weight, line)
        climbs = slope > self.bands / (frame.diagonal * self.flattest)
        contours = (distance < half) & climbs

        darkness = np.clip(1.0 - frame.luma, 0.0, 1.0) ** self.tone_curve
        grain = _grain(frame, self) < darkness
        grain &= ~((distance < half * self.halo) & climbs)

        return np.asarray((contours | grain) & ~frame.is_paper)


FINISHES: dict[str, _Style] = {
    "cross-hatch": _Engraving(angles=(45.0, -45.0, 90.0), cumulative=True, bands=4),
    "hedcut": _Engraving(angles=(75.0,), cumulative=False, bands=6),
    # Prussian blue and the white of unexposed paper — never pure 255, because
    # a cyanotype's highlight is paper rather than light.
    "cyanotype": _Survey(bands=5, paper=(17, 48, 92), inks=((238, 245, 252),)),
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


def _multiply(inks: tuple[tuple[int, int, int], ...]) -> tuple[int, int, int]:
    """The colour where plates cross: each ink multiplied into the last.

    Two spot inks crossing make a third colour rather than the second one
    winning, and that third colour is the reason a two-plate print looks like
    more than two.
    """
    out = [255.0, 255.0, 255.0]
    for ink in inks:
        out = [o * (c / 255.0) for o, c in zip(out, ink, strict=True)]
    first, second, third = (round(c) for c in out)
    return (first, second, third)


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


def _box(field: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """A box average of width 2*radius+1 along one axis, edge-clamped."""
    if radius < 1:
        return field
    pad = [(radius + 1, radius) if a == axis else (0, 0) for a in (0, 1)]
    summed = np.cumsum(np.pad(field, pad, mode="edge"), axis=axis)
    length = field.shape[axis]
    lo = np.take(summed, np.arange(0, length), axis=axis)
    hi = np.take(summed, np.arange(2 * radius + 1, length + 2 * radius + 1), axis=axis)
    return np.asarray((hi - lo) / (2 * radius + 1))


def _blur(field: np.ndarray, sigma: float) -> np.ndarray:
    """Three box passes, which is a Gaussian to the eye, and no scipy."""
    radius = round(sigma * 1.5)
    if radius < 1:
        return field
    out = field
    for _ in range(3):
        out = _box(_box(out, radius, 0), radius, 1)
    return out


def _hash01(a: np.ndarray, b: np.ndarray, salt: int) -> np.ndarray:
    """A repeatable value in [0, 1) per integer lattice cell.

    Repeatable matters: the same frame finished twice must jitter its grain the
    same way, or the frames of a turntable crawl.
    """
    h = (
        (a.astype(np.int64) * np.int64(73856093))
        ^ (b.astype(np.int64) * np.int64(19349663))
        ^ np.int64(salt * 83492791)
    )
    h &= np.int64(0x7FFFFFFF)
    h = ((h ^ (h >> 13)) * np.int64(1274126177)) & np.int64(0x7FFFFFFF)
    return np.asarray((h % np.int64(1000003)).astype(np.float64) / 1000003.0)


def _equalise(field: np.ndarray) -> np.ndarray:
    """Remap a field onto a uniform [0, 1), by rank.

    This is what makes the grain's coverage *exactly* the tone it was asked for
    rather than approximately: once the threshold field is uniform, `field < c`
    covers a fraction `c` of the frame by construction, for every c at once —
    which is the whole of "tone becomes density" in one line. Round dots come
    out anyway, because the field being equalised is the distance to the
    nearest lattice point.
    """
    flat = field.ravel()
    stride = max(1, flat.size // _RANK_SAMPLES)
    knots = np.sort(flat[::stride])
    # The midpoint of a tied run, not its start: a lattice repeats itself, so
    # exact ties are common and taking the low edge of each run biases the
    # whole field downward.
    lo = np.searchsorted(knots, flat, side="left")
    hi = np.searchsorted(knots, flat, side="right")
    ranks = (lo + hi) * 0.5
    return np.asarray((ranks / knots.size).clip(0.0, 1.0 - 1e-9).reshape(field.shape))


def _shade(rgb: np.ndarray, achromatic: float) -> np.ndarray:
    """The lighting alone, with the element colour divided out, in [0, 1].

    A spacefill render is coloured by element and shaded by multiplying that
    colour, so the brightest value seen at a hue is that element's unshaded
    colour and dividing by it recovers the shade. Measured on a real capture:
    saturation is flat at 0.82 across the whole molecule, which is what a
    multiply does and what makes the division sound. A pixel with no hue to
    divide out is taken at face value, which is what keeps a flat grey flat.
    """
    unit = rgb / 255.0
    value = unit.max(axis=2)
    low = unit.min(axis=2)
    saturation = np.where(value > 0.0, (value - low) / np.maximum(value, 1e-6), 0.0)
    coloured = saturation >= achromatic
    if not coloured.any():
        return np.asarray(value)

    r, g, b = unit[:, :, 0], unit[:, :, 1], unit[:, :, 2]
    span = np.maximum(value - low, 1e-6)
    hue = np.where(
        value == r,
        ((g - b) / span) % 6.0,
        np.where(value == g, (b - r) / span + 2.0, (r - g) / span + 4.0),
    )
    which = np.clip((hue / 6.0 * _HUE_BINS).astype(int), 0, _HUE_BINS - 1)

    # A high percentile rather than the max, so one stray specular pixel does
    # not set the ceiling for a whole element.
    ceiling = np.ones(_HUE_BINS)
    seen = np.zeros(_HUE_BINS, dtype=bool)
    for index in range(_HUE_BINS):
        here = coloured & (which == index)
        if here.sum() >= _MIN_HUE_SAMPLES:
            ceiling[index] = max(float(np.percentile(value[here], 99.0)), 1e-3)
            seen[index] = True
    if seen.any():
        # Carry a populated ceiling into the empty bins beside it, so a hue with
        # only a handful of pixels does not divide by 1.0 and read as a black
        # atom.
        bins = np.arange(_HUE_BINS)
        known = bins[seen]
        nearest = known[np.argmin(np.abs(bins[:, None] - known[None, :]), axis=1)]
        ceiling = np.where(seen, ceiling, ceiling[nearest])

    base = np.where(coloured, ceiling[which], 1.0)
    return np.asarray(np.clip(value / np.maximum(base, 1e-6), 0.0, 1.0))


def _grain(frame: _Frame, style: _Survey) -> np.ndarray:
    """A uniform [0, 1) threshold field whose level sets are round dots."""
    height, width = frame.shape
    pitch = max(2.0, frame.diagonal * style.pitch)
    radians = np.deg2rad(style.lattice_angle)
    y, x = np.ogrid[0:height, 0:width]
    u = (x * np.cos(radians) + y * np.sin(radians)) / pitch
    v = (-x * np.sin(radians) + y * np.cos(radians)) / pitch

    cu, cv = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    closest = np.full((height, width), np.inf)
    for du in (-1, 0, 1):
        for dv in (-1, 0, 1):
            au, av = cu + du, cv + dv
            spread = 2.0 * style.jitter
            ju = au + 0.5 + (_hash01(au, av, 1) - 0.5) * spread
            jv = av + 0.5 + (_hash01(au, av, 2) - 0.5) * spread
            closest = np.minimum(closest, np.hypot(u - ju, v - jv))
    return _equalise(closest)


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

    rgba = _rgba(image)
    pixels = np.asarray(rgba, dtype=np.float64)
    alpha = pixels[:, :, 3] / 255.0
    rgb = pixels[:, :, :3]
    # Rec. 601 luma: the weights the eye uses, so a saturated red reads as the
    # mid-tone it looks like rather than as the light one its red channel says.
    luma = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]) / 255.0
    shape = (rgba.height, rgba.width)

    frame = _Frame(
        rgb=rgb,
        luma=luma,
        is_paper=luma >= _PAPER,
        shape=shape,
        longest=float(max(shape)),
        diagonal=float(np.sqrt(rgba.width * rgba.height)),
        # Strokes want to be visibly apart: at 240ths of the frame they came
        # out about three pixels apart, and four crossed directions at that
        # spacing is not cross-hatching but a dot screen. Looked at, not
        # calculated.
        gap=spacing if spacing is not None else max(4.0, round(max(rgba.size) / 110)),
    )
    marks = style.marks(frame)

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
