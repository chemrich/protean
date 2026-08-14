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

from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom as BiotiteAtom
from biotite.structure import array as atom_array
from wiggles_em.occupancy import occupancy_view
from wiggles_em.scene import (
    Arrows,
    ColorByScalar,
    ColorFlat,
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


async def test_colorbyscalar_sends_a_display_copy_and_the_uncertainty_theme(
    structure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: dict[str, Any] = {}

    async def fake_send_structure(array: Any, label: str) -> dict[str, Any]:
        sent["b_factor"] = np.asarray(array.b_factor).copy()
        sent["label"] = label
        return {}

    monkeypatch.setattr(
        "protean_mcp.server._send_structure", fake_send_structure, raising=True
    )
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    atoms = atoms_for(structure, MODEL)
    field = ScalarField.per_atom([(a.key, a.q) for a in atoms])

    await backend.render(Scene([ColorByScalar(Sel.obj("x"), field, domain=(0.0, 1.0))]))

    # The occupancies, on the theme's span, in array order.
    assert [round(float(v), 1) for v in sent["b_factor"]] == [
        100.0,
        60.0,
        40.0,
        100.0,
        100.0,
        95.0,
    ]
    # The analysis copy is untouched: this is the whole difference from PyMOL.
    assert list(np.asarray(structure.b_factor)) == [42.0] * 6
    show = recorder.args_for("show")
    assert len(show) == 1
    assert show[0]["color"] == "uncertainty"
    assert any("display copy" in note for note in backend.notes)


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
    # 0.5 -> 0x7f, not 0x80: `int(v * 255)` truncates, which is exactly what
    # `wiggles_em.backends.pymol._colour_name` does. The two backends must
    # agree on a channel value or one scene draws two different pictures, and
    # matching upstream is what makes that true by construction.
    assert [a["color"] for a in recorder.args_for("color")] == ["#b3b3b3", "#ff007f"]


async def test_an_unknown_colour_name_is_refused_rather_than_guessed(
    structure: Any,
) -> None:
    backend = MolstarBackend(Recorder(), structure, model=MODEL)
    with pytest.raises(Refused, match="no value for"):
        await backend.render(Scene([ColorFlat(Sel.all(), "chartreuse")]))


async def test_hide_everything_hides_the_component(structure: Any) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(Scene([Hide(Sel.all(), Rep.EVERYTHING)]))
    assert recorder.actions() == ["select", "hide"]


async def test_hiding_one_representation_refuses_rather_than_over_hiding(
    structure: Any,
) -> None:
    backend = MolstarBackend(Recorder(), structure, model=MODEL)
    with pytest.raises(Refused, match="more than was asked"):
        await backend.render(Scene([Hide(Sel.all(), Rep.CARTOON)]))


async def test_opacity_and_delete_reach_their_actions(structure: Any) -> None:
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)
    await backend.render(Scene([Opacity(Sel.all(), 0.4), Delete(("gone",))]))
    assert recorder.args_for("opacity")[0]["opacity"] == 0.4
    assert recorder.args_for("remove")[0]["name"] == "gone"


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
        (Isosurface("s", "v", 0.05, Unit.ABSOLUTE), "volume action"),
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


async def test_occupancy_view_renders_through_this_backend(
    structure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first time a view written for PyMOL has been drawn by anything else.

    The view is not adapted, imported specially, or handed a port: it takes the
    atoms this array yields and returns a Scene, and the backend lowers it.
    """
    sent: dict[str, Any] = {}

    async def fake_send_structure(array: Any, label: str) -> dict[str, Any]:
        sent["b_factor"] = np.asarray(array.b_factor).copy()
        return {}

    monkeypatch.setattr(
        "protean_mcp.server._send_structure", fake_send_structure, raising=True
    )
    recorder = Recorder()
    backend = MolstarBackend(recorder, structure, model=MODEL)

    report, scene = occupancy_view(atoms_for(structure, MODEL), "test")
    assert scene.draws
    await backend.render(scene)

    # Occupancy on the fixed (0, 1) domain, not stretched over 0.4-1.0.
    assert [round(float(v), 1) for v in sent["b_factor"]] == [
        100.0,
        60.0,
        40.0,
        100.0,
        100.0,
        95.0,
    ]
    assert "uncertainty" in [a.get("color") for a in recorder.args_for("show")]
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
