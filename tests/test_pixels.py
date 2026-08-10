"""The pixel harness checking itself, against images whose answer we set.

No browser: the point is that every detector in `pixels.py` is exercised, and
in both directions. A harness that silently returns 0.0 for everything would
make each of Phase 4's features look verified while checking nothing, which is
precisely the failure this harness exists to prevent — so each assertion is
paired with the case that must *not* pass it.
"""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from .pixels import (
    background,
    color_fraction,
    corners,
    coverage,
    decode,
    mean_distance_from,
    opaque,
    transparent_fraction,
)

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
RED = (255, 0, 0, 255)
CLEAR = (0, 0, 0, 0)


def solid(width: int, height: int, color: tuple[int, int, int, int]) -> np.ndarray:
    return np.tile(np.array(color, dtype=np.uint8), (height, width, 1))


def png(pixels: np.ndarray, **save: object) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(pixels, mode="RGBA").save(buffer, format="PNG", **save)
    return buffer.getvalue()


# -- decoding ------------------------------------------------------------------


def test_decodes_raw_bytes_base64_and_data_uri_alike():
    """All three carry the same image, and callers should not have to care."""
    raw = png(solid(4, 3, RED))
    encoded = base64.b64encode(raw).decode()

    for source in (raw, encoded, f"data:image/png;base64,{encoded}"):
        render = decode(source)
        assert render.size == (4, 3)
        assert background(render) == RED


def test_rejects_a_data_uri_that_is_not_base64():
    with pytest.raises(ValueError, match="Unexpected image encoding"):
        decode("data:image/png,%89PNG")


def test_an_rgb_png_still_decodes_to_four_channels():
    """Guards the convert('RGBA') in decode().

    Without it a 3-channel PNG arrives with a 3-long last axis, and every alpha
    assertion silently reads the blue channel instead — which for an opaque
    render is 255 often enough to look like it passed.
    """
    buffer = io.BytesIO()
    Image.fromarray(solid(4, 3, RED)[:, :, :3], mode="RGB").save(buffer, format="PNG")
    render = decode(buffer.getvalue())

    assert render.pixels.shape == (3, 4, 4)
    assert opaque(render)


def test_dpi_is_none_when_the_file_does_not_carry_it():
    """Mol* writes no pHYs chunk, so this is the state of play before snapshot()."""
    assert decode(png(solid(4, 4, RED))).dpi is None


def test_dpi_is_read_back_when_the_file_does_carry_it():
    """The check that snapshot() stamped the resolution it claims.

    Approximate because PNG stores pixels *per metre* as an integer, so 300 dpi
    round-trips as 11811 ppm and back to 299.9999. A DPI assertion that demands
    equality would fail on a correctly written file.
    """
    dpi = decode(png(solid(4, 4, RED), dpi=(300, 300))).dpi
    assert dpi is not None
    assert dpi[0] == pytest.approx(300, rel=1e-4)
    assert dpi[1] == pytest.approx(300, rel=1e-4)


# -- corners and background ----------------------------------------------------


def test_each_corner_is_reported_from_the_right_place():
    """A non-square frame with four distinct corners.

    Square test images cannot catch a row/column transposition, and distinct
    corners are what make a swapped pair visible.
    """
    pixels = solid(8, 4, BLACK)
    pixels[0, 0] = (1, 0, 0, 255)
    pixels[0, -1] = (2, 0, 0, 255)
    pixels[-1, 0] = (3, 0, 0, 255)
    pixels[-1, -1] = (4, 0, 0, 255)

    found = corners(decode(png(pixels)))
    assert found["top-left"] == (1, 0, 0, 255)
    assert found["top-right"] == (2, 0, 0, 255)
    assert found["bottom-left"] == (3, 0, 0, 255)
    assert found["bottom-right"] == (4, 0, 0, 255)


def test_background_refuses_to_answer_when_the_corners_disagree():
    """A gradient has no single background, and guessing one would hide that."""
    pixels = solid(8, 4, BLACK)
    pixels[0, 0] = WHITE
    with pytest.raises(ValueError, match="Corners disagree"):
        background(decode(png(pixels)))


def test_background_tolerates_backend_noise_but_not_a_real_difference():
    """SwiftShader and a real GPU disagree in the low bits; that is not a colour."""
    noisy = solid(8, 4, (100, 100, 100, 255))
    noisy[0, 0] = (103, 100, 100, 255)
    assert background(decode(png(noisy))) == (100, 100, 100, 255)

    different = solid(8, 4, (100, 100, 100, 255))
    different[0, 0] = (140, 100, 100, 255)
    with pytest.raises(ValueError, match="Corners disagree"):
        background(decode(png(different)))


# -- transparency --------------------------------------------------------------


def test_opaque_is_false_if_even_one_pixel_is_not():
    pixels = solid(4, 4, RED)
    assert opaque(decode(png(pixels)))

    pixels[2, 2] = (255, 0, 0, 254)
    assert not opaque(decode(png(pixels)))


def test_transparent_fraction_counts_only_fully_transparent_pixels():
    pixels = solid(10, 10, CLEAR)
    pixels[:2, :] = RED  # 20 of 100 drawn
    assert transparent_fraction(decode(png(pixels))) == pytest.approx(0.8)


def test_transparent_fraction_ignores_partly_transparent_pixels():
    """Antialiased edges come back at partial alpha, and they are *drawn*.

    Counting anything below 255 as transparent would inflate the figure by the
    whole silhouette of the molecule, which on a thin cartoon is most of it.
    """
    pixels = solid(10, 10, CLEAR)
    pixels[3:7, :] = RED
    pixels[2, :] = (255, 0, 0, 128)  # the antialiased boundary
    pixels[7, :] = (255, 0, 0, 12)  # nearly transparent, but not transparent

    assert transparent_fraction(decode(png(pixels))) == pytest.approx(0.4)


def test_transparent_fraction_cannot_see_representation_opacity():
    """Encodes the trap the module docstring warns about.

    A half-opaque representation over an opaque canvas is composited during
    rendering: the output is fully opaque and only its *colour* moved. Anyone
    reaching for this function to check an opacity setting gets 0.0 for every
    value, which reads as a passing test on a feature that never worked.
    """
    blended = solid(10, 10, (128, 0, 0, 255))  # red at 50% over black, composited
    assert transparent_fraction(decode(png(blended))) == 0.0


# -- coverage ------------------------------------------------------------------


def test_coverage_is_zero_when_nothing_was_drawn():
    """The oldest failure in this project: success reported, nothing rendered."""
    assert coverage(decode(png(solid(10, 10, BLACK)))) == 0.0


def test_coverage_measures_what_differs_from_the_background():
    # Drawn away from the edges, because coverage() infers the background from
    # the corners and a shape running into one leaves it with no answer.
    pixels = solid(10, 10, BLACK)
    pixels[3:6, :] = RED
    assert coverage(decode(png(pixels))) == pytest.approx(0.3)


def test_coverage_on_a_transparent_background_counts_non_zero_alpha():
    """With no background colour to compare against, 'drawn' means 'has alpha'."""
    pixels = solid(10, 10, CLEAR)
    pixels[3:7, :] = RED
    assert coverage(decode(png(pixels))) == pytest.approx(0.4)


def test_coverage_needs_telling_when_the_subject_reaches_the_edge():
    """A molecule filling the frame leaves no corner to infer a background from.

    Passing the colour explicitly is the escape hatch, and this is the case
    that requires it — worth pinning, because the failure is an exception at
    the edge of the frame rather than a wrong number.
    """
    pixels = solid(10, 10, RED)
    pixels[:, :5] = BLACK
    render = decode(png(pixels))

    with pytest.raises(ValueError, match="Corners disagree"):
        coverage(render)
    assert coverage(render, of=BLACK) == pytest.approx(0.5)


# -- outline and opacity -------------------------------------------------------


def test_color_fraction_finds_a_colour_the_rest_of_the_frame_lacks():
    """How an outline gets measured: give it a colour nothing else uses."""
    pixels = solid(10, 10, BLACK)
    pixels[0, :5] = (0, 255, 0, 255)
    render = decode(png(pixels))

    assert color_fraction(render, (0, 255, 0, 255)) == pytest.approx(0.05)
    assert color_fraction(render, (255, 0, 255, 255)) == 0.0


def test_color_fraction_still_matches_a_colour_the_renderer_shifted():
    """An outline drawn in pure green does not come back as pure green.

    Backend noise and antialiasing move it a few bits, so an exact-match
    implementation would report 0.0 for an outline that is plainly there — a
    failure that looks like the feature not working.
    """
    pixels = solid(10, 10, BLACK)
    pixels[0, :3] = (0, 255, 0, 255)
    pixels[0, 3:5] = (3, 252, 2, 255)  # the same outline, as the GL backend left it

    assert color_fraction(decode(png(pixels)), (0, 255, 0, 255)) == pytest.approx(0.05)


def test_mean_distance_falls_as_a_representation_becomes_more_transparent():
    """The behavioural claim behind the opacity check.

    Both frames draw the same shape in the same place; only the blend toward
    the background differs. If this ordering ever inverts, the opacity tests
    downstream are measuring something else.
    """
    opaque_shape = solid(10, 10, BLACK)
    opaque_shape[:5, :] = (255, 0, 0, 255)

    faint_shape = solid(10, 10, BLACK)
    faint_shape[:5, :] = (64, 0, 0, 255)  # the same red, blended 25% over black

    solid_distance = mean_distance_from(decode(png(opaque_shape)), BLACK)
    faint_distance = mean_distance_from(decode(png(faint_shape)), BLACK)

    assert faint_distance < solid_distance
    assert faint_distance > 0


def test_mean_distance_ignores_how_much_of_the_frame_is_filled():
    """So that moving the camera does not read as an opacity change."""
    small = solid(20, 20, BLACK)
    small[:2, :] = RED

    large = solid(20, 20, BLACK)
    large[:15, :] = RED

    assert mean_distance_from(decode(png(small)), BLACK) == pytest.approx(
        mean_distance_from(decode(png(large)), BLACK)
    )


def test_mean_distance_is_zero_on_an_empty_frame():
    """No drawn pixels must not become a division by zero."""
    assert mean_distance_from(decode(png(solid(10, 10, BLACK))), BLACK) == 0.0
