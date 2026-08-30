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

# `hedcut`'s stroke interval, as a fraction of the longest side: 5 px on a
# 1890 px plate, against the 17 it drew at before.
#
# "Hedcut is also way too coarse" was half of one observation, and this is the
# half of the answer that is only about size — the mechanism is the style and
# is kept. Chosen by looking at 17 / 8 / 6 / 5 at plate size: 17 is bars, 8 is
# bold but readable, 6 keeps the width modulation visible, and 5 gives the
# smoothest gradation while the stroke is still plainly a stroke. Below it the
# swelling that carries the tone has too little room, since the lightest band's
# width is the interval over `bands`.
#
# It also leaves the three hatching finishes at three distinct marks — 6 px for
# `linear-hatch`, 5 here, 4 for `cross-hatch` — so the choice between them is a
# choice of texture and not only of mechanism.
_HEDCUT_SPACING = 1 / 378


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

    needs_colour: str | None = None
    """The colour theme this finish's claim depends on, if it has one.

    A capture-time finish sees pixels and cannot know what a hue was *made* to
    mean, so a finish that sorts by colour can only honestly claim "a plate per
    element" if something guarantees the capture was element-coloured. Naming
    the theme here is that guarantee: `snapshot()` asks the viewer to apply it
    for the capture and put the scene back afterwards.

    `None` for a finish that reads only tone, which is most of them.
    """

    supersample: int = 1
    """How many times over this finish wants its capture taken, before it is
    averaged back down to the size that was asked for.

    A plate has exactly two grey levels. `marks` returns booleans and
    `apply_finish` paints flat ink, and `ink_mask` recovers the mask bit for
    bit *because* of it — so an antialiased edge is not something this engine
    can draw. It can only be averaged out of a bigger plate afterwards. The
    stroke interval is a fraction of the frame, so a capture N times as wide
    puts the marks at N times the pixels and averaging back down returns them
    to the size they were chosen at with their edges resolved.

    Declared here rather than applied here, the same way `needs_colour` is:
    this is a fact about the finish, and `snapshot()` is the one place that
    decides how big a capture is. Nothing in `apply_finish` changes, so nothing
    the suite asserts about a plate's two values changes either.

    1 for every finish that does not need it, and that is not a default nobody
    looked at. A fixed-angle rule stays off the axes the renderer's own
    antialiasing favours and does not show a staircase; a bowed rule sweeps
    through every angle, including the bad ones, everywhere on the plate.
    `spot-ink-plates` must stay at 1 for a stronger reason: its plate
    boundaries are a **category**, not a shade, and averaging across one
    invents intermediate inks that its own `palette()` disowns.

    The cost is the square: 2 is four times the pixels, four times the render
    and four times the memory `apply_finish` holds. `snapshot()` re-checks the
    scaled size against the capture ceiling for that reason.
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

    spacing: float | None = None
    """Interval between strokes as a fraction of the longest side, or `None`
    to take `apply_finish`'s.

    Added because `apply_finish`'s `max(4.0, longest / 110)` is 17 px on a
    1890 px plate and 34 at 600 dpi, against a 40 px atom — and because **no
    test could see that**. Every guard in `tests/test_hatching.py` runs at 240
    or 480 px, where that expression clamps to its own 4 px floor, so the suite
    had only ever drawn this finish at its finest while the product shipped it
    at its coarsest. Stated as strokes per feature: the suite's `_dome` is a
    173 px sphere at a 4 px interval, 43 to 1; a real capture is a 40 px atom
    at 17 px, 2.35 to 1.

    Declaring it here rather than changing that expression keeps every caller's
    explicit `spacing=` working and leaves any finish that does not declare one
    exactly as it was.
    """

    def _step(self, frame: _Frame) -> float:
        """This finish's interval in pixels: its own, or the frame's."""
        if self.spacing is None:
            return frame.gap
        return max(4.0, frame.longest * self.spacing)

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
            step = self._step(frame)
            if self.cumulative:
                # Each darker band adds another direction across the last.
                for angle in self.angles[: min(level, len(self.angles))]:
                    marks |= here & _strokes(
                        frame.shape, angle, step, max(1.0, step * 0.30)
                    )
                if level >= self.bands:
                    marks |= here  # solid, past what crossing can carry
            else:
                # One direction throughout, the stroke swelling with the tone —
                # at the last band it closes up into solid ink on its own.
                width = step * (level / self.bands)
                marks |= here & _strokes(frame.shape, self.angles[0], step, width)
        return marks


@dataclass(frozen=True, kw_only=True)
class _Lozenge(_Style):
    """Tone becomes coverage; the form becomes the path the strokes take.

    `cross-hatch` and `hedcut` ruled strokes at a fixed angle regardless of
    what was underneath, which is why neither read as having depth. Measured:
    their ink landed on the form's edges no more often than anywhere else, and
    **making them finer did not touch that** — swept from a 17 px interval down
    to 2, the rim-landing never left chance. Coarseness was a number;
    form-blindness was a mechanism, and this is the mechanism.

    Marks are level sets of a ruled plane warped by the recovered light:

        phase = (x cos a + y sin a) / step  +  relief * shade

    The first term is the **carrier**, exactly the ruled plane `_strokes`
    draws. The second is the **warp**: the lighting alone, with each element's
    own unshaded colour divided out by `_shade`. Where the frame is flat the
    warp is constant and the level sets are straight rules; where a sphere
    sits, the lines are pushed aside by it, the way ruled lines on a rubber
    sheet bow when a ball is pushed through from behind.

    Three things then make it a *hatch* rather than a contour survey:

    - **constant duty, not constant width.** `_Survey` divides the residual by
      the local slope to hold one pixel width, which is right for a contour and
      wrong for a hatch: the warp changes the local interval, so a
      constant-width stroke changes its *coverage* wherever lines bunch, and
      that coverage error is an atom-sized low-frequency stain. Testing
      `|phase - rint(phase)| < 0.5 * c` instead puts a fraction `c` of every
      interval under ink whatever the interval became. It is also the craft
      rule — where the interval narrows the engraver thins the line, so the
      tone stays where it was put.
    - **the burr.** Extra coverage where the recovered lighting turns over: the
      engraver leaning on the burin where one form passes in front of another.
      At these intervals the swell closes adjacent strokes into a solid line
      along the rim, so it *draws* the contour rather than merely darkening
      near it. See `burr`.
    - **the second cut.** Past `hold` the tone is carried by a second family:
      half an interval over at the same angle in the linear treatment, and
      across at a second angle in the crossed one. That single difference is
      what separates the two finishes' shadows; their lights are separated by
      the carrier angle.

    What is *not* needed, and is why this is a smaller object than `_Survey`:
    no grain, because a plane always climbs and so the carrier draws on a flat
    tone by itself; no `flattest` guard, because `|grad phase|` is never zero;
    no dome transform and so no `brightest` clamp, because `d(elevation)/
    d(shade)` diverges at a highlight and `shade` used directly does not.

    On a flat grey `_shade` finds no saturation to divide out and hands back
    the value, so `turn` is zero, the burr is off, and what prints is a plain
    ruling whose duty cycle is exactly the tone. That is the answer to the
    suite's ramp, and it costs nothing.

    One class, two rows in `FINISHES`, the way `cross-hatch` and `hedcut` are
    one `_Engraving` twice. Like `_Survey`'s `pitch` and `line`, `spacing` is
    read off the frame's own size rather than out of `frame.gap` — so, as with
    `cyanotype` and `engraving`, an explicit `spacing=` from a caller does not
    reach this finish.

    Every number below was measured on two plate-size captures of 1UBQ, a
    spacefill and a cartoon, 1890x956, where an atom is about 40 px across.
    Where a number reads "spacefill / cartoon", that is the pair.
    """

    spacing: float
    """Interval between strokes, as a fraction of the **longest side**.

    Against `hedcut`'s `max(4.0, longest / 110)`, which is 17 px on a 1890 px
    plate: at 17 px a 40 px atom gets two or three lines and disappears into a
    grid. A sweep at 17 / 12 / 8 / 6 / 4 / 3 / 2 px found tone still grading
    smoothly at 4 and the lattice beating against the pixel grid at 2, where
    ink jumped from 0.42 to 0.64 of the subject with no change in tone at all.

    The longest side rather than the diagonal because `_Frame`'s own docstring
    says so: a stroke field runs one way, and it is the grain lattices that
    want the diagonal.

    Declared per row rather than defaulted, because the two treatments do not
    want the same interval and the difference is the point — see `FINISHES`.
    """

    least: float = 3.0
    """The interval never falls below this many pixels.

    The analogue of `_grain`'s `max(2.0, ...)`, one pixel higher because a dot
    can be 2 px and still be a dot while a stroke whose width has to grade
    inside a 2 px interval cannot be a line. It binds below a 1410 px capture.
    """

    angle: float = 58.0
    """Carrier direction in degrees.

    Off both axes, so the rules never lie along a pixel row or column, and off
    the 45 degrees the render's own antialiasing favours.

    **Not chosen to maximise any rim statistic**, and the sweep is the reason.
    Over ten carrier angles the correlation between mark direction and the
    recovered shade's gradient runs monotonically from +0.212 at 12 degrees to
    -0.181 at 82. A monotone trend means it is a fact about where these two
    cameras put the light rather than about the finish: a fixed carrier cannot
    know which way the form runs, and rotating the scene would rotate the
    table. Chasing it would be fitting two frames.

    What is left as a real constraint is the pixel grid, the AA diagonal, and
    that the two treatments must not draw the same picture. A shallow carrier
    would also stair-step badly, since a 12 degree line at a 4 px interval
    steps every five pixels.
    """

    cross: float | None = None
    """The second family's angle, or `None` for the linear treatment.

    The whole difference between the two finishes past `hold`, in the way
    `cumulative` is the whole difference between `cross-hatch` and `hedcut`.
    """

    relief: float = 2.5
    """Cycles of sideways deflection per unit of recovered shade — the bow.

    **No aggregate measure over the frame can see this**, and that has to be
    said where the parameter is. At relief 0.0 / 2.5 / 3.5 the ink fraction
    reads 0.496 / 0.497 / 0.496 and the rim-landing 0.219 / 0.218 / 0.222.
    A relief-0 mutant scores every scalar the shipped value does and is exactly
    the "screen laid over a silhouette" this finish exists to stop being: a
    flat ruling with dark blobs where atoms meet, against strokes that bend
    over each dome.

    What does see it is a differential — this finish against itself with
    `relief` set to 0 — which is the idiom `test_shuffle_differential.py`
    already uses, and which `tests/test_hatching.py` now carries. The warp
    moves **0.42 / 0.32** of the subject. See [[guards-that-cannot-see]] in
    spirit: a scalar over a whole frame cannot see a local geometric property.

    Bounded above by a measurement artefact rather than by taste, and that is
    the honest description. As the bow grows the carrier's spectral peak
    spreads until a dominant-period measure jumps from the mark to the
    atom-scale envelope: on the cartoon, relief 2.5 reads 4.03 px, 3.0 reads
    4.03, and **3.5 reads 54.19**. 3.5 is arguably the better picture and
    reports a number that fails. 2.5 is chosen for the margin.

    Bend and interval spread are the same quantity, so a stronger bow is not
    available from a scalar warp: the local interval's 5th-to-95th percentile
    is 4.02-4.02 px at relief 0, 3.42-4.71 at 2.5 and 3.22-5.05 at 3.5, against
    a nominal 4.02. Wanting more means a relaxed stripe field, which is not a
    gradient and costs far more.
    """

    smooth: float = 1 / 380
    """Blur on the shade before it warps the carrier, as a fraction of the
    diagonal.

    **This is the burr's detector bandwidth, not a softness dial**, which is
    the thing to know before touching it. `turn` reads `|grad shade|`, and
    blurring is exactly what takes a rim gradient away. Measured, rim-landing
    on the spacefill at burr 0.6: 1/380 gives 0.218, 1/300 gives 0.109, 1/250
    gives 0.064, 1/200 gives 0.021. Two steps softer and the finish is back at
    chance.

    The value is quantised more coarsely than it looks: `_blur` rounds its
    radius, so at plate size 1/440 and 1/380 are the *same* five-pixel blur and
    only 1/500 differs.
    """

    tone_curve: float = 2.4
    """Darkness is raised to this power to become coverage.

    Steeper than the module's `_TONE_CURVE` of 1.7, which was tuned for a
    *banded* engraving whose lightest inked band already lays a whole stroke.
    Mean ink over the subject, before the burr (spacefill / cartoon):

        2.0  0.561/0.470    2.2  0.528/0.435
        2.4  0.496/0.405    2.6  0.466/0.377

    At 2.0 the spacefill comes back with more ink than paper and reads as a
    dark mass with light seams; at 2.4 it sits beside `engraving` (0.542)
    rather than past it, and the domes keep a lit side.
    """

    weight: float = 1.04
    """Darkness is scaled by this before the curve, so black closes solid.

    Without it a tone of 0 reaches coverage 1.0 only in the limit and the
    suite's darkest-tone assertion rests on rounding. 1.04 puts everything
    darker than a value of 10 at full width, which is where `hedcut`'s last
    band is.
    """

    hold: float = 0.55
    """Coverage at which the second cut opens.

    For the linear treatment that is the interline; for the crossed one it is
    the second angle.

    **It deliberately does not carry the suite.** An earlier version gave both
    treatments the same carrier angle, which made them bit-identical below
    `hold`: on a real capture they then disagreed on 0.127 / 0.064 of the
    subject, against 0.466 / 0.426 for the old `cross-hatch` against `hedcut`.
    Two finishes that draw nearly the same picture are not two treatments, and
    the whole of their suite separation was riding on one fixture tone. With
    their own carrier angles they disagree on 0.433 / 0.401 as the pair ships,
    and on the suite's fixture at 0.488 / 0.444 / 0.272 for tones 60 / 100 /
    140 — which is `2c(1-c)` exactly, the most two patterns of equal density
    can differ. `hold` is therefore free to be chosen for the picture.

    0.55 for the linear: at 0.5 the stroke and the gap are equal, which is the
    widest a line can be and still read as a line, and a shade past that is
    where an engraver puts the second cut in rather than fattening the first.
    At `tone_curve` 2.4 that coverage is reached at a value of about 64/255, so
    the interline appears only in the deep shadows. Looked at as well as
    reasoned: at 0.40 the interline is open across the mid-tones and the mark
    goes speckly at 4x; at 0.55 the strokes stay continuous swelling lines.
    The crossed row holds at 0.45, which asks the same craft question of a
    different mark — see `FINISHES`.
    """

    burr: float = 0.75
    """Extra coverage where the surface turns away, in units of the target.

    A burin throws a burr at a form's edge and the engraver leans on the tool
    there. **This is the entire rim-landing mechanism**, and the assumption
    that a tone-driven width would find the rims by itself is false: mean
    darkness over the steepest decile against the whole subject is 0.625
    against 0.622 on the spacefill. The rim is not darker than the subject — it
    is only steeper.

    Measured (rim-landing spacefill / cartoon, then tone fidelity spacefill):

        0.0   0.017/0.036  0.960      0.6   0.218/0.230  0.941
        0.3   0.123/0.138  0.953      0.75  0.259/0.267  0.936
        0.9   0.290/0.291  0.931      1.2   0.338/0.323  0.923

    Monotone in the first and monotone the other way in the second, so this is
    a taste choice inside a measured range, and the crop settles it: at 0.0 the
    atoms barely separate; at 0.9 and past it every sphere carries a drawn
    outline and the picture reads as outline-and-fill rather than as engraving.
    0.75 draws the seam where two atoms meet without the seam becoming a
    cartoon edge.
    """

    turn_lo: float = 20.0
    turn_hi: float = 28.0
    """`|grad shade| * diagonal` between which the burr ramps in.

    Dimensionless per-diagonal, which is what makes it scale-invariant: render
    the same scene twice as large and the per-pixel gradient halves while the
    diagonal doubles, so the swell lands in the same places.

    Placed on the measured distribution rather than guessed. Over subject
    pixels at this blur:

        percentile     p50    p75    p90    p99
        spacefill      15.3   21.1   26.7   40.7
        cartoon        11.1   19.4   27.9   43.1

    So 20 is about the upper quartile on both plates and 28 about the top
    decile on both: the burr is off for three quarters of the subject and full
    on over the decile a rim measure calls the edge. Spending it more widely
    buys nothing, because that measure is chance-corrected.
    """

    achromatic: float = 0.12
    """Below this saturation a pixel has no element colour to divide out. The
    same value `_Survey` uses, for the same reason."""

    bands: int = 8
    """What the suite checks the finish separates, and a floor rather than a
    count: nothing here quantises. Measured over the full 255-to-0 ramp at
    240 px, both treatments come back with **219** distinguishable ink levels
    against the required `bands + 1` of 9, the way `_Survey` measures in the
    hundreds.
    """

    def _fields(self, frame: _Frame) -> tuple[np.ndarray, float, np.ndarray]:
        """The shade, the interval, and the coverage to lay.

        Split out so nothing downstream can disagree about any of them.
        """
        shade = _blur(_shade(frame.rgb, self.achromatic), frame.diagonal * self.smooth)
        gradient_y, gradient_x = np.gradient(shade)
        slope = np.hypot(gradient_x, gradient_y)
        step = max(self.least, frame.longest * self.spacing)

        target = (
            np.clip(np.clip(1.0 - frame.luma, 0.0, 1.0) * self.weight, 0.0, 1.0)
            ** self.tone_curve
        )
        turn = np.clip(
            (slope * frame.diagonal - self.turn_lo) / (self.turn_hi - self.turn_lo),
            0.0,
            1.0,
        )
        return shade, step, np.clip(target * (1.0 + self.burr * turn), 0.0, 1.0)

    def _phase(
        self, frame: _Frame, shade: np.ndarray, step: float, degrees: float
    ) -> np.ndarray:
        """The warped carrier: a ruled plane pushed aside by the light."""
        height, width = frame.shape
        y, x = np.ogrid[0:height, 0:width]
        radians = np.deg2rad(degrees)
        # `np.asarray` for mypy: `ogrid` is Any, so the whole expression is.
        return np.asarray(
            (x * np.cos(radians) + y * np.sin(radians)) / step + self.relief * shade
        )

    def marks(self, frame: _Frame) -> np.ndarray:
        shade, step, coverage = self._fields(frame)

        # The first cut carries the tone until `hold`, then holds its width.
        first = np.minimum(coverage, self.hold)
        phase = self._phase(frame, shade, step, self.angle)
        # Constant *duty*: a fraction `first` of every interval, whatever the
        # warp did to that interval's width in pixels.
        ink = np.abs(phase - np.rint(phase)) < 0.5 * first

        if self.cross is None:
            # The interline — a second thread half an interval over, at the
            # same angle, warped by the same shade, opening in the gap the
            # first leaves. The two bands are disjoint until they meet at
            # solid, so the shares **add** and the union is exactly the
            # coverage asked for. Halving the interval rather than turning the
            # tool is the single-family engraver's move.
            second = np.clip(coverage - self.hold, 0.0, 1.0)
            between = phase + 0.5
            ink |= np.abs(between - np.rint(between)) < 0.5 * second
        else:
            # A second direction, whose phase is incommensurate with the
            # first's, so the union is 1 - (1 - first)(1 - second). Solving
            # that for `second` makes the union exactly the coverage again — so
            # the two treatments lay down identical density at every tone and
            # differ only in the lay of the marks. Measured: the ink fractions
            # agree to 0.001 on both plates.
            second = np.clip((coverage - first) / np.maximum(1.0 - first, 1e-6), 0.0, 1.0)
            across = self._phase(frame, shade, step, self.cross)
            ink |= np.abs(across - np.rint(across)) < 0.5 * second

        return np.asarray(ink & ~frame.is_paper)


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


@dataclass(frozen=True, kw_only=True)
class _Bowed(_Engraving):
    """C — the incumbent mechanism, surviving, with the form added under it.

    `_Debanded` plus `_Lozenge`'s warped carrier: the ruled plane is pushed
    aside by the recovered light, so one direction still thickens with tone but
    the rules bow around each atom rather than running straight through it.

    This is the answer that keeps the most of what ships. Its rim lift says it
    does not answer the rim half — included because "keep the mechanism, add the
    bow" is the reasonable first idea and it should be looked at, not argued
    about.
    """

    relief: float = 2.5
    smooth: float = 1 / 380
    achromatic: float = 0.12

    def marks(self, frame: _Frame) -> np.ndarray:
        shade = _blur(_shade(frame.rgb, self.achromatic), frame.diagonal * self.smooth)
        darkness = np.power(np.clip(1.0 - frame.luma, 0.0, 1.0), _TONE_CURVE)
        step = self._step(frame)
        height, width = frame.shape
        y, x = np.ogrid[0:height, 0:width]
        radians = np.deg2rad(self.angles[0])
        phase = np.asarray(
            (x * np.cos(radians) + y * np.sin(radians)) / step + self.relief * shade
        )
        residual = np.abs(phase - np.rint(phase))
        return np.asarray((residual < 0.5 * darkness) & ~frame.is_paper)


@dataclass(frozen=True, kw_only=True)
class _Stipple(_Style):
    """`dotty` — a dot field, with the parameters a dot field actually has.

    Split out of `_AllDots` rather than kept as one. That was hedcut's
    overshoot: a `_Lozenge` with its burin branch switched off, so it carried
    an `angle`, a `cross`, a `hold`, a `relief` and a `split_lo`/`split_hi`
    ramp it never read, and took its dot pitch from `cross-hatch`'s 1/470 by
    inheritance rather than by choice. A finish whose defaults arrived by
    accident is a finish nobody can tune.

    What a stipple has instead:

    `pitch` is the lattice interval, the only size in the finish. `jitter` is
    how far a dot may wander from its cell — 0 is a mechanical halftone screen,
    0.5 is a dot that can reach its neighbour's seat, and the point of a hedcut
    dot field is that it is hand-placed rather than screened.

    `dot` is the radius at the lightest inked tone as a fraction of the pitch,
    and `growth` is how much it swells toward black. Both, because a stipple
    carries tone two ways at once — bigger dots and more of them — and a field
    that only has density cannot reach solid: measured, one caps at 0.283
    coverage at black and fails the suite's bar.

    The burr stays. It is what puts ink on the rim of an atom rather than
    spreading it evenly, and it is the reason a dot field reads as modelling a
    form rather than as a grey wash.
    """

    pitch: float = 1 / 470
    """Lattice interval as a fraction of the frame diagonal — 2.9 px on an
    1890 px plate.

    A fine grain rather than countable dots, which is Charlie's call: shown
    1/280, where a dot is round and plainly a dot, they said *"too coarse"*.
    So the pitch stays where it was and the fineness is now a decision rather
    than an inheritance from `cross-hatch`.

    **No test can see this.** `_step` clamps to a 2 px floor, and every guard
    in `tests/test_hatching.py` runs at 240 or 480 px where 1/470 and 1/220
    both clamp to the same 2 px — so all four pitches I swept scored byte
    identical bars. The same trap `_Engraving.spacing` documents. The numbers
    below were taken at 700 and 1200 px instead.
    """

    jitter: float = 0.30
    """How far a dot may wander from its cell, in cells.

    0 is a mechanical halftone screen; 0.5 lets a dot reach its neighbour's
    seat. It costs almost nothing in tone — measured, ink over the drawn region
    moves 0.508 to 0.517 across the whole range — so it is a choice about
    character rather than density, and 0.30 is a hand that is placing dots
    rather than a machine ruling them.
    """

    dot: float = 0.40
    """Dot radius at the lightest inked tone, as a fraction of the pitch.

    0.40 rather than the 0.34 inherited from the overshoot, because **0.34
    cannot close.** A stipple has to reach solid at black or it fails the
    suite's `coverage[-1] > 0.9` bar, and a circle needs a radius near 0.71 of
    its cell to cover the corners.

    This is a fraction of the pitch, so it is **independent of the pitch** —
    which is what lets the grain stay fine while the darks still close. That
    was not obvious: the first attempt coarsened the pitch to fix closure and
    changed the one thing that did not need changing. Measured at 1200 px with
    the pitch held at 1/470:

        0.34  black 0.792   fails
        0.38        0.891   fails
        0.40        0.928   closes, and is the least that does
        0.44        0.974

    `growth` cannot substitute: the swell is clipped at 1.6, so 0.34 tops out
    at 0.792 whatever growth is set to.
    """

    growth: float = 1.1
    """How much the dot swells from the lightest inked tone toward black.

    A stipple carries tone two ways at once, more dots and bigger ones. Density
    alone caps at 0.283 coverage at black — the reason the pure-density variant
    was dropped before it was ever plated.
    """
    lattice_angle: float = 33.0
    burr: float = 0.75
    turn_lo: float = 20.0
    turn_hi: float = 28.0
    smooth: float = 1 / 380
    achromatic: float = 0.12
    tone_curve: float = 2.4
    weight: float = 1.04
    bands: int = 8

    def _chroma(self, frame: _Frame) -> np.ndarray:
        """Whether a pixel has a hue at all.

        Computed here rather than read off `_families`, because `_families`
        sends an achromatic pixel to family 0 — which is the key plate in
        `_Plates` and is a *colour* here, so the two cases have to be told
        apart or every grey pixel would print in the first ink.
        """
        unit = frame.rgb / 255.0
        value = unit.max(axis=2)
        low = unit.min(axis=2)
        saturation = np.where(value > 0.0, (value - low) / np.maximum(value, 1e-6), 0.0)
        return np.asarray(saturation >= self.achromatic)

    def _coverage(self, frame: _Frame) -> np.ndarray:
        """How much ink this tone asks for, with the burr applied.

        Split out of `marks` so the colour variants can lay their own plates
        against the *same* field. They must: the whole design is that black
        carries the form and colour carries the hue, and that only holds if the
        black is bit-for-bit the field `dotty` already draws rather than a
        second implementation that agrees by inspection.
        """
        shade = _blur(_shade(frame.rgb, self.achromatic), frame.diagonal * self.smooth)
        gradient_y, gradient_x = np.gradient(shade)
        slope = np.hypot(gradient_x, gradient_y) * frame.diagonal
        turn = np.clip((slope - self.turn_lo) / (self.turn_hi - self.turn_lo), 0.0, 1.0)
        return np.asarray(
            np.clip(
                np.clip(1.0 - frame.luma, 0.0, 1.0) ** self.tone_curve
                * self.weight
                * (1.0 + self.burr * turn),
                0.0,
                1.0,
            )
        )

    def _dots(self, frame: _Frame, coverage: np.ndarray) -> np.ndarray:
        """One jittered dot lattice, lit and sized by `coverage`.

        Split out of `marks` alongside `_coverage`, for the same reason: the
        colour variants lay several of these — one per ink — and a second
        implementation of a dot lattice would drift from this one silently.
        """
        height, width = frame.shape
        pitch = max(2.0, frame.diagonal * self.pitch)
        radians = np.deg2rad(self.lattice_angle)
        y, x = np.ogrid[0:height, 0:width]
        u = (x * np.cos(radians) + y * np.sin(radians)) / pitch
        v = (-x * np.sin(radians) + y * np.cos(radians)) / pitch
        cell_u = np.floor(u).astype(np.int64)
        cell_v = np.floor(v).astype(np.int64)

        dots = np.zeros((height, width), dtype=bool)
        # Three by three, so a dot jittered out of a neighbouring cell still
        # lands here. At jitter 0.5 a dot reaches the next seat exactly, so one
        # ring is enough and two would only cost time.
        for du in (-1, 0, 1):
            for dv in (-1, 0, 1):
                au, av = cell_u + du, cell_v + dv
                ju = au + 0.5 + (_hash01(au, av, 1) - 0.5) * 2 * self.jitter
                jv = av + 0.5 + (_hash01(au, av, 2) - 0.5) * 2 * self.jitter
                radius = self.dot * np.clip(0.6 + self.growth * coverage, 0.0, 1.6)
                dots |= (np.hypot(u - ju, v - jv) < radius) & (
                    _hash01(au, av, 3) < np.sqrt(coverage)
                )
        return dots

    def marks(self, frame: _Frame) -> np.ndarray:
        return np.asarray(self._dots(frame, self._coverage(frame)) & ~frame.is_paper)


_KEY = (26, 22, 34)

#: Poppy, marigold, cornflower. Bright enough to read as a dot on white at
#: seven pixels, which a process yellow is not: (252, 222, 26) sits 0.18 of
#: luma from the paper and vanishes. These sit 0.477, 0.325 and 0.520 away.
#: Marigold is the weakest and is the one to watch.
#:
#: Which family wears which is decided by hue prevalence rather than by
#: chemistry, so all three have to work as the dominant colour.
_POPPY = (230, 87, 118)
_MARIGOLD = (238, 165, 36)
_CORNFLOWER = (58, 140, 200)


@dataclass(frozen=True, kw_only=True)
class _Intermixed(_Stipple):
    """Colour in the same dot field as the black, not on a layer above it.

    Charlie, on `dotty-sprinkles`: *"they look like they're on top of the
    protein, and I'd like them to appear intermixed with the black dots."*

    That is a symptom, and two mechanisms were producing it.

    **The sprinkle lattice was four times coarser** — 1/120 against the key's
    1/470 — so a coloured dot was four times the diameter of a grain dot, and a
    bigger mark reads as a nearer one. **And the sprinkles knocked out of the
    key**: `dots & ~disc` removed the black wherever a colour landed, which is
    exactly what something lying on top of something else does.

    So this does not overlay a second field. It **inks the one field**: the
    same lattice, the same pitch, the same jitter, the same dot size that
    `dotty` draws — and each cell takes either the key or one of the colours.
    A coloured dot is not a dot placed over the grain, it is a grain dot that
    happens to be poppy. Nothing is knocked out because nothing overlaps: the
    plates *partition* one mask rather than competing for it.

    A consequence worth stating, because it is the design and not a
    limitation: colour can only be as fine as the grain. There is no way to
    make a sprinkle bigger than a black dot here without reintroducing the
    layer Charlie is objecting to.
    """

    inks: tuple[tuple[int, int, int], ...] = (_KEY, _POPPY, _MARIGOLD, _CORNFLOWER)
    """Key first, then the colours. The key is a blue-black rather than black
    for the reason `_Sprinkles` records: `_multiply` collapses every crossing a
    pure black takes part in, and `palette()` then holds nine colours where the
    suite asserts sixteen. It applies here even though these plates never
    actually cross, because `palette()` enumerates the combinations rather than
    observing them."""

    rate: float = 0.42
    """Share of chromatic cells that take a colour rather than the key.

    Sits above the fraction of the *plate* that ends up coloured, because a
    cell only becomes a candidate where there is hue to carry. Tuned to land
    near the 21.7% chromatic share of the plate Charlie liked, so the change
    is about where the colour sits rather than how much of it there is.
    """

    lift: float = 0.12
    """Coverage below which a cell keeps the key.

    Colour is held off the lights. A bright dot alone on near-paper reads as a
    speck of dirt rather than as modelling, and the same reasoning kept the
    old sprinkles off the highlights.
    """

    def _inked(
        self, frame: _Frame, coverage: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """The dot field, and which ink each pixel's cell takes.

        The lattice arithmetic is `_Stipple._dots`' exactly — same pitch, angle,
        jitter, radius and lighting test — with a second array carried
        alongside saying which plate the cell belongs to. It is duplicated
        rather than shared because `_dots` returns a union and this needs the
        cell identity that the union throws away; if the two ever disagree the
        colour would drift off the grain, which is the whole thing being fixed.
        """
        colours = len(self.inks) - 1
        family = _families(frame.rgb, colours, self.achromatic)
        chroma = self._chroma(frame)
        seat = np.clip(coverage / self.lift, 0.0, 1.0) * self.rate

        height, width = frame.shape
        pitch = max(2.0, frame.diagonal * self.pitch)
        radians = np.deg2rad(self.lattice_angle)
        y, x = np.ogrid[0:height, 0:width]
        u = (x * np.cos(radians) + y * np.sin(radians)) / pitch
        v = (-x * np.sin(radians) + y * np.cos(radians)) / pitch
        cell_u = np.floor(u).astype(np.int64)
        cell_v = np.floor(v).astype(np.int64)

        dots = np.zeros((height, width), dtype=bool)
        which = np.zeros((height, width), dtype=np.int64)
        for du in (-1, 0, 1):
            for dv in (-1, 0, 1):
                au, av = cell_u + du, cell_v + dv
                ju = au + 0.5 + (_hash01(au, av, 1) - 0.5) * 2 * self.jitter
                jv = av + 0.5 + (_hash01(au, av, 2) - 0.5) * 2 * self.jitter
                radius = self.dot * np.clip(0.6 + self.growth * coverage, 0.0, 1.6)
                here = (np.hypot(u - ju, v - jv) < radius) & (
                    _hash01(au, av, 3) < np.sqrt(coverage)
                )
                # A cell goes chromatic on its own hash, so the choice is
                # stable per dot rather than flickering within one.
                takes = here & chroma & (_hash01(au, av, 21) < seat)
                which = np.where(takes & ~dots, family + 1, which)
                dots |= here
        return dots, which

    def plates(self, frame: _Frame) -> tuple[np.ndarray, ...]:
        coverage = self._coverage(frame)
        dots, which = self._inked(frame, coverage)
        dots &= ~frame.is_paper
        # A partition, not an overlay: every drawn pixel belongs to exactly one
        # plate, so no two ever cross and `_multiply` is never reached.
        return tuple(dots & (which == index) for index in range(len(self.inks)))

    def marks(self, frame: _Frame) -> np.ndarray:
        covered = np.zeros(frame.shape, dtype=bool)
        for plate in self.plates(frame):
            covered |= plate
        return covered


FINISHES: dict[str, _Style] = {
    # The hatching, in two treatments — which is the whole of the ask, and the
    # reason `_Lozenge` exists. The old `cross-hatch` ruled three fixed angles
    # over the frame regardless of what was underneath; its ink landed on the
    # form's edges no more often than chance (+0.014, against `engraving`'s
    # +0.202) and drawing it finer never moved that. It is replaced rather than
    # kept beside its successor, because two finishes under one idea is a menu
    # that makes the reader choose between a good one and a bad one.
    #
    # The two intervals are not the same and the difference is deliberate: one
    # direction can carry a bold mark, and two crossing families at the same
    # interval close up into tone. 6 px and 4 px on a 1890 px plate. Chosen by
    # looking at a 6/4/3 bracket at plate size, not from print convention.
    #
    # Their carrier angles are 26 degrees apart and neither is 45, so the pair
    # differs in the lights as well as in the shadows. Measured, they disagree
    # on 0.433 / 0.401 of a real subject — against 0.466 / 0.426 for the old
    # `cross-hatch` against `hedcut`, which is the separation two finishes that
    # have always been considered distinct actually have.
    "linear-hatch": _Lozenge(angle=58.0, cross=None, hold=0.55, spacing=1 / 315),
    "cross-hatch": _Lozenge(angle=32.0, cross=-41.0, hold=0.45, spacing=1 / 470),
    # A hedcut rules one direction and thickens it, and that is the style
    # rather than a defect. What it did not do was turn: the rules ran straight
    # through every form. `_Bowed` keeps the mechanism and pushes the ruled
    # plane aside with the light the render already carries, so the rules bow
    # around each atom. Chosen from a bracket of five plates.
    #
    # It stays a *control* for the rim guard, and that is not an oversight. The
    # bow is a texture, not a rim-landing mechanism: measured on `_spheres()`
    # at 1890x956 the bowed finish lifts +0.0694 and the same finish with
    # `relief` at 0 lifts +0.0714 — the warp does not move the rim at all.
    #
    # `bands` survives as a floor claim rather than as a count. Nothing in
    # `_Bowed.marks` reads it; it is the number the suite requires the finish
    # to beat in distinguishable ink levels, and a continuous duty clears six
    # bands by drawing 245 of them.
    "hedcut": _Bowed(angles=(75.0,), cumulative=False, bands=6, spacing=_HEDCUT_SPACING),
    # The same idea in dots rather than rules. Charlie asked for the dots-only
    # treatment as its own style rather than as a hedcut variant, and named it.
    "dotty": _Stipple(),
    # `dotty`'s one dot field, inked — not a second field laid over it. The two
    # differ only in how many cells go chromatic, which is the one parameter
    # separating them: about a fifth of the drawn pixels against about half.
    #
    # Neither declares `needs_colour`, deliberately. `spot-ink-plates` demands
    # an element theme because its plates *mean* something; these claim nothing
    # about what a hue is, so forcing one would override a colouring the caller
    # chose on purpose in order to sprinkle three arbitrary print inks. The
    # cost is that a greyscale render has no hue to sort and every cell takes
    # the key, which draws exactly `dotty` — so `snapshot` reports the
    # chromatic share, and that is what keeps the silence from passing for a
    # result.
    "dotty-mixed": _Intermixed(rate=0.26),
    "dotty-confetti": _Intermixed(rate=0.70),
    # Prussian blue and the white of unexposed paper — never pure 255, because
    # a cyanotype's highlight is paper rather than light.
    "cyanotype": _Survey(bands=5, paper=(17, 48, 92), inks=((238, 245, 252),)),
    # Madder and indigo on a warm white, from the plan's dyed-wool palette —
    # two inks a small press could actually mix, rather than process colours.
    # Their crossing is a near-black with a plum cast, which is what a
    # two-colour print gives you instead of a real black.
    # The same contour engine as cyanotype, in ink on paper and four times as
    # fine. `_Survey` was written to draw a survey sheet and has always been a
    # depth-cued renderer — it contours the recovered lighting field and holds
    # constant line width by dividing the residual by the local slope, so a
    # steep face gets a thin line rather than a fat smear. It had only ever
    # been drawn in blue.
    #
    # 14 bands rather than cyanotype's 5, chosen by looking at a sweep of
    # 5/9/14/20/28 on carbonic anhydrase rather than from print convention.
    #
    # `brightest` is raised from the 0.975 the survey uses. That clamp flattens
    # everything brighter to one elevation, and measured on the same capture it
    # takes 3.27% of molecule pixels — a small area that happens to sit on the
    # summit of every single atom, so each dome lost its innermost ring. At
    # 0.9975 the summits come back and the highlight spray the clamp exists to
    # prevent still does not appear, because the blur before contouring has
    # already taken the specular pixels down.
    #
    # The every-fifth-contour index weighting is kept deliberately. It was
    # tuned when `bands` was 5, where it fell on the silhouette alone; at 14 it
    # also lands mid-dome, and that reads as the index contour of a relief map,
    # which is what this is.
    "engraving": _Survey(
        bands=14,
        line=1 / 2000,
        pitch=1 / 320,
        brightest=0.9975,
        paper=(255, 255, 255),
        inks=((0, 0, 0),),
    ),
    "spot-ink-plates": _Plates(
        bands=5,
        paper=(247, 243, 233),
        inks=((163, 41, 38), (41, 61, 107)),
        needs_colour="element-symbol",
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


def chromatic_fraction(engraved: Image.Image, finish: str) -> float:
    """How much of the ink came off a plate other than the first.

    Reported for the reason `ink_fraction` is: the caller cannot look, and the
    failure this catches is silent. `dotty-mixed` sorts its dots by the hue
    already in the render and claims nothing about what a hue means — which is
    what makes it useful over any colouring, and what makes it draw **exactly**
    `dotty` over a greyscale one. There is no hue to sort, every cell takes the
    key, and the plate that comes back is byte-identical to the mono finish's.
    Without this number the reply carries a path, a success and an ink fraction
    with nothing anywhere saying the colour never arrived.

    A share of the *ink*, not of the frame, so it does not move when the
    subject grows or the tone deepens — it answers "how much of what was drawn
    came out coloured", which is the question, rather than "how much of the
    page is coloured", which confounds it with coverage.

    0.0 for a finish with one ink is the truth rather than a special case:
    every mark it makes is its only ink.
    """
    validate_finish(finish)
    inked = ink_mask(engraved, finish)
    if not inked.any():
        return 0.0
    key = np.array(FINISHES[finish].inks[0], dtype=np.uint8)
    pixels = np.asarray(_rgba(engraved))[:, :, :3]
    off_key: np.ndarray = (pixels != key).any(axis=2)
    return round(float(off_key[inked].mean()), 3)


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
