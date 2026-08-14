"""The Mol* backend against a real browser, measured in pixels.

`test_molstar_backend.py` proves the arithmetic — the domain mapping, the key
inversion, the index sets. It cannot prove that anything was *drawn*, because
it records calls instead of sending them, and a bridge action can be recorded
perfectly while the viewer discards it.

That gap is not hypothetical. Three of this backend's drawing ops shipped as
silent no-ops and passed the recorded-call tests: `color` on a component with
no representation updates nothing and reports success, `hide` toggles a
component that was never drawn, and `opacity` refuses outright. Every one of
those asserted `actions() == [...]` and was green.

So the claims here are the ones only a canvas can settle:

    Hide       the frame goes blank
    ColorFlat  the requested colour appears in the pixels
    Show       something is drawn at all

Each is a *pair* of measurements — before and after — for the reason
`test_render_differential.py` gives: a coverage number on its own is
unfalsifiable, and the difference between two shots of one session is what
demonstrates the harness is reading the scene rather than a constant.

Opt-in, needs a browser and the network:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_molstar_backend_differential.py
"""

from __future__ import annotations

from typing import Any

import numpy as np
from wiggles_em.scene import (
    ColorByScalar,
    ColorFlat,
    Hide,
    Rep,
    ScalarField,
    Scene,
    Sel,
    Show,
)

from protean_mcp.backends.molstar import MolstarBackend, atoms_for
from protean_mcp.fetch import fetch_structure_data
from protean_mcp.selections_numpy import load_structure

from .browser import BROWSER_MARKS, viewer_session
from .pixels import Render, color_fraction, coverage, decode

pytestmark = BROWSER_MARKS

FIXTURE = "1ubq"

# The same thresholds test_render_differential.py measured for this fixture and
# viewport: 1UBQ covers ~0.07 of the frame drawn and 0.0000 hidden.
DRAWN = 0.02
BLANK = 0.002

# Absent from every Mol* default palette, so any pixel matching it was put
# there by the op under test rather than by the preset. Magenta is what
# test_render_differential.py uses for the outline check, for the same reason.
MAGENTA: tuple[int, int, int, int] = (255, 0, 255, 255)


async def _shot(session: Any) -> Render:
    result = await session.request("screenshot", {})
    return decode(result["data_uri"])


async def _array(assembly: str = "asymmetric") -> Any:
    """The analysis array for the same file the session loaded."""
    structure = await fetch_structure_data(FIXTURE)
    return load_structure(structure.data, structure.format, assembly).array


def _backend(session: Any, array: Any) -> MolstarBackend:
    return MolstarBackend(session.request, array, model=FIXTURE)


async def test_hide_everything_blanks_the_frame() -> None:
    """`Hide` must remove what is on screen, not toggle an empty component.

    This is the test that fails on the first version of this backend: it minted
    a fresh component per op, so `hide` named something with no representation
    and the preset's own cartoon — registered under the reserved `auto`
    handle — stayed drawn. `assert actions() == ["select", "hide"]` passed
    throughout.
    """
    async with viewer_session(FIXTURE) as session:
        array = await _array()
        backend = _backend(session, array)

        before = await _shot(session)
        assert coverage(before) > DRAWN, "nothing was drawn to begin with"

        await backend.render(Scene([Hide(Sel.all(), Rep.EVERYTHING)]))
        after = await _shot(session)

        assert coverage(after) < BLANK, (
            f"Hide(EVERYTHING) left {coverage(after):.4f} of the frame drawn "
            f"(was {coverage(before):.4f}). The op reported success."
        )


async def test_colorflat_puts_its_colour_on_the_canvas() -> None:
    """`ColorFlat` must change pixels, not update zero representations.

    Mol*'s `color` action applies a theme to a component's representations. A
    component created by `select` alone has none, so the update commits an
    empty transaction and returns `{name, color, components: 1}` — a success
    reply for a picture that did not change.
    """
    async with viewer_session(FIXTURE) as session:
        array = await _array()
        backend = _backend(session, array)

        await backend.render(Scene([Show(Sel.all(), Rep.CARTOON)]))
        before = await _shot(session)
        assert color_fraction(before, MAGENTA) == 0.0, "magenta was already present"

        await backend.render(
            Scene([Show(Sel.all(), Rep.CARTOON), ColorFlat(Sel.all(), (1.0, 0.0, 1.0))])
        )
        after = await _shot(session)

        assert color_fraction(after, MAGENTA) > 0.0, (
            "ColorFlat drew none of its colour; the op reported success over "
            "zero representations."
        )


async def test_colorbyscalar_draws_and_keeps_what_was_drawn_before_it() -> None:
    """The display copy must not erase the rest of the scene.

    `load_structure` calls `components.clear()` and `plugin.clear()`, so a
    display copy sent part-way through a render wipes every op that preceded
    it. A scene that hides, shows and then colours by a scalar must end up
    showing all three, not only the last.

    The field is built by hand rather than by a view: it makes the expected
    picture independent of whether this PDB entry happens to carry partial
    occupancy, which is a fact about the deposition and not about the backend.
    """
    async with viewer_session(FIXTURE) as session:
        array = await _array()
        backend = _backend(session, array)
        atoms = atoms_for(array, FIXTURE)
        # A ramp along the chain: every atom carries a value, so the field
        # covers whatever the selection resolves to.
        field = ScalarField.per_atom(
            [(a.key, i / max(len(atoms) - 1, 1)) for i, a in enumerate(atoms)]
        )

        await backend.render(
            Scene(
                [
                    Show(Sel.all(), Rep.CARTOON),
                    ColorFlat(Sel.all(), (1.0, 0.0, 1.0)),
                    ColorByScalar(Sel.all(), field, domain=(0.0, 1.0)),
                ]
            )
        )
        after = await _shot(session)

        assert coverage(after) > DRAWN, (
            f"the scalar render drew {coverage(after):.4f} of the frame — the "
            f"display copy replaced the scene and nothing was redrawn"
        )
        # The scalar ramp must win over the flat colour: it comes last, and
        # later ops draw over earlier ones. If magenta survives, the ramp was
        # applied to a component the flat colour is still covering.
        assert color_fraction(after, MAGENTA) < 0.01, (
            "the flat colour is still on screen after a later ColorByScalar"
        )


async def test_the_expression_names_the_atoms_the_display_copy_actually_has() -> None:
    """A display copy is renumbered on the way out; the selection must follow.

    biotite's writer numbers `atom_site.id` from 1 and discards what the array
    carried, so an expression built from the *analysis* array's ids names
    different atoms in the file the viewer parses — silently, because the counts
    still agree. `server._renumber_for_viewer` exists for exactly this.

    Measured rather than asserted on the call: a scalar render that named the
    wrong atoms would still draw, so only the picture can tell.
    """
    async with viewer_session(FIXTURE) as session:
        array = await _array()
        # Renumber the analysis copy away from what the writer will emit. If
        # the backend builds its expression from these ids, it names atoms the
        # display copy does not have and colours nothing.
        array.atom_id = np.arange(1, array.array_length() + 1) + 10_000
        backend = _backend(session, array)
        atoms = atoms_for(array, FIXTURE)
        field = ScalarField.per_atom([(a.key, 1.0) for a in atoms])

        await backend.render(Scene([ColorByScalar(Sel.all(), field, domain=(0.0, 1.0))]))
        after = await _shot(session)

        assert coverage(after) > DRAWN, (
            f"the scalar render drew {coverage(after):.4f} of the frame: the "
            f"expression named the analysis array's ids, which the display "
            f"copy does not carry"
        )
