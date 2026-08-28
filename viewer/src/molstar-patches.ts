/**
 * A one-line fix to Mol*'s shaders, carried until upstream ships it.
 *
 * Mol* 5.4.2 tightened the background test in nine fragment shaders from
 * `depth > 0.999` to `depth == 1.0`, deleting the comment that said the
 * tolerance was there for precision. On the *opaque* path that is harmless: the
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
 * **This is upstream's own fix, not ours.** Mol* met this bug and repaired
 * three of the nine shaders — `postprocessing.frag`, `illumination/compose.frag`
 * and `bloom/luminosity.frag` all read `depth >= 0.99999994` at 5.11.0, with the
 * comment reproduced verbatim below. The other six still carry `== 1.0`. So this
 * is not a behaviour protean is inventing; it is the same constant applied to
 * the files upstream has not reached yet, and it should be deleted the moment a
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

/** Upstream's own repair, copied from `postprocessing.frag` at 5.11.0. */
export const FIXED_BACKGROUND_TEST = `bool isBackground(const in float depth) {
    // (2^24 - 1) / 2^24, max of 24-bit packed depth; also passes raw fp32.
    return depth >= 0.99999994;
}`;

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
  // CRLF first: it is what Mol* actually ships today, so the common case does
  // not pay for the uncommon one.
  for (const [broken, fixed] of [
    [withCrlf(BROKEN_BACKGROUND_TEST), withCrlf(FIXED_BACKGROUND_TEST)],
    [BROKEN_BACKGROUND_TEST, FIXED_BACKGROUND_TEST],
  ]) {
    if (code.includes(broken)) return code.split(broken).join(fixed);
  }
  return null;
}
