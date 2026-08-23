"""Does a data binding actually carry its data? Render it twice and find out.

`docs/bakeoff.md` once concluded that binding a channel does not make it
legible, from three treatments rendered on a structure whose B-factor column is
`0.00` on all 1,216 atoms. Every channel was constant, every picture rendered,
every picture looked right, and the conclusion had to be retracted. **A binding
test on a flat column is vacuous and looks exactly like a passing one.**

The shuffle test is the mechanical guard. Render once with the true channel and
once with the same numbers **permuted across residues** — same multiset, same
palette, same domain, same everything — and diff the two frames. Identical
frames mean the binding is not reading the data.

Two halves, and the second is the one that would have caught the bake-off:

1. The diff. Renders here are bit-deterministic once the ImagePass exists
   (`test_render_differential.py` asserts `difference(...) == 0.0` outright in
   three places), so a dead binding reads exactly **0.0**, not noise.
2. **The degenerate-input guard.** Before shuffling, prove the channel is not
   constant. Permuting identical values is a no-op, so a shuffle test on a flat
   column passes trivially while proving nothing at all.

`test_a_shuffle_that_moves_nothing_reads_zero` is the control that keeps this
file honest: a channel that genuinely cannot be permuted must read 0.0, which
is what demonstrates the other three tests are not passing by construction.

Proved able to fail, which for a test of this shape is the only verification
that counts: with `_shuffled` returning its input unchanged and the identity
guard disabled, all three positive arms failed at exactly `0.0 > 0.008` and the
control still passed.

Requires a real browser and is opt-in:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_shuffle_differential.py
"""

from __future__ import annotations

import random
from typing import Any

import protean_mcp.server as server_mod

from .browser import BROWSER_MARKS, viewer_session
from .pixels import coverage, difference

# Reused rather than restated. STYLED is the measured floor for "this style
# change moved the picture" and DRAWN for "something is on screen"; a shuffle
# that changes the picture less than a lighting rig does is not a finding worth
# a threshold of its own. Importing them keeps one number in one place — and
# nothing named `test_*` is imported, which would make pytest collect that
# module's tests a second time under this one.
#
# What each arm actually measures, at STYLED = 0.008, in a 722x311 viewport
# where a cartoon of 1UBQ's polymer covers 0.033 of the frame:
#
#   colour, ramp on a cartoon   0.0281   3.5x the threshold
#   size, ramp on a putty       0.0105   1.3x  — the thinnest margin here
#   colour, burial on a cartoon 0.0264   3.3x
#   the identity control        0.0000   exactly, as it must be
#
# The size arm looks thin only against the whole frame. A putty tube covers
# 0.014 of this viewport to a cartoon's 0.033, so 0.0105 is **73% of the
# tube's own pixels** — against 86% for the colour ramp and 80% for burial.
# These are also the conservative readings: CI renders into a taller viewport
# where the molecule fills more of the frame, so every figure above grows there.
from .test_render_differential import DRAWN, FIXTURE, STYLED, _as_server, _shot

pytestmark = BROWSER_MARKS

Entry = dict[str, Any]

#: Fixed, so the same permutation runs on every machine on every run. An
#: unseeded shuffle would make this suite fail on some runs and not others, and
#: `viewer/src/dispatch.ts:648-660` records the same lesson from the other
#: direction: a theme that jittered atom radii from an RNG gave the symmetry
#: copies of one atom different sizes, and a different answer on every reload.
SHUFFLE_SEED = 20260822


def _numbers(values: list[Entry], key: str) -> list[float]:
    """The channel itself, in the order the residues were given.

    Read through the server's own `_field_value` so this sees exactly the
    number `define_field` will see, including its refusal of a null.
    """
    return [server_mod._field_value(entry, key) for entry in values]


def _residues(values: list[Entry]) -> list[tuple[str, int, str]]:
    """The residue each entry names, in order — the half a shuffle must not touch."""
    return [
        (str(entry["chain"]), int(entry["seq"]), str(entry.get("ins_code", "")))
        for entry in values
    ]


def _shuffled(values: list[Entry], key: str, seed: int = SHUFFLE_SEED) -> list[Entry]:
    """The same numbers on different residues.

    The residue keys stay where they are and only the numbers move, so the
    marginal distribution, the fitted domain, the palette and the count of
    matched residues are all identical between the two arms. What differs is
    solely *which* residue holds which number — which is the whole of what a
    data binding claims to draw.
    """
    numbers = _numbers(values, key)
    order = list(range(len(numbers)))
    random.Random(seed).shuffle(order)
    return [{**entry, key: numbers[i]} for entry, i in zip(values, order, strict=True)]


def _distinct(values: list[Entry], key: str) -> int:
    """How many different numbers the channel takes."""
    return len(set(_numbers(values, key)))


def _moved(before: list[Entry], after: list[Entry], key: str) -> float:
    """Fraction of residues the permutation actually handed a different number."""
    pairs = zip(_numbers(before, key), _numbers(after, key), strict=True)
    return sum(1 for a, b in pairs if a != b) / len(before)


def _checked_shuffle(values: list[Entry], key: str) -> list[Entry]:
    """A permutation, with the four properties that make it one asserted.

    Every one of these has a way of failing quietly:

    * **Non-constant input.** The bake-off's whole error. Shuffling a flat
      column is a no-op, and the test then passes having compared a picture
      with itself.
    * **Same multiset.** If the numbers changed, a difference in the frame
      could be the new numbers rather than their new positions.
    * **Same residues.** If the keys moved instead, `matched` moves with them
      and the second frame is a differently-covered molecule.
    * **Not the identity.** `define_field` re-registers happily, so an identity
      permutation would render the same picture twice and read 0.0 — and
      `define_elements` next door short-circuits an identical payload to
      `reused: true` without re-registering at all (`dispatch.ts:2545`), which
      is the same trap one action over.
    """
    shuffled = _shuffled(values, key)
    assert _distinct(values, key) > 1, (
        f"{key!r} takes one value on every residue, so shuffling it changes "
        "nothing and this test would pass without testing anything — which is "
        "exactly how the bake-off's B-factor conclusion happened"
    )
    assert sorted(_numbers(shuffled, key)) == sorted(_numbers(values, key)), (
        "the shuffle changed the numbers, not just where they sit"
    )
    assert _residues(shuffled) == _residues(values), (
        "the shuffle moved the residue keys, so the two arms cover different "
        "parts of the molecule"
    )
    assert _moved(values, shuffled, key) > 0.0, (
        "the permutation is the identity, so both arms draw the same field"
    )
    return shuffled


# 1UBQ is one chain, 76 residues; a ramp along it is the same channel
# `test_a_registered_field_paints_and_sizes_what_it_matches` uses, and the
# domain is passed explicitly to both arms so the auto-fit is not a variable.
# (It would fit identically anyway — a permutation preserves min and max — but
# an assumption that holds today is not the same as one the test states.)
RAMP = [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)]
RAMP_DOMAIN = [1.0, 76.0]


async def _paint(session: Any, field: str, values: list[Entry], **kwargs: Any) -> Any:
    """Register a field, colour the `fold` handle with it, and capture.

    Each arm registers under its *own* name rather than re-registering one.
    Re-registering would be closer to "same everything", but `color()` would
    then be handed parameters identical to the ones already applied, and a
    Mol* state update that changes no parameter is entitled to skip the
    rebuild — which would hand this file a 0.0 that means "nothing repainted"
    while reading as "the binding is dead". A name is not drawn; the risk of
    trusting a no-op update is real.
    """
    await server_mod.define_field(field, values, **kwargs)
    await server_mod.color(field, name="fold")
    return await _shot(session)


async def test_a_colour_field_repaints_when_its_values_are_shuffled():
    """The headline claim: `color(field)` on a cartoon is reading the numbers.

    Without this, a colour theme that ignored its lookup and ramped over
    residue index — or over nothing at all — would draw a perfectly plausible
    blue-to-red ribbon and pass every test in the suite. Colour is
    residue-averaged on a cartoon, so a permutation is very visible.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        shuffled = _checked_shuffle(RAMP, "value")

        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="cartoon", handle="fold", color="#ffffff")

        true = await _paint(session, "ramp", RAMP, key="value", domain=RAMP_DOMAIN)
        permuted = await _paint(
            session, "ramp_shuffled", shuffled, key="value", domain=RAMP_DOMAIN
        )

        # Two blank frames also differ by 0.0, so the frames have to contain a
        # molecule before their difference means anything.
        assert coverage(true) > DRAWN, "the cartoon is not on screen"
        assert difference(true, permuted) > STYLED, (
            "shuffling the values across residues changed nothing on screen, "
            "so the colour theme is not reading the field"
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
        shuffled = _checked_shuffle(RAMP, "value")

        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="putty", handle="fold", color="#ffffff")

        await server_mod.define_field(
            "ramp", RAMP, key="value", domain=RAMP_DOMAIN, sizes=[0.2, 1.5]
        )
        await server_mod.size("ramp", name="fold")
        true = await _shot(session)

        await server_mod.define_field(
            "ramp_shuffled", shuffled, key="value", domain=RAMP_DOMAIN, sizes=[0.2, 1.5]
        )
        await server_mod.size("ramp_shuffled", name="fold")
        permuted = await _shot(session)

        # 0.01 rather than DRAWN, which was measured on a whole molecule: a
        # polymer tube is thinner than that and covers 0.0143 of this frame.
        # It is the same floor the putty test in `test_render_differential.py`
        # uses, and for the same reason.
        assert coverage(true) > 0.01, "the putty is not on screen"
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
    non-standard residues, and `_field_value` refuses a null rather than
    guessing at it. The rows are already `_fold_copies`-folded, so no duplicate
    residue key can reach `define_field`.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        measured = await server_mod.sasa()
        rows = [row for row in measured["residues"] if row["relative"] is not None]
        assert len(rows) > 50, f"only {len(rows)} scored residues to shuffle"

        shuffled = _checked_shuffle(rows, "relative")
        # Fitted from the true arm and passed to both, so the two frames differ
        # in nothing but which residue holds which number.
        numbers = _numbers(rows, "relative")
        domain = [min(numbers), max(numbers)]

        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="cartoon", handle="fold", color="#ffffff")

        true = await _paint(session, "burial", rows, key="relative", domain=domain)
        permuted = await _paint(
            session, "burial_shuffled", shuffled, key="relative", domain=domain
        )

        assert coverage(true) > DRAWN, "the cartoon is not on screen"
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
    renders successfully, looks fine, and carries nothing. `_checked_shuffle`
    refuses it, which is why this test permutes by hand.
    """
    async with viewer_session(FIXTURE) as session, _as_server(session, load=True):
        chains = sorted({str(c) for c in server_mod._structure.chain_id.tolist()})
        assert chains == ["A"], f"1UBQ is meant to be the single-chain control: {chains}"
        values = [
            {"chain": "A", "seq": n, "value": float(chains.index("A"))}
            for n in range(1, 77)
        ]
        shuffled = _shuffled(values, "value")

        assert _distinct(values, "value") == 1, "the control channel is not constant"
        assert _moved(values, shuffled, "value") == 0.0, (
            "a constant channel permuted to something other than itself"
        )

        await server_mod.hide(server_mod._WHOLE_SCENE)
        await server_mod.select("polymer", name="fold")
        await server_mod.show(representation="cartoon", handle="fold", color="#ffffff")

        # A domain of [min, max] would be [0, 0], which `define_field` refuses
        # for having no width. Both arms get the same explicit one.
        true = await _paint(session, "chains", values, key="value", domain=[0.0, 1.0])
        permuted = await _paint(
            session, "chains_shuffled", shuffled, key="value", domain=[0.0, 1.0]
        )

        assert coverage(true) > DRAWN, "the cartoon is not on screen"
        assert difference(true, permuted) == 0.0, (
            "an identity permutation moved the picture, so this harness is "
            "measuring something other than the field"
        )
