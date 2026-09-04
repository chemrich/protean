"""The neon presets, measured on the pixels they actually produce.

`neon-backbone` and `neon-cofactors` are both live-viewer looks — a real
Mol\\* emissive material plus its bloom pass, not a raster finish — so the
failure this file exists to catch is the usual one: a `material()` or
`effects()` call that reports success and changes nothing on screen. Each
"does it glow" test turns the preset's own signature feature back off, on the
same geometry, and diffs; a silently-inert call would pass the preset's own
reply while leaving an ordinary tube or an ordinary ball-and-stick behind.

Requires a real browser and is opt-in:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_neon_differential.py
"""

from __future__ import annotations

import pytest

import protean_mcp.server as server_mod
from protean_mcp.connection import ViewerError

from .browser import BROWSER_MARKS, viewer_session
from .pixels import coverage, difference
from .test_render_differential import FIXTURE, _as_server, _shot

pytestmark = BROWSER_MARKS

# Same floor as `test_render_differential.py`'s STYLED, for the same reason: a
# preset that does not change the picture is worse than no preset at all.
NEON_STYLED = 0.008


async def test_neon_backbone_glow_is_visible_in_the_picture():
    """emissive + bloom are the whole point; verify they draw something.

    Turns off exactly what `_neon_backbone_style` turned on — the material's
    emissive value and the bloom pass — on the same tube geometry, then diffs.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("neon-backbone")
        glowing = await _shot(session)
        await server_mod.material(finish="matte", emissive=0.0, name="auto_view")
        await server_mod.effects(bloom=False)
        plain = await _shot(session)

    assert difference(glowing, plain) > NEON_STYLED, (
        "turning off emissive and bloom changed nothing, so neon-backbone's "
        "own signature feature is not drawing"
    )


async def test_neon_backbone_is_thicker_than_the_plain_tube():
    """The 1.4x size factor is new plumbing (`_View.size`) and needs its own guard.

    Two sessions, not one. `show()` rebuilds the representation, and Mol*
    re-fits the camera to whatever the scene holds whenever that happens —
    documented in `_frame_the_scene`'s own docstring as 0.144 of the frame on
    1UBQ between two *identical* draws in the same session. A first attempt at
    this test drew both sizes in one session and passed even with the size
    factor deleted entirely: the camera drift from the second `show()` call
    dwarfed the real effect and the test could not see it. Each size gets its
    own session and its own single, deterministic camera fit instead.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await server_mod.preset("neon-backbone")
        thick = await _shot(session)

    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        # The same style, minus the size factor -- Mol*'s own default putty
        # width, which is what `neon-backbone` would draw without `_View.size`.
        # `hide("auto")` matters: without it the load's own default view stays
        # on screen underneath the tube, which was the actual bug in the
        # first version of this test -- it left both captures reporting
        # identical coverage even with the size factor deleted, because the
        # leftover "auto" scene was masking the real effect in both arms.
        await server_mod.hide(name="auto")
        await server_mod.show(
            selection="polymer",
            name="auto_view",
            representation="putty",
            color=server_mod._NEON_TUBE_COLOR,
        )
        await server_mod.size(size="uniform", name="auto_view")
        await server_mod.background(color="#000000", gradient="off")
        await server_mod.lighting(rig="three-point", ambient=0.3)
        await server_mod.material(
            finish="chrome", emissive=server_mod._NEON_TUBE_EMISSIVE, name="auto_view"
        )
        await server_mod.effects(bloom=True, occlusion=True)
        await server_mod.reset_view()
        default_width = await _shot(session)

    assert coverage(thick) > coverage(default_width) + NEON_STYLED, (
        "neon-backbone's tube does not cover more of the frame than the "
        "default putty width, so its size factor is not reaching Mol*"
    )


async def test_neon_backbones_ligand_gets_the_same_material():
    """A bound cofactor should glow too, not sit lit like a diagram.

    Myoglobin's heme is drawn automatically (`_draw_the_ligands`, since
    `neon-backbone` selects `polymer`). Dimming its material back down in
    isolation should change the picture if the style callback actually
    reached the ligand handle rather than only the tube's own.

    `NEON_STYLED` is calibrated against a whole-molecule effect and is too
    strict here: a heme is a handful of atoms against a 311x722 frame, so its
    full material change moves only 0.0064 of it — measured, not guessed, the
    same way `DRAWN`/`BLANK` were. `LIGAND_STYLED` sits at half that, with
    margin over pixel noise but well under the real effect.
    """
    async with viewer_session("1mbn") as session, _as_server(
        session, load=True, pdb_id="1mbn"
    ):
        await server_mod.preset("neon-backbone")
        glowing = await _shot(session)
        await server_mod.material(
            finish="matte", emissive=0.0, name=server_mod._LIGAND_HANDLE
        )
        heme_dimmed = await _shot(session)

    LIGAND_STYLED = 0.003
    assert difference(glowing, heme_dimmed) > LIGAND_STYLED, (
        "dimming the heme's material changed nothing, so neon-backbone is not "
        "matching the bound cofactor to the tube's material"
    )


async def test_neon_cofactors_glow_is_visible_in_the_picture():
    """The same guard as the tube, for the isolated-cofactor look."""
    async with viewer_session("1hsg") as session, _as_server(
        session, load=True, pdb_id="1hsg"
    ):
        await server_mod.preset("neon-cofactors")
        glowing = await _shot(session)
        await server_mod.material(finish="matte", emissive=0.0, name="auto_view")
        await server_mod.effects(bloom=False)
        plain = await _shot(session)

    assert difference(glowing, plain) > NEON_STYLED, (
        "turning off emissive and bloom changed nothing, so neon-cofactors' "
        "own signature feature is not drawing"
    )


async def test_neon_cofactors_is_refused_on_a_structure_with_nothing_bound():
    """1UBQ has ordered waters and nothing else non-polymer to draw."""
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        before = await _shot(session)
        with pytest.raises(ViewerError, match="Nothing matched"):
            await server_mod.preset("neon-cofactors")
        after = await _shot(session)

    assert difference(before, after) == 0.0, (
        "the refusal changed the picture, so the scene was taken over before "
        "the guard fired"
    )
