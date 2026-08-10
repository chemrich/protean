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

from typing import Any

import pytest

from .browser import BROWSER_MARKS, viewer_session
from .pixels import (
    Render,
    background,
    color_fraction,
    corners,
    coverage,
    decode,
    difference,
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


# -- capture stability ---------------------------------------------------------


@pytest.fixture(scope="module")
async def repeated() -> list[Render]:
    """Three captures of one unchanged scene."""
    async with viewer_session(FIXTURE) as session:
        return [await _shot(session) for _ in range(3)]


async def test_the_first_capture_is_as_good_as_the_second(repeated):
    """Regression: the first screenshot of a session used to be the worst one.

    Mol* creates the ImagePass lazily, and a capture taken through a freshly
    built pass came back measurably different from every identical capture
    after it — 2.1% of the frame on 1UBQ, slightly less antialiased. For a tool
    whose product is a figure that is a real defect, and an invisible one: both
    files open fine and byte size barely moves. The screenshot action now
    builds the pass before capturing.

    Asserting equality rather than similarity is deliberate. Renders here are
    deterministic once the pass exists, so any drift at all is a finding.
    """
    assert difference(repeated[0], repeated[1]) == 0.0


async def test_captures_stay_identical_while_nothing_changes(repeated):
    """Guards the test above: two identical captures prove nothing if every
    capture is identical because the harness is reading a cached image."""
    assert difference(repeated[1], repeated[2]) == 0.0
    assert coverage(repeated[0]) > DRAWN


# -- lighting rigs -------------------------------------------------------------

# Every rig lights the same geometry, so what separates them is which pixels
# changed, not how many are drawn. Measured on 1UBQ at 170x357, where the
# molecule covers 0.0463 of the frame, this is the fraction of the whole frame
# each rig repaints relative to `standard`:
#
#   three-point 0.0167   studio 0.0223   flat 0.0307   ring 0.0312   rim 0.0376
#
# three-point is the floor because it *is* standard plus a fill and a back
# light, so the key still dominates much of the surface — and it still repaints
# 36% of the molecule. The threshold sits 1.7x below it.
#
# An earlier version of this constant was calibrated against a contaminated
# baseline: before the ImagePass fix the first capture of a session differed
# from all the others, which inflated every measured difference.
RELIT = 0.01


@pytest.fixture(scope="module")
async def lit() -> dict[str, object]:
    """One frame per rig, plus the light counts the viewer reported."""
    frames: dict[str, Render] = {}
    replies: dict[str, object] = {}
    async with viewer_session(FIXTURE) as session:
        for rig in ("standard", "flat", "three-point", "rim", "ring", "studio"):
            replies[rig] = await session.request("lighting", {"rig": rig})
            frames[rig] = await _shot(session)
        # Scale the rig twice on the way back, so `restored` also answers
        # whether intensity compounds. Mol* holds the light list by reference,
        # and a rig scaled in place would come home 9x brighter.
        await session.request("lighting", {"rig": "standard", "intensity": 3})
        await session.request("lighting", {"rig": "standard", "intensity": 3})
        await session.request("lighting", {"rig": "standard"})
        restored = await _shot(session)
    return {"frames": frames, "replies": replies, "restored": restored}


@pytest.mark.parametrize("rig", ["flat", "three-point", "rim", "ring", "studio"])
async def test_each_rig_relights_the_molecule(lit, rig):
    """The pixels move, which is the only evidence a light list was applied.

    Mol* accepts a light array without complaint; a rig that never reached the
    renderer would leave the scene exactly as it was and report success.
    """
    frames: dict[str, Render] = lit["frames"]
    assert difference(frames["standard"], frames[rig]) > RELIT


@pytest.mark.parametrize("rig", ["flat", "three-point", "rim", "ring", "studio"])
async def test_relighting_does_not_redraw_the_molecule(lit, rig):
    """Different pixels, same silhouette — the signature of a shading change.

    Without this, a "rig" that hid the structure or moved the camera would
    score brilliantly on the test above.
    """
    frames: dict[str, Render] = lit["frames"]
    reference = background(frames["standard"])
    assert coverage(frames[rig], of=reference) == pytest.approx(
        coverage(frames["standard"], of=reference), abs=0.01
    )


@pytest.mark.parametrize(
    ("rig", "lights", "ambient"),
    [
        ("standard", 1, 0.4),
        ("flat", 0, 1.0),
        ("rim", 2, 0.25),
        ("three-point", 3, 0.3),
        ("ring", 6, 0.25),
    ],
)
async def test_the_rig_reports_what_the_canvas_took(lit, rig, lights, ambient):
    """Read back off the canvas, so a rejected value cannot report as applied.

    flat is the interesting one twice over: zero directional lights is valid in
    Mol*'s shader and means purely ambient, so 0 is a real answer rather than a
    missing one — and it is the only rig whose look depends entirely on the
    ambient level rather than on the light list.
    """
    replies: dict[str, Any] = lit["replies"]
    assert replies[rig]["lights"] == lights
    assert replies[rig]["ambient"] == pytest.approx(ambient)


async def test_returning_to_standard_restores_the_original_lighting(lit):
    """Rigs are a setting, not an accumulation.

    Mol* holds the light list by reference, so a rig that scaled a shared
    preset in place would drift a little further every time it was applied and
    never come home.
    """
    frames: dict[str, Render] = lit["frames"]
    assert difference(frames["standard"], lit["restored"]) == 0.0


# -- effects, shading and gradients --------------------------------------------

# Measured on 1UBQ at 170x357 against a molecule covering 0.0463 of the frame.
# Fraction of the whole frame that changes:
#
#   outline 0.0514   xray 0.0445   xray-inverted 0.0404   cel 0.0335
#   flat 0.0306      occlusion 0.0139   shadow 0.0042
#
# Shadow is the floor by a wide margin — Mol*'s defaults are one step at a
# maximum distance of 3, which is deliberately subtle. The thresholds are set
# per group rather than globally so the loosest effect does not set the bar for
# all the others.
SHADED = 0.01
SUBTLE = 0.002
# The outline is drawn in a colour nothing else in the scene uses, so counting
# it measures the effect directly. It covers 0.00124 of the frame; the baseline
# is exactly 0.0.
OUTLINED = 0.0005

OUTLINE_GREEN = (0, 255, 0, 255)


@pytest.fixture(scope="module")
async def styled_effects() -> dict[str, Any]:
    """One session walking every effect, restoring the default after each.

    Restoring matters: these are canvas-wide and would otherwise accumulate, so
    a later frame would be measuring the sum of everything before it.
    """
    out: dict[str, Any] = {}
    async with viewer_session(FIXTURE) as session:
        out["base"] = await _shot(session)

        await session.request("effects", {"outline": True, "outline_color": "#00ff00"})
        out["outline"] = await _shot(session)
        await session.request("effects", {"outline": False})

        await session.request("effects", {"occlusion": False})
        out["no_occlusion"] = await _shot(session)
        await session.request("effects", {"occlusion": True})

        await session.request("effects", {"shadow": True})
        out["shadow"] = await _shot(session)
        out["shadow_reply"] = await session.request("effects", {"shadow": False})

        for style in ("cel", "xray", "xray-inverted", "flat"):
            await session.request("shading", {"name": "auto", "style": style})
            out[style] = await _shot(session)
        await session.request("shading", {"name": "auto", "style": "normal"})
        out["unshaded"] = await _shot(session)

        await session.request(
            "background",
            {
                "gradient": "horizontal",
                "gradient_from": "#ff0000",
                "gradient_to": "#0000ff",
            },
        )
        out["horizontal"] = await _shot(session)
        await session.request(
            "background",
            {"gradient": "radial", "gradient_from": "#ff0000", "gradient_to": "#0000ff"},
        )
        out["radial"] = await _shot(session)
        await session.request("background", {"gradient": "off"})
        out["no_gradient"] = await _shot(session)
    return out


async def test_the_outline_is_drawn_in_the_colour_it_was_given(styled_effects):
    """Colour is what makes this measurable rather than inferred.

    Given a green nothing else in the scene uses, counting green pixels says
    the outline pass ran *and* that it took the colour — two claims a
    silhouette comparison could not separate.
    """
    assert color_fraction(styled_effects["base"], OUTLINE_GREEN) == 0.0
    assert color_fraction(styled_effects["outline"], OUTLINE_GREEN) > OUTLINED


async def test_the_outline_adds_to_the_silhouette(styled_effects):
    """An outline draws around the molecule, so unlike shading it grows it.

    The opposite of the lighting invariant, and worth pinning as its own claim:
    it is the one effect here that is not purely a repaint.
    """
    reference = background(styled_effects["base"])
    assert coverage(styled_effects["outline"], of=reference) > coverage(
        styled_effects["base"], of=reference
    )


async def test_occlusion_changes_the_shading(styled_effects):
    """Occlusion is on by default, so this switches it off and looks for the gap."""
    assert difference(styled_effects["base"], styled_effects["no_occlusion"]) > SUBTLE


async def test_shadow_reaches_the_render_even_though_it_is_subtle(styled_effects):
    """Mol*'s shadow defaults are quiet — one step, distance 3.

    Pinned anyway, and with its own low threshold, because "subtle" and "never
    applied" look identical in a reply and differ by 0.0042 of the frame here.
    """
    assert difference(styled_effects["base"], styled_effects["shadow"]) > SUBTLE
    # And the reply reports it back off, since that call turned it off again.
    assert styled_effects["shadow_reply"]["shadow"] is False


@pytest.mark.parametrize("style", ["cel", "xray", "xray-inverted", "flat"])
async def test_each_shading_style_changes_the_surface(styled_effects, style):
    assert difference(styled_effects["base"], styled_effects[style]) > SHADED


async def test_xray_inverted_is_not_the_same_as_xray(styled_effects):
    """The one that would silently degrade.

    Mol* types xrayShaded as `boolean | 'inverted'`. Sending `true` for the
    inverted style would give the ordinary ghost look and report success, and
    no comparison against the *default* shading would notice — both differ from
    it. Only comparing the two against each other does.
    """
    assert difference(styled_effects["xray"], styled_effects["xray-inverted"]) > SHADED


async def test_shading_returns_to_normal(styled_effects):
    """Styles are a setting, not an accumulation."""
    assert difference(styled_effects["base"], styled_effects["unshaded"]) == 0.0


async def test_a_horizontal_gradient_runs_from_the_first_colour_to_the_second(
    styled_effects,
):
    """Mol* names the stops per variant, so the mapping is where this breaks.

    Sending the radial pair to a horizontal gradient leaves it at its pale grey
    defaults — a background that looks deliberate and is not the one asked for.
    """
    found = corners(styled_effects["horizontal"])
    assert found["top-left"][0] > 200 and found["top-left"][2] < 60  # red on top
    assert found["bottom-left"][2] > 200 and found["bottom-left"][0] < 60  # blue below
    assert found["top-left"] == found["top-right"]


async def test_a_radial_gradient_is_symmetric_about_the_centre(styled_effects):
    """Which is what makes it radial rather than horizontal.

    All four corners are the same distance from the middle, so they land on the
    same blend — and that blend is neither stop, because the ratio puts the
    turn halfway.
    """
    found = corners(styled_effects["radial"])
    assert len({tuple(c) for c in found.values()}) <= 2  # allows 1-bit noise
    blend = background(styled_effects["radial"])
    assert 0 < blend[0] < 255
    assert 0 < blend[2] < 255


async def test_turning_the_gradient_off_restores_the_flat_canvas(styled_effects):
    assert difference(styled_effects["base"], styled_effects["no_gradient"]) == 0.0
    assert background(styled_effects["no_gradient"]) == background(styled_effects["base"])


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
