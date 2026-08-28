"""The engraving finish, tested on images whose answer we set.

Pure image processing, so unlike the render suite this can assert exactly
rather than within a threshold: a flat grey has a known tone, and how much ink
it earns is a property rather than a measurement.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import replace
from unittest import mock

import numpy as np
import pytest
from PIL import Image

from protean_mcp.analysis.hatching import (
    _PAPER,
    FINISHES,
    _blur,
    _Engraving,
    _Lozenge,
    _multiply,
    _Style,
    _Survey,
    apply_finish,
    ink_fraction,
    ink_mask,
)

FINISH_NAMES = sorted(FINISHES)


#: The size the finish-comparison test draws at. Big enough that no two
#: finishes' grain lattices collapse onto the same floor.
#:
#: `_grain` resolves its step as `max(2.0, diagonal * pitch)`, so below a
#: certain size every fine finish clamps to 2 px and draws an identical
#: lattice. Measured across all ten finish pairs: at 240 and 360 px the worst
#: pair scores **0.0000** — indistinguishable — and at 480 it jumps to 0.4800.
#: The cliff is where `engraving`'s 1/320 pitch clears the floor.
#:
#: Only the comparison uses it. The rest of the suite stays at 240, because
#: raising the fixture everywhere took the file from 26 s to 113 s.
_ENGRAVABLE = 480


def _flat(value: int, size: int = 240, alpha: int = 255) -> Image.Image:
    """A square of one tone, which is the cleanest thing to engrave."""
    return Image.fromarray(
        np.full((size, size, 4), (value, value, value, alpha), dtype=np.uint8),
        mode="RGBA",
    )


def _ink(image: Image.Image, finish: str) -> float:
    """The shipped measure, not a second copy of it.

    This used to be its own implementation of "how much came back black",
    which meant the suite agreed with `ink_fraction` because both had been
    written from the same wrong idea rather than because either was right.
    """
    return ink_fraction(image, finish)


# Every tone, not a sample of them. A step of 15 was enough for both shipping
# finishes, and that is not a property of the route: measured, a finish
# declaring 12 bands lands in 12 of its 13 at that step and one declaring 14
# lands in 13 of its 15. So the levels assertion below would have failed a
# perfectly correct finish and blamed it for the test's own sampling. The full
# sweep costs 0.8 s per finish and is the only version that means what it says.
_RAMP = tuple(range(255, -1, -1))


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_darker_tone_earns_more_ink(finish):
    """The whole claim of the technique: tone becomes line density."""
    coverage = [_ink(apply_finish(_flat(tone), finish), finish) for tone in _RAMP]

    assert coverage == sorted(coverage), f"{finish} did not darken monotonically"
    assert coverage[0] == 0.0, "white took ink"
    assert coverage[-1] > 0.9, "black did not fill in"
    # Every line above passes for a finish that hands its input straight back:
    # white has no ink, black is solid, and a sorted list tolerates a run of
    # identical values, so nothing between the ends has to move at all. What a
    # passthrough cannot do is separate the tones — it yields two levels where
    # a real finish yields one per band plus the paper. Measured: cross-hatch
    # 5, hedcut 7, passthrough 2.
    #
    # A floor rather than an equality, and the difference matters. Both
    # finishes shipping today quantise, so they produce exactly their declared
    # count and an equality held. A finish whose marks grade continuously
    # rather than in steps produces far more — a contour survey prototyped
    # alongside this measures 233 over the same sweep — and an equality would
    # refuse it for being finer than a step wedge. The claim under test is
    # "tone becomes density", and the floor is what that claim actually says;
    # the equality was the banded family's implementation detail wearing the
    # claim's clothes.
    assert len(set(coverage)) >= FINISHES[finish].bands + 1, (
        f"{finish} declares {FINISHES[finish].bands} bands but produced only "
        f"{len(set(coverage))} distinguishable ink levels"
    )


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_a_mid_tone_becomes_neither_blank_nor_solid(finish):
    """A grey that came back all-white or all-black would mean the banding
    collapsed, which is the failure that still looks like a picture."""
    middle = _ink(apply_finish(_flat(128), finish), finish)

    assert 0.02 < middle < 0.95, f"{finish} turned a mid grey into {middle}"


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_the_result_is_ink_on_paper_and_nothing_else(finish):
    """Two tones, no greys — that is what makes it an engraving rather than a
    filter over the original.

    Against the finish's *declared* colours, not against black and white. The
    older form asserted `{0, 255}`, which is the same assumption the ink
    measure was making: true of both engraving styles by coincidence, and
    quietly false for the first finish that prints in anything else.
    """
    style = FINISHES[finish]
    engraved = np.asarray(apply_finish(_flat(128), finish))
    colours = {tuple(c) for c in engraved[:, :, :3].reshape(-1, 3).tolist()}

    declared = style.palette()
    assert colours <= declared, (
        f"{finish} printed colours it never declared: {colours - declared}"
    )
    assert style.ink in colours, f"{finish} laid down no ink on a mid grey"
    assert style.paper in colours, f"{finish} left no paper showing on a mid grey"


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_nothing_drawn_stays_nothing_drawn(finish):
    """A transparent canvas has no tone to engrave. Filling it with the
    lightest band would put a hatched rectangle behind the molecule."""
    engraved = apply_finish(_flat(128, alpha=0), finish)

    assert np.asarray(engraved)[:, :, 3].max() == 0


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_the_size_is_the_size_it_was_given(finish):
    original = _flat(128, size=137)
    engraved = apply_finish(original, finish)

    assert engraved.size == original.size


def test_colour_is_weighted_the_way_an_eye_weighs_it():
    """Rec. 601 luma, not the channel average: a saturated blue is dark and a
    saturated green is light, and averaging would call them the same."""
    blue = Image.fromarray(
        np.full((240, 240, 4), (0, 0, 255, 255), dtype=np.uint8), "RGBA"
    )
    green = Image.fromarray(
        np.full((240, 240, 4), (0, 255, 0, 255), dtype=np.uint8), "RGBA"
    )

    assert _ink(apply_finish(blue, "hedcut"), "hedcut") > _ink(
        apply_finish(green, "hedcut"), "hedcut"
    )


@pytest.mark.parametrize(("left", "right"), list(itertools.combinations(FINISH_NAMES, 2)))
def test_no_two_finishes_draw_the_same_picture(left, right):
    """Cross-hatching crosses and a hedcut does not, so the same tone has to
    come out differently or one of them is not doing its job.

    Over every pair rather than the two that happened to exist when this was
    written: a third finish added as a `_Finish` whose fields the code ignores
    renders byte-identically to one already there, and a test naming the old
    pair would have gone on passing while the new name drew someone else's
    picture.

    Compared on where the ink *is*, not on the colours, so a finish that is an
    existing one recoloured is still caught. Through the shipped `ink_mask`
    rather than a copy of it, so a change to what counts as ink cannot leave
    this comparing masks the product no longer draws. Measured over inked
    tones: the two shipping finishes disagree on 0.37 to 0.50 of the frame,
    and a duplicate reads exactly 0.0.
    """
    # Tones light enough that both finishes leave the paper bare would compare
    # two blank frames and pass for any pair, including a duplicate.
    disagreement = max(
        float(
            (
                ink_mask(apply_finish(_flat(tone, _ENGRAVABLE), left), left)
                ^ ink_mask(apply_finish(_flat(tone, _ENGRAVABLE), right), right)
            ).mean()
        )
        for tone in (60, 100, 140)
    )

    assert disagreement > 0.1, (
        f"{left} and {right} drew the same picture (disagreed on "
        f"{disagreement:.4f} of the frame)"
    )


def _dome(size: int = 240, ground: int = 70) -> Image.Image:
    """A lit sphere on an opaque ground: a spacefill atom, in miniature.

    Opaque on purpose. Every finish looks competent over the transparent
    capture the bake-off used, because the background is not there to compete.
    Put the same subject on a grey field and the picture has to separate the
    two by its marks alone — which is the case that decided which finish
    shipped, and the one no test covered.
    """
    y, x = np.ogrid[0:size, 0:size]
    centre = size / 2
    radius = size * 0.36
    r = np.hypot(x - centre, y - centre) / radius
    inside = r <= 1.0
    # Lambert shading from a light up and to the left, which is where the
    # viewer's default light sits.
    z = np.sqrt(np.clip(1.0 - r**2, 0.0, 1.0))
    lit = np.clip(
        (-0.5 * (x - centre) / radius - 0.5 * (y - centre) / radius + z), 0, None
    )
    shade = np.clip(0.15 + 0.85 * lit / max(lit.max(), 1e-6), 0.0, 1.0)
    field = np.full((size, size), float(ground))
    field[inside] = (shade * 235.0)[inside]
    rgb = np.repeat(field[:, :, None], 3, axis=2).astype(np.uint8)
    out = np.dstack([rgb, np.full((size, size, 1), 255, dtype=np.uint8)])
    return Image.fromarray(out, "RGBA")


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_a_finish_keeps_its_subject_off_an_opaque_ground(finish):
    """A finish must ink the subject and the ground differently.

    Every finish shipped here has been looked at over a *transparent* capture,
    where the background is not there to compete. On an opaque field it has to
    hold the two apart by its marks alone, and this asserts the weakest form of
    that: the two densities differ at all.

    **It is weaker than the property that decided the cyanotype bake-off, and
    deliberately kept anyway.** Two of the four candidates dissolved on an
    opaque ground — the field earned a dense texture of its own and the
    molecule sank into it as a faint tracery. Measured against this fixture,
    the worst of those still separates at 0.117, so it would pass. What was
    lost there was *structure* inside a competing texture, which is a
    perceptual property and not one a density difference can stand in for. The
    honest guard against it is looking at the picture; this catches only the
    grosser failure of a finish that does not respond to its subject at all.
    """
    size = 240
    engraved = apply_finish(_dome(size), finish)
    marks = ink_mask(engraved, finish)

    y, x = np.ogrid[0:size, 0:size]
    subject = np.hypot(x - size / 2, y - size / 2) <= size * 0.36
    on_subject = float(marks[subject].mean())
    on_ground = float(marks[~subject].mean())

    assert abs(on_subject - on_ground) > 0.05, (
        f"{finish} inked the subject at {on_subject:.3f} and the ground at "
        f"{on_ground:.3f} — the two are not separable"
    )


def _longest_run(mask: np.ndarray) -> int:
    """The longest unbroken horizontal run of ink, over every row."""
    best = 0
    for row in mask:
        if not row.any():
            continue
        edges = np.diff(np.r_[0, row.view(np.int8), 0])
        starts = np.flatnonzero(edges == 1)
        if starts.size:
            best = max(best, int((np.flatnonzero(edges == -1) - starts).max()))
    return best


def test_the_survey_draws_lines_and_not_only_dots():
    """A contour is a curve; grain is a scatter of dots. This asserts the
    difference, because nothing else in the suite does.

    Found by mutation: deleting the contours outright and leaving `cyanotype`
    as pure grain **passed the entire suite**. The tone response, the two
    colours, the level count, the opaque-ground separation — none of them can
    see the marks the finish is named for. That is the shape this project has
    been bitten by before: the defining feature, untested.

    Measured on a lit dome, longest ink run as a fraction of the frame — the
    finish as it ships against the same finish with its contours removed:

        240 px  0.071 vs 0.046      480 px  0.060 vs 0.027
        360 px  0.069 vs 0.028      700 px  0.056 vs 0.029

    A proxy, and worth naming as one: it asserts that lines exist, not that
    they follow the elevation correctly. Whether the rings land on the atoms is
    a question for the eye, and the answer to it is in `docs/views.md`.
    """
    size = 360
    marks = ink_mask(apply_finish(_dome(size), "cyanotype"), "cyanotype")

    assert _longest_run(marks) / size > 0.045, (
        "the survey drew no runs longer than its grain — the contours are gone"
    )


def test_the_survey_inks_exactly_the_tone_it_was_asked_for():
    """The grain's coverage is the tone, not approximately the tone.

    That exactness is bought by rank-equalising the lattice: once the threshold
    field is uniform, `field < c` covers a fraction `c` by construction, for
    every c at once. Drop the equalisation for a plain normalisation and the
    finish still darkens monotonically and still passes every other test here —
    measured, it does — so this is the only guard on it.
    """
    style = FINISHES["cyanotype"]
    # `FINISHES` is typed by the base style, which knows nothing about grain.
    # Asserted rather than cast, so the test says what it needs of the finish.
    assert isinstance(style, _Survey)
    curve = style.tone_curve

    for tone in (200, 160, 120, 80, 40):
        inked = ink_fraction(apply_finish(_flat(tone), "cyanotype"), "cyanotype")
        wanted = (1.0 - tone / 255.0) ** curve

        assert inked == pytest.approx(wanted, abs=0.004), (
            f"tone {tone} inked {inked} where its tone curve asks for {wanted:.4f}"
        )


def _two_families(size: int = 240) -> Image.Image:
    """Two lit domes in different hues on an opaque ground: a spacefill in
    miniature, coloured the way an element theme colours one."""
    y, x = np.ogrid[0:size, 0:size]
    field = np.full((size, size, 3), 200.0)
    for across, hue in ((0.34, (0.25, 0.75, 0.30)), (0.68, (0.85, 0.25, 0.22))):
        cx, cy, radius = across * size, 0.5 * size, size * 0.24
        r = np.hypot(x - cx, y - cy) / radius
        inside = r <= 1.0
        shade = np.clip(0.25 + 0.75 * np.sqrt(np.clip(1.0 - r**2, 0.0, 1.0)), 0.0, 1.0)
        for channel in range(3):
            field[:, :, channel] = np.where(
                inside, shade * hue[channel] * 255.0, field[:, :, channel]
            )
    out = np.dstack([field.astype(np.uint8), np.full((size, size, 1), 255, np.uint8)])
    return Image.fromarray(out, "RGBA")


def _inks_on_the_page(engraved: Image.Image, finish: str) -> set[tuple[int, int, int]]:
    """Every colour other than the paper that actually reached the page."""
    paper = FINISHES[finish].paper
    seen = {tuple(c) for c in np.asarray(engraved)[:, :, :3].reshape(-1, 3).tolist()}
    return {c for c in seen if c != paper}


def test_a_plate_print_sorts_by_colour_and_goes_blind_without_it():
    """The channel, stated as something that can fail.

    `spot-ink-plates` claims that which plate a region prints on follows the
    colour family it had in the render. If that is true, taking the colour away
    must take the separation with it — and it does, categorically rather than
    by a margin: the same subject in colour reaches the paper in two inks and
    their crossing, and its own greyscale reaches it in one ink and nothing
    else.

    Measured on the bake-off's myoglobin as well as on this fixture: in colour,
    madder 0.261, indigo 0.083, crossing 0.010; in greyscale, madder 0.364 and
    no other ink at all. `hedcut` is the control — it reads only tone, and
    differs by 0.0022 of the frame between the two, so it could neither pass
    this nor be expected to.
    """
    subject = _two_families()
    px = np.asarray(subject).astype(float)
    luma = 0.299 * px[:, :, 0] + 0.587 * px[:, :, 1] + 0.114 * px[:, :, 2]
    greyed = Image.fromarray(
        np.dstack([np.repeat(luma[:, :, None], 3, axis=2), px[:, :, 3:4]]).astype(
            np.uint8
        ),
        "RGBA",
    )

    coloured = _inks_on_the_page(
        apply_finish(subject, "spot-ink-plates"), "spot-ink-plates"
    )
    grey = _inks_on_the_page(apply_finish(greyed, "spot-ink-plates"), "spot-ink-plates")

    assert len(coloured) > 1, f"colour separated onto one plate only: {coloured}"
    assert len(grey) == 1, f"greyscale still separated onto {len(grey)}: {grey}"


def test_the_plates_cross_where_they_disagree_about_a_boundary():
    """The crossing colour is the reason to print in two inks, and it very
    nearly never happened.

    Family assignment is exclusive — a pixel belongs to one plate — so shifting
    each plate's *screen* off register moves its dots and leaves the regions
    pinned, and no two plates can ever cover the same pixel. The first version
    did exactly that: the overlap was declared in the palette, described in the
    docstring, and reached the page **zero times**. Found by counting what
    actually printed, not by reading the code.

    The separation now travels off register with its screen, so the plates
    disagree about where a region ends and the overlap appears as a fringe
    along every boundary — which is what misregistration looks like on paper.
    """
    crossed = _multiply(FINISHES["spot-ink-plates"].inks)

    reached = _inks_on_the_page(
        apply_finish(_two_families(), "spot-ink-plates"), "spot-ink-plates"
    )

    assert crossed in reached, (
        f"the plates never crossed: {crossed} is declared in the palette and "
        f"only {sorted(reached)} reached the paper"
    )


def test_a_finish_prints_in_the_colours_it_declared(monkeypatch):
    """Both finishes that ship print black on white, which is exactly the
    coincidence that hid the old bug.

    So nothing else in this file can tell whether `apply_finish` reads the
    declared colours or has them written into it — a finish declaring anything
    else is the only thing that asks the question. Registered here rather than
    shipped, because PR-sized honesty says a finish exists when someone chose
    how it looks, not when a test needed one.
    """
    # A *warm* ink, chosen deliberately. The measure this replaced asked
    # whether the red channel was zero, and a Prussian blue — the obvious
    # blueprint colour, and the one this test reached for first — has a red
    # channel of zero, so it would have satisfied the old code by accident and
    # proved nothing. Madder red does not.
    cream = (247, 240, 214)
    madder = (163, 41, 38)
    monkeypatch.setitem(
        FINISHES,
        "test-madder",
        replace(FINISHES["hedcut"], paper=cream, inks=(madder,)),
    )

    engraved = apply_finish(_flat(128), "test-madder")
    colours = {tuple(c) for c in np.asarray(engraved)[:, :, :3].reshape(-1, 3).tolist()}

    assert colours == {cream, madder}, f"printed {colours}, not what it declared"

    # And the number the reply carries counts ink against *that* paper. Under
    # the measure this replaced, neither colour here has a zero red channel, so
    # a page with ink on it would have been reported as blank.
    inked = ink_fraction(engraved, "test-madder")
    assert 0.0 < inked < 1.0, f"a mid grey came back at {inked}"
    assert inked == pytest.approx(_ink(apply_finish(_flat(128), "hedcut"), "hedcut"))


@pytest.mark.parametrize("call", [apply_finish, ink_fraction, ink_mask])
def test_an_unknown_finish_names_the_ones_that_exist(call):
    """`ValueError`, not `KeyError`: `str(KeyError(msg))` is `repr(msg)`, so a
    caller has to strip the quotes back off and a name containing one arrives
    mangled — measured, a finish named `a'b` reached the model with a literal
    backslash in it.

    The expected list is **derived**, not written out. It was written out, and
    adding a finish then failed three tests that have nothing to do with the
    new finish and everything to do with the list having been copied — which is
    this repo's most-repeated defect in its smallest form.
    """
    with pytest.raises(ValueError, match=re.escape(", ".join(FINISH_NAMES))):
        call(_flat(128), "woodblock")


def test_two_plates_make_a_third_colour_where_they_cross():
    """The overlap is the point of a spot print, and no shipped finish has two
    inks yet — so without this the whole overlap branch of `palette()` ships
    untested. Mutation confirmed it: deleting the branch outright passed.

    Madder and indigo, from the plan's dyed-wool palette, on white.
    """
    two = replace(
        FINISHES["hedcut"], inks=((163, 41, 38), (41, 61, 107)), paper=(255, 255, 255)
    )
    palette = two.palette()

    assert len(palette) == 4, f"two inks should make four colours, not {len(palette)}"
    assert (255, 255, 255) in palette, "the paper"
    assert (163, 41, 38) in palette and (41, 61, 107) in palette, "each ink alone"
    # Multiplied, not averaged: ink over ink subtracts light, so the overlap is
    # darker than either plate rather than sitting between them.
    crossed = _multiply(((163, 41, 38), (41, 61, 107)))
    assert crossed in palette, "the crossing"
    assert crossed == (26, 10, 16), f"the overlap came out {crossed}"
    assert crossed[0] < min(163, 41), "the crossing is lighter than either plate"


def test_a_finish_must_declare_an_ink():
    """A finish with an empty palette would print its paper everywhere and
    report a perfectly good ink fraction of zero — the silent-success shape,
    arriving through the one number built to prevent it."""
    with pytest.raises(ValueError, match="at least one ink"):
        replace(FINISHES["hedcut"], inks=())


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_a_finish_may_print_only_from_the_palette_it_declares(finish):
    """`palette()` is what the page is checked against, so it has to be right.

    A finish with one ink may print two colours. A finish with two may print
    four — the paper, each ink, and the overlap — because ink multiplies what
    is under it rather than replacing it, which is where a spot print's third
    colour comes from. Derived rather than declared, so a finish cannot name
    one palette and print another.
    """
    style = FINISHES[finish]
    palette = style.palette()

    assert style.paper in palette, "the paper is not in the finish's own palette"
    for ink in style.inks:
        assert ink in palette, f"{ink} prints nothing"
    assert len(palette) == 2 ** len(style.inks), (
        f"{finish} declares {len(style.inks)} inks, which is "
        f"{2 ** len(style.inks)} possible colours, but its palette holds "
        f"{len(palette)}"
    )


@pytest.mark.parametrize(
    "colour", [(255, 255), (300, 0, 0), (-1, 0, 0), (255, 255, 255, 255)]
)
def test_a_malformed_colour_is_refused_where_it_is_declared(colour):
    """Not where it prints. Left to numpy these surface as a broadcasting
    error, or an `OverflowError` on a channel of 300, from inside a finish that
    has already been handed a figure-resolution capture to ruin."""
    with pytest.raises(ValueError, match="three channels"):
        replace(FINISHES["hedcut"], inks=(colour,))


def test_ink_fraction_says_when_the_tone_had_nowhere_to_go():
    """A dark ground engraves to almost solid ink, and the caller is usually a
    model that cannot look at the file."""
    assert ink_fraction(apply_finish(_flat(255), "hedcut"), "hedcut") == 0.0
    # Only true black fills completely: the tone curve puts a value of 10 in
    # the second-darkest band, at five sixths ink, which is the point of
    # having bands at all.
    assert ink_fraction(apply_finish(_flat(0), "hedcut"), "hedcut") == 1.0
    assert ink_fraction(apply_finish(_flat(10), "hedcut"), "hedcut") > 0.8
    middle = ink_fraction(apply_finish(_flat(150), "hedcut"), "hedcut")
    assert 0.0 < middle < 0.5


def test_ink_fraction_ignores_what_was_never_drawn():
    """Transparent pixels are not pale ones; counting them would report every
    cropped capture as mostly paper."""
    assert ink_fraction(apply_finish(_flat(128, alpha=0), "hedcut"), "hedcut") == 0.0


def test_no_two_finishes_share_a_grain_lattice_at_the_test_size():
    """A guard on the guard above, aimed at the mechanism rather than the number.

    `test_no_two_finishes_draw_the_same_picture` compares ink masks, so it can
    only see a difference the fixture is large enough to render. `_grain`
    resolves its step as `max(2.0, diagonal * pitch)`: below a certain size
    every fine finish clamps to the same 2 px floor and draws the same lattice,
    and the comparison scores a perfect 0.0000 while reporting a failure whose
    cause looks like the finishes rather than the fixture. That is what
    happened when `engraving` was added — cyanotype and engraving disagreed on
    nothing at 240 px and on 0.4811 of the frame at 1200.

    This asserts the fixture can still tell them apart, so shrinking `_flat`
    or adding a finer finish fails *here*, naming the real reason.
    """
    diagonal = float(np.hypot(_ENGRAVABLE, _ENGRAVABLE))
    steps = {
        name: max(2.0, diagonal * style.pitch)
        for name, style in FINISHES.items()
        if hasattr(style, "pitch")
    }
    assert len(steps) >= 2, f"expected at least two grained finishes, got {steps}"
    collided = [name for name, step in steps.items() if step <= 2.0]
    assert not collided, (
        f"at {_ENGRAVABLE}px these finishes are pinned to the 2px grain floor "
        f"and cannot be told apart: {sorted(collided)}. Raise _ENGRAVABLE. "
        f"Resolved steps: { {n: round(v, 2) for n, v in steps.items()} }"
    )
    assert len({round(v, 2) for v in steps.values()}) == len(steps), (
        f"two finishes resolve to the same grain step: "
        f"{ {n: round(v, 2) for n, v in steps.items()} }"
    )


# -- the hatch that follows the form -------------------------------------------
#
# Everything above draws at 240 or 480 px. `_Engraving`'s interval resolves as
# `max(4.0, longest * spacing)` and `_Lozenge`'s as `max(least, ...)`, so at
# those sizes EVERY hatching finish clamps to its own floor and the suite has
# only ever seen the finest mark each one can draw. The product draws a 1890 px
# plate, where `hedcut` is 5 px and `cross-hatch` 4 — and where the old
# `cross-hatch` was 17 px against a 40 px atom, which is how "the hatches are
# way too coarse" shipped with every test in this file green.
#
# So these three draw at plate size, on a subject shaped like the one the
# product actually draws: many small overlapping spheres, not one large one.
# They are the only tests here that do, and together they cost about a second.

#: The plate the product draws: 160 mm at 300 dpi, which is what `snapshot()`
#: returns for a double-column figure. Not a round number chosen for the test —
#: the point is to draw exactly what ships.
_A_PLATE = (1890, 956)

#: A van der Waals sphere is about a fortieth of the plate's long side.
#: Measured off a real 1890 px spacefill capture of 1UBQ, where an atom is
#: about 40 px across, rather than assumed.
_FEATURE = _A_PLATE[0] / 40

#: The tone a real element-coloured spacefill sits at: the median luma over its
#: subject. Measured on that same capture. The fixture is built to match,
#: because it is the thing the first version of it got wrong — spheres lit to a
#: median of 0.72 gave a coverage of 0.04, and every finish then scored at
#: chance for want of any ink to land anywhere.
_A_REAL_TONE = 0.32


def _spheres(size: tuple[int, int] = _A_PLATE) -> Image.Image:
    """A field of small overlapping spheres: the shape of a spacefill capture.

    `_dome` is one sphere filling a third of the frame, which is the wrong
    fixture for anything about mark size — at 240 px it is a 173 px feature
    against a 4 px interval, 43 marks per feature, where the product draws a
    40 px feature against 17, 2.35 per feature. Eighteen times better sampled,
    and that gap is exactly where "too coarse" hid for the finish's whole life.

    Laid on a jittered lattice rather than at random, so the picture is
    repeatable and never resolves into a grid. Overlapping on purpose, and
    depth-sorted rather than blended: the rim where one sphere passes in front
    of another is the line a draughtsman would draw, and a max-blend has no
    such edges in it at all.

    Written per bounding box rather than over the whole array. The first
    version touched a two-megapixel array once per sphere and took over two
    minutes; this takes 0.1 s and draws the same picture.
    """
    width, height = size
    field = np.ones((height, width))
    depth = np.full((height, width), -np.inf)
    radius = _FEATURE / 2
    step = radius * 1.35
    for row in range(int(height / step) + 2):
        for column in range(int(width / step) + 2):
            # A repeatable jitter, so the same fixture comes back the same way
            # twice — a finish drawn twice must give the same pixels.
            wobble = ((row * 73856093) ^ (column * 19349663)) % 1000 / 1000.0
            cx = column * step + (wobble - 0.5) * step * 0.7
            cy = row * step + (((wobble * 7919) % 1000) / 1000.0 - 0.5) * step * 0.7
            left, right = max(0, int(cx - radius) - 1), min(width, int(cx + radius) + 2)
            top, bottom = max(0, int(cy - radius) - 1), min(height, int(cy + radius) + 2)
            if left >= right or top >= bottom:
                continue
            y, x = np.ogrid[top:bottom, left:right]
            r2 = ((x - cx) ** 2 + (y - cy) ** 2) / radius**2
            inside = r2 <= 1.0
            if not inside.any():
                continue
            z = np.sqrt(np.clip(1.0 - r2, 0.0, 1.0))
            # Lambert from up and to the left, where the viewer's default light
            # sits, over a base and span that put the median at _A_REAL_TONE.
            lit = np.clip(
                -0.45 * (x - cx) / radius - 0.45 * (y - cy) / radius + z, 0, None
            )
            tone = np.clip(0.03 + 0.35 * lit, 0.0, 1.0)
            nearer = inside & (z > depth[top:bottom, left:right])
            patch = field[top:bottom, left:right]
            field[top:bottom, left:right] = np.where(nearer, tone, patch)
            depth[top:bottom, left:right] = np.where(
                nearer, z, depth[top:bottom, left:right]
            )
    grey = (field * 255).astype(np.uint8)
    rgb = np.repeat(grey[:, :, None], 3, axis=2)
    return Image.fromarray(
        np.dstack([rgb, np.full((height, width, 1), 255, dtype=np.uint8)]), "RGBA"
    )


def _lozenges() -> list[str]:
    """The finishes built on the warped carrier, derived rather than named."""
    return [name for name, style in FINISHES.items() if isinstance(style, _Lozenge)]


def _subject_and_rims(source: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Where the drawing is, and where its form has an edge.

    A rim is the steepest decile of the shading: where one sphere passes in
    front of another, which is the line a draughtsman would draw.
    """
    grey = np.asarray(source.convert("L"), dtype=np.float64) / 255.0
    subject = grey < _PAPER
    gradient_y, gradient_x = np.gradient(_blur(grey, max(source.size) / 500.0))
    steep = np.hypot(gradient_x, gradient_y)
    return subject, subject & (steep > np.percentile(steep[subject], 90.0))


def test_the_fixture_is_the_picture_the_product_draws():
    """A guard on the two guards below, aimed at the fixture rather than a finish.

    Both of them measure how ink falls across a form, and both are meaningless
    if the fixture has no ink or no form. The first version of `_spheres` was
    lit far too brightly — median subject luma 0.72 against a real capture's
    0.32 — so coverage came out at 0.04, and every finish scored at chance for
    want of anything on the page. That failure looked exactly like a broken
    finish and was a broken fixture, which is the confusion this asserts away.
    """
    source = _spheres()
    subject, _ = _subject_and_rims(source)
    grey = np.asarray(source.convert("L"), dtype=np.float64) / 255.0
    tone = float(np.median(grey[subject]))
    assert abs(tone - _A_REAL_TONE) < 0.06, (
        f"the fixture sits at a median tone of {tone:.3f} where a real capture "
        f"is {_A_REAL_TONE} — it is no longer the picture the product draws, "
        f"and the tests below will report a finish's failure instead of this one"
    )
    # And it must be many small features, not one large one: that is the whole
    # difference from `_dome` and the reason these tests exist.
    assert max(_A_PLATE) / 20 > _FEATURE, "the fixture's spheres are not small"


def test_the_suite_can_see_the_interval_a_finish_actually_ships():
    """A finish's declared interval must be the one it draws at plate size.

    The cheap half of the lesson, and it needs no render. Every other test in
    this file draws at 240 or 480 px, where `max(floor, longest * spacing)`
    returns the *floor* for every finish here — so a finish could declare any
    interval at all, ship it, and nothing else in this file would change. That
    is what happened: `cross-hatch` shipped a 17 px mark for its whole life
    while the suite drew it at 4.
    """
    assert _lozenges(), "no _Lozenge finishes: this file is testing nothing"
    for name in (*_lozenges(), "hedcut"):
        style = FINISHES[name]
        # Only the two stroke-ruling families declare an interval; `_Survey`
        # and `_Plates` size their marks with `pitch` instead.
        assert isinstance(style, _Lozenge | _Engraving), name
        assert style.spacing is not None, (
            f"{name} declares no interval of its own, so it draws "
            f"apply_finish's — which is 17 px on the plate it ships at"
        )
        floor = style.least if isinstance(style, _Lozenge) else 4.0
        declared = max(_A_PLATE) * style.spacing
        assert declared > floor, (
            f"{name} declares an interval of {declared:.2f} px on the plate it "
            f"ships at, which is under its own {floor} px floor — so the number "
            f"in FINISHES is decorative and the finish draws the floor instead"
        )
        # And the sizes the rest of the file uses genuinely cannot see it,
        # which is the reason these tests carry the cost of a whole plate.
        assert 480 * style.spacing < floor, (
            f"{name} no longer clamps at the 480 px fixture, so the cheap tests "
            f"above can see its interval and this comment is stale"
        )


@pytest.mark.parametrize("finish", _lozenges())
def test_a_hatch_lands_its_ink_on_the_form(finish):
    """The marks must go where the form has an edge, not merely where it is dark.

    This is the whole difference between the hatching and what it replaced.
    Chance-corrected, so a finish cannot pass by laying down more ink: the
    number is how much more likely a rim pixel is to be inked than any other
    pixel of the subject, and 0 is a mark field laid without reference to what
    is underneath.

    Measured on a real 1890 px spacefill capture, the old `cross-hatch` scored
    +0.014 and `hedcut` +0.004 — and **drawing them finer never moved it**.
    Swept from a 17 px interval down to 2, the best either managed was +0.026.

    On this fixture the two hatches score about +0.18. The control is `hedcut`,
    which is deliberately not in this list and scores **+0.086**: it rules one
    direction and thickens it regardless of what is underneath, and that is its
    style rather than a defect. So the bar sits between the two, and a hatch
    that regressed to ruling a screen would fail here rather than pass.
    """
    source = _spheres()
    subject, rim = _subject_and_rims(source)
    inked = ink_mask(apply_finish(source, finish), finish)

    lift = float(inked[rim].mean() - inked[subject].mean())
    assert lift > 0.12, (
        f"{finish} put ink on the form's edges no more often than anywhere "
        f"else (lift {lift:+.4f}); it is ruling a screen over a silhouette"
    )


@pytest.mark.parametrize("finish", _lozenges())
def test_the_warp_is_what_draws_the_form(finish):
    """Take the warp out and the picture must change, a lot.

    **No aggregate number over the frame can see this**, and that is why the
    guard is a differential. Setting `relief` to 0 leaves straight ruled lines
    with the rim-swelling still on, and that mutant scores every scalar the
    shipped finish does: on a real capture, ink 0.506 against 0.507, the rim
    lift above +0.259 against +0.259, tone fidelity 0.934 against 0.936. The
    pictures are not remotely alike — one is a flat ruling with dark blobs
    where atoms meet, the other has strokes that bend over every dome.

    So this compares the finish against **itself with the mechanism removed**,
    which is the arm `test_shuffle_differential.py` uses for the same reason.
    On real captures the warp moves 0.42 of a spacefill subject and 0.32 of a
    cartoon.
    """
    source = _spheres()
    subject, _ = _subject_and_rims(source)

    style = FINISHES[finish]
    assert isinstance(style, _Lozenge), finish
    live = ink_mask(apply_finish(source, finish), finish)
    flattened: dict[str, _Style] = dict(FINISHES)
    flattened[finish] = replace(style, relief=0.0)
    with mock.patch.dict(FINISHES, flattened):
        flat = ink_mask(apply_finish(source, finish), finish)

    moved = float((live ^ flat)[subject].mean())
    assert moved > 0.15, (
        f"{finish} draws the same picture with its warp switched off "
        f"(moved {moved:.4f} of the subject) — the strokes are not following "
        f"anything, and every other number in this file would still pass"
    )
