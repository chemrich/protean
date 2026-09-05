/**
 * The GLSL for the painterly pass, kept apart from the plumbing that runs it.
 *
 * Mol\* authors every shader in GLSL ES 1.00 and rewrites it to `#version 300
 * es` on WebGL2 (`mol-gl/shader-code.js:362`), so these must be 1.00-legal:
 * constant loop bounds, no `inverse()`, `texture2D` rather than `texture`, and
 * `gl_FragColor` rather than a declared output. `#include common` brings in
 * `unpackRGBAToDepth`, `perspectiveDepthToViewZ` and `orthographicDepthToViewZ`
 * from `mol-gl/shader/chunks/common.glsl.js`.
 *
 * `quad_vert` declares no varying, so every pass derives its own coordinates
 * from `gl_FragCoord.xy / uTexSize`, the way `outlines.frag` does.
 */

/** Scharr gradients into a Di Zenzo structure tensor, plus linear view depth.
 *
 * Scharr rather than Sobel: the same eight taps at the same cost, with about an
 * order of magnitude less angular error. Everything downstream consumes a
 * *direction*, so angular error is the only error that matters here.
 *
 * The tensor is summed over the three colour channels rather than over
 * luminance. Collapsing to luminance first throws away every isoluminant edge,
 * and a structure coloured by chain or by secondary structure is largely
 * isoluminant edges.
 *
 * Output is `(E, F, G, viewZ)` in a half-float target. `viewZ` rides along so
 * the two smoothing passes and the brush can all read depth from one texture.
 */
export const painterly_tensor_frag = `
precision highp float;
precision highp sampler2D;

uniform sampler2D tColor;
uniform sampler2D tDepth;
uniform vec2 uTexSize;
uniform float uNear;
uniform float uFar;
uniform float uIsOrtho;

#include common

float getDepth(const in vec2 coords) {
    #ifdef depthTextureSupport
        return texture2D(tDepth, coords).r;
    #else
        return unpackRGBAToDepth(texture2D(tDepth, coords));
    #endif
}

// Un-premultiplied. With a transparent background the colour target holds
// colour*alpha, so the gradient of the raw target is the gradient of the
// *alpha* edge — which would draw a phantom ridge around the whole molecule
// and comb the brush along it.
vec3 fetch(const in vec2 coords) {
    vec4 c = texture2D(tColor, coords);
    return c.a > 0.0 ? c.rgb / c.a : vec3(0.0);
}

void main(void) {
    vec2 coords = gl_FragCoord.xy / uTexSize;
    vec2 d = 1.0 / uTexSize;

    vec3 cmm = fetch(coords + vec2(-d.x, -d.y));
    vec3 c0m = fetch(coords + vec2(0.0, -d.y));
    vec3 cpm = fetch(coords + vec2(d.x, -d.y));
    vec3 cm0 = fetch(coords + vec2(-d.x, 0.0));
    vec3 cp0 = fetch(coords + vec2(d.x, 0.0));
    vec3 cmp = fetch(coords + vec2(-d.x, d.y));
    vec3 c0p = fetch(coords + vec2(0.0, d.y));
    vec3 cpp = fetch(coords + vec2(d.x, d.y));

    vec3 gx = (3.0 * (cpm + cpp) + 10.0 * cp0 - 3.0 * (cmm + cmp) - 10.0 * cm0) / 32.0;
    vec3 gy = (3.0 * (cmp + cpp) + 10.0 * c0p - 3.0 * (cmm + cpm) - 10.0 * c0m) / 32.0;

    float depth = getDepth(coords);
    // The background sits at a depth of exactly 1. Pushing it far past uFar
    // rather than leaving it at uFar is what makes the bilateral weight below
    // treat the silhouette as a break rather than as a steep slope.
    float viewZ = depth >= 0.99999994 ? -4.0 * uFar : depthToViewZ(uIsOrtho, depth, uNear, uFar);

    gl_FragColor = vec4(dot(gx, gx), dot(gx, gy), dot(gy, gy), viewZ);
}
`;

/** One half of a separable, depth-aware Gaussian over the tensor components.
 *
 * The tensor is smoothed, never the directions. A direction carries a 180°
 * ambiguity, so averaging `+t` and `-t` gives zero; the outer product `E,F,G`
 * is sign-invariant and averages correctly. That is the whole reason the
 * structure tensor exists rather than "blur the gradient".
 *
 * The bilateral term on linear view Z is not optional. A tensor from the far
 * side of a silhouette is a different object's direction, and blending the two
 * is what puts a coloured aura around the subject in every naive painterly
 * filter.
 */
export const painterly_blur_frag = `
precision highp float;
precision highp sampler2D;

uniform sampler2D tTensor;
uniform vec2 uTexSize;
uniform vec2 uDir;
uniform float uSigma;
uniform float uDepthFalloff;

void main(void) {
    vec2 coords = gl_FragCoord.xy / uTexSize;
    vec4 c = texture2D(tTensor, coords);
    float z0 = c.w;
    vec3 acc = c.xyz;
    float wsum = 1.0;

    for (int i = 1; i <= dTaps; ++i) {
        float o = float(i);
        float wg = exp(-0.5 * o * o / (uSigma * uSigma));
        for (int s = 0; s < 2; ++s) {
            vec2 walk = (s == 0 ? o : -o) * uDir / uTexSize;
            vec4 t = texture2D(tTensor, coords + walk);
            float wz = exp(-abs(t.w - z0) / uDepthFalloff);
            float w = wg * wz;
            acc += t.xyz * w;
            wsum += w;
        }
    }

    gl_FragColor = vec4(acc / wsum, z0);
}
`;

/** The smoothed tensor decomposed into a direction, a confidence and a depth.
 *
 * Its own pass rather than folded into the brush, because the brush walks the
 * field a few dozen steps and re-deriving the eigenvector at every step would
 * pay for the decomposition once per tap instead of once per pixel.
 *
 * Where the field has nothing to say — a flat interior, an empty ground — the
 * direction is not merely arbitrary, it is *unstable*: neighbouring pixels get
 * unrelated answers from rounding noise, and a stroke walked through that turns
 * into a tangle. So below a confidence floor the field is laid over with a
 * single diagonal, which is what a painter does with a ground anyway.
 */
export const painterly_flow_frag = `
precision highp float;
precision highp sampler2D;

uniform sampler2D tTensor;
uniform vec2 uTexSize;
uniform float uFlowFloor;
uniform float uGroundWander;

float hash21(const in vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float valueNoise(const in vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash21(i), hash21(i + vec2(1.0, 0.0)), f.x),
               mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), f.x), f.y);
}

void main(void) {
    vec2 coords = gl_FragCoord.xy / uTexSize;
    vec4 t = texture2D(tTensor, coords);
    float E = t.x, F = t.y, G = t.z;

    float tr = E + G;
    float disc = sqrt(max(0.0, (E - G) * (E - G) + 4.0 * F * F));
    float l1 = 0.5 * (tr + disc);
    float l2 = 0.5 * (tr - disc);

    // The published closed form for the major eigenvector, (l1 - E, -F),
    // collapses to the null vector wherever F is zero — which is not an edge
    // case in a molecular figure, it is every pixel of a helix drawn
    // horizontally. The two rows of (J - l1*I) each degenerate exactly where
    // the other does not, so take the better-conditioned one. Checked by hand:
    // a pure-x gradient gives a tangent of (0,1), a pure-y gradient (-1,0), and
    // E=2,F=1,G=1 gives an eigenvector satisfying J*v = l1*v.
    vec2 vA = vec2(l1 - G, F);
    vec2 vB = vec2(F, l1 - E);
    vec2 major = dot(vA, vA) >= dot(vB, vB) ? vA : vB;
    float mlen = length(major);
    vec2 tang = mlen > 1e-8 ? vec2(-major.y, major.x) / mlen : vec2(1.0, 0.0);
    float aniso = tr > 1e-8 ? (l1 - l2) / tr : 0.0;

    // A ground laid in on the diagonal. Not a fallback so much as the
    // convention: an unpainted area of canvas gets blocked in with one
    // consistent sweep, and the alternative — whatever the noise floor happens
    // to say — is a tangle.
    //
    // Wandering, though, and slowly. A single ruled direction across a whole
    // frame is the one thing here that reads as machinery rather than as a
    // hand, and the wander costs two noise lookups.
    if (aniso < uFlowFloor) {
        vec2 q = gl_FragCoord.xy / uGroundWander;
        vec2 drift = vec2(valueNoise(q) - 0.5, valueNoise(q + vec2(7.3, 2.1)) - 0.5);
        tang = normalize(vec2(0.8660254, 0.5) + 0.7 * drift);
    }

    gl_FragColor = vec4(tang * 0.5 + 0.5, aniso, t.w);
}
`;

/** The brush itself: abstraction, bristle, impasto, glaze, canvas.
 *
 * ## Why abstraction alone is not a painting
 *
 * The first version of this pass was anisotropic Kuwahara and nothing else, on
 * the grounds that it is what a painterly filter is made of. Rendered, it gave
 * back a clean cartoon with a slightly softer silhouette, and the reason is
 * worth writing down: **Kuwahara abstracts texture that is already there.**
 * Every published painterly filter is demonstrated on a photograph, where the
 * grass and the brickwork and the skin supply the variation the sectors sort
 * through. A Mol\\* cartoon supplies none — it is a smooth surface under a
 * smooth light, and the honest output of an abstraction filter over it is the
 * same smooth surface.
 *
 * So the paint has to be *made*, and it is made here in three layers over the
 * abstraction:
 *
 * - **Bristle.** Value noise dragged along the flow field by line-integral
 *   convolution, which is what turns isotropic noise into streaks that follow
 *   the form. This is the mark of the brush.
 * - **Impasto.** The same streak field read as a height, and relit by a raking
 *   light fixed in screen space. This is the thickness of the paint, and it is
 *   the single strongest signal that a surface is oil rather than print.
 * - **Canvas.** A woven ground beneath both, buried where the paint is thick.
 *
 * The raking light is screen-fixed on purpose: a relief that rotated with the
 * molecule would read as a bug, and light from the upper left is a painting
 * convention that stays put while the subject turns.
 *
 * ## What each loop costs
 *
 * The disc walk is `(2*dSamples+1)^2` positions with about pi/4 of them inside,
 * and the stroke walk is `2*dStroke` steps. Both bounds are `#define`s resolved
 * from the frame, so the cost is set by the size of the picture and not by how
 * broadly it is being painted.
 */
export const painterly_brush_frag = `
precision highp float;
precision highp sampler2D;

uniform sampler2D tColor;
uniform sampler2D tFlow;
uniform vec2 uTexSize;
uniform float uRadius;
uniform float uAlpha;
uniform float uHardness;
uniform float uVarRef;
uniform float uDepthFalloff;
uniform float uStroke;
uniform float uGrain;
uniform float uBristle;
uniform float uRelief;
uniform float uFar;
uniform float uGroundPaint;
uniform float uGlaze;
uniform vec3 uGlazeColor;
uniform float uHighlight;
uniform vec3 uHighlightColor;
uniform float uEdgeDark;
uniform float uWeaveDepth;
uniform float uWeavePitch;
uniform float uDabSpacing;
uniform float uDabRadius;
uniform float uDabJitter;
uniform float uDabChroma;
uniform float uDabSizeVariance;

const float TAU = 6.283185307;
// Upper left, and never anywhere else. Fixed in screen space so the relief
// stays put while the molecule turns.
const vec3 RAKING = vec3(-0.5504, 0.6205, 0.5580);

float hash21(const in vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// A better-behaved hash than the one above, which carries a faint diagonal
// bias in the direction of its own (127.1, 311.7) constant — invisible at
// the coarse scale hash21 was first used at (a canvas weave, a bristle
// noise field, both smoothed by interpolation before they reach a pixel),
// not invisible at the fine, dense lattice a dab reads unsmoothed and
// directly as a position and a colour. Used only there.
float hash21b(const in vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec2 rotate2(const in vec2 p, const in float a) {
    float c = cos(a);
    float s = sin(a);
    return vec2(c * p.x - s * p.y, s * p.x + c * p.y);
}

float threadHash(const in float i) {
    return fract(sin(i * 12.9898) * 43758.5453);
}

/** Smooth value noise. Screen position only — never a frame counter, never a
 *  clock. Mol* accumulates frames whenever the camera stops, so anything
 *  seeded from time is averaged to mush at exactly the moment somebody stops
 *  moving in order to look. */
float valueNoise(const in vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

/** Which cell a position falls in, scrambled — not the position itself.
 *
 * A jittered lattice keeps its topology no matter how hard the jitter
 * pushes: every point still has the same handful of neighbours at the same
 * rough spacing, because the *partition* — which region of the screen maps
 * to which cell — is still a perfectly periodic grid underneath the jitter.
 * Two, let alone three, periodic partitions laid over each other do not
 * erase that: their boundary is itself close to periodic, and the ripple
 * that makes visible is a beat between two regular structures, not noise.
 *
 * Warping the position *before* it is floored into a cell breaks the
 * partition itself rather than moving points around inside it — cascaded at
 * two unrelated scales (an ex-Quilez trick) so neither octave leaves its own
 * residue. Used only to choose a cell index; every other calculation for
 * that cell (its jittered centre, its colour) stays in real screen space, so
 * a dab is still a real, sample-able point and not a warped illusion of one.
 */
vec2 scrambleCell(const in vec2 p, const in float spacing) {
    vec2 w = p + (vec2(valueNoise(p / (spacing * 2.6) + 5.0), valueNoise(p / (spacing * 2.6) + 71.0)) - 0.5)
        * spacing * 1.7;
    w += (vec2(valueNoise(w / (spacing * 0.85) + 133.0), valueNoise(w / (spacing * 0.85) + 271.0)) - 0.5)
        * spacing * 0.8;
    return w;
}

// Slub hashed on the thread index rather than on the pixel. Per-pixel noise is
// grain; per-thread variation is cloth. This is the one line that separates
// linen from corduroy.
float weaveHeight(const in vec2 P, const in float pitch) {
    vec2 t = P / pitch;
    float wi = floor(t.x);
    float fi = floor(t.y);

    float wThick = 0.62 + 0.30 * threadHash(wi * 1.7);
    float fThick = 0.62 + 0.30 * threadHash(fi * 2.3 + 11.0);
    float wWander = (threadHash(wi * 5.1 + 3.0) - 0.5) * 0.22;
    float fWander = (threadHash(fi * 7.3 + 5.0) - 0.5) * 0.22;

    float u = fract(t.x) - 0.5 - wWander;
    float v = fract(t.y) - 0.5 - fWander;
    float warp = cos(clamp(u / wThick, -0.5, 0.5) * 3.14159265);
    float weft = cos(clamp(v / fThick, -0.5, 0.5) * 3.14159265);

    // Plain weave: over and under, alternating on cell parity. The buried
    // thread still shows, lower — that is what makes it a weave and not a grid.
    float over = mod(wi + fi, 2.0);
    return max(mix(weft, warp, over), mix(warp, weft, over) * 0.55);
}

// Straight colour and coverage, from a texel centre.
//
// Snapped rather than sampled at the offset itself, because the source's own
// filter is not ours to choose: the accumulated multisample target is
// nearest-filtered and the plain draw target is linear
// (passes/multi-sample.js:52 against passes/draw.js:46), so an unsnapped fetch
// would quietly change character with a setting that has nothing to do with
// this. It also costs nothing: the brush is a statistic over a region, and a
// statistic wants samples, not interpolation.
vec4 fetchAt(const in vec2 pixel) {
    vec2 coords = (floor(pixel) + 0.5) / uTexSize;
    vec4 c = texture2D(tColor, coords);
    return vec4(c.a > 0.0 ? c.rgb / c.a : vec3(0.0), c.a);
}

vec4 flowAt(const in vec2 pixel) {
    vec4 f = texture2D(tFlow, (floor(pixel) + 0.5) / uTexSize);
    return vec4(f.xy * 2.0 - 1.0, f.z, f.w);
}

/** One half of a streak: walk the flow field and gather noise along it.
 *
 * maxw counts the weight a *complete* walk would have carried, whether or not
 * this one got there. The ratio is the confidence in the streak, and it is what
 * keeps the silhouette clean: a walk that starts on the ground beside the
 * molecule stops after two steps, and two samples of noise average to whatever
 * those two samples happened to be — an extreme value, relit into a bright ring
 * hugging the subject. The first render of this had that ring and it read as a
 * halo, which is the one artefact that says "filter" out loud.
 */
void march(const in vec2 P0, const in float sgn, const in float z0,
           inout float acc, inout float wsum, inout float maxw) {
    vec2 P = P0;
    vec2 prev = flowAt(P0).xy * sgn;
    bool stopped = false;
    for (int i = 1; i <= dStroke; ++i) {
        if (float(i) > uStroke) break;
        // Fuller than triangular: a stroke has a body, not only a peak.
        float w = pow(1.0 - float(i) / uStroke, 0.5);
        maxw += w;
        if (stopped) continue;
        vec4 f = flowAt(P);
        vec2 t = f.xy;
        // The field is a direction, not a vector: its sign comes back
        // arbitrarily from texel to texel. March without this and every
        // streamline folds back on itself after a few steps, giving short
        // hooked marks — the signature failure of every naive oil filter, and
        // the reason they all look like worms.
        if (dot(t, prev) < 0.0) t = -t;
        prev = t;
        P += t;
        if (abs(flowAt(P).w - z0) > uDepthFalloff * 3.0) {
            stopped = true;
            continue;
        }
        acc += valueNoise(P / uGrain) * w;
        wsum += w;
    }
}

void main(void) {
    vec2 coords = gl_FragCoord.xy / uTexSize;
    vec4 f = texture2D(tFlow, coords);
    vec2 tang = f.xy * 2.0 - 1.0;
    float aniso = f.z;
    float z0 = f.w;

    float along = (uAlpha + aniso) / uAlpha;
    float across = uAlpha / (uAlpha + aniso);

    // The ellipse in pixels: rotate onto the flow, then scale. Built forwards
    // and applied to unit-disc offsets, so no matrix inverse is needed — GLSL
    // ES 1.00 has none.
    vec2 axisA = tang * (uRadius * along);
    vec2 axisB = vec2(-tang.y, tang.x) * (uRadius * across);

    // Colour and coverage together: alpha has to come through the same filter
    // the colour does. Mol*'s renderer outputs premultiplied alpha and every
    // capture divides by it unconditionally (passes/image.js:145), so a pass
    // that writes 1 here turns a transparent snapshot opaque and reports
    // success — and one that writes straight colour has it divided out and
    // blown to white.
    vec4 here = fetchAt(gl_FragCoord.xy);
    vec3 col;
    float alpha;

    if (uDabSpacing > 0.0) {
        // -- a jittered lattice of dabs, in place of the continuous Kuwahara
        // abstraction below. Colour is sampled once per dab, at its own
        // jittered centre, and held flat over the whole disc — a dab modulates
        // *colour*, never area, which is the one structural fact that keeps
        // this from reading as spot-ink-plates with softer edges. Ungated by
        // depth, unlike the bristle further down: a real divisionist canvas
        // is dabs everywhere, ground included — the water and the grass in
        // Seurat's own paintings are built from points exactly as much as the
        // figures are, and painting the subject onto a smooth background
        // reads as a sticker rather than as the technique.
        // No backticks in this comment or the ones below, deliberately: the
        // whole shader is a TypeScript template literal and one would end it.
        vec2 P = gl_FragCoord.xy;
        // bestScore, not bestD, decides the winner — see below, where a
        // dab's own size is folded into the contest rather than only into
        // how big it draws once it has already won.
        float bestScore = 1.0e6;
        float bestD = 1.0e6;
        float bestRadius = uDabRadius * uDabSpacing;
        vec3 dabColor = here.rgb;
        float dabAlpha = here.a;

        // Nine lattices, independently rotated, offset and scaled, unioned by
        // nearest point — not one lattice jittered harder. A single jittered
        // grid keeps its topology no matter how far each point wanders
        // inside its own cell: the same rough neighbour count at the same
        // rough spacing is what the eye reads as a repeat, and jitter alone
        // never touches it. Two or three unioned layers still beat visibly
        // against each other — a beat is what any two periodic structures do
        // when combined, however they are jittered — so this needed both
        // more layers and a principled separation between them: each layer's
        // angle is the last plus the golden angle (about 137.5 degrees), the
        // same spacing that keeps sunflower seeds from ever lining up into
        // rows, because it is about as far from any simple fraction of a
        // turn as an angle can be. Scales spread the same way, by golden-
        // ratio powers, so no layer's spacing is a simple multiple of
        // another's either. Measured, not assumed: an FFT of the rendered
        // background's periodicity dropped from 7.5 at five layers to 6.2 at
        // nine — see the divisionist-anti-moire workflow run for the
        // comparison against three other candidate techniques.
        const int LAYERS = 9;
        float layerAngle[LAYERS];
        layerAngle[0] = 0.0;
        layerAngle[1] = 2.399963;
        layerAngle[2] = 4.799926;
        layerAngle[3] = 7.199889;
        layerAngle[4] = 9.599852;
        layerAngle[5] = 11.999815;
        layerAngle[6] = 14.399778;
        layerAngle[7] = 16.799741;
        layerAngle[8] = 19.199704;
        float layerScale[LAYERS];
        layerScale[0] = 0.55;
        layerScale[1] = 0.65;
        layerScale[2] = 0.78;
        layerScale[3] = 0.92;
        layerScale[4] = 1.0;
        layerScale[5] = 1.08;
        layerScale[6] = 1.22;
        layerScale[7] = 1.38;
        layerScale[8] = 1.55;
        vec2 layerOffset[LAYERS];
        layerOffset[0] = vec2(0.0, 0.0);
        layerOffset[1] = vec2(31.7, 11.3);
        layerOffset[2] = vec2(-17.1, 53.9);
        layerOffset[3] = vec2(67.3, -29.4);
        layerOffset[4] = vec2(-41.8, -63.2);
        layerOffset[5] = vec2(83.1, 42.6);
        layerOffset[6] = vec2(-59.4, 19.8);
        layerOffset[7] = vec2(12.9, -77.3);
        layerOffset[8] = vec2(-93.2, -8.4);

        for (int layer = 0; layer < LAYERS; ++layer) {
            float spacing = uDabSpacing * layerScale[layer];
            vec2 lp = rotate2(scrambleCell(P, spacing), layerAngle[layer]) + layerOffset[layer];
            vec2 cell = floor(lp / spacing);
            for (int j = -1; j <= 1; ++j) {
                for (int i = -1; i <= 1; ++i) {
                    vec2 c = cell + vec2(float(i), float(j));
                    // Seeded on the layer too, so the three lattices draw
                    // independent jitter and colour rather than the same
                    // pattern rotated three times.
                    vec2 seed = c + float(layer) * 97.13;
                    // Two independent hashes of the same seed, not one hash
                    // read twice: hash21 is a scalar function, and reusing
                    // its output for both axes would jitter every dab along
                    // the diagonal only.
                    vec2 jitter = (vec2(hash21b(seed), hash21b(seed + 91.7)) - 0.5) * uDabJitter;
                    vec2 centerLocal = (c + 0.5 + jitter) * spacing;
                    // Back to real screen space, so the distance test and the
                    // colour fetch below both stay honest about where a pixel
                    // actually is.
                    vec2 center = rotate2(centerLocal - layerOffset[layer], -layerAngle[layer]);
                    float d = distance(P, center);
                    // A dab's own size, drawn once per dab like its colour —
                    // and it has to change which dab *wins* a pixel, not only
                    // how big the winner then draws. A power diagram rather
                    // than a plain Voronoi one: size is folded into the
                    // contest as a bonus subtracted from distance, so a
                    // larger dab reaches out and wins ground a same-distance
                    // smaller dab would have kept, and two different-sized
                    // neighbours meet at a boundary shaped by both of them
                    // rather than a smaller one clipping a bigger one's
                    // circle in two.
                    // Ceiling at the baseline itself, not above it: a dab
                    // only ever shrinks from here, never grows past it,
                    // because a genuinely large one reads as a blob and
                    // muddies the picture — measured by bracketing four
                    // ceilings from 1.28x down to 1.0x and this is the one
                    // that was chosen. Squaring the hash before the mix
                    // biases the draw toward the floor rather than sampling
                    // it evenly, so most dabs land well under the baseline
                    // and the odd one reaches it.
                    float sizeDraw = hash21b(seed + 571.0);
                    float sizeFactor = mix(1.0 - uDabSizeVariance, 1.0, sizeDraw * sizeDraw);
                    float radius = uDabRadius * spacing * sizeFactor;
                    float score = d - (sizeFactor - 1.0) * spacing * 0.6;
                    if (score < bestScore) {
                        bestScore = score;
                        bestD = d;
                        bestRadius = radius;
                        vec4 base = fetchAt(center);
                        // Three more hashes of the seed, one per channel, so
                        // the perturbation is a colour and not a brightness —
                        // a shared scalar tint would still draw one RGB per
                        // flat region, which is exactly the failure this look
                        // exists to avoid.
                        vec3 tint = vec3(hash21b(seed + 3.0), hash21b(seed + 41.0), hash21b(seed + 197.0));
                        dabColor = clamp(base.rgb + (tint - 0.5) * 2.0 * uDabChroma, 0.0, 1.0);
                        dabAlpha = base.a;
                    }
                }
            }
        }
        // Soft disc edge, not a hard circle — an aliased dab boundary is the
        // signature of a filter, not a mark. bestRadius is the winning dab's
        // own radius (uDabRadius scaled by its layer's spacing and its own
        // size factor).
        float edge = smoothstep(bestRadius, bestRadius * 0.88, bestD);
        col = mix(here.rgb, dabColor, edge);
        alpha = mix(here.a, dabAlpha, edge);
    } else {
        vec4 mean[8];
        float lsum[8];
        float l2sum[8];
        float nsum[8];
        for (int k = 0; k < 8; ++k) {
            mean[k] = vec4(0.0);
            lsum[k] = 0.0;
            l2sum[k] = 0.0;
            nsum[k] = 0.0;
        }

        for (int j = -dSamples; j <= dSamples; ++j) {
            for (int i = -dSamples; i <= dSamples; ++i) {
                vec2 u = vec2(float(i), float(j)) / float(dSamples);
                float r2 = dot(u, u);
                if (r2 > 1.0) continue;

                vec2 pixel = gl_FragCoord.xy + u.x * axisA + u.y * axisB;
                vec4 tap = fetchAt(pixel);
                vec3 rgb = tap.rgb;

                // The same silhouette guard the tensor smoothing uses. Without it
                // the abstraction is also a bleed, and the subject grows a halo of
                // its own colour against the ground.
                float wz = exp(-abs(flowAt(pixel).w - z0) / uDepthFalloff);
                float wr = exp(-r2 / (2.0 * 0.4 * 0.4));
                float L = dot(rgb, vec3(0.299, 0.587, 0.114));
                // The centre tap has no angle: atan(0.0, 0.0) is undefined in
                // GLSL, and on a real driver it came back as NaN, which poisoned
                // every sum in the pixel and arrived as **alpha zero**. That is not
                // a visible artefact: it is one transparent pixel in a frame, and
                // snapshot() refuses the whole capture on it, because on an
                // opaque canvas a transparent pixel means part of the image was
                // never rendered. Found by a 1890px plate of haemoglobin.
                bool centred = r2 < 1e-12;
                float ang = centred ? 0.0 : atan(u.y, u.x);

                for (int k = 0; k < 8; ++k) {
                    float delta = ang - TAU * float(k) / 8.0;
                    delta = mod(delta + 3.14159265, TAU) - 3.14159265;
                    // Hann windows two sectors wide at 50% overlap are a partition
                    // of unity: they sum to exactly 1, so no tap is double counted
                    // and no sector boundary is a seam. A hard pie slice leaves a
                    // faint eight-pointed star on every flat region, which is the
                    // most recognisable way this filter looks wrong.
                    // The centre belongs to every sector equally, at an eighth
                    // each — which keeps the same total of 1 without pretending it
                    // points somewhere.
                    float wa = centred
                        ? 0.125
                        : (abs(delta) < TAU / 8.0 ? 0.5 + 0.5 * cos(delta * 4.0) : 0.0);
                    float w = wa * wr * wz;
                    mean[k] += tap * w;
                    lsum[k] += L * w;
                    l2sum[k] += L * L * w;
                    nsum[k] += w;
                }
            }
        }

        vec4 num = vec4(0.0);
        float den = 0.0;
        for (int k = 0; k < 8; ++k) {
            if (nsum[k] <= 0.0) continue;
            vec4 mu = mean[k] / nsum[k];
            float lmu = lsum[k] / nsum[k];
            // Luminance variance rather than the sum of per-channel variances. A
            // structure coloured by chain has hue boundaries with no luminance
            // step; on RGB variance the brush refuses to work across them and
            // leaves a hard plastic seam at every one.
            float variance = max(0.0, l2sum[k] / nsum[k] - lmu * lmu);
            // Divided by a reference variance before the exponent bites. Written
            // this way because the published formula (Kyprianidis & Doellner) is
            // for 0-255 values: on the [0,1] the shader carries, raw variance
            // never leaves the flat top of the curve — at hardness 8 its entire
            // range is 1.0000000 to 0.9999847, which is a Gaussian blur wearing a
            // Kuwahara's name.
            //
            // uVarRef is therefore where a look chooses how much abstraction it
            // wants, and 1.0 is exactly the ungoverned form: verified bit-for-bit
            // at every sample. chiaroscuro asks for 1.0 on purpose.
            //
            // No backticks in this comment, deliberately: the whole shader is a
            // TypeScript template literal and one would end it here.
            float scaled = variance / (uVarRef * uVarRef);
            float w = 1.0 / (1.0 + pow(scaled, 0.5 * uHardness));
            num += mu * w;
            den += w;
        }

        // A second guard on the same failure, and independent of the first: if
        // the brush found nothing to average — every tap in the disc across a
        // depth break from an isolated pixel — the answer is the pixel itself,
        // not black and certainly not transparent.
        vec4 painted = den > 1e-5 ? num / den : here;
        col = painted.rgb;
        alpha = clamp(painted.a, 0.0, 1.0);
    }

    // -- the mark of the brush ------------------------------------------------
    float acc = valueNoise(gl_FragCoord.xy / uGrain);
    float wsum = 1.0;
    float maxw = 1.0;
    march(gl_FragCoord.xy, 1.0, z0, acc, wsum, maxw);
    march(gl_FragCoord.xy, -1.0, z0, acc, wsum, maxw);
    // Centred on zero and stretched: line-integral convolution of uniform noise
    // regresses hard toward its mean, so the raw result is a flat grey with a
    // whisper of streak in it.
    float streak = clamp((acc / wsum - 0.5) * 3.2, -1.0, 1.0);
    // Faded out where the walk was cut short, so a two-sample average never
    // arrives as a full-strength mark.
    streak *= smoothstep(0.30, 0.80, wsum / maxw);

    // The ground is a ground. It is primed and blocked in, not worked — and the
    // first render of this pass put full impasto across the whole frame, which
    // buried the molecule under its own background. The tensor pass parks the
    // background beyond four times the far plane, so this is a clean test and
    // not a threshold anybody has to tune.
    float onPaint = z0 > -2.0 * uFar ? 1.0 : uGroundPaint;

    col *= 1.0 + uBristle * onPaint * streak;

    // -- the thickness of the paint -------------------------------------------
    // A height field from the same streaks, relit. Applied to luminance rather
    // than added as light, so the relief cannot invent a colour the molecule
    // does not have.
    if (uRelief > 0.0) {
        vec2 slope = vec2(dFdx(streak), dFdy(streak)) * uRelief * onPaint;
        vec3 n = normalize(vec3(-slope, 1.0));
        float lambert = max(0.0, dot(n, RAKING));
        float spec = pow(max(0.0, dot(reflect(-RAKING, n), vec3(0.0, 0.0, 1.0))), 24.0);
        // Faded out with onPaint rather than only its slope, so that bare ground
        // keeps the colour it was given. Relighting a flat surface still
        // multiplies it — by 0.93 here — and a ground that comes back 7% darker
        // than the colour the caller asked for is a small lie the reply cannot
        // see.
        col *= mix(1.0, 0.62 + 0.55 * lambert, onPaint);
        col += 0.16 * spec * alpha * onPaint;
    }

    float L = dot(col, vec3(0.299, 0.587, 0.114));

    // A shadow glaze is transparent, so it goes deep and coloured; a highlight
    // is opaque lead white, so it goes light and desaturated. Pulling the darks
    // toward grey instead is what makes a painterly filter read as a dirty
    // photograph.
    float glaze = smoothstep(0.38, 0.04, L) * uGlaze;
    col = mix(col, col * uGlazeColor, glaze);

    float lit = smoothstep(0.70, 0.96, L) * uHighlight;
    col = mix(col, mix(col, uHighlightColor, 0.55), lit);

    // Darkened where the paint has structure rather than along the silhouette.
    // A depth outline darkens the outside edge only, and reads as a sticker.
    // On the paint, though: the ground has no structure to find, and darkening
    // the background right at the silhouette draws a grey ring around the
    // subject that reads as a halo rather than as a drawn edge.
    col *= 1.0 - uEdgeDark * onPaint * smoothstep(0.25, 0.75, aniso);

    if (uWeaveDepth > 0.0) {
        float h = weaveHeight(gl_FragCoord.xy, uWeavePitch);
        // On luminance only. Canvas does not tint paint, it shades it, and
        // tinting is what makes a weave overlay read as a stock texture.
        // Buried under thick paint: without that, the most loaded passage is
        // also the most textile, which is backwards and gives the whole thing
        // away as a filter.
        float buried = 1.0 - 0.7 * clamp(abs(streak), 0.0, 1.0);
        col *= 1.0 - uWeaveDepth * buried * (1.0 - h);
    }

    // Premultiplied, because that is what Mol* hands on and what every consumer
    // downstream assumes.
    gl_FragColor = vec4(clamp(col, 0.0, 1.0) * alpha, alpha);
}
`;
