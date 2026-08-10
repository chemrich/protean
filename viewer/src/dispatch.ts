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

/** Must stay below the bridge's own request timeout so our error wins the race. */
const HIDDEN_TIMEOUT_MS = 30_000;
/** Settling budget for a visible tab, where rAF runs and the work is real but
 * not instant. Shorter than the hidden budget: nothing is paused here, so a
 * wait this long means the commit loop is stuck rather than merely slow. */
const VISIBLE_TIMEOUT_MS = 10_000;
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
async function settleCamera(plugin: any, budgetMs: number): Promise<void> {
  const camera = plugin.canvas3d?.camera;
  if (!camera) return;
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

  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      const pump = setTurbo ? 'the hidden-tab render pump is active' : 'no render pump is installed';
      reject(
        new Error(
          `'${action}' did not finish within ${HIDDEN_TIMEOUT_MS / 1000}s while the ` +
            `viewer tab was hidden (visibilityState=${document.visibilityState}, ${pump}). ` +
            `Browsers pause requestAnimationFrame in background tabs, which Mol* needs ` +
            `to build representations. Bring the protean tab to the front and retry.`
        )
      );
    }, HIDDEN_TIMEOUT_MS);
  });

  const settled = (async () => {
    const result = await run();
    await settleRender(plugin, HIDDEN_TIMEOUT_MS);
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
  /** Named components, so later show/color calls can target an earlier select. */
  const components = new Map<string, Entry>();

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

  const actions: Record<string, { render?: boolean; run: (args: any) => Promise<unknown> }> = {
    load_structure: {
      render: true,
      async run({ name, format, data, assembly }: LoadStructureArgs) {
        components.clear();
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
        await plugin.builders.structure.representation.addRepresentation(selector, params);
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
      async run({ color, transparent }: BackgroundArgs) {
        const canvas3d = plugin.canvas3d;
        if (!canvas3d) throw new Error('No 3D canvas yet — load a structure first.');

        const props: Record<string, unknown> = {};
        if (color !== undefined) {
          props.renderer = { backgroundColor: parseHexColor(color) };
        }
        if (transparent !== undefined) props.transparentBackground = transparent;
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
        return {
          background: toHex(canvas3d.props?.renderer?.backgroundColor),
          transparent: canvas3d.props?.transparentBackground ?? null,
          screenshot_transparent: helper ? helper.values.transparent : null,
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
        components.clear();
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
          return { data_uri: await helper.getImageDataUri() };
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
    return withRenderPump(plugin, action, () => spec.run(args));
  };
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

/** Opacity is a fraction. Mol* clamps silently, so 50 would become 1 — solid,
 * the exact opposite of what someone typing "50" meant. */
export function checkOpacity(opacity: number): void {
  if (!Number.isFinite(opacity) || opacity < 0 || opacity > 1) {
    throw new Error(`Opacity must be between 0 and 1, got ${opacity}`);
  }
}
