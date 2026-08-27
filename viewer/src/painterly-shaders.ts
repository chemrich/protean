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
 * The disc walk is (2*dSamples+1)^2 positions with about pi/4 of them inside,
 * and it is the only loop left: laying a stroke is a lattice lookup, where
 * dragging noise along the flow was a march. The bound is a define resolved
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
uniform float uStrokeLen;
uniform float uStrokeWidth;
uniform float uStrokeFill;
uniform float uBristle;
uniform float uRidge;
uniform float uRelief;
uniform float uSpecular;
uniform float uFar;
uniform float uGroundPaint;
uniform float uGlaze;
uniform vec3 uShadowColor;
uniform float uShadowFrom;
uniform float uShadowTo;
uniform float uHighlight;
uniform vec3 uHighlightColor;
uniform float uLightFrom;
uniform float uLightTo;
uniform float uShade;
uniform float uChroma;
uniform float uWeaveDepth;
uniform float uWeavePitch;

const float TAU = 6.283185307;
// Upper left, and never anywhere else. Fixed in screen space so the relief
// stays put while the molecule turns.
const vec3 RAKING = vec3(-0.5504, 0.6205, 0.5580);
// What the raking light gives a pixel with no paint standing off it — which is
// just RAKING.z, since a flat surface's normal is (0,0,1). Named, because the
// relight below is centred on it and the two must not drift apart.
// How hard the relief swings brightness about unity. One number rather than a
// look field: a look already says how thick its paint is, through its own
// relief, which decides how far the surface tilts and so how far the swing
// goes. A second knob over the same effect is a second thing to get out of step.
const float RELIEF_CONTRAST = 0.55;

float hash21(const in vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
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

// -- colour, in a space where chroma and hue come apart -----------------------
//
// Oklab (Bjorn Ottosson, 2020) rather than HSV, and the reason is protean's
// rather than a graphics one: **these colours mean something.** The ribbon is
// coloured by secondary structure, so a boost that rotated hue would make a
// helix read as a strand. Scaling (a, b) in Oklab cannot move a hue — the hue
// is atan(b, a) and scaling both leaves it exactly — and cannot move
// lightness, because that is L. An HSV or a mix-from-luma boost does both the
// moment a channel clips.

float srgbToLinear(const in float c) {
    return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4);
}

float linearToSrgb(const in float c) {
    return c <= 0.0031308 ? c * 12.92 : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

vec3 srgbToOklab(const in vec3 srgb) {
    vec3 c = vec3(srgbToLinear(srgb.r), srgbToLinear(srgb.g), srgbToLinear(srgb.b));
    float l = 0.4122214708 * c.r + 0.5363325363 * c.g + 0.0514459929 * c.b;
    float m = 0.2119034982 * c.r + 0.6806995451 * c.g + 0.1073969566 * c.b;
    float s = 0.0883024619 * c.r + 0.2817188376 * c.g + 0.6299787005 * c.b;
    // Cube root, sign-preserving. A negative here means the colour was already
    // outside the sRGB gamut, which the un-premultiply can produce.
    vec3 n = vec3(
        sign(l) * pow(abs(l), 1.0 / 3.0),
        sign(m) * pow(abs(m), 1.0 / 3.0),
        sign(s) * pow(abs(s), 1.0 / 3.0)
    );
    return vec3(
        0.2104542553 * n.x + 0.7936177850 * n.y - 0.0040720468 * n.z,
        1.9779984951 * n.x - 2.4285922050 * n.y + 0.4505937099 * n.z,
        0.0259040371 * n.x + 0.7827717662 * n.y - 0.8086757660 * n.z
    );
}

vec3 oklabToSrgb(const in vec3 lab) {
    float l_ = lab.x + 0.3963377774 * lab.y + 0.2158037573 * lab.z;
    float m_ = lab.x - 0.1055613458 * lab.y - 0.0638541728 * lab.z;
    float s_ = lab.x - 0.0894841775 * lab.y - 1.2914855480 * lab.z;
    float l = l_ * l_ * l_;
    float m = m_ * m_ * m_;
    float s = s_ * s_ * s_;
    vec3 c = vec3(
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    );
    return vec3(linearToSrgb(c.r), linearToSrgb(c.g), linearToSrgb(c.b));
}

bool inGamut(const in vec3 c) {
    return all(greaterThanEqual(c, vec3(-0.001))) && all(lessThanEqual(c, vec3(1.001)));
}

/** More of the colour it already is: hue and lightness held, chroma scaled.
 *
 * The clamp is a bisection on chroma rather than a clamp on channels. Clamping
 * channels is what rotates hue — clip blue first and a violet arrives magenta —
 * so instead the chroma is walked back until the colour fits, which keeps the
 * hue angle exact and only gives up intensity. Four steps is under 1% error.
 */
vec3 boostChroma(const in vec3 rgb, const in float k) {
    if (k == 1.0) return rgb;
    vec3 lab = srgbToOklab(rgb);
    vec2 ab = lab.yz * k;
    vec3 out0 = oklabToSrgb(vec3(lab.x, ab));
    if (inGamut(out0)) return clamp(out0, 0.0, 1.0);

    float lo = 1.0 / max(k, 1e-4);   // the original chroma always fits
    float hi = 1.0;
    for (int i = 0; i < 4; ++i) {
        float mid = 0.5 * (lo + hi);
        if (inGamut(oklabToSrgb(vec3(lab.x, ab * mid)))) lo = mid;
        else hi = mid;
    }
    return clamp(oklabToSrgb(vec3(lab.x, ab * lo)), 0.0, 1.0);
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

/** The strokes over this pixel: marks laid down, not a texture sampled.
 *
 * **A stroke is a shape, and it has to be placed rather than derived.** Three
 * versions preceded this and each failed in its own way for one reason — they
 * all asked a *field* what the paint was doing at a pixel, and a field has no
 * beginnings, ends or edges:
 *
 *   1. isotropic noise dragged a little way along the flow and relit — came
 *      back as crumpled foil;
 *   2. noise stretched along the flow before dragging — came back as sandpaper;
 *   3. a lattice in the flow's own frame, from dot(P, tangent) — which looks right
 *      and is not. P is an absolute screen coordinate of order a thousand, so a
 *      tangent rotation of a hundredth of a radian moves the coordinate by a
 *      whole stroke width. The lattice dissolves into noise before it can be a
 *      mark, and the picture is sandpaper again for a completely different
 *      reason.
 *
 * So the marks are *splatted*. An isotropic jittered lattice at the stroke's
 * own spacing; each cell carries one stroke, centred at its jittered point and
 * oriented by the flow **at that centre** rather than at the pixel — which is
 * what makes the orientation a property of the mark instead of a field the mark
 * is read out of. Nine cells are tested and the nearest paint wins.
 *
 * Returns (tone, height, coverage): the stroke's own tone, so the paint varies
 * mark to mark rather than pixel to pixel; a ridged height for the raking light
 * to find an edge on; and how much paint is actually here, so the ground shows
 * between the marks.
 */
vec3 strokeAt(const in vec2 P) {
    // The lattice is spaced by the stroke's *width*, not its length, or the
    // marks never tile across a ribbon and the paint reads as scattered dashes
    // on bare colour. Length then overlaps its neighbours along the flow, which
    // is what a passage of brushwork actually is.
    //
    // Nine cells are searched, so a mark may reach one cell out and no further.
    // The length is clamped to that rather than trusted: a longer one would be
    // dropped wherever its centre fell outside the window, which shows up as
    // marks that vanish at their own ends.
    float spacing = max(2.0, uStrokeWidth * 1.5);
    float reach = 1.35 * spacing;
    vec2 home = floor(P / spacing);
    vec3 best = vec3(0.5, 0.0, 0.0);

    for (int j = -1; j <= 1; ++j) {
        for (int i = -1; i <= 1; ++i) {
            vec2 c = home + vec2(float(i), float(j));
            float k = dot(c, vec2(1.0, 57.31));
            vec2 jitter = vec2(threadHash(k), threadHash(k + 19.7)) - 0.5;
            vec2 centre = (c + 0.5 + 0.9 * jitter) * spacing;

            // The flow where the brush was put down, not where we are looking.
            vec2 t = texture2D(tFlow, (floor(centre) + 0.5) / uTexSize).xy * 2.0 - 1.0;
            if (dot(t, t) < 1e-6) t = vec2(1.0, 0.0);
            t = normalize(t);

            vec2 d = P - centre;
            // Length and width vary mark to mark: a hand does not repeat.
            float half_len = min(0.5 * uStrokeLen, reach) * uStrokeFill
                * (0.72 + 0.56 * threadHash(k + 3.3));
            float half_wid = 0.5 * uStrokeWidth * (0.78 + 0.44 * threadHash(k + 7.9));
            float along = dot(d, t) / max(half_len, 1.0);
            float across = dot(d, vec2(-t.y, t.x)) / max(half_wid, 0.75);

            // Elliptical, and squared along so the mark keeps its body and
            // tapers only near the ends — a brush does not fade from its middle.
            float r = along * along * along * along + across * across;
            if (r >= 1.0) continue;

            float cover = 1.0 - r;
            if (cover <= best.z) continue;
            // Across the width: a crest with the paint thinning to nothing at
            // both edges, which is the section a bristle leaves behind it.
            float w = clamp(1.0 - abs(across), 0.0, 1.0);
            float crest = mix(w, w * w * (3.0 - 2.0 * w), uRidge);
            best = vec3(threadHash(k + 41.5), crest * smoothstep(0.0, 0.35, cover), cover);
        }
    }
    return best;
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
    vec4 mean[8];
    float lsum[8];
    float l2sum[8];
    float c2sum[8];
    float nsum[8];
    for (int k = 0; k < 8; ++k) {
        mean[k] = vec4(0.0);
        lsum[k] = 0.0;
        l2sum[k] = 0.0;
        c2sum[k] = 0.0;
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
            // never rendered. Found by a 1890px plate of haemoglobin, on the
            // frames where the geometry happened to put an isolated pixel under
            // the centre of the brush.
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
                //
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
                c2sum[k] += dot(rgb, rgb) * w;
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
        // Luminance spread *and* colour spread, because a palette can be made
        // of isoluminant contrast. Coral against teal is a luminance step of
        // 0.06 and a hue step of nearly everything; on luminance alone the
        // brush reads that boundary as flat, averages across it, and hands
        // back grey. The comment that used to sit here argued the opposite —
        // that RGB variance leaves "a hard plastic seam" at a hue boundary —
        // and it was describing an effect the weight below could not produce
        // at any setting, because the weight did nothing at all. Measure, then
        // assert.
        float variance = max(
            max(0.0, l2sum[k] / nsum[k] - lmu * lmu),
            max(0.0, c2sum[k] / nsum[k] - dot(mu.rgb, mu.rgb)) * 0.5
        );
        // Against a reference spread, and this is the line that makes the pass
        // an *abstraction* rather than a blur.
        //
        // Kyprianidis and Doellner's weight is 1/(1 + sigma^q) on 0-255 values.
        // On the [0,1] values a shader has, sigma^q annihilates: at hardness 8
        // the weight's entire dynamic range across every variance a luminance
        // can have is 1.0000000 to 0.9999847. Every sector was therefore
        // weighted equally, the least-variance selection never happened, and
        // what ran was an anisotropic Gaussian blur wearing the name of a
        // Kuwahara filter. Dividing by a reference spread first gives the
        // exponent something to bite on: at uVarRef 0.03 and hardness 8 the
        // weight runs 0.96 at a spread of 0.02 to 6.6e-5 at 0.10.
        float scaled = variance / (uVarRef * uVarRef);
        float w = 1.0 / (1.0 + pow(scaled, 0.5 * uHardness));
        num += mu * w;
        den += w;
    }

    // A second guard on the same failure, and independent of the first: if the
    // brush found nothing to average — every tap in the disc across a depth
    // break from an isolated pixel — the answer is the pixel itself, not black
    // and certainly not transparent.
    vec4 here = fetchAt(gl_FragCoord.xy);
    vec4 painted = den > 1e-5 ? num / den : here;
    vec3 col = painted.rgb;
    float alpha = clamp(painted.a, 0.0, 1.0);

    // -- the mark of the brush ------------------------------------------------
    vec3 mark = strokeAt(gl_FragCoord.xy);
    // The height field the raking light reads. It keeps its old name because
    // everything downstream — the relight, the weave burial — asks the same
    // question of it: how much paint is standing here.
    float streak = mark.y;

    // The ground is a ground. It is primed and blocked in, not worked — and the
    // first render of this pass put full impasto across the whole frame, which
    // buried the molecule under its own background. The tensor pass parks the
    // background beyond four times the far plane, so this is a clean test and
    // not a threshold anybody has to tune.
    float onPaint = z0 > -2.0 * uFar ? 1.0 : uGroundPaint;

    // Tone varies *by stroke*, not by pixel. That is the whole difference
    // between a loaded brush and a layer of grain.
    col *= 1.0 + uBristle * onPaint * (mark.x - 0.5) * 2.0 * mark.z;

    // -- the thickness of the paint -------------------------------------------
    // A height field from the same streaks, relit. Applied to luminance rather
    // than added as light, so the relief cannot invent a colour the molecule
    // does not have.
    if (uRelief > 0.0) {
        vec2 slope = vec2(dFdx(streak), dFdy(streak)) * uRelief * onPaint;
        vec3 n = normalize(vec3(-slope, 1.0));
        float spec = pow(max(0.0, dot(reflect(-RAKING, n), vec3(0.0, 0.0, 1.0))), 24.0);
        // A swing about unity that is *odd in the slope*, so its mean is zero
        // over any symmetric field of tilts, at any paint thickness, without a
        // constant anyone has to keep in step.
        //
        // Two wrong versions preceded it and both were measured rather than
        // reasoned into. The shipped one, 0.62 + 0.55 * lambert, took **14.3%**
        // off the mean painted pixel — not the 7.3% a flat pixel suggests,
        // because a textured surface tilts a mean 53 degrees off the screen and
        // a third of it has lambert clamped to zero. Recentring that on
        // RAKING.z halved the loss to 7.0% and no further: RAKING.z is the
        // flat-surface value and the surface is not flat. Dropping the clamp
        // and taking the tilt directly is exact by construction — measured mean
        // 1.0011 to 1.0018 across every paint thickness in the table.
        float tilt = dot(vec3(-slope, 0.0), RAKING) / (1.0 + length(slope));
        col *= mix(1.0, 1.0 + RELIEF_CONTRAST * tilt, onPaint);
        // The glint keeps the true lambert — a highlight is a real reflection
        // and has no business being mean-neutral.
        col += uSpecular * spec * alpha * onPaint;
    }

    float L = dot(col, vec3(0.299, 0.587, 0.114));

    // A tint toward a colour, not a multiply by one — and the bands come from
    // the look rather than from a literal.
    //
    // A multiply by a colour can only ever darken, because every component of
    // a colour is at most 1. That is right for a Dutch Master, whose shadow is
    // a transparent brown laid over the ground, and it makes a bright look
    // impossible: there is no value of it that lifts a passage. Tinting toward
    // a *pale periwinkle* is the move a spring palette wants, and it is the
    // same line of code.
    //
    // The bands were literals at 0.38/0.04 and 0.70/0.96, tuned around a
    // subject living at L 0.18-0.40. A bright palette lives at 0.45-0.85, where
    // the shadow never fired at all and the highlight fired on almost nothing —
    // two effects reported as applied and doing nothing, which is this
    // project's whole failure mode in two lines.
    float glaze = smoothstep(uShadowFrom, uShadowTo, L) * uGlaze;
    col = mix(col, uShadowColor, glaze);

    float lit = smoothstep(uLightFrom, uLightTo, L) * uHighlight;
    col = mix(col, uHighlightColor, lit);

    // Deepened where the flow is coherent — which on a ribbon is *most of it*,
    // and that is the honest description rather than the one this line used to
    // carry.
    //
    // It was written as an edge darkening, on the theory that anisotropy marks
    // where the paint has structure. Anisotropy is a measure of *shape*, not of
    // strength: a smooth shaded slope across a cartoon band is as directional
    // as a hard edge, so smoothstep(0.25, 0.75, aniso) saturates over the
    // whole subject. Measured on 1UBQ: switching this off lifts the painted
    // subject's mean luminance from 0.686 of the render to 0.852 and its mean
    // saturation from 0.698 to 0.867. A 22% dim cannot move a mean by 17%
    // unless it is at full strength nearly everywhere.
    //
    // Kept, because it is most of what made the Dutch Master read as painted
    // rather than filtered, and named for what it does so the next look can
    // decide on the evidence.
    col *= 1.0 - uShade * onPaint * smoothstep(0.25, 0.75, aniso);

    if (uWeaveDepth > 0.0) {
        float h = weaveHeight(gl_FragCoord.xy, uWeavePitch);
        // On luminance only. Canvas does not tint paint, it shades it, and
        // tinting is what makes a weave overlay read as a stock texture.
        // Buried under thick paint: without that, the most loaded passage is
        // also the most textile, which is backwards and gives the whole thing
        // away as a filter.
        float buried = 1.0 - 0.7 * clamp(abs(streak), 0.0, 1.0);
        // Centred on the weave's own mean height rather than on its peak, so a
        // canvas texture is a texture and not a 4.3% tax. E[h] is 0.578 for
        // this weave; the one-sided form only ever subtracted.
        col *= 1.0 + uWeaveDepth * buried * (h - 0.578);
    }

    // Put back what the averaging took, and then some.
    //
    // Kuwahara is a mean, and a mean in sRGB desaturates: measured on 1UBQ, the
    // painted subject came back at 0.876 of the render's mean saturation with
    // every other term switched off. So a look that wants its colours to sing
    // has to ask for it. Hue and lightness are held exactly — see boostChroma.
    col = boostChroma(col, uChroma);

    // Premultiplied, because that is what Mol* hands on and what every consumer
    // downstream assumes.
    gl_FragColor = vec4(clamp(col, 0.0, 1.0) * alpha, alpha);
}
`;
