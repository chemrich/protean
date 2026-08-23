"""Permuting a channel across residues, and proving the permutation is one.

Split out from the test that uses it for the reason `pixels.py` is split out
from `test_render_differential.py`: the browser suite is opt-in behind
`PROTEAN_DIFFERENTIAL=1`, so anything defined inside it never executes in the
fast job. That matters here more than usual, because the load-bearing half of
the shuffle test is a **refusal** — `checked_shuffle` rejecting a channel that
is constant — and a refusal nobody ever watches fire is a refusal that can be
deleted without turning anything red. `test_shuffle.py` watches it fire, with
no browser and no network.

The values these take are the list `define_field` takes: a list of dicts, each
carrying "chain", "seq", optionally "ins_code", and the number.
"""

from __future__ import annotations

import random
from typing import Any

import protean_mcp.server as server_mod

Entry = dict[str, Any]

#: Fixed, so the same permutation runs on every machine on every run. An
#: unseeded shuffle would make the differential arms fail on some runs and not
#: others, and `viewer/src/dispatch.ts:649-667` records the same lesson from
#: the other direction: a theme that jittered atom radii from an RNG gave the
#: symmetry copies of one atom different sizes, and a different answer on
#: every reload.
SEED = 20260822

#: How much of the channel a permutation has to actually move. Not `> 0`,
#: which only rejects the exact identity: a single transposition of 76
#: residues would satisfy that while leaving the two frames all but identical,
#: and the arm would then fail at something like 0.0004 > 0.008 and report a
#: working binding as dead — with the guard that exists to catch a bad setup
#: having passed it. Measured at SEED on 1UBQ's 76 residues: 0.961 of the
#: channel moves, 3 residues keep their own number. The floor sits well below
#: that and well above a single transposition's 0.026.
MOVED = 0.5


def numbers(values: list[Entry], key: str) -> list[float]:
    """The channel itself, in the order the residues were given.

    Read through the server's own `_field_value` so this sees exactly the
    number `define_field` will see.
    """
    return [server_mod._field_value(entry, key) for entry in values]


def residues(values: list[Entry]) -> list[tuple[str, int, str]]:
    """The residue each entry names, in order — the half a shuffle must not touch."""
    return [
        (str(entry["chain"]), int(entry["seq"]), str(entry.get("ins_code", "")))
        for entry in values
    ]


def shuffled(values: list[Entry], key: str, seed: int = SEED) -> list[Entry]:
    """The same numbers on different residues.

    The residue keys stay where they are and only the numbers move, so the
    marginal distribution, the fitted domain, the palette and the count of
    matched residues are all identical between the two arms. What differs is
    solely *which* residue holds which number — which is the whole of what a
    data binding claims to draw.
    """
    original = numbers(values, key)
    order = list(range(len(original)))
    random.Random(seed).shuffle(order)
    return [{**entry, key: original[i]} for entry, i in zip(values, order, strict=True)]


def distinct(values: list[Entry], key: str) -> int:
    """How many different numbers the channel takes."""
    return len(set(numbers(values, key)))


def moved(before: list[Entry], after: list[Entry], key: str) -> float:
    """Fraction of residues the permutation actually handed a different number."""
    pairs = zip(numbers(before, key), numbers(after, key), strict=True)
    return sum(1 for a, b in pairs if a != b) / len(before)


def checked_shuffle(values: list[Entry], key: str, seed: int = SEED) -> list[Entry]:
    """A permutation, refusing anything that would make the shuffle test vacuous.

    Three of these can fire against today's `shuffled`, and they are the point
    of the function:

    * **A null in the channel.** `sasa()` reports a null `relative` for a
      ligand, a nucleotide or an ion, and `_field_value` raises a `ViewerError`
      on one. Caught here, first, so a forgotten filter reads as "filter these
      out" rather than as an error from inside a shuffle helper.
    * **A constant channel.** The bake-off's whole error, and the half of this
      machinery that would actually have caught it. Permuting identical values
      is a no-op, so the test would compare a picture with itself and pass
      having proved nothing.
    * **A permutation that barely moves.** See `MOVED`.

    The other two — same multiset, same residue keys — cannot fail against the
    implementation above, because it copies each entry and replaces one field.
    They are kept as `shuffled`'s *contract* rather than as live checks: a
    future version that resampled the numbers, or that permuted the keys
    instead of the values, would break the arms in a way that reads as a
    finding about the binding, and these two say which half moved.
    `test_shuffle.py` exercises the contract directly.
    """
    nulls = [entry for entry in values if entry.get(key, "absent") is None]
    assert not nulls, (
        f"{len(nulls)} of {len(values)} entries have a null {key!r}, which means "
        "the analysis could not measure them rather than that they measured "
        "zero. Filter them out before shuffling."
    )

    original = numbers(values, key)
    assert len(set(original)) > 1, (
        f"{key!r} takes one value on every residue, so shuffling it changes "
        "nothing and the test would pass without testing anything — which is "
        "exactly how the bake-off's B-factor conclusion happened"
    )

    out = shuffled(values, key, seed)
    permuted = numbers(out, key)

    assert sorted(permuted) == sorted(original), (
        "the shuffle changed the numbers, not just where they sit"
    )
    assert residues(out) == residues(values), (
        "the shuffle moved the residue keys, so the two arms would cover "
        "different parts of the molecule"
    )

    fraction = sum(1 for a, b in zip(original, permuted, strict=True) if a != b) / len(
        original
    )
    assert fraction > MOVED, (
        f"the permutation moved {fraction:.3f} of the channel, at or below the "
        f"{MOVED} floor — too close to the identity for the two arms to differ"
    )
    return out
