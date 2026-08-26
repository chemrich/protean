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

import asyncio
import contextlib
import os
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
from protean_mcp.selections import parse as parse_selection
from protean_mcp.selections_numpy import evaluate, load_structure

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

        # The bump, taken last and on its own spacefill.
        #
        # Not on the cartoon the load preset drew: a ribbon is too little
        # surface for the effect to clear this file's threshold. Measured on
        # 1UBQ — the strongest cartoon setting moves 0.0073 of the frame
        # against a DISTINCT of 0.008, and the same setting on spacefill moves
        # 0.036. A test written against the cartoon would have failed for a
        # working control.
        # Put the scene back to neutral first. The emissive frames above leave
        # `auto` self-illuminating at 0.8 with bloom back on, and bloom is a
        # blur: the finest bump here measures 0.004 of the frame, which is well
        # inside what a glow pass can add or wash out.
        await session.request(
            "material", {"name": "auto", "finish": "matte", "emissive": 0.0}
        )
        await session.request(
            "show",
            {
                "name": "bump",
                "expression": "(sel.atom.all)",
                "representation": "spacefill",
            },
        )
        await session.request("material", {"name": "bump", "finish": "matte"})
        frames["smooth"] = await _shot(session)
        bumped = await session.request(
            "material",
            {"name": "bump", "finish": "matte", "bumpiness": 0.9, "bump_frequency": 3},
        )
        frames["bumpy"] = await _shot(session)
        # Half a bump is no bump: a frequency with nothing to scale.
        frequency_only = await session.request(
            "material", {"name": "bump", "finish": "matte", "bump_frequency": 3}
        )
        frames["frequency_only"] = await _shot(session)
        # Frequency is the *fineness* of the perturbation, so a high one moves
        # fewer pixels than a low one rather than more. Kept as three frames
        # because the ordering is the surprising part and nothing else pins it.
        for freq in (1, 6):
            await session.request(
                "material",
                {
                    "name": "bump",
                    "finish": "matte",
                    "bumpiness": 0.9,
                    "bump_frequency": freq,
                },
            )
            frames[f"bump_freq_{freq}"] = await _shot(session)
    return {
        "frames": frames,
        "replies": replies,
        "restored": restored,
        "dull": dull,
        "glow": glow,
        "bumped": bumped,
        "frequency_only": frequency_only,
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


async def test_bumpiness_reaches_the_pixels(finishes):
    """The control was pinned to zero and undocumented until 2026-08-21.

    The reason given was that it "does nothing unless bumpFrequency is above 0,
    and that defaults to 0". Of the eleven representations that declare the
    parameter, seven default non-zero — spacefill, molecular-surface,
    gaussian-surface, orientation and polyhedron at 1, cartoon and putty at 2 —
    so pinning bumpiness killed a control that seven of them would have shown
    untouched. Measured here at 0.018 of the frame against a 0.008 threshold.
    """
    frames: dict[str, Render] = finishes["frames"]
    assert difference(frames["smooth"], frames["bumpy"]) > DISTINCT


async def test_a_frequency_with_no_bumpiness_changes_nothing(finishes):
    """Both halves or neither, and the reply has to be honest about which.

    `bump_frequency` alone has nothing to scale, so the picture is the smooth
    one. Worth pinning because the reply still reports a non-zero
    `bump_frequency_applied_to` here — the frequency really did land on the
    representation — and a caller reading only that number would conclude the
    surface had changed.
    """
    frames: dict[str, Render] = finishes["frames"]
    assert difference(frames["smooth"], frames["frequency_only"]) == 0.0
    # The half of the claim the docstring makes and this test used not to check:
    # the frequency really did land, and a caller reading only that number would
    # conclude the surface had changed.
    reply = finishes["frequency_only"]
    assert reply["bump_frequency_applied_to"] >= 1
    assert reply["bump_will_show"] is False


async def test_a_finer_bump_moves_fewer_pixels_than_a_coarser_one(finishes):
    """Frequency is fineness, and the effect of raising it is counter-intuitive.

    A higher frequency makes the perturbation smaller relative to a pixel, so
    the surface reads *smoother*, not rougher. Measured on 1UBQ spacefill:
    0.036 of the frame at frequency 1, 0.018 at 3, 0.004 at 6. A caller reaching
    for "more texture" by raising this gets less, and nothing else in the suite
    says so.
    """
    frames: dict[str, Render] = finishes["frames"]
    smooth = frames["smooth"]
    coarse = difference(smooth, frames["bump_freq_1"])
    fine = difference(smooth, frames["bump_freq_6"])
    assert coarse > fine, f"frequency 1 moved {coarse:.5f}, frequency 6 moved {fine:.5f}"


async def test_the_reply_counts_the_representations_that_took_a_frequency(finishes):
    # Exactly one: `bump` is one component holding one representation. A
    # regression that walked the whole hierarchy instead of the named target
    # would spray the frequency onto the load preset's cartoon as well, report
    # 2, and sail past a `>= 1`.
    assert finishes["bumped"]["bump_frequency_applied_to"] == 1
    assert finishes["bumped"]["bump_frequency"] == 3
    assert finishes["bumped"]["bumpiness"] == 0.9
    assert finishes["bumped"]["bump_will_show"] is True


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
# it measures the effect directly. The baseline is exactly 0.0 — not nearly
# zero, exactly — which is what makes a small number here meaningful.
#
# Counted in pixels rather than as a fraction of the frame, after a fraction
# threshold had to be moved twice. The quantity varies with frame size, but
# not the way a fraction assumes: these are the same fixture and flags.
#
#     frame        green pixels   as a fraction
#     746x335 (CI)          117       0.00047
#     722x311               166       0.00074
#     1166x937               83       0.000076
#     2332x1274          13,258       0.00446
#
# The fractions span 59x and the counts span 160x, so neither is stable — but
# a count is the right unit for a *noise floor*, which is all this is: the
# question it answers is "did any green arrive", and the baseline says stray
# green does not exist in this scene at all. Whether the outline is faithful
# is asserted separately, below, by a ratio the frame cannot affect.
OUTLINE_PIXELS = 20

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
        await session.request(
            "effects",
            {"outline": True, "outline_color": "#00ff00", "outline_scale": 3},
        )
        out["outline_wide"] = await _shot(session)
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
    assert _green_pixels(styled_effects["outline"]) > OUTLINE_PIXELS

    # The floor above only says green arrived. This says the pass is really
    # drawing the outline: widen it and there has to be more of it. A ratio
    # rather than a level, so it holds at any frame size and survived the
    # renderer change that broke the level — 44x on the frame that failed.
    assert (
        _green_pixels(styled_effects["outline_wide"])
        > _green_pixels(styled_effects["outline"]) * 3
    ), "widening the outline did not widen the outline"


def _green_pixels(render: Render) -> int:
    """How many pixels the outline actually painted, not what share of the frame.

    A share divides by an area that changes with the window; the outline is a
    line, so its pixel count follows the silhouette's perimeter instead and the
    two disagree by two orders of magnitude between a retina window and CI's.
    """
    return round(color_fraction(render, OUTLINE_GREEN) * render.height * render.width)


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


# -- the lens: how the camera sees ---------------------------------------------


def _shift(clear: Render, changed: Render) -> Any:
    """How far each drawn pixel moved, in levels, between two renders."""
    a = np.asarray(clear.pixels).astype(float)
    b = np.asarray(changed.pixels).astype(float)
    ground = a[0, 0, :3]
    drawn = np.abs(a[:, :, :3] - ground).max(axis=2) > 6
    return np.abs(b[:, :, :3] - a[:, :, :3]).mean(axis=2)[drawn]


async def _deep_scene() -> None:
    """A spacefill, which fills far more depth than a ribbon does — and depth
    is the only thing fog reads."""
    await server_mod.preset("publication-cartoon")
    await server_mod.select("polymer", name="deep")
    await server_mod.show(representation="spacefill", handle="deep")


async def test_fog_is_a_depth_cue_and_not_a_dim():
    """The assertion a "just darken everything" fog cannot pass.

    The obvious test is that fog changes fewer pixels than a global dim would,
    since only the far ones fade. **Measured, that is false**: fog at 100 moves
    0.9994 of the drawn pixels, because even the nearest atom has some depth
    and shifts by at least a level. A test built on it would have passed for
    real fog, passed for a uniform dim, and read as a rigorous depth-cue guard
    while proving nothing.

    What separates them is the *spread*. A uniform dim moves every pixel by the
    same amount, so its standard deviation is zero; a depth cue grades the
    shift with distance. Measured on 1UBQ spacefill: mean 46.35, std 25.26 — a
    spread of **0.545** — with deciles at 14.7 / 31.3 / 45.0 / 57.3 / 81.7,
    smooth rather than bimodal. A dim of the same average strength reads 0.000.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await _deep_scene()
        await server_mod.lens(fog=0)
        clear = await _shot(session)
        await server_mod.lens(fog=100)
        fogged = await _shot(session)

    shift = _shift(clear, fogged)
    spread = float(shift.std() / max(shift.mean(), 1e-9))

    assert float(shift.mean()) > 10.0, f"fog moved almost nothing: {shift.mean():.2f}"
    assert spread > 0.2, (
        f"fog moved every drawn pixel by about the same amount (spread "
        f"{spread:.3f}), which is a dim rather than a depth cue"
    )


async def test_fog_draws_nothing_at_the_default_it_has_always_had():
    """Mol*'s fog defaults to *on* at 15, so every protean figure ever made has
    carried it — and it has never once been visible.

    Measured with no tolerance, on a spacefill: 5, 15 and 25 are bit-identical
    to fog off. 40 reads 0.00009, 60 reads 0.026, 100 reads 0.103. That is the
    reason this knob is worth exposing at all: the default is not a mild
    version of the effect, it is the absence of one.

    It is also why `fog=0` can only be checked by reading the canvas back.
    There is nothing to see, so a pixel test for "off" would either fail on
    correct code or be loosened until it measured nothing.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await _deep_scene()
        await server_mod.lens(fog=0)
        off = await _shot(session)
        await server_mod.lens(fog=15)
        default = await _shot(session)
        heavy_reply = await server_mod.lens(fog=100)
        heavy = await _shot(session)

    assert difference(off, default) == 0.0, (
        "Mol*'s default fog became visible — the range this tool exposes was "
        "calibrated on it being invisible, so re-measure before trusting it"
    )
    assert difference(off, heavy) > 0.05, (
        f"fog at full strength moved only {difference(off, heavy):.5f}"
    )
    assert heavy_reply["fog"] == 100


async def test_the_lens_reports_what_the_canvas_holds_not_what_it_was_asked():
    """`cameraFog` is a *mapped* parameter — `{name, params}` — and Mol* takes a
    bare `{intensity}` without complaint while leaving the fog as it was. A
    reply built from the request would report a change that never happened, and
    for fog off there are no pixels to catch it.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("publication-cartoon")

        assert (await server_mod.lens(fog=0))["fog"] == 0
        assert (await server_mod.lens(fog=60))["fog"] == 60
        turned = await server_mod.lens(projection="orthographic")
        assert turned["projection"] == "orthographic"
        # And the projection survives a call that says nothing about it.
        both = await server_mod.lens(fog=15)
        assert both["projection"] == "orthographic"
        assert both["fog"] == 15


async def test_an_orthographic_lens_draws_a_different_picture():
    """Perspective converges and orthographic does not, so the same scene from
    the same camera is a different picture — which is the point of the knob.
    Measured on 1UBQ spacefill with no tolerance: 0.1066 of the frame.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await _deep_scene()
        await server_mod.lens(projection="perspective")
        perspective = await _shot(session)
        await server_mod.lens(projection="orthographic")
        orthographic = await _shot(session)

    assert difference(perspective, orthographic) > 0.01, (
        "the projection changed nothing, so the camera mode never reached the canvas"
    )


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


#: The encoders, and what each one is expected to produce.
#:
#: Only PNG is exercised at journal size. The three cases used to render the
#: *identical* 4323 px scene three times and differ only in which encoder wrote
#: it — three browser launches and three of the most expensive captures in the
#: suite to test what a file extension does. Suffix, colour mode and the DPI
#: round-trip are the claims here, and none of them is a function of pixel
#: count: TIFF stores resolution the same way at 1200 px as at 4323.
#:
#: The one claim that *is* size-dependent — that the tool can capture at
#: journal size at all, which is Phase 4's exit criterion — is kept, once.
_ENCODERS = [("png", ".png", "RGBA"), ("tiff", ".tiff", "RGBA"), ("jpeg", ".jpg", "RGB")]


@pytest.fixture(scope="module")
async def journal_figures(tmp_path_factory) -> dict[str, Any]:
    """Every format written from one session, plus one at full journal size.

    The modest captures come first and the expensive one last, deliberately:
    at 4323 px a software renderer can run out of room and `snapshot()`
    refuses, which is the behaviour worth having. Taken first, that skip would
    take the cheap assertions down with it.
    """
    out = tmp_path_factory.mktemp("journal")
    modest: dict[str, Any] = {}
    full: dict[str, Any] | None = None
    skipped = ""

    async with viewer_session(FIXTURE) as session:
        for fmt, _suffix, _mode in _ENCODERS:
            # 101.6 mm at 300 dpi is MODEST_PIXELS, the size the rest of this
            # section already uses.
            modest[fmt] = await _figure_or_skip(
                session, str(out / f"modest-{fmt}"), width_mm=101.6, dpi=300, format=fmt
            )
        if os.environ.get("PROTEAN_SKIP_JOURNAL_FIGURE"):
            # Measured on CI, 2026-08-22: this fixture cost 642 s, of which the
            # 4323 px capture is roughly 600 — about **19% of the entire
            # browser job for one capture**. 4323x3242 at 16 samples is ~224
            # million samples through a software rasteriser.
            #
            # It is Phase 4's exit criterion and it is not being dropped: CI
            # runs it on every push to `main` and skips it on pull requests, so
            # every commit that lands is still covered and no PR waits ten
            # minutes for it. Unset here, so a developer's run is unchanged.
            skipped = "PROTEAN_SKIP_JOURNAL_FIGURE is set (CI runs this on main)"
        else:
            full = await _capture_or_none(
                session, str(out / "figure"), column="double", dpi=600, format="png"
            )
            if full is None:
                skipped = f"this renderer cannot capture at {FIGURE_PIXELS}px"

    return {"modest": modest, "full": full, "skipped": skipped}


async def _capture_or_none(session, path: str, **kwargs) -> dict[str, Any] | None:
    """`_figure_or_skip`, but reporting the shortfall instead of skipping.

    A fixture that skips takes every test using it down. The journal-size
    capture is allowed to be beyond a software renderer; the encoder claims
    beside it are not.
    """
    previous = server_mod._bridge
    server_mod._bridge = session.bridge
    try:
        result: dict[str, Any] = await server_mod.snapshot(path, **kwargs)
        return result
    except ViewerError as exc:
        if "came back incomplete" in str(exc):
            return None
        raise
    finally:
        server_mod._bridge = previous


@pytest.mark.parametrize(("fmt", "suffix", "mode"), _ENCODERS)
async def test_each_format_reaches_disk_as_itself(journal_figures, fmt, suffix, mode):
    """Suffix, colour mode, and the DPI round-trip, per encoder.

    The DPI assertion is the point: Mol* cannot write physical resolution at
    all, so a figure that is "300 dpi" only in the tool's reply would satisfy
    every other test here.

    Approximate for PNG because it stores pixels per *metre* as an integer, so
    300 dpi round-trips as 11811 ppm and back again.
    """
    result = journal_figures["modest"][fmt]
    written = Path(result["path"])

    assert written.suffix == suffix
    assert result["pixels"][0] == MODEST_PIXELS

    with PILImage.open(written) as reopened:
        assert reopened.size == tuple(result["pixels"])
        assert reopened.mode == mode
        assert reopened.info["dpi"][0] == pytest.approx(300, rel=1e-3)

    assert written.stat().st_size == result["bytes"] > 0


async def test_a_real_journal_figure_reaches_disk(journal_figures):
    """Phase 4's exit criterion, end to end and through the real tool.

    A double-column figure at 600 dpi, written by snapshot() itself rather than
    by the bridge, then reopened and asked what it is. Rendered once, in PNG:
    what makes this expensive is the 4323 px capture, and which encoder wrote
    it afterwards is the neighbouring test's business.
    """
    if journal_figures["full"] is None:
        pytest.skip(journal_figures["skipped"])
    result = journal_figures["full"]
    written = Path(result["path"])

    assert result["pixels"][0] == FIGURE_PIXELS  # 183 mm at 600 dpi
    with PILImage.open(written) as reopened:
        assert reopened.size == tuple(result["pixels"])
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
async def _as_server(session, load: bool = False, pdb_id: str = FIXTURE):
    """Point the server module's tools at this browser session.

    `load` fills in the server's own copy of the structure, which
    `viewer_session` does not: it loads straight into the viewer, so anything
    that resolves atom indices — the ghost-heart preset, for one — finds
    nothing loaded.

    Filled in directly rather than by calling `fetch_structure()`, which would
    reload the viewer too and reframe its camera, so every before/after
    comparison in the test would be measuring the camera move instead of the
    preset. Same file and same assembly the session used, so both halves still
    describe one molecule.
    """
    previous = server_mod._bridge
    # Adopted the way the server adopts one, rather than assigned. The
    # difference is `on_invoke`: a bridge assigned straight into the global is
    # a socket the page can talk to with no rule about what it may ask for, and
    # every page-initiated test would then be exercising an arrangement that
    # does not exist in production.
    server_mod.use_bridge(session.bridge)
    saved = (
        server_mod._structure,
        server_mod._structure_identifier,
        server_mod._b_factor_column,
    )
    # The handle table is module state and the browser is not: a test that
    # drew a view left `auto_view` registered here while the next test's fresh
    # viewer knew only `auto`, so a styling preset resolved its target to a
    # handle that page had never heard of. It failed as "No selection named
    # 'auto_view'" in whichever test happened to run after one that drew, and
    # passed on its own — which is the shape of thing that gets called flaky.
    saved_handles = dict(server_mod._handles.handles)
    server_mod._handles.clear()
    try:
        if load:
            # `pdb_id` has to match what the session opened, and nothing here
            # can check that: the two are filled in separately, so a test that
            # opens one structure and analyses another gets a server confidently
            # describing a molecule that is not on screen. Cost one debugging
            # session already.
            fetched = await fetch_structure_data(pdb_id)
            loaded = load_structure(fetched.data, fetched.format, "asymmetric")
            server_mod._structure = loaded.array
            server_mod._structure_identifier = pdb_id
            # Filled in from the same parse `fetch_structure` would have used.
            # Left over from a previous test it decides whether `uncertainty`
            # and `plddt` are refused, which is state no test set on purpose.
            server_mod._b_factor_column = server_mod._BFactorColumn(
                confidence=loaded.confidence
            )
        yield
    finally:
        server_mod._bridge = previous
        (
            server_mod._structure,
            server_mod._structure_identifier,
            server_mod._b_factor_column,
        ) = saved
        server_mod._handles.clear()
        server_mod._handles.handles.update(saved_handles)


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
# Nine views: six borrowed from MCPymol, of which four decide what is drawn
# rather than only restyling it, the two illustration styles of §5.9, and felt. Taken
# in one session, in this order, because a browser launch is the expensive part:
# each frame is compared with the one before it, and then all ten frames — the
# plain load plus the nine views — are compared with each other. The second
# claim is the one worth having: two recipes that composed to the same picture
# would both pass "it changed something" and still be one view wearing two
# names.

_VIEW_SEQUENCE = [
    "textbook",
    "putty",
    "hydrophobic-surface",
    "spacefill",
    "skeleton",
    "painting",
    "richardson",
    "felt",
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


async def test_felts_halo_is_visible_in_the_picture():
    """The halo earns its cost, or it does not — and until now nobody had looked.

    `felt` draws a second spacefill at 1.12x and alpha 0.2 under its own
    handle. Both `docs/views.md` and the unit test that stands in for this one
    say the same thing about it: *"at that opacity nobody can tell from the
    picture whether it drew"*. That claim was never measured. The unit test's
    own docstring records why — a differential test "was written first and
    could not work, because the browser fixture restores the handle table on
    teardown and the assertion ran after it". A fixture problem became a
    statement about a picture, and then became the reason not to test it.

    It is false. Hiding the halo inside one session, so the handle is still
    live, moves **0.0970 of the frame past protean's own 8/255 tolerance**,
    with a worst single-channel delta of 202. That is not invisible; it is one
    of the larger single-layer effects in the whole view set.

    Asserted as a floor rather than a window because it is evidence the layer
    is doing work, not a pin on how much.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("felt")
        with_halo = await _shot(session)
        await server_mod.hide(name=server_mod._FELT_HALO)
        without = await _shot(session)

    assert difference(with_halo, without) > STYLED, (
        "hiding felt's halo changed nothing, so the second layer is not drawing"
    )


def _frame(views: list[tuple[str, Render]], name: str) -> Render:
    return dict(views)[name]


# Measured on 1UBQ rather than guessed, and the tolerance is the point. The
# painting ground is #efe9dc, which covers 0.918 of the frame under it and
# 0.0025 of the white-ground frames — but at tolerance 40 plain white *also*
# matches it, at 0.969, because white is within 40 of buff on every channel. A
# looser tolerance would have made this test pass for every view in the
# catalogue while appearing to check the one thing that is different.
PAPER = (0xEF, 0xE9, 0xDC, 255)
PAPER_TOLERANCE = 20


async def test_painting_lays_down_the_ground_it_names(views):
    """A view whose ground is nine tenths of the frame has to have that ground.

    The cheapest thing to get wrong here is the order: the recipe recolours the
    molecule after drawing it, and a `background()` that never landed would
    leave the previous view's white behind a correctly repainted structure —
    which reads as "nearly right" rather than as a failure.
    """
    painted = color_fraction(_frame(views, "painting"), PAPER, PAPER_TOLERANCE)
    plain = color_fraction(_frame(views, "plain"), PAPER, PAPER_TOLERANCE)
    assert painted > 0.5 and plain < 0.01, (
        f"painting covers {painted:.4f} of the frame in its own ground against "
        f"{plain:.4f} for the plain load"
    )


async def test_painting_takes_down_a_line_the_previous_view_left_up():
    """`painting` is the one view with no line at all, and pixels cannot see it.

    The obvious check — count near-black pixels — was written first and is
    wrong, which is worth recording because it is convincing: ambient occlusion
    and a cast shadow on a sphere model drive the crevices to near-black at
    0.0056 of the frame, against 0.0017 for the black outline `textbook`
    actually draws. The darkest view in the catalogue is the one with no line
    in it.

    So the claim is made against the renderer's own state instead, read out of
    the page. Applied after a view that leaves an outline on, because "off" is
    also what a recipe that never ran would find.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        read = (
            "JSON.stringify(window.__protean.plugin"
            ".canvas3d.props.postprocessing.outline.name)"
        )
        await server_mod.preset("textbook")
        before = await session.evaluate(read)
        await server_mod.preset("painting")
        after = await session.evaluate(read)

    assert before == "on", f"textbook left the outline {before!r}, so this proves nothing"
    assert after == "off", "painting left an outline on the canvas"


# The line colours, and the two numbers that separate them. Measured on 1UBQ:
# a *black* outline — the one `textbook` draws — reads 0.00088 of the frame as
# near-black and 0.0023 as near-#4a4a4a, because an antialiased black line has a
# grey halo wider than its core. `richardson`'s grey line reads 0.0000 black and
# 0.0030 grey. So grey-versus-black is not the discriminating comparison and the
# absence of black is: the first version of this test asserted grey > black,
# which is true of a black line too, and it duly passed with the colour mutated
# back to black.
INK_TOLERANCE = 24
NO_BLACK = 0.0002
SOME_LINE = 0.001


async def test_richardson_draws_its_line_in_grey_rather_than_black(views):
    """§5.9 asked for a thinner line; Mol* has no thinner line.

    `outline.scale` is `min: 1, step: 1` and `illustrative` already sits at the
    floor, so the quieter edge comes from colour instead. That substitution is
    the kind that silently does nothing — `effects()` would accept a smaller
    scale and clamp it — so the colour is measured rather than assumed.

    Both halves are needed and each is mutation-tested: without the first, a
    black line passes; without the second, no line at all passes.
    """
    frame = _frame(views, "richardson")
    black = color_fraction(frame, (0, 0, 0, 255), tolerance=INK_TOLERANCE)
    grey = color_fraction(frame, (0x4A, 0x4A, 0x4A, 255), tolerance=INK_TOLERANCE)
    assert black < NO_BLACK, (
        f"richardson reads {black:.5f} near-black: the outline colour never took"
    )
    assert grey > SOME_LINE, (
        f"richardson reads {grey:.5f} near-grey: there is no line at all"
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
    # Not bit-exact. Between these two frames the scene handle is destroyed and
    # rebuilt twice, a surface is meshed, and the camera is reset twice — and
    # the neighbouring test in this file exists because an absolute pixel
    # threshold measured on one machine is a claim about a renderer rather than
    # about the thing under test. The claim here is that switching away and back
    # returns to the view, so it is stated against the size of the detour.
    returned = difference(direct, switched)
    assert returned < STYLED, (
        f"putty reached twice differs by {returned:.6f}: the surface is still "
        "on screen underneath, or the view is not idempotent"
    )
    assert returned * 10 < difference(direct, surfaced)


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

    # What the frames looked like, carried into any failure message. A bare
    # pair of ratios says the test failed and nothing about why: this one has
    # failed once in a full-suite run with *both* numbers an order of magnitude
    # up, which is the signature of a differently framed scene rather than a
    # differently drawn one — and re-running to find that out cost twenty
    # minutes and did not reproduce.
    shape = ", ".join(
        f"{kind}/{variant} {frame.width}x{frame.height} coverage {coverage(frame):.4f}"
        for (kind, variant), frame in sorted(frames.items())
    )

    assert measured > STYLED, (
        f"putty drew the same tube for two different B-factor sets: "
        f"{measured:.6f} — {shape}"
    )
    # Measured 162x apart on the development machine (0.020219 against
    # 0.000125). Five is the floor: a renderer with more edge noise than this
    # one still has to leave the claim unambiguous, and if the two ever come
    # that close the instrument has become the subject rather than putty.
    assert control * 5 < measured, (
        f"control {control:.6f} against measured {measured:.6f}: too close to "
        f"separate, so this is measuring the reload rather than B-factor — {shape}"
    )


# The pLDDT half of the same question. `test_putty_width_follows_the_bfactor_it
# _claims` above established that the tube's width comes from `B_iso_or_equiv`;
# these establish which *direction* it comes from, which is backlog 41.

#: A confidence ramp with real spread, 30 to 99 along the sequence.
#:
#: Synthetic on purpose. Real AlphaFold pLDDT is often clustered almost flat --
#: AF-P69905 reads p10 97.6, p50 98.6, p90 98.8 over a full range of 65.4 to
#: 98.9 -- so a picture drawn from it would separate two polarities by a few
#: pixels around one terminus, and a test built on that would be measuring the
#: renderer's edge noise. This is the same molecule and the same column with a
#: quantity in it that a tube can actually show.
PLDDT_LOW = 30.0
PLDDT_HIGH = 99.0


def _confidence_ramp(array: Any) -> Any:
    """A per-residue pLDDT running the length of the chain."""
    residue = np.asarray(array.res_id, dtype=float)
    span = float(residue.max() - residue.min())
    return PLDDT_LOW + (PLDDT_HIGH - PLDDT_LOW) * (residue - residue.min()) / span


async def test_plddt_width_is_the_exact_reverse_of_uncertaintys():
    """The polarity, pinned in pixels rather than in arithmetic.

    pLDDT and the B-factor ride the same column and mean opposite things, so a
    `plddt` size theme is right exactly when it draws a value of *v* the width
    `uncertainty` draws *100 - v*. That is a claim two loads can settle
    exactly: one structure carrying the ramp, one carrying its mirror image,
    nothing else different between them.

    Three frames per load, and the cartoon is the control that carries the
    test. Cartoon's default size theme is uniform, so it must read identical
    across the two loads -- which is also what rules out the camera having
    moved when the second structure replaced the first. A control that moves
    means the numbers below are measuring the reload.

    Without this, `plddt` could be `uncertainty` under another name and every
    Python-side test in `test_server.py` would still pass: the refusals would
    fire correctly and the picture would still make the most trustworthy
    regions the fattest, which is the whole bug.
    """
    fetched = await fetch_structure_data(FIXTURE)
    deposited = load_structure(fetched.data, fetched.format, "asymmetric").array

    confident = deposited.copy()
    confident.b_factor = _confidence_ramp(deposited)
    mirrored = deposited.copy()
    mirrored.b_factor = 100.0 - confident.b_factor

    # Asserted before anything about the picture. A flat column is the trap
    # this whole change exists because of, and on a flat column every claim
    # below would pass for the wrong reason.
    deciles = np.percentile(confident.b_factor, [10, 50, 90])
    assert deciles[2] - deciles[0] > 20.0, (
        f"the fixture's confidence column is nearly flat ({deciles}), so a tube "
        "drawn from it says nothing about polarity"
    )

    frames: dict[tuple[str, str], Render] = {}
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        for variant, array in (("confident", confident), ("mirrored", mirrored)):
            await server_mod._send_structure(array, FIXTURE)
            server_mod._structure = array
            await server_mod.hide(server_mod._WHOLE_SCENE)
            await server_mod.select("polymer", name="fold")
            # White, so nothing in these frames is a colour theme's doing --
            # the only thing that varies is the width.
            await server_mod.show(
                representation="cartoon", handle="fold", color="#ffffff"
            )
            frames[("cartoon", variant)] = await _shot(session)
            await server_mod.show(representation="putty", handle="fold", color="#ffffff")
            for theme in ("plddt", "uncertainty"):
                # Straight down the bridge rather than through `size()`, whose
                # provenance guard would refuse one of these two on a structure
                # that never went through `fetch_structure`. What is under test
                # here is the theme the viewer registers; the guard has its own
                # tests, in `test_server.py`.
                await session.request("size", {"name": "fold", "size": theme})
                frames[(theme, variant)] = await _shot(session)

    control = difference(
        frames[("cartoon", "confident")], frames[("cartoon", "mirrored")]
    )
    inverted = difference(
        frames[("plddt", "confident")], frames[("uncertainty", "confident")]
    )
    mirror = difference(
        frames[("plddt", "confident")], frames[("uncertainty", "mirrored")]
    )

    shape = ", ".join(
        f"{theme}/{variant} coverage {coverage(frame):.4f}"
        for (theme, variant), frame in sorted(frames.items())
    )

    # Measured on the development machine: control 0.000000, inverted 0.039881,
    # mirror 0.000000. The two zeroes are the theory holding exactly -- same
    # coordinates and same widths produce bit-identical frames -- so all three
    # claims below are stated as ratios rather than against those numbers. A
    # renderer with edge noise, or a slower one caught mid-tween, moves the
    # zeroes and must not move the conclusion.
    assert inverted > STYLED, (
        f"'plddt' and 'uncertainty' drew the same tube from the same column: "
        f"{inverted:.6f} — one of them is not reading the polarity it claims. {shape}"
    )
    # The exactness claim, stated against the control rather than against a
    # number measured on this machine. `plddt` over v and `uncertainty` over
    # 100 - v are the same widths atom for atom, so the two frames can differ
    # only by whatever the reload itself costs.
    assert mirror < inverted / 5, (
        f"'plddt' over the ramp and 'uncertainty' over its mirror differ by "
        f"{mirror:.6f} against an inversion of {inverted:.6f}: the two are not "
        f"the same ramp read from opposite ends. {shape}"
    )
    assert control * 5 < inverted, (
        f"control {control:.6f} against inverted {inverted:.6f}: too close to "
        f"separate, so this is measuring the reload rather than the theme. {shape}"
    )


async def test_plddt_colour_reaches_the_pixels():
    """The colour half of the claim `_VIEW_THEMES` can no longer make.

    `plddt` is excluded from that parametrized sweep because its subject is a
    predicted model while this suite's fixture is a crystal structure, so the
    provenance guard refuses it there -- correctly, and that refusal is the
    whole point of backlog 41. But the claim still has to be made somewhere: a
    theme in the catalogue that paints nothing is a view nobody can use, and an
    exclusion with no replacement is how a sweep quietly stops covering things.

    Straight down the bridge rather than through `color()`, for the same reason
    `test_plddt_width_is_the_exact_reverse_of_uncertaintys` above does: what is
    under test here is the theme the viewer registers, and the guard has its
    own tests in `test_server.py`.

    The column carries the synthetic ramp rather than a real model's numbers,
    for the reason `_confidence_ramp` records -- AF-P69905 reads p10 97.6, p50
    98.6, p90 98.8, so a picture drawn from it would be separating a handful of
    pixels and this would pass on renderer noise.
    """
    fetched = await fetch_structure_data(FIXTURE)
    deposited = load_structure(fetched.data, fetched.format, "asymmetric").array

    confident = deposited.copy()
    confident.b_factor = _confidence_ramp(deposited)

    # Asserted before anything about the picture, for the reason this whole
    # change exists: a flat column draws one flat colour and every claim below
    # would pass without the binding carrying anything.
    deciles = np.percentile(confident.b_factor, [10, 50, 90])
    assert deciles[2] - deciles[0] > 20.0, (
        f"the fixture's confidence column is nearly flat ({deciles}), so a "
        "picture drawn from it says nothing about the theme"
    )

    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod._send_structure(confident, FIXTURE)
        server_mod._structure = confident
        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="cartoon", handle="fold", color="#ffffff")
        white = await _shot(session)
        assert coverage(white) > DRAWN, "nothing was drawn to colour"

        await session.request("color", {"name": "fold", "color": "plddt"})
        painted = await _shot(session)

    measured = difference(white, painted)
    assert measured > STYLED, (
        f"'plddt' painted nothing over a confidence ramp spanning {PLDDT_LOW:g} "
        f"to {PLDDT_HIGH:g}: {measured:.6f} against a threshold of {STYLED}"
    )


def _confidence_below(array: Any, fraction: float) -> Any:
    """A confidence column where `fraction` of the polymer sits below the line.

    Built by choosing the residues rather than by ramping, because the ramp
    helper spans `res_id` over the **whole** array — and 1UBQ's waters run to
    134, so a ramp over 1-134 puts every polymer residue (1-76) below 70 and a
    test built on it compares "cover nothing" against "cover everything". That
    passed, and it could not have told either of those from a cover that
    ignores the numbers and drapes the whole molecule whenever anything is low.
    """
    values = np.full(array.array_length(), 95.0)
    polymer = evaluate(parse_selection("polymer"), array)
    ids = np.unique(array.res_id[polymer])
    doomed = set(ids[: round(len(ids) * fraction)].tolist())
    picked = polymer & np.array([int(r) in doomed for r in array.res_id])
    values[picked] = 40.0
    return values


async def test_scaffold_covers_more_when_more_is_guessed_at():
    """The cover tracks how much is below the line, not merely that something is.

    Three arms over the same coordinates: nothing below the threshold, half the
    chain below it, all of it below it. A cover that reads the column puts the
    three pictures in that order, increasingly far from the bare cartoon. A
    cover that drapes the molecule whenever *anything* is low — or that draws a
    fixed shell — collapses the last two together.

    Ordering rather than absolute numbers, and that is what makes the reload a
    non-issue: all three arms send a structure and redraw, so a camera that
    moved or a cost the reload itself carries lands on every pair equally and
    cannot produce a monotone sequence.

    The step list is checked too, because "nothing to cover" and "the cover
    failed to draw" are the same picture and only the reply separates them.
    """
    fetched = await fetch_structure_data(FIXTURE)
    deposited = load_structure(fetched.data, fetched.format, "asymmetric").array

    arms = {
        "none": _confidence_below(deposited, 0.0),
        "half": _confidence_below(deposited, 0.5),
        "all": _confidence_below(deposited, 1.0),
    }

    # Stated against the data before any picture is compared. The middle arm is
    # the one that matters and the one the first version of this test did not
    # actually have: if it is 0 or 1 the sequence below is two points, not
    # three, and the claim collapses.
    polymer = evaluate(parse_selection("polymer"), deposited)
    fractions = {
        name: float((values[polymer] < server_mod._CONFIDENT).mean())
        for name, values in arms.items()
    }
    assert fractions["none"] == 0.0, f"the 'none' arm has something below: {fractions}"
    assert fractions["all"] == 1.0, f"the 'all' arm has something above: {fractions}"
    assert 0.3 < fractions["half"] < 0.7, (
        f"the middle arm is not a partial cover, so this is a two-point test "
        f"wearing three names: {fractions}"
    )

    frames: dict[str, Render] = {}
    said: dict[str, str] = {}
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        for name, values in arms.items():
            array = deposited.copy()
            array.b_factor = values
            await server_mod._send_structure(array, FIXTURE)
            server_mod._structure = array
            # Set directly: these are 1UBQ carrying a confidence column, which
            # no file on disk is. The detection that fills this in has its own
            # tests in test_server.py; what is under test here is the view.
            server_mod._b_factor_column = server_mod._BFactorColumn(confidence="pLDDT")
            result = await server_mod.preset("scaffold")
            said[name] = " ".join(result["steps"])
            frames[name] = await _shot(session)

    for name, frame in frames.items():
        assert coverage(frame) > DRAWN, f"the {name!r} arm is not on screen"

    half = difference(frames["none"], frames["half"])
    full = difference(frames["none"], frames["all"])
    shape = ", ".join(
        f"{n} coverage {coverage(f):.4f}" for n, f in sorted(frames.items())
    )

    assert half > STYLED, (
        f"covering half the chain changed nothing against covering none: "
        f"{half:.6f}. The cover is not reading the column. {shape}"
    )
    assert full > half, (
        f"covering the whole chain ({full:.6f}) is no further from the bare "
        f"cartoon than covering half of it ({half:.6f}), so the cover is not "
        f"tracking how much is below the line. {shape}"
    )
    assert "no cover to draw" in said["none"], said["none"]
    assert "covered rather than drawn" in said["half"], said["half"]


async def test_scaffold_is_refused_on_a_structure_with_no_confidence():
    """A crystal structure has no part the model was guessing at.

    Its B-factors describe disorder, not confidence, so "below 70" would select
    whatever happened to be poorly ordered and cover it — producing a picture
    that reads as a statement about a prediction and is not one. Refused before
    the scene is taken over, so an error does not leave a half-drawn viewer.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        before = await _shot(session)
        with pytest.raises(ViewerError, match="nothing to cover here"):
            await server_mod.preset("scaffold")
        after = await _shot(session)

    assert difference(before, after) == 0.0, (
        "the refusal changed the picture, so it fired after the scene was taken "
        "over rather than before"
    )


# -- a control that asks the server, from docs/views.md §4 ---------------------

# Driven by what a person does — open the menu, click the entry — rather than by
# reaching for an element id the page happens to use. The single button these
# once addressed became a menu, and the tests failed on `null.click()` rather
# than on anything about the behaviour, which is the wrong way for a test to
# notice a redesign.
# By the name the server uses, not by the label: the label reads "asking…" for
# the length of a round trip, so anything keyed on text loses the element at
# exactly the moment this waits on it.
_ITEM = "document.querySelector('.view-menu-item[data-view=\"ghost-heart\"]')"
_CLICK_VIEW = (
    "(document.getElementById('view-menu-button').click(),"
    f" {_ITEM}.click(), JSON.stringify('ok'))"
)
_BUTTON_IDLE = f"JSON.stringify(!{_ITEM}.disabled)"
_BUTTON_LABEL = "JSON.stringify(document.getElementById('view-menu-button').textContent)"


async def _click_and_settle(session, timeout: float = 60) -> str:
    """Click the view button and wait for the server's answer to land.

    The button disables itself for the round trip, so its own state says when
    the answer arrived. That is the only thing it is trusted for: what the
    *scene* did is read from pixels, because a control reporting success while
    the picture does not move is the failure this whole design is arranged
    around.
    """
    await session.evaluate(_CLICK_VIEW)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await session.evaluate(_BUTTON_IDLE):
            label: str = await session.evaluate(_BUTTON_LABEL)
            return label
        await asyncio.sleep(0.1)
    raise AssertionError("the button never came back from its round trip")


async def test_a_click_draws_the_view_the_server_was_asked_for():
    """Criterion 4, and the criterion 3 claim checked from the other end.

    The scene has to arrive over the ordinary action channel, which means the
    pixels move *and* the handle it created is one the Python side knows about.
    The pixels alone would pass with the page drawing for itself — the exact
    arrangement §3.2 rules out, because a selection made in the browser is a
    handle no model can refer to.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        before = await _shot(session)
        label = await _click_and_settle(session)
        after = await _shot(session)
        handles = server_mod._handles.names()
        reported = await server_mod.list_selections()

    assert "refused" not in label, f"the button reported {label!r}"
    assert difference(before, after) > STYLED, "the click changed no pixels"
    # The surface is wider than the cartoon it wraps, as it is for the tool.
    assert coverage(after, of=background(before)) > coverage(
        before, of=background(before)
    )
    assert "auto_ghost" in handles, "the page drew this, not the server"
    assert "ghost-heart" in reported["user_actions"]


async def test_a_click_for_a_view_that_cannot_apply_is_refused_on_the_button():
    """A control that cannot report failure is a control that reports success.

    Nothing loaded, so the same refusal the tool gives has to reach the button
    rather than being swallowed into a console nobody is reading.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=False):
        server_mod._structure = None
        server_mod._structure_error = None
        before = await _shot(session)
        label = await _click_and_settle(session)
        after = await _shot(session)

    assert "refused" in label, f"the button reported {label!r}"
    assert difference(before, after) == 0.0, "a refused click changed the picture"


async def test_ghost_heart_layers_over_what_is_already_drawn():
    """The scoping claim, checked on screen rather than in the call log.

    A surface shown under the same handle rebuilds that component, so the
    cartoon inside would disappear and the frame would show only a surface.
    Here the ghost is its own component, so the drawn area *grows* — the
    surface is wider than the cartoon it wraps — and the scene keeps everything
    it had.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        before = await _shot(session)
        result = await server_mod.preset("ghost-heart")
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


@pytest.fixture(scope="module")
async def boiled(tmp_path_factory) -> dict[str, Any]:
    """One short boil at full amplitude, and one at an amplitude too small to see.

    The tiny arm is the control and it is what makes the other numbers mean
    anything. Every pose reloads the structure, so a reload that repainted even
    slightly differently would show up as pose-to-pose change and read exactly
    like a working boil. At 0.001 A no wobble can be visible, so whatever that
    arm measures is the reload's own noise and the real boil has to clear it.
    """
    out = tmp_path_factory.mktemp("boil")
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("publication-cartoon")
        before = await _shot(session)
        loud = await server_mod.boil(str(out / "loud"), frames=6, width=400)
        quiet = await server_mod.boil(
            str(out / "quiet"), frames=4, width=400, amplitude=0.001
        )
        after = await _shot(session)
    return {"loud": loud, "quiet": quiet, "before": before, "after": after}


def _frames(result: dict[str, Any]) -> list[Render]:
    return [
        decode(frame.read_bytes())
        for frame in sorted(Path(result["directory"]).glob("frame_*.png"))
    ]


async def test_a_boil_holds_each_pose_for_its_whole_hold(boiled):
    """On twos: two captures of one drawing, then a new drawing.

    Held frames must be **bit-identical**, not merely close. The pose is one
    upload drawn twice, so anything at all between them would be the renderer
    disagreeing with itself, and this file asserts exact equality in fourteen
    other places for the same reason.
    """
    renders = _frames(boiled["loud"])
    assert len(renders) == 6
    assert boiled["loud"]["poses"] == 3
    for first in range(0, len(renders), 2):
        held = difference(renders[first], renders[first + 1])
        assert held == 0.0, (
            f"frames {first} and {first + 1} are one pose held twice and differ "
            f"by {held:.6f}"
        )


async def test_a_boil_redraws_between_poses(boiled):
    """And the redraw is the wobble, not the reload that carries it.

    Measured on the development machine: 0.0332 and 0.0397 between poses at the
    default amplitude, against 0.000154 for the whole quiet sequence — so the
    reload contributes about a two-hundredth of what is being claimed here.
    Stated as a ratio against the control rather than against those numbers,
    because a slower machine caught mid-tween moves both.
    """
    loud = _frames(boiled["loud"])
    quiet = _frames(boiled["quiet"])

    moved = [difference(loud[i - 1], loud[i]) for i in (2, 4)]
    reload_noise = max(difference(quiet[i - 1], quiet[i]) for i in range(1, len(quiet)))

    assert min(moved) > STYLED, (
        f"the poses are the same picture: {moved}. Nothing is boiling."
    )
    assert min(moved) > reload_noise * 10, (
        f"pose changes {moved} are not clear of the reload's own noise "
        f"({reload_noise:.6f}), so this is measuring the upload rather than the "
        "wobble"
    )
    for index, render in enumerate(loud):
        assert coverage(render) > DRAWN, f"frame {index} lost the molecule"


async def test_a_boil_puts_the_coordinates_back(boiled):
    """A drawing style may not quietly edit the structure.

    Every pose uploads moved atoms, so the last one would otherwise be left on
    screen and in the analysis copy — and every distance measured afterwards
    would be off by a wobble. The scene is a different matter and is not
    claimed: a reload rebuilds the viewer's components, so a preset's own scene
    does not survive, which the reply says outright.
    """
    assert boiled["loud"]["coordinates_restored"] is True
    assert boiled["loud"]["reloaded"] is True
    assert any("reloaded" in step for step in boiled["loud"]["steps"])


async def test_a_boil_with_trails_writes_the_exposure_its_frames_add_up_to(
    tmp_path,
):
    """End to end: poses on a real molecule become one long exposure.

    The accumulation itself is tested on fixtures whose answer is set, in
    `tests/test_phosphor.py`. What only a real boil can show is that the frames
    it writes are the frames that get accumulated — that the glob finds them in
    order, that the ground is read off the capture rather than guessed, and
    that the number in the reply describes the file on disk.

    Its own control is built in. A boil whose amplitude is far below a pixel
    draws the same pose every time, so its exposure is the still and `smear`
    must read **0.0 exactly** — the honest answer on a structure the wobble has
    nothing to say about, and the assertion that would catch an exposure
    reporting motion it invented.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("publication-cartoon")
        moving = await server_mod.boil(
            str(tmp_path / "moving"), frames=6, width=400, trails=True
        )
        held = await server_mod.boil(
            str(tmp_path / "held"),
            frames=6,
            width=400,
            amplitude=0.001,
            trails=True,
        )

    for result in (moving, held):
        assert Path(result["exposure"]).is_file(), "no exposure was written"
        assert result["ground"] in ("light", "dark")

    assert moving["smear"] > 0.0, (
        f"a boil that visibly moves left no trail: {moving['smear']}"
    )
    assert held["smear"] == 0.0, (
        f"a boil too small to see reported a smear of {held['smear']}, so the "
        "exposure is inventing motion"
    )

    # The exposure is the frames, not one of them: it must differ from the
    # sharp last pose it ends on, and cover at least as much as it does.
    frames = sorted(Path(moving["directory"]).glob("frame_*.png"))
    last = decode(frames[-1].read_bytes())
    exposure = decode(Path(moving["exposure"]).read_bytes())

    assert exposure.pixels.shape == last.pixels.shape
    assert difference(exposure, last) > 0.0, "the exposure is just the last frame"


async def test_a_boil_says_what_its_wobble_follows(boiled):
    """The channel, reported rather than implied.

    1UBQ's B-factors span 2 to 46.9, so the wobble carries something; on a flat
    column it would carry nothing and the note says *that* instead. A treatment
    that claimed a channel either way would be the bake-off again.
    """
    note = boiled["loud"]["steps"][0]
    assert "B-factor" in note and "disorder wanders" in note, note
    assert "carries nothing" not in note, note


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

# Read off the live catalogue rather than listed here. The hand-kept version
# this replaces went stale the moment the catalogue changed: it still named the
# colour of a deleted view and knew nothing of `spacefill` or `skeleton`, so it
# was pinning a vocabulary nothing used while the one in use went unchecked.
_VIEW_THEMES = sorted(
    (
        {view.color for view in server_mod._VIEWS.values()}
        | {
            # Not a view's colour today, and both spoken for in docs/views.md:
            # the charge view is planned on partial-charge (a proxy, not a
            # solve), and `illustrative` is the colour half of the styling
            # preset of that name.
            "partial-charge",
            "illustrative",
        }
    )
    # `plddt` is the one colour in the catalogue that *cannot* be asked for
    # here, and by design: this suite's fixture is 1UBQ, and `color("plddt")`
    # on an experimental structure is refused — a crystal structure's B-factors
    # are not confidences, and painting the AlphaFold ramp over them produces a
    # confident-looking picture that means nothing (backlog 41). The refusal
    # firing here is the guard working; excluding it is the test wiring
    # catching up with a view whose subject is a different kind of file.
    #
    # Covered instead by `test_plddt_colour_reaches_the_pixels` and
    # `test_plddt_width_is_the_exact_reverse_of_uncertaintys` above, both of
    # which put a confidence ramp in the column and go down the bridge. The
    # exclusion must not become a hole: this is the failure mode the derived
    # list exists to prevent, and dropping a theme from it without a named
    # replacement would reintroduce exactly that.
    - {"plddt"}
)

# `cartoon` is what `_bare_fold` draws as its baseline, so drawing it again
# would be comparing a picture with itself. That baseline asserts its own
# coverage, which is the same claim for that one primitive.
_VIEW_REPRESENTATIONS = sorted(
    {view.representation for view in server_mod._VIEWS.values()} - {"cartoon"}
)


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


@pytest.mark.parametrize("representation", _VIEW_REPRESENTATIONS)
async def test_a_representation_the_views_need_draws_something(representation):
    """One case per primitive some view in the catalogue is built on."""
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


# -- the camera belongs to the caller ------------------------------------------


async def test_a_load_takes_the_camera_off_automatic_fitting():
    """Backlog 26, pinned at the mechanism rather than at the symptom.

    Mol\\*'s `commitScene` requests a camera reset whenever `shouldResetCamera()`
    decides the visible bounding sphere has moved out from under the camera, and
    a commit has a 250 ms budget it can run out of. Which commit boundary a
    `hide` and a `show` land on then decided whether that test ran against the
    old scene or the new one, so `show()` moved the camera about **one time in
    seven** and held still the rest — measured, fourteen runs, twelve at 0.0 and
    two at 0.030043.

    That rate is why this test asserts the property and not the picture. A
    repeated-`show()` comparison would pass six times in seven against a
    regression, which is a test that mostly cannot fail.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        # Stringified because `evaluate` parses what comes back as JSON, and a
        # bare boolean is not a JSON document.
        camera = await session.evaluate(
            "JSON.stringify(window.__protean.plugin.canvas3d.props.camera)"
        )
        assert camera["manualReset"] is True, (
            "the automatic fit is live again, and show() can take the camera"
        )


async def test_a_load_still_frames_the_molecule_itself():
    """The other half: `manualReset` suppresses the fit a load used to get free.

    Without an explicit reset beside it this leaves every load framed by
    whatever the camera was doing before, which looks like nothing at all on an
    empty canvas and like the wrong molecule on a second load.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        drawn = await _shot(session)
        assert coverage(drawn) > 0.02, "loaded and framed nothing"

        camera = await session.request("camera_state")
        assert camera["radius"] and camera["radius"] > 0, "camera never fitted"


async def test_the_ghost_heart_is_scenery_and_takes_no_clicks():
    """A see-through surface exists to be looked *through*.

    Left pickable it intercepts every click meant for what is inside it, so a
    selection lands on a jagged patch of mesh rather than on the residue
    someone aimed at. `markerActions` matters as much as `pickable` and for a
    different reason: picking decides what a click *hits*, marker actions
    decide what lights up when something else is highlighted — so without both,
    clicking the cartoon underneath still flares the surface over it.

    Asserted on the representation's state rather than by dispatching a click,
    because a synthetic click has to land on a pixel the surface actually
    covers, and choosing that pixel is a second measurement that can be wrong
    on its own.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("ghost-heart")

        states = await session.evaluate(
            "JSON.stringify(window.__protean.plugin.managers.structure.hierarchy"
            ".current.structures[0].components"
            ".flatMap(c => c.representations)"
            ".map(r => ({"
            "  type: r.cell.transform.params?.type?.name ?? null,"
            "  pickable: r.cell.obj?.data?.repr?.state?.pickable ?? null,"
            "  markers: r.cell.obj?.data?.repr?.state?.markerActions ?? null,"
            "})))"
        )

    surfaces = [s for s in states if s["type"] == "molecular-surface"]
    assert surfaces, f"no ghost surface was drawn at all: {states}"
    for surface in surfaces:
        assert surface["pickable"] is False, f"the ghost takes clicks: {surface}"
        assert surface["markers"] == 0, f"the ghost still lights up: {surface}"

    others = [s for s in states if s["type"] != "molecular-surface"]
    assert others, "nothing else was drawn, so this proves nothing about scoping"
    assert all(s["pickable"] is not False for s in others), (
        f"everything became scenery, not just the ghost: {others}"
    )


@pytest.mark.parametrize(
    ("mode", "args"),
    [("spin", {}), ("rock", {"angle": 30})],
)
async def test_a_turning_view_actually_turns(mode, args):
    """`spin()` reported success while the camera sat perfectly still.

    Mol\\* 5 added an `axis` parameter to both the spin and rock groups and
    dereferences it every frame, and `TrackballControls.setProps` shallow-
    assigns rather than filling in group defaults — so a params object without
    it replaced the animation with one that could not run. The reply said
    `{mode: 'spin', speed: 1}` either way.

    Nothing caught it. The suite checked what the viewer *reported*, and the
    viewer reported what it had been asked for rather than what it did. This
    asks the camera instead, which is the only thing that knows.
    """
    async with viewer_session(FIXTURE) as session:
        await session.request("spin", {"mode": "off"})
        before = await _camera_position(session)

        await session.request("spin", {"mode": mode, "speed": 1, **args})
        await asyncio.sleep(2.0)
        turning = await _camera_position(session)
        assert turning != before, f"{mode} reported success and moved nothing"

        # And stopping has to stop it, or the viewer is left animating under
        # whatever is captured next.
        await session.request("spin", {"mode": "off"})
        stopped = await _camera_position(session)
        await asyncio.sleep(1.0)
        assert await _camera_position(session) == stopped, "off did not stop it"


async def _camera_position(session) -> list[float]:
    """Where the camera actually is, rounded past floating-point jitter."""
    position = await session.evaluate(
        "JSON.stringify(window.__protean.plugin.canvas3d.camera.state.position)"
    )
    return [round(v, 6) for v in position]


# -- width as a channel of its own ---------------------------------------------


async def test_a_size_theme_changes_the_width_of_what_is_drawn():
    """Colour was exposed and width was not, so `putty` varied with B-factor
    because Mol* happened to default it that way and nothing could say
    otherwise. This is the claim that `size()` reaches the pixels at all."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="putty", handle="fold")
        varying = await _shot(session)

        # `uniform` is the honest opposite of putty's default: one width
        # everywhere, so the tube stops carrying the B-factor at all.
        await server_mod.size("uniform", name="fold")
        flattened = await _shot(session)

        assert coverage(flattened) > 0.01, "the putty vanished rather than changing"
        assert difference(varying, flattened) > STYLED, "the width did not change"


async def test_a_cartoon_has_a_width_too():
    """Written first as "sizing a cartoon is refused", on the assumption that
    only tubes and spheres have a width. Measured before shipping: `physical`
    moves 0.0337 of the frame on a cartoon, which is more than it moves on a
    putty. The refusal would have blocked something that works.

    Kept as a test rather than deleted, because the assumption is an easy one
    to make again."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="cartoon", handle="fold")
        uniform = await _shot(session)

        await server_mod.size("physical", name="fold")
        physical = await _shot(session)

        assert difference(uniform, physical) > STYLED


async def test_an_unknown_size_theme_is_refused_with_the_real_list():
    """Validated against the live registry, like every other name protean
    takes, so the message cannot go stale against the bundled Mol*."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="putty", handle="fold")

        with pytest.raises(ViewerError, match="uncertainty"):
            await server_mod.size("thickness", name="fold")


async def test_size_themes_are_reported_as_a_capability():
    """A model can only pick from what it can see at the point of use, which
    is why the colour themes are already listed here."""
    async with viewer_session(FIXTURE) as session:
        caps = await session.request("capabilities", {})
        assert "uncertainty" in caps["size_themes"]
        assert "physical" in caps["size_themes"]


# -- a number of your own, drawn ------------------------------------------------


async def test_a_registered_field_paints_and_sizes_what_it_matches():
    """The point of the exercise: an arbitrary per-residue number becomes both
    a colour and a width, with no structure re-sent and no B-factor column
    borrowed to carry it."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        # 1UBQ is one chain, 76 residues; a ramp along it is unmistakable.
        values = [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)]

        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="putty", handle="fold", color="#ffffff")
        plain = await _shot(session)

        reply = await server_mod.define_field("ramp", values)
        assert reply["matched"] > 0, "registered against nothing"

        await server_mod.color("ramp", name="fold")
        painted = await _shot(session)
        assert difference(plain, painted) > STYLED, "the field did not colour anything"

        await server_mod.size("ramp", name="fold")
        widened = await _shot(session)
        assert difference(painted, widened) > STYLED, "the field did not size anything"


async def test_a_field_matching_no_residue_is_refused():
    """Registering happily and painting the whole molecule the no-data grey is
    indistinguishable from a rendering fault, and reports as success."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        with pytest.raises(ViewerError, match="matches no residue"):
            await server_mod.define_field(
                "wrong", [{"chain": "Z", "seq": 9000, "value": 1.0}]
            )


async def test_a_field_takes_an_analysis_reply_unchanged():
    """`rmsf()` names its number "rmsf" and conservation names its own; making
    a caller rename before drawing would make the common case the awkward one.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        reply = await server_mod.define_field(
            "as_returned",
            [{"chain": "A", "seq": n, "rmsf": n / 10} for n in range(1, 77)],
        )
        assert reply["matched"] > 0

        with pytest.raises(ViewerError, match="more than one number"):
            await server_mod.define_field(
                "ambiguous", [{"chain": "A", "seq": 1, "rmsf": 1.0, "plddt": 2.0}]
            )


async def test_a_field_can_be_registered_again_with_a_better_domain():
    """Looking at the result and re-fitting the domain is the obvious second
    step, and Mol*'s registry throws on a name it already holds — which would
    have failed the correction while leaving the wrong version installed."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        values = [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)]
        await server_mod.define_field("ramp", values)
        again = await server_mod.define_field("ramp", values, domain=[0, 200])

        assert again["domain"] == [0, 200], "the second registration did not take"


async def test_a_field_does_not_outlive_the_structure_it_was_keyed_to():
    """Mol*'s theme registries are plugin-wide and survive `plugin.clear()`, so
    a field would stay in capabilities() after the next load and paint the new
    molecule almost entirely the no-data grey."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.define_field(
            "ramp", [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)]
        )
        assert "ramp" in (await session.request("capabilities", {}))["color_themes"]

        await session.request("clear", {})
        after = await session.request("capabilities", {})
        assert "ramp" not in after["color_themes"], "the field outlived the structure"
        assert "ramp" not in after["size_themes"]


async def test_a_field_will_not_take_a_name_molstar_owns():
    """`physical` is a size theme with no colour twin, so adding the colour
    half first and discovering the collision second would leave an unpaired
    theme installed that nothing can remove."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        with pytest.raises(ViewerError, match=r"Mol\*'s own"):
            await server_mod.define_field(
                "physical", [{"chain": "A", "seq": 1, "value": 1.0}]
            )
        # And nothing was half-registered on the way out.
        caps = await session.request("capabilities", {})
        assert "physical" not in caps["color_themes"]


async def test_a_field_colours_the_sticks_as_well_as_the_atoms():
    """A bond location carries `aUnit`/`aIndex` rather than `unit`/`element`,
    so the first version of the lookup returned "no data" for every stick: a
    ball-and-stick with a ramp on the balls and grey on the sticks, which
    reads as a half-broken render."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(
            representation="ball-and-stick", handle="fold", color="#ffffff"
        )
        # Flat, unlit and unshaded: a lit white surface passes through mid-grey
        # on its way to its dark side, which is indistinguishable from the
        # no-data grey and made the first version of this test measure the
        # lighting rig. Flat keeps the palette's own colours on screen.
        await server_mod.lighting("flat")
        await server_mod.shading("flat", name="fold")
        await server_mod.effects(occlusion=False, shadow=False)
        await server_mod.define_field(
            "ramp",
            [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)],
            palette="white-red",
        )
        await server_mod.color("ramp", name="fold")
        painted = await _shot(session)

        # 0x808080 is what an unmatched location paints, and white-red never
        # produces it. Measured: 0.0000 with bonds resolved, 0.0211 without.
        assert color_fraction(painted, (0x80, 0x80, 0x80, 255), tolerance=6) < 0.0005


async def test_a_domain_with_no_width_is_refused():
    """[5, 5] or [5, 0] would paint every residue the middle of the ramp: one
    flat colour at one flat width, which looks like a broken render and reports
    as a success."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        values = [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)]
        for bad in ([5.0, 5.0], [5.0, 0.0]):
            with pytest.raises(ViewerError, match="which has no width"):
                await server_mod.define_field("flat", values, domain=bad)


async def test_a_snapshot_can_be_engraved_on_the_way_out(tmp_path):
    """The finish runs on the file, not in the viewer, so this is the only
    place the whole path is exercised: capture, engrave, save, reopen."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("publication-cartoon")
        plain = await server_mod.snapshot(str(tmp_path / "plain.png"), width_mm=60)
        inked = await server_mod.snapshot(
            str(tmp_path / "inked.png"), width_mm=60, finish="hedcut"
        )

        assert inked["finish"] == "hedcut"
        assert "after the capture" in inked["finish_applied"]
        assert 0.0 < inked["ink"] < 0.9, "a cartoon on white should not fill in"
        assert inked["pixels"] == plain["pixels"], "the finish changed the size"

        # Two tones and nothing between, which is what makes it an engraving.
        engraved = decode((tmp_path / "inked.png").read_bytes())
        assert set(np.unique(engraved.pixels[:, :, :3]).tolist()) <= {0, 255}


async def test_a_plate_print_colours_for_its_capture_and_puts_the_scene_back(
    tmp_path,
):
    """The separation is guaranteed rather than hoped for, and it costs the
    caller nothing.

    A capture-time finish reads pixels and cannot know what a hue was *made* to
    mean, so `spot-ink-plates` sorting by colour could only ever claim "a plate
    per whatever this render happened to be coloured by". Asking the viewer to
    colour by element for the one capture makes "a plate per element" true.

    The price would be a capture tool quietly changing the caller's scene,
    which is a worse surprise than a narrow claim — so it does not. The scene
    here is deliberately painted a flat white first, which is the *worst* case:
    a white scene has no colour families at all, so if the theme were not
    applied the print could not separate, and if it were not put back the
    screenshot afterwards would come back in element colours.

    Both halves are asserted, because either alone passes for a broken version.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="spacefill", handle="fold", color="#ffffff")

        before = await _shot(session)
        reply = await server_mod.snapshot(
            str(tmp_path / "press.png"), width_mm=60, finish="spot-ink-plates"
        )
        after = await _shot(session)

        assert reply["separated_by"] == "element-symbol"
        assert reply["scene_restored"] is True

        printed = decode((tmp_path / "press.png").read_bytes())
        paper = (247, 243, 233)
        inks = {
            tuple(c)
            for c in np.unique(printed.pixels[:, :, :3].reshape(-1, 3), axis=0).tolist()
        } - {paper}
        assert len(inks) > 1, (
            f"a white scene printed on one plate, so the capture was never "
            f"recoloured: {sorted(inks)}"
        )

        assert difference(before, after) == 0.0, (
            "the viewer was left in the colours the print asked for, so the "
            "capture changed the caller's scene"
        )


async def test_an_unknown_finish_is_refused_before_anything_is_written(tmp_path):
    """A file half-written in a style nobody asked for is worse than an error."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        out = tmp_path / "nope.png"
        with pytest.raises(
            ViewerError,
            match="cross-hatch, cyanotype, engraving, hedcut, spot-ink-plates",
        ):
            await server_mod.snapshot(str(out), width_mm=60, finish="woodblock")


# -- sidechains that are attached to something ---------------------------------


async def test_sidechains_are_drawn_from_the_alpha_carbon():
    """They floated. `sidechain` is "polymer and not backbone" and CA *is*
    backbone, so the sticks began at CB with no bond back to anything — a
    cloud of fragments beside the ribbon they belong to.

    Reported by looking at it, and the fix is to draw the anchor too. The
    selection keyword is untouched: its definition is right and heavily
    tested, and only what this view draws changes.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("textbook")
        await server_mod.preset("sidechains")

        drawn = server_mod._handles.get(server_mod._SIDECHAIN_HANDLE).indices
        names = set(server_mod._structure.atom_name[drawn].tolist())

        assert "CA" in names, "the anchor is missing, so the sticks float"
        assert "N" not in names and "C" not in names, (
            "the rest of the backbone came too, which draws a second chain"
        )


async def test_sidechains_take_protean_element_colours():
    """Mol*'s `element-symbol` can recolour carbon and nothing else, so an
    all-atom view could not be made to agree with the cartoon under it."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("textbook")
        await server_mod.preset("sidechains")

        painted = await _shot(session)
        # Teal nitrogen, from protean's palette and from nothing in Mol*'s.
        assert color_fraction(painted, (0x4E, 0xC9, 0xC9, 255), tolerance=24) > 0.0005


async def test_an_element_palette_refuses_a_colour_that_is_not_one():
    """`parseInt('#oops'.slice(1), 16)` is NaN and NaN paints black without
    complaint, which reads as a render failure rather than a bad argument."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        with pytest.raises(ViewerError, match="not a colour"):
            await server_mod.define_elements(colors={"C": "burnt sienna"})


async def test_an_element_palette_will_not_take_a_name_molstar_owns():
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        with pytest.raises(ViewerError, match=r"Mol\*'s own"):
            await server_mod.define_elements(name="element-symbol")


# -- comparing two structures without interleaving them -------------------------


async def test_superposing_registers_a_field_over_every_shared_residue():
    """A superposed pair drawn in two colours is close to unreadable: where the
    two agree the backbones interleave at one depth and read as a mottle, and
    where they disagree looks the same.

    The field is the answer, and it has to cover the whole molecule. The fit
    discards outliers to find its transform, and on a hinge motion the
    discarded residues are the ones that moved — 185 of maltose-binding
    protein's 370. A field built from the fit alone paints the rigid lobe and
    leaves the hinge blank.
    """
    async with (
        viewer_session("1omp") as session,
        _as_server(session, load=True, pdb_id="1omp"),
    ):
        out = await server_mod.superpose("1anf", "1omp", show=True)
        field = out["deviation_field"]

        assert out["aligned_residues"] < 250, "the fit kept more than expected"
        assert field["residues"] > 350, (
            "the field covers only what the fit kept, so the hinge is blank"
        )

        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.show(representation="cartoon", handle=field["target_handle"])
        plain = await _shot(session)
        await server_mod.color("deviation", name=field["target_handle"])
        painted = await _shot(session)

        assert difference(plain, painted) > STYLED, "the deviation did not paint"


async def test_a_view_draws_what_is_bound_as_well_as_the_polymer():
    """`textbook` selects `polymer` and a ligand is not polymer, so
    maltose-binding protein came up with no maltose in it — which is most of
    the reason anyone loads that structure."""
    async with (
        viewer_session("1anf") as session,
        _as_server(session, load=True, pdb_id="1anf"),
    ):
        await server_mod.preset("textbook")

        assert server_mod._LIGAND_HANDLE in server_mod._handles.names()
        drawn = server_mod._handles.get(server_mod._LIGAND_HANDLE).indices
        assert set(server_mod._structure.res_name[drawn].tolist()) == {"GLC"}


async def test_a_structure_with_nothing_bound_draws_no_ligand():
    """No handle rather than an empty one: a handle naming nothing is the kind
    of thing a later call resolves successfully and draws nothing from."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("textbook")

        assert server_mod._LIGAND_HANDLE not in server_mod._handles.names()


# -- the views that take an argument -------------------------------------------


async def test_a_ligand_view_finds_the_ligand_by_name():
    """`active-site` already draws this; what a caller has is a name, not a
    handle. The reply says which ligand and how many copies, because four
    hemes is a different picture from one and nobody can see the screen."""
    async with (
        viewer_session("1anf") as session,
        _as_server(session, load=True, pdb_id="1anf"),
    ):
        before = await _shot(session)
        out = await server_mod.ligand_view("GLC")
        after = await _shot(session)

        assert out["ligand"] == "GLC"
        assert out["copies"] == 2, "maltose is two glucose units"
        assert out["lining_residues"] > 0
        # Not `coverage`: this view focuses on the site, so the molecule fills
        # the frame and the corners stop agreeing on what the background is.
        assert difference(before, after) > STYLED


async def test_a_ligand_that_is_not_there_names_the_ones_that_are():
    """ "No HEM here" leaves a caller guessing whether they misspelled it or
    loaded the wrong file. The list settles it in one reply."""
    async with (
        viewer_session("1anf") as session,
        _as_server(session, load=True, pdb_id="1anf"),
    ):
        with pytest.raises(ViewerError, match="GLC"):
            await server_mod.ligand_view("HEM")


async def test_a_mutation_view_refuses_a_residue_that_is_not_what_it_claims():
    """The one thing worth doing better than MCPymol, which does not check.

    A mutation view that highlights the wrong residue because the numbering is
    offset by a construct tag looks exactly like one that worked — confident
    either way, with no way for the reader to tell.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        # 1UBQ position 1 is methionine.
        out = await server_mod.mutation_view("M1A")
        assert out["verified"][0]["residue"] == "MET"

        with pytest.raises(ViewerError, match="holds MET, not TRP"):
            await server_mod.mutation_view("W1A")


async def test_a_mutation_that_is_not_notation_says_what_notation_is():
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        with pytest.raises(ViewerError, match="not a mutation"):
            await server_mod.mutation_view("the first one")


async def test_default_puts_back_the_picture_the_load_produced():
    """Watched go wrong: eight views clicked in a row leave no way back,
    because each hides `auto` and replaces the handle they share."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        loaded = await _shot(session)
        await server_mod.preset("spacefill")
        away = await _shot(session)
        assert difference(loaded, away) > STYLED, "the view changed nothing"

        await server_mod.preset("default")
        back = await _shot(session)

        assert server_mod._SCENE_HANDLE not in server_mod._handles.names()
        assert difference(loaded, back) < difference(loaded, away), (
            "default did not get closer to the picture the load produced"
        )


async def test_a_pocket_view_draws_a_surface_around_what_is_bound():
    """The same lining `ligand_view` draws as sticks, drawn as a surface —
    which is what a pocket looks like when the question is about shape."""
    async with (
        viewer_session("1anf") as session,
        _as_server(session, load=True, pdb_id="1anf"),
    ):
        before = await _shot(session)
        out = await server_mod.pocket_view("GLC")
        after = await _shot(session)

        assert out["lining_residues"] > 0
        assert difference(before, after) > STYLED


async def test_a_crosslink_view_refuses_a_structure_with_nothing_holding_it():
    """1UBQ has no disulfides and no metals. A cartoon with nothing picked out
    looks the same as a search that failed."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        with pytest.raises(ViewerError, match="No disulfides and no metals"):
            await server_mod.crosslink_view()


async def test_a_crosslink_view_finds_the_disulfides_that_are_there():
    """Lysozyme has four, which is why it is the structure in every textbook
    chapter about them."""
    async with (
        viewer_session("2lyz") as session,
        _as_server(session, load=True, pdb_id="2lyz"),
    ):
        out = await server_mod.crosslink_view()

        assert len(out["disulfides"]) == 4, out["disulfides"]
        assert all(bridge["angstroms"] < 2.5 for bridge in out["disulfides"])


async def test_a_pharmacophore_view_types_a_ligand_and_says_how():
    """The typing is inferred rather than read — most crystal structures carry
    no hydrogens — so the reply carries the counts and says which rules fired.
    """
    async with (
        viewer_session("1anf") as session,
        _as_server(session, load=True, pdb_id="1anf"),
    ):
        before = await _shot(session)
        out = await server_mod.pharmacophore_view("GLC")
        after = await _shot(session)

        # A sugar is hydroxyls and ring carbons: mostly "both", some acceptor.
        assert out["features"].get("both", 0) > 0, out["features"]
        assert "inferred" in out
        assert difference(before, after) > STYLED


async def test_a_crosslink_view_finds_a_metal_site_without_taking_the_whole_structure():
    """`not metals within X of metals` parses as `not (metals within X of
    metals)`, which is everything *not* near a metal — 1260 atoms of 1260 on
    myoglobin, drawn as ball-and-stick and reported as coordinating.

    Neither structure the other crosslink tests use has a metal, which is why
    nothing caught it.
    """
    async with (
        viewer_session("1mbn") as session,
        _as_server(session, load=True, pdb_id="1mbn"),
    ):
        out = await server_mod.crosslink_view()

        assert out["metal_atoms"] >= 1
        total = server_mod._residue_count(
            server_mod._structure,
            np.ones(server_mod._structure.array_length(), dtype=bool),
        )
        assert out["coordinating_residues"] < total / 2, (
            "the coordination selection took most of the structure"
        )


async def test_default_takes_away_a_layer_view_too():
    """`ghost-heart` is one click away and registers a handle of its own, so a
    "way back" that knew only the scene, ligand and sidechain handles left its
    translucent surface wrapped around whatever came next."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("ghost-heart")
        assert any("ghost" in name for name in server_mod._handles.names())

        await server_mod.preset("default")

        assert not any("ghost" in name for name in server_mod._handles.names())
