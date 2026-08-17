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

import contextlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from biotite.structure.io.xtc import XTCFile
from PIL import Image as PILImage

import protean_mcp.server as server_mod
from protean_mcp.analysis.encode import ffmpeg_binary
from protean_mcp.connection import ViewerError
from protean_mcp.fetch import fetch_structure_data
from protean_mcp.selections_numpy import load_structure

from .browser import BROWSER_MARKS, PATHTRACE_MARKS, viewer_session
from .pixels import (
    Render,
    background,
    close,
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


# -- materials -----------------------------------------------------------------

# Every pair of named finishes, measured on 1UBQ under the studio rig:
#
#   matte/satin 0.0180 (closest)   metallic/chrome 0.0234   satin/glossy 0.0282
#   glossy/metallic 0.0339         matte/glossy 0.0348      matte/chrome 0.0397
#
# The threshold sits 2.2x below the closest pair. These numbers are the reason
# the finishes carry metalness at all: defined as pure dielectrics, matte and
# satin differed by 0.0001 — a named finish that changed nothing.
DISTINCT = 0.008

FINISHES = ("matte", "satin", "glossy", "metallic", "chrome")


@pytest.fixture(scope="module")
async def finishes() -> dict[str, Any]:
    """One frame per finish, under a rig with enough light to show them.

    `studio` rather than the default: a material needs a directional light to
    play off, and `flat` has none at all, so every finish would look identical
    and the suite would be measuring nothing.
    """
    frames: dict[str, Render] = {}
    replies: dict[str, Any] = {}
    async with viewer_session(FIXTURE) as session:
        await session.request("lighting", {"rig": "studio"})
        for finish in FINISHES:
            replies[finish] = await session.request(
                "material", {"name": "auto", "finish": finish}
            )
            frames[finish] = await _shot(session)
        await session.request("material", {"name": "auto", "finish": "matte"})
        restored = await _shot(session)

        dull = await session.request(
            "material", {"name": "auto", "finish": "matte", "emissive": 0.0}
        )
        frames["emissive_off"] = await _shot(session)
        glow = await session.request(
            "material", {"name": "auto", "finish": "matte", "emissive": 0.8}
        )
        frames["emissive_on"] = await _shot(session)
        await session.request("effects", {"bloom": False})
        frames["bloom_off"] = await _shot(session)
        await session.request("effects", {"bloom": True})
    return {
        "frames": frames,
        "replies": replies,
        "restored": restored,
        "dull": dull,
        "glow": glow,
    }


@pytest.mark.parametrize(
    ("a", "b"), [(a, b) for i, a in enumerate(FINISHES) for b in FINISHES[i + 1 :]]
)
async def test_every_finish_looks_different_from_every_other(finishes, a, b):
    """The invariant that stops an enum from being decorative.

    A named finish that does not change the picture is worse than no finish at
    all: it reports success and the figure comes out identical. Defined as
    physically correct dielectrics, matte and satin differed by 0.0001 — this
    test is why they carry metalness now.
    """
    frames: dict[str, Render] = finishes["frames"]
    assert difference(frames[a], frames[b]) > DISTINCT


async def test_finishes_run_from_dull_to_sharp(finishes):
    """Mol*'s own preset labels have Glossy duller than Plastic.

    Roughness is 0 for a mirror and 1 for fully diffuse, so this pins that our
    names mean what they say — the reported roughness falls as the finish gets
    sharper.
    """
    replies: dict[str, Any] = finishes["replies"]
    sharpening = [replies[f]["roughness"] for f in ("matte", "satin", "glossy")]
    assert sharpening == sorted(sharpening, reverse=True)
    assert replies["chrome"]["roughness"] < replies["metallic"]["roughness"]


async def test_returning_to_matte_restores_the_original_surface(finishes):
    assert difference(finishes["frames"]["matte"], finishes["restored"]) == 0.0


async def test_emissive_makes_the_molecule_glow(finishes):
    frames: dict[str, Render] = finishes["frames"]
    assert difference(frames["emissive_off"], frames["emissive_on"]) > DISTINCT


async def test_bloom_only_shows_where_something_is_emissive(finishes):
    """Why bloom looks broken out of the box, pinned as behaviour.

    Bloom is on by default and its default mode is `emissive`, while emissive
    itself defaults to 0 — so bloom correctly draws nothing until a material
    asks for it. The reply says which of those two states you are in, and this
    checks the reply against what the renderer actually did.
    """
    assert finishes["dull"]["bloom_will_show"] is False
    assert finishes["glow"]["bloom_will_show"] is True

    frames: dict[str, Render] = finishes["frames"]
    # With something emissive on screen, switching bloom off has to change it.
    assert difference(frames["emissive_on"], frames["bloom_off"]) > DISTINCT


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
    # Within tolerance, not bit-exact: these two corners came back one bit
    # apart in green under CI's SwiftShader while matching exactly locally.
    assert close(found["top-left"], found["top-right"])
    assert not close(found["top-left"], found["bottom-left"])


async def test_a_radial_gradient_is_symmetric_about_the_centre(styled_effects):
    """Which is what makes it radial rather than horizontal.

    All four corners are the same distance from the middle, so they land on the
    same blend — and that blend is neither stop, because the ratio puts the
    turn halfway.
    """
    found = corners(styled_effects["radial"])
    first = found["top-left"]
    assert all(close(first, c) for c in found.values())
    # A blend of the two stops actually asked for, not Mol*'s pale grey
    # defaults: red and blue both present, green absent. Without the green
    # bound this passes on a grey gradient, which is what a stop name mapped to
    # the wrong variant leaves behind.
    blend = background(styled_effects["radial"])
    assert blend[0] > 20 and blend[2] > 20
    assert blend[1] < 60


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


# -- path tracing --------------------------------------------------------------

# Opt-in and not run by CI — see PATHTRACE_MARKS in browser.py for why. Measured
# on 1UBQ at 800x600 on an Apple GPU: a traced capture repaints 0.0176 of the
# frame relative to the raster render, and costs draft 1.2s, standard 4.1s,
# high 15.8s.
TRACED = 0.008


@pytest.fixture(scope="module")
async def traced() -> dict[str, Any]:
    """A raster baseline, one frame per quality, and the way back."""
    frames: dict[str, Render] = {}
    replies: dict[str, Any] = {}
    elapsed: dict[str, int] = {}
    async with viewer_session(FIXTURE) as session:
        frames["raster"] = await _shot(session)
        for quality in ("draft", "standard", "high"):
            replies[quality] = await session.request(
                "path_trace", {"enabled": True, "quality": quality}
            )
            shot = await session.request("screenshot", {}, timeout=600)
            frames[quality] = decode(shot["data_uri"])
            elapsed[quality] = shot.get("traced_ms", 0)
        replies["off"] = await session.request("path_trace", {"enabled": False})
        frames["off"] = await _shot(session)
    return {"frames": frames, "replies": replies, "elapsed": elapsed}


class TestPathTracing:
    """Grouped so one set of marks gates them all."""

    pytestmark = PATHTRACE_MARKS

    def test_tracing_changes_the_image(self, traced):
        frames = traced["frames"]
        assert difference(frames["raster"], frames["standard"]) > TRACED

    def test_turning_it_off_restores_the_raster_render_exactly(self, traced):
        """Tracing is a mode, not a one-way door."""
        frames = traced["frames"]
        assert difference(frames["raster"], frames["off"]) == 0.0
        assert traced["replies"]["off"]["enabled"] is False

    def test_the_canvas_confirms_it_is_tracing(self, traced):
        """Read back, because IlluminationPass fails by going quiet.

        Its constructor returns early when the WebGL extensions are missing and
        the pass stays unsupported, so an unchecked enable would render an
        ordinary image and report success.
        """
        assert traced["replies"]["standard"]["enabled"] is True

    @pytest.mark.parametrize(
        ("quality", "samples"), [("draft", 8), ("standard", 32), ("high", 128)]
    )
    def test_quality_maps_to_a_sample_count(self, traced, quality, samples):
        assert traced["replies"][quality]["samples"] == samples

    def test_the_capture_reports_what_it_cost(self, traced):
        """The number that decides whether a bigger one is worth asking for."""
        assert traced["elapsed"]["standard"] > 0

    def test_more_samples_cost_more_time(self, traced):
        """The quality dial does work, even where it does not change the picture.

        On a scene this simple the denoiser converges early: draft, standard and
        high differ from each other by 0.0001 of the frame, which is nothing. So
        the honest claim is about cost, not appearance — 1.2s against 15.8s when
        measured. The ladder buys visible quality on surfaces and heavy
        transparency, which this fixture does not exercise.
        """
        assert traced["elapsed"]["high"] > traced["elapsed"]["draft"]


# -- snapshot ------------------------------------------------------------------

# Nature's double column at 600 dpi. Chosen because it is a figure someone
# would actually submit, and because it is large enough that a broken capture
# path shows up: 4323x3242 is 14 megapixels and takes about 2.5s on a real GPU.
FIGURE_PIXELS = 4323
# A size every renderer manages, including CI's software one, so the parts of
# snapshot() that are not about sheer size — dimensions, content, and putting
# the helper back — are verified everywhere rather than skipped where it counts.
MODEST_PIXELS = 1200


async def _figure_or_skip(session, path: str, **kwargs) -> dict[str, Any]:
    """Take a journal-sized snapshot, or skip if this renderer cannot.

    Software rendering runs out of room well before a GPU does: at 4323 px it
    returns an image of exactly the right dimensions with three of its four
    corners never written. `snapshot()` detects that and refuses, which is the
    behaviour worth having — so a skip here still means the tool did the right
    thing and only the environment was short.
    """
    previous = server_mod._bridge
    server_mod._bridge = session.bridge
    try:
        result: dict[str, Any] = await server_mod.snapshot(path, **kwargs)
        return result
    except ViewerError as exc:
        if "came back incomplete" in str(exc):
            pytest.skip(f"this renderer cannot capture at {FIGURE_PIXELS}px: {exc}")
        raise
    finally:
        server_mod._bridge = previous


@pytest.fixture(scope="module")
async def figure(tmp_path_factory) -> dict[str, Any]:
    """A real journal-sized figure, plus what the viewer did around it."""
    out = tmp_path_factory.mktemp("snapshots")
    async with viewer_session(FIXTURE) as session:
        before = await _shot(session)
        result = await _figure_or_skip(
            session, str(out / "figure.png"), width_mm=101.6, dpi=300
        )
        # The capture that matters: an ordinary screenshot *after* a snapshot,
        # which is where a helper left at figure resolution would show up.
        after = await _shot(session)
    return {"result": result, "before": before, "after": after, "dir": out}


async def test_a_snapshot_comes_back_at_the_size_it_was_asked_for(figure):
    # 101.6 mm is exactly 4 inches, so 300 dpi is exactly 1200 pixels.
    assert figure["result"]["pixels"][0] == MODEST_PIXELS


async def test_a_scaled_up_capture_still_draws_the_molecule(figure):
    """Right dimensions and wrong content is the failure to rule out.

    Coverage is a fraction of the frame, so it stays roughly constant as the
    pixel count grows — measured 0.0147 at 1000px and 0.0133 at 4323px on a
    GPU. A truncated capture collapses it, which is exactly what software
    rendering produces at journal size; `snapshot()` refuses those outright, so
    anything that gets here is expected to be whole.
    """
    written = Path(figure["result"]["path"])
    render = decode(written.read_bytes())
    assert render.width == MODEST_PIXELS
    assert coverage(render) > 0.005


async def test_a_snapshot_does_not_leave_the_viewer_at_figure_resolution(figure):
    """The trap this feature carries.

    The screenshot helper's settings persist, so a snapshot that failed to put
    them back would leave every later screenshot rendering a 14-megapixel image
    — slower, larger, and indistinguishable from correct in the reply.
    """
    assert figure["after"].size == figure["before"].size
    assert difference(figure["before"], figure["after"]) == 0.0


@pytest.mark.parametrize(
    ("fmt", "suffix", "mode"),
    [("png", ".png", "RGBA"), ("tiff", ".tiff", "RGBA"), ("jpeg", ".jpg", "RGB")],
)
async def test_a_real_journal_figure_reaches_disk(tmp_path, fmt, suffix, mode):
    """Phase 4's exit criterion, end to end and through the real tool.

    A double-column figure at 600 dpi, written by snapshot() itself rather than
    by the bridge, then reopened and asked what it is. The DPI assertion is the
    point: Mol* cannot write physical resolution at all, so a figure that is
    "600 dpi" only in the tool's reply would satisfy every other test here.

    Approximate for PNG because it stores pixels per *metre* as an integer, so
    600 dpi round-trips as 11811 ppm and back to 599.9988.
    """
    async with viewer_session(FIXTURE) as session:
        result = await _figure_or_skip(
            session, str(tmp_path / "figure"), column="double", dpi=600, format=fmt
        )

    written = Path(result["path"])
    assert written.suffix == suffix
    assert result["pixels"][0] == FIGURE_PIXELS  # 183 mm at 600 dpi

    with PILImage.open(written) as reopened:
        assert reopened.size == tuple(result["pixels"])
        assert reopened.mode == mode
        assert reopened.info["dpi"][0] == pytest.approx(600, rel=1e-3)

    # The physical width the file actually claims, derived from its own pixels.
    assert result["width_mm"] == pytest.approx(183.0, abs=0.1)
    assert written.stat().st_size == result["bytes"] > 0


# -- presets -------------------------------------------------------------------

# A preset that does not change the picture is worse than no preset: it reports
# success and the figure comes out identical. Threshold well below the smallest
# measured effect of any single style change this suite already covers.
STYLED = 0.008


@contextlib.asynccontextmanager
async def _as_server(session, load: bool = False):
    """Point the server module's tools at this browser session.

    `load` fills in the server's own copy of the structure, which
    `viewer_session` does not: it loads straight into the viewer, so anything
    that resolves atom indices — the ghost-surface preset, for one — finds
    nothing loaded.

    Filled in directly rather than by calling `fetch_structure()`, which would
    reload the viewer too and reframe its camera, so every before/after
    comparison in the test would be measuring the camera move instead of the
    preset. Same file and same assembly the session used, so both halves still
    describe one molecule.
    """
    previous = server_mod._bridge
    server_mod._bridge = session.bridge
    saved = (server_mod._structure, server_mod._structure_identifier)
    try:
        if load:
            fetched = await fetch_structure_data(FIXTURE)
            server_mod._structure = load_structure(
                fetched.data, fetched.format, "asymmetric"
            ).array
            server_mod._structure_identifier = FIXTURE
        yield
    finally:
        server_mod._bridge = previous
        server_mod._structure, server_mod._structure_identifier = saved


async def _apply(session, *args, **kwargs) -> dict[str, Any]:
    async with _as_server(session):
        result: dict[str, Any] = await server_mod.preset(*args, **kwargs)
        return result


@pytest.fixture(scope="module")
async def presets() -> dict[str, Any]:
    """One frame per scene-wide preset, from a common baseline."""
    frames: dict[str, Render] = {}
    async with viewer_session(FIXTURE) as session:
        frames["plain"] = await _shot(session)
        for name in ("publication-cartoon", "illustrative"):
            await _apply(session, name)
            frames[name] = await _shot(session)
    return {"frames": frames}


@pytest.mark.parametrize("name", ["publication-cartoon", "illustrative"])
async def test_a_preset_changes_the_picture(presets, name):
    frames: dict[str, Render] = presets["frames"]
    assert difference(frames["plain"], frames[name]) > STYLED


async def test_the_presets_differ_from_each_other(presets):
    """Two recipes that composed to the same thing would both 'work'."""
    frames: dict[str, Render] = presets["frames"]
    assert difference(frames["publication-cartoon"], frames["illustrative"]) > STYLED


async def test_illustrative_draws_the_outline_it_promises(presets):
    """Named for a look, so the look is what gets checked.

    The outline is black on a white ground, which nothing else in this scene
    produces, so counting near-black pixels separates "the recipe ran" from
    "the recipe produced the thing it is named after".
    """
    frames: dict[str, Render] = presets["frames"]
    black = color_fraction(frames["illustrative"], (0, 0, 0, 255), tolerance=40)
    assert black > color_fraction(frames["plain"], (0, 0, 0, 255), tolerance=40)


# -- the style presets from docs/views.md §5.1 ---------------------------------
#
# Six views borrowed from MCPymol, four of which decide what is drawn rather
# than only restyling it. Taken in one session, in this order, because a browser
# launch is the expensive part: each frame is compared with the one before it,
# and then all seven are compared with each other. The second claim is the one
# worth having — two recipes that composed to the same picture would both pass
# "it changed something" and still be one view wearing two names.

_VIEW_SEQUENCE = [
    "textbook",
    "bfactor",
    "putty",
    "hydrophobic-surface",
    "cinematic",
    "pointillist",
]


@pytest.fixture(scope="module")
async def views() -> list[tuple[str, Render]]:
    """One frame per view, in sequence, starting from the plain load."""
    taken: list[tuple[str, Render]] = []
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        taken.append(("plain", await _shot(session)))
        for name in _VIEW_SEQUENCE:
            await server_mod.preset(name)
            taken.append((name, await _shot(session)))
    return taken


@pytest.mark.parametrize("name", _VIEW_SEQUENCE)
async def test_a_view_changes_the_picture(views, name):
    """The floor: a view that reports success and draws the previous frame."""
    index = [n for n, _ in views].index(name)
    before, after = views[index - 1][1], views[index][1]
    assert difference(before, after) > STYLED


async def test_every_view_looks_different_from_every_other(views):
    for i, (left, left_frame) in enumerate(views):
        for right, right_frame in views[i + 1 :]:
            assert difference(left_frame, right_frame) > STYLED, (
                f"{left} and {right} render the same picture"
            )


async def test_a_second_view_replaces_the_first_rather_than_stacking():
    """Switching views has to end at the view, not at both views at once.

    Every view draws through one shared handle, so Mol* rebuilds that component
    instead of adding another. Checked in pixels rather than in the call log:
    the same view reached by two routes has to land on the same frame, and it
    would not if the surface were still on screen underneath the tube.

    `putty` is the view taken twice because it sets every property it depends
    on — ground, lighting, effects, shading, material, representation, colour —
    so arriving at it a second time cannot inherit anything from the detour.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("putty")
        direct = await _shot(session)

        await server_mod.preset("hydrophobic-surface")
        surfaced = await _shot(session)
        await server_mod.preset("putty")
        switched = await _shot(session)

    assert difference(direct, surfaced) > STYLED, "the surface never drew"
    assert difference(direct, switched) == 0.0


async def test_putty_width_follows_the_bfactor_it_claims():
    """The plan's one open question about these six, answered from pixels.

    docs/views.md asked whether putty's tube varies with B-factor by default or
    needs a size theme protean does not expose. Two loads of the *same*
    coordinates, one with the deposited B-factors and one with every B-factor
    flattened to their mean, isolate the answer: nothing else about the two
    files differs.

    The cartoon pair is the control, and it carries the test. Cartoon's default
    size theme is uniform, so it must read identical across the two loads —
    which is also what rules out the camera having moved when the second
    structure replaced the first. A control that moves means the putty number
    is measuring the reload.

    **The control is asserted against the signal rather than against a number.**
    The first version of this test used an absolute ceiling of 0.001, measured
    on the development machine, where the control reads 0.000125. CI read
    0.007983 and the test failed — correctly: `load_structure` did not wait for
    the camera the load preset moves, so the first capture after a load could be
    mid-tween, and a slower renderer opened the gap wide enough to see. That is
    fixed in the viewer, but the lesson about the threshold stands. A ratio says
    what the test actually claims — putty responds to B-factor and cartoon does
    not — in a form that does not depend on which machine draws it.
    """
    fetched = await fetch_structure_data(FIXTURE)
    deposited = load_structure(fetched.data, fetched.format, "asymmetric").array
    flattened = deposited.copy()
    flattened.b_factor = np.full(
        deposited.array_length(), float(deposited.b_factor.mean())
    )

    frames: dict[tuple[str, str], Render] = {}
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        for variant, array in (("deposited", deposited), ("flattened", flattened)):
            await server_mod._send_structure(array, FIXTURE)
            server_mod._structure = array
            for representation in ("cartoon", "putty"):
                await server_mod.hide(server_mod._WHOLE_SCENE)
                await server_mod.select("polymer", name="fold")
                await server_mod.show(
                    representation=representation, handle="fold", color="#ffffff"
                )
                frames[(representation, variant)] = await _shot(session)

    control = difference(
        frames[("cartoon", "deposited")], frames[("cartoon", "flattened")]
    )
    measured = difference(frames[("putty", "deposited")], frames[("putty", "flattened")])

    assert measured > STYLED, (
        f"putty drew the same tube for two different B-factor sets: {measured:.6f}"
    )
    # Measured 162x apart on the development machine (0.020219 against
    # 0.000125). Five is the floor: a renderer with more edge noise than this
    # one still has to leave the claim unambiguous, and if the two ever come
    # that close the instrument has become the subject rather than putty.
    assert control * 5 < measured, (
        f"control {control:.6f} against measured {measured:.6f}: too close to "
        "separate, so this is measuring the reload rather than B-factor"
    )


async def test_ghost_surface_layers_over_what_is_already_drawn():
    """The scoping claim, checked on screen rather than in the call log.

    A surface shown under the same handle rebuilds that component, so the
    cartoon inside would disappear and the frame would show only a surface.
    Here the ghost is its own component, so the drawn area *grows* — the
    surface is wider than the cartoon it wraps — and the scene keeps everything
    it had.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        before = await _shot(session)
        result = await server_mod.preset("ghost-surface")
        after = await _shot(session)

    reference = background(before)
    assert result["applied_to"] == "auto"
    assert difference(before, after) > STYLED
    # A molecular surface encloses the cartoon, so it covers strictly more.
    assert coverage(after, of=reference) > coverage(before, of=reference)


# -- image and skybox backgrounds ----------------------------------------------

# Colours nothing in a default Mol* scene produces, so finding them in the
# corners is unambiguous evidence the image reached the renderer.
IMAGE_COLOUR = (255, 0, 255, 255)
SKYBOX_COLOUR = (0, 255, 255, 255)


def _solid_image(path: Path, colour: tuple[int, int, int]) -> Path:
    PILImage.new("RGB", (64, 64), colour).save(path)
    return path


@pytest.fixture(scope="module")
async def imaged(tmp_path_factory) -> dict[str, Render]:
    """Frames with a flat image behind the scene, and with a cube map around it."""
    folder = tmp_path_factory.mktemp("backgrounds")
    flat = _solid_image(folder / "flat.png", IMAGE_COLOUR[:3])
    sky = folder / "sky"
    sky.mkdir()
    for face in ("nx", "ny", "nz", "px", "py", "pz"):
        _solid_image(sky / f"{face}.png", SKYBOX_COLOUR[:3])

    frames: dict[str, Render] = {}
    async with viewer_session(FIXTURE) as session, _as_server(session):
        frames["plain"] = await _shot(session)
        await server_mod.background(image=str(flat))
        frames["image"] = await _shot(session)
        await server_mod.background(skybox=str(sky))
        frames["skybox"] = await _shot(session)
        await server_mod.background(gradient="off")
        frames["off"] = await _shot(session)
    return frames


async def test_an_image_background_reaches_the_pixels(imaged):
    """The claim that cannot be made from the reply.

    Mol* takes a URL and draws nothing when it fails to load — no error, no
    field in the reply, just a background that stays as it was. The only
    evidence the image arrived is the image being on screen.
    """
    assert all(close(pixel, IMAGE_COLOUR) for pixel in corners(imaged["image"]).values())
    assert not close(background(imaged["plain"]), IMAGE_COLOUR)


async def test_a_skybox_reaches_the_pixels(imaged):
    """Six faces, one colour, so whichever the camera faces proves it loaded."""
    assert all(
        close(pixel, SKYBOX_COLOUR) for pixel in corners(imaged["skybox"]).values()
    )


async def test_the_molecule_survives_an_image_background(imaged):
    """A background that covered everything would pass the test above perfectly."""
    assert coverage(imaged["image"], of=IMAGE_COLOUR) > 0.005
    assert coverage(imaged["skybox"], of=SKYBOX_COLOUR) > 0.005


async def test_turning_the_background_off_restores_the_plain_canvas(imaged):
    assert difference(imaged["plain"], imaged["off"]) == 0.0


# -- turntable -----------------------------------------------------------------


@pytest.fixture(scope="module")
async def turned(tmp_path_factory) -> dict[str, Any]:
    """A short turn, plus the view before and after the whole sequence."""
    out = tmp_path_factory.mktemp("turntable")
    async with viewer_session(FIXTURE) as session, _as_server(session):
        before = await _shot(session)
        result = await server_mod.turntable(str(out / "turn"), frames=6, width=400)
        after = await _shot(session)
    return {"result": result, "before": before, "after": after}


async def test_a_turntable_writes_every_frame(turned):
    frames = sorted(Path(turned["result"]["directory"]).glob("frame_*.png"))
    assert len(frames) == 6
    assert turned["result"]["step_degrees"] == 60.0
    assert all(frame.stat().st_size > 0 for frame in frames)


async def test_each_frame_sees_the_molecule_from_somewhere_new(turned):
    """The camera actually moved, rather than six captures of one view.

    Compared against its immediate predecessor: a rotation this large repaints
    most of the molecule, and identical neighbours would mean the orbit never
    reached the camera.
    """
    frames = sorted(Path(turned["result"]["directory"]).glob("frame_*.png"))
    renders = [decode(frame.read_bytes()) for frame in frames]
    for index in range(1, len(renders)):
        assert difference(renders[index - 1], renders[index]) > 0.005


async def test_every_frame_still_draws_the_molecule(turned):
    """A turn that rotated the structure out of frame would pass the test above."""
    frames = sorted(Path(turned["result"]["directory"]).glob("frame_*.png"))
    for frame in frames:
        assert coverage(decode(frame.read_bytes())) > 0.005


async def test_a_full_turn_comes_back_exactly(turned):
    """The property that makes a sequence loop, and that drift would break.

    Six steps of sixty degrees, then a final sixty to close it: the view has to
    land back where it started, bit for bit. Asserting equality rather than
    similarity is what makes accumulated rounding visible — it would show up
    here long before it showed up as a jump at the seam of a movie.
    """
    assert difference(turned["before"], turned["after"]) == 0.0


# -- trajectory frames ---------------------------------------------------------

# 1L2Y is Trp-cage: 38 NMR models, so a real multi-frame structure that needs no
# trajectory file to exercise frame stepping.
MULTI_MODEL = "1l2y"


@pytest.fixture(scope="module")
async def stepped() -> dict[str, Any]:
    """Three frames of a multi-model structure, and the reply for each."""
    frames: dict[int, Render] = {}
    replies: dict[int, Any] = {}
    async with viewer_session(MULTI_MODEL) as session:
        for index in (0, 18, 37):
            replies[index] = await session.request("frame", {"index": index})
            frames[index] = await _shot(session)
        back = await session.request("frame", {"index": 0})
        frames[-1] = await _shot(session)
    return {"frames": frames, "replies": replies, "back": back}


async def test_the_viewer_finds_every_frame(stepped):
    """Read off the trajectory, not the model: the model is one frame of it."""
    assert stepped["replies"][0]["frames"] == 38


async def test_stepping_frames_changes_what_is_drawn(stepped):
    """The whole claim. A frame index that never reached the state tree would
    leave the scene exactly as it was and report success."""
    frames: dict[int, Render] = stepped["frames"]
    assert difference(frames[0], frames[18]) > 0.005
    assert difference(frames[18], frames[37]) > 0.005


async def test_every_trajectory_frame_still_draws_the_molecule(stepped):
    """A frame index past the end could empty the scene and pass the test above."""
    for index in (0, 18, 37):
        assert coverage(stepped["frames"][index]) > 0.005


async def test_a_frame_index_is_read_back_off_the_state_tree(stepped):
    assert stepped["replies"][18]["index"] == 18
    assert stepped["back"]["index"] == 0


async def test_going_back_to_a_frame_reproduces_it_exactly(stepped):
    """Frames are a setting, not a walk: returning to one shows the same thing."""
    assert difference(stepped["frames"][0], stepped["frames"][-1]) == 0.0


async def test_a_frame_outside_the_trajectory_is_refused():
    """Clamping would quietly show the wrong frame and report success."""
    async with viewer_session(MULTI_MODEL) as session:
        with pytest.raises(ViewerError, match=r"outside 0\.\.37"):
            await session.request("frame", {"index": 38})


async def test_a_single_frame_structure_says_so():
    """1UBQ is one model, so there is nothing to step through."""
    async with viewer_session(FIXTURE) as session:
        with pytest.raises(ViewerError, match="single frame"):
            await session.request("frame", {"index": 1})


async def test_a_trajectory_file_loads_and_animates(tmp_path):
    """End to end, through the tools: structure, then coordinates onto it.

    Built rather than downloaded, so the motion is known: every atom slides a
    little further each frame, which has to show as a changing picture and as a
    frame count the viewer agrees with.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        template = server_mod._require_structure()
        base = np.asarray(template.coord, dtype=np.float32)
        shift = np.array([0.0, 0.6, 0.0], dtype=np.float32)
        frames = np.stack([base + shift * step for step in range(6)])

        handle = XTCFile()
        handle.set_coord(frames)
        path = tmp_path / "run.xtc"
        handle.write(str(path))

        loaded = await server_mod.load_trajectory(str(path))
        first = await _shot(session)
        moved = await server_mod.frame(5)
        last = await _shot(session)

    assert loaded["frames"] == 6
    # Both halves agree on the molecule, which is the invariant decision 9 set.
    assert loaded["atoms"] == loaded["viewer_atoms"]
    assert moved["frames"] == 6
    assert difference(first, last) > 0.005
    assert coverage(last) > 0.005


async def test_colouring_by_rmsf_paints_mobile_and_rigid_differently(tmp_path):
    """The measurement made visible, checked as pixels rather than as a reply.

    The trajectory is built so half the molecule is pinned and half swings, so
    a correct ramp puts two clearly different colours on screen. A B-factor
    column that never reached the viewer, or a theme that never applied, would
    leave one flat colour and report success either way.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        template = server_mod._require_structure()
        base = np.asarray(template.coord, dtype=np.float32)
        half = base.shape[0] // 2
        frames = []
        for step in range(8):
            coord = base.copy()
            coord[half:, 1] += step * 0.4  # one half moves, the other is pinned
            frames.append(coord)

        handle = XTCFile()
        handle.set_coord(np.stack(frames))
        path = tmp_path / "hinge.xtc"
        handle.write(str(path))

        await server_mod.load_trajectory(str(path))
        plain = await _shot(session)
        measured = await server_mod.rmsf()
        painted = await server_mod.color_by_rmsf()
        after = await _shot(session)

    # The measurement itself separates the two halves.
    assert measured["max"] > measured["min"]
    assert painted["rmsf_max"] > painted["rmsf_min"]
    assert painted["reloaded"] is True

    # And the picture changed, with the molecule still drawn.
    assert difference(plain, after) > 0.005
    assert coverage(after) > 0.005


async def test_the_rmsf_ramp_depends_on_the_motion_it_measures(tmp_path):
    """Two runs, same molecule, same representation — only the ramp differs.

    A rigid run gives every atom the same fluctuation and therefore one flat
    colour; a hinge gives a spread and therefore a ramp. Comparing the two
    painted frames is what separates "a ramp was drawn" from "a colour was
    applied": a constant written into the B-factor column would paint both
    identically and satisfy any single-frame check.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        template = server_mod._require_structure()
        base = np.asarray(template.coord, dtype=np.float32)
        half = base.shape[0] // 2

        def write(name: str, hinge: bool) -> str:
            frames = []
            for step in range(8):
                coord = base.copy()
                if hinge:
                    coord[half:, 1] += step * 0.4
                else:
                    coord[:, 1] += step * 0.4  # the whole thing slides
                frames.append(coord)
            handle = XTCFile()
            handle.set_coord(np.stack(frames))
            path = tmp_path / name
            handle.write(str(path))
            return str(path)

        await server_mod.load_trajectory(write("rigid.xtc", hinge=False))
        rigid_reply = await server_mod.color_by_rmsf()
        rigid = await _shot(session)

        await server_mod.load_trajectory(write("hinge.xtc", hinge=True))
        hinge_reply = await server_mod.color_by_rmsf()
        hinged = await _shot(session)

    # A rigid slide superposes away to no fluctuation at all; a hinge does not.
    assert rigid_reply["rmsf_max"] == pytest.approx(0.0, abs=1e-2)
    assert hinge_reply["rmsf_max"] > 0.5
    assert difference(rigid, hinged) > 0.005


# -- recording and encoding ----------------------------------------------------


async def test_a_trajectory_becomes_a_movie(tmp_path):
    """Phase 5's exit criterion, end to end and through the real tools.

    Load a trajectory, measure it, capture every frame, encode. The frames are
    checked for actually differing before the encode, because a movie made from
    one repeated frame is a valid file that plays and shows nothing — and its
    byte size would not say so.
    """
    if ffmpeg_binary() is None:
        pytest.skip("ffmpeg is not installed")

    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        template = server_mod._require_structure()
        base = np.asarray(template.coord, dtype=np.float32)
        half = base.shape[0] // 2
        built = []
        for step in range(10):
            coord = base.copy()
            coord[half:, 1] += step * 0.5
            built.append(coord)

        handle = XTCFile()
        handle.set_coord(np.stack(built))
        path = tmp_path / "run.xtc"
        handle.write(str(path))

        loaded = await server_mod.load_trajectory(str(path))
        measured = await server_mod.rmsd_series()
        recorded = await server_mod.record_trajectory(str(tmp_path / "frames"), width=320)
        encoded = await server_mod.movie(
            recorded["directory"], str(tmp_path / "run.mp4"), fps=10
        )
        # The viewer is left on the first frame, not part-way through the run.
        settled = await session.request("frame", {"index": 0})

    assert loaded["frames"] == 10
    # The structure departs from where it started, which is what a movie shows.
    assert measured["final"] > measured["rmsd"][0]

    frames = sorted(Path(recorded["directory"]).glob("frame_*.png"))
    assert len(frames) == 10
    renders = [decode(frame.read_bytes()) for frame in (frames[0], frames[-1])]
    assert difference(renders[0], renders[1]) > 0.005
    assert coverage(renders[-1]) > 0.005

    assert Path(encoded["path"]).stat().st_size > 0
    assert encoded["frames"] == 10
    assert encoded["seconds"] == pytest.approx(1.0)
    assert settled["index"] == 0


# -- keyframed timeline --------------------------------------------------------


async def test_a_timeline_swings_the_camera_between_saved_views(tmp_path):
    """The camera move, checked as frames rather than as arithmetic.

    Two keyframes a quarter turn apart. Every frame has to keep the molecule on
    screen and roughly the same size — which is the whole reason the path goes
    around the target rather than straight between the two positions — and
    consecutive frames have to differ, or the camera never moved.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session):
        server_mod._keyframes.clear()
        await server_mod.keyframe("start")
        await session.request("orbit", {"degrees": 90})
        await server_mod.keyframe("side")

        recorded = await server_mod.record_timeline(
            str(tmp_path / "move"), frames=8, width=320
        )

    frames = sorted(Path(recorded["directory"]).glob("frame_*.png"))
    assert len(frames) == 8
    assert recorded["keyframes"] == ["start", "side"]

    renders = [decode(frame.read_bytes()) for frame in frames]
    covers = [coverage(render) for render in renders]

    # The subject stays framed throughout: nothing empties, nothing fills.
    assert all(cover > 0.005 for cover in covers)
    # And roughly the same size — a straight line between the two positions
    # would pass closer and swell the molecule on the way.
    assert max(covers) < min(covers) * 2.5
    # The camera actually moved, frame to frame.
    for index in range(1, len(renders)):
        assert difference(renders[index - 1], renders[index]) > 0.001


async def test_a_timeline_ends_on_its_last_keyframe(tmp_path):
    """So a still taken afterwards matches the frame the movie ends on."""
    async with viewer_session(FIXTURE) as session, _as_server(session):
        server_mod._keyframes.clear()
        await server_mod.keyframe("start")
        await session.request("orbit", {"degrees": 60})
        await server_mod.keyframe("end")
        expected = await _shot(session)

        await server_mod.record_timeline(str(tmp_path / "move"), frames=6, width=320)
        landed = await _shot(session)

    assert difference(expected, landed) == 0.0


async def test_a_timeline_needs_two_keyframes():
    async with viewer_session(FIXTURE) as session, _as_server(session):
        server_mod._keyframes.clear()
        await server_mod.keyframe("only")
        with pytest.raises(ViewerError, match="at least two keyframes"):
            await server_mod.record_timeline("/tmp/nowhere", frames=4)


# -- the vocabulary the view catalogue is built on ------------------------------
#
# docs/views.md acceptance criteria 1 and 2. The catalogue of views borrowed from
# MCPymol is almost entirely recipes over primitives Mol* already has, so the
# thing worth pinning is not the recipes but that each primitive *reaches the
# pixels*. A theme that silently fails to apply would leave every view built on
# it reporting success and drawing the previous picture.
#
# The instrument used while planning could not measure this: it coloured a
# handle drawn on top of the load preset's own representation, so the visible
# pixels came from `auto` and even a literal #ff0000 measured as no change. The
# fix is the first line of each test — hide what the preset drew, then draw the
# thing being measured. Worth stating because the broken version looked correct
# and produced a confident, wrong answer.

# Every theme the view catalogue needs, and what each is for there.
_VIEW_THEMES = [
    "uncertainty",  # B-factor: the bfactor and putty views
    "hydrophobicity",  # the hydrophobic-surface view
    "illustrative",  # the textbook and cinematic views
    "partial-charge",  # the charge-colouring view (a proxy, not a solve)
    "secondary-structure",  # a staple of every cartoon figure
]


@contextlib.asynccontextmanager
async def _bare_fold(session):
    """The polymer alone, in flat white, with the load preset's scene hidden.

    A theme has to be applied to something visible to be measurable, and the
    preset's `auto` representation is coincident with anything drawn over it.
    """
    async with _as_server(session, load=True):
        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="cartoon", handle="fold", color="#ffffff")
        yield


@pytest.mark.parametrize("theme", _VIEW_THEMES)
async def test_a_colour_theme_the_views_need_reaches_the_pixels(theme):
    async with viewer_session(FIXTURE) as session, _bare_fold(session):
        white = await _shot(session)
        assert coverage(white) > 0.02, "nothing was drawn to colour"

        await server_mod.color(theme, name="fold")
        painted = await _shot(session)

        assert difference(white, painted) > STYLED, f"{theme} changed nothing"


@pytest.mark.parametrize("representation", ["putty", "point"])
async def test_a_representation_the_views_need_draws_something(representation):
    """`putty` carries the putty view, `point` the pointillist one."""
    async with viewer_session(FIXTURE) as session, _bare_fold(session):
        as_cartoon = await _shot(session)

        await server_mod.show(representation=representation, handle="fold")
        drawn = await _shot(session)

        assert coverage(drawn) > 0.01, f"{representation} drew nothing at all"
        assert difference(as_cartoon, drawn) > STYLED


async def test_ellipsoid_draws_after_all():
    """Recorded as a silent no-op by the planning probe. It was not.

    docs/views.md said ellipsoid drew nothing on 1UBQ while reporting success,
    which would have been the silent failure this suite exists to catch. It was
    the third thing the broken instrument got wrong: drawn over the load
    preset's own representation, nothing appears to change whatever is drawn.
    Against a hidden scene it draws like anything else.
    """
    async with viewer_session(FIXTURE) as session, _bare_fold(session):
        before = await _shot(session)
        await server_mod.show(representation="ellipsoid", handle="fold")
        after = await _shot(session)
        assert difference(before, after) > STYLED
