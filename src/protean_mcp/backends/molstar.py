"""Lower a :class:`~wiggles_em.scene.Scene` onto Mol\\* through the bridge.

The counterpart to ``wiggles_em.backends.pymol``, and the first time anything
but PyMOL has lowered a Scene. What each backend converts is different, and
that difference is the whole argument for the seam sitting where it does:

* **PyMOL** converts *contour levels*, because it normalises each map on load
  and contours in sigma while the data is in absolute map values.
* **Mol\\*** converts *scalar domains*, because its ``uncertainty`` theme ramps
  over a fixed ``[0, 100]`` while a scene states the domain the quantity is
  actually in — ``(0.0, 1.0)`` for occupancy.

Neither conversion appears in a view. That is the point.

**Per-atom scalars ride the B-factor column**, as they do in PyMOL, but
without the stash: PyMOL has one copy of an object and must save the
crystallographic values before overwriting them, while this backend builds a
*display copy* and sends that. The analysis array is never touched, so there is
nothing to restore and no warning to issue. ``scene.py`` predicted exactly
this — "they differ in destructiveness, not mechanism".

**What this viewer cannot do**, refused rather than approximated:

``SizeByScalar``
    The bridge's ``show`` takes a scalar ``size`` (Mol\\*'s ``sizeFactor``),
    not a per-atom size *theme*. Mol\\* has an ``uncertainty`` size theme; the
    viewer does not expose it, so a putty cannot be drawn honestly. Falling
    back to colour is forbidden by the op's own contract.
``Label``
    The bridge's ``label`` draws structural labels — chain, residue or element
    names — and takes no text. A :class:`~wiggles_em.scene.Label` carries
    literal text plus atom fields to interpolate, which has nowhere to go.
``Isosurface``, ``ColorSurfaceByMap``
    Volumes have no bridge actions yet; they arrive with the ``cryoem-volumes``
    work.
``Frames``, ``Morph``, ``Arrows``, ``Scatter``
    No Mol\\* equivalent, no custom geometry channel, and ``Scatter`` is
    forbidden to every backend by invariant I2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
from biotite.structure import AtomArray
from wiggles_em.atoms import Atom
from wiggles_em.scene import (
    Arrows,
    ColorByScalar,
    ColorFlat,
    ColorSurfaceByMap,
    Colour,
    Delete,
    Frames,
    Granularity,
    Hide,
    Isosurface,
    Label,
    Legend,
    Morph,
    Opacity,
    Refused,
    Rep,
    ScalarField,
    Scatter,
    Scene,
    SceneOp,
    Sel,
    Show,
    SizeByScalar,
)

from ..handles import Indices, to_molscript
from ..selections import SelectionError
from ..selections import parse as _parse_selection
from ..selections_numpy import evaluate as _evaluate

#: Mol\*'s ``uncertainty`` theme ramps over a fixed ``[0, 100]`` and takes no
#: domain from us, so a scene's domain is mapped onto this span.
#:
#: Deliberately a second definition of ``server._B_FACTOR_FULL`` rather than an
#: import. Nothing in ``server`` imports this package *yet*; the tool that
#: renders a view will, and importing ``server`` from here would make that a
#: cycle the moment it does. ``test_molstar_backend.py`` asserts the two agree,
#: which is the same discipline the wiggles/MCPymol divergence audit applies to
#: two copies of a rule that must not drift.
B_FACTOR_FULL = 100.0

#: Scene representations in Mol\*'s vocabulary. ``MESH`` is absent for the same
#: reason it is absent from the PyMOL backend — a mesh is a property of an
#: isosurface, not of a selection — and ``EVERYTHING`` is absent because Mol\*
#: has no representation meaning "all of them"; hiding a component hides it
#: whatever it is drawn as, which is what :class:`Hide` needs and gets.
_REPS: dict[Rep, str] = {
    Rep.CARTOON: "cartoon",
    Rep.STICKS: "ball-and-stick",
    Rep.SPHERES: "spacefill",
    Rep.SURFACE: "molecular-surface",
    Rep.LINES: "line",
}

#: PyMOL colour names the wiggles views emit, as hex.
#:
#: ``Colour`` is ``str | tuple[float, float, float]``, and every string a view
#: emits today is a **PyMOL** colour name — ``grey70``, ``skyblue``. That is a
#: gap in the seam rather than a fact about this viewer: a viewer-neutral value
#: that names one viewer's palette can only be honoured by a second viewer that
#: reimplements the first one's table, which is this dictionary.
#:
#: Values are PyMOL's own RGB definitions scaled to 8-bit. Unknown names are
#: refused rather than guessed — a wrong colour renders as an ordinary picture
#: and is invisible in review.
_COLOUR_NAMES: dict[str, str] = {
    "grey50": "#808080",
    "grey70": "#b3b3b3",
    "skyblue": "#99ccff",
    "salmon": "#ff9999",
    "palegreen": "#a5e6a5",
    "wheat": "#fcd1a5",
    "lightpink": "#ffbfdd",
    "paleyellow": "#ffff80",
    "lightblue": "#bfffff",
    "lightorange": "#ffcc80",
}

#: ``(action, args)`` in, viewer reply out. Exactly ``server._call``'s shape.
Send = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def resi_of(array: AtomArray[Any], index: int) -> str:
    """The residue identifier for one atom, insertion code included.

    One definition, used both by :func:`atoms_for` when it builds the atoms a
    view reads and by this backend when it resolves a selection over the same
    array. Two spellings of "which residue is this" is how a selection comes to
    match a different set from the one the view measured — and residues 100 and
    100A are ordinary in any antibody model, not an exotic case.
    """
    code = str(array.ins_code[index]).strip()
    return f"{int(array.res_id[index])}{code}"


def atoms_for(array: AtomArray[Any], model: str) -> list[Atom]:
    """Read an ``AtomArray`` as the ``Atom`` list a wiggles view expects.

    The views take ``list[Atom]`` and no port — they were extracted from a
    PyMOL-only package but they do not require PyMOL, which is what makes this
    integration possible at all.

    **``rank`` is the array index, and that convention is load-bearing.**
    :attr:`wiggles_em.atoms.Atom.key` is ``(model, str(rank))``, so a scalar
    field a view builds is keyed on it, and
    :meth:`MolstarBackend._index_of` inverts it to get back to a row of this
    array. Defined here, next to its inverse, so the two cannot drift apart —
    a mismatch would not fail, it would colour the wrong atoms.

    ``model`` must be the same string the backend is constructed with, for the
    same reason.
    """
    altlocs = (
        np.asarray(array.get_annotation("altloc_id"))
        if "altloc_id" in array.get_annotation_categories()
        else np.full(array.array_length(), "")
    )
    occupancies = (
        np.asarray(array.occupancy)
        if "occupancy" in array.get_annotation_categories()
        else np.ones(array.array_length())
    )
    return [
        Atom(
            chain=str(array.chain_id[i]),
            resi=resi_of(array, i),
            resn=str(array.res_name[i]),
            name=str(array.atom_name[i]),
            # mmCIF spells "no alternate" as "."; a view reads a blank as
            # "not an alternate conformer" and groups on the rest, so the
            # dot has to become a blank here rather than a group of its own.
            alt="" if str(altlocs[i]).strip() in (".", "?") else str(altlocs[i]).strip(),
            q=float(occupancies[i]),
            b=float(array.b_factor[i]),
            model=model,
            rank=i,
        )
        for i in range(array.array_length())
    ]


class MolstarBackend:
    """Draws a Scene through protean's viewer bridge.

    Args:
        send: The bridge call — ``server._call``, or a recorder in a test.
        array: The analysis array the view's atoms were read from. Selections
            resolve against it and a display copy is built from it; it is never
            modified.
        model: The model name :func:`atoms_for` stamped on those atoms. A
            scalar field keyed on a different one is refused rather than
            silently matching nothing.
        component: Prefix for the viewer-side component names this backend
            creates, so one render's components do not collide with another's.
    """

    def __init__(
        self,
        send: Send,
        array: AtomArray[Any],
        *,
        model: str,
        component: str = "wgf",
    ) -> None:
        self.send = send
        self.array = array
        self.model = model
        self.component = component
        #: Caveats true of this viewer and of no other. The host appends these
        #: to the view's report, exactly as the PyMOL backend's ``notes`` are
        #: appended to it there.
        self.notes: list[str] = []
        #: Component names created this render, in order. Lets a host clean up,
        #: and lets a test see what was built without reading the canvas.
        self.created: list[str] = []
        self._serial = 0

    # -- entry point -------------------------------------------------------

    async def render(self, scene: Scene) -> None:
        """Draw every op in order. Raises on the first one it cannot honour."""
        self.created.clear()
        self._serial = 0
        for op in scene:
            await self.render_op(op)

    async def render_op(self, op: SceneOp) -> None:
        handler = getattr(self, f"_{type(op).__name__.lower()}", None)
        if handler is None:
            raise Refused(f"{type(op).__name__} has no Mol* lowering")
        await handler(op)

    # -- selections --------------------------------------------------------

    def indices(self, sel: Sel) -> Indices:  # noqa: PLR0911 - one branch per Sel kind
        """Resolve a :class:`Sel` to atom indices into :attr:`array`.

        Mol\\* is never handed a selection *string* built here. protean's own
        path — resolve to indices in numpy, emit MolScript over atom ids — is
        reused rather than reimplemented, so a wiggles selection and a protean
        one cannot disagree about the same atoms.
        """
        n = self.array.array_length()
        kind = sel.kind
        if kind == "all":
            return np.arange(n)
        if kind == "obj":
            # protean holds one structure per session, so "the object" is it.
            # The name is not checked against anything: there is nothing else
            # it could name, and refusing on a mismatch would break every view
            # that passes a PyMOL object name it read from a PyMOL session.
            return np.arange(n)
        if kind == "prop":
            return self._prop(str(sel.key), sel.value)
        if kind == "lt":
            return np.flatnonzero(self._numeric(str(sel.key)) < float(sel.value))
        if kind == "residues":
            return self._residues(sel.value)
        if kind == "first":
            inner = self.indices(sel.parts[0])
            return inner[:1]
        if kind == "raw":
            return self._raw(sel)
        if kind == "and":
            parts = [self.indices(p) for p in sel.parts]
            out = parts[0]
            for part in parts[1:]:
                out = np.intersect1d(out, part)
            return out
        if kind == "or":
            parts = [self.indices(p) for p in sel.parts]
            out = parts[0]
            for part in parts[1:]:
                out = np.union1d(out, part)
            return out
        if kind == "not":
            return np.setdiff1d(np.arange(n), self.indices(sel.parts[0]))
        raise Refused(f"unknown selection kind {sel.kind!r}")

    def _prop(self, key: str, value: object) -> Indices:
        wanted = str(value).strip()
        if key == "chain":
            return np.flatnonzero(np.asarray(self.array.chain_id) == wanted)
        if key == "name":
            return np.flatnonzero(np.asarray(self.array.atom_name) == wanted)
        if key == "resn":
            return np.flatnonzero(np.asarray(self.array.res_name) == wanted)
        if key == "resi":
            resi = np.asarray(
                [resi_of(self.array, i) for i in range(self.array.array_length())]
            )
            return np.flatnonzero(resi == wanted)
        if key == "alt":
            if "altloc_id" not in self.array.get_annotation_categories():
                raise Refused(
                    "this structure carries no alternate-conformer annotation, so "
                    "`alt` cannot be selected. Colouring the whole molecule instead "
                    "would claim every atom is the requested conformer."
                )
            letters = np.asarray(self.array.get_annotation("altloc_id"))
            cleaned = np.array([str(x).strip() for x in letters])
            # "." and "?" are mmCIF's spellings of "no alternate", and
            # `atoms_for` reads both as a blank. The same two have to be
            # blank here or `alt ""` matches nothing on a file that spells it
            # with a dot, and the view's own grouping disagrees with the
            # picture drawn from it.
            cleaned = np.where(np.isin(cleaned, [".", "?"]), "", cleaned)
            return np.flatnonzero(cleaned == wanted)
        raise Refused(
            f"no Mol* lowering for the selection property {key!r}. Guessing at "
            f"an annotation would select a plausible wrong set of atoms."
        )

    def _numeric(self, key: str) -> Any:
        if key == "q":
            if "occupancy" not in self.array.get_annotation_categories():
                raise Refused(
                    "this structure carries no occupancy annotation, so `q` "
                    "cannot be compared against."
                )
            return np.asarray(self.array.occupancy)
        if key == "b":
            return np.asarray(self.array.b_factor)
        raise Refused(f"no Mol* lowering for the numeric selection property {key!r}")

    def _residues(self, value: object) -> Indices:
        residues: tuple[Any, ...] = tuple(value or ())  # type: ignore[arg-type]
        if not residues:
            return np.empty(0, dtype=int)
        wanted = {(str(chain), str(resi)) for chain, resi in residues}
        chains = np.asarray(self.array.chain_id)
        present = [
            i
            for i in range(self.array.array_length())
            if (str(chains[i]), resi_of(self.array, i)) in wanted
        ]
        return np.asarray(present, dtype=int)

    def _raw(self, sel: Sel) -> Indices:
        """Lower caller-supplied selection text, in the one dialect we can check.

        protean parses a PyMOL *subset*, which would normally argue for
        refusing: a string that means one thing to PyMOL and another to a
        partial parser selects the wrong atoms silently, which is the failure
        :meth:`Sel.raw` exists to make auditable.

        It is passed through because that parser **raises on everything it does
        not implement** rather than ignoring it — ``_UNSUPPORTED`` is an
        explicit table, and an unknown token is an error. So the bad case is a
        refusal, not a wrong picture. If it ever starts skipping what it cannot
        parse, this must go back to refusing.
        """
        if sel.key != "pymol":
            raise Refused(
                f"selection text is in the {sel.key!r} dialect and this backend "
                f"can only check PyMOL's."
            )
        try:
            return np.flatnonzero(_evaluate(_parse_selection(str(sel.value)), self.array))
        except SelectionError as exc:
            raise Refused(
                f"protean parses a subset of PyMOL selections and cannot parse "
                f"{sel.value!r}: {exc}"
            ) from exc

    async def _component(self, sel: Sel, hint: str) -> tuple[str, int]:
        """Create a named viewer component for ``sel``. Returns name and count.

        Every component-scoped action the bridge exposes — ``color``, ``hide``,
        ``opacity``, ``remove`` — takes a *name*, not an expression, so a Scene
        selection has to become a named component before any of them applies.
        """
        self._serial += 1
        name = f"{self.component}_{hint}_{self._serial}"
        indices = self.indices(sel)
        await self.send(
            "select",
            {"name": name, "expression": to_molscript(self.array, indices), "limit": 0},
        )
        self.created.append(name)
        return name, len(indices)

    # -- scalars -----------------------------------------------------------

    def _index_of(self, key: tuple[str, ...]) -> int:
        """Invert :attr:`wiggles_em.atoms.Atom.key` back to an array row.

        The inverse of the ``rank = array index`` convention :func:`atoms_for`
        establishes. A key stamped with a different model is refused: it would
        match nothing, and a field that matches nothing leaves the B-factor
        column holding whatever it held before — which then renders as an
        ordinary ramp of the *previous* quantity, under this view's legend.
        """
        model, rank = key
        if model != self.model:
            raise Refused(
                f"this scalar field was built against model {model!r} and this "
                f"backend is drawing {self.model!r}. Applying it would colour by "
                f"whatever the B-factor column already held."
            )
        return int(rank)

    def _scaled(self, field: ScalarField, domain: tuple[float, float]) -> Any:
        """The B-factor column a display copy needs, over the theme's span.

        This is the conversion this backend owns. The scene states the domain
        the quantity is in — occupancy's fixed ``(0.0, 1.0)`` — and the
        ``uncertainty`` theme ramps over ``[0, 100]`` regardless, so the
        mapping happens here and in no view.

        Values outside the domain are **clamped**, not rescaled. A domain is a
        claim about the quantity, and stretching the ramp to fit an outlier is
        the "rainbow over a constant" failure the explicit domain exists to
        prevent.
        """
        if field.granularity is not Granularity.ATOM:
            raise Refused(
                f"a per-{field.granularity.value} scalar field has no Mol* "
                f"lowering yet — only per-atom fields ride the B-factor column."
            )
        low, high = domain
        span = high - low
        if span <= 0:
            raise Refused(
                f"scalar domain {domain} has no width, so every atom would take "
                f"the same colour under a legend describing a range."
            )
        column = np.zeros(self.array.array_length(), dtype=float)
        for key, value in zip(field.keys, field.values, strict=True):
            fraction = (float(value) - low) / span
            clipped = float(np.clip(fraction, 0.0, 1.0))
            column[self._index_of(key)] = clipped * B_FACTOR_FULL
        return column

    async def _send_display(self, column: Any, label: str) -> None:
        """Send a display copy carrying ``column`` in its B-factor slot.

        The analysis array keeps its crystallographic values. That is the whole
        difference from the PyMOL backend, which has one copy and must stash.
        """
        display = self.array.copy()
        display.atom_id = np.arange(1, display.array_length() + 1)
        display.b_factor = column
        # Imported lazily, for when `server` imports this module back — the
        # tool that renders a view will, and a module-scope import here would
        # make that a cycle. Reused rather than reimplemented because
        # `_structure_as_mmcif` carries the altloc-column fix-up, and a second
        # copy of that is exactly the divergence this project keeps paying for.
        from ..server import _send_structure  # noqa: PLC0415 - breaks an import cycle

        await _send_structure(display, label)

    async def _colorbyscalar(self, op: ColorByScalar) -> None:
        targets = self.indices(op.sel)
        covered = {self._index_of(key) for key in op.field.keys}
        missing = [int(i) for i in targets if int(i) not in covered]
        if missing:
            raise Refused(
                f"{len(missing)} of {len(targets)} selected atoms carry no value in "
                f"this scalar field (first: index {missing[0]}). They would be drawn "
                f"from whatever the B-factor column held, on this quantity's ramp, "
                f"under this quantity's legend."
            )
        column = self._scaled(op.field, op.domain)
        label = f"{self.component}_scalar"
        await self._send_display(column, label)
        await self.send(
            "show",
            {
                "name": label,
                "expression": to_molscript(self.array, targets),
                "representation": "cartoon",
                "color": "uncertainty",
                "limit": 0,
            },
        )
        self.created.append(label)
        if op.palette != "red_white_blue":
            self.notes.append(
                f"  Palette {op.palette!r} was not applied: Mol*'s uncertainty theme "
                f"carries its own ramp and takes no colour list. The ordering and "
                f"the domain are honoured; the exact hues are the theme's."
            )
        self.notes.append(
            "  Drawn on a display copy, so the analysis structure keeps its "
            "crystallographic B-factors. Nothing needs restoring."
        )

    async def _sizebyscalar(self, op: SizeByScalar) -> None:
        raise Refused(
            "SizeByScalar needs a per-atom size theme and the viewer bridge exposes "
            "only a scalar `size` (Mol*'s sizeFactor), which is one number for the "
            "whole representation. Mol* has an `uncertainty` size theme; until "
            "`show` can select it, a putty cannot be drawn. Colouring instead is "
            "refused deliberately — a view that asked for thickness and got colour "
            "reads as though the quantity were unavailable."
        )

    # -- colour, visibility ------------------------------------------------

    def _hex(self, colour: Colour) -> str:
        if not isinstance(colour, str):
            r, g, b = colour
            return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
        if colour.startswith("#"):
            return colour
        try:
            return _COLOUR_NAMES[colour]
        except KeyError:
            raise Refused(
                f"{colour!r} is a PyMOL colour name this backend has no value for. "
                f"Known names: {', '.join(sorted(_COLOUR_NAMES))}. A scene that "
                f"names one viewer's palette can only be honoured by another that "
                f"reimplements it, so an unknown name is refused rather than "
                f"approximated."
            ) from None

    async def _colorflat(self, op: ColorFlat) -> None:
        name, count = await self._component(op.sel, "flat")
        if not count:
            return
        await self.send("color", {"name": name, "color": self._hex(op.colour)})

    def _rep(self, rep: Rep) -> str:
        try:
            return _REPS[rep]
        except KeyError:
            raise Refused(
                f"Mol* has no {rep.value!r} representation for a selection"
            ) from None

    async def _show(self, op: Show) -> None:
        name, _ = await self._component(op.sel, "show")
        await self.send(
            "show",
            {
                "name": name,
                "expression": to_molscript(self.array, self.indices(op.sel)),
                "representation": self._rep(op.rep),
                "limit": 0,
            },
        )

    async def _hide(self, op: Hide) -> None:
        # Rep.EVERYTHING is the common case — a view clearing a slate before
        # drawing — and it is the one Mol* answers most directly: hiding a
        # component hides it whatever it is drawn as. A *specific*
        # representation cannot be hidden while leaving its siblings, so that
        # is refused rather than over-hiding, which would silently remove more
        # of the picture than was asked for.
        if op.rep is not Rep.EVERYTHING:
            raise Refused(
                f"the bridge hides a whole component, so {op.rep.value!r} cannot be "
                f"hidden while leaving other representations of the same atoms "
                f"drawn. Hiding everything instead would remove more than was asked."
            )
        name, _ = await self._component(op.sel, "hide")
        await self.send("hide", {"name": name})

    async def _opacity(self, op: Opacity) -> None:
        name, count = await self._component(op.sel, "opacity")
        if not count:
            return
        await self.send("opacity", {"name": name, "opacity": float(op.value)})

    async def _delete(self, op: Delete) -> None:
        for name in op.names:
            await self.send("remove", {"name": name})

    async def _legend(self, op: Legend) -> None:
        """Draws nothing. Legends are report text; they sit in the scene so an
        invariant can be checked on the value rather than on a string."""

    # -- refusals ----------------------------------------------------------

    async def _label(self, op: Label) -> None:
        raise Refused(
            "the bridge's `label` draws structural labels — chain, residue or "
            "element names — and takes no text, while this Label carries the "
            f"literal {op.text!r} with atom fields {op.fields or '()'} to "
            "interpolate. There is nowhere to put it. altloc_view(label=False) "
            "renders in full; only its optional labels are unavailable."
        )

    async def _isosurface(self, op: Isosurface) -> None:
        raise Refused(
            f"contouring {op.volume!r} needs a volume action, and the bridge has "
            f"none yet — that work is on the `cryoem-volumes` branch. Mol* takes "
            f"absolute iso-values natively, so no sigma conversion will be needed "
            f"here when it lands."
        )

    async def _colorsurfacebymap(self, op: ColorSurfaceByMap) -> None:
        raise Refused(
            f"colouring a surface by {op.volume!r} needs the volume actions the "
            f"`cryoem-volumes` branch adds."
        )

    async def _frames(self, op: Frames) -> None:
        raise Refused(
            f"a {len(op.names)}-frame timeline needs stepped playback and Mol* has "
            f"no movie timeline here. This is what latent_traverse_view needs, so "
            f"latent traversals are unavailable in this viewer — showing one frame, "
            f"or all of them at once, would misrepresent a traversal as a structure."
        )

    async def _morph(self, op: Morph) -> None:
        raise Refused(
            f"interpolating {op.obj!r} needs a morph action the bridge does not "
            f"expose. The topology judgement in the view stands; only the playback "
            f"is missing."
        )

    async def _arrows(self, op: Arrows) -> None:
        raise Refused(
            f"{len(op.segments)} displacement arrows need a custom-geometry channel "
            f"and the bridge has none — it draws representations of a structure, "
            f"not free shapes."
        )

    async def _scatter(self, op: Scatter) -> None:
        raise Refused(
            "Scatter is defined so invariant I2 can name what it forbids, and no "
            "view emits it. Motion is recoverable from a heterogeneity method and "
            "populations are not, so a latent scatter renders a claim the data does "
            "not support."
        )


__all__ = ["B_FACTOR_FULL", "MolstarBackend", "Send", "atoms_for", "resi_of"]
