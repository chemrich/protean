#!/usr/bin/env python3
"""Splice one Mol\\* release's GLSL into another release's prebuilt bundle.

Backlog 40 named Mol\\* 5.4.2 by an elimination argument and only later got the
intervention that proves it: revert *that one line* in a stock 5.4.2 bundle and
the step goes away. This module is that intervention, generalised, so the second
regression at 5.6.0 can be answered the same way instead of by reading a diff.

**A diff cannot name a cost.** Four files change in the capture path between
5.5.0 and 5.6.0 — ``ssao.frag``, ``ssao.js``, ``outlines.frag`` and
``canvas3d.js`` — and the previous session named the first of them from reading
alone. Transplanting one shader between two bundles tests exactly one of those
four while leaving the other three in place, in both directions:

* 5.6.0 carrying 5.5.0's ``ssao.frag`` — if the step is gone, no other change
  in that release contributes to it;
* 5.5.0 carrying 5.6.0's ``ssao.frag`` — if the step appears, that shader is
  sufficient on its own.

Only one of those two is a proof; together they are a double dissociation, and
neither costs more than a row in a table this benchmark already prints.

## Why this works at all

Every Mol\\* release ships ``build/viewer/molstar.js`` with its shaders as plain
backtick template literals, unminified, because the GLSL is data rather than
code. So a shader can be lifted out of one bundle and dropped into another with
a string splice — no build, no npm resolution, and nothing that could differ
between the version being measured and the version being shipped.

The transplant is only meaningful while the shader's *interface* is unchanged:
the same uniforms, the same ``#define``s, driven by the same JavaScript. That
holds across 5.5.0/5.6.0, where the diff begins below the last uniform
declaration. It would not hold across an arbitrary pair, so
:func:`swap_shader` reports the uniform lines it saw on both sides and the
caller is expected to look.

## Nothing here fails quietly

This repo has been bitten twice by a substitution that matched nothing inside a
build that stayed green — most recently a shader patch written against LF that
Mol\\* ships with CRLF, which would have produced a viewer that looked right and
captured three times slower. So every step here raises:

* the anchor must be found, and every occurrence of it must land inside the
  *same* literal;
* that literal must actually look like a shader;
* the replacement must **differ** from what it replaces, because a no-op swap
  measured against its own unpatched twin is a row that says "no effect" for
  the wrong reason.

The digests of both sides go into the result JSON, so a row in the summary
table can be traced to the bytes that produced it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# A substring that appears in one shader's GLSL and in no other. It does not
# have to be unique in the bundle — `find_shader_literal` requires only that
# every occurrence falls inside one literal, which is the property that actually
# matters and which is checked rather than assumed.
SHADER_ANCHORS = {
    "ssao.frag": "StarCraft II Ambient Occlusion",
    "ssao-blur.frag": "uKernel[dOcclusionKernelSize]",
    "outlines.frag": "opaqueOutlineFlag",
}

# What a GLSL literal has to contain before this module will write to it. Both
# are in every fragment shader Mol* ships and in none of its JavaScript, so a
# bundle that changed shape enough to break the backtick assumption fails here
# instead of quietly corrupting a 4.8 MB file.
SHADER_MARKERS = ("precision highp float;", "void main")


class SwapError(RuntimeError):
    """A transplant that could not be made safely. Always fatal, never warned."""


def find_shader_literal(bundle: str, anchor: str) -> tuple[int, int]:
    """Return the (start, end) offsets of the GLSL body carrying ``anchor``.

    The offsets are of the text *inside* the backticks, so ``bundle[start:end]``
    is the shader and splicing it does not disturb the delimiters.
    """
    spans = set()
    at = bundle.find(anchor)
    if at < 0:
        raise SwapError(f"anchor not found in bundle: {anchor!r}")
    while at >= 0:
        start = bundle.rfind("`", 0, at)
        end = bundle.find("`", at)
        if start < 0 or end < 0:
            raise SwapError(f"anchor {anchor!r} is not inside a template literal")
        spans.add((start + 1, end))
        at = bundle.find(anchor, at + 1)
    if len(spans) != 1:
        raise SwapError(
            f"anchor {anchor!r} appears in {len(spans)} different literals; "
            "it does not identify one shader"
        )
    start, end = spans.pop()
    body = bundle[start:end]
    missing = [m for m in SHADER_MARKERS if m not in body]
    if missing:
        raise SwapError(
            f"the literal holding {anchor!r} does not look like a shader "
            f"(missing {missing}); the bundle's shape has changed"
        )
    return start, end


def read_shader(bundle: str, shader: str) -> str:
    """Lift one shader's GLSL out of a bundle."""
    anchor = SHADER_ANCHORS.get(shader)
    if anchor is None:
        raise SwapError(
            f"no anchor known for {shader!r}; known: {', '.join(sorted(SHADER_ANCHORS))}"
        )
    start, end = find_shader_literal(bundle, anchor)
    return bundle[start:end]


def uniform_lines(glsl: str) -> list[str]:
    """The shader's declared interface, for the caller to compare across a swap.

    A transplant between two releases is only meaningful while the JavaScript
    driving the shader still sets everything it reads. That is not decidable
    from the GLSL alone, so this reports rather than judges — but a difference
    here is the one thing that would make a measured row meaningless, and it
    belongs in the result next to the timings.
    """
    return sorted(
        line.strip()
        for line in glsl.splitlines()
        if line.strip().startswith(("uniform ", "#define d", "attribute "))
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def swap_shader(bundle: str, shader: str, replacement: str) -> tuple[str, dict]:
    """Return the bundle with ``shader`` replaced, and a record of what happened.

    Raises rather than returning the bundle unchanged if the replacement is
    identical to what is already there: a swap that changes nothing, measured
    against the version it was taken from, produces two matching rows for a
    reason that has nothing to do with the question being asked.
    """
    anchor = SHADER_ANCHORS.get(shader)
    if anchor is None:
        raise SwapError(
            f"no anchor known for {shader!r}; known: {', '.join(sorted(SHADER_ANCHORS))}"
        )
    start, end = find_shader_literal(bundle, anchor)
    original = bundle[start:end]
    if "`" in replacement:
        raise SwapError(
            f"{shader} replacement contains a backtick, which would terminate "
            "the template literal it is being spliced into"
        )
    if original == replacement:
        raise SwapError(
            f"{shader} in the target bundle is already byte-identical to the "
            "replacement; this swap would measure nothing"
        )
    record = {
        "shader": shader,
        "fromDigest": _digest(original),
        "toDigest": _digest(replacement),
        "fromLength": len(original),
        "toLength": len(replacement),
        "interfaceUnchanged": uniform_lines(original) == uniform_lines(replacement),
    }
    return bundle[:start] + replacement + bundle[end:], record


def resolve_source(spec: str, bundles_root: Path) -> tuple[str, str]:
    """Turn a ``--shader-swap`` right-hand side into GLSL, plus how it was found.

    Two forms, because the two questions want different sources:

    ``5.5.0``
        another release's bundle, unpacked under ``bundles_root``. This is the
        A/B against upstream itself and is what names a release.
    ``@path/to/file.frag``
        a file. This is how a *candidate repair* gets measured before anyone
        proposes it — the same shape as the fix already carried in
        ``viewer/src/molstar-patches.ts``.
    """
    if spec.startswith("@"):
        path = Path(spec[1:])
        if not path.is_file():
            raise SwapError(f"shader file not found: {path}")
        return path.read_text(), f"file:{path}"
    candidate = bundles_root / spec / "build" / "viewer" / "molstar.js"
    if not candidate.is_file():
        raise SwapError(
            f"no bundle for {spec!r} at {candidate}; "
            "it has to be fetched before it can be transplanted from"
        )
    return candidate.read_text(errors="replace"), f"bundle:{spec}"


def apply_swaps(bundle_path: Path, specs: list[str], bundles_root: Path) -> list[dict]:
    """Apply ``SHADER=SOURCE`` swaps to a bundle in place. Returns the records."""
    if not specs:
        return []
    bundle = bundle_path.read_text(errors="replace")
    records = []
    for spec in specs:
        if "=" not in spec:
            raise SwapError(f"malformed --shader-swap {spec!r}; want SHADER=SOURCE")
        shader, _, source = spec.partition("=")
        shader, source = shader.strip(), source.strip()
        source_text, provenance = resolve_source(source, bundles_root)
        replacement = (
            source_text
            if provenance.startswith("file:")
            else read_shader(source_text, shader)
        )
        bundle, record = swap_shader(bundle, shader, replacement)
        record["source"] = provenance
        records.append(record)
    bundle_path.write_text(bundle)
    return records
