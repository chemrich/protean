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

# It takes two of something to be a separation. Below this a plate print has
# nothing to sort, and everything goes to the key plate.
_A_SEPARATION = 2


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

    def plates(self, frame: _Frame) -> tuple[np.ndarray, ...]:
        """One mask per ink: what each plate of the press lays down.

        Most finishes are a single plate, and say so by leaving this alone —
        their `marks` is the whole print. A finish that separates its subject
        onto several plates overrides this instead, and then the overlaps are
        composited from `palette()` rather than by painting one plate over
        another.
        """
        return (self.marks(frame),)


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


@dataclass(frozen=True, kw_only=True)
class _Plates(_Style):
    """Spot inks, one plate each, screened at their own angle and off register.

    A separation rather than a rendering: the frame is sorted into as many
    colour families as there are inks, each family is screened onto its own
    plate, and the plates are printed a little out of register the way a small
    press prints. Where two plates cross, the inks multiply and make a colour
    neither carries alone — which is the whole reason a two-ink print looks
    like more than two.

    **What it binds is which plate a region prints on**, which is a category
    rather than a shade, and that is deliberate. A shade-driven binding — dot
    area from a measurement — sits downstream of the lighting rig, so the
    screen converts *shading* to dot area and the measurement never reaches the
    page. That is the trap `docs/bakeoff.md` fell into. A plate assignment
    cannot be quantised away by shading, because shading multiplies brightness
    and leaves hue alone.

    **It reads the colours already in the render, and claims no more than
    that.** Colour by element — which is the default for a spacefill — and the
    plates are elements. Colour by chain and they are chains. The finish sees
    pixels and cannot know what a hue was made to mean, so it does not pretend
    to: the reply reports how many plates were used and what share of the
    drawing each took, and the docstring says what the binding actually is.
    """

    angles: tuple[float, ...] = (15.0, 75.0, 45.0)
    """Screen angle per plate, in degrees.

    The printer's angles, thirty degrees apart — but **not for the printer's
    reason**. A press separates its screens to stop them beating into a moire,
    and that hardly applies here: the plates carry different *regions* rather
    than different tonal channels, so they barely overlap, and a round dot is
    isotropic enough that rotating its lattice changes no directional statistic
    I could measure. Two plates at the same angle came back identical on every
    number I tried.

    They earn their place by eye and only by eye: with its own angle each plate
    reads as its own screen, and the blue lattice visibly runs across the red
    instead of lying parallel to it. Verified by looking at a 3x crop, and
    **untested** — a mutation setting every plate to one angle passes the whole
    suite.
    """

    offset: float = 1 / 300
    """How far each plate sits out of register, as a fraction of the frame.

    The defining artefact. Too little and the print is merely a halftone; too
    much and it reads as a fault rather than as a hand-fed press. Each plate
    is pushed a different way so the error does not look like one global
    shift.
    """

    pitch: float = 1 / 110
    """Distance between dot centres, as a fraction of the frame. Coarse on
    purpose: a screen fine enough to disappear is a photograph, and the dots
    are meant to be seen."""

    grain: float = 0.10
    """How much the dot threshold is roughed up, as a fraction of its range.

    A small press does not lay a perfect dot. Enough to break the edge of each
    dot, not enough to break the tone.
    """

    tone_curve: float = 1.35
    """Darkness is raised to this power before it becomes dot area."""

    achromatic: float = 0.10
    """Below this saturation a pixel has no colour family to sort by, and goes
    to the first plate — the key, in the way a woodblock's key block carries
    everything the colour blocks do not."""

    def plates(self, frame: _Frame) -> tuple[np.ndarray, ...]:
        family = _families(frame.rgb, len(self.inks), self.achromatic)
        darkness = np.clip(1.0 - frame.luma, 0.0, 1.0) ** self.tone_curve
        drawn = ~frame.is_paper

        made = []
        for index in range(len(self.inks)):
            angle = self.angles[index % len(self.angles)]
            # Each plate pushed its own way, so the misregistration reads as a
            # press rather than as one global shift of the whole image.
            step = frame.diagonal * self.offset
            radians = np.deg2rad(120.0 * index)
            shift = (step * float(np.cos(radians)), step * float(np.sin(radians)))
            screen = _screen(frame, self.pitch, angle, shift)
            if self.grain:
                rough = _hash01(
                    np.arange(frame.shape[0])[:, None],
                    np.arange(frame.shape[1])[None, :],
                    7 + index,
                )
                screen = np.clip(screen + (rough - 0.5) * self.grain, 0.0, 1.0)
            # The plate carries its own separation off register, not just its
            # own screen. Shifting the screen alone moves the dots and leaves
            # the *regions* pinned, so no two plates could ever cover the same
            # pixel and the crossing colour — the point of printing in two inks
            # — could never appear. Shifted together, the plates disagree about
            # where a region ends, and the overlap shows up as a fringe along
            # every boundary. Which is exactly what misregistration looks like
            # on paper.
            mine = np.roll(
                family == index, (round(shift[1]), round(shift[0])), axis=(0, 1)
            )
            made.append((screen < darkness) & drawn & mine)
        return tuple(made)

    def marks(self, frame: _Frame) -> np.ndarray:
        """Every plate at once, for anything that wants the ink as a whole."""
        covered = np.zeros(frame.shape, dtype=bool)
        for plate in self.plates(frame):
            covered |= plate
        return covered


FINISHES: dict[str, _Style] = {
    "cross-hatch": _Engraving(angles=(45.0, -45.0, 90.0), cumulative=True, bands=4),
    "hedcut": _Engraving(angles=(75.0,), cumulative=False, bands=6),
    # Prussian blue and the white of unexposed paper — never pure 255, because
    # a cyanotype's highlight is paper rather than light.
    "cyanotype": _Survey(bands=5, paper=(17, 48, 92), inks=((238, 245, 252),)),
    # Madder and indigo on a warm white, from the plan's dyed-wool palette —
    # two inks a small press could actually mix, rather than process colours.
    # Their crossing is a near-black with a plum cast, which is what a
    # two-colour print gives you instead of a real black.
    "spot-ink-plates": _Plates(
        bands=5,
        paper=(247, 243, 233),
        inks=((163, 41, 38), (41, 61, 107)),
    ),
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


def _screen(
    frame: _Frame, pitch: float, angle: float, shift: tuple[float, float]
) -> np.ndarray:
    """A halftone screen: a uniform [0, 1) field whose level sets are dots.

    Ruled rather than jittered, because a screen is a screen — the regularity
    is what makes the rosette where two of them cross, and jitter would turn a
    press into a sandstorm. Rank-equalised for the same reason the survey's
    grain is: once the field is uniform, `field < c` covers exactly a fraction
    `c`, so dot area *is* tone rather than approximately tone.
    """
    height, width = frame.shape
    step = max(2.0, frame.diagonal * pitch)
    radians = np.deg2rad(angle)
    y, x = np.ogrid[0:height, 0:width]
    ox, oy = shift
    u = ((x - ox) * np.cos(radians) + (y - oy) * np.sin(radians)) / step
    v = (-(x - ox) * np.sin(radians) + (y - oy) * np.cos(radians)) / step
    # Distance to the nearest cell centre, which makes the level sets circles.
    du = u - np.floor(u) - 0.5
    dv = v - np.floor(v) - 0.5
    return _equalise(np.hypot(du, dv))


def _families(rgb: np.ndarray, count: int, achromatic: float) -> np.ndarray:
    """Sort each pixel into one of `count` colour families, by hue.

    The separation a plate print needs. A render coloured by element has a
    handful of well-parted hues — carbon, oxygen, nitrogen — so the dominant
    peaks of the hue histogram *are* the elements, and sorting by nearest peak
    recovers them without the finish being told what they mean.

    A pixel too grey to have a hue goes to family 0, the key plate. So does
    every pixel when the render is greyscale, which is the honest answer: a
    picture with one colour family separates onto one plate, and the reply says
    so rather than inventing three.
    """
    unit = rgb / 255.0
    value = unit.max(axis=2)
    low = unit.min(axis=2)
    saturation = np.where(value > 0.0, (value - low) / np.maximum(value, 1e-6), 0.0)
    coloured = saturation >= achromatic

    family = np.zeros(rgb.shape[:2], dtype=np.int64)
    if count < _A_SEPARATION or not coloured.any():
        return family

    r, g, b = unit[:, :, 0], unit[:, :, 1], unit[:, :, 2]
    span = np.maximum(value - low, 1e-6)
    hue = (
        np.where(
            value == r,
            ((g - b) / span) % 6.0,
            np.where(value == g, (b - r) / span + 2.0, (r - g) / span + 4.0),
        )
        / 6.0
    )

    # The dominant hues, taken greedily and kept apart, so two bins either side
    # of one peak do not come back as two families.
    counts, edges = np.histogram(hue[coloured], bins=_HUE_BINS, range=(0.0, 1.0))
    centres = (edges[:-1] + edges[1:]) / 2.0
    peaks: list[float] = []
    for index in np.argsort(counts)[::-1]:
        if counts[index] == 0:
            break
        here = float(centres[index])
        apart = min((min(abs(here - p), 1.0 - abs(here - p)) for p in peaks), default=1.0)
        if apart > 1.0 / (2 * count):
            peaks.append(here)
        if len(peaks) == count:
            break
    if len(peaks) < _A_SEPARATION:
        return family

    distance = np.stack(
        [np.minimum(np.abs(hue - p), 1.0 - np.abs(hue - p)) for p in peaks], axis=0
    )
    nearest = np.argmin(distance, axis=0)
    family[coloured] = nearest[coloured]
    return family


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
    plates = style.plates(frame)

    # Which plates cover each pixel, as a bit per plate. A print is composited
    # by asking that question once rather than by laying each plate down in
    # turn: an ink is its own colour where it prints alone and a blend where it
    # crosses another, and painting them in sequence would give the last plate
    # the crossing instead.
    code = np.zeros(shape, dtype=np.int64)
    for index, plate in enumerate(plates):
        code |= plate.astype(np.int64) << index

    out = np.empty((*shape, 4), dtype=np.uint8)
    out[:, :, :3] = np.array(style.paper, dtype=np.uint8)
    for covering in range(1, 1 << len(plates)):
        here = code == covering
        if not here.any():
            continue
        crossed = tuple(
            ink for index, ink in enumerate(style.inks) if covering >> index & 1
        )
        colour = crossed[0] if len(crossed) == 1 else _multiply(crossed)
        out[:, :, :3][here] = np.array(colour, dtype=np.uint8)

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
