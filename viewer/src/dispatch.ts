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

/** Must stay below the bridge's own request timeout so our error wins the race. */
const HIDDEN_TIMEOUT_MS = 30_000;
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

  const start = performance.now();
  let previous = sample();
  let quiet = 0;
  while (quiet < 3 && performance.now() - start < budgetMs) {
    await new Promise((resolve) => requestAnimationFrame(resolve));
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
  if (!isHidden()) return run();

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
  /** Present only for components we created from a selection. */
  selector?: any;
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
    if (selector) components.set(name, { refs: [selector.ref], selector });
    return selector;
  }

  function dataOf(selector: any) {
    return selector?.data ?? selector?.cell?.obj?.data ?? undefined;
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
      async run({ name, format, data }: LoadStructureArgs) {
        components.clear();
        const raw = await plugin.builders.data.rawData({ data, label: name });
        const trajectory = await plugin.builders.structure.parseTrajectory(
          raw,
          format === 'pdb' ? 'pdb' : 'mmcif'
        );
        await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default');
        // Register the preset's own representations under a reserved handle so
        // they can be hidden or removed like any other selection.
        const auto = allComponents().map((c: any) => c.cell.transform.ref);
        if (auto.length) components.set(AUTO, { refs: auto });
        return { loaded: name, auto_components: auto.length };
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
      async run({ name, expression, representation, color, limit }: ShowArgs) {
        const selector = await component(name, expression);
        const structure = dataOf(selector);
        if (!structure || structure.elementCount === 0) {
          return { name, representation, atom_count: 0, residue_count: 0, chains: [], residues: [], truncated: false };
        }
        const params: Record<string, unknown> = { type: representation };
        if (color) Object.assign(params, colorParams(color));
        await plugin.builders.structure.representation.addRepresentation(selector, params);
        return {
          name,
          representation,
          ...summarise(structure, limit ?? DEFAULT_RESIDUE_LIMIT),
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
          const structure = dataOf(entry.selector);
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

    color: {
      render: true,
      async run({ name, color }: { name: string; color: string }) {
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

    focus: {
      render: true,
      async run({ name }: { name: string }) {
        const structure = dataOf(require(name).selector);
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
          const structure = dataOf(require(n).selector);
          if (!structure?.elementCount) {
            throw new Error(`Selection '${n}' has no atoms to measure`);
          }
          return lociOf(rootStructure(), structure);
        });
        const measurement = plugin.managers.structure.measurement;
        if (kind === 'distance') await measurement.addDistance(loci[0], loci[1]);
        else if (kind === 'angle') await measurement.addAngle(loci[0], loci[1], loci[2]);
        else await measurement.addDihedral(loci[0], loci[1], loci[2], loci[3]);
        return { kind, names, atoms: names.map((n) => dataOf(require(n).selector).elementCount) };
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
