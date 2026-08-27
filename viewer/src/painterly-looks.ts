/**
 * The named looks, and the one number every look resolves from the frame.
 *
 * Apart from `painterly.ts` so that the dispatcher can validate and report a
 * look without pulling Mol\* into its module graph — the unit suite runs the
 * dispatcher in jsdom with a fake plugin, and it must stay able to.
 */

/** A named look: the whole recipe, so a caller picks a painting and not a
 * parameter set. Adding one here is the only place a new look is declared —
 * `capabilities()` reports these keys and `brushwork()` checks against them, so
 * the offer and the gate cannot come apart. */
export interface Look {
  /** How far the shadows are tinted, and the colour they are tinted toward.
   *
   * A *tint*, not a multiply. The multiply this replaced could only ever
   * darken — every component of a colour is at most 1 — which is right for a
   * Dutch Master, whose shadow is a brown laid over the ground, and makes a
   * bright look arithmetically impossible. A pale periwinkle here *lifts* a
   * dark passage, and that is the move that reads as joyful rather than as
   * merely lighter. */
  glaze: number;
  shadowColor: [number, number, number];
  /** Where the shadow tint fades in and reaches full, in luminance. Descending.
   *
   * A look field because it was a literal at 0.38/0.04, tuned around a subject
   * living at L 0.18-0.40 — so on a bright palette living at 0.45-0.85 it never
   * fired at all, and reported itself applied. */
  shadowBand: [number, number];
  /** Weight of the light, and the colour it is tinted toward. */
  highlight: number;
  highlightColor: [number, number, number];
  /** Where the light tint starts and reaches full. Ascending, and a look field
   *  for the same reason: at 0.70/0.96 the brightest thing a Dutch Master ever
   *  drew arrived at 0.676, so it never fired either. */
  lightBand: [number, number];
  /** Deepening wherever the flow field is coherent.
   *
   * Which on a molecular ribbon is nearly the whole subject, so this reads as a
   * general dim rather than as an edge — it was written as the latter and
   * measured as the former. Switching it off on 1UBQ lifts the painted
   * subject's mean luminance from 0.686 of the render to 0.852 and its mean
   * saturation from 0.698 to 0.867, which makes it far and away the most
   * expensive term in the pass. A bright look should spend very little here. */
  shade: number;
  /** How hard the brush commits to the flattest sector it found.
   *
   * Meaningful only against `varRef`. On its own it did nothing whatsoever: the
   * weight is `1/(1 + spread^hardness)` and on the [0,1] values a shader has,
   * that spans 1.0000000 to 0.9999847 across every spread a luminance can have.
   * Every sector was weighted the same, the least-variance choice never
   * happened, and the pass was an anisotropic Gaussian blur wearing a
   * Kuwahara's name. The note that used to sit here — that above 16 it
   * posterises into patches with visible seams — described an effect the
   * arithmetic could not produce at any setting. */
  hardness: number;
  /** The spread `hardness` is measured against, and the number that makes the
   *  selection happen at all. At 0.03 with hardness 8 the weight runs 0.96 at a
   *  spread of 0.02 down to 6.6e-5 at 0.10. */
  varRef: number;
  /** How far the brush stretches along the flow, as `1 + anisotropy`. */
  eccentricity: number;
  /** Depth of the canvas weave, 0 for a smooth ground. */
  weave: number;
  /** Length of a brush stroke, as a fraction of the frame diagonal.
   *
   * The number that decides whether this reads as painting or as a novelty
   * filter. Past about a twentieth of the diagonal the strokes run further than
   * the thing they are describing, which is the classic failure of every "Van
   * Gogh filter" ever shipped. Against `grain`, which is the width, it also
   * sets how much a mark reads as a *stroke* rather than a dab: four or five to
   * one is a brush. */
  stroke: number;
  /** Width of one stroke, as a fraction of the frame diagonal. */
  grain: number;
  /** How much of its cell a stroke actually fills, along its length.
   *
   * Below 1 the marks have ends and the ground shows between them, which is
   * most of what makes a passage read as *painted* rather than *filled*. */
  strokeFill: number;
  /** How sharply the paint crests across a stroke's width. At 0 the section is
   *  a plain wedge; at 1 it is an eased ridge with the paint thinning to
   *  nothing at both edges, which is the section a bristle leaves. */
  ridge: number;
  /** How much a stroke's own tone shifts its *value*.
   *
   * Small, and it has to be. A random brightness per mark on a curved surface
   * is what crumpled foil looks like — random brightness *is* light catching
   * facets at random angles — and at 0.30 that is exactly what it looked
   * like. */
  bristle: number;
  /** How much a stroke's own tone shifts its *chroma*: how loaded the brush
   *  was. This is where the variation belongs, because a brush carries more or
   *  less pigment far more than it carries more or less light. */
  load: number;
  /** How thick the paint stands off the canvas, for the raking light.
   *
   * **Zero for every bright look, and that is the finding rather than a
   * setting.** Charlie, on the first bright plates: *"the ribbons look like
   * crumpled foil or mylar."* They did, and it was this — a relit height field
   * reads as a *metal* surface, because relighting is what tells an eye it is
   * looking at something with a surface normal. Real oil paint on a ribbon
   * reads as paint through its *tone* varying mark to mark, not through
   * catching a light. Taking the relight to zero removed the metal outright.
   * `chiaroscuro` keeps a little because a Dutch Master genuinely is a lit
   * impasto; nothing else should.
   *
   * It reads as a swing about unity, mean-neutral by construction. Quoted as an
   * absolute range it took **14.3%** off the mean painted pixel — not the 7.3% a
   * flat pixel suggests, because a textured surface tilts a mean 53 degrees off
   * the screen and a third of it has its lambert clamped to zero. The look was
   * reported as a Dutch Master and the gloom was blamed on the ground; most of
   * it was here. */
  relief: number;
  /** How wet the paint looks: the specular glint off a ridge. Gouache is
   *  matte and takes near zero; an oil takes some. */
  specular: number;
  /** Chroma, scaled. 1 leaves the colours where the abstraction left them.
   *
   * The abstraction is a mean and a mean desaturates: with every other term off
   * the painted subject came back at 0.876 of the render's saturation. So 1.14
   * is roughly parity and anything above is a decision. Hue and lightness are
   * held exactly, which matters here more than it would elsewhere — the ribbon
   * is coloured by secondary structure, and a boost that rotated hue would make
   * a helix read as a strand. */
  chroma: number;
  /** How much of the brushwork the bare ground gets, 0 to 1.
   *
   * Zero is a real answer and the usual one: the ground is primed canvas, and
   * paint goes where the subject is. Two renders were needed to learn it — at 1
   * the background was full impasto and shouted the molecule down, and at 0.15
   * it still read as fur. */
  groundPaint: number;
}

export const PAINTERLY_LOOKS: Record<string, Look> = {
  // Rembrandt's arrangement rather than Rembrandt's hand: the darks are a
  // transparent brown glaze that goes deeper and *warmer* as it goes down, the
  // lights are opaque lead white, and the paint sits on a visible linen weave.
  // The one number chosen against the alternative rather than from convention
  // is `hardness`: at 16 the brush posterises into hard patches with visible
  // seams, which is the most recognisably filtered outcome available.
  chiaroscuro: {
    glaze: 0.5,
    shadowColor: [0.2, 0.13, 0.08],
    shadowBand: [0.4, 0.06],
    highlight: 0.22,
    highlightColor: [0.965, 0.93, 0.85],
    lightBand: [0.5, 0.85],
    shade: 0.22,
    hardness: 8,
    varRef: 0.03,
    eccentricity: 1,
    weave: 0.13,
    // A twelfth of a Van Gogh. Dutch Master brushwork is *there* — you can see
    // where the brush went — but it describes the form rather than performing.
    stroke: 1 / 26,
    grain: 1 / 165,
    strokeFill: 1.1,
    ridge: 0.55,
    bristle: 0.12,
    load: 0.4,
    relief: 6,
    specular: 0.04,
    chroma: 1.0,
    groundPaint: 0.0,
  },

  // -- the bright ones --------------------------------------------------------
  //
  // Three things separate these from `chiaroscuro`, and only the last is about
  // taste. `shade` is near zero, because it was the most expensive term in the
  // pass and it was spending on a dim nobody asked for. The glaze *tints*
  // rather than darkens: its colour has a blue above 1, so a shadow goes cool
  // and stays where it is instead of going brown and going down. And `chroma`
  // is above 1, because the abstraction is a mean and a mean desaturates.

  // The three bright looks differ in *how much brush you see*, as well as in
  // colour: `spring` is the quietest, `orchard` in between, `poster` the most
  // worked. That is `load` and the mark's width — not `bristle`, which is
  // value, and value is the one thing a brush mark must not vary much.
  //
  // Soft, high-key, luminous. A gouache rather than an oil: matte, no specular,
  // a light weave, and the quietest brush of the three.
  spring: {
    glaze: 0.42,
    shadowColor: [0.62, 0.66, 0.92],
    shadowBand: [0.58, 0.18],
    highlight: 0.4,
    highlightColor: [1.0, 0.972, 0.906],
    lightBand: [0.5, 0.86],
    shade: 0.06,
    hardness: 8,
    varRef: 0.028,
    eccentricity: 1,
    weave: 0.05,
    stroke: 1 / 26,
    grain: 1 / 175,
    strokeFill: 1.15,
    ridge: 0.7,
    bristle: 0.12,
    load: 0.55,
    relief: 0,
    specular: 0.0,
    chroma: 1.5,
    groundPaint: 0.0,
  },

  // Flat and graphic. A harder brush so the colour goes down in patches rather
  // than blending, almost no relief, and the highest chroma of the three.
  poster: {
    glaze: 0.4,
    shadowColor: [0.66, 0.72, 0.95],
    shadowBand: [0.5, 0.16],
    highlight: 0.3,
    highlightColor: [1.0, 0.985, 0.95],
    lightBand: [0.55, 0.9],
    shade: 0.04,
    hardness: 13,
    varRef: 0.02,
    eccentricity: 1,
    weave: 0.04,
    stroke: 1 / 22,
    grain: 1 / 135,
    strokeFill: 1.05,
    ridge: 0.8,
    bristle: 0.24,
    load: 1.2,
    relief: 0,
    specular: 0.0,
    chroma: 1.55,
    groundPaint: 0.0,
  },

  // A painting rather than a print: visible brush, real impasto, bright.
  orchard: {
    glaze: 0.48,
    shadowColor: [0.58, 0.62, 0.9],
    shadowBand: [0.6, 0.2],
    highlight: 0.38,
    highlightColor: [1.0, 0.97, 0.9],
    lightBand: [0.5, 0.86],
    shade: 0.08,
    hardness: 8,
    varRef: 0.03,
    eccentricity: 1,
    weave: 0.06,
    stroke: 1 / 24,
    grain: 1 / 155,
    strokeFill: 1.1,
    ridge: 0.65,
    bristle: 0.18,
    load: 0.85,
    relief: 0,
    specular: 0.0,
    chroma: 1.4,
    groundPaint: 0.0,
  },
};

/** Brush radius as a fraction of the frame diagonal.
 *
 * A fraction and never a pixel count, and never scaled by `webgl.pixelRatio`.
 * The precedent is exact and expensive: `analysis/hatching.py` resolves its
 * grain as `max(2.0, diagonal * pitch)`, and at the suite's 240px fixture every
 * fine finish clamped onto the same 2px floor — so two finishes that disagree
 * on 0.48 of a 1200px plate were bit-identical in the test. The GPU version of
 * that bug is worse, because there is no number in the reply to catch it. Hence
 * `brushPixels()` below, and hence the reply reports what it resolved to.
 *
 * Mol\*'s own `OutlinePass` *does* multiply by `pixelRatio`, and it is right to:
 * a drawn line should be a constant weight in CSS pixels. A brush is the
 * opposite — it must stay constant relative to the frame, or a 1200px plate and
 * a 400px viewport get the same absolute mark and only one of them is the
 * picture the caller approved.
 */
export const BRUSH_SIZES: Record<string, number> = {
  fine: 1 / 380,
  medium: 1 / 260,
  broad: 1 / 160,
};

/** Below this the marks stop being marks and become tone, so the look is a
 * slightly blurred render and the caller paid for nothing. Refused rather than
 * clamped: a look that cannot be drawn should say so, not draw a different
 * one. */
export const MIN_BRUSH_PX = 3;

/** The weight one sector of the brush gets, from how uniform it was.
 *
 * **This mirrors a line of GLSL** — `painterly-shaders.ts`, in the reduction
 * loop — and exists because that line was arithmetically inert for the whole of
 * the pass's life and nothing could see it. Kyprianidis and Doellner's weight is
 * `1/(1 + sigma^q)` on *0-255* values; on the [0,1] values a shader has, the
 * exponent annihilates it. At `hardness 8` the weight spanned 1.0000000 to
 * 0.9999847 across every spread a luminance can have, so every sector was
 * weighted the same, the least-variance selection never happened, and the pass
 * was an anisotropic Gaussian blur wearing a Kuwahara's name.
 *
 * A picture could not catch that: the abstraction going missing looks like a
 * slightly softer painting, and every other term in the pass still runs. So the
 * guard is on the arithmetic, and it is a guard on a *property* — that the
 * weight can actually discriminate — rather than on a value.
 *
 * The duplication is real and is the price. Changing the GLSL without changing
 * this leaves a test that passes for a formula nobody is running; the test's own
 * docstring says so, and it is checked against the shader source.
 */
export function sectorWeight(variance: number, hardness: number, varRef: number): number {
  return 1 / (1 + Math.pow(variance / (varRef * varRef), 0.5 * hardness));
}

/** The brush radius in pixels for a frame of this size. NaN for an unknown
 * size name, so the caller's own check is the one that reports it. */
export function brushPixels(width: number, height: number, brushSize: string): number {
  const fraction = BRUSH_SIZES[brushSize];
  if (fraction === undefined) return NaN;
  return Math.hypot(width, height) * fraction;
}

/** Every length the brush works at, in pixels of this frame.
 *
 * One function because they have to move together. `brush_size` names a *mark*,
 * and the mark a viewer sees is made by the stroke and the grain of the bristle
 * rather than by the width of the abstraction — over a render with almost no
 * texture in it, changing the abstraction radius alone is very nearly a no-op.
 * The first version did exactly that, reported a different `brush_px` for each
 * size, and drew three pictures a differ could barely separate.
 *
 * Here rather than in `painterly.ts` so that it can be tested without a GPU.
 */
export function resolveBrush(
  width: number,
  height: number,
  look: Look,
  brushSize: string
): { brush: number; stroke: number; grain: number } {
  const diagonal = Math.hypot(width, height);
  const scale = BRUSH_SIZES[brushSize] / BRUSH_SIZES.medium;
  return {
    brush: diagonal * BRUSH_SIZES[brushSize],
    stroke: diagonal * look.stroke * scale,
    grain: diagonal * look.grain * scale,
  };
}
