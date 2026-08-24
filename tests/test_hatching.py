"""The engraving finish, tested on images whose answer we set.

Pure image processing, so unlike the render suite this can assert exactly
rather than within a threshold: a flat grey has a known tone, and how much ink
it earns is a property rather than a measurement.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from protean_mcp.analysis.hatching import (
    FINISHES,
    _Survey,
    apply_finish,
    ink_fraction,
    ink_mask,
)

FINISH_NAMES = sorted(FINISHES)


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

    declared = {style.paper, style.ink}
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
                ink_mask(apply_finish(_flat(tone), left), left)
                ^ ink_mask(apply_finish(_flat(tone), right), right)
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
        replace(FINISHES["hedcut"], paper=cream, ink=madder),
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
    backslash in it."""
    with pytest.raises(ValueError, match="cross-hatch, cyanotype, hedcut"):
        call(_flat(128), "woodblock")


@pytest.mark.parametrize(
    "colour", [(255, 255), (300, 0, 0), (-1, 0, 0), (255, 255, 255, 255)]
)
def test_a_malformed_colour_is_refused_where_it_is_declared(colour):
    """Not where it prints. Left to numpy these surface as a broadcasting
    error, or an `OverflowError` on a channel of 300, from inside a finish that
    has already been handed a figure-resolution capture to ruin."""
    with pytest.raises(ValueError, match="three channels"):
        replace(FINISHES["hedcut"], ink=colour)


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
