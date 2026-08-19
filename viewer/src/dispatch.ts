/** Maps bridge actions to Mol* plugin-state transactions.
 *
 * `plugin` is the PluginUIContext of the prebuilt Mol* viewer. Typed as `any`
 * because molstar is loaded as a prebuilt global rather than bundled (see
 * main.ts); Phase 2 can layer type-only imports on top if wanted.
 *
 * Actions are declared in one registry with an explicit `render` flag rather
 * than a hand-maintained name list, so a new action cannot forget the
 * hidden-tab pump.
 */

import type { Handler } from './bridge';

interface LoadStructureArgs {
  name: string;
  format: 'pdb' | 'mmcif';
  data: string;
  /**
   * Which molecule to build: the biological assembly (Mol*'s default, and a
   * tetramer for haemoglobin) or the deposited asymmetric unit. The server
   * makes the same choice for its own copy — analysis describing a different
   * molecule than the picture is a bug that hides until someone counts atoms.
   */
  assembly?: 'biological' | 'asymmetric';
}

interface ColorByVolumeArgs {
  /** Existing selection whose representations get recoloured. */
  name: string;
  /** OpenDX source. Both electrostatics backends emit this. */
  volume: string;
  /** 'absolute-value' reads the grid's own units; 'relative-value' normalises. */
  coloring?: 'absolute-value' | 'relative-value';
  /** Explicit [min, max]; omitted means a symmetric range about zero. */
  domain?: [number, number];
  /** Mol* colour list name; the default is the red-white-blue convention. */
  palette?: string;
}

interface SelectArgs {
  name: string;
  /** MolScript source, compiled from PyMOL syntax by the Python side. */
  expression: string;
  /** Cap on residues listed back; the count is always exact. */
  limit?: number;
}

interface ShowArgs extends SelectArgs {
  representation: string;
  color?: string;
  /** Scales the representation; for spacefill this scales the vdW radius. */
  size?: number;
  /** 0 invisible, 1 solid. Mol* calls this `alpha`. */
  opacity?: number;
  /** False makes this scenery: it cannot be clicked and never lights up. */
  pickable?: boolean;
}

interface EffectsArgs {
  outline?: boolean;
  /** Literal hex. Only meaningful with the outline on. */
  outline_color?: string;
  outline_scale?: number;
  occlusion?: boolean;
  shadow?: boolean;
  depth_of_field?: boolean;
  bloom?: boolean;
  sharpening?: boolean;
}

interface SetCameraArgs {
  position?: number[];
  target?: number[];
  up?: number[];
}

interface OrbitArgs {
  /** Degrees to swing the camera around the up axis, through the target. */
  degrees: number;
}

interface SpinArgs {
  /** 'spin', 'rock' or 'off'. */
  mode: string;
  /** Radians per second; Mol*'s own default is 1. */
  speed?: number;
  /** Rock only: how far it swings either side, in degrees. */
  angle?: number;
}

interface SnapshotArgs {
  /** Output width in pixels, computed by the Python side from mm and DPI. */
  width: number;
  /** Omitted means "keep the viewport's aspect". */
  height?: number;
  transparent?: boolean;
  /** Trim to the molecule's bounds, which changes the output dimensions. */
  crop?: boolean;
}

interface PathTraceArgs {
  /** Defaults to true; pass false to go back to ordinary rendering. */
  enabled?: boolean;
  /** A key of TRACE_QUALITY. */
  quality?: string;
  /** Light bounces, 1-16. Mol*'s default is 4. */
  bounces?: number;
  shadows?: boolean;
  denoise?: boolean;
}

interface MaterialArgs {
  name: string;
  /** A key of MATERIAL_FINISHES. */
  finish: string;
  /** Each 0-1, overriding the finish where given. */
  metalness?: number;
  roughness?: number;
  /** Self-illumination. Bloom's default mode only glows where this is > 0. */
  emissive?: number;
}

interface ShadingArgs {
  name: string;
  /** A key of SHADING_STYLES. */
  style: string;
  /** Cel band count, 2-16. Global, so it affects every cel-shaded thing. */
  cel_steps?: number;
}

interface LightingArgs {
  /** A key of LIGHTING_RIGS. */
  rig?: string;
  /** Scales every light in the rig; 1 leaves it as designed. */
  intensity?: number;
  /** Overrides the rig's ambient level. */
  ambient?: number;
  /** Mol*'s overall exposure, 0-3. */
  exposure?: number;
}

interface BackgroundArgs {
  /** Literal hex, e.g. "#ffffff". A canvas has no theme to look a name up in. */
  color?: string;
  /** Render onto nothing, so a figure drops into a document without a card. */
  transparent?: boolean;
  /** 'off', or a key of GRADIENTS. */
  gradient?: string;
  /** Top (horizontal) or centre (radial). */
  gradient_from?: string;
  /** Bottom (horizontal) or edge (radial). */
  gradient_to?: string;
  /** A URL or data URI for a flat image behind the scene. */
  image?: string;
  /** Six URLs or data URIs, keyed nx/ny/nz/px/py/pz. */
  skybox?: Record<string, string>;
  /** Softens whichever of the two is in use, 0-1. */
  blur?: number;
}

declare global {
  interface Window {
    __protean?: {
      setTurbo?: (on: boolean) => void;
      pumpState?: () => { turbo: boolean; queued: number };
      plugin?: any;
    };
  }
}

/** Colour ramps for scalar fields, low value first.
 *
 * red-white-blue is the electrostatics convention: acidic red, basic blue.
 * Mol* resolves preset names internally, but a ColorList *value* has to carry
 * real colours, so they are spelled out here.
 */
const PALETTES: Record<string, number[]> = {
  'red-white-blue': [0xd7191c, 0xffffff, 0x2c7bb6],
  'blue-white-red': [0x2c7bb6, 0xffffff, 0xd7191c],
  viridis: [0x440154, 0x21918c, 0xfde725],
  'white-red': [0xffffff, 0xd7191c],
};

/** Named lighting rigs, as generated light lists.
 *
 * Mol*'s `renderer.light` is an ObjectList of
 * `{inclination, azimuth, color, intensity}`, so a rig is data rather than a
 * feature — which is the whole reason there is an enum here instead of five
 * separate knobs. Angles follow Mol*'s own convention: inclination 0-180,
 * azimuth 0-360, and its single default light sits at 150/320.
 *
 * Intensities across rigs are kept roughly comparable so that switching one
 * does not silently double the exposure and read as a broken render.
 *
 * `ambient` is the ambient intensity that goes with the rig; Mol*'s default
 * is 0.4 against a single 0.6 light.
 */
const WHITE = 0xffffff;

function ring(count: number, inclination: number, intensity: number) {
  return Array.from({ length: count }, (_, i) => ({
    inclination,
    azimuth: Math.round((360 / count) * i),
    color: WHITE,
    intensity,
  }));
}

export const LIGHTING_RIGS: Record<
  string,
  { ambient: number; lights: Array<Record<string, number>> }
> = {
  // Mol*'s own defaults, so this is also the way back.
  standard: {
    ambient: 0.4,
    lights: [{ inclination: 150, azimuth: 320, color: WHITE, intensity: 0.6 }],
  },
  // No directional light at all — dLightCount 0 is valid and shaded purely by
  // ambient. Even and shadowless, which is what a schematic figure wants.
  flat: { ambient: 1.0, lights: [] },
  // Key, fill, back. The classic portrait rig: form from the key, shadows
  // opened by the fill, separation from the back light.
  'three-point': {
    ambient: 0.3,
    lights: [
      { inclination: 150, azimuth: 320, color: WHITE, intensity: 0.6 },
      { inclination: 120, azimuth: 60, color: WHITE, intensity: 0.25 },
      { inclination: 60, azimuth: 180, color: WHITE, intensity: 0.35 },
    ],
  },
  // Silhouette first: a weak key and a strong back light, so the edge reads
  // against the background. Good for showing a shape, poor for reading detail.
  rim: {
    ambient: 0.25,
    lights: [
      { inclination: 150, azimuth: 320, color: WHITE, intensity: 0.25 },
      { inclination: 60, azimuth: 180, color: WHITE, intensity: 0.9 },
    ],
  },
  // Six lights on a circle: soft and nearly shadowless, which suits a surface
  // whose curvature would otherwise disappear into one hard highlight.
  ring: { ambient: 0.25, lights: ring(6, 110, 0.18) },
  // Warm key against a cool fill, low contrast. The photographic look.
  studio: {
    ambient: 0.35,
    lights: [
      { inclination: 150, azimuth: 320, color: 0xfff4e6, intensity: 0.55 },
      { inclination: 120, azimuth: 140, color: 0xe6f0ff, intensity: 0.3 },
      { inclination: 50, azimuth: 200, color: WHITE, intensity: 0.4 },
    ],
  },
};

/** Complete parameter groups for the postprocessing effects we can switch on.
 *
 * Spelled out rather than toggled, because a Mol* MappedStatic that is `off`
 * carries `params: {}` — verified against a live canvas. Flipping only the name
 * would enable an effect with no parameters at all, which is the kind of input
 * Mol* accepts and then renders something arbitrary from.
 *
 * Values are Mol* 4.18's own defaults, from mol-canvas3d/passes/*.
 */
const EFFECT_PARAMS: Record<string, Record<string, unknown>> = {
  outline: { scale: 1, threshold: 0.33, color: 0x000000, includeTransparent: true },
  occlusion: {
    samples: 32,
    multiScale: { name: 'off', params: {} },
    radius: 5,
    bias: 0.8,
    blurKernelSize: 15,
    blurDepthBias: 0.5,
    resolutionScale: 1,
    color: 0x000000,
    transparentThreshold: 0.4,
  },
  shadow: { steps: 1, maxDistance: 3, tolerance: 1.0 },
  dof: {
    blurSize: 9,
    blurSpread: 1.0,
    inFocus: 0.0,
    PPM: 20.0,
    center: 'camera-target',
    mode: 'plane',
  },
  bloom: { strength: 1, radius: 0, threshold: 0, mode: 'emissive' },
  sharpening: { sharpness: 0.5, denoise: true },
};

/** Per-representation shading modes.
 *
 * Each is a Mol* representation parameter rather than a postprocessing pass,
 * so they travel the same route as `alpha` — and, like opacity, they apply to
 * a representation and not to a bare selection component.
 */
const SHADING_STYLES: Record<string, Record<string, unknown>> = {
  // Mol*'s own shading. Also the way back from any of the others.
  normal: { celShaded: false, xrayShaded: false, ignoreLight: false },
  // Banded, cartoon-like. Band count is renderer.celSteps, which is global.
  cel: { celShaded: true, xrayShaded: false, ignoreLight: false },
  // The ghost look: see-through with edges picked out.
  xray: { celShaded: false, xrayShaded: true, ignoreLight: false },
  // Inverts which parts fade, so the facing surface goes and the rim stays.
  'xray-inverted': { celShaded: false, xrayShaded: 'inverted', ignoreLight: false },
  // Unlit flat colour, for a diagram rather than a picture of an object.
  flat: { celShaded: false, xrayShaded: false, ignoreLight: true },
};

/** Path-trace quality, as sample counts.
 *
 * Mol*'s `illumination.maxIterations` is a power of two, so a caller working in
 * samples would have to compute a logarithm to ask for 128 of them. These names
 * carry the exponent and the reply reports the sample count it works out to.
 *
 * Measured on 1UBQ at 800x600 on an Apple GPU, per capture:
 *
 *   draft 1.2s (8 samples)   standard 4.1s (32)   high 15.8s (128)
 *
 * Cost is roughly 4x per step and scales with pixel count, so the same ladder
 * at figure resolution runs into minutes.
 */
const TRACE_QUALITY: Record<string, number> = {
  draft: 3,
  standard: 5,
  high: 7,
  ultra: 9,
};

/** Named surface finishes, as PBR material values.
 *
 * Two departures from the obvious, both forced by measuring what actually
 * changes on screen rather than by what the parameters are called.
 *
 * **Mol*'s own presets are not adopted.** `Material.getParam()` ships Matte,
 * Plastic, Glossy, Metallic — with Plastic at roughness 0.2 and Glossy at 0.6,
 * so their "glossy" is the duller of the two. Roughness runs 0 (mirror) to 1
 * (fully diffuse), so a model choosing `glossy` for a highlight would get the
 * opposite. These names run monotonically from dull to sharp, and avoid reusing
 * `plastic` with different numbers than Mol* attaches to it.
 *
 * **The non-metals carry a little metalness anyway.** The shader computes
 * `specularColor = mix(vec3(0.04), color.rgb, metalness)`, so a true dielectric
 * has a 4% specular term and roughness barely moves it: measured on 1UBQ,
 * roughness 1.0 to 0.2 at metalness 0 repaints 0.0017 of the frame — nothing.
 * At metalness 0.25 the same sweep separates cleanly. A named finish that does
 * not change the picture has no business being an enum value, so `satin` and
 * `glossy` are figure presets tuned to be visibly distinct rather than
 * physically accurate BRDF parameters. Anyone wanting the physical values can
 * pass metalness and roughness explicitly.
 *
 * `matte` is Mol*'s default material, so it is also the way back.
 */
const MATERIAL_FINISHES: Record<string, { metalness: number; roughness: number }> = {
  matte: { metalness: 0, roughness: 1.0 },
  satin: { metalness: 0.15, roughness: 0.6 },
  glossy: { metalness: 0.3, roughness: 0.15 },
  metallic: { metalness: 1.0, roughness: 0.6 },
  chrome: { metalness: 1.0, roughness: 0.1 },
};

/** Gradient background variants, keyed by the name we expose.
 *
 * Mol* names the two stops differently per variant — top/bottom for the
 * horizontal one, center/edge for the radial — so the mapping is spelled out
 * and both are exposed as a single from/to pair, which reads the same way
 * whichever is chosen.
 */
const GRADIENTS: Record<string, { variant: string; from: string; to: string }> = {
  horizontal: {
    variant: 'horizontalGradient',
    from: 'topColor',
    to: 'bottomColor',
  },
  radial: { variant: 'radialGradient', from: 'centerColor', to: 'edgeColor' },
};

/** Must stay below the bridge's own request timeout so our error wins the race. */
const HIDDEN_TIMEOUT_MS = 30_000;
/** Settling budget for a visible tab, where rAF runs and the work is real but
 * not instant. Shorter than the hidden budget: nothing is paused here, so a
 * wait this long means the commit loop is stuck rather than merely slow. */
const VISIBLE_TIMEOUT_MS = 10_000;
/** Budget once path tracing is on, where a single capture legitimately takes
 * minutes. Generous enough not to abort real work, bounded so that software
 * rendering — where tracing never finishes — still reports rather than hangs. */
const TRACED_TIMEOUT_MS = 300_000;
const DEFAULT_RESIDUE_LIMIT = 200;
/** Camera moves are tweened; this bounds the wait for one to land. */
const CAMERA_TIMEOUT_MS = 3_000;

export function isHidden(): boolean {
  return document.visibilityState !== 'visible';
}

async function settleRender(plugin: any, budgetMs: number): Promise<void> {
  const canvas3d = plugin.canvas3d;
  if (!canvas3d) return;

  // Both counters are only republished from inside the commit loop, so neither
  // is meaningful on its own (an untouched queue reads 0 exactly like a drained
  // one). Watching them for a few frames of no change is the honest signal that
  // the loop has finished its work.
  const sample = () =>
    `${canvas3d.commitQueueSize?.value ?? 0}/${canvas3d.reprCount?.value ?? 0}`;

  const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
  const start = performance.now();

  // Let the work start before stillness is read as completion. A state
  // transaction resolves before canvas3d has queued the geometry it implies,
  // so sampling immediately finds an untouched queue and calls it drained —
  // the same trap settleCamera documents, and the reason CI captured a blank
  // frame from a molecule that had definitely loaded.
  for (let i = 0; i < 3; i++) await frame();

  let previous = sample();
  let quiet = 0;
  while (quiet < 3 && performance.now() - start < budgetMs) {
    await frame();
    const current = sample();
    quiet = current === previous ? quiet + 1 : 0;
    previous = current;
  }
}

/** Waits for a camera move to land before its result is read.
 *
 * `focusLoci` does not update `camera.state` synchronously — the new position
 * is applied on the next render frame, and by default tweened over ~250ms on
 * top of that. Reading the state straight after the call returns the *old*
 * camera, or a mid-tween one; either way the reported target is wrong while
 * the camera itself ends up in the right place.
 */
/** Returns whether the camera actually came to rest inside its budget.
 *
 * **The budget expiring is a real outcome and used to be an invisible one.**
 * The loop simply ran out and returned, so a camera still travelling was
 * indistinguishable from one that had arrived, and the only symptom was a
 * figure framed for a scene that no longer existed — which is exactly the
 * silent success this project exists to catch. Whether it settled now travels
 * with the reply, so a mis-framed capture can say so instead of looking like a
 * measurement.
 */
async function settleCamera(plugin: any, budgetMs: number): Promise<boolean> {
  const camera = plugin.canvas3d?.camera;
  if (!camera) return true;
  const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));
  const sample = () => {
    const state = camera.state;
    return `${Array.from(state.target as ArrayLike<number>).join(',')}|${state.radius}`;
  };

  const start = performance.now();
  // Give the move a few frames to begin, so stillness beforehand is not
  // mistaken for having arrived.
  for (let i = 0; i < 3; i++) await frame();

  let previous = sample();
  let quiet = 0;
  while (quiet < 3 && performance.now() - start < budgetMs) {
    await frame();
    const current = sample();
    const moving = !!camera.transition?.inTransition || current !== previous;
    quiet = moving ? 0 : quiet + 1;
    previous = current;
  }
  return quiet >= 3;
}

async function withRenderPump<T>(
  plugin: any,
  action: string,
  run: () => Promise<T>
): Promise<T> {
  if (!isHidden()) {
    // A visible tab still has to settle. Mol* commits geometry on the render
    // loop *after* the state transaction resolves, so replying the moment the
    // transaction lands describes a scene the renderer has not built yet.
    // Harmless when the answer is a count; wrong when it is a picture — CI, on
    // a runner slow enough for the gap to open, screenshotted a molecule that
    // had loaded successfully and photographed an empty canvas.
    const result = await run();
    await settleRender(plugin, VISIBLE_TIMEOUT_MS);
    return result;
  }

  const setTurbo = window.__protean?.setTurbo;
  setTurbo?.(true);

  // A path-traced capture takes seconds to minutes, so the ordinary budget
  // would abort work that was going to succeed. Measured on 1UBQ at 800x600:
  // 4.1s at standard quality, 15.8s at high, and both scale with pixel count.
  const traced = !!plugin.canvas3d?.props?.illumination?.enabled;
  const budget = traced ? TRACED_TIMEOUT_MS : HIDDEN_TIMEOUT_MS;

  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      const pump = setTurbo ? 'the hidden-tab render pump is active' : 'no render pump is installed';
      reject(
        new Error(
          `'${action}' did not finish within ${budget / 1000}s while the ` +
            `viewer tab was hidden (visibilityState=${document.visibilityState}, ${pump}` +
            `${traced ? ', path tracing is on' : ''}). ` +
            (traced
              ? `Path tracing needs a real GPU; under software rendering a single ` +
                `capture can take longer than this. Lower path_trace(quality=...), ` +
                `or turn it off.`
              : `Browsers pause requestAnimationFrame in background tabs, which Mol* ` +
                `needs to build representations. Bring the protean tab to the front ` +
                `and retry.`)
        )
      );
    }, budget);
  });

  const settled = (async () => {
    const result = await run();
    await settleRender(plugin, budget);
    return result;
  })();

  try {
    // The losing side keeps running — Mol* has no cancellation hook here — but
    // reporting a cause beats hanging until the bridge gives up.
    return await Promise.race([settled, deadline]);
  } finally {
    clearTimeout(timer);
    setTurbo?.(false);
  }
}

/** Summarise what a selection actually resolved to.
 *
 * The point of returning this is that the agent never has to infer a
 * selection's contents from a picture: PyMOL reports a bare count and makes you
 * `iterate` for the rest.
 */
export function summarise(structure: any, limit: number) {
  const residues: Array<Record<string, unknown>> = [];
  const chains = new Set<string>();
  const seen = new Set<string>();

  for (const unit of structure.units) {
    const h = unit.model.atomicHierarchy;
    const residueIndex = h.residueAtomSegments.index;
    const chainIndex = h.chainAtomSegments.index;
    const elements = unit.elements;
    for (let i = 0, n = elements.length; i < n; i++) {
      const element = elements[i];
      const ri = residueIndex[element];
      const key = `${unit.model.id}:${ri}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const chain = h.chains.auth_asym_id.value(chainIndex[element]);
      chains.add(chain);
      if (residues.length < limit) {
        // comp_id is carried on the atom table, not the residue table.
        const entry: Record<string, unknown> = {
          chain,
          seq: h.residues.auth_seq_id.value(ri),
          comp: h.atoms.label_comp_id.value(element),
        };
        const insertion = h.residues.pdbx_PDB_ins_code.value(ri);
        if (insertion) entry.ins_code = insertion;
        residues.push(entry);
      }
    }
  }

  return {
    atom_count: structure.elementCount,
    residue_count: seen.size,
    chains: Array.from(chains).sort(),
    residues,
    truncated: seen.size > residues.length,
  };
}

/** Handle for the representations `applyPreset` builds on load.
 *
 * Without this they are unreachable: `show` layers new components on top of
 * them, so hiding your own selection leaves the automatic cartoon still drawn
 * and nothing appears to happen. */
const AUTO = 'auto';

interface Entry {
  refs: string[];
}

export function createDispatcher(plugin: any): Handler {
  takeTheCameraOffAutomaticFitting(plugin);

  /** Named components, so later show/color calls can target an earlier select. */
  const components = new Map<string, Entry>();

  /** Loaded volumes by handle: the parsed data plus the state refs behind it.
   *
   * `downloadRef` is the ghost Download node holding the raw bytes. Deleting
   * only the parsed volume leaves those bytes resident, so load/remove cycles
   * would accumulate exactly the maps this feature exists not to hold. */
  const volumes = new Map<
    string,
    {
      ref: string;
      downloadRef?: string;
      data: any;
      provenance: string;
      format: string;
      /** Computed once at load. Deterministic for a given grid, and two full
       *  linear passes over up to 10^8 voxels is not a thing to repeat on
       *  every volume_info, list_volumes and contour adjustment. */
      stats: ReturnType<typeof volumeStats>;
      /** The isosurface node, once one has been asked for. */
      reprRef?: string;
    }
  >();

  function requireVolume(name: string) {
    const entry = volumes.get(name);
    if (!entry) {
      const known = [...volumes.keys()].sort().join(', ') || '(none)';
      throw new Error(`No volume named '${name}'. Known: ${known}`);
    }
    return entry;
  }

  /** Delete a volume's state nodes, parsed and raw, and forget the handle. */
  async function dropVolume(name: string) {
    const entry = volumes.get(name);
    volumes.delete(name);
    if (!entry) return;
    const build = plugin.state.data.build();
    // The isosurface first: it is a child of the volume node, and deleting a
    // parent leaves nothing to hang a stale handle on either way, but naming
    // it keeps the intent visible.
    for (const ref of [entry.reprRef, entry.ref, entry.downloadRef]) {
      if (ref) build.delete(ref);
    }
    await build.commit();
  }

  /** Forget every volume handle, for the paths that wipe the state tree.
   *
   * `plugin.clear()` removes the volume nodes but cannot know about this Map,
   * and the statistics are computed from a `data` object the Map keeps alive.
   * Without this, `volume_info` answers fully and plausibly about a volume the
   * viewer no longer holds — the exact "returns cleanly, describes nothing"
   * failure these tools were written to refuse. */
  function forgetVolumes() {
    volumes.clear();
  }

  /** Grid statistics computed from the voxels, plus whatever the file claimed.
   *
   * These are the only way a caller can convert a published absolute contour
   * into sigma, so they have to describe the data being drawn.
   *
   * `grid.stats` does NOT do that for CCP4/MRC: those four numbers are stored
   * fields in the file header, and Mol* passes them through. A file whose
   * header says one thing and whose voxels say another — a cropped or rescaled
   * map, or a header nobody updated — reports the header and looks healthy.
   * That was not a hypothesis: a fixture written with deliberately false header
   * statistics reported them back verbatim, dimensions and all, while the
   * voxels said something else entirely.
   *
   * So the voxels are walked. Two passes rather than one, because a
   * sum-of-squares pass computed against a running mean loses precision on the
   * ~10^7-voxel grids this is for, and 2x a linear scan is cheap next to the
   * download and parse that just happened.
   *
   * `stated` is kept alongside rather than dropped: a large disagreement is
   * itself information — it says the file has been through something. */
  function volumeStats(data: any) {
    const grid = data.grid;
    const space = grid.cells.space;
    const [nx, ny, nz] = space.dimensions;
    const values = grid.cells.data;
    const n = values.length;

    if (!n) {
      throw new Error(
        'volume parsed to a grid with no voxels — the bytes were accepted but hold nothing'
      );
    }

    let min = Infinity;
    let max = -Infinity;
    let sum = 0;
    for (let i = 0; i < n; i++) {
      const v = values[i];
      if (v < min) min = v;
      if (v > max) max = v;
      sum += v;
    }
    const mean = sum / n;

    let squares = 0;
    for (let i = 0; i < n; i++) {
      const d = values[i] - mean;
      squares += d * d;
    }
    const sigma = Math.sqrt(squares / n);

    const stated = grid.stats ?? {};
    return {
      dimensions: [nx, ny, nz],
      voxels: n,
      min,
      max,
      mean,
      sigma,
      stated: {
        min: stated.min ?? null,
        max: stated.max ?? null,
        mean: stated.mean ?? null,
        sigma: stated.sigma ?? null,
      },
    };
  }

  const currentStructure = () => {
    const current = plugin.managers.structure.hierarchy.current.structures[0];
    if (!current) throw new Error('No structure loaded — call fetch_structure first.');
    return current;
  };

  const structureRef = () => currentStructure().cell.transform.ref;
  const rootStructure = () => currentStructure().cell.obj.data;

  async function component(name: string, expression: string) {
    const existing = components.get(name);
    if (existing) {
      for (const ref of existing.refs) {
        await plugin.state.data.build().delete(ref).commit();
      }
      components.delete(name);
    }
    const selector = await plugin.builders.structure.tryCreateComponent(
      structureRef(),
      {
        type: { name: 'script', params: { language: 'mol-script', expression } },
        nullIfEmpty: false,
        label: name,
      },
      `protean-${name}`
    );
    if (selector) components.set(name, { refs: [selector.ref] });
    return selector;
  }

  function dataOf(selector: any) {
    return selector?.data ?? selector?.cell?.obj?.data ?? undefined;
  }

  /** Resolve a handle's structure from the state tree.
   *
   * Reading through the refs rather than a captured selector is what lets a
   * handle survive a session restore, where no selector object exists. */
  function structureOf(entry: Entry) {
    for (const ref of entry.refs) {
      const data = plugin.state.data.cells.get(ref)?.obj?.data;
      if (data) return data;
    }
    return undefined;
  }

  /** Names Mol* accepts, read from the live registries rather than hardcoded,
   * so the list cannot drift from the bundled version. */
  const registryNames = (registry: any): string[] => {
    try {
      return (registry?.types ?? []).map((t: any) => t[0]);
    } catch {
      return [];
    }
  };

  const representationTypes = () =>
    registryNames(plugin.representation?.structure?.registry);
  const colorThemeNames = () =>
    registryNames(plugin.representation?.structure?.themes?.colorThemeRegistry);

  /** Which params a representation actually accepts, for the same reason we
   * validate its name: an unsupported one is ignored rather than refused. */
  function representationParams(type: string): string[] | null {
    try {
      const provider = plugin.representation.structure.registry.get(type);
      const params = provider.getParams(
        plugin.representation.structure.themes,
        rootStructure()
      );
      return Object.keys(params);
    } catch (err) {
      // Returning [] here would make the size check quietly pass for every
      // representation, which is the failure mode this whole layer exists to
      // avoid. null means "could not introspect" and is reported as such.
      console.warn(`protean: cannot read params for '${type}':`, err);
      return null;
    }
  }

  /** Mol* accepts an unknown representation or theme name without complaint and
   * then draws nothing, so a typo would look like an empty selection. */
  function checkName(kind: string, value: string, valid: string[]) {
    if (!valid.length || valid.includes(value)) return;
    throw new Error(
      `Unknown ${kind} '${value}'. Available: ${valid.sort().join(', ')}`
    );
  }

  function known(): string[] {
    return [...components.keys()].sort();
  }

  function require(name: string): Entry {
    const entry = components.get(name);
    if (!entry) {
      throw new Error(
        `No selection named '${name}'. Known: ${known().join(', ') || '(none)'}`
      );
    }
    return entry;
  }

  /** All components the hierarchy currently knows about. */
  function allComponents() {
    return plugin.managers.structure.hierarchy.current.structures.flatMap(
      (s: any) => s.components ?? []
    );
  }

  /** The hierarchy's view of our refs, which is what the managers act on. */
  function hierarchyComponents(refs: string[]) {
    const wanted = new Set(refs);
    return allComponents().filter((c: any) => wanted.has(c.cell.transform.ref));
  }

  function isHiddenComponent(c: any): boolean {
    return !!c.cell?.state?.isHidden;
  }

  /** Hiding a component hides its representation subtree along with it.
   *
   * Goes through the component manager: `updateCellState` flips the flag in the
   * state tree but never reaches the renderer, so the scene keeps drawing the
   * "hidden" component. The manager's two-argument form is not usable either —
   * passing `true` throws inside Mol* — but the no-argument form flips, which
   * is all we need once we have read the current state.
   */
  async function setHidden(name: string, hidden: boolean) {
    const entry = require(name);
    const found = hierarchyComponents(entry.refs);
    if (!found.length) {
      throw new Error(`Selection '${name}' has no component in the hierarchy to hide`);
    }
    let changed = 0;
    for (const c of found) {
      if (isHiddenComponent(c) === hidden) continue;
      await plugin.managers.structure.component.toggleVisibility([c]);
      changed++;
    }
    return { name, hidden, components: found.length, changed };
  }

  const actions: Record<
    string,
    { render?: boolean; camera?: boolean; run: (args: any) => Promise<unknown> }
  > = {
    load_structure: {
      render: true,
      // The preset frames the new molecule, and Mol* tweens that over ~250 ms
      // like any other camera move. `render: true` waits for the geometry to
      // stop changing, which says nothing about the camera, so without this a
      // capture taken straight after a load could be mid-flight. focus, orient
      // and reset_view have always waited; this one never did, and the symptom
      // is a figure framed slightly wrong rather than an error.
      camera: true,
      async run({ name, format, data, assembly }: LoadStructureArgs) {
        components.clear();
        forgetVolumes();
        // Everything downstream reads structures[0], so a second load without
        // clearing leaves every later answer describing the *first* structure.
        // That is how a reload with different assembly settings silently kept
        // reporting the old molecule's atom count.
        await plugin.clear();
        const raw = await plugin.builders.data.rawData({ data, label: name });
        const trajectory = await plugin.builders.structure.parseTrajectory(
          raw,
          format === 'pdb' ? 'pdb' : 'mmcif'
        );
        // The preset builds the assembly by default; 'model' is the deposited
        // asymmetric unit instead.
        const structureParams =
          assembly === 'asymmetric' ? { name: 'model', params: {} } : undefined;
        await plugin.builders.structure.hierarchy.applyPreset(
          trajectory,
          'default',
          structureParams ? { structure: structureParams } : undefined
        );
        // The fit the preset used to get for free. Automatic fitting is off
        // (see `takeTheCameraOffAutomaticFitting`), and a load replaces a scene
        // that was already framed, so the first-fit rule in the dispatcher will
        // not fire — this is the one place that asks. `camera: true` above
        // waits for it to arrive.
        plugin.managers.camera.reset();
        // Register the preset's own representations under a reserved handle so
        // they can be hidden or removed like any other selection.
        const auto = allComponents().map((c: any) => c.cell.transform.ref);
        if (auto.length) components.set(AUTO, { refs: auto });
        // Report what was actually built. The server holds its own copy of the
        // same molecule, and this is the number that proves the two agree.
        const built = plugin.managers.structure.hierarchy.current.structures[0];
        const atomCount = built?.cell?.obj?.data?.elementCount ?? 0;
        return {
          loaded: name,
          auto_components: auto.length,
          atom_count: atomCount,
          assembly: assembly ?? 'biological',
        };
      },
    },

    select: {
      render: true,
      async run({ name, expression, limit }: SelectArgs) {
        const selector = await component(name, expression);
        const structure = dataOf(selector);
        if (!structure) {
          return { name, atom_count: 0, residue_count: 0, chains: [], residues: [], truncated: false };
        }
        return { name, ...summarise(structure, limit ?? DEFAULT_RESIDUE_LIMIT) };
      },
    },

    show: {
      render: true,
      async run({
        name,
        expression,
        representation,
        color,
        size,
        opacity,
        pickable,
        limit,
      }: ShowArgs) {
        checkName('representation', representation, representationTypes());
        if (color && !color.startsWith('#')) {
          checkName('colour theme', color, colorThemeNames());
        }
        let sizeValidated: boolean | undefined;
        if (size !== undefined) {
          const accepted = representationParams(representation);
          sizeValidated = accepted !== null;
          if (accepted && !accepted.includes('sizeFactor')) {
            throw new Error(
              `Representation '${representation}' has no size control. ` +
                `It accepts: ${accepted.filter((k) => /size|scale/i.test(k)).join(', ') || '(no size-like params)'}`
            );
          }
        }
        let opacityValidated: boolean | undefined;
        if (opacity !== undefined) {
          checkOpacity(opacity);
          const accepted = representationParams(representation);
          opacityValidated = accepted !== null;
          if (accepted && !accepted.includes('alpha')) {
            throw new Error(`Representation '${representation}' has no opacity control.`);
          }
        }
        const selector = await component(name, expression);
        const structure = dataOf(selector);
        if (!structure || structure.elementCount === 0) {
          return { name, representation, atom_count: 0, residue_count: 0, chains: [], residues: [], truncated: false };
        }
        const params: Record<string, unknown> = { type: representation };
        if (color) Object.assign(params, colorParams(color));
        const typeParams: Record<string, unknown> = {};
        if (size !== undefined) typeParams.sizeFactor = size;
        if (opacity !== undefined) typeParams.alpha = opacity;
        if (Object.keys(typeParams).length) params.typeParams = typeParams;
        const built = await plugin.builders.structure.representation.addRepresentation(
          selector,
          params
        );
        if (pickable === false) markAsScenery(built);
        return {
          name,
          representation,
          ...(size !== undefined ? { size, size_validated: sizeValidated } : {}),
          ...(opacity !== undefined
            ? { opacity, opacity_validated: opacityValidated }
            : {}),
          ...summarise(structure, limit ?? DEFAULT_RESIDUE_LIMIT),
        };
      },
    },

    opacity: {
      render: true,
      async run({ name, opacity }: { name: string; opacity: number }) {
        checkOpacity(opacity);
        const entry = require(name);
        const target = hierarchyComponents(entry.refs);
        if (!target.length) {
          throw new Error(`Selection '${name}' has no component in the hierarchy`);
        }
        // Opacity lives on the representation, not on the component. Updating a
        // bare handle would commit an empty transaction and report success
        // while nothing on screen changed — the same shape as the recolouring
        // bug, so this refuses instead.
        const update = plugin.state.data.build();
        let changed = 0;
        for (const c of target) {
          for (const repr of c.representations ?? []) {
            update.to(repr.cell).update((old: any) => {
              old.type.params.alpha = opacity;
            });
            changed++;
          }
        }
        if (!changed) {
          throw new Error(
            `Selection '${name}' has no representation to make transparent. ` +
              `Call show() on it first.`
          );
        }
        await update.commit();
        return { name, opacity, representations: changed };
      },
    },

    effects: {
      render: true,
      async run(args: EffectsArgs) {
        const canvas3d = plugin.canvas3d;
        if (!canvas3d) throw new Error('No 3D canvas yet — load a structure first.');

        const toggles: Array<[keyof EffectsArgs, string]> = [
          ['outline', 'outline'],
          ['occlusion', 'occlusion'],
          ['shadow', 'shadow'],
          ['depth_of_field', 'dof'],
          ['bloom', 'bloom'],
          ['sharpening', 'sharpening'],
        ];

        const post: Record<string, unknown> = {};
        for (const [arg, key] of toggles) {
          const wanted = args[arg];
          if (wanted === undefined) continue;
          post[key] = wanted
            ? { name: 'on', params: { ...EFFECT_PARAMS[key] } }
            : { name: 'off', params: {} };
        }

        if (args.outline_color !== undefined || args.outline_scale !== undefined) {
          // Tuning an outline that is off would be silently ignored, so say so
          // rather than let the caller believe it took.
          const enabled = args.outline ?? canvas3d.props?.postprocessing?.outline?.name === 'on';
          if (!enabled) {
            throw new Error('Set outline=true before adjusting its colour or scale');
          }
          const params: Record<string, unknown> = {
            ...EFFECT_PARAMS.outline,
            ...(canvas3d.props?.postprocessing?.outline?.params ?? {}),
          };
          if (args.outline_color !== undefined) {
            params.color = parseHexColor(args.outline_color);
          }
          if (args.outline_scale !== undefined) params.scale = args.outline_scale;
          post.outline = { name: 'on', params };
        }

        if (!Object.keys(post).length) {
          throw new Error('Pass at least one effect to change');
        }
        canvas3d.setProps({ postprocessing: post });

        return effectState(canvas3d);
      },
    },

    frame: {
      render: true,
      async run({ index }: { index: number }) {
        const current = plugin.managers.structure.hierarchy.current;
        const model = current.models?.[0];
        if (!model) throw new Error('No structure loaded — call fetch_structure first.');

        // frameCount lives on the trajectory, not the model: the model is one
        // frame of it, selected by modelIndex.
        const trajectory = current.trajectories?.[0];
        const frames = trajectory?.cell?.obj?.data?.frameCount ?? 1;
        if (frames < 2) {
          throw new Error(
            'This structure has a single frame. Load a trajectory onto it first.'
          );
        }
        if (!Number.isInteger(index) || index < 0 || index >= frames) {
          throw new Error(`Frame ${index} is outside 0..${frames - 1}`);
        }

        await plugin.state.data
          .build()
          .to(model.cell.transform.ref)
          .update({ ...model.cell.transform.params, modelIndex: index })
          .commit();

        // Read back off the rebuilt cell rather than echoing: a refused update
        // leaves the previous frame in place and the scene simply looks stale.
        const now = plugin.managers.structure.hierarchy.current.models?.[0];
        return {
          index: now?.cell?.transform?.params?.modelIndex ?? null,
          frames,
        };
      },
    },

    camera_state: {
      async run() {
        const camera = plugin.canvas3d?.camera;
        if (!camera) throw new Error('No 3D canvas yet — load a structure first.');
        const snapshot = camera.getSnapshot();
        return {
          position: Array.from(snapshot.position as ArrayLike<number>),
          target: Array.from(snapshot.target as ArrayLike<number>),
          up: Array.from(snapshot.up as ArrayLike<number>),
          radius: snapshot.radius ?? null,
        };
      },
    },

    set_camera: {
      render: true,
      async run({ position, target, up }: SetCameraArgs) {
        const camera = plugin.canvas3d?.camera;
        if (!camera) throw new Error('No 3D canvas yet — load a structure first.');
        for (const [name, value] of [
          ['position', position],
          ['target', target],
          ['up', up],
        ] as Array<[string, number[] | undefined]>) {
          if (value !== undefined && (value.length !== 3 || value.some((n) => !Number.isFinite(n)))) {
            throw new Error(`Camera ${name} must be three finite numbers, got ${JSON.stringify(value)}`);
          }
        }

        const snapshot = camera.getSnapshot();
        camera.setState(
          {
            ...snapshot,
            ...(position !== undefined ? { position } : {}),
            ...(target !== undefined ? { target } : {}),
            ...(up !== undefined ? { up } : {}),
          },
          // No tween: a frame of a timeline wants the camera exactly where the
          // interpolation put it, not somewhere on the way there.
          0
        );
        await settleCamera(plugin, CAMERA_TIMEOUT_MS);

        const now = camera.getSnapshot();
        return {
          position: Array.from(now.position as ArrayLike<number>),
          target: Array.from(now.target as ArrayLike<number>),
          up: Array.from(now.up as ArrayLike<number>),
        };
      },
    },

    orbit: {
      render: true,
      async run({ degrees }: OrbitArgs) {
        const camera = plugin.canvas3d?.camera;
        if (!camera) throw new Error('No 3D canvas yet — load a structure first.');
        if (!Number.isFinite(degrees)) {
          throw new Error(`Orbit needs a number of degrees, got ${degrees}`);
        }

        // Rotate the camera about the up axis, through the point it is looking
        // at, rather than driving Mol*'s spin animation and sampling it. A
        // frame sequence has to be reproducible: sampling a live animation
        // makes each frame depend on when it was taken.
        const snapshot = camera.getSnapshot();
        const target = Array.from(snapshot.target as ArrayLike<number>);
        const position = Array.from(snapshot.position as ArrayLike<number>);
        const up = Array.from(snapshot.up as ArrayLike<number>);

        const moved = rotateAbout(
          [position[0] - target[0], position[1] - target[1], position[2] - target[2]],
          up,
          (degrees * Math.PI) / 180
        );
        camera.setState(
          {
            ...snapshot,
            position: [target[0] + moved[0], target[1] + moved[1], target[2] + moved[2]],
          },
          // No tween: a frame grab wants the camera where it was put, now.
          0
        );
        await settleCamera(plugin, CAMERA_TIMEOUT_MS);

        const now = camera.getSnapshot();
        return {
          degrees,
          position: Array.from(now.position as ArrayLike<number>),
          target: Array.from(now.target as ArrayLike<number>),
        };
      },
    },

    spin: {
      render: true,
      async run({ mode, speed, angle }: SpinArgs) {
        const canvas3d = plugin.canvas3d;
        if (!canvas3d) throw new Error('No 3D canvas yet — load a structure first.');
        const modes = ['off', 'spin', 'rock'];
        if (!modes.includes(mode)) {
          throw new Error(`Unknown spin mode '${mode}'. Available: ${modes.join(', ')}`);
        }
        if (speed !== undefined && (!Number.isFinite(speed) || speed <= 0)) {
          throw new Error(`Spin speed must be above 0, got ${speed}`);
        }

        const params: Record<string, unknown> =
          mode === 'rock'
            ? { speed: speed ?? 0.3, angle: angle ?? 10 }
            : mode === 'spin'
              ? { speed: speed ?? 1 }
              : {};
        canvas3d.setProps({ trackball: { animate: { name: mode, params } } });

        const applied = canvas3d.props?.trackball?.animate;
        return {
          mode: applied?.name ?? null,
          speed: applied?.params?.speed ?? null,
          angle: applied?.params?.angle ?? null,
        };
      },
    },

    snapshot: {
      render: true,
      async run({ width, height, transparent, crop }: SnapshotArgs) {
        const helper = plugin.helpers?.viewportScreenshot;
        if (!helper?.getImageDataUri) {
          throw new Error('This Mol* build has no viewport screenshot helper');
        }
        if (!Number.isInteger(width) || width < 1) {
          throw new Error(`Snapshot width must be a whole number of pixels, got ${width}`);
        }

        // Keep the figure proportioned like what is on screen unless a height
        // was given, rather than defaulting to a square nobody asked for.
        const viewport = helper.getSizeAndViewport?.() ?? {};
        const aspect =
          viewport.height && viewport.width ? viewport.height / viewport.width : 0.75;
        const tall = height ?? Math.max(1, Math.round(width * aspect));

        // Everything the helper is about to be told, so it can be put back.
        // These values persist on the helper: without restoring them, the next
        // ordinary screenshot would come back at figure resolution.
        const previousValues = { ...helper.values };
        const previousCrop = { ...helper.cropParams };

        try {
          helper.behaviors.values.next({
            ...helper.values,
            resolution: { name: 'custom', params: { width, height: tall } },
            // Always PNG out of Mol*: lossless, and the only format here that
            // carries an alpha channel. Python converts onward from that.
            format: { name: 'png', params: {} },
            ...(transparent !== undefined ? { transparent } : {}),
          });
          if (crop) helper.autocrop(0.05);
          else helper.resetCrop();

          // Build the pass before capturing, as `screenshot` does — a capture
          // through a freshly created pass differs from the ones after it.
          const pass = helper.imagePass;
          if (!pass) throw new Error('Mol* built no image pass for the snapshot');

          const traced = !!plugin.canvas3d?.props?.illumination?.enabled;
          const started = performance.now();
          const data_uri = await helper.getImageDataUri();
          const elapsed = Math.round(performance.now() - started);

          return {
            data_uri,
            // What was asked for. The decoded image is the authority on what
            // arrived, and cropping deliberately changes it.
            requested_width: width,
            requested_height: tall,
            cropped: !!crop,
            // Whether this capture was actually transparent, so the Python side
            // knows whether empty pixels in the result are expected or evidence
            // that the render never finished.
            transparent: !!helper.values.transparent,
            ...(traced ? { traced_ms: elapsed } : { elapsed_ms: elapsed }),
          };
        } finally {
          helper.behaviors.values.next(previousValues);
          helper.behaviors.cropParams.next(previousCrop);
        }
      },
    },

    path_trace: {
      render: true,
      async run({ enabled, quality, bounces, shadows, denoise }: PathTraceArgs) {
        const canvas3d = plugin.canvas3d;
        if (!canvas3d) throw new Error('No 3D canvas yet — load a structure first.');

        const on = enabled ?? true;
        const chosen = quality ?? 'standard';
        const iterations = TRACE_QUALITY[chosen];
        if (iterations === undefined) {
          throw new Error(
            `Unknown path-trace quality '${chosen}'. ` +
              `Available: ${Object.keys(TRACE_QUALITY).sort().join(', ')}`
          );
        }
        if (bounces !== undefined && (!Number.isInteger(bounces) || bounces < 1 || bounces > 16)) {
          throw new Error(`bounces must be a whole number from 1 to 16, got ${bounces}`);
        }

        // The four extensions IlluminationPass requires. It does not throw when
        // they are missing — the constructor returns early and leaves the pass
        // permanently unsupported — so an unchecked enable would render an
        // ordinary raster image and report success.
        const extensions = canvas3d.webgl?.extensions ?? {};
        const required = ['textureFloat', 'colorBufferFloat', 'depthTexture', 'drawBuffers'];
        const missing = required.filter((name) => !extensions[name]);
        if (on && missing.length) {
          throw new Error(
            `This browser cannot path trace: WebGL is missing ${missing.join(', ')}. ` +
              `Mol* would silently fall back to ordinary rendering.`
          );
        }

        const illumination: Record<string, unknown> = { enabled: on, maxIterations: iterations };
        if (bounces !== undefined) illumination.bounces = bounces;
        if (shadows !== undefined) illumination.shadowEnable = shadows;
        if (denoise !== undefined) illumination.denoise = denoise;
        canvas3d.setProps({ illumination });

        const applied = canvas3d.props?.illumination ?? {};
        return {
          // Read back: enabling a pass Mol* cannot build leaves this false.
          enabled: applied.enabled ?? null,
          quality: chosen,
          // Also read back rather than computed from the argument. Reporting
          // 2**iterations would state the sample count that was *asked for*,
          // which stays right even when the canvas took something else — the
          // mutation that pinned maxIterations to a constant passed the whole
          // suite until this line stopped echoing.
          samples: applied.maxIterations !== undefined ? 2 ** applied.maxIterations : null,
          bounces: applied.bounces ?? null,
          shadows: applied.shadowEnable ?? null,
          denoise: applied.denoise ?? null,
          // The headline cost. Every capture from here on runs the tracer.
          note: on
            ? 'Every screenshot now path traces, which takes seconds to minutes.'
            : 'Back to ordinary rendering.',
        };
      },
    },

    material: {
      render: true,
      async run({ name, finish, metalness, roughness, emissive }: MaterialArgs) {
        const base = MATERIAL_FINISHES[finish];
        if (!base) {
          throw new Error(
            `Unknown finish '${finish}'. ` +
              `Available: ${Object.keys(MATERIAL_FINISHES).sort().join(', ')}`
          );
        }
        const overrides: Array<[string, number | undefined]> = [
          ['metalness', metalness],
          ['roughness', roughness],
        ];
        // Mol*'s material group also carries bumpiness. It is not exposed —
        // it does nothing unless bumpFrequency is above 0, and that defaults to
        // 0 — but the group is sent complete so nothing is left undefined.
        const material: Record<string, number> = { ...base, bumpiness: 0 };
        for (const [key, value] of overrides) {
          if (value === undefined) continue;
          checkFraction(key, value);
          material[key] = value;
        }
        if (emissive !== undefined) checkFraction('emissive', emissive);

        const entry = require(name);
        const target = hierarchyComponents(entry.refs);
        if (!target.length) {
          throw new Error(`Selection '${name}' has no component in the hierarchy`);
        }

        const update = plugin.state.data.build();
        let changed = 0;
        for (const c of target) {
          for (const repr of c.representations ?? []) {
            update.to(repr.cell).update((old: any) => {
              old.type.params.material = { ...material };
              if (emissive !== undefined) old.type.params.emissive = emissive;
            });
            changed++;
          }
        }
        if (!changed) {
          throw new Error(
            `Selection '${name}' has no representation to give a material to. ` +
              `Call show() on it first.`
          );
        }
        await update.commit();

        const bloom = plugin.canvas3d?.props?.postprocessing?.bloom;
        return {
          name,
          finish,
          representations: changed,
          ...material,
          ...(emissive !== undefined ? { emissive } : {}),
          // Bloom defaults to mode 'emissive', so it draws nothing at all until
          // something has emissive above zero. Saying so here is the difference
          // between "bloom is broken" and "bloom has nothing to glow".
          bloom_will_show: emissive !== undefined && emissive > 0 && bloom?.name === 'on',
        };
      },
    },

    shading: {
      render: true,
      async run({ name, style, cel_steps }: ShadingArgs) {
        const params = SHADING_STYLES[style];
        if (!params) {
          throw new Error(
            `Unknown shading style '${style}'. ` +
              `Available: ${Object.keys(SHADING_STYLES).sort().join(', ')}`
          );
        }
        const entry = require(name);
        const target = hierarchyComponents(entry.refs);
        if (!target.length) {
          throw new Error(`Selection '${name}' has no component in the hierarchy`);
        }

        // celSteps is a renderer property, so it is global rather than
        // per-representation. Setting it here is the honest place for it —
        // it only means anything once something is cel shaded.
        if (cel_steps !== undefined) {
          if (!Number.isInteger(cel_steps) || cel_steps < 2 || cel_steps > 16) {
            throw new Error(`cel_steps must be a whole number from 2 to 16, got ${cel_steps}`);
          }
          plugin.canvas3d?.setProps({ renderer: { celSteps: cel_steps } });
        }

        const update = plugin.state.data.build();
        let changed = 0;
        for (const c of target) {
          for (const repr of c.representations ?? []) {
            update.to(repr.cell).update((old: any) => {
              Object.assign(old.type.params, params);
            });
            changed++;
          }
        }
        if (!changed) {
          throw new Error(
            `Selection '${name}' has no representation to shade. Call show() on it first.`
          );
        }
        await update.commit();
        return {
          name,
          style,
          representations: changed,
          cel_steps: plugin.canvas3d?.props?.renderer?.celSteps ?? null,
        };
      },
    },

    lighting: {
      render: true,
      async run({ rig, intensity, ambient, exposure }: LightingArgs) {
        const canvas3d = plugin.canvas3d;
        if (!canvas3d) throw new Error('No 3D canvas yet — load a structure first.');

        const chosen = rig ?? 'standard';
        const preset = LIGHTING_RIGS[chosen];
        if (!preset) {
          throw new Error(
            `Unknown lighting rig '${chosen}'. ` +
              `Available: ${Object.keys(LIGHTING_RIGS).sort().join(', ')}`
          );
        }
        if (intensity !== undefined && (!Number.isFinite(intensity) || intensity < 0)) {
          throw new Error(`Lighting intensity must be 0 or more, got ${intensity}`);
        }

        const scale = intensity ?? 1;
        const renderer: Record<string, unknown> = {
          // A fresh array every call: Mol* holds this list by reference, and
          // mutating a shared preset would leave the next caller with whatever
          // the last one scaled it to.
          light: preset.lights.map((light) => ({
            ...light,
            intensity: light.intensity * scale,
          })),
          ambientIntensity: ambient ?? preset.ambient,
        };
        if (exposure !== undefined) renderer.exposure = exposure;
        canvas3d.setProps({ renderer });

        const applied = canvas3d.props?.renderer;
        return {
          rig: chosen,
          // Read back, because a rejected light list leaves the previous one in
          // place and the scene simply looks unchanged.
          lights: applied?.light?.length ?? null,
          ambient: applied?.ambientIntensity ?? null,
          exposure: applied?.exposure ?? null,
        };
      },
    },

    background: {
      render: true,
      async run({
        color,
        transparent,
        gradient,
        gradient_from,
        gradient_to,
        image,
        skybox,
        blur,
      }: BackgroundArgs) {
        const canvas3d = plugin.canvas3d;
        if (!canvas3d) throw new Error('No 3D canvas yet — load a structure first.');

        const props: Record<string, unknown> = {};
        if (color !== undefined) {
          props.renderer = { backgroundColor: parseHexColor(color) };
        }
        if (transparent !== undefined) props.transparentBackground = transparent;

        if (gradient !== undefined) {
          if (gradient === 'off') {
            props.postprocessing = { background: { variant: { name: 'off', params: {} } } };
          } else {
            const spec = GRADIENTS[gradient];
            if (!spec) {
              throw new Error(
                `Unknown gradient '${gradient}'. ` +
                  `Available: off, ${Object.keys(GRADIENTS).sort().join(', ')}`
              );
            }
            props.postprocessing = {
              background: {
                variant: {
                  name: spec.variant,
                  params: {
                    [spec.from]: parseHexColor(gradient_from ?? '#dddddd'),
                    [spec.to]: parseHexColor(gradient_to ?? '#eeeeee'),
                    ratio: 0.5,
                    // 'canvas' would key the gradient to the whole element
                    // rather than the rendered viewport, so a captured figure
                    // would show a different slice of it than the screen did.
                    coverage: 'viewport',
                  },
                },
              },
            };
          }
        } else if (gradient_from !== undefined || gradient_to !== undefined) {
          throw new Error('Pass gradient= as well, to say which gradient the colours are for');
        }

        // Mol* takes both of these as plain URL strings, and a data URI is a
        // URL — so an image never has to go through its File params, and the
        // Python side can send a local file without the bridge growing a
        // route to serve it from.
        if (image !== undefined) {
          props.postprocessing = {
            background: {
              variant: {
                name: 'image',
                params: {
                  source: { name: 'url', params: image },
                  blur: blur ?? 0,
                  opacity: 1,
                  saturation: 0,
                  lightness: 0,
                  // 'viewport' keys the image to the rendered area, so a
                  // capture frames the same part of it that the screen did.
                  coverage: 'viewport',
                },
              },
            },
          };
        }

        if (skybox !== undefined) {
          const faces = ['nx', 'ny', 'nz', 'px', 'py', 'pz'];
          const missing = faces.filter((face) => !skybox[face]);
          if (missing.length) {
            throw new Error(`Skybox is missing ${missing.join(', ')}; all six faces are needed`);
          }
          props.postprocessing = {
            background: {
              variant: {
                name: 'skybox',
                params: {
                  faces: { name: 'urls', params: { ...skybox } },
                  blur: blur ?? 0,
                  rotation: { x: 0, y: 0, z: 0 },
                  opacity: 1,
                  saturation: 0,
                  lightness: 0,
                },
              },
            },
          };
        }

        canvas3d.setProps(props);

        // The screenshot helper carries its *own* transparency flag and passes
        // it to the image pass as `transparentBackground`, overriding whatever
        // the canvas holds. Setting only the canvas gives a transparent viewer
        // and an opaque PNG from every capture — a success reply and the wrong
        // file. See viewport-screenshot.js, which reads `this.values.transparent`.
        const helper = plugin.helpers?.viewportScreenshot;
        if (transparent !== undefined && helper) {
          helper.behaviors.values.next({ ...helper.values, transparent });
        }

        // Read back rather than echo: this is the only evidence that the props
        // were accepted, and Mol* accepts bad input without complaint.
        const variant = canvas3d.props?.postprocessing?.background?.variant?.name ?? null;
        return {
          background: toHex(canvas3d.props?.renderer?.backgroundColor),
          transparent: canvas3d.props?.transparentBackground ?? null,
          screenshot_transparent: helper ? helper.values.transparent : null,
          // Mol*'s own variant name, not ours, so a mapping mistake shows up
          // here rather than being hidden by echoing the argument back.
          gradient: variant,
        };
      },
    },

    hide: {
      render: true,
      run: ({ name }: { name: string }) => setHidden(name, true),
    },

    unhide: {
      render: true,
      run: ({ name }: { name: string }) => setHidden(name, false),
    },

    remove: {
      render: true,
      async run({ name }: { name: string }) {
        const entry = require(name);
        for (const ref of entry.refs) {
          await plugin.state.data.build().delete(ref).commit();
        }
        components.delete(name);
        return { name, removed: entry.refs.length, remaining: known() };
      },
    },

    list_selections: {
      async run() {
        const selections = known().map((name) => {
          const entry = components.get(name)!;
          const found = hierarchyComponents(entry.refs);
          const structure = structureOf(entry);
          const atoms =
            structure?.elementCount ??
            found.reduce((n: number, c: any) => n + (c.cell?.obj?.data?.elementCount ?? 0), 0);
          return {
            name,
            atom_count: atoms,
            components: found.length,
            // `auto` can be partly hidden if its components were toggled apart.
            hidden: found.length > 0 && found.every(isHiddenComponent),
          };
        });
        return { selections };
      },
    },

    load_volume: {
      async run({
        name,
        url,
        format,
        provenance,
      }: {
        name: string;
        url: string;
        format: string;
        provenance?: string;
      }) {
        const provider = plugin.dataFormats.get(format);
        if (!provider) {
          const known = plugin.dataFormats.list
            .map((f: any) => f.name)
            .sort()
            .join(', ');
          throw new Error(`This Mol* build cannot parse '${format}'. Known: ${known}`);
        }
        if (volumes.has(name)) await dropVolume(name);

        // download(), not rawData(): the bytes come over HTTP from the same
        // server that serves this page, because a 256 MB map does not belong
        // in a JSON RPC frame.
        const raw = await plugin.builders.data.download(
          { url, isBinary: format !== 'dx' && format !== 'cube' },
          { state: { isGhost: true } }
        );
        const downloadRef = raw?.ref ?? raw?.cell?.transform?.ref;

        // Everything from here can throw, and the Download node is already in
        // the state tree holding the bytes. Nothing else has a handle on it, so
        // failing without this cleanup strands a map-sized buffer per attempt.
        let ref: string | undefined;
        let stats: ReturnType<typeof volumeStats>;
        let data: any;
        try {
          const parsed = await provider.parse(plugin, raw);
          const cell = parsed.volume ?? parsed.volumes?.[0] ?? parsed;
          data = cell?.obj?.data ?? cell?.cell?.obj?.data;
          if (!data) throw new Error(`'${name}' parsed to nothing`);
          ref = cell.ref ?? cell.cell?.transform?.ref;
          if (!ref) {
            // Without a ref there is no way to delete this later; registering
            // it would mean remove_volume silently frees nothing.
            throw new Error(`'${name}' parsed but Mol* gave it no state ref`);
          }
          // Before the Map, not after: volumeStats throws on a zero-voxel grid,
          // and an entry registered first would poison every later
          // list_volumes, which maps over all of them.
          stats = volumeStats(data);
        } catch (err) {
          if (downloadRef) {
            try {
              await plugin.state.data.build().delete(downloadRef).commit();
            } catch {
              // The cleanup failing must not replace the real error.
            }
          }
          throw err;
        }

        // Held here rather than server-side so it shares the handle's
        // lifetime exactly: every path that forgets a volume — plugin.clear(),
        // load_session, remove_volume — drops its provenance with it, and
        // there is no second registry to keep in step. 'unknown' rather than
        // undefined, because the absence of a declaration is itself the
        // answer and should read the same as declaring it.
        const declared = provenance ?? 'unknown';
        volumes.set(name, { ref, downloadRef, data, provenance: declared, format, stats });
        return { name, format, provenance: declared, ...stats };
      },
    },

    volume_info: {
      async run({ name }: { name: string }) {
        const { provenance, stats } = requireVolume(name);
        return { name, provenance, ...stats };
      },
    },

    list_volumes: {
      async run() {
        return {
          volumes: [...volumes.entries()].map(([name, { provenance, stats }]) => ({
            name,
            provenance,
            ...stats,
          })),
        };
      },
    },

    isosurface: {
      render: true,
      async run({
        name,
        level,
        unit,
        style,
        opacity,
      }: {
        name: string;
        level: number;
        unit: 'sigma' | 'absolute';
        style: 'surface' | 'mesh';
        opacity?: number;
      }) {
        const entry = requireVolume(name);
        const stats = entry.stats;

        // **The conversion, and the reason this is not left to Mol\*.**
        // Mol* accepts `{kind:'relative'}` and converts with
        // `relativeValue * grid.stats.sigma + grid.stats.mean` — and for
        // CCP4/MRC `grid.stats` is the file header, which is routinely stale.
        // Its own default isosurface is 2 relative, so out of the box a map
        // with a bad header contours in the wrong place and looks fine. We
        // convert here against the sigma and mean measured off the voxels, and
        // hand Mol* an absolute value it cannot reinterpret.
        const absolute = unit === 'sigma' ? level * stats.sigma + stats.mean : level;

        // What the header would have produced, for the same request. Reported
        // rather than hidden: a large gap is the signal that the file's own
        // statistics disagree with its contents.
        const st = entry.data.grid?.stats;
        const headerAbsolute =
          unit === 'sigma' && st && typeof st.sigma === 'number'
            ? level * st.sigma + st.mean
            : null;

        if (!Number.isFinite(absolute)) {
          throw new Error(
            `contour level ${level} in ${unit} is not a finite value for '${name}' ` +
              `(sigma ${stats.sigma}, mean ${stats.mean})`
          );
        }

        if (!entry.reprRef) {
          const before = new Set<string>(plugin.state.data.cells.keys());
          const provider = plugin.dataFormats.get(entry.format);
          if (!provider?.visuals) {
            throw new Error(
              `this Mol* build has no volume visuals for '${entry.format}', so ` +
                `'${name}' cannot be contoured`
            );
          }
          const cell = plugin.state.data.cells.get(entry.ref);
          const selector = { ref: entry.ref, cell, obj: cell?.obj };
          // Both shapes, deliberately. The ccp4/dsn6/dx/cube providers read
          // `data.volume`; the dscif one destructures `const { volumes } =
          // data` and immediately reads `volumes.length`, so passing only
          // `volume` gives a bare TypeError on a .bcif map rather than
          // anything a caller could act on.
          await provider.visuals(plugin, { volume: selector, volumes: [selector] });
          const added = [...plugin.state.data.cells.values()].filter(
            (c: any) => !before.has(c.transform.ref) && c.obj?.type?.name === 'Volume 3D'
          );
          if (!added.length) {
            throw new Error(`'${name}' produced no isosurface node to contour`);
          }
          if (added.length > 1) {
            // Cube files get a +1 and a -1 lobe; a dscif difference map gets a
            // green +3 and a red -3. Those levels are not one number, and
            // moving only the first would leave the others at Mol*'s defaults
            // while the reply named a single level as though it described the
            // picture. Undo and say so rather than half-apply it.
            const undo = plugin.state.data.build();
            for (const c of added) undo.delete(c.transform.ref);
            await undo.commit();
            throw new Error(
              `'${name}' draws as ${added.length} surfaces (a signed pair, or a ` +
                `difference map), and one level cannot describe them. Contouring ` +
                `this format is not supported yet.`
            );
          }
          entry.reprRef = added[0].transform.ref;
        }

        await plugin.state.data
          .build()
          .to(entry.reprRef)
          .update((old: any) => ({
            ...old,
            type: {
              ...old.type,
              params: {
                ...old.type.params,
                // A plain object, deliberately: the prebuilt Mol* bundle
                // exposes only `Viewer`, so `Volume.IsoValue.absolute()` is
                // not reachable — but this is exactly the shape it returns.
                isoValue: { kind: 'absolute', absoluteValue: absolute },
                visuals: [style === 'mesh' ? 'wireframe' : 'solid'],
                alpha: opacity ?? old.type.params.alpha ?? 1,
              },
            },
          }))
          .commit();

        return {
          volume: name,
          level,
          unit,
          style,
          absolute,
          provenance: entry.provenance,
          // The statistics the conversion actually used, so the number above
          // can be checked rather than trusted.
          sigma: stats.sigma,
          mean: stats.mean,
          stated_absolute: headerAbsolute,
        };
      },
    },

    remove_volume: {
      async run({ name }: { name: string }) {
        requireVolume(name);
        await dropVolume(name);
        return { removed: name };
      },
    },

    color_by_volume: {
      render: true,
      async run({ name, volume, coloring, domain, palette }: ColorByVolumeArgs) {
        const entry = require(name);
        const target = hierarchyComponents(entry.refs);
        if (!target.length) {
          throw new Error(`Selection '${name}' has no component in the hierarchy to colour`);
        }

        const provider = plugin.dataFormats.get('dx');
        if (!provider) throw new Error('This Mol* build cannot parse OpenDX volumes');
        const raw = await plugin.builders.data.rawData({ data: volume, label: name });
        const parsed = await provider.parse(plugin, raw);
        const cell = parsed.volume ?? parsed.volumes?.[0] ?? parsed;
        const data = cell?.obj?.data ?? cell?.cell?.obj?.data;
        if (!data) throw new Error('The volume parsed to nothing');
        const ref = cell.ref ?? cell.cell?.transform?.ref;

        // external-volume takes a ValueRef: a state ref plus a getter. Passing
        // the ref alone leaves the theme with no volume and it silently paints
        // everything its default grey, so the getter is supplied explicitly.
        const stats = data.grid.stats;
        await plugin.managers.structure.component.updateRepresentationsTheme(target, {
          color: 'external-volume',
          colorParams: {
            volume: { ref, getValue: () => data },
            coloring: {
              name: coloring ?? 'absolute-value',
              params: {
                domain: domain
                  ? { name: 'custom', params: domain }
                  : { name: 'auto', params: { symmetric: true } },
                // A ColorList value carries the colours themselves. Passing a
                // preset *name* with an empty `colors` array leaves the ramp
                // with nothing to interpolate and paints the whole surface
                // black — which looks like a render failure, not a bad param.
                list: {
                  kind: 'interpolate',
                  colors: (PALETTES[palette ?? 'red-white-blue'] ?? PALETTES['red-white-blue']),
                },
              },
            },
          },
        });
        return {
          name,
          components: target.length,
          volume_min: stats.min,
          volume_max: stats.max,
          domain: domain ?? null,
        };
      },
    },

    color: {
      render: true,
      async run({ name, color }: { name: string; color: string }) {
        if (!color.startsWith('#')) checkName('colour theme', color, colorThemeNames());
        const entry = require(name);
        const target = hierarchyComponents(entry.refs);
        if (!target.length) {
          throw new Error(`Selection '${name}' has no component in the hierarchy to colour`);
        }
        await plugin.managers.structure.component.updateRepresentationsTheme(
          target,
          colorParams(color)
        );
        return { name, color, components: target.length };
      },
    },

    capabilities: {
      async run() {
        return {
          representations: representationTypes().sort(),
          color_themes: colorThemeNames().sort(),
          // Named styles belong here for the same reason the two lists above
          // do: a model can only pick from what it can see at the point of use.
          lighting_rigs: Object.keys(LIGHTING_RIGS).sort(),
          shading_styles: Object.keys(SHADING_STYLES).sort(),
          gradients: ['off', ...Object.keys(GRADIENTS).sort()],
          material_finishes: Object.keys(MATERIAL_FINISHES).sort(),
          path_trace_quality: Object.keys(TRACE_QUALITY).sort(),
        };
      },
    },

    focus: {
      render: true,
      async run({ name }: { name: string }) {
        const structure = structureOf(require(name));
        if (!structure?.elementCount) {
          throw new Error(`Selection '${name}' has no atoms to focus on`);
        }
        // focusLoci, not focusSphere: a point-like selection's boundary sphere
        // has ~zero radius and the camera barely moves.
        plugin.managers.camera.focusLoci(lociOf(rootStructure(), structure));
        await settleCamera(plugin, CAMERA_TIMEOUT_MS);
        const camera = plugin.canvas3d?.camera?.state;
        return {
          name,
          target: camera ? Array.from(camera.target as ArrayLike<number>) : null,
          radius: camera?.radius ?? null,
        };
      },
    },

    reset_view: {
      render: true,
      async run() {
        plugin.managers.camera.reset();
        await settleCamera(plugin, CAMERA_TIMEOUT_MS);
        return { reset: true };
      },
    },

    orient: {
      render: true,
      async run() {
        // Aligns the camera to the structure's principal axes.
        plugin.managers.camera.orientAxes();
        await settleCamera(plugin, CAMERA_TIMEOUT_MS);
        return { oriented: true };
      },
    },

    measure: {
      render: true,
      async run({ kind, names }: { kind: string; names: string[] }) {
        const arity: Record<string, number> = { distance: 2, angle: 3, dihedral: 4 };
        const wanted = arity[kind];
        if (!wanted) {
          throw new Error(`Unknown measurement '${kind}' (distance, angle, dihedral)`);
        }
        if (names.length !== wanted) {
          throw new Error(`A ${kind} needs ${wanted} selections, got ${names.length}`);
        }
        const loci = names.map((n) => {
          const structure = structureOf(require(n));
          if (!structure?.elementCount) {
            throw new Error(`Selection '${n}' has no atoms to measure`);
          }
          return lociOf(rootStructure(), structure);
        });
        const measurement = plugin.managers.structure.measurement;
        if (kind === 'distance') await measurement.addDistance(loci[0], loci[1]);
        else if (kind === 'angle') await measurement.addAngle(loci[0], loci[1], loci[2]);
        else await measurement.addDihedral(loci[0], loci[1], loci[2], loci[3]);
        return { kind, names, atoms: names.map((n) => structureOf(require(n))!.elementCount) };
      },
    },

    label: {
      render: true,
      async run({ name, level }: { name: string; level?: string }) {
        const entry = require(name);
        const chosen = level ?? 'residue';
        const levels = ['chain', 'residue', 'element'];
        if (!levels.includes(chosen)) {
          throw new Error(`Unknown label level '${chosen}'. Available: ${levels.join(', ')}`);
        }
        const structure = structureOf(entry);
        if (!structure?.elementCount) {
          throw new Error(`Selection '${name}' has no atoms to label`);
        }
        // addRepresentation takes a StateObjectRef, and a plain ref string is
        // one — which matters because a restored handle has no selector object.
        await plugin.builders.structure.representation.addRepresentation(entry.refs[0], {
          type: 'label',
          typeParams: { level: chosen },
        });
        return { name, level: chosen, labelled: structure.elementCount };
      },
    },

    save_session: {
      async run() {
        // The snapshot embeds the structure data, so a session reproduces the
        // scene without refetching anything.
        const snapshot = plugin.state.getSnapshot();
        const handles: Record<string, string[]> = {};
        for (const [name, entry] of components) handles[name] = entry.refs;
        return { snapshot, handles };
      },
    },

    load_session: {
      render: true,
      async run({ snapshot, handles }: { snapshot: any; handles: Record<string, string[]> }) {
        await plugin.state.setSnapshot(snapshot);
        // A snapshot carries canvas3d props, and `manualReset` defaults to
        // false — so restoring any session written before this existed would
        // hand automatic fitting back for the rest of the viewer's life, and
        // backlog 26's race with it. Every session file on disk today is such
        // a file.
        takeTheCameraOffAutomaticFitting(plugin);
        components.clear();
        // Volume handles are not saved, so none can survive a restore. Keeping
        // them would leave `volume_info` answering from a `data` object this
        // Map holds alive, describing a volume the restored state never had.
        forgetVolumes();
        // Keep only refs the restored state actually contains, so a stale or
        // hand-edited session degrades to fewer handles rather than to handles
        // that point at nothing.
        const live = new Set<string>(
          allComponents().map((c: any) => c.cell.transform.ref)
        );
        const dropped: string[] = [];
        for (const [name, refs] of Object.entries(handles ?? {})) {
          const kept = refs.filter((ref) => live.has(ref));
          if (kept.length) components.set(name, { refs: kept });
          else dropped.push(name);
        }
        const structure = plugin.managers.structure.hierarchy.current.structures[0];
        return {
          restored: known(),
          dropped,
          atom_count: structure?.cell?.obj?.data?.elementCount ?? 0,
        };
      },
    },

    clear: {
      render: true,
      async run() {
        components.clear();
        forgetVolumes();
        await plugin.clear();
        return {};
      },
    },

    screenshot: {
      render: true,
      async run() {
        const helper = plugin.helpers?.viewportScreenshot;
        if (helper?.getImageDataUri) {
          // Build the image pass before capturing, rather than letting
          // getImageDataUri create it on the way past. Measured: a capture
          // taken through a freshly created pass differs from every identical
          // capture after it — 2.1% of the frame on 1UBQ, and slightly less
          // antialiased. For a tool whose product is a figure, that made the
          // first export quietly the worst one. Reading the getter is what
          // constructs and caches it.
          const pass = helper.imagePass;
          if (!pass) throw new Error('Mol* built no image pass for the screenshot');
          const traced = !!plugin.canvas3d?.props?.illumination?.enabled;
          const started = performance.now();
          const data_uri = await helper.getImageDataUri();
          // Only reported when tracing, where the cost is the thing a caller
          // most needs to know before asking for a bigger one.
          return traced
            ? { data_uri, traced_ms: Math.round(performance.now() - started) }
            : { data_uri };
        }
        // Fallback: read the 3D canvas directly.
        const canvas: HTMLCanvasElement | undefined =
          plugin.canvas3dContext?.canvas ?? document.querySelector('#app canvas') ?? undefined;
        if (!canvas) throw new Error('No screenshot mechanism available');
        return { data_uri: canvas.toDataURL('image/png') };
      },
    },
  };

  return async (action, args) => {
    const spec = actions[action];
    if (!spec) throw new Error(`Unknown action: ${action}`);
    if (!spec.render) return spec.run(args);
    // Read before the action: whether the scene was empty is what decides
    // framing, and afterwards it never is.
    const wasEmpty = !plugin.canvas3d?.reprCount?.value;
    const result = await withRenderPump(plugin, action, () => spec.run(args));
    // Bounds after every draw, framing only when the scene had nothing in it.
    const fitted = keepCameraBounded(plugin, wasEmpty);
    // After the pump, never inside the action. Mol* resolves a requested
    // camera reset from `commit()`, and only when `commitScene` reports
    // everything committed — "Only reset the camera after the full scene has
    // been commited", canvas3d.js. So the camera has not begun to move while
    // geometry is still queued, and a wait placed before the render pump
    // watches a camera that is still, decides it has arrived, and returns just
    // in time for the tween to start behind it.
    if (!spec.camera && !fitted) return result;
    const settled = await settleCamera(plugin, CAMERA_TIMEOUT_MS);
    // Reported rather than thrown. A camera that ran out of budget has still
    // very likely arrived somewhere sensible, and refusing the whole load over
    // it would turn a framing wobble into a failure to display a molecule. The
    // caller gets to decide, and a capture taken straight afterwards can say
    // why it looks different rather than presenting itself as a measurement.
    if (settled || typeof result !== 'object' || result === null) return result;
    return { ...(result as Record<string, unknown>), camera_settled: false };
  };
}

/** Take the camera off Mol*'s automatic fitting, for the life of the viewer.
 *
 * `commitScene` requests a camera reset whenever `shouldResetCamera()` decides
 * the visible bounding sphere has moved out from under the camera, and a commit
 * has a 250 ms budget it can run out of. Which commit boundary a `hide` and a
 * `show` land on then decided whether that test ran against the old scene or
 * the new one, so `show()` moved the camera about one time in seven and held
 * still the rest — backlog 26. A caller could rely neither on it moving nor on
 * it staying, which is worse than either.
 *
 * `manualReset` gates only the *automatic* request; `managers.camera.reset()`
 * sets the flag directly and still works, which is what `focus()`,
 * `reset_view()` and the explicit fit after a load rely on.
 *
 * **Set once here rather than per load.** An earlier version set it inside
 * `load_structure`, claiming a prop set before `plugin.clear()` would not
 * survive. That claim was wrong — `clear()` takes `resetViewportSettings` and
 * protean passes nothing, so canvas3d props are untouched — and the placement
 * left every path that draws without loading a structure still auto-fitting:
 * a volume into an empty viewer, or anything after `clear_viewer`.
 */
function takeTheCameraOffAutomaticFitting(plugin: any): void {
  plugin.canvas3d?.setProps({ camera: { manualReset: true } });
}

/** Keep the camera's limits current without touching where it points.
 *
 * The same flag that stops Mol* re-framing also stops it maintaining
 * `radiusMax` — `commitScene` ends with `if (!p.camera.manualReset)
 * camera.setState({ radiusMax: getSceneRadius() })`, and the trackball's
 * min/max distances are recomputed only inside `resolveCameraReset`. Left
 * alone, a scene that grows after the load — a volume spanning far more than
 * the protein — keeps the molecule's radius, and the clip slab drawn from it
 * cuts the new geometry away while zoom stays clamped to the old bounds.
 *
 * So the limits are maintained here, deliberately without moving the camera:
 * framing belongs to the caller, bounds belong to the scene.
 *
 * The one exception is a scene nothing has ever framed, which is what Mol*'s
 * own `reprCount === 0` branch handled: there is no caller framing to protect,
 * and leaving it unfitted shows a blank canvas and reports success.
 */
function keepCameraBounded(plugin: any, wasEmpty: boolean): boolean {
  const canvas = plugin.canvas3d;
  const camera = canvas?.camera;
  if (!camera) return false;

  // Touched to force the recompute, not for its value. `canvas3d.boundingSphere`
  // is the object captured from `scene.boundingSphere` when the canvas was
  // built, and the scene only recalculates it when something *reads the
  // getter*. Across the whole bundle three things do, and the per-commit one is
  // `getSceneRadius()` inside `if (!p.camera.manualReset)` — the line this file
  // turns off. `getProps()` reads it too, so asking for the props brings the
  // sphere up to date; without this the radius below is the one from the last
  // camera reset, which is the scene *before* whatever just drew.
  void canvas.props;
  const radius = canvas.boundingSphere?.radius ?? 0;
  if (radius <= 0) return false;

  // Mol*'s own condition, which is about the *scene* and not the camera:
  // `commitScene` fitted whenever `reprCount.value === 0` before the commit.
  //
  // Two earlier versions asked the camera instead and both were wrong.
  // `radiusMax` is 10 in `createDefaultSnapshot`, so that branch could never
  // run at all. `radius` looked right — it defaults to 0 — but a viewer that
  // has been *cleared* keeps the fit from whatever it held before, so the
  // first geometry drawn after a `clear` still found a non-zero radius and
  // went unframed. Measured: target stayed at the old structure's centre while
  // the map was drawn somewhere else entirely.
  if (wasEmpty) {
    plugin.managers.camera.reset();
    return true;
  }
  camera.setState({ radiusMax: radius }, 0);
  return false;
}

/** Take a representation out of picking and highlighting both.
 *
 * A see-through surface drawn over a chain is scenery: it exists to be looked
 * *through*. Left pickable it intercepts every click meant for what is inside
 * it, and a selection lands on a jagged patch of mesh rather than on the
 * residue someone aimed at.
 *
 * `markerActions` matters as much as `pickable` and for a different reason.
 * Picking decides what a click *hits*; marker actions decide what lights up
 * when something else is highlighted. Without both, clicking the cartoon
 * underneath — or picking a residue from the sequence strip — still flares the
 * surface over it, which is the same mess arriving by another route.
 *
 * `0` is `MarkerAction.None`. The prebuilt Mol* bundle does not export the
 * enum, so the number is written out rather than imported; Mol* sets exactly
 * this pair itself, in `mol-plugin-state/transforms/representation.js`, for a
 * representation that should be seen and not touched.
 */
function markAsScenery(built: any): void {
  built?.obj?.data?.repr?.setState?.({ pickable: false, markerActions: 0 });
}

/** Build a StructureElement.Loci covering every atom of *structure*.
 *
 * The prebuilt global exposes no helper for this, but the shape is safe to
 * construct by hand: an OrderedSet of element indices is either an Interval or
 * a SortedArray, and a SortedArray is a plain typed array — so a 0..n-1
 * Int32Array is a valid one. `indices` are unit-local, not element ids.
 *
 * Verified by handing the result to `camera.focusLoci`, which parked the camera
 * on exactly the intended atom's coordinates.
 */
export function lociOf(root: any, component: any) {
  // Anchored to the *root* structure, not the component's own child structure.
  // Mol* serialises a loci to a bundle keyed by unit id and re-resolves it
  // against the parent, so a child-anchored loci silently points at whatever
  // atom happens to sit at the same unit-local index in the root's unit — a
  // distance that should read 4.56 A rendered as 24.93 A. Immediate consumers
  // like camera.focusLoci never round-trip and so hid the bug.
  const wanted = new Set<number>();
  for (const unit of component.units) {
    for (let i = 0; i < unit.elements.length; i++) wanted.add(unit.elements[i]);
  }
  const elements: Array<{ unit: any; indices: Int32Array }> = [];
  for (const unit of root.units) {
    const indices: number[] = [];
    for (let i = 0; i < unit.elements.length; i++) {
      if (wanted.has(unit.elements[i])) indices.push(i);
    }
    if (indices.length) elements.push({ unit, indices: Int32Array.from(indices) });
  }
  return { kind: 'element-loci', structure: root, elements };
}

/** Rotate *v* about *axis* by *radians*, by Rodrigues' formula.
 *
 * Written out because the prebuilt Mol* global exposes about a dozen names and
 * its Vec3 helpers are not among them.
 */
export function rotateAbout(v: number[], axis: number[], radians: number): number[] {
  const length = Math.hypot(axis[0], axis[1], axis[2]) || 1;
  const [kx, ky, kz] = [axis[0] / length, axis[1] / length, axis[2] / length];
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const dot = kx * v[0] + ky * v[1] + kz * v[2];
  const cross = [
    ky * v[2] - kz * v[1],
    kz * v[0] - kx * v[2],
    kx * v[1] - ky * v[0],
  ];
  return [
    v[0] * cos + cross[0] * sin + kx * dot * (1 - cos),
    v[1] * cos + cross[1] * sin + ky * dot * (1 - cos),
    v[2] * cos + cross[2] * sin + kz * dot * (1 - cos),
  ];
}

/** A leading '#' means a literal colour; anything else is a Mol* colour theme. */
export function colorParams(color: string): Record<string, unknown> {
  if (color.startsWith('#')) {
    return { color: 'uniform', colorParams: { value: parseInt(color.slice(1), 16) } };
  }
  return { color };
}

/** Parse "#rrggbb" into the packed integer Mol* calls a Color.
 *
 * Strict on purpose. `parseInt('#oops'.slice(1), 16)` is NaN, and a NaN
 * background paints black without complaint, which reads as a render failure
 * rather than as a bad argument.
 */
export function parseHexColor(color: string): number {
  if (!/^#[0-9a-fA-F]{6}$/.test(color)) {
    throw new Error(`Expected a colour like "#ff8800", got '${color}'`);
  }
  return parseInt(color.slice(1), 16);
}

/** The inverse, for reading a colour back out of the canvas. */
export function toHex(value: unknown): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return `#${(value & 0xffffff).toString(16).padStart(6, '0')}`;
}

/** What the canvas is actually doing, read back rather than echoed.
 *
 * Reported as on/off per effect plus the outline's own settings, because the
 * whole question a caller has after switching an effect on is whether it took.
 */
export function effectState(canvas3d: any): Record<string, unknown> {
  const post = canvas3d.props?.postprocessing ?? {};
  const on = (key: string) => post[key]?.name === 'on';
  return {
    outline: on('outline'),
    outline_color: on('outline') ? toHex(post.outline?.params?.color) : null,
    outline_scale: on('outline') ? (post.outline?.params?.scale ?? null) : null,
    occlusion: on('occlusion'),
    shadow: on('shadow'),
    depth_of_field: on('dof'),
    bloom: on('bloom'),
    sharpening: on('sharpening'),
  };
}

/** Opacity is a fraction. Mol* clamps silently, so 50 would become 1 — solid,
 * the exact opposite of what someone typing "50" meant. */
export function checkOpacity(opacity: number): void {
  checkFraction('Opacity', opacity);
}

/** Every PBR value here runs 0 to 1, and Mol* clamps all of them silently. */
export function checkFraction(what: string, value: number): void {
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error(`${what} must be between 0 and 1, got ${value}`);
  }
}
