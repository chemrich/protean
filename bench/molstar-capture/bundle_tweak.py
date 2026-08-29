#!/usr/bin/env python3
"""Named one-line changes to a Mol\\* bundle's JavaScript, for the same reason.

`shader_swap.py` can move GLSL between releases because Mol\\* ships its shaders
as plain template literals. Its JavaScript is minified, so the same trick does
not work there — and the leading candidate for the 5.6.0 regression is in
JavaScript, not GLSL.

Without something here, the best that experiment can say about the SSAO kernel
is *by elimination*: the shader was transplanted, the step survived, so it must
be the other thing in the occlusion pass. That is exactly the shape of argument
backlog 40 was criticised for making, and the criticism was fair — an
elimination names a suspect, an intervention convicts one.

So: a small registry of exact, named substitutions, each matched by a regular
expression written against the *structure* rather than the identifiers, because
identifiers are what minification renames. Each must match exactly once or the
run stops.

## The one that matters

At 5.6.0 the SSAO kernel became blue-noise distributed, by best-candidate
selection: draw `candidateCount` vectors, keep the one furthest from everything
already chosen. That is not radially neutral. Maximising the minimum distance to
existing samples is systematically easier to satisfy further out, so the
selection biases the radial distribution outward — measured over 60 trials at
n=128, with the identical `scale` ramp both releases apply:

    5.5.0 Math.random hemisphere   mean |v| 0.2030   mean |v.xy| 0.1608
    5.6.0 best-candidate blue noise mean |v| 0.2858  mean |v.xy| 0.1859

Those are the algorithm's *expectation*, and they overstate it. 5.6.0's
generator is a PCG with a fixed seed, so Mol* uploads one kernel every time, and
that kernel's actual figures are **+34% radius and +7% in-plane** — see
`kernel_stats.mjs`. In-plane is the part that moves a texture fetch, and +7% is
small. 5.5.0's table, by contrast, is built with `Math.random()` at module load,
so it really is a distribution: a different kernel per page load, +/-6% on mean
|s|, which is run-to-run variance 5.6.0 does not have and 5.5.0 rows do.

`candidates-1` sets `candidateCount` to 1. Best-candidate selection with one
candidate is plain sampling from the base distribution — same generator, same
PCG, same code path, one token different. It removes the outward bias and then
some: the *base* 5.6.0 distribution is tighter than 5.5.0's (in-plane 0.594x),
because the raw generator doubles z before normalising. So the prediction is
sharp and one-sided.

**If the kernel spread is the mechanism, that row is markedly faster than 5.6.0
stock, and faster than 5.5.0 too. If it reads the same as stock, the hypothesis
is dead** — and it is dead by measurement rather than by my having run out of
files to blame.

The anchors are written against 5.6.0 and asserted, not assumed. A release that
restructures this code fails the run rather than silently measuring nothing.
"""

from __future__ import annotations

import hashlib
import re

# name -> (pattern, replacement, what it does and why it is worth a row)
#
# Patterns match operators and literals, never minified identifiers: `Math.max`,
# `Math.min` and the numbers survive minification, `candidateCount` does not.
TWEAKS = {
    "candidates-1": (
        re.compile(
            r"Math\.max\(10,\s*Math\.min\(30,\s*Math\.floor\([A-Za-z_$][\w$]*\s*/\s*10\)\)\)"
        ),
        "1",
        "SSAO blue-noise candidateCount -> 1, which turns best-candidate "
        "selection into plain sampling and removes its outward radial bias",
    ),
}


class TweakError(RuntimeError):
    """A bundle change that could not be made exactly. Always fatal."""


def apply_tweak(bundle: str, name: str) -> tuple[str, dict]:
    """Apply one named tweak, or raise. Returns the bundle and a record."""
    entry = TWEAKS.get(name)
    if entry is None:
        raise TweakError(
            f"no bundle tweak named {name!r}; known: {', '.join(sorted(TWEAKS))}"
        )
    pattern, replacement, description = entry
    found = pattern.findall(bundle)
    if len(found) != 1:
        raise TweakError(
            f"tweak {name!r} matched {len(found)} times, not once. Its pattern is "
            "written against Mol* 5.6.0's minified shape; a release that "
            "restructured this code has to be looked at rather than measured."
        )
    matched = pattern.search(bundle)
    assert matched is not None  # findall found exactly one
    if matched.group(0) == replacement:
        raise TweakError(f"tweak {name!r} would change nothing")
    # Spliced at the matched span rather than through `re.sub(..., count=1)`.
    # With the single-match check above, the count argument could never matter,
    # and a guard no test can reach is a place the next person stops looking.
    start, end = matched.span()
    return bundle[:start] + replacement + bundle[end:], {
        "tweak": name,
        "what": description,
        "matched": matched.group(0),
        "replacedWith": replacement,
        "atOffset": matched.start(),
        "bundleDigestBefore": hashlib.sha256(bundle.encode()).hexdigest()[:12],
    }


def apply_tweaks(bundle_path, names: list[str]) -> list[dict]:
    """Apply named tweaks to a bundle in place. Returns the records."""
    if not names:
        return []
    bundle = bundle_path.read_text(errors="replace")
    records = []
    for name in names:
        bundle, record = apply_tweak(bundle, name)
        records.append(record)
    bundle_path.write_text(bundle)
    return records
