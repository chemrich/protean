/**
 * The soft-matter bake-off: three treatments, one per route, on one structure.
 *
 * Built on a viewer bundled from `molstar/lib` rather than the prebuilt UMD
 * bundle protean ships, because the mesh route needs it — see
 * docs/molstar-bundling.md. Nothing here is wired into protean; the point is
 * to look at three pictures before anyone commits to 28 weeks.
 */
import { Viewer } from 'molstar/lib/apps/viewer/app';
import { ParamDefinition as PD } from 'molstar/lib/mol-util/param-definition';
import { Color } from 'molstar/lib/mol-util/color';
import { RadiolariaRepresentationProvider } from './radiolaria';

const PDB = new URLSearchParams(location.search).get('pdb') ?? '1mbn';

/** Deterministic per-atom jitter. RNG would differ between symmetry mates of one atom. */
function hash(i: number, seed: number): number {
  const h = Math.imul(i + 1, 2654435761) ^ seed;
  return ((h >>> 0) % 100000) / 100000;
}

const result: Record<string, unknown> = { ready: false };
(window as unknown as Record<string, unknown>).__bake = result;

async function main() {
  const viewer = await Viewer.create('app', {
    layoutIsExpanded: false,
    layoutShowControls: false,
    layoutShowSequence: false,
    layoutShowLog: false,
    viewportShowExpand: false,
    viewportShowAnimation: false,
  });
  const plugin = viewer.plugin;

  const themes = plugin.representation.structure.themes;
  themes.colorThemeRegistry.add(woolThemeProvider() as any);
  themes.sizeThemeRegistry.add(jitterSizeProvider() as any);
  plugin.representation.structure.registry.add(RadiolariaRepresentationProvider as any);
  // Registered is not the same as reachable, and an unknown representation type
  // is a silent no-op at `addRepresentation` — it resolves and adds nothing.
  result.registryTypes = plugin.representation.structure.registry.types.map((t: any) => t[0]);

  await viewer.loadPdb(PDB);
  const cell = plugin.managers.structure.hierarchy.current.structures[0];
  result.atomCount = cell?.cell.obj?.data.elementCount ?? 0;

  /**
   * Re-read every time, never cached.
   *
   * The first version of this held one `components` array from boot and both
   * clearing and drawing quietly did nothing: Mol* rebuilds the hierarchy when
   * representations change, so the cached refs pointed at cells that no longer
   * existed — and `addRepresentation` on a stale ref resolves successfully.
   * "Radiolaria" drew in 30 ms and photographed the previous treatment.
   */
  const components = () => plugin.managers.structure.hierarchy.current.structures[0].components;

  async function clear() {
    for (const c of components()) {
      const reps = [...c.representations];
      for (const r of reps) {
        await plugin.managers.structure.component.removeRepresentations([c], r);
      }
    }
    const left = components().reduce((n, c) => n + c.representations.length, 0);
    if (left !== 0) throw new Error(`clear left ${left} representations behind`);
  }

  async function add(type: string, typeParams: any, color: any, size?: any) {
    for (const c of components()) {
      const selector = await plugin.builders.structure.representation.addRepresentation(c.cell, {
        type: type as any,
        typeParams,
        color: color?.name,
        colorParams: color?.params,
        size: size?.name,
        sizeParams: size?.params,
      });
      if (!selector?.isOk) {
        // The selector being not-ok is all `addRepresentation` tells you; the
        // reason is on the state cell it built.
        const cell: any = selector?.cell;
        const why = cell?.errorText ?? cell?.status ?? 'no cell';
        const logged = plugin.log.entries.toArray().slice(-3).map((e: any) => e.message).join(' | ');
        throw new Error(`addRepresentation('${type}') produced nothing: ${why} :: ${logged}`);
      }
    }
  }

  const treatments: Record<string, () => Promise<void>> = {
    /** The control the whole plan argues against. */
    async control() {
      await add('spacefill', { sizeFactor: 1 }, { name: 'element-symbol' });
    },

    /** SM-01 — speculars killed, ambient raised, fibrous silhouette, halo layer. */
    async felt() {
      await add(
        'spacefill',
        {
          sizeFactor: 1,
          material: { metalness: 0, roughness: 1, bumpiness: 0.9 },
          bumpFrequency: 6,
          bumpAmplitude: 1.4,
        },
        { name: 'dyed-wool' },
        { name: 'felt-jitter' }
      );
      // The fiber halo without a shader: a second layer at 1.12x, barely opaque.
      await add(
        'spacefill',
        {
          sizeFactor: 1.12,
          alpha: 0.2,
          material: { metalness: 0, roughness: 1, bumpiness: 1 },
          bumpFrequency: 9,
          bumpAmplitude: 2,
        },
        { name: 'dyed-wool' },
        { name: 'felt-jitter' }
      );
    },

    /**
     * The source frame for SS-03 duotone, which happens in Python after the
     * capture and therefore sees only pixels. Its channel — B-factor to dot
     * size — can only survive if the render already carries B-factor as tone,
     * so this draws it in greyscale for the finish to read back.
     */
    async bfactor() {
      await add('spacefill', { sizeFactor: 1 }, { name: 'uncertainty', params: { list: { kind: 'set', colors: [0x000000, 0xffffff] } } });
    },

    /** SP-01 — the geodesic lattice. */
    async radiolaria() {
      // strutRadius from the plan's own manifest example. At 0.3 the struts
      // merge and the lattice reads as a spiky solid — the opposite of the
      // see-through-it property the treatment exists for.
      await add(
        'radiolaria',
        {
          // `quality: 'custom'` or Mol* recomputes `detail` from the structure
          // size and silently ignores what the treatment asked for. Passing
          // detail 0 without this rendered at detail 3: 1920 struts per atom
          // instead of 30, and 23,040 vertices per atom instead of ~600.
          quality: 'custom',
          detail: 0,
          strutRadius: 0.08,
          segments: 3,
          porosityLow: 0.1,
          porosityHigh: 0.75,
        },
        { name: 'element-symbol' }
      );
    },
  };

  (result as any).draw = async (name: string) => {
    const started = performance.now();
    await clear();
    await treatments[name]();
    // Refit, or the frame is whatever the previous treatment's camera was
    // pointing at — the first radiolaria capture was a close-up of two atoms.
    // `managers.camera.reset()` restores the *saved* camera; it does not refit
    // to geometry that changed underneath it. canvas3d recomputes the bounds.
    plugin.canvas3d?.requestCameraReset();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    // What is actually on screen, not what was asked for. A count and a vertex
    // total are the two things that distinguish "drew" from "reported success".
    let vertices = 0;
    const kinds: string[] = [];
    for (const c of components()) {
      for (const r of c.representations) {
        kinds.push(r.cell.transform.params?.type?.name ?? '?');
        const repr = r.cell.obj?.data?.repr;
        for (const v of repr?.renderObjects ?? []) {
          vertices += v.values?.uVertexCount?.ref?.value ?? 0;
        }
      }
    }
    return { name, ms: Math.round(performance.now() - started), kinds, vertices };
  };
  result.treatments = Object.keys(treatments);
  result.ready = true;

  /** The plan's dyed-wool palette: madder, indigo, weld yellow, walnut, undyed cream. */
  function woolThemeProvider() {
    const wool: Record<string, number> = {
      C: 0xd9cbb3, N: 0x3f5d7d, O: 0xa33b32, S: 0xc9a227, H: 0xe8e0d2,
    };
    return {
      name: 'dyed-wool',
      label: 'Dyed wool',
      category: 'Misc',
      factory: () => ({
        factory: () => {},
        granularity: 'group',
        color: (location: any) => {
          const s = location.unit?.model?.atomicHierarchy?.atoms?.type_symbol;
          const symbol = s ? String(s.value(location.element)).toUpperCase() : 'C';
          return Color(wool[symbol] ?? 0x8f8578);
        },
        props: {},
        description: 'dyed wool',
      }),
      getParams: () => ({}),
      defaultValues: {},
      isApplicable: () => true,
    };
  }

  /**
   * Hash-based, not RNG-based, and this is the reason the plan gives: a
   * symmetry mate that jitters differently from its parent looks broken.
   * B-factor scales the amplitude, so the binding is visible as roughness.
   */
  function jitterSizeProvider() {
    return {
      name: 'felt-jitter',
      label: 'Felt jitter',
      category: 'Misc',
      factory: () => ({
        factory: () => {},
        granularity: 'group',
        size: (location: any) => {
          const model = location.unit.model;
          const vdw = model.atomicConformation.B_iso_or_equiv ? 1.7 : 1.7;
          const b = model.atomicConformation.B_iso_or_equiv.value(location.element);
          const amp = 0.04 + Math.min(1, b / 60) * 0.14;
          return vdw * (1 - amp + 2 * amp * hash(location.element, 42));
        },
        props: {},
        description: 'felt jitter',
      }),
      getParams: () => ({}),
      defaultValues: {},
      isApplicable: () => true,
    };
  }
}

main().catch((e: unknown) => {
  result.error = String(e);
  result.ready = true;
});
