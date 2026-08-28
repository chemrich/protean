# Mol\*: `isBackground()` never fires on the transparent depth path, so SSAO evaluates over the whole framebuffer

**Status: written 2026-08-28, not yet filed.** Target:
<https://github.com/molstar/molstar/issues>. Affects 5.4.2 through 5.11.0.

---

## Summary

Since 5.4.2, `ssao.frag`'s background test cannot return true for any texel of
the *transparent* depth texture. The early return it guards therefore never
fires on that pass, and the ambient-occlusion sample loop runs across the entire
framebuffer instead of over the pixels transparent geometry actually covers.

On a CPU rasteriser this costs about **3x the price of a capture**. On a GPU it
is smaller but not free.

The same predicate landed in nine shaders in 5.4.2. Three have since been
repaired upstream — `postprocessing.frag`, `illumination/compose.frag` and
`bloom/luminosity.frag` all read `depth >= 0.99999994` as of 5.11.0, with the
comment *"(2^24 - 1) / 2^24, max of 24-bit packed depth; also passes raw fp32."*
**Six still carry `depth == 1.0`:** `ssao.frag`, `ssao-blur.frag`,
`outlines.frag`, `dof.frag`, `shadows.frag` and `illumination/trace.frag`.

So this is not a new diagnosis so much as a report that the fix was applied to
three of the nine files it was needed in.

## The change

In 5.4.1 and every release back to at least 4.18.0:

```glsl
bool isBackground(const in float depth) {
    return depth > 0.999; // handle precision issues with packed depth
}
```

In 5.4.2 and since:

```glsl
bool isBackground(const in float depth) {
    return depth == 1.0;
}
```

The deleted comment was the load-bearing part.

## Why it cannot be true

`ssao.frag` reads its depth two ways:

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

`SsaoPass.render` invokes the shader twice — once with `uTransparencyFlag = 0`
and, when transparency is included, again with `uTransparencyFlag = 1`.

**The opaque pass is fine.** With `depthTextureSupport` the value comes from a
real depth attachment, cleared to exactly `1.0`, and `== 1.0` holds.

**The transparent pass cannot be.** `tDepthTransparent` is a `uint8` RGBA target
that `Renderer.clearDepth(true)` fills with `clearColor(1, 1, 1, 1)`, and
`unpackRGBAToDepthWithAlpha` is `dot(v.xyz, UnpackFactors.yzw)` with
`UnpackFactors = (255/256) / vec4(PackFactors, 1)` and
`PackFactors = vec3(256*256*256, 256*256, 256)`. For `(1, 1, 1)` that is

```
(255/256) * (1 + 1/256 + 1/65536) = 16777215 / 16777216 = 1 - 2^-24 = 0.99999994
```

which is **the largest value the encoding can produce**. `== 1.0` is therefore
false for every texel of that texture — the cleared background included — so the
early return

```glsl
if (isBackground(selfDepth)) {
    gl_FragColor = vec4(packUnitIntervalToRG(1.0), selfPackedDepth);
    return;
}
```

is unreachable on that pass, and every pixel goes on to build a TBN basis,
generate noise and run the full `dNSamples` loop.

`ssao-blur.frag` takes the same predicate, where it is used both as an early
return and inside the kernel loop to skip background taps. The 5.6.0 addition of
`if (!isBackground(sampleDepthWithAlpha.x)) ...` to the transparent tap is dead
for the same reason.

## Impact, measured

Timed on headless Chrome under SwiftShader (`--use-gl=angle
--use-angle=swiftshader`), one 800x600 `ImagePass` capture of PDB 1UBQ with the
default preset, through `plugin.helpers.viewportScreenshot` — which forces
`samples: 128` and `reuseOcclusion: false`, so a `sampleLevel: 4` capture pays
sixteen full-screen 128-sample evaluations.

All nineteen releases from 4.18.0 to 5.11.0 were measured back to back in one
CI job, so these are ratios on one machine, not across machines:

```
release   median capture   vs 4.18.0
4.18.0          8,596 ms       1.00x
5.0.0 .. 5.4.1  8,605-8,986    ~1.00x     nine releases, no change
5.4.2          25,027          2.91x      <-- here
5.5.0 .. 5.11.0 25,316-29,509  2.95-3.43x
4.18.0 again    8,658          1.01x      runner drift over the whole job: 1%
```

Four further measurements, each of which could have refuted the diagnosis:

| condition | 5.4.1 -> 5.4.2 |
|---|---|
| as shipped | 2.91x |
| `postprocessing.occlusion` off | 0.87x — the step is gone |
| `occlusion.params.blurKernelSize` 15 -> 1 | 3.13x — not the blur |
| `occlusion.params.samples` 32 -> 1 | 1.20x — it is this loop |
| waters removed, scene fully opaque | 0.96x — gone |

The last is the direct test of the mechanism: with nothing transparent in the
scene there is no transparent SSAO pass to mis-classify, and the regression
disappears.

## The fix, and what it costs

Apply upstream's own repair to the remaining six shaders:

```glsl
bool isBackground(const in float depth) {
    // (2^24 - 1) / 2^24, max of 24-bit packed depth; also passes raw fp32.
    return depth >= 0.99999994;
}
```

Measured with that substitution applied to the six shaders in the prebuilt
5.11.0 viewer bundle, same scene, same machine, same session:

```
                      sampleLevel 4    sampleLevel 1
5.11.0 as shipped          9,829 ms         1,292 ms
5.11.0 with the fix        3,619 ms           441 ms      2.72x faster
5.4.1, before the bug      3,136 ms           370 ms
```

The fix returns 5.11.0 to within 15% of the last release that predates the bug —
and that remaining 15% matches the gradual cost the other releases in the range
add, so it is unrelated.

**And the picture does not change.** Comparing the two captures pixel by pixel,
**3 pixels of 43,200 differ, by one unit in one channel** — which is what you
would expect, since occlusion computed over empty background comes out as
approximately no occlusion anyway. The cost was being paid for nothing.

## Reproducing

Any scene with transparent geometry will do; Mol\*'s own default preset produces
one, because it renders waters at `alpha: 0.6`. Take a `sampleLevel: 4`
screenshot with `postprocessing.occlusion` on, at 5.4.1 and at 5.4.2, and
compare. Under SwiftShader the difference is unmistakable; on a GPU, time it.

A standalone harness that does exactly this across every published release is at
<https://github.com/chemrich/protean> under `bench/molstar-capture` — it loads
the prebuilt `build/viewer/molstar.js` from each version, so it needs no build
and works unchanged from 4.18.0 to 5.11.0.
