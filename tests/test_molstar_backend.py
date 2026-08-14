"""Lowering a wiggles_em Scene onto Mol*, on structures with known answers.

Offline: no browser, no bridge. What a real render puts on the canvas is the
differential suite's job; what this covers is the arithmetic and the refusals,
which is where a wrong picture would come from and where it would be invisible.

The scalar tests assert the **numbers in the B-factor column**, not that a
`load_structure` was sent. A test that only checks the action was issued passes
just as happily when the column holds the previous quantity — which is the
exact failure `ColorByScalar`'s explicit domain exists to prevent.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom as BiotiteAtom
from biotite.structure import array as atom_array
from biotite.structure.io.pdbx import CIFFile, get_structure
from wiggles_em.occupancy import occupancy_view
from wiggles_em.scene import (
    Arrows,
    ColorByScalar,
    ColorFlat,
    ColorSurfaceByMap,
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
    Sel,
    Show,
    SizeByScalar,
    Unit,
)

from protean_mcp.backends.molstar import (
    _COLOUR_NAMES,
    AUTO,
    B_FACTOR_FULL,
    MolstarBackend,
    atoms_for,
    resi_of,
)

MODEL = "test"


class Recorder:
    """Records `(action, args)` instead of sending. Returns an empty reply."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action, args))
        return {}

    def actions(self) -> list[str]:
        return [action for action, _ in self.calls]

    def args_for(self, action: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == action]


def _atom(
    atom_id: int,
    chain: str,
    res_id: int,
    atom_name: str,
    occupancy: float,
    altloc: str = ".",
    ins_code: str = "",
    b_factor: float = 42.0,
) -> BiotiteAtom:
    # `atom_id` is carried because `to_molscript` selects on it — protean's
    # arrays come from `load_structure`, which always has it, so a fixture
    # without one would be a shape this code never meets.
    return BiotiteAtom(
        [float(res_id), 0.0, 0.0],
        atom_id=atom_id,
        chain_id=chain,
        res_id=res_id,
        ins_code=ins_code,
        res_name="ALA",
        atom_name=atom_name,
        element="C",
        hetero=False,
        b_factor=b_factor,
        occupancy=occupancy,
        altloc_id=altloc,
    )


@pytest.fixture
def structure() -> Any:
    """Six atoms: two chains, a partial pair of conformers, an insertion code.

    Small enough to write the expected index sets by hand, which is the point —
    an expected set computed the same way the code computes it proves nothing.
    """
    return atom_array(
        [
            _atom(1, "A", 1, "CA", 1.0),  # 0
            _atom(2, "A", 2, "CA", 0.6, altloc="A"),  # 1
            _atom(3, "A", 2, "CB", 0.4, altloc="B"),  # 2
            _atom(4, "A", 2, "CA", 1.0, ins_code="A"),  # 3  -> resi "2A"
            _atom(5, "B", 1, "CA", 1.0),  # 4
            _atom(6, "B", 7, "CB", 0.95),  # 5
        ]
    )


@pytest.fixture
def backend(structure: Any) -> MolstarBackend:
    return MolstarBackend(Recorder(), structure, model=MODEL)


# -- the convention, and its inverse ---------------------------------------


def test_rank_is_the_array_index_and_the_backend_inverts_it(structure: Any) -> None:
    """`atoms_for` and `_index_of` must agree, or a field colours other atoms."""
    atoms = atoms_for(structure, MODEL)
    backend = MolstarBackend(Recorder(), structure, model=MODEL)
    for i, atom in enumerate(atoms):
        assert atom.rank == i
        assert backend._index_of(atom.key) == i


def test_atoms_carry_the_occupancies_and_identities_the_array_holds(
    structure: Any,
) -> None:
    atoms = atoms_for(structure, MODEL)
    assert [a.q for a in atoms] == [1.0, 0.6, 0.4, 1.0, 1.0, 0.95]
    assert [a.chain for a in atoms] == ["A", "A", "A", "A", "B", "B"]
    # "." is mmCIF for "no alternate" and must read as blank, or a view groups
    # every ordinary atom into a conformer group called ".".
    assert [a.alt for a in atoms] == ["", "A", "B", "", "", ""]
    # The insertion code is part of the residue identity, not decoration.
    assert atoms[3].resi == "2A"
    assert resi_of(structure, 3) == "2A"


def test_a_field_from_another_model_is_refused_not_silently_unmatched(
    backend: MolstarBackend,
) -> None:
    with pytest.raises(Refused, match="other"):
        backend._index_of(("other", "0"))


# -- the conversion this backend owns --------------------------------------


def test_domain_maps_onto_the_themes_fixed_span(backend: MolstarBackend) -> None:
    field = ScalarField.per_atom(
        [((MODEL, "0"), 0.0), ((MODEL, "1"), 0.5), ((MODEL, "2"), 1.0)]
    )
    column = backend._scaled(field, (0.0, 1.0))
    assert column[0] == 0.0
    assert column[1] == 50.0
    assert column[2] == B_FACTOR_FULL


def test_the_stated_domain_is_used_and_not_the_observed_range(
    backend: MolstarBackend,
) -> None:
    """The reason ColorByScalar demands a domain at all.

    Occupancies of 0.95-1.0 over a fixed (0, 1) must stay at the top of the
    ramp. Rescaled to their own range they would span the full spectrum and
    imply variation that is not in the data.
    """
    field = ScalarField.per_atom(
        [((MODEL, "0"), 0.95), ((MODEL, "1"), 0.975), ((MODEL, "2"), 1.0)]
    )
    column = backend._scaled(field, (0.0, 1.0))
    assert [round(float(v), 1) for v in column[:3]] == [95.0, 97.5, 100.0]
    # The negation of the claim: an observed-range mapping would put these at
    # 0, 50 and 100. If that ever passes, the domain is being ignored.
    assert float(column[0]) != 0.0


def test_values_outside_the_domain_clamp_rather_than_stretch_it(
    backend: MolstarBackend,
) -> None:
    field = ScalarField.per_atom([((MODEL, "0"), -0.5), ((MODEL, "1"), 3.0)])
    column = backend._scaled(field, (0.0, 1.0))
    assert float(column[0]) == 0.0
    assert float(column[1]) == B_FACTOR_FULL


def test_a_zero_width_domain_is_refused(backend: MolstarBackend) -> None:
    field = ScalarField.per_atom([((MODEL, "0"), 1.0)])
    with pytest.raises(Refused, match="no width"):
        backend._scaled(field, (1.0, 1.0))


def test_a_per_residue_field_is_refused_rather_than_read_as_atoms(
    backend: MolstarBackend,
) -> None:
    field = ScalarField(
        keys=(("A", "1"),), values=(1.0,), granularity=Granularity.RESIDUE
    )
    with pytest.raises(Refused, match="per-residue"):
        backend._scaled(field, (0.0, 1.0))


def test_b_factor_full_matches_the_servers_own_constant() -> None:
    """Two definitions of one fact; this is what stops them drifting.

    `server` imports this package, so the backend cannot import back. The
    constant is duplicated deliberately and pinned here instead.
    """
    from protean_mcp.server import _B_FACTOR_FULL  # noqa: PLC0415 - see docstring

    assert B_FACTOR_FULL == _B_FACTOR_FULL


# -- selections -------------------------------------------------------------


def test_selection_kinds_resolve_to_the_atoms_written_out_by_hand(
    backend: MolstarBackend,
) -> None:
    idx = backend.indices
    assert list(idx(Sel.all())) == [0, 1, 2, 3, 4, 5]
    assert list(idx(Sel.obj("anything"))) == [0, 1, 2, 3, 4, 5]
    assert list(idx(Sel.prop("chain", "B"))) == [4, 5]
    assert list(idx(Sel.prop("name", "CB"))) == [2, 5]
    assert list(idx(Sel.prop("alt", "A"))) == [1]
    # Blank altloc is every ordinary atom, "." included.
    assert list(idx(Sel.prop("alt", ""))) == [0, 3, 4, 5]
    assert list(idx(Sel.lt("q", 0.999))) == [1, 2, 5]
    assert list(idx(Sel.residues([("A", "2A")]))) == [3]
    assert list(idx(Sel.residues([("A", "2"), ("B", "7")]))) == [1, 2, 5]
    assert list(idx(Sel.first(Sel.prop("chain", "B")))) == [4]
    assert list(idx(Sel.prop("chain", "A") & Sel.lt("q", 0.999))) == [1, 2]
    assert list(idx(Sel.prop("chain", "B") | Sel.prop("alt", "B"))) == [2, 4, 5]
    assert list(idx(~Sel.prop("chain", "A"))) == [4, 5]


def test_an_insertion_code_residue_is_not_confused_with_its_neighbour(
    backend: MolstarBackend,
) -> None:
    """Residue 2 and residue 2A are different residues.

    A backend that compared `res_id` alone would return both for either, which
    renders as an ordinary picture of slightly too many atoms.
    """
    assert list(backend.indices(Sel.residues([("A", "2")]))) == [1, 2]
    assert list(backend.indices(Sel.residues([("A", "2A")]))) == [3]


def test_raw_pymol_text_is_parsed_and_an_unparsable_one_refuses(
    backend: MolstarBackend,
) -> None:
    assert list(backend.indices(Sel.raw("chain B"))) == [4, 5]
    with pytest.raises(Refused, match="subset of PyMOL"):
        backend.indices(Sel.raw("flibble 3"))


def test_a_non_pymol_dialect_is_refused(backend: MolstarBackend) -> None:
    with pytest.raises(Refused, match="dialect"):
        backend.indices(Sel.raw("whatever", dialect="molscript"))


# -- what gets sent ---------------------------------------------------------


def _bfactors_of(mmcif: str) -> list[float]:
    """Read the B-factor column back out of what was actually sent.

    The point of reading the wire format rather than the array: the column has
    to survive serialisation to mean anything, and `_structure_as_mmcif` is
    where a per-atom annotation has been mangled before.
    """
    handle = CIFFile.read(io.StringIO(mmcif))
    # `altloc="all"`, or biotite keeps only the first conformer of each residue
    # and the column comes back a row short — which would read as the backend
    # having dropped an atom rather than the reader having filtered one.
    array = get_structure(handle, model=1, extra_fields=["b_factor"], altloc="all")
    return [round(float(v), 1) for v in array.b_factor]


async def test_colorbyscalar_sends_a_display_copy_and_the_uncertainty_theme(
    structure: Any,
) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    atoms = atoms_for(structure, MODEL)
    field = ScalarField.per_atom([(a.key, a.q) for a in atoms])

    await backend.render(Scene([ColorByScalar(Sel.obj("x"), field, domain=(0.0, 1.0))]))

    # The occupancies, on the theme's span, in array order — read back out of
    # the mmCIF that went over the wire, not off the array we built.
    loaded = recorder.args_for("load_structure")
    assert len(loaded) == 1
    assert _bfactors_of(loaded[0]["data"]) == [100.0, 60.0, 40.0, 100.0, 100.0, 95.0]
    # The analysis copy is untouched: this is the whole difference from PyMOL.
    assert list(np.asarray(structure.b_factor)) == [42.0] * 6
    # Nothing had been drawn, so the theme goes on the viewer's own preset.
    assert recorder.args_for("color") == [{"name": AUTO, "color": "uncertainty"}]
    assert any("display copy" in note for note in backend.notes)


async def test_the_display_copy_goes_through_the_injected_transport(
    structure: Any,
) -> None:
    """Everything this backend sends must go through `send`.

    It briefly reached into `server._send_structure`, which uses the module
    global bridge. A backend handed any other transport then sent its structure
    to a different place from its ops, and in a real session with a viewer
    attached it died on "No viewer connected".
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    field = ScalarField.per_atom([(a.key, a.q) for a in atoms_for(structure, MODEL)])
    await backend.render(Scene([ColorByScalar(Sel.all(), field, domain=(0.0, 1.0))]))
    assert "load_structure" in recorder.actions()


async def test_the_expression_uses_the_ids_the_display_copy_will_carry(
    structure: Any,
) -> None:
    """biotite renumbers `atom_site.id` on write, so the selection must follow.

    An expression built from the analysis array's ids names different atoms in
    the file the viewer parses, and the counts still agree — so nothing looks
    wrong. Here the analysis ids are pushed far away from 1..N; the expression
    that follows the load must use the renumbered ones.
    """
    structure.atom_id = np.arange(1, structure.array_length() + 1) + 10_000
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    field = ScalarField.per_atom([(a.key, a.q) for a in atoms_for(structure, MODEL)])
    await backend.render(
        Scene(
            [
                ColorByScalar(Sel.all(), field, domain=(0.0, 1.0)),
                Show(Sel.prop("chain", "B"), Rep.STICKS),
            ]
        )
    )
    expression = recorder.args_for("show")[0]["expression"]
    # Chain B is rows 4 and 5, which the display copy numbers 5 and 6.
    assert "10005" not in expression and "10006" not in expression
    assert "5" in expression and "6" in expression


async def test_two_scalar_ops_refuse_rather_than_overwrite_one_column(
    structure: Any,
) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    field = ScalarField.per_atom([(a.key, a.q) for a in atoms_for(structure, MODEL)])
    with pytest.raises(Refused, match="one B-factor column"):
        await backend.render(
            Scene(
                [
                    ColorByScalar(Sel.all(), field, domain=(0.0, 1.0)),
                    ColorByScalar(Sel.all(), field, domain=(0.0, 0.5)),
                ]
            )
        )
    assert recorder.calls == []


async def test_colorbyscalar_refuses_when_the_field_misses_selected_atoms(
    structure: Any,
) -> None:
    """The silent failure this guard exists for.

    Atoms with no value keep whatever the B-factor column held and are drawn on
    this quantity's ramp under this quantity's legend — an ordinary-looking
    picture of the wrong thing.
    """
    backend = MolstarBackend(Recorder(), structure, model=MODEL)
    partial = ScalarField.per_atom([((MODEL, "0"), 1.0)])
    with pytest.raises(Refused, match="carry no value"):
        await backend.render(
            Scene([ColorByScalar(Sel.all(), partial, domain=(0.0, 1.0))])
        )


async def test_show_names_a_component_and_a_molstar_representation(
    structure: Any,
) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(Scene([Show(Sel.prop("chain", "B"), Rep.STICKS)]))
    assert recorder.actions() == ["select", "show"]
    assert recorder.args_for("show")[0]["representation"] == "ball-and-stick"


async def test_colorflat_translates_a_pymol_name_and_an_rgb_triple(
    structure: Any,
) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(
        Scene([ColorFlat(Sel.all(), "grey70"), ColorFlat(Sel.all(), (1.0, 0.0, 0.5))])
    )
    # A name and a triple go through one conversion, so a colour resolves the
    # same way whichever it was written as. Two paths once gave 0xb3 for
    # `grey70` and 0xb2 for its triple — one colour with two answers.
    #
    # 0xb4, not the 0xb2 this asserted before: PyMOL's `grey70` is 0.707071
    # (its grey ramp is n/99), which truncates to 180. The old expectation came
    # from this table's own wrong 0.7, so it moved when the table was corrected
    # against a running PyMOL — which is the guard working, not a regression.
    assert [a["color"] for a in recorder.args_for("color")] == ["#b4b4b4", "#ff007f"]


@pytest.mark.skipif(
    os.environ.get("PROTEAN_PYMOL") != "1" or shutil.which("pymol") is None,
    reason="needs a runnable PyMOL; set PROTEAN_PYMOL=1 to run",
)
def test_every_colour_matches_a_running_pymol() -> None:
    """Check the table against PyMOL itself, not against a copy of it.

    **This test replaces one that could not fail.** The previous guard compared
    this table to `wiggles_em.composition`'s, and they agreed — because one was
    transcribed from the other. Both were wrong for four of the ten names:
    `skyblue` was a lighter blue than PyMOL's, `lightblue` was a pale *cyan*,
    and `grey50`/`grey70` ignored that PyMOL's grey ramp is `n/99` inclusive.
    Two copies of a transcription agreeing is evidence of the copy.

    So the reference here is a running PyMOL. It cannot be a CI dependency —
    there is no wheel — which is why this is gated and why the values carry the
    date they were read. Run it when the table changes:

        PROTEAN_PYMOL=1 uv run pytest tests/test_molstar_backend.py -k pymol

    `-k` in the PyMOL invocation matters: it skips pymolrc, so a user's own
    colour redefinitions cannot make this pass or fail.
    """
    script = textwrap.dedent("""
        from pymol import cmd
        import json, sys
        names = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
        out = {n: list(cmd.get_color_tuple(n)) for n in names}
        print("COLOURS " + json.dumps(out))
    """)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dump.py"
        path.write_text(
            script.replace("sys.argv[1]", repr(json.dumps(list(_COLOUR_NAMES))))
        )
        proc = subprocess.run(
            ["pymol", "-ckq", str(path)], capture_output=True, text=True, timeout=120
        )
    line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("COLOURS ")), None
    )
    assert line, f"PyMOL produced no colours: {proc.stdout[-400:]} {proc.stderr[-400:]}"
    theirs = json.loads(line.removeprefix("COLOURS "))

    assert set(theirs) == set(_COLOUR_NAMES), "PyMOL did not know every name"
    for name, value in theirs.items():
        assert _COLOUR_NAMES[name] == pytest.approx(tuple(value), abs=5e-4), (
            f"{name}: this table says {_COLOUR_NAMES[name]}, PyMOL says {tuple(value)}"
        )


async def test_an_unknown_colour_name_is_refused_rather_than_guessed(
    structure: Any,
) -> None:
    backend = MolstarBackend(Recorder(), structure, model=MODEL)
    with pytest.raises(Refused, match="no value for"):
        await backend.render(Scene([ColorFlat(Sel.all(), "chartreuse")]))


async def test_a_colour_op_over_undrawn_atoms_refuses(structure: Any) -> None:
    """Mol* colours representations, so nothing drawn means nothing to colour.

    The viewer accepts this and changes nothing — `color` over a component with
    no representation commits an empty transaction and reports success. Drawing
    a representation here to have something to colour would invent geometry the
    scene never asked for, so it refuses instead.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    with pytest.raises(Refused, match="nothing has drawn"):
        await backend.render(Scene([ColorFlat(Sel.prop("chain", "B"), "grey70")]))


async def test_a_show_then_a_colour_reuse_one_component(structure: Any) -> None:
    """The fix for three silent no-ops, asserted on the names.

    A fresh component per op meant `color` always named something a `Show` had
    never drawn. The browser test is what proves the picture changes; this is
    what pins the mechanism that makes it change.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    target = Sel.prop("chain", "B")
    await backend.render(Scene([Show(target, Rep.STICKS), ColorFlat(target, "grey70")]))
    assert recorder.actions() == ["select", "show", "color"]
    created = recorder.args_for("select")[0]["name"]
    assert recorder.args_for("show")[0]["name"] == created
    assert recorder.args_for("color")[0]["name"] == created


async def test_a_colour_reaches_every_drawing_of_those_atoms(structure: Any) -> None:
    """`show` layers a component *on top of* the preset, it does not replace it.

    So after a Show, two coincident cartoons cover the same atoms and colouring
    only ours leaves the preset's drawn over it. Measured on a real canvas:
    Show + ColorFlat put 0.0000 of the requested colour on screen, and the same
    scene with a Hide in front put 0.0029 there.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(
        Scene([Show(Sel.all(), Rep.CARTOON), ColorFlat(Sel.all(), "grey70")])
    )
    coloured = [a["name"] for a in recorder.args_for("color")]
    assert AUTO in coloured, "the preset was left in its default colours"
    assert recorder.args_for("show")[0]["name"] in coloured


async def test_a_hidden_preset_is_not_coloured_afterwards(structure: Any) -> None:
    """Once hidden, the preset is not a thing to colour.

    Colouring a hidden representation changes nothing and reports success,
    which is the shape of every defect this backend has already had.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(
        Scene(
            [
                Hide(Sel.all(), Rep.EVERYTHING),
                Show(Sel.all(), Rep.CARTOON),
                ColorFlat(Sel.all(), "grey70"),
            ]
        )
    )
    assert AUTO not in [a["name"] for a in recorder.args_for("color")]


async def test_hide_everything_clears_the_preset_and_what_was_drawn(
    structure: Any,
) -> None:
    """Hiding only this backend's components leaves the preset's cartoon up.

    That is what `Hide` is normally emitted to clear, and it is why hiding a
    freshly created component measured no change on the canvas at all.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(
        Scene([Show(Sel.all(), Rep.CARTOON), Hide(Sel.all(), Rep.EVERYTHING)])
    )
    hidden = [a["name"] for a in recorder.args_for("hide")]
    assert AUTO in hidden, "the viewer's own preset was left drawn"
    assert recorder.args_for("show")[0]["name"] in hidden


async def test_hiding_one_representation_refuses_rather_than_over_hiding(
    structure: Any,
) -> None:
    backend = MolstarBackend(Recorder(), structure, model=MODEL)
    with pytest.raises(Refused, match="more than was asked"):
        await backend.render(Scene([Hide(Sel.all(), Rep.CARTOON)]))


async def test_a_partial_hide_refuses_because_the_preset_cannot_be_split(
    structure: Any,
) -> None:
    backend = MolstarBackend(Recorder(), structure, model=MODEL)
    with pytest.raises(Refused, match="in part"):
        await backend.render(Scene([Hide(Sel.prop("chain", "B"), Rep.EVERYTHING)]))


async def test_opacity_needs_something_drawn(structure: Any) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(Scene([Opacity(Sel.all(), 0.4)]))
    assert recorder.args_for("opacity")[0] == {"name": AUTO, "opacity": 0.4}

    with pytest.raises(Refused, match="nothing has drawn"):
        await backend.render(Scene([Opacity(Sel.prop("chain", "B"), 0.4)]))


async def test_delete_removes_what_this_backend_made_and_refuses_the_rest(
    structure: Any,
) -> None:
    """Scene `Delete` names PyMOL objects, which do not exist here.

    `remove` requires a component this session created, so a map or CGO name
    would fail deep in the bridge as `No selection named 'X'` — which reads as
    a bad argument rather than as an op this viewer has no equivalent for.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    with pytest.raises(Refused, match="did not create"):
        await backend.render(Scene([Delete(("wgf_map",))]))

    await backend.render(Scene([Show(Sel.all(), Rep.CARTOON)]))
    made = recorder.args_for("select")[0]["name"]
    backend.created.append(made)
    await backend.render_op(Delete((made,)))
    assert recorder.args_for("remove")[0]["name"] == made


async def test_a_legend_draws_nothing(structure: Any) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(Scene([Legend("text")]))
    assert recorder.calls == []


# -- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        (
            SizeByScalar(
                Sel.all(),
                ScalarField.per_atom([((MODEL, "0"), 1.0)]),
                domain=(0.0, 1.0),
            ),
            "size theme",
        ),
        (Label(Sel.all(), "%.2f", fields=("q",)), "takes no text"),
        (Frames(("a", "b"), (1, 2)), "latent traversals"),
        (Morph("m", "obj"), "morph action"),
        (Arrows(()), "custom-geometry"),
        (Scatter(()), "I2"),
    ],
)
async def test_unhonourable_ops_refuse_and_name_what_is_missing(
    structure: Any, op: Any, expected: str
) -> None:
    """Refused, never skipped, and never approximated.

    Each message has to name the missing capability: a bare "cannot render"
    leaves the caller unable to tell a viewer gap from a bad argument.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    with pytest.raises(Refused, match=expected):
        await backend.render(Scene([op]))
    # Nothing was half-drawn on the way to the refusal.
    assert recorder.calls == []


# -- end to end -------------------------------------------------------------


async def test_occupancy_view_renders_through_this_backend(structure: Any) -> None:
    """The first time a view written for PyMOL has been drawn by anything else.

    The view is not adapted, imported specially, or handed a port: it takes the
    atoms this array yields and returns a Scene, and the backend lowers it.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)

    report, scene = occupancy_view(atoms_for(structure, MODEL), "test")
    assert scene.draws
    await backend.render(scene)

    # Occupancy on the fixed (0, 1) domain, not stretched over 0.4-1.0, read
    # back off the mmCIF that went over the wire.
    loaded = recorder.args_for("load_structure")
    assert _bfactors_of(loaded[0]["data"]) == [100.0, 60.0, 40.0, 100.0, 100.0, 95.0]
    # The whole object, so the theme lands on the viewer's own preset.
    assert recorder.args_for("color") == [{"name": AUTO, "color": "uncertainty"}]
    # The partial atoms get sticks; the view's second op.
    assert "ball-and-stick" in [a["representation"] for a in recorder.args_for("show")]
    assert "SENSE 1" in report


async def test_occupancy_view_on_a_fully_occupied_model_draws_nothing(
    structure: Any,
) -> None:
    """A refusal by the view, not by the backend, and it must stay one."""
    full = structure.copy()
    full.occupancy = np.ones(full.array_length())
    recorder = Recorder()
    backend = MolstarBackend(recorder, full, model=MODEL)
    report, scene = occupancy_view(atoms_for(full, MODEL), "test")
    assert not scene.draws
    await backend.render(scene)
    assert recorder.calls == []
    assert "Nothing to show" in report


# -- contouring ----------------------------------------------------------------


async def test_an_isosurface_is_lowered_with_its_unit_intact(structure: Any) -> None:
    """The op carries a unit; the wire has to carry it too.

    Sending the bare number would leave the viewer to guess, and guessing wrong
    between 0.05 absolute and 0.05 sigma is the EMD-30913 trap this seam was
    moved up a layer to prevent.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)

    await backend.render(Scene([Isosurface("s", "emd", 0.05, Unit.ABSOLUTE)]))

    assert recorder.args_for("isosurface") == [
        {"name": "emd", "level": 0.05, "unit": "absolute", "style": "mesh"}
    ]


async def test_a_sigma_level_stays_sigma_over_the_wire(structure: Any) -> None:
    """Not converted here: protean converts against the sigma it measured.

    A backend converting against the file header — which is what `equivalent`
    exists to route around — is exactly the stale-RMS failure. This backend can
    always reach the honest number, so it passes the unit along and lets the
    server do it.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)

    await backend.render(
        Scene([Isosurface("s", "emd", 3.0, Unit.SIGMA, style=Rep.SURFACE)])
    )

    assert recorder.args_for("isosurface") == [
        {"name": "emd", "level": 3.0, "unit": "sigma", "style": "surface"}
    ]


async def test_a_carve_is_refused_rather_than_drawn_whole(structure: Any) -> None:
    """Drawing the whole surface would answer a different question.

    The point of a carve is that the density around one site is what is being
    judged; a full surface instead is not a degraded version of that, it is a
    different picture that looks fine.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)

    with pytest.raises(Refused, match="carve"):
        await backend.render(
            Scene([Isosurface("s", "emd", 3.0, Unit.SIGMA, carve_radius=2.0)])
        )
    assert recorder.args_for("isosurface") == []


async def test_a_scene_that_cannot_finish_draws_nothing_at_all(structure: Any) -> None:
    """The isosurface must not land before the colouring is refused.

    `local_resolution_view` emits an `Isosurface` and then a
    `ColorSurfaceByMap`. Now that the first one draws, rendering in order would
    put a plain grey density surface on the canvas and *then* raise — and a
    screenshot taken afterwards looks like a local-resolution figure while
    carrying none of the resolution colouring. Refusing the whole scene first
    is the only honest answer.
    """
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)

    with pytest.raises(Refused, match="Nothing was drawn"):
        await backend.render(
            Scene(
                [
                    Isosurface("s", "emd", 3.0, Unit.SIGMA),
                    ColorSurfaceByMap("s", "locres", (2.0, 6.0), ("blue", "red")),
                ]
            )
        )

    assert recorder.args_for("isosurface") == [], (
        f"the surface was drawn before the scene was refused, so the canvas now "
        f"shows a density map posing as a local-resolution figure: {recorder.calls}"
    )
