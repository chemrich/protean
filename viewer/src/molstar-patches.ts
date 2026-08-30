/**
 * A one-line fix to Mol*'s shaders, carried until upstream ships it.
 *
 * Mol* 5.4.2 tightened the background test in **four** fragment shaders —
 * `ssao`, `ssao-blur`, `outlines` and `postprocessing` — to `depth == 1.0`,
 * deleting the comment that said the old tolerance was there for precision.
 * (It was not one constant before: `ssao` read `> 0.999`, `ssao-blur`
 * `>= 0.999`, `outlines` and `postprocessing` `> 0.9999`.) Five *other*
 * shaders — `dof`, `shadows`, `illumination/trace`, `illumination/compose`,
 * `bloom/luminosity` — have read `== 1.0` since at least 4.18.0. That is an
 * older, separate instance of the same mistake and is not part of the
 * 4.18 -> 5.11 regression, though the same repair applies.
 *
 * On the *opaque* path the tightening is harmless: the
 * depth comes from a real depth texture, where the background really is exactly
 * 1.0. On the **transparent** path it is not. There the depth is read back
 * through `unpackRGBAToDepthWithAlpha` over a uint8 RGBA target that
 * `renderer.clearDepth` fills with (1,1,1,1), and that unpacks to
 *
 *     (255/256) * (1 + 1/256 + 1/65536) = 16777215/16777216 = 1 - 2^-24
 *
 * which is the largest value the encoding can produce and is not 1.0. So the
 * test is false for every texel of that texture, background or not, and the
 * early return in front of each shader's sample loop never fires.
 *
 * For ambient occlusion that is the whole cost of a capture. The screenshot
 * helper forces `samples: 128` and `reuseOcclusion: false`
 * (`mol-plugin/util/viewport-screenshot.js`), so a level-4 capture pays sixteen
 * full-screen 128-sample occlusion evaluations across the entire framebuffer
 * instead of over the few percent that transparent geometry covers. Measured on
 * CI over all nineteen releases between 4.18.0 and 5.11.0: 5.4.2 costs **2.91x**
 * its predecessor, and switching the occlusion pass off erases the step
 * completely. `docs/backlog.md` item 40 has the table.
 *
 * **The 24-bit constant is upstream's own, not ours.** Mol* met this bug and
 * repaired three shaders — `postprocessing.frag`, `illumination/compose.frag`
 * and `bloom/luminosity.frag` all read `depth >= 0.99999994` at 5.11.0, with the
 * comment reproduced verbatim below. Six still carry `== 1.0`.
 *
 * **But one constant does not fit all six, and assuming it did was a bug in the
 * first version of this file.** `ssao-blur.frag` does not read the 24-bit depth
 * texture. It reads what `ssao.frag` *wrote*: a depth packed by
 * `packUnitIntervalToRG` into two uint8 channels — sixteen bits, not
 * twenty-four. A transparent-background texel round-trips through that encoding
 * to **0.99998468**, so `>= 0.99999994` is false there and the patch would have
 * been a silent no-op on that shader. It is given `>= 0.999` instead, which is
 * exactly what Mol* itself had in that file at 5.4.1.
 *
 * (Worth recording alongside: the blur's *opaque* background test has never
 * fired in any release, because `packUnitIntervalToRG(1.0)` does not round-trip
 * to anything near 1.0. That is a third upstream bug and is deliberately NOT
 * fixed here — it would be new behaviour nobody has measured.)
 *
 * `shadows.frag` and `illumination/trace.frag` read only the opaque depth
 * texture, where the background really is exactly 1.0. The patch is a no-op on
 * them today; they are included so the predicate is uniform if they ever start
 * reading a packed depth, and because leaving a known-wrong test in place
 * invites the next person to assume it was considered.
 *
 * None of this is behaviour protean is inventing: every constant here is one
 * Mol* has itself used in that same file. It should be deleted the moment a
 * release ships with them fixed. `MOLSTAR_SHADERS_NEEDING_THE_FIX` is what makes
 * that deletion happen rather than be remembered — a test asserts the list is
 * exactly right, so a Mol* upgrade that fixes any of them fails loudly.
 *
 * Kept out of `vite.config.ts` so it can be tested without running a build.
 */

/** The predicate as Mol* 5.4.2 left it. Matched exactly, indentation included. */
export const BROKEN_BACKGROUND_TEST = `bool isBackground(const in float depth) {
    return depth == 1.0;
}`;

/**
 * Upstream's own repair, copied from `postprocessing.frag` at 5.11.0. Correct for
 * every shader that reads the 24-bit packed depth texture, or a raw fp32 one.
 */
export const FIXED_BACKGROUND_TEST = `bool isBackground(const in float depth) {
    // (2^24 - 1) / 2^24, max of 24-bit packed depth; also passes raw fp32.
    return depth >= 0.99999994;
}`;

/**
 * For `ssao-blur.frag` alone, which reads a SIXTEEN-bit `packUnitIntervalToRG`
 * encoding rather than the 24-bit depth texture. A transparent-background texel
 * comes back as 0.99998468 through that round trip, so the 24-bit constant above
 * cannot fire. This is the constant Mol* had in this file at 5.4.1.
 */
export const FIXED_BACKGROUND_TEST_16BIT = `bool isBackground(const in float depth) {
    // Reads packUnitIntervalToRG's 16-bit encoding, not the 24-bit depth
    // texture: a background texel round-trips to 0.99998468, so the 24-bit
    // constant cannot fire here. This is what Mol* itself used at 5.4.1.
    return depth >= 0.999;
}`;

/** The one shader that needs the 16-bit constant. */
export const SHADER_NEEDING_16BIT_FIX = 'ssao-blur.frag';

/**
 * The shaders that still carry the broken predicate, as of Mol* 5.11.0, given
 * relative to `molstar/lib/mol-gl/shader/`.
 *
 * Not used to decide what to patch — the transform matches on the source text,
 * so a shader Mol* adds later is covered without anyone editing this list. It is
 * here so a test can assert the list is still accurate, which is what turns "we
 * should delete this when upstream fixes it" from an intention into a failure.
 */
export const MOLSTAR_SHADERS_NEEDING_THE_FIX = [
  'dof.frag.js',
  'illumination/trace.frag.js',
  'outlines.frag.js',
  'shadows.frag.js',
  'ssao-blur.frag.js',
  'ssao.frag.js',
];

/** The three upstream has already repaired, for the same reason. */
export const MOLSTAR_SHADERS_ALREADY_FIXED = [
  'bloom/luminosity.frag.js',
  'illumination/compose.frag.js',
  'postprocessing.frag.js',
];

/**
 * Mol* ships the GLSL inside these modules with **CRLF** line endings, all nine
 * of them, while the surrounding JavaScript uses LF.
 *
 * Worth stating rather than quietly handling, because the first version of this
 * patch matched on LF alone and would have rewritten nothing at all — in a build
 * that stayed green, producing a viewer that looked exactly right and captured
 * three times slower than it needed to. The test caught it; the build assertion
 * would have caught it second. Both forms are matched now, so a future Mol*
 * release that normalises its line endings does not silently turn the patch off.
 */
function withCrlf(text: string): string {
  return text.replace(/\n/g, '\r\n');
}

/**
 * Apply the fix to one module's source, or return null if it does not carry the
 * broken predicate.
 *
 * Null rather than the unchanged source, because Vite reads null as "this plugin
 * had nothing to say" and returning identical code would put a needless
 * sourcemap break in every module in the graph.
 */
export function patchBackgroundTest(code: string, id: string): string | null {
  if (!id.includes('mol-gl/shader/')) return null;
  // Which constant depends on the ENCODING the shader reads back, not on
  // taste. See the header: ssao-blur reads 16 bits where the rest read 24.
  const fix = id.includes(SHADER_NEEDING_16BIT_FIX)
    ? FIXED_BACKGROUND_TEST_16BIT
    : FIXED_BACKGROUND_TEST;
  // CRLF first: it is what Mol* actually ships today, so the common case does
  // not pay for the uncommon one.
  for (const [broken, fixed] of [
    [withCrlf(BROKEN_BACKGROUND_TEST), withCrlf(fix)],
    [BROKEN_BACKGROUND_TEST, fix],
  ]) {
    if (code.includes(broken)) return code.split(broken).join(fixed);
  }
  return null;
}
