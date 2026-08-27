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

from typing import Any

import pytest

import protean_mcp.server as server_mod
from protean_mcp.connection import ViewerError

from .browser import BROWSER_MARKS, viewer_session
from .pixels import Render, background, close, coverage, decode, difference
from .test_render_differential import _as_server

pytestmark = BROWSER_MARKS

FIXTURE = "1ubq"

#: A painterly finish repaints every pixel of the frame, so the fraction it
#: changes is most of it. Measured on 1UBQ at a 1200px capture: 0.86. This sits
#: well below that and well above anything a lighting change produces.
PAINTED = 0.4

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
        await _ribbon(session)

        frames["plain"] = await _capture(session)
        frames["plain_canvas"] = await _canvas(session)

        frames["reply"] = await session.request(
            "brushwork", {"look": "chiaroscuro", "brush_size": "medium"}
        )
        frames["painted"] = await _capture(session)
        frames["painted_canvas"] = await _canvas(session)

        for size in ("fine", "broad"):
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

    changed = difference(plain, painted["single_painted"])
    assert changed > PAINTED, (
        f"with multisampling off the canvas changed on {changed:.4f} of the "
        "frame, so the plain draw route has no finish on it"
    )

    # And the route still delivers *new* frames with no look on it. This is the
    # arm the brightness check above cannot supply: the wrapper takes ownership
    # of the only blit to the canvas, so one that returns without blitting
    # leaves Mol*'s retained drawing buffer showing the last good picture.
    assert close(background(painted["single_after"]), (0x12, 0x34, 0x56, 255)), (
        f"the canvas reads {background(painted['single_after'])} after the "
        "background was changed, so the plain draw route stopped updating it"
    )


async def test_the_finish_reaches_the_capture(painted):
    """The file has to carry the look, and this is the arm that can pass alone.

    It is here twice over: `ImagePass` builds its own `DrawPass`, so a pass
    patched onto the canvas's instance paints the screen and leaves every
    `snapshot()` plain — with a success message on it.
    """
    changed = difference(painted["plain"], painted["painted"])
    assert changed > PAINTED, (
        f"the capture changed on {changed:.4f} of the frame, so the finish "
        "never reached ImagePass"
    )


async def test_the_finish_reaches_the_canvas(painted):
    """And the screen has to carry it, which is the whole reason this pass
    exists rather than a sixth entry in `snapshot(finish=...)`."""
    changed = difference(painted["plain_canvas"], painted["painted_canvas"])
    assert changed > PAINTED, (
        f"the drawing buffer changed on {changed:.4f} of the frame, so the "
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
    fine_px = painted["fine_reply"]["brush_px"]
    broad_px = painted["broad_reply"]["brush_px"]
    assert broad_px > fine_px, f"broad resolved to {broad_px}, fine to {fine_px}"
    assert painted["broad_reply"]["stroke_px"] > painted["fine_reply"]["stroke_px"]

    marked = difference(painted["fine"], painted["broad"])
    drawn = coverage(painted["plain"])
    assert marked > MARKED_FRACTION_OF_SUBJECT * drawn, (
        f"fine and broad differ on {marked:.4f} of the frame against a subject "
        f"covering {drawn:.4f}, so the size is reported and the mark is not drawn"
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

    apart = difference(felt, painting)
    assert apart > 0.5, (
        f"felt and painting differ on {apart:.4f} of the frame, which is the "
        "complaint this change exists to answer"
    )
