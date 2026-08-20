"""The engraving finish, tested on images whose answer we set.

Pure image processing, so unlike the render suite this can assert exactly
rather than within a threshold: a flat grey has a known tone, and how much ink
it earns is a property rather than a measurement.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from protean_mcp.analysis.hatching import FINISHES, apply_finish, ink_fraction

FINISH_NAMES = sorted(FINISHES)


def _flat(value: int, size: int = 240, alpha: int = 255) -> Image.Image:
    """A square of one tone, which is the cleanest thing to engrave."""
    return Image.fromarray(
        np.full((size, size, 4), (value, value, value, alpha), dtype=np.uint8),
        mode="RGBA",
    )


def _ink(image: Image.Image) -> float:
    """Fraction of the opaque area that came back black."""
    pixels = np.asarray(image)
    opaque = pixels[:, :, 3] > 0
    if not opaque.any():
        return 0.0
    return float((pixels[:, :, 0][opaque] == 0).mean())


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_darker_tone_earns_more_ink(finish):
    """The whole claim of the technique: tone becomes line density."""
    coverage = [
        _ink(apply_finish(_flat(tone), finish)) for tone in (255, 200, 140, 80, 0)
    ]

    assert coverage == sorted(coverage), f"{finish} did not darken monotonically"
    assert coverage[0] == 0.0, "white took ink"
    assert coverage[-1] > 0.9, "black did not fill in"


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_a_mid_tone_becomes_neither_blank_nor_solid(finish):
    """A grey that came back all-white or all-black would mean the banding
    collapsed, which is the failure that still looks like a picture."""
    middle = _ink(apply_finish(_flat(128), finish))

    assert 0.02 < middle < 0.95, f"{finish} turned a mid grey into {middle}"


@pytest.mark.parametrize("finish", FINISH_NAMES)
def test_the_result_is_ink_on_paper_and_nothing_else(finish):
    """Two tones, no greys — that is what makes it an engraving rather than a
    filter over the original."""
    values = np.unique(np.asarray(apply_finish(_flat(128), finish))[:, :, :3])

    assert set(values.tolist()) <= {0, 255}


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

    assert _ink(apply_finish(blue, "hedcut")) > _ink(apply_finish(green, "hedcut"))


def test_the_two_finishes_do_not_draw_the_same_picture():
    """Cross-hatching crosses and a hedcut does not, so the same tone has to
    come out differently or one of them is not doing its job."""
    tone = _flat(140)

    crossed = np.asarray(apply_finish(tone, "cross-hatch"))
    engraved = np.asarray(apply_finish(tone, "hedcut"))

    assert not np.array_equal(crossed, engraved)


def test_an_unknown_finish_names_the_ones_that_exist():
    with pytest.raises(KeyError, match="cross-hatch, hedcut"):
        apply_finish(_flat(128), "woodblock")


def test_ink_fraction_says_when_the_tone_had_nowhere_to_go():
    """A dark ground engraves to almost solid ink, and the caller is usually a
    model that cannot look at the file."""
    assert ink_fraction(apply_finish(_flat(255), "hedcut")) == 0.0
    # Only true black fills completely: the tone curve puts a value of 10 in
    # the second-darkest band, at five sixths ink, which is the point of
    # having bands at all.
    assert ink_fraction(apply_finish(_flat(0), "hedcut")) == 1.0
    assert ink_fraction(apply_finish(_flat(10), "hedcut")) > 0.8
    middle = ink_fraction(apply_finish(_flat(150), "hedcut"))
    assert 0.0 < middle < 0.5


def test_ink_fraction_ignores_what_was_never_drawn():
    """Transparent pixels are not pale ones; counting them would report every
    cropped capture as mostly paper."""
    assert ink_fraction(apply_finish(_flat(128, alpha=0), "hedcut")) == 0.0
