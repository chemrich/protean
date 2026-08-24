"""Does a data binding actually carry its data? Render it twice and find out.

`docs/bakeoff.md` once concluded that binding a channel does not make it
legible, from three treatments rendered on a structure whose B-factor column is
`0.00` on all 1,216 atoms. Every channel was constant, every picture rendered,
every picture looked right, and the conclusion had to be retracted. **A binding
test on a flat column is vacuous and looks exactly like a passing one.**

The shuffle test is the mechanical guard. Render once with the true channel and
once with the same numbers **permuted across residues** — same multiset, same
residue keys, same palette, same explicit domain — and diff the two frames.
Identical frames mean the binding is not reading the data.

Two halves, and the second is the one that would have caught the bake-off:

1. The diff, here. Renders are bit-deterministic once the ImagePass exists —
   `test_render_differential.py` asserts `difference(...) == 0.0` outright in
   14 places — so a dead binding reads exactly **0.0**, not noise.
2. **The degenerate-input guard**, in `shuffle.py` and exercised by
   `test_shuffle.py` in the fast job, because a refusal nobody watches fire is
   one that can be deleted without turning anything red.

`test_a_shuffle_that_moves_nothing_reads_zero` is the control that keeps this
file honest: a channel that genuinely cannot be permuted must read 0.0, which
is what demonstrates the other three arms are not passing by construction. It
does not cover everything — see its own docstring for what it cannot see.

Four mechanisms, not one. Four arms register a field and read it back through
`color()` or `size()`; the fifth is `color_by_rmsf`, which registers nothing and
writes into the **B-factor column** instead, the sixth is `scaffold`, whose
channel becomes an *absence* — it decides what is covered rather than what
colour something takes — and the seventh is `boil`, whose channel never reaches
the renderer at all: it scales how far each atom is moved before the frame is
drawn — the same column whose flatness
caused the retraction above. Adding it was the point of the extension: a file
that tested only `define_field` was testing everything except the path that
actually went wrong.

Proved able to fail, three times, which for a test of this shape is the only
verification that counts. With the permutation replaced by the identity and the
guard disabled, all four positive arms failed at exactly `0.0 > 0.008` and the
control still passed. With the second arm made to render nothing, the colour
arm failed at `0.0 > 0.02` on `_both_on_screen` — which it would otherwise have
**passed**, because an empty second frame makes `difference` report the whole
molecule.

Requires a real browser and is opt-in:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_shuffle_differential.py
"""

from __future__ import annotations

import pathlib
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
from biotite.structure.io.xtc import XTCFile

# Reused rather than restated. STYLED is the measured floor for "this style
# change moved the picture" and DRAWN for "something is on screen"; a shuffle
# that changes the picture less than a lighting rig does is not a finding worth
# a threshold of its own. Nothing named `test_*` is imported, which would make
# pytest collect that module's tests a second time under this one.
#
# What each arm actually measures, at STYLED = 0.008, in a 722x311 viewport
# where a cartoon of 1UBQ's polymer covers 0.033 of the frame:
#
#   colour, ramp on a cartoon   0.0281   3.5x the threshold
#   size, ramp on a putty       0.0105   1.3x  — the thinnest margin here
#   colour, burial on a cartoon 0.0264   3.3x
#   rmsf, via the B-factor col  0.0301   3.8x  — the only arm that skips
#                                              define_field entirely
#   scaffold, cover placement   0.1129   14x   — the widest, and the only arm
#                                              where the channel becomes an
#                                              absence rather than a colour
#   boil, wobble amplitude      0.0286   3.6x  — same seed in both arms, so the
#                                              direction each atom is pushed is
#                                              identical and only the distance
#                                              differs
#   the identity control        0.0000   exactly, as it must be
#
# The size arm looks thin only against the whole frame. A putty tube covers
# 0.014 of this viewport to a cartoon's 0.033, so 0.0105 is **73% of the
# tube's own pixels** — against 86% for the colour ramp and 80% for burial.
# These four numbers are local; the viewport CI renders into is a different
# one, and the only measurement of these arms there is whether the job is green.
from PIL import Image

import protean_mcp.server as server_mod
from protean_mcp.analysis.hatching import apply_finish
from protean_mcp.analysis.trajectory import rmsf as measured_rmsf
from protean_mcp.fetch import fetch_structure_data
from protean_mcp.selections import parse as parse_selection
from protean_mcp.selections_numpy import evaluate, load_structure

from .browser import BROWSER_MARKS, viewer_session
from .pixels import Render, coverage, decode, difference
from .shuffle import (
    Entry,
    checked_shuffle,
    checked_shuffle_values,
    distinct,
    moved,
    numbers,
    shuffled,
)
from .test_render_differential import DRAWN, FIXTURE, STYLED, _as_server, _shot

pytestmark = BROWSER_MARKS

# 1UBQ is one chain, 76 residues; a ramp along it is the same channel
# `test_a_registered_field_paints_and_sizes_what_it_matches` uses, and the
# domain is passed explicitly to both arms so the auto-fit is not a variable.
# (It would fit identically anyway — a permutation preserves min and max — but
# an assumption that holds today is not the same as one the test states.)
RAMP = [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)]
RAMP_DOMAIN = [1.0, 76.0]

#: Thin things need a lower floor than a whole molecule does. A polymer putty
#: covers 0.0143 of the frame where the cartoon covers 0.033, which is why
#: `test_render_differential.py`'s own putty test uses this number too.
TUBE = 0.01

Apply = Callable[..., Awaitable[dict[str, Any]]]


async def _scene(representation: str) -> None:
    """One representation of the polymer, painted a flat white.

    White and nothing else, so a difference between two arms is the field
    moving rather than a preset, a rig or a second component.
    """
    await server_mod.hide(server_mod._WHOLE_SCENE)
    await server_mod.select("polymer", name="fold")
    await server_mod.show(representation=representation, handle="fold", color="#ffffff")


async def _arm(
    session: Any, field: str, values: list[Entry], apply: Apply, **kwargs: Any
) -> Render:
    """Register a field, apply it through `apply`, and capture.

    `apply` is `color` or `size` — the two registries `define_field` writes
    into (`dispatch.ts:2388` and `:2409`), and the two ways a field becomes a
    picture.

    Each arm registers under its *own* name rather than re-registering one.
    Re-registering would be closer to "same everything", but `color()` would
    then be handed parameters identical to the ones already applied, and a Mol*
    state update that changes no parameter is entitled to skip the rebuild —
    which would hand this file a 0.0 that means "nothing repainted" while
    reading as "the binding is dead". A name is not drawn; the risk of trusting
    a no-op update is real.
    """
    await server_mod.define_field(field, values, **kwargs)
    await apply(field, name="fold")
    return await _shot(session)


def _both_on_screen(true: Render, permuted: Render, floor: float) -> None:
    """Neither frame is empty.

    Checking only the first is this project's canonical silent success with the
    sign flipped. If the shuffled arm renders nothing — a theme that throws
    during colour evaluation, a lost context, a component rebuild that fails —
    then `difference` reports the whole molecule, sails past STYLED, and the
    arm announces that the binding is reading its data on the strength of a
    blank frame.
    """
    assert coverage(true) > floor, "the true arm is not on screen"
    assert coverage(permuted) > floor, "the shuffled arm is not on screen"


async def test_a_colour_field_repaints_when_its_values_are_shuffled():
    """The headline claim: `color(field)` on a cartoon is reading the numbers.

    Without this, a colour theme that ignored its lookup and ramped over
    residue index — or over nothing at all — would draw a perfectly plausible
    blue-to-red ribbon and pass every test in the suite. Colour is
    residue-averaged on a cartoon, so a permutation is very visible.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        permuted_values = checked_shuffle(RAMP, "value")
        await _scene("cartoon")

        true = await _arm(
            session, "ramp", RAMP, server_mod.color, key="value", domain=RAMP_DOMAIN
        )
        permuted = await _arm(
            session,
            "ramp_shuffled",
            permuted_values,
            server_mod.color,
            key="value",
            domain=RAMP_DOMAIN,
        )

        _both_on_screen(true, permuted, DRAWN)
        assert difference(true, permuted) > STYLED, (
            "shuffling the values across residues changed nothing on screen, "
            "so the colour theme is not reading the field"
        )


def _printed(render: Render, finish: str = "spot-ink-plates") -> Render:
    """The same frame, put through the press."""
    inked = apply_finish(Image.fromarray(render.pixels, "RGBA"), finish)
    return Render(pixels=np.asarray(inked))


async def test_the_plates_follow_the_field_they_were_coloured_by():
    """The arm this file's own rule requires of `spot-ink-plates`.

    The finish claims that which plate a region prints on follows the colour
    family it had in the render. `tests/test_hatching.py` proves the finish
    reads colour at all, by taking the colour away and watching two inks and a
    crossing collapse to one ink. That is a property of the Pillow half. This
    is the whole chain: a number per residue becomes a colour theme, the theme
    becomes pixels, and the press sorts those pixels onto plates. Permute the
    numbers across the residues and a different separation has to come out.

    **Retention is the assertion that matters**, and it is the one a finish
    cannot fake. Any finish at all will differ between these two arms, because
    the renders differ — a passthrough would score the raw difference exactly.
    What a *plate print* has to do is carry that difference through to the page
    rather than flattening it, so the check is the finished difference as a
    share of the raw one.

    Measured on 1UBQ spacefill at this viewport: the render carries 0.0903 of
    the frame between the two arms, the press keeps 0.0438 of it, and the ratio
    is **0.485**. The same finish with its colour sorting removed — everything
    onto one plate — keeps 0.0165, a ratio of **0.183**, and fails here. The
    floor at 0.25 sits between them.

    Under that mutation the line above, `printed > STYLED`, **still passes**:
    the two renders genuinely differ, and a press that flattens them still
    prints two different pages. So retention is not a second opinion on that
    assertion. It is the only one here that can tell a separation from a
    screen.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        permuted_values = checked_shuffle(RAMP, "value")
        # Spacefill, not cartoon: the plates sort by colour *family*, and a
        # per-atom representation is what gives a ramp enough distinct hue to
        # separate. A cartoon averages colour along the ribbon.
        await _scene("spacefill")

        true = await _arm(
            session, "ramp", RAMP, server_mod.color, key="value", domain=RAMP_DOMAIN
        )
        permuted = await _arm(
            session,
            "ramp_shuffled",
            permuted_values,
            server_mod.color,
            key="value",
            domain=RAMP_DOMAIN,
        )

        _both_on_screen(true, permuted, DRAWN)
        raw = difference(true, permuted)
        printed = difference(_printed(true), _printed(permuted))

        assert printed > STYLED, (
            "the two separations printed the same page, so the plates are not "
            "reading the colours the field painted"
        )
        assert printed / raw > 0.25, (
            f"the press kept only {printed / raw:.2f} of the difference the "
            f"render carried ({printed:.4f} of {raw:.4f}), which is a finish "
            "flattening its subject rather than separating it"
        )


async def test_a_printed_identity_reads_exactly_zero():
    """The control, and the reason the arm above is not vacuous.

    Two presses of the *same* frame must be bit-identical. If they are not, the
    finish has a source of randomness that is not the picture — and every
    number in this file would then be measuring that instead. A hashed grain is
    repeatable by construction; this is what proves it stayed that way.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        await _scene("spacefill")
        once = await _arm(
            session, "ramp", RAMP, server_mod.color, key="value", domain=RAMP_DOMAIN
        )

        assert difference(_printed(once), _printed(once)) == 0.0, (
            "the same frame printed twice came out different"
        )


async def test_a_size_field_redraws_the_tube_when_its_values_are_shuffled():
    """The more interesting arm, and the one a monotonicity check cannot do.

    A width binding is a one-line monotone function of the channel, so
    "is the visual parameter monotone in the data" verifies a lambda and passes
    by construction whether or not the lambda is ever handed a real number.
    Permuting the channel is the check that cannot be satisfied that way.

    Colour is pinned to white so only the width moves: a difference here is the
    silhouette changing, not the palette.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        permuted_values = checked_shuffle(RAMP, "value")
        await _scene("putty")

        widths = {"key": "value", "domain": RAMP_DOMAIN, "sizes": [0.2, 1.5]}
        true = await _arm(session, "ramp", RAMP, server_mod.size, **widths)
        permuted = await _arm(
            session, "ramp_shuffled", permuted_values, server_mod.size, **widths
        )

        _both_on_screen(true, permuted, TUBE)
        assert difference(true, permuted) > STYLED, (
            "shuffling the values across residues left the tube the same shape, "
            "so the size theme is not reading the field"
        )


async def test_burial_from_sasa_survives_its_own_shuffle():
    """The calibration case, on a real measured channel rather than a ramp.

    `define_field("burial", sasa()["residues"], key="relative")` is the call
    `sasa()`'s own docstring recommends, so it is the one worth proving carries
    data. Burial is spatially autocorrelated with the silhouette — the surface
    is exposed and the core is not — so permuting it scatters colour that was
    organised, and the difference should be large.

    Null `relative` rows are filtered first: it is null for ligands and
    non-standard residues, and both `_field_value` and `checked_shuffle` refuse
    a null rather than guessing at it. The rows are already `_fold_copies`-
    folded, so no duplicate residue key can reach `define_field`.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        measured = await server_mod.sasa()
        rows = [row for row in measured["residues"] if row["relative"] is not None]
        assert len(rows) > 50, f"only {len(rows)} scored residues to shuffle"

        permuted_values = checked_shuffle(rows, "relative")
        # Fitted from the true arm and passed to both, so the two frames differ
        # in nothing but which residue holds which number.
        scores = numbers(rows, "relative")
        burial = {"key": "relative", "domain": [min(scores), max(scores)]}

        await _scene("cartoon")
        true = await _arm(session, "burial", rows, server_mod.color, **burial)
        permuted = await _arm(
            session, "burial_shuffled", permuted_values, server_mod.color, **burial
        )

        _both_on_screen(true, permuted, DRAWN)
        assert difference(true, permuted) > STYLED, (
            "burial and a permutation of burial paint the same picture"
        )


async def test_a_shuffle_that_moves_nothing_reads_zero():
    """The control, and the cheapest guard against a shuffle test that always
    passes. It tests the harness, not a binding.

    Chain identity on 1UBQ is a channel with one value, because 1UBQ has one
    chain. There is no permutation of it but the identity, so both arms draw
    the same field and the difference must be exactly 0.0. If it is not, then
    something other than the field is moving between the two captures and every
    positive result in this file is suspect.

    It is also the shape of the bake-off's failure, held still: a flat channel
    renders successfully, looks fine, and carries nothing. `checked_shuffle`
    refuses it, which is why this test permutes by hand.

    What it does **not** cover, stated so nobody reads more into the 0.0 than
    is there: both arms carry identical numbers, so this shows that two
    identically-valued fields under different names render identically. It does
    not show that a capture taken after a *changed* theme has settled — the
    case the three positive arms rely on — is stable, and there is no control
    at all on the size registry. `docs/ci-and-tests.md` records a live
    suspicion that a capture can read before something has finished settling,
    so that gap is real rather than theoretical.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        chains = sorted({str(c) for c in server_mod._structure.chain_id.tolist()})
        assert chains == ["A"], f"1UBQ is meant to be the single-chain control: {chains}"
        values = [
            {"chain": "A", "seq": n, "value": float(chains.index("A"))}
            for n in range(1, 77)
        ]
        permuted_values = shuffled(values, "value")

        assert distinct(values, "value") == 1, "the control channel is not constant"
        assert moved(values, permuted_values, "value") == 0.0, (
            "a constant channel permuted to something other than itself"
        )

        await _scene("cartoon")
        # A domain of [min, max] would be [0, 0], which `define_field` refuses
        # for having no width. Both arms get the same explicit one.
        flat = {"key": "value", "domain": [0.0, 1.0]}
        true = await _arm(session, "chains", values, server_mod.color, **flat)
        permuted = await _arm(
            session, "chains_shuffled", permuted_values, server_mod.color, **flat
        )

        _both_on_screen(true, permuted, DRAWN)
        assert difference(true, permuted) == 0.0, (
            "an identity permutation moved the picture, so this harness is "
            "measuring something other than the field"
        )


# -- the path that does not go through define_field ---------------------------


async def test_the_rmsf_ramp_lands_on_the_atoms_that_moved(tmp_path, monkeypatch):
    """`color_by_rmsf` reads *which* atom moved, not just how much anything did.

    This is the arm the other four cannot reach. `color_by_rmsf` does not
    register a field: it writes its numbers into the **B-factor column** and
    draws them with Mol*'s `uncertainty` theme (`server.py`, and the reply says
    `reloaded`). That column is the exact mechanism whose flatness caused the
    bake-off retraction, so leaving it unshuffled left this file testing
    everything except the thing that went wrong.

    `test_the_rmsf_ramp_depends_on_the_motion_it_measures` in
    `test_render_differential.py` already compares a rigid run against a hinge.
    That catches a *constant* written into the column. It cannot catch a
    correct distribution landing on the wrong atoms, because both runs there
    have different distributions — and a mis-keyed handoff is precisely the
    bug protean has already shipped twice: `ins_code: None` building `A|76|None`
    against the viewer's `A|76|`, and a biological assembly's symmetry copies
    giving 584 rows for 292 residues. Both put real numbers on wrong atoms and
    both render a plausible picture.

    The real tool runs both times, so what is under test is the shipped path
    rather than a reimplementation of it. Only `_rmsf`'s answer is permuted,
    and the permutation is checked before it is used: a trajectory whose atoms
    all fluctuate alike would make this vacuous, and `checked_shuffle_values`
    refuses that rather than drawing it.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        template = server_mod._require_structure()
        base = np.asarray(template.coord, dtype=np.float32)
        half = base.shape[0] // 2
        # Half pinned, half swinging: a spread with *structure*, so scattering
        # the numbers across the molecule is visible rather than merely
        # different. A uniform spread would shuffle to something similar.
        frames = [base.copy() for _ in range(8)]
        for step, coord in enumerate(frames):
            coord[half:, 1] += step * 0.4

        handle = XTCFile()
        handle.set_coord(np.stack(frames))
        path = tmp_path / "hinge.xtc"
        handle.write(str(path))
        await server_mod.load_trajectory(str(path))

        await server_mod.color_by_rmsf()
        true = await _shot(session)

        # Imported from its own module rather than read off `server_mod`: it is
        # the same function object (`server.py` does `from .analysis.trajectory
        # import rmsf as _rmsf`), and mypy will not let a re-export be reached
        # through the importing module. The patch below still targets
        # `server_mod`, because that namespace is what `color_by_rmsf` calls.
        def permuting(stack: Any) -> Any:
            return np.asarray(
                checked_shuffle_values([float(v) for v in measured_rmsf(stack)]),
                dtype=float,
            )

        monkeypatch.setattr(server_mod, "_rmsf", permuting)
        await server_mod.color_by_rmsf()
        permuted = await _shot(session)

    _both_on_screen(true, permuted, DRAWN)
    measured = difference(true, permuted)
    assert measured > STYLED, (
        f"the rmsf ramp drew the same picture from the same numbers on "
        f"different atoms: {measured:.6f} against a threshold of {STYLED}. The "
        "B-factor column reached the viewer either way, so what this says is "
        "that the ramp is not reading which atom it is on."
    )


# -- the treatment that draws by leaving things out ---------------------------


async def test_scaffold_covers_the_residues_whose_numbers_are_low():
    """`scaffold` covers *which* residues are unconfident, not merely how many.

    The other arms here shuffle a channel that becomes a colour or a width. This
    one shuffles a channel that becomes an **absence**: `scaffold` hides the
    regions below pLDDT 70, so the question a permutation asks is whether the
    holes are in the right places.

    Both arms have exactly the same number of residues below the line, so the
    same amount of the molecule is covered and the same count is reported. Only
    the identity of the covered residues differs. A cover keyed on the count,
    or on a fixed region, or on anything other than the per-residue number,
    draws these two identically.

    This is the arm `docs/soft-matter-status.md` requires before a treatment may
    claim it shows data, and it is stricter than the three-arm test in
    `test_render_differential.py`: that one varies how much is below the line,
    which a cover reading only "how many" would still pass.
    """
    fetched = await fetch_structure_data(FIXTURE)
    deposited = load_structure(fetched.data, fetched.format, "asymmetric").array

    polymer = evaluate(parse_selection("polymer"), deposited)
    ids = [int(r) for r in np.unique(deposited.res_id[polymer])]

    # A ramp along the chain, every residue a different number. Two values would
    # be enough to decide what gets covered, but a two-valued channel cannot be
    # permuted by more than half — `checked_shuffle_values` refuses it at the
    # `MOVED` floor, correctly, because at that point the two arms are too close
    # to separate. Distinct numbers make the permutation a real relocation while
    # leaving the *threshold* as the only thing that decides the cover.
    per_residue = [float(v) for v in np.linspace(30.0, 99.0, len(ids))]
    true_values = np.full(deposited.array_length(), 95.0)
    for residue, value in zip(ids, per_residue, strict=True):
        true_values[polymer & (deposited.res_id == residue)] = value

    # Permuted per residue, not per atom: the treatment thresholds a residue's
    # confidence, so scattering atoms within a residue would leave every residue
    # holding the same mixture and change nothing about what gets covered.
    permuted_by_residue = dict(zip(ids, checked_shuffle_values(per_residue), strict=True))
    shuffled_values = true_values.copy()
    for residue, value in permuted_by_residue.items():
        shuffled_values[polymer & (deposited.res_id == residue)] = value

    below = sum(1 for v in per_residue if v < server_mod._CONFIDENT)
    after = sum(1 for v in permuted_by_residue.values() if v < server_mod._CONFIDENT)
    assert below == after, (
        "the permutation changed how much is covered, so a difference between "
        "the two arms would not be about *which* residues were covered"
    )

    frames: dict[str, Render] = {}
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        for name, values in (("true", true_values), ("shuffled", shuffled_values)):
            array = deposited.copy()
            array.b_factor = values
            await server_mod._send_structure(array, FIXTURE)
            server_mod._structure = array
            server_mod._b_factor_column = server_mod._BFactorColumn(confidence="pLDDT")
            await server_mod.preset("scaffold")
            frames[name] = await _shot(session)

    _both_on_screen(frames["true"], frames["shuffled"], DRAWN)
    measured = difference(frames["true"], frames["shuffled"])
    assert measured > STYLED, (
        f"scaffold covered the same pixels with the low numbers on different "
        f"residues: {measured:.6f}. The cover is counting, not reading."
    )


async def test_the_boil_wobbles_the_atoms_the_data_is_unsure_of(tmp_path):
    """`boil` moves the least certain atoms furthest — which ones, not how many.

    The cleanest arm in this file, because the seed removes the only other
    variable. Both boils run at the same seed, so the random *direction* each
    atom is pushed is identical between them and the only thing that differs is
    how far each one may go. Any difference in the picture is the channel and
    nothing else.

    Without this, `boil` could scale every atom by the column's mean — a wobble
    that responds to the numbers being present rather than to what they say —
    and every other check on it would still pass: the poses would hold, the
    poses would differ, and the note would still name the B-factor.
    """
    fetched = await fetch_structure_data(FIXTURE)
    deposited = load_structure(fetched.data, fetched.format, "asymmetric").array

    polymer = evaluate(parse_selection("polymer"), deposited)
    ids = [int(r) for r in np.unique(deposited.res_id[polymer])]
    per_residue = [float(v) for v in np.linspace(2.0, 47.0, len(ids))]
    permuted = dict(zip(ids, checked_shuffle_values(per_residue), strict=True))

    def column(by_residue: dict[int, float]) -> Any:
        out = np.zeros(deposited.array_length(), dtype=float)
        for residue, value in by_residue.items():
            out[deposited.res_id == residue] = value
        return out

    arms = {
        "true": column(dict(zip(ids, per_residue, strict=True))),
        "shuffled": column(permuted),
    }

    frames: dict[str, Render] = {}
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        for name, values in arms.items():
            array = deposited.copy()
            array.b_factor = values
            await server_mod._send_structure(array, FIXTURE)
            server_mod._structure = array
            await server_mod.preset("publication-cartoon")
            result = await server_mod.boil(
                str(tmp_path / name), frames=2, width=400, seed=7
            )
            written = sorted(pathlib.Path(result["directory"]).glob("frame_*.png"))
            frames[name] = decode(written[0].read_bytes())

    _both_on_screen(frames["true"], frames["shuffled"], DRAWN)
    measured = difference(frames["true"], frames["shuffled"])
    assert measured > STYLED, (
        f"the same seed wobbled the same atoms the same distance with the "
        f"numbers on different residues: {measured:.6f}. The amplitude is not "
        "reading the column."
    )
