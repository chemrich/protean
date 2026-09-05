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
  /** Depth of the shadow glaze, and the colour it is glazed with. */
  glaze: number;
  glazeColor: [number, number, number];
  /** Weight of the opaque light, and the white it is mixed from. */
  highlight: number;
  highlightColor: [number, number, number];
  /** Darkening where the paint has structure. Not a silhouette outline. */
  edge: number;
  /** How hard the brush commits to one sector. Above ~16 it posterises. */
  hardness: number;

  /** The variance the abstraction measures itself against, before `hardness`
   * bites. Small values abstract hard — flat passages average and edges
   * survive. **1.0 leaves the filter ungoverned**, which is an anisotropic
   * Gaussian blur rather than a Kuwahara, and `chiaroscuro` asks for exactly
   * that: it melts interior colour transitions over about six pixels at
   * `medium`, and that softness is the look. Charlie compared it against the
   * governed version and picked this one. */
  varRef: number;
  /** How far the brush stretches along the flow, as `1 + anisotropy`. */
  eccentricity: number;
  /** Depth of the canvas weave, 0 for a smooth ground. */
  weave: number;
  /** Half-length of a brush stroke, as a fraction of the frame diagonal.
   *
   * The number that decides whether this reads as painting or as a novelty
   * filter. Past about a twentieth of the diagonal the strokes run further than
   * the thing they are describing, which is the classic failure of every "Van
   * Gogh filter" ever shipped. */
  stroke: number;
  /** Scale of the noise the stroke drags, as a fraction of the diagonal. This
   * is the width of a bristle. */
  grain: number;
  /** How strongly the bristle modulates colour. */
  bristle: number;
  /** How thick the paint stands off the canvas, for the raking light. */
  relief: number;
  /** How much of the brushwork the bare ground gets, 0 to 1.
   *
   * Zero is a real answer and the usual one: the ground is primed canvas, and
   * paint goes where the subject is. Two renders were needed to learn it — at 1
   * the background was full impasto and shouted the molecule down, and at 0.15
   * it still read as fur. */
  groundPaint: number;

  /** Lattice spacing for a dab-based look, as a fraction of the frame
   * diagonal. Zero — every look but `divisionist` — means "no lattice, paint
   * continuously": which of the two mark mechanisms `painterly_brush_frag`
   * runs is decided by this field alone. */
  dabSpacing: number;
  /** How far a dab wanders off its ruled lattice position, as a fraction of
   * `dabSpacing`. Zero rules the lattice into a grid, and a grid samples the
   * image at a fixed frequency and gets a rosette back — the specific
   * failure a dab look exists to avoid — so this wants to stay large. */
  dabJitter: number;
  /** Dab radius, as a fraction of a layer's own spacing. `divisionist` runs
   * this well past a single layer's own edge-to-edge point — full coverage
   * (no gap back to the smooth render underneath) is the look, not an
   * accident held in check, and it takes several unioned layers reaching
   * into each other's territory to get there without the dabs reading as
   * one continuous wash. See the layer union below, not this field alone. */
  dabRadius: number;
  /** Strength of the per-dab colour perturbation, drawn once per dab and
   * held flat over its whole disc. Zero draws every dab in one flat-coloured
   * region at the same RGB — which is what `spot-ink-plates` does by
   * construction, and exactly what a dab look must not do. */
  dabChroma: number;
  /** How much a dab's own radius can shrink from the baseline, 0 to under 1
   * as a fraction of `dabRadius` — the range is `[1 - x, 1]`. A dab only
   * ever shrinks here, never grows past the baseline: a genuinely large one
   * reads as a blob and muddies the picture, found by bracketing ceilings
   * from 1.28x baseline down to the baseline itself and choosing the
   * baseline. Drawn skewed toward that floor too, so most dabs sit well
   * under it and the odd one reaches the baseline. Zero draws every dab the
   * same size, which reads as a filter rather than a hand.
   * This has to change which dab *wins* a pixel, not only how big the winner
   * draws — sizing the disc alone without touching the contest a bigger dab
   * would still lose to a nearer small one exactly where it should be
   * reaching furthest, and its edge would clip against its neighbour's
   * boundary rather than draw a whole circle. */
  dabSizeVariance: number;
}

export const PAINTERLY_LOOKS: Record<string, Look> = {
  // Rembrandt's arrangement rather than Rembrandt's hand: the darks are a
  // transparent brown glaze that goes deeper and *warmer* as it goes down, the
  // lights are opaque lead white, and the paint sits on a visible linen weave.
  // The one number chosen against the alternative rather than from convention
  // is `hardness`: at 16 the brush posterises into hard patches with visible
  // seams, which is the most recognisably filtered outcome available.
  chiaroscuro: {
    glaze: 0.45,
    glazeColor: [0.42, 0.3, 0.19],
    highlight: 0.3,
    highlightColor: [0.949, 0.91, 0.835],
    edge: 0.22,
    hardness: 8,
    varRef: 1.0,
    eccentricity: 1,
    weave: 0.13,
    // A twelfth of a Van Gogh. Dutch Master brushwork is *there* — you can see
    // where the brush went — but it describes the form rather than performing.
    stroke: 1 / 150,
    grain: 1 / 340,
    bristle: 0.13,
    relief: 14,
    groundPaint: 0.0,
    dabSpacing: 0,
    dabJitter: 0,
    dabRadius: 0,
    dabChroma: 0,
    dabSizeVariance: 0,
  },

  // Seurat's mechanism, not his palette: dabs, each one colour sampled once
  // at its own centre and then perturbed — never area-modulated the way a
  // halftone screen is. Everything else on the `Look` (glaze, highlight,
  // edge, weave) is shared with `chiaroscuro` and still applies on top of
  // the dabs; `stroke`, `grain` and `bristle` are unused here — there is no
  // continuous streak to drag noise along, only discrete marks.
  //
  // Chosen over several rounds of bracketing against real renders, not
  // picked once: full coverage rather than a lattice with gaps back to the
  // smooth render (§1b's original "coverage held below 1" was superseded —
  // the picture has to read as built entirely from points, foreground and
  // background both); a single ruled-then-jittered lattice always kept a
  // visible grid no matter how hard it was jittered, fixed by unioning nine
  // independently rotated, offset and scaled lattices (golden-angle
  // separated) rather than jittering one harder; the classic sin/dot hash
  // carried a faint diagonal bias only visible at this density, fixed by
  // swapping it for a better one; dab size varies but only ever shrinks from
  // the baseline, never grows past it, because a large dab reads as a blob.
  // Full account in docs/soft-matter-status.md 1b.
  divisionist: {
    glaze: 0.18,
    glazeColor: [0.3, 0.2, 0.35],
    highlight: 0.12,
    highlightColor: [1.0, 0.98, 0.9],
    edge: 0.0,
    // hardness/varRef govern the Kuwahara abstraction, which this look never
    // runs — the dab path branches ahead of it. Left inert rather than
    // omitted, so the record stays honest about what actually drives it.
    hardness: 0,
    varRef: 1,
    eccentricity: 1,
    weave: 0.0,
    stroke: 0,
    grain: 0,
    bristle: 0,
    relief: 0,
    groundPaint: 0.0,
    dabSpacing: 1 / 185,
    dabJitter: 0.95,
    dabRadius: 0.8,
    dabChroma: 0.16,
    dabSizeVariance: 0.8,
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

/** The GLSL sector weight, in TypeScript, so the suite can reason about it.
 * Duplicated deliberately and guarded by a test that reads the shader source —
 * see `painterly-looks.test.ts`. */
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

/** Dab lattice spacing and radius, in pixels, for a frame of this size —
 * the same brush-size scaling `resolveBrush` uses, so `dabSpacing` reads as
 * "how far apart the dabs are" the way `stroke` reads as "how long the mark
 * is": both fractions of the diagonal, scaled by the named brush size. */
export function resolveDabs(
  width: number,
  height: number,
  look: Look,
  brushSize: string
): { spacing: number; radius: number } {
  const diagonal = Math.hypot(width, height);
  const scale = BRUSH_SIZES[brushSize] / BRUSH_SIZES.medium;
  const spacing = diagonal * look.dabSpacing * scale;
  return { spacing, radius: spacing * look.dabRadius };
}
