/**
 * A painterly finish that runs in the viewer, on the GPU, every frame.
 *
 * Everything protean draws in a print style until now — cross-hatch, hedcut,
 * cyanotype, engraving, the plate print — happens in Python over captured
 * pixels, and `analysis/hatching.py` says why: *"Adding one would mean a custom
 * render pass, which means building Mol\\* from source, which this project does
 * not do."* That sentence stopped being true on 2026-08-26, and the cost it
 * named was measured and wrong. This is the first thing built on the change.
 * The difference it buys is the one the finish route could never have: the
 * person looking at the viewer sees the finish, and `snapshot()` returns what
 * they are looking at.
 *
 * ## Where it runs, and why there are three seams rather than one
 *
 * Mol\* offers no registry, no props variant and no hook for a third-party
 * post-processing pass — the search is recorded in the commit that added this
 * file. So the passes are wrapped. The seam has to satisfy two conditions at
 * once, and only one arrangement does:
 *
 * 1. **Every route that produces pixels.** `ImagePass` owns its *own*
 *    `DrawPass`, `MultiSamplePass` and `IlluminationPass` (`passes/image.js:44`),
 *    so patching the canvas's instances gives a finish that is visible on
 *    screen and absent from every capture — this project's signature failure,
 *    arriving through the one door that reports success. Patching the
 *    *prototypes* catches both, because protean bundles Mol\* from `molstar/lib`
 *    and there is exactly one module instance of each class.
 * 2. **After multisample accumulation, not inside it.** The live canvas runs
 *    `multiSample: temporal` at sample level 2 and a capture runs `on` at level
 *    4 — four jittered sub-frames against sixteen. A finish applied per
 *    sub-frame is *averaged*, which low-pass filters exactly the brush marks
 *    and canvas weave it exists to create, and by different amounts on screen
 *    and in the file. So `MultiSamplePass.render` is wrapped and suppresses the
 *    `DrawPass` wrapper for the duration.
 *
 * `DrawPass._render` is still wrapped, for `multiSample: off`, and it is
 * `_render` rather than `render` because `render` calls it once per eye and
 * only the second eye's pixels would survive.
 *
 * ## What each wrapper does
 *
 * It forces `toDrawingBuffer` to `false`, so Mol\* leaves the frame in a render
 * target it owns rather than rasterising straight into the canvas; then it runs
 * the passes below and blits the result. When the caller wanted a target rather
 * than the screen — every capture does — the result bounces through a scratch
 * target and is copied back, because the consumer holds the *identity* of the
 * target it asked for, and because sampling and writing one texture in a single
 * draw is not a thing.
 *
 * The failure mode to watch: with `toDrawingBuffer` forced off, Mol\*'s own copy
 * to the canvas never runs, so **if the blit here silently does nothing the
 * canvas goes black** rather than falling back to an unpainted picture. Every
 * test of this feature therefore asserts on pixels.
 */

import { QuadSchema, QuadValues, createCopyRenderable } from 'molstar/lib/mol-gl/compute/util';
import { createComputeRenderable } from 'molstar/lib/mol-gl/renderable';
import { DefineSpec, TextureSpec, UniformSpec } from 'molstar/lib/mol-gl/renderable/schema';
import { ShaderCode } from 'molstar/lib/mol-gl/shader-code';
import { createComputeRenderItem } from 'molstar/lib/mol-gl/webgl/render-item';
import { quad_vert } from 'molstar/lib/mol-gl/shader/quad.vert';
import { Vec2, Vec3 } from 'molstar/lib/mol-math/linear-algebra';
import { ValueCell } from 'molstar/lib/mol-util';
import { DrawPass } from 'molstar/lib/mol-canvas3d/passes/draw';
import { MultiSamplePass } from 'molstar/lib/mol-canvas3d/passes/multi-sample';
import { IlluminationPass } from 'molstar/lib/mol-canvas3d/passes/illumination';

import { PAINTERLY_LOOKS, brushPixels, resolveBrush } from './painterly-looks';
import {
  painterly_blur_frag,
  painterly_brush_frag,
  painterly_flow_frag,
  painterly_tensor_frag,
} from './painterly-shaders';

/** Canvas weave pitch, as a fraction of the frame diagonal. 4.5px at a 1200px
 * plate — a real primed linen at about that scale. Below 3px it aliases into
 * grey mush, so it is switched off there rather than drawn wrong. */
const WEAVE_PITCH = 1 / 380;
const MIN_WEAVE_PX = 3;

/** Standard deviation of the tensor smoothing, in pixels of the frame
 * diagonal. Fixed at the viewport rather than scaled with the brush: it sets
 * how far the *direction* is coherent, which is a property of the drawing and
 * not of how broadly it is being painted. */
const FLOW_SIGMA_PX = 2.0;

/** Below this much anisotropy the field is noise rather than direction, and the
 * ground is laid in on a single diagonal instead. Measured rather than picked:
 * on 1UBQ the empty ground reads under 0.02 and the ribbon reads 0.3 upwards. */
const FLOW_FLOOR = 0.08;

/** How loudly the depth gradient speaks in the structure tensor, against a
 * colour gradient of the same size.
 *
 * Depth is normalised over the scene, so its gradient across a ribbon is a few
 * thousandths per pixel where a shading gradient is a few hundredths. This
 * brings them into the same range, so the form is heard under flat light
 * without drowning the colour under raking light. */
const FORM_WEIGHT = 14.0;

/** How slowly the ground's block-in direction wanders, as a fraction of the
 * diagonal. A twentieth means the sweep turns about five times across the
 * frame, which reads as a hand rather than as a ruled fill. */
const GROUND_WANDER = 1 / 20;

/** The grain a look's `relief` is quoted against: the bristle width at a 1200px
 * plate, which is the size the looks were tuned by looking at. */
const REFERENCE_GRAIN_PX = 5.0;

export interface PainterlySettings {
  look: string | null;
  /** A key of BRUSH_SIZES. */
  brushSize: string;
}

let settings: PainterlySettings = { look: null, brushSize: 'medium' };

/** The DrawPass whose wrapper is currently standing down, because the pass that
 * owns it is going to paint the accumulated frame instead.
 *
 * The pass instance rather than a boolean. Three `DrawPass`es are live at once
 * — the canvas's, the screenshot helper's image pass, and its preview pass
 * (`passes/passes.js:14`, `passes/image.js:44`) — and `ImagePass.render` awaits
 * inside its tracing loop while protean's raf pump keeps the canvas drawing, so
 * a global flag set by one pass can be read by another. Naming the instance
 * makes that impossible rather than unlikely. */
let standingDown: object | null = null;

let installed = false;

/** Whether the classes patched here are the ones the running viewer built with.
 *
 * `null` until the first check. Mol\* ships no `exports` map, so
 * `molstar/lib/mol-canvas3d/passes/draw` resolves by extension probing — one
 * module in a Rollup build, but Vite's dev-mode dependency optimiser discovers
 * it as a separate entry and could hand out a second copy of the class. Two
 * copies means the patch is applied to one and the viewer uses the other: the
 * finish would report success and never draw, which is the exact failure this
 * project keeps meeting. So it is checked against a live pass rather than
 * assumed, and the answer is in every `brushwork()` reply. */
let patchesTheLivePasses: boolean | null = null;

/** Compare the patched classes against the ones a running viewer actually
 * built, and remember the answer. */
export function checkPatchReachesViewer(plugin: any): boolean | null {
  const passes = plugin?.canvas3dContext?.passes;
  if (!passes?.draw) return patchesTheLivePasses;
  patchesTheLivePasses =
    passes.draw.constructor === DrawPass && passes.multiSample?.constructor === MultiSamplePass;
  return patchesTheLivePasses;
}

export function patchReachesViewer(): boolean | null {
  return patchesTheLivePasses;
}

type PassState = {
  width: number;
  height: number;
  /** Ping-pong pair for the structure tensor and its two smoothing halves. */
  tensorA: any;
  tensorB: any;
  /** Where the brush writes when the result has to go back where it came from. */
  scratch: any;
  tensor: any;
  blur: any;
  flow: any;
  brush: any;
  copy: any;
  /** Counts baked into a program as `#define`s, so a change recompiles. */
  samples: number;
  taps: number;
  stroke: number;
};

/** One set of targets per owning pass, keyed by the pass itself.
 *
 * Not a module singleton. Three DrawPasses exist at once — the canvas's, the
 * screenshot helper's image pass and its preview pass — at three different
 * sizes, and a shared set would resize itself on every frame, thrashing between
 * the canvas and the capture. */
const states = new WeakMap<object, PassState>();

const TensorSchema = {
  ...QuadSchema,
  tColor: TextureSpec('texture', 'rgba', 'ubyte', 'nearest'),
  tDepth: TextureSpec('texture', 'rgba', 'ubyte', 'nearest'),
  uTexSize: UniformSpec('v2'),
  uNear: UniformSpec('f'),
  uFar: UniformSpec('f'),
  uIsOrtho: UniformSpec('f'),
  uFormWeight: UniformSpec('f'),
};

const BlurSchema = {
  ...QuadSchema,
  tTensor: TextureSpec('texture', 'rgba', 'float', 'nearest'),
  uTexSize: UniformSpec('v2'),
  uDir: UniformSpec('v2'),
  uSigma: UniformSpec('f'),
  uDepthFalloff: UniformSpec('f'),
  dTaps: DefineSpec('number'),
};

const FlowSchema = {
  ...QuadSchema,
  tTensor: TextureSpec('texture', 'rgba', 'float', 'nearest'),
  uTexSize: UniformSpec('v2'),
  uFlowFloor: UniformSpec('f'),
  uGroundWander: UniformSpec('f'),
};

const BrushSchema = {
  ...QuadSchema,
  tColor: TextureSpec('texture', 'rgba', 'ubyte', 'nearest'),
  tFlow: TextureSpec('texture', 'rgba', 'float', 'nearest'),
  uTexSize: UniformSpec('v2'),
  uRadius: UniformSpec('f'),
  uAlpha: UniformSpec('f'),
  uHardness: UniformSpec('f'),
  uVarRef: UniformSpec('f'),
  uDepthFalloff: UniformSpec('f'),
  uStrokeLen: UniformSpec('f'),
  uStrokeWidth: UniformSpec('f'),
  uStrokeFill: UniformSpec('f'),
  uRidge: UniformSpec('f'),
  uBristle: UniformSpec('f'),
  uLoad: UniformSpec('f'),
  uRelief: UniformSpec('f'),
  uSpecular: UniformSpec('f'),
  uFar: UniformSpec('f'),
  uGroundPaint: UniformSpec('f'),
  uGlaze: UniformSpec('f'),
  uShadowColor: UniformSpec('v3'),
  uShadowFrom: UniformSpec('f'),
  uShadowTo: UniformSpec('f'),
  uHighlight: UniformSpec('f'),
  uHighlightColor: UniformSpec('v3'),
  uLightFrom: UniformSpec('f'),
  uLightTo: UniformSpec('f'),
  uShade: UniformSpec('f'),
  uChroma: UniformSpec('f'),
  uWeaveDepth: UniformSpec('f'),
  uWeavePitch: UniformSpec('f'),
  dSamples: DefineSpec('number'),
};

const TensorShader = ShaderCode('painterly-tensor', quad_vert, painterly_tensor_frag);
const BlurShader = ShaderCode('painterly-blur', quad_vert, painterly_blur_frag);
const FlowShader = ShaderCode('painterly-flow', quad_vert, painterly_flow_frag);
const BrushShader = ShaderCode('painterly-brush', quad_vert, painterly_brush_frag);

/** How many rings of taps the flow smoothing walks, for a given sigma. */
function tapsFor(sigma: number): number {
  return Math.max(1, Math.min(12, Math.ceil(2.5 * sigma)));
}

/** How finely the unit disc is walked, for a brush of this radius.
 *
 * Set from the radius so that the taps land roughly a pixel apart along the
 * ellipse's short axis, and bounded at both ends: the brush is a *statistic*
 * over a region, so sampling it sparsely is legitimate, but a lattice coarser
 * than the marks it is measuring turns the statistic into noise. `4` is about
 * 49 taps and `8` about 200.
 */
function samplesFor(radius: number): number {
  return Math.max(3, Math.min(8, Math.round(radius)));
}

function buildState(webgl: any, width: number, height: number, radius: number): PassState {
  const half = webgl.extensions.colorBufferHalfFloat && webgl.extensions.textureHalfFloat;
  const float = webgl.extensions.colorBufferFloat && webgl.extensions.textureFloat;
  // The tensor holds unbounded sums of squares and a linear view depth, so it
  // wants more than eight bits. Half float is plenty and is what every WebGL2
  // context protean has met provides; the uint8 fall-back would quantise the
  // depth channel into steps the silhouette guard could see, so it is only
  // reached on hardware where nothing else here would work either.
  const type = half ? 'fp16' : float ? 'float32' : 'uint8';
  const tensorA = webgl.createRenderTarget(width, height, false, type, 'nearest');
  const tensorB = webgl.createRenderTarget(width, height, false, type, 'nearest');
  const scratch = webgl.createRenderTarget(width, height, false, 'uint8', 'nearest');

  const texSize = Vec2.create(width, height);
  const samples = samplesFor(radius);
  const taps = tapsFor(FLOW_SIGMA_PX);

  const tensorValues = {
    ...QuadValues,
    tColor: ValueCell.create(tensorA.texture),
    tDepth: ValueCell.create(tensorA.texture),
    uTexSize: ValueCell.create(Vec2.copy(Vec2.zero(), texSize)),
    uNear: ValueCell.create(1),
    uFar: ValueCell.create(100),
    uIsOrtho: ValueCell.create(0),
    uFormWeight: ValueCell.create(0),
  };
  const tensor = createComputeRenderable(
    createComputeRenderItem(webgl, 'triangles', TensorShader, { ...TensorSchema }, tensorValues),
    tensorValues
  );

  const blurValues = {
    ...QuadValues,
    tTensor: ValueCell.create(tensorA.texture),
    uTexSize: ValueCell.create(Vec2.copy(Vec2.zero(), texSize)),
    uDir: ValueCell.create(Vec2.create(1, 0)),
    uSigma: ValueCell.create(FLOW_SIGMA_PX),
    uDepthFalloff: ValueCell.create(1),
    dTaps: ValueCell.create(taps),
  };
  const blur = createComputeRenderable(
    createComputeRenderItem(webgl, 'triangles', BlurShader, { ...BlurSchema }, blurValues),
    blurValues
  );

  const flowValues = {
    ...QuadValues,
    tTensor: ValueCell.create(tensorA.texture),
    uTexSize: ValueCell.create(Vec2.copy(Vec2.zero(), texSize)),
    uFlowFloor: ValueCell.create(FLOW_FLOOR),
    uGroundWander: ValueCell.create(64),
  };
  const flow = createComputeRenderable(
    createComputeRenderItem(webgl, 'triangles', FlowShader, { ...FlowSchema }, flowValues),
    flowValues
  );

  const brushValues = {
    ...QuadValues,
    tColor: ValueCell.create(tensorA.texture),
    tFlow: ValueCell.create(tensorB.texture),
    uTexSize: ValueCell.create(Vec2.copy(Vec2.zero(), texSize)),
    uRadius: ValueCell.create(radius),
    uAlpha: ValueCell.create(1),
    uHardness: ValueCell.create(8),
    uVarRef: ValueCell.create(0.03),
    uDepthFalloff: ValueCell.create(1),
    uStrokeLen: ValueCell.create(1),
    uStrokeWidth: ValueCell.create(1),
    uStrokeFill: ValueCell.create(0.8),
    uRidge: ValueCell.create(0),
    uBristle: ValueCell.create(0),
    uLoad: ValueCell.create(0),
    uRelief: ValueCell.create(0),
    uSpecular: ValueCell.create(0),
    uFar: ValueCell.create(100),
    uGroundPaint: ValueCell.create(0.3),
    uGlaze: ValueCell.create(0),
    uShadowColor: ValueCell.create(Vec3.create(0, 0, 0)),
    uShadowFrom: ValueCell.create(0.38),
    uShadowTo: ValueCell.create(0.04),
    uHighlight: ValueCell.create(0),
    uHighlightColor: ValueCell.create(Vec3.create(1, 1, 1)),
    uLightFrom: ValueCell.create(0.7),
    uLightTo: ValueCell.create(0.96),
    uShade: ValueCell.create(0),
    uChroma: ValueCell.create(1),
    uWeaveDepth: ValueCell.create(0),
    uWeavePitch: ValueCell.create(4),
    dSamples: ValueCell.create(samples),
  };
  const brush = createComputeRenderable(
    createComputeRenderItem(webgl, 'triangles', BrushShader, { ...BrushSchema }, brushValues),
    brushValues
  );

  return {
    width,
    height,
    tensorA,
    tensorB,
    scratch,
    tensor,
    blur,
    flow,
    brush,
    copy: createCopyRenderable(webgl, scratch.texture),
    samples,
    taps,
    stroke: 1,
  };
}

/** Resize lazily, at render time, the way `MultiSamplePass.syncSize` does.
 *
 * Nothing calls back into this pass on a resize — `Passes.updateSize`,
 * `ImagePass.setSize` and `IlluminationPass.setSize` are the three call sites
 * and patching all three is fragile. Comparing against the source every frame
 * is cheap and cannot go stale, and a stale `uTexSize` is the worst available
 * failure: the frame still renders and only its scale is wrong. */
function syncSize(webgl: any, state: PassState, width: number, height: number): void {
  if (state.width === width && state.height === height) return;
  state.width = width;
  state.height = height;
  state.tensorA.setSize(width, height);
  state.tensorB.setSize(width, height);
  state.scratch.setSize(width, height);
}

function beginQuad(
  webgl: any,
  viewport: { x: number; y: number; width: number; height: number },
  clear = false
) {
  const { gl, state } = webgl;
  state.enable(gl.SCISSOR_TEST);
  state.disable(gl.BLEND);
  state.disable(gl.DEPTH_TEST);
  state.depthMask(false);
  state.viewport(viewport.x, viewport.y, viewport.width, viewport.height);
  state.scissor(viewport.x, viewport.y, viewport.width, viewport.height);
  if (clear) {
    // Transparent black, not opaque black. With `toDrawingBuffer` forced off
    // Mol*'s own clear of the canvas on the transparent-background path
    // (`passes/draw.js:356`) never runs, so this is the only one — and clearing
    // to alpha 1 there would turn a transparent capture opaque while reporting
    // success.
    state.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }
}

/**
 * Paint one finished frame.
 *
 * `source` is where Mol\* left the picture; `depth` is the opaque depth texture
 * that goes with it. `destination` is `null` for the canvas and a render target
 * for a capture — and when it is the same target as `source`, which is the
 * usual case for a capture, the brush writes to a scratch target and is copied
 * back afterwards.
 */
function paint(
  owner: object,
  webgl: any,
  camera: any,
  source: any,
  depth: any,
  destination: any | null
): void {
  const look = settings.look ? PAINTERLY_LOOKS[settings.look] : undefined;
  if (!look) return;

  const width = source.getWidth();
  const height = source.getHeight();
  const radius = brushPixels(width, height, settings.brushSize);
  if (!Number.isFinite(radius) || radius <= 0) return;

  let state = states.get(owner);
  if (!state) {
    state = buildState(webgl, width, height, radius);
    states.set(owner, state);
  }
  syncSize(webgl, state, width, height);

  const { gl } = webgl;
  const viewport = camera.viewport;
  // The scene's depth range, which is what the silhouette guard is measured in.
  // Fitted to the molecule by Mol*, so a fraction of it is a fraction of the
  // molecule and the guard means the same thing at every framing.
  const falloff = Math.max(1e-4, (camera.far - camera.near) * 0.02);
  const diagonal = Math.hypot(width, height);
  const weavePx = diagonal * WEAVE_PITCH;

  // -- the structure tensor ---------------------------------------------------
  ValueCell.update(state.tensor.values.tColor, source.texture);
  ValueCell.update(state.tensor.values.tDepth, depth);
  ValueCell.update(
    state.tensor.values.uTexSize,
    Vec2.set(state.tensor.values.uTexSize.ref.value, width, height)
  );
  ValueCell.updateIfChanged(state.tensor.values.uNear, camera.near);
  ValueCell.updateIfChanged(state.tensor.values.uFar, camera.far);
  ValueCell.updateIfChanged(
    state.tensor.values.uIsOrtho,
    camera.state.mode === 'orthographic' ? 1 : 0
  );
  ValueCell.updateIfChanged(state.tensor.values.uFormWeight, FORM_WEIGHT);
  state.tensor.update();
  state.tensorA.bind();
  beginQuad(webgl, viewport);
  state.tensor.render();

  // -- two halves of a separable, depth-aware smoothing ------------------------
  ValueCell.update(
    state.blur.values.uTexSize,
    Vec2.set(state.blur.values.uTexSize.ref.value, width, height)
  );
  ValueCell.updateIfChanged(state.blur.values.uDepthFalloff, falloff);
  for (const [from, to, dir] of [
    [state.tensorA, state.tensorB, [1, 0]],
    [state.tensorB, state.tensorA, [0, 1]],
  ] as Array<[any, any, number[]]>) {
    ValueCell.update(state.blur.values.tTensor, from.texture);
    ValueCell.update(
      state.blur.values.uDir,
      Vec2.set(state.blur.values.uDir.ref.value, dir[0], dir[1])
    );
    state.blur.update();
    to.bind();
    beginQuad(webgl, viewport);
    state.blur.render();
  }

  // -- the flow: a direction, a confidence, a depth ----------------------------
  ValueCell.update(state.flow.values.tTensor, state.tensorA.texture);
  ValueCell.update(
    state.flow.values.uTexSize,
    Vec2.set(state.flow.values.uTexSize.ref.value, width, height)
  );
  ValueCell.updateIfChanged(state.flow.values.uGroundWander, diagonal * GROUND_WANDER);
  state.flow.update();
  state.tensorB.bind();
  beginQuad(webgl, viewport);
  state.flow.render();

  // -- the brush, and everything laid over it ---------------------------------
  const lengths = resolveBrush(width, height, look, settings.brushSize);
  const strokePx = lengths.stroke;
  const samples = samplesFor(radius);
  if (samples !== state.samples) {
    // A define, so this recompiles the program. It changes only when the brush
    // size or the frame size changes — a tool call or a resize.
    ValueCell.update(state.brush.values.dSamples, samples);
    state.samples = samples;
  }
  ValueCell.update(state.brush.values.tColor, source.texture);
  ValueCell.update(state.brush.values.tFlow, state.tensorB.texture);
  ValueCell.update(
    state.brush.values.uTexSize,
    Vec2.set(state.brush.values.uTexSize.ref.value, width, height)
  );
  ValueCell.updateIfChanged(state.brush.values.uRadius, radius);
  ValueCell.updateIfChanged(state.brush.values.uAlpha, look.eccentricity);
  ValueCell.updateIfChanged(state.brush.values.uHardness, look.hardness);
  ValueCell.updateIfChanged(state.brush.values.uVarRef, look.varRef);
  ValueCell.updateIfChanged(state.brush.values.uDepthFalloff, falloff);
  ValueCell.updateIfChanged(state.brush.values.uStrokeLen, Math.max(4, strokePx));
  ValueCell.updateIfChanged(state.brush.values.uStrokeWidth, Math.max(2, lengths.grain));
  ValueCell.updateIfChanged(state.brush.values.uStrokeFill, look.strokeFill);
  ValueCell.updateIfChanged(state.brush.values.uRidge, look.ridge);
  ValueCell.updateIfChanged(state.brush.values.uBristle, look.bristle);
  ValueCell.updateIfChanged(state.brush.values.uLoad, look.load);
  // Scaled with the grain, not fixed. The relief reads `dFdx` of the streak
  // field, whose slope goes as 1/grain — so a fixed number gives a thin bristle
  // a violently steeper ridge than a thick one, and at a small plate the paint
  // stops being paint and becomes black speckle. Found by rendering a 620px
  // figure tile of a look that was right at 1200. It is also the honest
  // physics: a finer brush leaves a shallower ridge.
  ValueCell.updateIfChanged(
    state.brush.values.uRelief,
    (look.relief * lengths.grain) / REFERENCE_GRAIN_PX
  );
  ValueCell.updateIfChanged(state.brush.values.uSpecular, look.specular);
  ValueCell.updateIfChanged(state.brush.values.uFar, camera.far);
  ValueCell.updateIfChanged(state.brush.values.uGroundPaint, look.groundPaint);
  ValueCell.updateIfChanged(state.brush.values.uGlaze, look.glaze);
  ValueCell.update(
    state.brush.values.uShadowColor,
    Vec3.set(state.brush.values.uShadowColor.ref.value, ...look.shadowColor)
  );
  ValueCell.updateIfChanged(state.brush.values.uShadowFrom, look.shadowBand[0]);
  ValueCell.updateIfChanged(state.brush.values.uShadowTo, look.shadowBand[1]);
  ValueCell.updateIfChanged(state.brush.values.uLightFrom, look.lightBand[0]);
  ValueCell.updateIfChanged(state.brush.values.uLightTo, look.lightBand[1]);
  ValueCell.updateIfChanged(state.brush.values.uHighlight, look.highlight);
  ValueCell.update(
    state.brush.values.uHighlightColor,
    Vec3.set(state.brush.values.uHighlightColor.ref.value, ...look.highlightColor)
  );
  ValueCell.updateIfChanged(state.brush.values.uShade, look.shade);
  ValueCell.updateIfChanged(state.brush.values.uChroma, look.chroma);
  ValueCell.updateIfChanged(
    state.brush.values.uWeaveDepth,
    weavePx >= MIN_WEAVE_PX ? look.weave : 0
  );
  ValueCell.updateIfChanged(state.brush.values.uWeavePitch, weavePx);
  state.brush.update();

  const bounce = destination !== null;
  if (bounce) state.scratch.bind();
  else webgl.bindDrawingBuffer();
  beginQuad(webgl, viewport, !bounce);
  state.brush.render();

  if (bounce) {
    // The consumer holds the identity of the target it asked for —
    // `ImagePass.getImageData` binds it and reads pixels off it — so the
    // painted frame has to end up in that object and not merely somewhere.
    if (state.copy.values.tColor.ref.value !== state.scratch.texture) {
      ValueCell.update(state.copy.values.tColor, state.scratch.texture);
      ValueCell.update(
        state.copy.values.uTexSize,
        Vec2.set(state.copy.values.uTexSize.ref.value, width, height)
      );
      state.copy.update();
    }
    destination.bind();
    beginQuad(webgl, viewport);
    state.copy.render();
  }
  gl.flush();
}

/** Patch the three passes. Idempotent, and called once from `main.ts`. */
export function installPainterly(): void {
  if (installed) return;
  installed = true;

  const drawRender = (DrawPass.prototype as any)._render;
  (DrawPass.prototype as any)._render = function (
    this: any,
    renderer: any,
    camera: any,
    scene: any,
    helper: any,
    toDrawingBuffer: boolean,
    transparentBackground: boolean,
    props: any
  ) {
    // `camera.disabled` is the second eye of a single-view XR pose
    // (`camera/stereo.js:140`), and Mol* returns before drawing anything. The
    // guard has to be mirrored here or the wrapper blits a stale target over a
    // frame that was deliberately left alone.
    if (standingDown === this || !settings.look || camera.disabled) {
      return drawRender.call(
        this,
        renderer,
        camera,
        scene,
        helper,
        toDrawingBuffer,
        transparentBackground,
        props
      );
    }
    drawRender.call(this, renderer, camera, scene, helper, false, transparentBackground, props);
    const source = this.getColorTarget(props.postprocessing);
    paint(this, this.webgl, camera, source, this.depthTextureOpaque, toDrawingBuffer ? null : source);
  };

  const multiRender = (MultiSamplePass.prototype as any).render;
  (MultiSamplePass.prototype as any).render = function (
    this: any,
    sampleIndex: number,
    ctx: any,
    props: any,
    toDrawingBuffer: boolean,
    forceOn: boolean
  ) {
    if (!settings.look) {
      return multiRender.call(this, sampleIndex, ctx, props, toDrawingBuffer, forceOn);
    }
    // Returned, not swallowed. `MultiSampleHelper` stores this as its next
    // sample index (`passes/multi-sample.js:301`); an `undefined` there makes
    // `update()` answer "more samples needed" forever and the next frame
    // indexes the jitter table with it and throws.
    const previous = standingDown;
    standingDown = this.drawPass;
    let next: number;
    try {
      next = multiRender.call(this, sampleIndex, ctx, props, false, forceOn);
    } finally {
      standingDown = previous;
    }
    paint(
      this,
      this.webgl,
      ctx.camera,
      this.colorTarget,
      this.drawPass.depthTextureOpaque,
      toDrawingBuffer ? null : this.colorTarget
    );
    return next;
  };

  const illuminationRender = (IlluminationPass.prototype as any).render;
  (IlluminationPass.prototype as any).render = function (
    this: any,
    ctx: any,
    props: any,
    toDrawingBuffer: boolean
  ) {
    if (!settings.look || !this.supported) {
      return illuminationRender.call(this, ctx, props, toDrawingBuffer);
    }
    const previous = standingDown;
    standingDown = this.drawPass;
    try {
      illuminationRender.call(this, ctx, props, false);
    } finally {
      standingDown = previous;
    }
    const source = this.colorTarget;
    if (!source) return;
    paint(
      this,
      this.webgl,
      ctx.camera,
      source,
      this.drawPass.depthTextureOpaque,
      toDrawingBuffer ? null : source
    );
  };
}

/** Choose a look, or take it off. Returns what is in force afterwards. */
export function setPainterly(next: PainterlySettings): PainterlySettings {
  settings = { ...next };
  return { ...settings };
}

export function painterlyState(): PainterlySettings {
  return { ...settings };
}
