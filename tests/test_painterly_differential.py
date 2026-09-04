"""The painterly pass, measured on the pixels it actually produced.

`brushwork()` is protean's own render pass patched into Mol\\*'s, so almost
every way it can fail is a way that reports success:

- It can paint the canvas and not the capture, because `ImagePass` owns a
  different `DrawPass` from the one on screen (`passes/image.js:44`). Every
  test here that asserts on a capture is paired with one that asserts on the
  drawing buffer, because **either arm alone passes for the broken version**.
- It can be patched onto a second copy of Mol\\*'s classes and never run at
  all, which is why the reply carries `reaches_viewer` and why it is asserted.
- It can resolve its brush against the wrong frame and paint a 1200px plate
  with a 400px viewport's marks. `analysis/hatching.py` shipped that bug in
  Pillow, where at least there was a number in the reply to catch it; on the
  GPU there is not.
- It can report a size that changes and a picture that does not.

Requires a real browser and is opt-in:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_painterly_differential.py
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import numpy as np
import pytest

import protean_mcp.server as server_mod
from protean_mcp.connection import ViewerError

from .browser import BROWSER_MARKS, viewer_session
from .pixels import (
    TOLERANCE,
    Render,
    background,
    close,
    coverage,
    decode,
    difference,
)
from .test_render_differential import _as_server

pytestmark = BROWSER_MARKS

FIXTURE = "1ubq"

#: How much of the *subject* a finish has to repaint. Of the subject, not of the
#: frame, and that distinction is the whole of this constant's history.
#:
#: It was 0.4 of the frame, measured at 0.86 — and 0.86 of a frame whose subject
#: covers 0.03 can only have been the *background* moving. It was: the canvas
#: weave was one-sided and took up to 13% off every ground pixel, which is above
#: `tests/pixels.py`'s 8/255 tolerance. So the guard for "the finish reaches the
#: capture" was passing on the strength of a darkened background, and would have
#: gone on passing with the brush switched off entirely.
#:
#: Centring the weave on its own mean height — which it should always have been —
#: dropped the frame difference to 0.118 and left this test with nothing to
#: stand on. Measured over the subject instead: 0.93 of it changes.
PAINTED = 0.6

#: Two brush sizes differ over the *subject*, not over the frame — the ground
#: takes no brushwork at all. So the claim is expressed against how much of the
#: frame the molecule covers rather than against a number picked from the air.
#:
#: Measured on 1UBQ at a 1200px capture: the ribbon covers 0.045 of the frame
#: and `fine` against `broad` changes 0.0315 of it — 0.70 of everything drawn.
#: The first version of this test asserted 0.05 of the *frame*, which is more
#: than the molecule occupies and could never have passed. Measure the number
#: before writing the threshold.
MARKED_FRACTION_OF_SUBJECT = 0.4


def _decode(data_uri: str) -> Render:
    return decode(data_uri)


def _subject(render: Render) -> Any:
    """The drawn pixels, as a mask: everything unlike this frame's own corner.

    A painterly finish touches the ground too — that is what a canvas texture
    is — but the ground is 96% of the frame, so a claim measured over the whole
    frame is a claim about the background whatever it says in its name.
    """
    px = render.pixels[:, :, :3].astype(np.int16)
    corner = px[4, 4]
    return np.abs(px - corner).max(axis=2) > 24


def _repainted(before: Render, after: Render) -> float:
    """Fraction of the drawn subject that the finish actually changed."""
    mask = _subject(before)
    gap = np.abs(before.pixels.astype(np.int16) - after.pixels.astype(np.int16))
    return float((gap.max(axis=2) > TOLERANCE)[mask].mean())


def _local_jump(render: Render, mask: np.ndarray, step: int) -> float:
    """Median colour change between a pixel and a neighbour `step` pixels
    away, over the masked region — a measure of *local* variation at that
    scale, as opposed to `difference()`'s whole-frame mean.

    This is what tells a dab lattice from a smoothly shaded surface: shading
    changes slowly, so its own local jump at one dab's width is small, while
    a lattice of independently coloured dabs jumps sharply from one to the
    next. Comparing against the same statistic on the *plain* render is the
    mechanism-removed control — no dab lattice runs there at all.
    """
    px = render.pixels[:, :, :3].astype(np.int16)
    shifted = np.roll(px, -step, axis=1)
    shifted_mask = np.roll(mask, -step, axis=1)
    both = mask & shifted_mask
    if not both.any():
        return 0.0
    gap = np.abs(px - shifted).max(axis=2)
    return float(np.median(gap[both]))


async def _capture(session, width: int = 1200) -> Render:
    args = {"width": width, "crop": False}
    return _decode((await session.request("snapshot", args, timeout=240))["data_uri"])


async def _canvas(session) -> Render:
    """The drawing buffer itself, not a capture.

    `snapshot` goes through `ImagePass`, which owns its own draw pass; this is
    the picture a person is looking at. The two have to be checked separately
    or a finish that reaches one of them passes.
    """
    return _decode(
        await session.evaluate(
            "JSON.stringify(document.querySelector('#app canvas').toDataURL('image/png'))"
        )
    )


#: The brush is a fraction of the frame diagonal, and `fine` is 1/380 of it —
#: so a frame under about 1140 px across the diagonal cannot draw one, and
#: `brushwork()` refuses rather than drawing a different look. That is the
#: designed behaviour and it is unit-tested; it is also why this file cannot
#: take the frame it is given.
#:
#: **CI's headless canvas is 746x335.** Not the window — the canvas, after
#: Mol\*'s own furniture has taken its share. Every pixel threshold in
#: `test_render_differential.py` was calibrated at that size, which is worth
#: knowing before anyone reads one of them as a statement about a plate.
MIN_DIAGONAL = 1140


async def _widen(session) -> tuple[int, int]:
    """Give the canvas a frame big enough for the smallest brush to be a brush.

    The container is sized directly rather than the window, because the window
    is the runner's to decide and this suite has to mean the same thing on a
    laptop and on a CI box. Mol\\* follows its container on the next resize.
    """
    await session.evaluate(
        """JSON.stringify((() => {
          const app = document.getElementById('app');
          app.style.position = 'fixed';
          app.style.left = '0';
          app.style.top = '0';
          app.style.width = '1200px';
          app.style.height = '900px';
          window.dispatchEvent(new Event('resize'));
          return true;
        })())"""
    )
    read = "JSON.stringify(window.__protean.plugin.canvas3d.webgl.getDrawingBufferSize())"
    frame = {"width": 0, "height": 0}
    for _ in range(40):
        frame = await session.evaluate(read)
        if math.hypot(frame["width"], frame["height"]) >= MIN_DIAGONAL:
            break
        await asyncio.sleep(0.25)
    return frame["width"], frame["height"]


async def _ribbon(session) -> None:
    """A plain painted-subject scene: a ribbon in earth pigments, studio lit."""
    # Mol*'s own load preset is hidden first, or its waters sit under the
    # picture as a scatter of small spheres.
    await session.request("hide", {"name": "auto"})
    await session.request("select", {"name": "fold", "expression": "(sel.atom.all)"})
    await session.request(
        "show",
        {
            "name": "fold",
            "expression": "(sel.atom.all)",
            "representation": "cartoon",
            "color": "pigment",
        },
    )
    await session.request("background", {"color": "#4a3b2c", "gradient": "off"})
    await session.request("lighting", {"rig": "studio"})
    await session.request(
        "effects",
        {
            "occlusion": True,
            "shadow": True,
            "outline": False,
            "depth_of_field": False,
            "bloom": False,
            "sharpening": False,
        },
    )


@pytest.fixture(scope="module")
async def painted() -> dict[str, Any]:
    """One session walking the pass, keeping both the pixels and the replies.

    Module-scoped because a browser session costs 5-10 seconds on CI and every
    claim below is about the same scene seen under different settings.
    """
    frames: dict[str, Any] = {}
    async with viewer_session(FIXTURE) as session:
        frames["frame"] = await _widen(session)
        await _ribbon(session)

        frames["plain"] = await _capture(session)
        frames["plain_canvas"] = await _canvas(session)

        frames["reply"] = await session.request(
            "brushwork", {"look": "chiaroscuro", "brush_size": "medium"}
        )
        frames["painted"] = await _capture(session)
        frames["painted_canvas"] = await _canvas(session)

        frames["sizes"] = (await session.request("capabilities", {}))["brush_sizes"]
        for size in frames["sizes"]:
            frames[f"{size}_reply"] = await session.request(
                "brushwork", {"brush_size": size}
            )
            frames[size] = await _capture(session)

        # Half the size, same scene. A brush resolved against a stale frame
        # paints this one with the 1200px mark, or fails to paint it at all.
        frames["small"] = await _capture(session, width=600)

        frames["off_reply"] = await session.request("brushwork", {"look": "off"})
        frames["off"] = await _capture(session)
        frames["off_canvas"] = await _canvas(session)

        # And the same scene with Mol*'s multisampling switched off, which is
        # a different route to the screen and a different seam in the pass.
        # Mol*'s own controls panel is one click away in protean's viewer, so
        # this is a state a person can reach.
        await session.evaluate(
            "JSON.stringify(!!window.__protean.plugin.canvas3d"
            ".setProps({ multiSample: { mode: 'off' } }))"
        )
        frames["single_plain"] = await _canvas(session)
        await session.request("brushwork", {"look": "chiaroscuro"})
        frames["single_painted"] = await _canvas(session)
        # Off again, and then something visible changes. A wrapper that swallows
        # the frame on this route leaves the *previous* picture standing on the
        # canvas — Mol* keeps its drawing buffer, so a stale frame looks exactly
        # like a correct one until the scene moves.
        await session.request("brushwork", {"look": "off"})
        await session.request("background", {"color": "#123456", "gradient": "off"})
        frames["single_after"] = await _canvas(session)
    return frames


async def test_the_finish_survives_multisampling_being_switched_off(painted):
    """The frame reaches the screen by one of three routes, and this is the one
    protean never takes by default: `multiSample: off` sends it straight through
    `DrawPass` instead of the accumulator.

    It matters more than a rarely-taken branch usually would. The pass forces
    `toDrawingBuffer` to false, which means Mol\\*'s own copy to the canvas never
    runs — so a seam missing on this route does not draw a plain picture, it
    draws a **black** one, and only pixels can tell.
    """
    plain = painted["single_plain"]
    # Both ends, and this is the load-bearing pair rather than belt and braces:
    # a wrapper that blanks the canvas when no look is set still produces a
    # large difference against a painted frame, so the difference alone passes
    # for it. Only asking whether each frame has any light in it can tell.
    for name in ("single_plain", "single_painted"):
        lit = float(painted[name].pixels[:, :, :3].mean())
        assert lit > 16, f"{name} came back at a mean brightness of {lit:.1f}"

    changed = _repainted(plain, painted["single_painted"])
    assert changed > PAINTED, (
        f"with multisampling off the canvas changed on {changed:.4f} of the "
        "subject, so the plain draw route has no finish on it"
    )

    # And the route still delivers *new* frames with no look on it. This is the
    # arm the brightness check above cannot supply: the wrapper takes ownership
    # of the only blit to the canvas, so one that returns without blitting
    # leaves Mol*'s retained drawing buffer showing the last good picture.
    assert close(background(painted["single_after"]), (0x12, 0x34, 0x56, 255)), (
        f"the canvas reads {background(painted['single_after'])} after the "
        "background was changed, so the plain draw route stopped updating it"
    )


async def test_the_suite_has_a_frame_the_smallest_brush_can_be_drawn_in(painted):
    """Stated rather than assumed, because everything below depends on it.

    `fine` is 1/380 of the frame diagonal, so under about 1140 px it is below
    the 3 px floor and `brushwork()` refuses — correctly. CI's headless canvas
    is 746x335, which is where this suite first failed and where the number
    above comes from. Without this assertion the fixture would refuse and every
    test in the file would error with the same message, which is a great deal
    of noise for one fact.
    """
    width, height = painted["frame"]
    assert math.hypot(width, height) >= MIN_DIAGONAL, (
        f"the canvas came back {width}x{height}; the smallest brush needs a "
        f"diagonal of {MIN_DIAGONAL} and the container resize did not take"
    )


async def test_the_finish_reaches_the_capture(painted):
    """The file has to carry the look, and this is the arm that can pass alone.

    It is here twice over: `ImagePass` builds its own `DrawPass`, so a pass
    patched onto the canvas's instance paints the screen and leaves every
    `snapshot()` plain — with a success message on it.
    """
    changed = _repainted(painted["plain"], painted["painted"])
    assert changed > PAINTED, (
        f"the capture changed on {changed:.4f} of the subject, so the finish "
        "never reached ImagePass"
    )


async def test_the_finish_reaches_the_canvas(painted):
    """And the screen has to carry it, which is the whole reason this pass
    exists rather than a sixth entry in `snapshot(finish=...)`."""
    changed = _repainted(painted["plain_canvas"], painted["painted_canvas"])
    assert changed > PAINTED, (
        f"the drawing buffer changed on {changed:.4f} of the subject, so the "
        "finish is in the file and not on the screen"
    )


async def test_the_pass_is_patched_into_the_viewer_that_is_running(painted):
    """`reaches_viewer` compares the classes this pass patched against the ones
    the live viewer built with.

    Mol\\* ships no `exports` map, so `molstar/lib/.../draw` resolves by
    extension probing; a bundler that hands out two copies of the class leaves
    the patch on the one nobody uses, and every call still returns ok.
    """
    assert painted["reply"]["reaches_viewer"] is True


async def test_taking_the_finish_off_gives_back_the_exact_picture(painted):
    """Bit-identical, not merely similar.

    The pass forces Mol\\* to render into a target rather than straight to the
    canvas, so "off" is not a no-op — it is a different code path that has to
    arrive at the same pixels. Anything less than 0.0 here means the wrapper
    leaves a residue on every frame protean has ever drawn.
    """
    assert difference(painted["plain"], painted["off"]) == 0.0
    assert difference(painted["plain_canvas"], painted["off_canvas"]) == 0.0
    assert painted["off_reply"]["look"] == "off"


async def test_the_brush_size_changes_the_mark_and_not_only_the_number(painted):
    """The defect this is for shipped once already, in Pillow: a finish whose
    resolved size moved and whose picture did not.

    The first version of this pass scaled only the abstraction radius, and
    `fine` against `broad` came back as very nearly the same image — because
    what a viewer sees is the stroke and the grain of the bristle, and a
    render has almost no texture for an abstraction to work on. So both halves
    are asserted: the numbers move, *and* the pixels move with them.
    """
    # Every size the viewer offers, walked in the order it offers them. Naming
    # two here would have let a third arrive untested; asking `capabilities()`
    # is the same rule the rest of protean follows.
    sizes = painted["sizes"]
    assert len(sizes) >= 2, sizes
    brush = [painted[f"{size}_reply"]["brush_px"] for size in sizes]
    stroke = [painted[f"{size}_reply"]["stroke_px"] for size in sizes]
    assert brush == sorted(brush), dict(zip(sizes, brush, strict=True))
    assert stroke == sorted(stroke), dict(zip(sizes, stroke, strict=True))
    assert len(set(brush)) == len(sizes) and len(set(stroke)) == len(sizes)

    marked = difference(painted[sizes[0]], painted[sizes[-1]])
    drawn = coverage(painted["plain"])
    assert marked > MARKED_FRACTION_OF_SUBJECT * drawn, (
        f"{sizes[0]} and {sizes[-1]} differ on {marked:.4f} of the frame against "
        f"a subject covering {drawn:.4f}, so the size is reported and the mark "
        "is not drawn"
    )


async def test_a_smaller_plate_is_still_painted(painted):
    """The brush is a fraction of the frame and the pass reads that frame every
    time it runs, so a capture at another size is painted too.

    Nothing calls back into the pass on a resize — `Passes.updateSize`,
    `ImagePass.setSize` and `IlluminationPass.setSize` are three separate call
    sites — so the size is compared against the source on every frame instead.
    A stale one gives a picture that still renders and is silently at the wrong
    scale, which is why this is a test and not a comment.
    """
    small = painted["small"]
    assert small.width == 600, f"the capture came back {small.size}"
    # Not compared against the 1200px plate, which is a different size and
    # cannot be differenced. Compared against its own ground: a frame the pass
    # skipped would be the flat `#4a3b2c` it was told to be, everywhere.
    flat = float((small.pixels[:, :, :3].std(axis=(0, 1))).mean())
    assert flat > 1.0, f"the 600px capture has no variation in it at all ({flat:.3f})"


async def test_a_painted_ground_refuses_to_be_cropped():
    """`autocrop` finds the molecule by testing each pixel for *exact* equality
    with the background colour, and a painted ground has none left to match —
    so the box comes back as the whole frame and `cropped: true` is a lie.

    Refused rather than quietly ignored, and the refusal names the way out.
    """
    async with viewer_session(FIXTURE) as session:
        await _ribbon(session)
        # The control: without a look, cropping is fine. Asserted first, so a
        # refusal that fires for every capture cannot pass this test.
        ok = await session.request("snapshot", {"width": 400, "crop": True}, timeout=240)
        assert ok["cropped"] is True

        await session.request("brushwork", {"look": "chiaroscuro"})
        with pytest.raises(ViewerError, match="paints the ground"):
            await session.request("snapshot", {"width": 400, "crop": True}, timeout=240)


async def test_the_pigment_palette_is_not_jmols_and_is_not_black():
    """The pigment theme is Mol\\*'s own secondary structure wearing earth
    colours, and the first version of it painted the whole molecule **black**
    while reporting itself applied.

    That happened because `SecondaryStructureColorTheme` reads
    `props.saturation` and `props.lightness`, and a props object carrying only
    the colour map hands it two undefineds — every channel comes out NaN.
    Nothing about the registration could see it; only a picture could.
    """
    async with viewer_session(FIXTURE) as session:
        await session.request("hide", {"name": "auto"})
        await session.request("select", {"name": "fold", "expression": "(sel.atom.all)"})
        await session.request("background", {"color": "#ffffff", "gradient": "off"})
        await session.request(
            "show",
            {
                "name": "fold",
                "expression": "(sel.atom.all)",
                "representation": "cartoon",
                "color": "secondary-structure",
            },
        )
        jmol = await _capture(session, width=600)
        await session.request("color", {"name": "fold", "color": "pigment"})
        earth = await _capture(session, width=600)

    apart = difference(jmol, earth)
    assert apart > 0.005, (
        f"pigment and Mol*'s own scheme differ on {apart:.4f} of the frame, so "
        "the colour map never reached the theme"
    )

    # And not black: a NaN colour map renders every drawn pixel at zero.
    drawn = earth.pixels[:, :, :3]
    nearly_black = ((drawn.max(axis=2) < 24) & (earth.pixels[:, :, 3] > 0)).mean()
    assert nearly_black < 0.01, (
        f"{nearly_black:.4f} of the frame is near-black, which is what an "
        "undefined saturation does to this theme"
    )


async def test_a_plate_sized_capture_of_a_large_molecule_survives():
    """One transparent pixel makes `snapshot()` refuse the whole capture, and
    the brush produced them.

    `atan(0.0, 0.0)` is undefined in GLSL. The brush's centre tap sits at
    exactly that offset, and on a real driver it came back NaN, poisoned every
    sector sum in the pixel and arrived as alpha zero. On an opaque canvas a
    transparent pixel cannot be legitimate, so `_incomplete_capture` reads it as
    a frame that was never finished and raises — which is the right behaviour
    and made the failure look like a size limit rather than a shader bug.

    Haemoglobin at plate size because that is where it appeared: 1UBQ at 1200px
    never showed a single one, and 4HHB at 1890px showed a scatter of them
    through the middle of the frame. It is geometry-dependent, so a small cheap
    fixture is exactly the fixture that cannot see it.
    """
    async with viewer_session("4hhb") as session:
        await _ribbon(session)
        await session.request("brushwork", {"look": "chiaroscuro"})
        plate = await _capture(session, width=1890)

    # Asserted on the pixels rather than by catching `snapshot()`'s refusal:
    # `_incomplete_capture` reads the alpha channel's *minimum*, so a single
    # bad pixel and a whole unrendered half of the frame are the same failure
    # to it, and only this says which.
    empty = float((plate.pixels[:, :, 3] == 0).mean())
    assert empty == 0.0, (
        f"{empty:.6f} of a {plate.size} plate came back transparent, which "
        "makes snapshot() refuse the whole capture"
    )


async def test_a_view_that_does_not_ask_for_paint_does_not_get_it():
    """The painterly pass is canvas-wide, like the ground and unlike a
    representation — so it survives a preset unless the next one says otherwise.

    It did. CI caught `richardson` reporting *"there is no line at all"*: the
    grey outline that test measures was still being drawn, and the brush that
    `painting` had switched on two presets earlier was abstracting it away.
    Exactly the shape of `cinematic`'s depth of field surviving into every view
    that came after it, which is why `_set_effects` states every effect rather
    than the ones being changed — and why it states the paint now too.

    Bit-identical rather than merely similar, because "off" is a different code
    path that has to arrive at the same pixels, and because anything looser
    would pass for a brush that had merely been turned down.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("richardson")
        clean = await _capture(session, width=600)
        await server_mod.preset("painting")
        await server_mod.preset("richardson")
        again = await _capture(session, width=600)

    changed = difference(clean, again)
    assert changed == 0.0, (
        f"richardson drawn after painting differs on {changed:.4f} of the "
        "frame, so the paint outlived the view that asked for it"
    )


async def test_the_looks_are_the_ones_the_viewer_offers():
    """Python gates on `_PAINTERLY_LOOKS` so a refusal can name the choices
    without a round trip, and the viewer gates on its own list. Two hardcoded
    copies of the same set agree until someone edits one of them, and this is
    the only thing that compares them.

    Named in `server.py` beside the tuple, which is where the next person to add
    a look will be standing.
    """
    async with viewer_session(FIXTURE) as session:
        caps = await session.request("capabilities", {})

    assert set(caps["painterly_looks"]) == set(server_mod._PAINTERLY_LOOKS), (
        f"the viewer offers {caps['painterly_looks']} and Python gates on "
        f"{list(server_mod._PAINTERLY_LOOKS)}"
    )
    assert set(caps["brush_sizes"]) == set(server_mod._BRUSH_SIZES), (
        f"the viewer offers {caps['brush_sizes']} and Python gates on "
        f"{list(server_mod._BRUSH_SIZES)}"
    )


async def test_painting_no_longer_reproduces_felt():
    """The observation this whole change came from, turned into a guard.

    Charlie, using the viewer: *"Painting just reproduces felt."* It did — both
    drew `not solvent` as spacefill, their carbons differed by 13 counts of 255
    and their grounds by exactly 8, which `tests/pixels.py` counts as
    identical. protean's own differ could not tell the two views apart.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("felt")
        felt = await _capture(session, width=600)
        await server_mod.preset("painting")
        painting = await _capture(session, width=600)

    # Over the subject, and the reason is the same as everywhere else in this
    # file: both views now sit on a light ground, so almost all of the frame is
    # identical between them and a whole-frame number would be a statement about
    # the paper. What is being asked is whether the two draw the same *picture*.
    apart = _repainted(felt, painting)
    assert apart > 0.6, (
        f"felt and painting differ on {apart:.4f} of what felt drew, which is "
        "the complaint this change exists to answer"
    )


#: divisionist is built to cover the whole picture, not just the subject —
#: §1b's own direction. `chiaroscuro`'s `PAINTED` (0.6) is deliberately loose
#: because it paints only where the subject is; this is a claim about the
#: *ground* changing too, so it is measured on the frame's full 96% rather
#: than the subject's 4%, and held to a real fraction of it rather than
#: chiaroscuro's near-zero.
DIVISIONIST_SUBJECT_COVERAGE = 0.97
DIVISIONIST_GROUND_COVERAGE = 0.85


@pytest.fixture(scope="module")
async def painted_divisionist() -> dict[str, Any]:
    """The same walk `painted` does, for the other live look.

    Its own fixture rather than a parametrized version of `painted`: full
    coverage and a dab lattice are claims `painted`'s tests never make, and
    forcing one fixture to serve both risks the kind of guard that passes
    for either look because it never had to distinguish them.
    """
    frames: dict[str, Any] = {}
    async with viewer_session(FIXTURE) as session:
        await _widen(session)
        await _ribbon(session)

        frames["plain"] = await _capture(session)
        frames["plain_canvas"] = await _canvas(session)

        frames["reply"] = await session.request(
            "brushwork", {"look": "divisionist", "brush_size": "medium"}
        )
        frames["painted"] = await _capture(session)
        frames["painted_canvas"] = await _canvas(session)

        frames["sizes"] = (await session.request("capabilities", {}))["brush_sizes"]
        for size in frames["sizes"]:
            frames[f"{size}_reply"] = await session.request(
                "brushwork", {"brush_size": size}
            )
            frames[size] = await _capture(session)

        frames["off_reply"] = await session.request("brushwork", {"look": "off"})
        frames["off"] = await _capture(session)
        frames["off_canvas"] = await _canvas(session)

        await session.evaluate(
            "JSON.stringify(!!window.__protean.plugin.canvas3d"
            ".setProps({ multiSample: { mode: 'off' } }))"
        )
        frames["single_plain"] = await _canvas(session)
        await session.request("brushwork", {"look": "divisionist"})
        frames["single_painted"] = await _canvas(session)
        await session.request("brushwork", {"look": "off"})
        await session.request("background", {"color": "#123456", "gradient": "off"})
        frames["single_after"] = await _canvas(session)

        # A flat-coloured subject: one solid colour, so any local colour
        # variation left in the picture afterwards is the dab mechanism's
        # own, not the molecule's own shading or its chain colouring.
        await session.request("brushwork", {"look": "off"})
        await session.request("background", {"color": "#4a3b2c", "gradient": "off"})
        await session.request("color", {"name": "fold", "color": "#88bbee"})
        frames["flat_plain"] = await _capture(session)
        await session.request("brushwork", {"look": "divisionist"})
        frames["flat_painted"] = await _capture(session)
    return frames


async def test_divisionist_reaches_the_capture(painted_divisionist):
    """Mirrors `test_the_finish_reaches_the_capture` for the other look —
    `ImagePass` builds its own `DrawPass`, so this arm can pass alone."""
    changed = _repainted(painted_divisionist["plain"], painted_divisionist["painted"])
    assert changed > PAINTED, (
        f"the capture changed on {changed:.4f} of the subject, so divisionist "
        "never reached ImagePass"
    )


async def test_divisionist_reaches_the_canvas(painted_divisionist):
    """Mirrors `test_the_finish_reaches_the_canvas` for the other look."""
    changed = _repainted(
        painted_divisionist["plain_canvas"], painted_divisionist["painted_canvas"]
    )
    assert changed > PAINTED, (
        f"the drawing buffer changed on {changed:.4f} of the subject, so "
        "divisionist is in the file and not on the screen"
    )


async def test_divisionist_is_patched_into_the_viewer_that_is_running(
    painted_divisionist,
):
    assert painted_divisionist["reply"]["reaches_viewer"] is True


async def test_divisionist_survives_multisampling_being_switched_off(painted_divisionist):
    """Mirrors `test_the_finish_survives_multisampling_being_switched_off`."""
    plain = painted_divisionist["single_plain"]
    for name in ("single_plain", "single_painted"):
        lit = float(painted_divisionist[name].pixels[:, :, :3].mean())
        assert lit > 16, f"{name} came back at a mean brightness of {lit:.1f}"

    changed = _repainted(plain, painted_divisionist["single_painted"])
    assert changed > PAINTED, (
        f"with multisampling off the canvas changed on {changed:.4f} of the "
        "subject, so the plain draw route has no dab lattice on it"
    )

    assert close(
        background(painted_divisionist["single_after"]), (0x12, 0x34, 0x56, 255)
    ), (
        f"the canvas reads {background(painted_divisionist['single_after'])} "
        "after the background was changed, so the plain draw route stopped "
        "updating it"
    )


async def test_taking_divisionist_off_gives_back_the_exact_picture(painted_divisionist):
    """Mirrors `test_taking_the_finish_off_gives_back_the_exact_picture`."""
    assert difference(painted_divisionist["plain"], painted_divisionist["off"]) == 0.0
    assert (
        difference(painted_divisionist["plain_canvas"], painted_divisionist["off_canvas"])
        == 0.0
    )
    assert painted_divisionist["off_reply"]["look"] == "off"


async def test_the_dab_size_changes_the_mark_and_not_only_the_number(painted_divisionist):
    """Mirrors `test_the_brush_size_changes_the_mark_and_not_only_the_number`,
    against `dab_px` rather than `stroke_px` — divisionist's own length,
    added for exactly this reason: `stroke_px` is always 0 for a look that
    never runs the continuous stroke."""
    sizes = painted_divisionist["sizes"]
    assert len(sizes) >= 2, sizes
    dab_px = [painted_divisionist[f"{size}_reply"]["dab_px"] for size in sizes]
    assert dab_px == sorted(dab_px), dict(zip(sizes, dab_px, strict=True))
    assert len(set(dab_px)) == len(sizes)

    marked = difference(painted_divisionist[sizes[0]], painted_divisionist[sizes[-1]])
    drawn = coverage(painted_divisionist["plain"])
    assert marked > MARKED_FRACTION_OF_SUBJECT * drawn, (
        f"{sizes[0]} and {sizes[-1]} differ on {marked:.4f} of the frame "
        f"against a subject covering {drawn:.4f}, so the size is reported "
        "and the mark is not drawn"
    )


async def test_a_divisionist_ground_also_refuses_to_be_cropped():
    """The same refusal `test_a_painted_ground_refuses_to_be_cropped` checks
    for chiaroscuro — the check itself is look-agnostic (`if (crop &&
    painting)`), but nothing exercised that for the other look until now."""
    async with viewer_session(FIXTURE) as session:
        await _ribbon(session)
        await session.request("brushwork", {"look": "divisionist"})
        with pytest.raises(ViewerError, match="paints the ground"):
            await session.request("snapshot", {"width": 400, "crop": True}, timeout=240)


async def test_dabs_within_one_flat_region_are_not_all_the_same_rgb(painted_divisionist):
    """§1b's own correctness claim, checked directly: within a flat-coloured
    region the dabs must not all be the same RGB — which for
    `spot-ink-plates` they are by construction, and which a plain render's
    own smooth shading never produces at this frequency either.

    The plain (unpainted) capture of the same flat-coloured molecule is the
    mechanism-removed control: no dab lattice runs there, so whatever local
    colour jump it has is shading and nothing else. `dab_px` from the
    fixture's own reply — not a guessed pixel count — sets the neighbour
    distance, so the measurement is at the mechanism's own scale.
    """
    plain = painted_divisionist["flat_plain"]
    painted_img = painted_divisionist["flat_painted"]
    mask = _subject(plain)

    step = max(3, round(painted_divisionist["reply"]["dab_px"]))
    baseline = _local_jump(plain, mask, step)
    dabbed = _local_jump(painted_img, mask, step)
    assert dabbed > max(baseline * 3, TOLERANCE * 2), (
        f"neighbouring points {step}px apart differ by a median of {dabbed:.1f} "
        f"under divisionist, against {baseline:.1f} on the plain flat render — "
        "the dabs are not varying in colour from one to the next"
    )


async def test_divisionist_covers_the_subject_almost_completely(painted_divisionist):
    """Full coverage was the point, confirmed on Charlie's own account after
    several rounds of bracketing real renders: the picture reads as built
    entirely from points, not as points scattered over a smooth ribbon."""
    changed = _repainted(painted_divisionist["plain"], painted_divisionist["painted"])
    assert changed > DIVISIONIST_SUBJECT_COVERAGE, (
        f"only {changed:.4f} of the subject changed under divisionist, so "
        "the smooth render still shows through — full coverage was the point"
    )


async def test_divisionist_covers_the_ground_too(painted_divisionist):
    """*"the water and the grass in Seurat's own paintings are built from
    points exactly as much as the figures are"* — the ground is 96% of this
    frame, so this is the arm `_repainted` (subject-only, by design) cannot
    supply on its own."""
    plain = painted_divisionist["plain"]
    painted_img = painted_divisionist["painted"]
    ground = ~_subject(plain)
    gap = np.abs(
        plain.pixels[:, :, :3].astype(np.int16)
        - painted_img.pixels[:, :, :3].astype(np.int16)
    )
    changed = float((gap.max(axis=2) > TOLERANCE)[ground].mean())
    assert changed > DIVISIONIST_GROUND_COVERAGE, (
        f"only {changed:.4f} of the ground changed under divisionist, so the "
        "background is not carrying dabs the way the subject is"
    )


async def test_divisionist_paints_the_ground_and_chiaroscuro_does_not(
    painted, painted_divisionist
):
    """The structural difference the two looks were built around, checked as
    a mechanism fact rather than assumed from their names.

    Not "chiaroscuro leaves the ground untouched" — measured, and it does
    not: the canvas weave and the glaze/highlight tone-mapping are not gated
    by `onPaint` the way the bristle and relief are, so chiaroscuro's own
    ground changes on 0.65 of this fixture, not the near-zero its
    `groundPaint: 0` might suggest. What is real is the *margin* — a dab
    lattice with no gap back to the render underneath changes essentially
    all of it (0.99) — and that chiaroscuro's own ground stays meaningfully
    short of complete, which is the second assertion below: without it, a
    future chiaroscuro change that pushed its own ground toward 1.0 could
    still pass the margin check by dragging divisionist up alongside it.
    """

    def _ground_changed(before: Render, after: Render) -> float:
        ground = ~_subject(before)
        gap = np.abs(
            before.pixels[:, :, :3].astype(np.int16)
            - after.pixels[:, :, :3].astype(np.int16)
        )
        return float((gap.max(axis=2) > TOLERANCE)[ground].mean())

    chiaroscuro_ground = _ground_changed(painted["plain"], painted["painted"])
    divisionist_ground = _ground_changed(
        painted_divisionist["plain"], painted_divisionist["painted"]
    )
    assert divisionist_ground > chiaroscuro_ground + 0.2, (
        f"chiaroscuro changed {chiaroscuro_ground:.4f} of the ground and "
        f"divisionist changed {divisionist_ground:.4f} of it — not the gap "
        "between 'paint only the subject' and 'paint everywhere' the two "
        "looks are supposed to be"
    )
    assert chiaroscuro_ground < 0.9, (
        f"chiaroscuro changed {chiaroscuro_ground:.4f} of the ground, which "
        "is close enough to complete that this comparison would stop "
        "meaning anything even if divisionist agreed"
    )
