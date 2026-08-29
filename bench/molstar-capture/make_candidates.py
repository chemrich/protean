#!/usr/bin/env python3
"""Write the candidate shaders the 5.6.0 experiment measures, from real bundles.

The 5.5.0-vs-5.6.0 transplant answers *which file* costs the 15%. It does not
answer the question protean actually has, which is **what that file costs
protean**, because protean does not ship stock Mol\\*: it carries the
`isBackground` repair from backlog 40 (`viewer/src/molstar-patches.ts`), and
that repair restores the early return in front of the occlusion sample loop.

Those are two different regimes and they could easily disagree:

* **stock** — `isBackground` never fires on the transparent path, so the
  128-sample loop runs over every texel of the framebuffer. A per-sample
  overhead is paid on the whole frame.
* **patched** — the early return fires, and the loop runs only where it must.
  The same per-sample overhead is then paid on a small fraction of the frame,
  and should be a much smaller fraction of the capture.

`docs/backlog.md` item 40 currently reads the agreement between the sweep's
15.2% step and the patched build's 15.4% residual as confirmation that the
patch removes none of the 5.6.0 cost. That agreement is across those two
regimes, so it is only meaningful if the loop is the same share of a capture in
both — and after the patch it plainly is not, or the patch would have bought far
more than 2.72x. The pair of files this writes is how that gets measured instead
of argued.

Each output is one release's shader with **only** the `isBackground` predicate
replaced, by exactly the substitution `viewer/src/molstar-patches.ts` performs —
upstream's own constant, and for `ssao-blur.frag` the 16-bit constant Mol\\*
itself used at 5.4.1, because that shader reads `packUnitIntervalToRG`'s
encoding rather than the depth texture.

Run it where the bundles are unpacked:

    python3 make_candidates.py --bundles-root bundles

The outputs are committed, because a measurement whose inputs are generated at
run time cannot be reproduced from the repository alone. Re-running this must
reproduce them byte for byte; `--check` asserts exactly that.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import shader_swap

HERE = Path(__file__).resolve().parent
OUT = HERE / "candidates"

# Verbatim from viewer/src/molstar-patches.ts. Not retyped from memory: the two
# constants differ because the two shaders read different encodings, and a
# previous version of that file used one constant for both, which made the patch
# a silent no-op on the blur.
BROKEN = """bool isBackground(const in float depth) {
    return depth == 1.0;
}"""

FIXED_24BIT = """bool isBackground(const in float depth) {
    // (2^24 - 1) / 2^24, max of 24-bit packed depth; also passes raw fp32.
    return depth >= 0.99999994;
}"""

FIXED_16BIT = """bool isBackground(const in float depth) {
    // Reads packUnitIntervalToRG's 16-bit encoding, not the 24-bit depth
    // texture: a background texel round-trips to 0.99998468, so the 24-bit
    // constant cannot fire here. This is what Mol* itself used at 5.4.1.
    return depth >= 0.999;
}"""

# (output name, source release, shader, replacement predicate)
CANDIDATES = [
    ("ssao-5.5.0-bgfix.frag", "5.5.0", "ssao.frag", FIXED_24BIT),
    ("ssao-5.6.0-bgfix.frag", "5.6.0", "ssao.frag", FIXED_24BIT),
    ("ssao-blur-bgfix.frag", "5.6.0", "ssao-blur.frag", FIXED_16BIT),
]

# --- splitting 5.6.0's ssao.frag into the commits that made it ---------------
#
# The transplant showed the shader carries the step: dropped into 5.5.0 it
# reproduces 108% of it, and reverting it out of 5.6.0 recovers 68%. That names
# a file. It does not name a line, and the file changed three times, in three
# commits with three different intentions:
#
#   a20e7bb40  PR #1737  wrap the opaque sample's occlusion in
#                        `if (!isBackground(sampleDepth))`
#   bb4c04f3b  PR #1740  stop clamping out-of-bounds sample coordinates and skip
#                        those samples instead, with `isOutsideBounds` + continue
#   ade027911  PR #1741  decrement nSamples on the skip path and renormalise by
#                        it rather than by the constant dNSamples
#
# Two of those are one repair (#1740 introduced the skip, #1741 made it not
# darken edges), so they are reverted together. Each variant below is 5.6.0 with
# exactly one of the two reverted, which is what turns "ssao.frag" into a line
# somebody upstream could act on.
#
# Reverting the skip restores the `clamp()` calls it replaced, rather than
# leaving coordinates unclamped and relying on the texture's wrap mode. That
# matters: unclamped is not a third semantics anyone shipped, and measuring it
# would price a build Mol* never had.

CLAMP = (
    "vec2 c = vec2(clamp(coords.x, uBounds.x, uBounds.z), "
    "clamp(coords.y, uBounds.y, uBounds.w));"
)

# Every `texture2D(<sampler>, coords)` in the four depth getters, back to the
# clamped `c` those getters used at 5.5.0. Listed rather than done by regex so
# that a miss is a KeyError here and not a quietly half-reverted shader.
UNCLAMPED_READS = [
    "unpackRGBAToDepthWithAlpha(texture2D(tDepthTransparent, coords)).x",
    "texture2D(tDepth, coords).r",
    "unpackRGBAToDepth(texture2D(tDepth, coords))",
    "unpackRGBAToDepthWithAlpha(texture2D(tDepthTransparent, coords))",
    "texture2D(tDepthQuarter, coords).r",
    "texture2D(tDepthHalf, coords).r",
    "unpackRGBAToDepth(texture2D(tDepthQuarter, coords))",
    "unpackRGBAToDepth(texture2D(tDepthHalf, coords))",
    "unpackRGBAToDepthWithAlpha(texture2D(tDepthQuarterTransparent, coords))",
    "unpackRGBAToDepthWithAlpha(texture2D(tDepthHalfTransparent, coords))",
]

VARIANT_HEADER = """// GENERATED by make_candidates.py -- do not hand-edit.
//
// Mol* 5.6.0's ssao.frag with {reverted} reverted and nothing else changed, so
// that one upstream commit can be priced on its own. Measured through
// --shader-swap ssao.frag=@<this file>.
"""


# No backticks anywhere in this, ever: the file is spliced into a JavaScript
# template literal, and one backtick would end it, producing a syntax error
# three megabytes from its cause. tests/test_shader_swap.py asserts it, and
# caught this header the first time it was written.
HEADER = """// GENERATED by bench/molstar-capture/make_candidates.py -- do not hand-edit.
//
// Mol* {version}'s {shader}, with the isBackground predicate replaced by the
// repair protean ships in viewer/src/molstar-patches.ts, and nothing else
// changed. Measured through --shader-swap {shader}=@<this file> so that the
// cost of {shader}'s body can be read in the regime protean actually runs in,
// where the early return in front of the sample loop fires.
"""


def revert_bounds_skip(glsl: str) -> str:
    """5.6.0's ssao.frag with PR #1740 + #1741 backed out.

    The skip goes, the clamps it replaced come back, and normalisation returns
    to the compile-time constant. `isOutsideBounds` itself is left declared and
    unused, which costs nothing and keeps the diff to the executed path.
    """
    out = glsl
    # The multiScale loop's copy is indented one level deeper than the plain
    # one. Both must be found exactly once and both must go: removing one and
    # not the other would produce a shader that reverts the commit in the branch
    # this scene does not run and keeps it in the branch it does.
    for block in (
        "                if (isOutsideBounds(offset.xy)) {\n"
        "                    nSamples -= 1.0;\n"
        "                    continue;\n"
        "                }\n",
        "            if (isOutsideBounds(offset.xy)) {\n"
        "                nSamples -= 1.0;\n"
        "                continue;\n"
        "            }\n",
    ):
        if out.count(block) != 1:
            raise shader_swap.SwapError(
                f"expected one bounds skip of this indentation, found "
                f"{out.count(block)}; Mol* has reformatted the loop"
            )
        out = out.replace(block, "")
    if "isOutsideBounds(offset.xy)" in out:
        raise shader_swap.SwapError("a bounds skip survived the revert")

    # Put the clamps back into the four depth getters. Deduplicated first:
    # `tDepthTransparent` is read the same way in two getters, and replacing all
    # occurrences of the first one makes the second entry look missing.
    for read in dict.fromkeys(UNCLAMPED_READS):
        if read not in out:
            raise shader_swap.SwapError(f"expected an unclamped read: {read}")
        out = out.replace(read, read.replace(", coords)", ", c)"))
    if ", coords)" in out.split("void main")[0].split("vec3 viewNormalAtPixel")[0]:
        raise shader_swap.SwapError("a depth getter still reads unclamped coords")
    # Each getter needs the `c` those reads now name. The declaration sits
    # immediately after the getter's opening line in 5.5.0; re-inserting it in
    # front of the first statement of each body reproduces that.
    for opener, indent in (
        (
            "float getDepth(const in vec2 coords, const in int transparentFlag) {\n",
            "    ",
        ),
        ("vec2 getDepthTransparentWithAlpha(const in vec2 coords){\n", "        "),
        (
            "float getMappedDepth(const in vec2 coords, const in vec2 selfCoords) {\n",
            "    ",
        ),
        (
            "vec2 getMappedDepthTransparentWithAlpha("
            "const in vec2 coords, const in vec2 selfCoords) {\n",
            "        ",
        ),
    ):
        if opener not in out:
            raise shader_swap.SwapError(f"getter not found: {opener.strip()}")
        out = out.replace(opener, opener + indent + CLAMP + "\n", 1)

    for before, after in (
        ("levelOcclusion /= nSamples;", "levelOcclusion /= float(dNSamples);"),
        ("occlusion /= nSamples;", "occlusion /= float(dNSamples);"),
    ):
        if before not in out:
            raise shader_swap.SwapError(f"normalisation not found: {before}")
        out = out.replace(before, after)
    if "nSamples -= 1.0" in out:
        raise shader_swap.SwapError("a nSamples decrement survived the revert")
    return out


def revert_background_guard(glsl: str) -> str:
    """5.6.0's ssao.frag with PR #1737's shader half backed out.

    The opaque sample's occlusion goes back to being computed unconditionally,
    as it was at 5.5.0. Only the *opaque* guard: the transparent one predates
    both releases and must survive.

    The GLSL is assembled from fragments rather than written as long literals,
    because it has to match Mol*'s text exactly and a wrapped literal would not.
    """

    def guarded(indent: str, fetch: str, radius: str, bias: str) -> str:
        i = indent
        return (
            f"{i}{fetch}\n"
            f"{i}if (!isBackground(sampleDepth)) {{\n"
            f"{i}    float sampleViewZ = screenSpaceToViewSpace("
            "vec3(offset.xy, sampleDepth), uInvProjection).z;\n"
            f"{i}    sampleOcc = step(sampleViewPos.z + 0.025, sampleViewZ)"
            f" * smootherstep(0.0, 1.0, {radius}"
            f" / abs(selfViewPos.z - sampleViewZ)){bias};\n"
            f"{i}}}"
        )

    def unguarded(indent: str, fetch: str, radius: str, bias: str) -> str:
        i = indent
        return (
            f"{i}{fetch}\n"
            f"{i}float sampleViewZ = screenSpaceToViewSpace("
            "vec3(offset.xy, sampleDepth), uInvProjection).z;\n"
            f"{i}sampleOcc = step(sampleViewPos.z + 0.025, sampleViewZ)"
            f" * smootherstep(0.0, 1.0, {radius}"
            f" / abs(selfViewPos.z - sampleViewZ)){bias};"
        )

    sites = [
        # the multiScale loop
        (
            " " * 24,
            "float sampleDepth = getMappedDepth(offset.xy, selfCoords);",
            "uLevelRadius[l]",
            " * uLevelBias[l]",
        ),
        # the plain loop, which is the one this scene runs
        (
            " " * 20,
            "float sampleDepth = getDepth(offset.xy, 0);",
            "uRadius",
            "",
        ),
    ]
    out = glsl
    for site in sites:
        before, after = guarded(*site), unguarded(*site)
        if out.count(before) != 1:
            raise shader_swap.SwapError(
                "the opaque background guard is not in the shape this expects "
                f"(found {out.count(before)} of it at indent {len(site[0])}); "
                "Mol* has reformatted it and the revert must be re-derived"
            )
        out = out.replace(before, after, 1)
    return out


VARIANTS = [
    (
        "ssao-5.6.0-no-bounds-skip.frag",
        revert_bounds_skip,
        "PR #1740 + #1741, the isOutsideBounds skip and its renormalisation",
    ),
    (
        "ssao-5.6.0-no-bg-guard.frag",
        revert_background_guard,
        "PR #1737's shader half, the opaque isBackground guard",
    ),
]


def build(bundles_root: Path) -> dict[str, str]:
    out = {}
    for name, version, shader, fixed in CANDIDATES:
        bundle, _ = shader_swap.resolve_source(version, bundles_root)
        glsl = shader_swap.read_shader(bundle, shader)
        if BROKEN not in glsl:
            raise shader_swap.SwapError(
                f"{version} {shader} does not carry the broken predicate; "
                "either the version is wrong or Mol* has fixed it and this "
                "experiment no longer means what it says"
            )
        patched = glsl.replace(BROKEN, fixed)
        if patched == glsl:
            raise shader_swap.SwapError(f"substitution changed nothing in {name}")
        out[name] = HEADER.format(version=version, shader=shader) + patched

    bundle, _ = shader_swap.resolve_source("5.6.0", bundles_root)
    stock = shader_swap.read_shader(bundle, "ssao.frag")
    for name, revert, reverted in VARIANTS:
        variant = revert(stock)
        if variant == stock:
            raise shader_swap.SwapError(f"{name} is identical to stock 5.6.0")
        out[name] = VARIANT_HEADER.format(reverted=reverted) + variant
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundles-root", type=Path, default=Path("bundles"))
    ap.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed files are not what this would write",
    )
    args = ap.parse_args()

    built = build(args.bundles_root.resolve())
    OUT.mkdir(exist_ok=True)
    for name, text in built.items():
        path = OUT / name
        if args.check:
            if not path.is_file() or path.read_text() != text:
                print(f"{path} is not what make_candidates.py would write")
                return 1
            print(f"ok  {path}")
        else:
            path.write_text(text)
            print(f"wrote {path} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
