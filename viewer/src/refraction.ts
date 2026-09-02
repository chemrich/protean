/**
 * Refractive Glass & Frosted Seaglass WebGL Shader Pipeline for Mol*.
 *
 * Implements:
 * 1. Screen-space refraction offset derived from Snell's law with perspective depth scaling and isotropic aspect ratio correction.
 * 2. Dielectric Schlick Fresnel factor (F0 = 0.04).
 * 3. 3-tap spectral chromatic dispersion for clear glass.
 * 4. 12-tap Vogel Golden Angle spiral kernel with Gaussian weights and screen-space dither for frosted seaglass.
 * 5. Procedural FBM bump perturbation for tumbled beach glass surface facets.
 * 6. Transmitted color filtering / Beer-Lambert absorption tinting.
 */

import { QuadSchema, QuadValues, createCopyRenderable } from 'molstar/lib/mol-gl/compute/util';
import { createComputeRenderable } from 'molstar/lib/mol-gl/renderable';
import { TextureSpec, UniformSpec } from 'molstar/lib/mol-gl/renderable/schema';
import { ShaderCode } from 'molstar/lib/mol-gl/shader-code';
import { createComputeRenderItem } from 'molstar/lib/mol-gl/webgl/render-item';
import { quad_vert } from 'molstar/lib/mol-gl/shader/quad.vert';
import { Vec2 } from 'molstar/lib/mol-math/linear-algebra';
import { ValueCell } from 'molstar/lib/mol-util';
import { DrawPass } from 'molstar/lib/mol-canvas3d/passes/draw';
import { MultiSamplePass } from 'molstar/lib/mol-canvas3d/passes/multi-sample';
import { PostprocessingPass } from 'molstar/lib/mol-canvas3d/passes/postprocessing';

import { refraction_composite_frag } from './refraction-shaders';

export interface RefractionSettings {
  enabled: boolean;
  ior: number;
  refractionStrength: number;
  dispersionSpread: number;
  diffusionSpread: number;
  roughness: number;
  bumpiness: number;
  bumpFrequency: number;
  absorptionStrength: number;
  fresnelF0: number;
}

const DEFAULT_REFRACTION_SETTINGS: RefractionSettings = {
  enabled: true,
  ior: 1.50,
  refractionStrength: 0.08,
  dispersionSpread: 0.02,
  diffusionSpread: 0.04,
  roughness: 0.05,
  bumpiness: 0.0,
  bumpFrequency: 4.0,
  absorptionStrength: 0.75,
  fresnelF0: 0.04,
};

let settings: RefractionSettings = { ...DEFAULT_REFRACTION_SETTINGS };
let installed = false;
let patchesTheLivePasses: boolean | null = null;
let standingDown: object | null = null;

export function setRefraction(next: Partial<RefractionSettings>): RefractionSettings {
  settings = { ...settings, ...next };
  return { ...settings };
}

export function refractionState(): RefractionSettings {
  return { ...settings };
}

export function checkRefractionPatchReachesViewer(plugin: any): boolean | null {
  const passes = plugin?.canvas3dContext?.passes;
  if (!passes?.draw) return patchesTheLivePasses;
  patchesTheLivePasses =
    passes.draw.constructor === DrawPass && passes.multiSample?.constructor === MultiSamplePass;
  return patchesTheLivePasses;
}

export function refractionPatchReachesViewer(): boolean | null {
  return patchesTheLivePasses;
}

// =============================================================================
// Mathematical Optics Helpers (Unit-testable pure TS functions)
// =============================================================================

/**
 * Calculates Snell's law refraction vector and maps it to screen UV deflection.
 */
export function snellRefractionOffset(
  viewDir: [number, number, number],
  normal: [number, number, number],
  depth: number,
  ior: number,
  bufferWidth: number,
  bufferHeight: number,
  strength: number = 0.08
): [number, number] {
  const eta = 1.0 / Math.max(ior, 1.0001);
  let [nx, ny, nz] = normal;
  const [vx, vy, vz] = viewDir;

  const dotNV = nx * vx + ny * vy + nz * vz;
  if (dotNV < 0) {
    nx = -nx;
    ny = -ny;
    nz = -nz;
  }

  // 3D Snell Refraction: R = eta * I + (eta * dotNI - sqrt(k)) * N
  // where I = -V, dotNI = -dot(V, N)
  const dotNI = -(vx * nx + vy * ny + vz * nz);
  const k = 1.0 - eta * eta * (1.0 - dotNI * dotNI);

  let rx = 0;
  let ry = 0;
  if (k < 0) {
    // Total internal reflection -> fallback to reflection: reflect(-V, N)
    const dotVN = vx * nx + vy * ny + vz * nz;
    rx = -vx + 2.0 * dotVN * nx;
    ry = -vy + 2.0 * dotVN * ny;
  } else {
    const coeff = eta * dotNI - Math.sqrt(k);
    rx = eta * (-vx) + coeff * nx;
    ry = eta * (-vy) + coeff * ny;
  }

  const aspectY = bufferWidth / Math.max(bufferHeight, 1.0);
  const zScale = Math.max(depth, 1.0);

  return [(rx * strength / zScale), (ry * strength / zScale) * aspectY];
}

/**
 * Evaluates dielectric Schlick Fresnel reflectance.
 */
export function schlickFresnel(
  viewDir: [number, number, number],
  normal: [number, number, number],
  f0: number = 0.04
): number {
  const [vx, vy, vz] = viewDir;
  const [nx, ny, nz] = normal;
  const dotNV = Math.min(Math.max(Math.abs(nx * vx + ny * vy + nz * vz), 0.0), 1.0);
  // Epic Games exp2 approximation: exp2((-5.55473 * dotNV - 6.98316) * dotNV)
  const fresnelExp = Math.pow(2.0, (-5.55473 * dotNV - 6.98316) * dotNV);
  return f0 * (1.0 - fresnelExp) + fresnelExp;
}

/**
 * Computes 3-tap spectral chromatic dispersion UV offsets.
 */
export function spectralDispersionOffsets(
  baseOffset: [number, number],
  dispersion: number = 0.02
): { r: [number, number]; g: [number, number]; b: [number, number] } {
  return {
    r: [baseOffset[0] * (1.0 - dispersion), baseOffset[1] * (1.0 - dispersion)],
    g: [baseOffset[0], baseOffset[1]],
    b: [baseOffset[0] * (1.0 + dispersion), baseOffset[1] * (1.0 + dispersion)],
  };
}

/**
 * Generates an N-tap Vogel Golden Angle spiral distribution in unit disc.
 */
export function vogelSpiralKernel(numTaps: number = 12): Array<[number, number]> {
  const GOLDEN_ANGLE = Math.PI * (3.0 - Math.sqrt(5.0)); // ~2.39996323 rad
  const kernel: Array<[number, number]> = [];
  for (let i = 0; i < numTaps; i++) {
    const r = Math.sqrt((i + 0.5) / numTaps);
    const theta = i * GOLDEN_ANGLE;
    kernel.push([r * Math.cos(theta), r * Math.sin(theta)]);
  }
  return kernel;
}

/**
 * Computes 2D Gaussian attenuation weights for disc kernel samples.
 */
export function gaussianWeights(numTaps: number = 12, sigma: number = 0.707): number[] {
  const kernel = vogelSpiralKernel(numTaps);
  return kernel.map(([x, y]) => {
    const r2 = x * x + y * y;
    return Math.exp(-r2 / (2.0 * sigma * sigma));
  });
}

/**
 * Transmitted color filtering via Beer-Lambert absorption law.
 */
export function beerLambertAbsorption(
  baseColor: [number, number, number],
  normal: [number, number, number],
  viewDir: [number, number, number],
  strength: number = 0.75
): [number, number, number] {
  const [nx, ny, nz] = normal;
  const [vx, vy, vz] = viewDir;
  const nDotV = Math.min(Math.max(Math.abs(nx * vx + ny * vy + nz * vz), 0.0), 1.0);
  const pathThickness = Math.min(Math.max(1.0 / Math.max(nDotV, 0.25), 1.0), 3.5);
  const expFactor = pathThickness * strength;

  return [
    Math.pow(Math.max(baseColor[0], 0.02), expFactor),
    Math.pow(Math.max(baseColor[1], 0.02), expFactor),
    Math.pow(Math.max(baseColor[2], 0.02), expFactor),
  ];
}

/**
 * Fast screen-space dither hash angle in radians.
 */
export function screenSpaceDitherAngle(x: number, y: number): number {
  const dotVal = x * 12.9898 + y * 78.233;
  const sinVal = Math.sin(dotVal) * 43758.5453;
  const frac = sinVal - Math.floor(sinVal);
  return frac * 2.0 * Math.PI;
}

// =============================================================================
// WebGL Refraction Pass Implementation
// =============================================================================

type RefractionPassState = {
  width: number;
  height: number;
  scratch: any;
  refraction: any;
  copy: any;
};

const states = new WeakMap<object, RefractionPassState>();

const RefractionSchema = {
  ...QuadSchema,
  tColor: TextureSpec('texture', 'rgba', 'ubyte', 'nearest'),
  tTransparentColor: TextureSpec('texture', 'rgba', 'ubyte', 'nearest'),
  tDepthOpaque: TextureSpec('texture', 'rgba', 'ubyte', 'nearest'),
  tDepthTransparent: TextureSpec('texture', 'rgba', 'ubyte', 'nearest'),
  uTexSize: UniformSpec('v2'),
  uNear: UniformSpec('f'),
  uFar: UniformSpec('f'),
  uIsOrtho: UniformSpec('f'),
  uGlassIOR: UniformSpec('f'),
  uRefractionStrength: UniformSpec('f'),
  uDispersionSpread: UniformSpec('f'),
  uDiffusionSpread: UniformSpec('f'),
  uRoughness: UniformSpec('f'),
  uBumpiness: UniformSpec('f'),
  uBumpFrequency: UniformSpec('f'),
  uAbsorptionStrength: UniformSpec('f'),
  uFresnelF0: UniformSpec('f'),
};

const RefractionShader = ShaderCode('refraction-composite', quad_vert, refraction_composite_frag);

function buildRefractionState(webgl: any, width: number, height: number, tColor: any, tTrans: any, tDepthOp: any, tDepthTrans: any): RefractionPassState {
  const scratch = webgl.createRenderTarget(width, height, false, 'uint8', 'nearest');
  const texSize = Vec2.create(width, height);

  const refractionValues = {
    ...QuadValues,
    tColor: ValueCell.create(tColor),
    tTransparentColor: ValueCell.create(tTrans),
    tDepthOpaque: ValueCell.create(tDepthOp),
    tDepthTransparent: ValueCell.create(tDepthTrans),
    uTexSize: ValueCell.create(Vec2.copy(Vec2.zero(), texSize)),
    uNear: ValueCell.create(1),
    uFar: ValueCell.create(100),
    uIsOrtho: ValueCell.create(0),
    uGlassIOR: ValueCell.create(settings.ior),
    uRefractionStrength: ValueCell.create(settings.refractionStrength),
    uDispersionSpread: ValueCell.create(settings.dispersionSpread),
    uDiffusionSpread: ValueCell.create(settings.diffusionSpread),
    uRoughness: ValueCell.create(settings.roughness),
    uBumpiness: ValueCell.create(settings.bumpiness),
    uBumpFrequency: ValueCell.create(settings.bumpFrequency),
    uAbsorptionStrength: ValueCell.create(settings.absorptionStrength),
    uFresnelF0: ValueCell.create(settings.fresnelF0),
  };

  const refraction = createComputeRenderable(
    createComputeRenderItem(webgl, 'triangles', RefractionShader, { ...RefractionSchema }, refractionValues),
    refractionValues
  );

  return {
    width,
    height,
    scratch,
    refraction,
    copy: createCopyRenderable(webgl, scratch.texture),
  };
}

function syncRefractionSize(state: RefractionPassState, width: number, height: number): void {
  if (state.width === width && state.height === height) return;
  state.width = width;
  state.height = height;
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
    state.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }
}

/**
 * Runs the screen-space refraction pass over the current frame buffers.
 */
function applyRefraction(
  owner: object,
  webgl: any,
  camera: any,
  colorSource: any,
  transparentColorSource: any,
  depthOpaque: any,
  depthTransparent: any,
  destination: any | null
): void {
  if (!settings.enabled) return;

  const width = colorSource.getWidth();
  const height = colorSource.getHeight();
  if (!width || !height) return;

  let state = states.get(owner);
  if (!state) {
    state = buildRefractionState(webgl, width, height, colorSource.texture, transparentColorSource.texture, depthOpaque, depthTransparent);
    states.set(owner, state);
  }
  syncRefractionSize(state, width, height);

  const { gl } = webgl;
  const viewport = camera.viewport;

  ValueCell.update(state.refraction.values.tColor, colorSource.texture);
  ValueCell.update(state.refraction.values.tTransparentColor, transparentColorSource.texture);
  ValueCell.update(state.refraction.values.tDepthOpaque, depthOpaque);
  ValueCell.update(state.refraction.values.tDepthTransparent, depthTransparent);
  ValueCell.update(
    state.refraction.values.uTexSize,
    Vec2.set(state.refraction.values.uTexSize.ref.value, width, height)
  );
  ValueCell.updateIfChanged(state.refraction.values.uNear, camera.near);
  ValueCell.updateIfChanged(state.refraction.values.uFar, camera.far);
  ValueCell.updateIfChanged(
    state.refraction.values.uIsOrtho,
    camera.state.mode === 'orthographic' ? 1 : 0
  );
  ValueCell.updateIfChanged(state.refraction.values.uGlassIOR, settings.ior);
  ValueCell.updateIfChanged(state.refraction.values.uRefractionStrength, settings.refractionStrength);
  ValueCell.updateIfChanged(state.refraction.values.uDispersionSpread, settings.dispersionSpread);
  ValueCell.updateIfChanged(state.refraction.values.uDiffusionSpread, settings.diffusionSpread);
  ValueCell.updateIfChanged(state.refraction.values.uRoughness, settings.roughness);
  ValueCell.updateIfChanged(state.refraction.values.uBumpiness, settings.bumpiness);
  ValueCell.updateIfChanged(state.refraction.values.uBumpFrequency, settings.bumpFrequency);
  ValueCell.updateIfChanged(state.refraction.values.uAbsorptionStrength, settings.absorptionStrength);
  ValueCell.updateIfChanged(state.refraction.values.uFresnelF0, settings.fresnelF0);

  state.refraction.update();

  const bounce = destination !== null;
  if (bounce) state.scratch.bind();
  else webgl.bindDrawingBuffer();
  beginQuad(webgl, viewport, !bounce);
  state.refraction.render(); const err1 = webgl.gl.getError(); if (err1 !== 0) window.GL_ERROR_1 = err1; console.log("after refraction:", webgl.gl.getError());

  if (bounce) {
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
    state.copy.render(); const err2 = webgl.gl.getError(); if (err2 !== 0) window.GL_ERROR_2 = err2; console.log("after copy:", webgl.gl.getError());
  }
  gl.flush();
}

/**
 * Installs the refraction shader pipeline hooks into Mol*'s render passes.
 */
export function installRefraction(): void {
  if (installed) return;
  installed = true;

  const originalPostprocessingRender = (PostprocessingPass.prototype as any).render;
  (PostprocessingPass.prototype as any).render = function (
    this: any,
    camera: any,
    scene: any,
    toDrawingBuffer: boolean,
    transparentBackground: boolean,
    backgroundColor: any,
    props: any,
    light: any,
    ambientColor: any,
    bloomEnable: boolean = false
  ) {
    const result = originalPostprocessingRender.call(
      this,
      camera,
      scene,
      toDrawingBuffer,
      transparentBackground,
      backgroundColor,
      props,
      light,
      ambientColor,
      bloomEnable
    );

    // If transparent primitives rendered, apply the screen-space refraction composite
    if (scene.opacityAverage < 1 && settings.enabled) {
      const destination = toDrawingBuffer ? null : this.target;
      applyRefraction(
        this,
        this.webgl,
        camera,
        this.drawPass.colorTarget,
        this.drawPass.transparentColorTarget,
        this.drawPass.depthTextureOpaque,
        this.drawPass.depthTextureTransparent,
        destination
      );
    }

    return result;
  };
}
