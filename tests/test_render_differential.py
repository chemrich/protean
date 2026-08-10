"""The pixel harness against real Mol* output.

`test_pixels.py` proves the detectors work on images whose answer we set. This
proves they work on images Mol* produced, which is a different claim: a
harness can be perfectly correct about synthetic PNGs and still be pointed at
the wrong thing — the wrong canvas, a stale frame, an empty viewport — and
report a serene 0.0 forever.

The load-bearing test here is the hide/unhide pair. A coverage number on its
own is unfalsifiable; the same number taken with the molecule hidden and again
with it shown is what demonstrates the harness is reading the scene rather than
a constant. Every Phase 4 feature will lean on that pattern.

Requires a real browser and is opt-in:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_render_differential.py
"""

from __future__ import annotations

import pytest

from .browser import BROWSER_MARKS, viewer_session
from .pixels import (
    Render,
    background,
    corners,
    coverage,
    decode,
    mean_distance_from,
    opaque,
    transparent_fraction,
)

pytestmark = BROWSER_MARKS

FIXTURE = "1ubq"

# Measured, not guessed. 1UBQ in a 1140x1278 viewport covers 0.0715 of the
# frame drawn and exactly 0.0000 hidden, so these sit with ~3.5x margin either
# side. Deliberately loose: the exact fraction depends on the viewport aspect
# and where Mol*'s default camera parks, neither of which this suite is trying
# to pin down. The claim is the *gap* between drawn and not drawn, not its size.
DRAWN = 0.02
BLANK = 0.002


async def _shot(session) -> Render:
    result = await session.request("screenshot", {})
    return decode(result["data_uri"])


@pytest.fixture(scope="module")
async def frames() -> dict[str, Render]:
    """One session, three frames: as loaded, with the molecule hidden, restored.

    Taken in a single session on purpose. Three separate browser launches could
    differ in viewport size or camera, and then a coverage difference would say
    nothing about whether anything was hidden.
    """
    async with viewer_session(FIXTURE) as session:
        drawn = await _shot(session)
        await session.request("hide", {"name": "auto"})
        hidden = await _shot(session)
        await session.request("unhide", {"name": "auto"})
        restored = await _shot(session)
    return {"drawn": drawn, "hidden": hidden, "restored": restored}


async def test_a_render_decodes_to_a_real_image(frames):
    """The floor: Mol* returned a PNG with pixels in it."""
    render = frames["drawn"]
    assert render.width > 0
    assert render.height > 0
    assert render.pixels.shape == (render.height, render.width, 4)


async def test_the_default_canvas_is_uniform_and_opaque(frames):
    """Establishes the baseline every background assertion will move away from.

    Mol* renders onto an opaque canvas by default, so `transparent_fraction`
    reads 0.0 here — which is exactly why transparency needs its own feature
    work and cannot be inferred from what the viewer already does.
    """
    hidden = frames["hidden"]
    assert opaque(hidden)
    colour = background(hidden)  # raises if the corners disagree
    assert colour[3] == 255


async def test_the_molecule_is_actually_on_screen(frames):
    """The guard against the oldest failure in this project.

    A load that succeeds and draws nothing has happened here more than once,
    and until now nothing in the suite would have noticed: every count came
    from the Python side, and byte size was only ever a hint.
    """
    assert coverage(frames["drawn"]) > DRAWN


async def test_hiding_the_molecule_empties_the_frame(frames):
    """The differential claim that makes the number above mean something.

    If coverage were reading a constant — the wrong canvas, a cached frame —
    it would report the same figure with the structure hidden. It does not.
    """
    hidden = frames["hidden"]
    assert coverage(hidden) < BLANK
    assert coverage(frames["drawn"]) > coverage(hidden) * 10


async def test_unhiding_puts_it_back(frames):
    """Rules out a one-way failure: something that empties the frame for good.

    Without this, a `hide` that broke the renderer outright would satisfy the
    test above perfectly.
    """
    assert coverage(frames["restored"]) > DRAWN


# -- background and opacity ----------------------------------------------------


@pytest.fixture(scope="module")
async def styled() -> dict[str, object]:
    """One session walking the two features this PR adds.

    Sequenced so each frame differs from the last in exactly one respect, and
    the replies are kept alongside the pixels: the tool claims something about
    the canvas, and the picture is where that claim is checked.
    """
    async with viewer_session(FIXTURE) as session:
        solid = await _shot(session)

        await session.request("opacity", {"name": "auto", "opacity": 0.25})
        faint = await _shot(session)
        await session.request("opacity", {"name": "auto", "opacity": 1.0})

        red_reply = await session.request("background", {"color": "#ff0000"})
        red = await _shot(session)

        clear_reply = await session.request("background", {"transparent": True})
        clear = await _shot(session)

    return {
        "solid": solid,
        "faint": faint,
        "red": red,
        "clear": clear,
        "red_reply": red_reply,
        "clear_reply": clear_reply,
    }


async def test_background_colour_reaches_the_pixels(styled):
    """Not the reply — the corners of the actual image."""
    assert styled["red_reply"]["background"] == "#ff0000"
    assert background(styled["red"]) == (255, 0, 0, 255)


async def test_a_transparent_background_really_has_no_pixels_in_it(styled):
    """The trap this feature is built around.

    ViewportScreenshotHelper passes its *own* `transparent` value to the image
    pass, overriding whatever the canvas holds. Setting only
    canvas3d.transparentBackground gives a transparent viewer and an opaque PNG
    from every capture — a success reply and a wrong file. This asserts on the
    capture, which is the half that was going to be wrong.
    """
    clear = styled["clear"]
    assert styled["clear_reply"]["screenshot_transparent"] is True
    assert not opaque(clear)
    # Every corner is empty, and most of the frame with it.
    assert all(pixel[3] == 0 for pixel in corners(clear).values())
    assert transparent_fraction(clear) > 0.5


async def test_the_molecule_survives_a_transparent_background(styled):
    """A blank frame would satisfy the transparency test perfectly."""
    assert coverage(styled["clear"]) > DRAWN


async def test_opacity_moves_the_drawn_pixels_toward_the_background(styled):
    """Opacity is a colour shift, not an alpha change — see tests/pixels.py.

    Over an opaque canvas Mol* composites the representation at render time, so
    the output stays fully opaque and only moves toward the background. The
    measure is the distance, and `transparent_fraction` would read 0.0 for both
    frames and look like a passing test on a feature that never worked.
    """
    solid, faint = styled["solid"], styled["faint"]
    assert opaque(solid)
    assert opaque(faint)

    reference = background(solid)
    solid_distance = mean_distance_from(solid, reference)
    faint_distance = mean_distance_from(faint, reference)

    assert faint_distance < solid_distance * 0.9
    assert faint_distance > 0


async def test_a_transparent_representation_is_still_drawn(styled):
    """Distinguishes 'faded' from 'gone'.

    Without this, `opacity` deleting the representation outright would pass the
    test above with the best score it could possibly get.
    """
    solid, faint = styled["solid"], styled["faint"]
    reference = background(solid)
    assert coverage(faint, of=reference) > coverage(solid, of=reference) * 0.5


async def test_todays_renders_carry_no_dpi(frames):
    """Records the gap that snapshot() exists to close.

    Mol* writes no pHYs chunk, so a file it produces has no physical
    resolution at all — a figure that is '300 dpi' only in the prose around
    it. When `snapshot()` lands, this test flips to asserting the stamped
    value, and until then it stops the harness from claiming DPI support it
    does not have.
    """
    assert frames["drawn"].dpi is None
