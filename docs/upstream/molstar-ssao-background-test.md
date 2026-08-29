# Mol\*: `isBackground()` cannot fire on the packed transparent depth, so SSAO evaluates over the whole framebuffer

**Status: written 2026-08-28. NOT YET FILED.** Target:
<https://github.com/molstar/molstar/issues>. Observed on 5.4.2 through 5.11.0.

## Before filing — do these four things first

This was written months before it will be sent. Do not paste it without:

1. **Re-check the six shaders at HEAD.** Some or all may already read
   `>= 0.99999994`. Run, in a checkout of molstar:
   `grep -rn "isBackground" src/mol-gl/shader/ -A3`
   If `ssao.frag.ts` is fixed, the headline is gone and this becomes at most a
   note about `ssao-blur.frag.ts`.
2. **Find the two commits this report leans on and put their links in.** The one
   that changed the predicate to `== 1.0` (lands in 5.4.2), and the one that
   fixed `postprocessing.frag.ts` to `>= 0.99999994` (lands in 5.11.0). The
   second is the strongest argument in the document — *upstream already agrees,
   it just missed some files* — and it is worth nothing without a link.
3. **Decide issue or PR.** See the last section; a PR is probably right.
4. **Check the repro link resolves** and that linking protean publicly is wanted.

## Summary

`ssao.frag`'s background test cannot return true for any texel of the
*transparent* depth texture. The early return it guards therefore never fires on
that pass, and the ambient-occlusion sample loop runs across the entire
framebuffer instead of over the pixels transparent geometry actually covers.

Under a software rasteriser this costs roughly **2.9x the price of a capture**.
On a GPU it will be smaller — **we have not measured a GPU and make no claim
about one** — but the wasted work is the same work, and headless CI, server-side
rendering and any ANGLE/SwiftShader fallback pay for it in full.

## Which shaders, and when

At **5.4.2** the predicate changed in **four** shaders, from four different
constants, all to `depth == 1.0`:

| shader | before 5.4.2 | 5.4.2 onward | at 5.11.0 |
|---|---|---|---|
| `mol-gl/shader/ssao.frag` | `depth > 0.999` | `depth == 1.0` | **still `== 1.0`** |
| `mol-gl/shader/ssao-blur.frag` | `depth >= 0.999` | `depth == 1.0` | **still `== 1.0`** |
| `mol-gl/shader/outlines.frag` | `depth > 0.9999` | `depth == 1.0` | **still `== 1.0`** |
| `mol-gl/shader/postprocessing.frag` | `depth > 0.9999` | `depth == 1.0` | fixed: `>= 0.99999994` |

The comment deleted alongside the first two — *"handle precision issues with
packed depth"* / *"checking for 1.0 is not enough, because of precision issues"*
— was the load-bearing part.

Five *other* shaders have read `depth == 1.0` since at least 4.18.0, so they are
not part of this regression, but they are the same mistake and the same repair
applies: `dof.frag`, `shadows.frag`, `illumination/trace.frag`,
`illumination/compose.frag`, `bloom/luminosity.frag`. **Of those, upstream fixed
`illumination/compose.frag` and `bloom/luminosity.frag` at 5.11.0**, to the same
`>= 0.99999994` — which is why this report exists: the fix is already yours, it
just has not reached six of the nine sites.

## Why it cannot be true

`ssao.frag` reads depth two ways:

```glsl
float getDepth(const in vec2 coords, const in int transparentFlag) {
    if (transparentFlag == 1) {
        return unpackRGBAToDepthWithAlpha(texture2D(tDepthTransparent, coords)).x;
    }
    #ifdef depthTextureSupport
        return texture2D(tDepth, coords).r;
    #else
        return unpackRGBAToDepth(texture2D(tDepth, coords));
    #endif
}
```

`SsaoPass.render` invokes the shader twice — once with `uTransparencyFlag = 0`,
and again with `= 1` when transparency is included.

`tDepthTransparent` is a `uint8` RGBA target that `Renderer.clearDepth(true)`
fills with `clearColor(1, 1, 1, 1)`. With
`PackFactors = vec3(256*256*256, 256*256, 256)` and
`UnpackFactors = (255/256) / vec4(PackFactors, 1)`, the **three-channel**
`unpackRGBAToDepthWithAlpha` — `dot(v.xyz, UnpackFactors.yzw)` — gives, for
`(1, 1, 1)`:

```
(255/256) * (1 + 1/256 + 1/65536) = 16777215/16777216 = 1 - 2^-24 = 0.99999994
```

That is **the largest value the encoding can produce**, so `== 1.0` is false for
every texel of that texture, cleared background included.

**The detail that explains why nobody noticed**: the four-channel
`unpackRGBAToDepth` used on the *opaque* fallback path adds
`(255/256)/256^3` as well, and that sum rounds to **exactly 1.0 in float32**. So
the opaque path — packed or not — still takes the early-out, and only the
three-channel transparent path is broken. Verified in float32 in both summation
orders.

Consequence: from 5.4.2, whenever a scene has transparency, the transparent SSAO
evaluation runs its full sample loop on **every pixel** rather than the few
percent transparent geometry covers.

**It is worst on the screenshot path specifically.**
`mol-plugin/util/viewport-screenshot.js` builds its `ImagePass` with
`samples: 128`, `transparentThreshold: 1` and `reuseOcclusion: false`. The
`transparentThreshold: 1` is what turns the transparent pass *on* for a scene
whose `transparencyMin` is 0.4 — the interactive viewport's default of 0.4 would
leave it off — and `reuseOcclusion: false` means a `sampleLevel: 4` capture pays
**sixteen** full-screen 128-sample evaluations.

### `ssao-blur.frag` needs a different constant

`ssao-blur.frag` does **not** read the depth texture. It reads what `ssao.frag`
wrote: a depth packed by `packUnitIntervalToRG` into two `uint8` channels —
sixteen bits, not twenty-four. A transparent-background texel round-trips
through that encoding to **0.99998468**, so `>= 0.99999994` would not fire there
either. The correct constant for that file is the `>= 0.999` it had at 5.4.1.

Separately, and not proposed for fixing here because it is a behaviour change
nobody has measured: `packUnitIntervalToRG(1.0)` does not round-trip to anything
near 1.0, so the blur's *opaque* background test appears never to have fired in
any release.

## Impact, measured

**Two different machines. They are not comparable in absolute terms**, only
within each block.

**(1) GitHub Actions `ubuntu-latest`, headless Chrome 152, ANGLE/SwiftShader
(Subzero)**, `--headless=new --no-sandbox --use-gl=angle
--use-angle=swiftshader`. One 800x600 `ImagePass` capture of PDB 1UBQ with the
default preset, through `plugin.helpers.viewportScreenshot`. All nineteen
releases 4.18.0–5.11.0 measured **back to back in one job**, 4 timed captures
after 1 warmup each:

```
release   median capture   vs 4.18.0
4.18.0          8,596 ms       1.00x
5.0.0 .. 5.4.1  8,605-8,986    ~1.00x     nine releases, no change
5.4.2          25,027          2.91x      <-- here
5.5.0          25,316          2.95x
5.6.0          29,174          3.39x      <-- a second, separate step: 1.15x
5.6.1 .. 5.11.0 28,615-29,509  3.33-3.43x
4.18.0 again    8,658          1.01x      drift over the whole job: 1%
```

Controls: 4.18.0 first and last differ by 1%. 5.1.0/5.1.1/5.1.2 have
byte-identical render paths and read 2.07% apart, which is the noise floor; the
step is 92x that. Pre- and post-step capture populations are disjoint. Draw and
instance counts are identical throughout.

Four interventions, each of which could have refuted the diagnosis:

| condition | 5.4.1 -> 5.4.2 |
|---|---|
| as shipped | 2.91x |
| `postprocessing.occlusion` off | 1.04x — the step is gone |
| `occlusion.params.blurKernelSize` 15 -> 1 | unchanged — not the blur |
| `occlusion.params.samples` 128 -> 1 | collapses — it is this loop |
| waters removed, scene fully opaque | 0.96x — gone |

And the decisive one: reverting **that single line** in the 5.4.2 bundle, with
nothing else changed, removes the step and restores 5.4.1 timings.

**(2) A macOS laptop, Chrome, ANGLE/SwiftShader (LLVM 10.0.0), n=1 per
condition, 4 repeats.** Applying the fix to the six shaders in the prebuilt
5.11.0 viewer bundle:

```
                      sampleLevel 4    sampleLevel 1
5.11.0 as shipped          9,829 ms         1,292 ms
5.11.0 with the fix        3,619 ms           441 ms
5.4.1, before the bug      3,136 ms           370 ms
```

That is a **2.2x-2.9x** band, not a point — there is no drift control inside
this experiment. The remaining 15% between the fixed build and 5.4.1 is the
**5.6.0** step above, not measurement error: the sweep puts 5.6.0 at 15.2% and
this residual at 15.4%.

**Rendering difference from the fix**, at full 800x600 resolution, both builds
bit-reproducible across separate browser processes: **56 of 480,000 pixels
differ by at most 2/255** under default postprocessing, none on the background.
With the outline pass enabled, 2,425 pixels differ by up to 161/255 — and that
one is a *correction*: the fixed build's outline matches 5.4.1's coverage to
five decimals, where 5.11.0 stock does not.

## The second regression, at 5.6.0

Separate from the above and **not fixed by the constant**: 5.6.0 costs a further
**1.15x**, also in `ssao.frag`, where the per-sample bounds clamp became an
`isOutsideBounds()` test with a `continue`. The eight releases either side of it
add about 2.3% between them, so this is a discrete step and not drift. Worth
looking at alongside; we have not diagnosed it.

## The fix

```glsl
// ssao.frag, outlines.frag, dof.frag, shadows.frag, illumination/trace.frag
bool isBackground(const in float depth) {
    // (2^24 - 1) / 2^24, max of 24-bit packed depth; also passes raw fp32.
    return depth >= 0.99999994;
}

// ssao-blur.frag — reads packUnitIntervalToRG's 16-bit encoding, whose
// background texel round-trips to 0.99998468; the 24-bit constant cannot fire.
bool isBackground(const in float depth) {
    return depth >= 0.999;
}
```

Sources are `src/mol-gl/shader/*.frag.ts` in the repository; the published
package ships the compiled `lib/`.

## Reproducing

Any scene with transparent geometry; Mol\*'s own default preset produces one,
rendering waters at `alpha: 0.6`. Take a `sampleLevel: 4` screenshot with
`postprocessing.occlusion` on, at 5.4.1 and 5.4.2, and time it. Under
SwiftShader the difference is unmistakable.

A standalone harness that does this across every published release — loading
each version's prebuilt `build/viewer/molstar.js`, so it needs no build and runs
unchanged from 4.18.0 to 5.11.0 — is at
<https://github.com/chemrich/protean> under `bench/molstar-capture`.

## Issue or pull request?

Probably **a PR**, with the issue text above as its description. The change is
one line in six files, upstream has already made the identical change in three
others, and the constant and its comment are upstream's own. The thing a PR must
not do is apply `>= 0.99999994` uniformly — `ssao-blur.frag` needs `>= 0.999`,
for the reason given above, and that is the part a reviewer is most likely to
miss.
