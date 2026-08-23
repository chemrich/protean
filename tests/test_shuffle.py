"""The shuffle helper, including the refusals that make it worth having.

`test_shuffle_differential.py` proves that shuffling a channel changes the
picture. This proves the shuffle is a shuffle — and, more importantly, that
`checked_shuffle` actually refuses the inputs it claims to refuse. Those
refusals are the half of the shuffle test that would have caught the bake-off,
and every one of them is a `raise` nobody would otherwise ever see happen: the
differential arms only ever hand it channels already known to be good, so an
inverted comparison or a deleted assertion would leave the whole suite green.

No browser and no network, so this runs in the fast job.
"""

from __future__ import annotations

import pytest

from .shuffle import (
    MOVED,
    SEED,
    checked_shuffle,
    distinct,
    moved,
    numbers,
    residues,
    shuffled,
)

RAMP = [{"chain": "A", "seq": n, "value": float(n)} for n in range(1, 77)]


def test_a_shuffle_keeps_every_number_and_moves_almost_all_of_them():
    """The two halves of "same marginal distribution, different residues".

    If the numbers changed, a difference in the rendered frame could be the new
    numbers rather than their new positions, and the arms would prove nothing
    about the binding.
    """
    out = checked_shuffle(RAMP, "value")

    assert sorted(numbers(out, "value")) == sorted(numbers(RAMP, "value"))
    assert residues(out) == residues(RAMP)
    # Measured at this seed: 73 of 76 residues change hands.
    assert moved(RAMP, out, "value") > 0.9


def test_the_same_seed_shuffles_the_same_way_every_time():
    """An unseeded permutation makes a test that fails on some runs and not
    others, which is the most expensive kind of failure this repo has."""
    assert shuffled(RAMP, "value") == shuffled(RAMP, "value")
    assert shuffled(RAMP, "value", seed=SEED) == shuffled(RAMP, "value")
    assert shuffled(RAMP, "value", seed=SEED + 1) != shuffled(RAMP, "value")


def test_the_shuffle_does_not_touch_the_original():
    """The differential arms register the true channel *after* building the
    shuffled one, so a shuffle that mutated its input in place would draw the
    permuted numbers in both arms and read 0.0 — a working binding reported as
    dead."""
    before = [dict(entry) for entry in RAMP]
    checked_shuffle(RAMP, "value")
    assert before == RAMP


def test_an_insertion_code_travels_with_its_residue():
    """`define_field` keys on chain, sequence number *and* insertion code, and
    residues 100 and 100A are different residues. A shuffle that dropped the
    code would collapse them into one key and be refused as a duplicate."""
    values = [
        {"chain": "A", "seq": 100, "value": 1.0},
        {"chain": "A", "seq": 100, "ins_code": "A", "value": 2.0},
    ]
    out = shuffled(values, "value")
    assert residues(out) == [("A", 100, ""), ("A", 100, "A")]


def test_a_flat_channel_is_refused_before_anything_is_drawn():
    """The load-bearing refusal. A shuffle test on a column with one distinct
    value permutes nothing, renders the same picture twice, and passes — which
    is exactly what happened to the bake-off, whose three treatments all bound
    a B-factor column that is 0.00 on every atom of 1MBN."""
    flat = [{"chain": "A", "seq": n, "value": 0.0} for n in range(1, 77)]
    assert distinct(flat, "value") == 1
    with pytest.raises(AssertionError, match="one value on every residue"):
        checked_shuffle(flat, "value")


def test_a_null_in_the_channel_is_refused_with_a_count_and_a_remedy():
    """`sasa()` reports a null `relative` for a ligand, a nucleotide or an ion.
    Without this the first thing to touch the value is `_field_value`, which
    raises a ViewerError attributed to a shuffle helper — a confusing error for
    a caller who simply forgot to filter."""
    values = [
        {"chain": "A", "seq": 1, "relative": 0.5},
        {"chain": "A", "seq": 2, "relative": None},
    ]
    with pytest.raises(AssertionError, match="1 of 2 entries have a null"):
        checked_shuffle(values, "relative")


def test_a_permutation_that_barely_moves_is_refused():
    """`> 0` only rejects the exact identity. A single transposition satisfies
    it while leaving both frames all but identical, and the arm then fails at
    something like 0.0004 > 0.008 and reports a working binding as dead."""
    # A seed is not tunable to produce this on demand, so the near-identity is
    # built directly: `shuffled` is bypassed and the floor is checked on what
    # it would have to accept.
    swapped = [dict(entry) for entry in RAMP]
    swapped[0], swapped[1] = (
        {**swapped[0], "value": swapped[1]["value"]},
        {**swapped[1], "value": swapped[0]["value"]},
    )
    assert moved(RAMP, swapped, "value") == pytest.approx(2 / 76)
    assert moved(RAMP, swapped, "value") < MOVED
    assert moved(RAMP, [dict(e) for e in RAMP], "value") == 0.0
