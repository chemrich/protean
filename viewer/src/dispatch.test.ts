import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  colorParams,
  createDispatcher,
  lociOf,
  atomAt,
  rampColor,
  rotateAbout,
  summarise,
} from './dispatch';

/** A structure shaped like Mol*'s, with the table layout the real one uses:
 *  comp_id on the atom table, seq/ins_code on the residue table. */
function fakeStructure(
  atoms: Array<{ residue: number; chain: number; comp: string }>,
  residues: Array<{ seq: number; ins?: string }>,
  chains: string[]
) {
  const column = <T,>(values: T[]) => ({ value: (i: number) => values[i] });
  return {
    elementCount: atoms.length,
    units: [
      {
        model: {
          id: 'model-1',
          atomicHierarchy: {
            residueAtomSegments: { index: atoms.map((a) => a.residue) },
            chainAtomSegments: { index: atoms.map((a) => a.chain) },
            chains: { auth_asym_id: column(chains) },
            residues: {
              auth_seq_id: column(residues.map((r) => r.seq)),
              pdbx_PDB_ins_code: column(residues.map((r) => r.ins ?? '')),
            },
            atoms: { label_comp_id: column(atoms.map((a) => a.comp)) },
          },
        },
        elements: atoms.map((_, i) => i),
      },
    ],
  };
}

describe('summarise', () => {
  const structure = fakeStructure(
    [
      { residue: 0, chain: 0, comp: 'ALA' },
      { residue: 0, chain: 0, comp: 'ALA' },
      { residue: 1, chain: 0, comp: 'GLY' },
      { residue: 2, chain: 1, comp: 'HEM' },
    ],
    [{ seq: 10 }, { seq: 11, ins: 'A' }, { seq: 200 }],
    ['A', 'B']
  );

  it('counts atoms and deduplicates residues', () => {
    const out = summarise(structure, 10);
    expect(out.atom_count).toBe(4);
    expect(out.residue_count).toBe(3);
  });

  it('reports the chains touched, sorted', () => {
    expect(summarise(structure, 10).chains).toEqual(['A', 'B']);
  });

  it('reads comp_id from the atom table', () => {
    expect(summarise(structure, 10).residues.map((r) => r.comp)).toEqual([
      'ALA',
      'GLY',
      'HEM',
    ]);
  });

  it('includes an insertion code only when present', () => {
    const residues = summarise(structure, 10).residues;
    expect(residues[0]).not.toHaveProperty('ins_code');
    expect(residues[1].ins_code).toBe('A');
  });

  it('caps the residue list but keeps the count exact', () => {
    const out = summarise(structure, 2);
    expect(out.residues).toHaveLength(2);
    expect(out.residue_count).toBe(3);
    expect(out.truncated).toBe(true);
  });

  it('is not truncated when everything fits', () => {
    expect(summarise(structure, 10).truncated).toBe(false);
  });
});

describe('lociOf', () => {
  // Element ids are model-wide atom indices; a component's units carry a
  // subset of them, and the root splits the same ids across several units.
  const root = {
    units: [
      { id: 0, elements: Int32Array.from([10, 11, 12]) },
      { id: 1, elements: Int32Array.from([20, 21, 22, 23]) },
    ],
  };

  it('anchors to the root structure, not the component', () => {
    const component = { units: [{ id: 9, elements: Int32Array.from([21]) }] };
    const loci = lociOf(root, component);
    expect(loci.kind).toBe('element-loci');
    expect(loci.structure).toBe(root);
  });

  it('maps element ids to unit-local indices in the root unit that holds them', () => {
    const component = { units: [{ id: 9, elements: Int32Array.from([21]) }] };
    const loci = lociOf(root, component);
    // 21 is the second element of the root's second unit.
    expect(loci.elements).toHaveLength(1);
    expect(loci.elements[0].unit).toBe(root.units[1]);
    expect(Array.from(loci.elements[0].indices)).toEqual([1]);
  });

  it('spans several root units and skips those contributing nothing', () => {
    const component = { units: [{ id: 9, elements: Int32Array.from([12, 20, 23]) }] };
    const loci = lociOf(root, component);
    expect(loci.elements).toHaveLength(2);
    expect(Array.from(loci.elements[0].indices)).toEqual([2]);
    expect(Array.from(loci.elements[1].indices)).toEqual([0, 3]);
  });

  it('yields no elements for an empty component', () => {
    expect(lociOf(root, { units: [] }).elements).toHaveLength(0);
  });
});

describe('colorParams', () => {
  it('treats a leading # as a literal colour', () => {
    expect(colorParams('#ff0000')).toEqual({
      color: 'uniform',
      colorParams: { value: 0xff0000 },
    });
  });

  it('passes anything else through as a theme name', () => {
    expect(colorParams('chain-id')).toEqual({ color: 'chain-id' });
  });
});

/** Minimal stand-in for the Mol* plugin surface the dispatcher touches. */
function fakePlugin() {
  const componentRefs: any[] = [];
  const structure = fakeStructure(
    [{ residue: 0, chain: 0, comp: 'ALA' }],
    [{ seq: 1 }],
    ['A']
  );
  const hierarchyStructure = {
    cell: { transform: { ref: 'structure-ref' }, obj: { data: structure } },
    components: componentRefs,
  };
  const toggleVisibility = vi.fn(async (comps: any[]) => {
    for (const c of comps) c.cell.state.isHidden = !c.cell.state.isHidden;
  });
  return {
    componentRefs,
    toggleVisibility,
    representation: {
      structure: {
        registry: {
          // `line` is here because protean really does offer representations
          // with no surface to shade — line, point and label declare no
          // bumpFrequency — and a fake where every representation can take a
          // bump cannot exercise the branch that reports one that cannot.
          types: [['cartoon'], ['line'], ['spacefill']],
          get: (type: string) => ({
            getParams: () =>
              type === 'spacefill'
                ? { sizeFactor: 1, alpha: 1 }
                : { alpha: 1, aspectRatio: 1 },
          }),
        },
        themes: {
          // `add` and `get` are real methods on the live registries, and the
          // dispatcher registers a size theme the moment it is created. A fake
          // carrying only `types` failed every test in this file at once with
          // "sizeThemeRegistry.add is not a function" — which is a fake that
          // had drifted from the thing it stands in for, not a broken feature.
          colorThemeRegistry: {
            types: [['chain-id'], ['element-symbol']],
            add: vi.fn(),
          },
          sizeThemeRegistry: {
            types: [['uniform'], ['physical'], ['uncertainty']],
            registered: [] as any[],
            add(provider: any) {
              this.registered.push(provider);
              this.types.push([provider.name]);
            },
            get(name: string) {
              return this.registered.find((p: any) => p.name === name);
            },
          },
        },
      },
    },
    clear: vi.fn(async () => {}),
    dataFormats: {
      get: (name: string) =>
        name === 'dx'
          ? {
              parse: vi.fn(async () => ({
                volume: { ref: 'volume-1', obj: { data: { grid: { stats: { min: -3, max: 4 } } } } },
              })),
            }
          : undefined,
    },
    builders: {
      data: { rawData: vi.fn(async () => ({})) },
      structure: {
        parseTrajectory: vi.fn(async () => ({})),
        hierarchy: { applyPreset: vi.fn(async () => {}) },
        representation: {
          // The real builder hangs the representation off the component, which
          // is how the opacity action finds it. A mock that only records the
          // call would let opacity() "succeed" against zero representations.
          addRepresentation: vi.fn(async (selector: any, params: any) => {
            const ref = selector?.ref ?? selector;
            const owner = componentRefs.find((c) => c.cell.transform.ref === ref);
            const cell = {
              transform: {
                ref: `repr-${ref}`,
                params: {
                  type: {
                    name: params.type,
                    params: {
                      // Mol* fills every declared parameter with its default,
                      // so a real params object always *has* the key. The
                      // material action tests for the key to decide whether a
                      // representation can take a bump at all, and a fake that
                      // omitted it made that branch untestable — the frequency
                      // silently went nowhere and the test read `undefined`.
                      // `label` is the real case with no surface to perturb.
                      ...(params.type === 'line' ? {} : { bumpFrequency: 1 }),
                      ...(params.typeParams ?? {}),
                    },
                  },
                },
              },
            };
            owner?.representations.push({ cell });
            return { ref: cell.transform.ref, cell };
          }),
        },
        tryCreateComponent: vi.fn(async (_ref: string, params: any) => {
          const ref = `component-${params.label}`;
          const cell = { transform: { ref }, state: { isHidden: false }, obj: { data: structure } };
          componentRefs.push({ cell, representations: [] });
          return { ref, data: structure, cell };
        }),
      },
    },
    managers: {
      structure: {
        hierarchy: { current: { structures: [hierarchyStructure] } },
        component: { toggleVisibility, updateRepresentationsTheme: vi.fn(async () => {}) },
      },
      // The one fit a load asks for, now that automatic fitting is off.
      camera: { reset: vi.fn() },
    },
    state: {
      getSnapshot: () => ({ id: 'snapshot-1' }),
      setSnapshot: vi.fn(async () => {}),
      data: {
        cells: { get: (ref: string) => componentRefs.find((c) => c.cell.transform.ref === ref)?.cell },
        build: () => {
          const builder: any = {
            delete: () => ({ commit: async () => {} }),
            // Mol*'s update takes a mutator over the cell's existing params,
            // so applying it here is what makes an assertion on the resulting
            // params mean anything.
            to: (cell: any) => ({
              update: (mutate: (params: any) => void) => {
                mutate(cell.transform.params);
                return builder;
              },
            }),
            commit: async () => {},
          };
          return builder;
        },
      },
    },
  };
}

/** Add the canvas and screenshot helper that only some actions touch.
 *
 * Deliberately not part of fakePlugin: with a canvas3d present every render
 * action runs the real settle loop against jsdom's requestAnimationFrame, which
 * turned a 15ms unit suite into a 3.5s one. The tests that need a canvas ask
 * for it.
 */
function withCanvas(plugin: any) {
  const canvasProps: any = {
    renderer: { backgroundColor: 0x000000 },
    transparentBackground: false,
    // Off by default, exactly as Mol* ships it; `load_structure` turns it on so
    // a later `show()` cannot take the camera. See backlog 26.
    camera: { manualReset: false },
    // Mirrors a live canvas, read off one with CDP: occlusion and bloom start
    // on with full parameter groups, everything else is off and — crucially —
    // an off mapped static carries `params: {}`. That last detail is the whole
    // reason effects have to send a complete group when switching on.
    postprocessing: {
      occlusion: { name: 'on', params: { samples: 32, radius: 5, bias: 0.8 } },
      shadow: { name: 'off', params: {} },
      outline: { name: 'off', params: {} },
      dof: { name: 'off', params: {} },
      sharpening: { name: 'off', params: {} },
      bloom: { name: 'on', params: { strength: 1, mode: 'emissive' } },
      background: { variant: { name: 'off', params: {} } },
    },
  };
  // The real helper's value set, read off a live one: axes, format,
  // illumination, resolution, transparent.
  let screenshotValues: any = {
    transparent: false,
    format: { name: 'png' },
    resolution: { name: 'viewport', params: {} },
    axes: { name: 'off', params: {} },
    illumination: { extraIterations: 0, targetIterationTimeMs: 100 },
  };
  plugin.canvas3d = {
    props: canvasProps,
    setProps: vi.fn((props: any) => {
      if (props.renderer) Object.assign(canvasProps.renderer, props.renderer);
      if (props.transparentBackground !== undefined) {
        canvasProps.transparentBackground = props.transparentBackground;
      }
      if (props.postprocessing) {
        Object.assign(canvasProps.postprocessing, props.postprocessing);
      }
      // `load_structure` takes the camera off Mol*'s automatic fitting here.
      if (props.camera) Object.assign(canvasProps.camera, props.camera);
    }),
    // Settled instantly: these tests are about props, not about timing.
    commitQueueSize: { value: 0 },
    reprCount: { value: 0 },
  };
  plugin.helpers = {
    viewportScreenshot: {
      get values() {
        return screenshotValues;
      },
      behaviors: {
        // BehaviorSubject.next *replaces* the value; merging here would make
        // restoring a previous snapshot of it silently leave keys behind, and
        // the test for that would pass against a bug.
        values: { next: vi.fn((v: any) => (screenshotValues = v)) },
      },
    },
  };
  return plugin;
}

describe('createDispatcher', () => {
  beforeEach(() => {
    // jsdom reports 'visible', so render actions settle without spinning the
    // turbo pump — and settling is a no-op here because fakePlugin has no
    // canvas3d. The 'settling a visible tab' block below supplies one.
    window.__protean = { setTurbo: vi.fn() };
  });

  it('rejects an unknown action', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(dispatch('nope', {})).rejects.toThrow('Unknown action: nope');
  });

  it('registers the preset components under the auto handle', async () => {
    const plugin = fakePlugin();
    const dispatch = createDispatcher(plugin);
    plugin.componentRefs.push({ cell: { transform: { ref: 'preset-1' }, state: {} } });

    const loaded: any = await dispatch('load_structure', {
      name: '1ubq', format: 'mmcif', data: 'x',
    });
    expect(loaded.auto_components).toBe(1);

    const listed: any = await dispatch('list_selections', {});
    expect(listed.selections.map((s: any) => s.name)).toContain('auto');
  });

  it('reports what a selection matched', async () => {
    const dispatch = createDispatcher(fakePlugin());
    const out: any = await dispatch('select', { name: 'sele', expression: '(sel.atom.all)' });
    expect(out).toMatchObject({ name: 'sele', atom_count: 1, residue_count: 1, chains: ['A'] });
  });

  it('names the known handles when one is missing', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await dispatch('select', { name: 'pocket', expression: '(sel.atom.all)' });
    await expect(dispatch('hide', { name: 'typo' })).rejects.toThrow(
      /No selection named 'typo'\. Known: pocket/
    );
  });

  it('only toggles components whose visibility actually differs', async () => {
    const plugin = fakePlugin();
    const dispatch = createDispatcher(plugin);
    await dispatch('select', { name: 'sele', expression: '(sel.atom.all)' });

    const hidden: any = await dispatch('hide', { name: 'sele' });
    expect(hidden).toMatchObject({ hidden: true, changed: 1 });
    expect(plugin.toggleVisibility).toHaveBeenCalledTimes(1);

    // Already hidden — must not flip it back by toggling blindly.
    const again: any = await dispatch('hide', { name: 'sele' });
    expect(again.changed).toBe(0);
    expect(plugin.toggleVisibility).toHaveBeenCalledTimes(1);

    const shown: any = await dispatch('unhide', { name: 'sele' });
    expect(shown).toMatchObject({ hidden: false, changed: 1 });
  });

  it('rejects an unknown representation instead of drawing nothing', async () => {
    // Mol* accepts a bogus name without complaint and renders nothing, so a
    // typo would be indistinguishable from an empty selection.
    const dispatch = createDispatcher(fakePlugin());
    await expect(
      dispatch('show', { name: 's', expression: '(sel.atom.all)', representation: 'cartoonn' })
    ).rejects.toThrow(
      /Unknown representation 'cartoonn'\. Available: cartoon, line, spacefill/
    );
  });

  it('rejects an unknown colour theme', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(
      dispatch('show', {
        name: 's', expression: '(sel.atom.all)', representation: 'cartoon', color: 'nope',
      })
    ).rejects.toThrow(/Unknown colour theme 'nope'/);
  });

  it('rejects an unknown size theme against the live registry', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(dispatch('size', { name: 's', size: 'thickness' })).rejects.toThrow(
      /Unknown size theme 'thickness'\. Available: jitter, physical, uncertainty, uniform/
    );
  });

  it('treats a hex value as a literal colour, not a theme name', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(
      dispatch('show', {
        name: 's', expression: '(sel.atom.all)', representation: 'cartoon', color: '#ff0000',
      })
    ).resolves.toMatchObject({ name: 's' });
  });

  it('reports the names it accepts', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(dispatch('capabilities', {})).resolves.toEqual({
      representations: ['cartoon', 'line', 'spacefill'],
      color_themes: ['chain-id', 'element-symbol'],
      // `jitter` is protean's own, registered when the dispatcher is built.
      size_themes: ['jitter', 'physical', 'uncertainty', 'uniform'],
      // Named styles are reported for the same reason the registries are: a
      // model can only choose from what it can see at the point of use.
      lighting_rigs: ['flat', 'rim', 'ring', 'standard', 'studio', 'three-point'],
      shading_styles: ['cel', 'flat', 'normal', 'xray', 'xray-inverted'],
      gradients: ['off', 'horizontal', 'radial'],
      material_finishes: ['chrome', 'glossy', 'matte', 'metallic', 'satin'],
      path_trace_quality: ['draft', 'high', 'standard', 'ultra'],
    });
  });

  it('skips validation when the registry cannot be read', async () => {
    // Better to attempt the call than to block on an empty list.
    const plugin: any = fakePlugin();
    plugin.representation = {};
    const dispatch = createDispatcher(plugin);
    await expect(
      dispatch('show', { name: 's', expression: '(sel.atom.all)', representation: 'anything' })
    ).resolves.toMatchObject({ name: 's' });
  });

  it('saves the snapshot alongside the named handles', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await dispatch('select', { name: 'pocket', expression: '(sel.atom.all)' });
    const saved: any = await dispatch('save_session', {});
    expect(saved.snapshot).toEqual({ id: 'snapshot-1' });
    expect(Object.keys(saved.handles)).toEqual(['pocket']);
  });

  it('restores handles and reports ones the state no longer contains', async () => {
    // A stale or hand-edited session should lose handles rather than keep ones
    // pointing at components that are not there.
    const plugin = fakePlugin();
    const dispatch = createDispatcher(plugin);
    plugin.componentRefs.push({ cell: { transform: { ref: 'live-ref' }, state: {} } });

    const result: any = await dispatch('load_session', {
      snapshot: { id: 'x' },
      handles: { kept: ['live-ref'], gone: ['dead-ref'] },
    });
    expect(plugin.state.setSnapshot).toHaveBeenCalled();
    expect(result.restored).toEqual(['kept']);
    expect(result.dropped).toEqual(['gone']);
  });

  it('replaces any pre-existing handles on load', async () => {
    const plugin = fakePlugin();
    const dispatch = createDispatcher(plugin);
    await dispatch('select', { name: 'stale', expression: '(sel.atom.all)' });
    await dispatch('load_session', { snapshot: {}, handles: {} });
    const listed: any = await dispatch('list_selections', {});
    expect(listed.selections).toEqual([]);
  });

  it('passes size through as a sizeFactor', async () => {
    const plugin = fakePlugin();
    const dispatch = createDispatcher(plugin);
    const result: any = await dispatch('show', {
      name: 's', expression: '(sel.atom.all)', representation: 'spacefill', size: 0.35,
    });
    expect(result).toMatchObject({ size: 0.35, size_validated: true });
    expect(
      plugin.builders.structure.representation.addRepresentation
    ).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      typeParams: { sizeFactor: 0.35 },
    }));
  });

  it('refuses size on a representation that has no sizeFactor', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(
      dispatch('show', {
        name: 's', expression: '(sel.atom.all)', representation: 'cartoon', size: 2,
      })
    ).rejects.toThrow(/has no size control/);
  });

  it('says so when it cannot check whether size is supported', async () => {
    // Reporting an unchecked size beats silently pretending it was validated.
    const plugin: any = fakePlugin();
    plugin.representation.structure.registry.get = () => {
      throw new Error('registry unavailable');
    };
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const dispatch = createDispatcher(plugin);
    const result: any = await dispatch('show', {
      name: 's', expression: '(sel.atom.all)', representation: 'spacefill', size: 0.5,
    });
    expect(result.size_validated).toBe(false);
  });

  it('labels a selection at the requested level', async () => {
    const plugin = fakePlugin();
    const dispatch = createDispatcher(plugin);
    await dispatch('select', { name: 'site', expression: '(sel.atom.all)' });
    const result: any = await dispatch('label', { name: 'site', level: 'chain' });
    expect(result).toMatchObject({ name: 'site', level: 'chain' });
    expect(
      plugin.builders.structure.representation.addRepresentation
    ).toHaveBeenCalledWith(expect.anything(), { type: 'label', typeParams: { level: 'chain' } });
  });

  it('defaults labels to residue level and rejects an unknown one', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await dispatch('select', { name: 'site', expression: '(sel.atom.all)' });
    await expect(dispatch('label', { name: 'site' })).resolves.toMatchObject({
      level: 'residue',
    });
    await expect(dispatch('label', { name: 'site', level: 'atoms' })).rejects.toThrow(
      /Unknown label level 'atoms'\. Available: chain, residue, element/
    );
  });

  it('drops the handle on remove', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await dispatch('select', { name: 'sele', expression: '(sel.atom.all)' });
    const removed: any = await dispatch('remove', { name: 'sele' });
    expect(removed).toMatchObject({ name: 'sele', removed: 1 });
    await expect(dispatch('hide', { name: 'sele' })).rejects.toThrow('No selection named');
  });

  it('pumps render actions when the tab is hidden, and skips the pump otherwise', async () => {
    const plugin = fakePlugin();
    const dispatch = createDispatcher(plugin);
    const setTurbo = vi.fn();
    window.__protean = { setTurbo };

    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    await dispatch('select', { name: 'sele', expression: '(sel.atom.all)' });
    expect(setTurbo).toHaveBeenCalledWith(true);
    expect(setTurbo).toHaveBeenLastCalledWith(false);

    setTurbo.mockClear();
    // list_selections is not a render action, so it must not spin the pump.
    await dispatch('list_selections', {});
    expect(setTurbo).not.toHaveBeenCalled();
  });

  it('colours a selection from a volume and reports its range', async () => {
    const plugin: any = fakePlugin();
    const dispatch = createDispatcher(plugin);
    await dispatch('select', { name: 'surf', expression: '(sel.atom.all)' });

    const result: any = await dispatch('color_by_volume', {
      name: 'surf',
      volume: 'object 1 class gridpositions counts 2 2 2',
      domain: [-5, 5],
    });

    expect(result.volume_min).toBe(-3);
    expect(result.volume_max).toBe(4);
    const theme = plugin.managers.structure.component.updateRepresentationsTheme.mock.calls.at(-1)[1];
    expect(theme.color).toBe('external-volume');
    // A ColorList value must carry real colours; a preset name with an empty
    // array paints the surface black, which reads as a render failure.
    expect(theme.colorParams.coloring.params.list.colors.length).toBeGreaterThan(1);
    // The theme resolves the volume through a getter, not the ref alone.
    expect(theme.colorParams.volume.getValue()).toBeTruthy();
  });

  it('refuses to colour a selection that was never shown', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(
      dispatch('color_by_volume', { name: 'ghost', volume: 'x' })
    ).rejects.toThrow(/No selection named/);
  });
});

describe('rotateAbout', () => {
  const close = (a: number[], b: number[]) =>
    a.every((value, i) => Math.abs(value - b[i]) < 1e-9);

  it('is the identity for a full turn', () => {
    // The property the whole turntable rests on: 36 steps of 10 degrees have
    // to come home, or a looping sequence jumps at the seam.
    let v = [3, 0, 4];
    for (let i = 0; i < 36; i++) v = rotateAbout(v, [0, 1, 0], (10 * Math.PI) / 180);
    expect(close(v, [3, 0, 4])).toBe(true);
  });

  it('rotates a quarter turn about the up axis', () => {
    const turned = rotateAbout([1, 0, 0], [0, 1, 0], Math.PI / 2);
    expect(close(turned.map((n) => Math.round(n * 1e9) / 1e9), [0, 0, -1])).toBe(true);
  });

  it('leaves a vector along the axis untouched', () => {
    expect(close(rotateAbout([0, 5, 0], [0, 1, 0], 1.234), [0, 5, 0])).toBe(true);
  });

  it('preserves length', () => {
    const turned = rotateAbout([1, 2, 3], [4, 5, 6], 0.7);
    expect(Math.hypot(...turned)).toBeCloseTo(Math.hypot(1, 2, 3), 12);
  });

  it('normalises the axis rather than trusting it', () => {
    const unit = rotateAbout([1, 0, 0], [0, 1, 0], 0.5);
    const long = rotateAbout([1, 0, 0], [0, 9, 0], 0.5);
    expect(close(unit, long)).toBe(true);
  });
});

describe('orbit and spin', () => {
  function orbiting(plugin: any) {
    withCanvas(plugin);
    const snapshot: any = { position: [0, 0, 10], target: [0, 0, 0], up: [0, 1, 0] };
    plugin.canvas3d.camera = {
      getSnapshot: () => ({ ...snapshot }),
      setState: vi.fn((next: any) => Object.assign(snapshot, next)),
      state: snapshot,
    };
    plugin.canvas3d.props.trackball = { animate: { name: 'off', params: {} } };
    const setProps = plugin.canvas3d.setProps;
    plugin.canvas3d.setProps = vi.fn((props: any) => {
      setProps(props);
      if (props.trackball) Object.assign(plugin.canvas3d.props.trackball, props.trackball);
    });
    return { plugin, snapshot };
  }

  it('swings the camera around the target', async () => {
    const { plugin, snapshot } = orbiting(fakePlugin());
    await createDispatcher(plugin)('orbit', { degrees: 90 });

    // Started at +Z looking at the origin; a quarter turn about +Y lands on +X.
    expect(snapshot.position[0]).toBeCloseTo(10, 6);
    expect(snapshot.position[2]).toBeCloseTo(0, 6);
    expect(snapshot.target).toEqual([0, 0, 0]);
  });

  it('keeps the camera the same distance away', async () => {
    const { plugin, snapshot } = orbiting(fakePlugin());
    await createDispatcher(plugin)('orbit', { degrees: 37 });
    expect(Math.hypot(...snapshot.position)).toBeCloseTo(10, 6);
  });

  it('moves without a tween, so a frame grab gets where it was put', async () => {
    const { plugin } = orbiting(fakePlugin());
    await createDispatcher(plugin)('orbit', { degrees: 45 });
    expect(plugin.canvas3d.camera.setState.mock.calls.at(-1)[1]).toBe(0);
  });

  it('refuses a degree count that is not a number', async () => {
    const { plugin } = orbiting(fakePlugin());
    await expect(
      createDispatcher(plugin)('orbit', { degrees: Number.NaN })
    ).rejects.toThrow(/number of degrees/);
  });

  it('sets a live spin and reports what the canvas took', async () => {
    const { plugin } = orbiting(fakePlugin());
    const result: any = await createDispatcher(plugin)('spin', { mode: 'spin', speed: 2 });
    expect(plugin.canvas3d.props.trackball.animate).toMatchObject({
      name: 'spin',
      params: { speed: 2 },
    });
    expect(result.mode).toBe('spin');
  });

  it('gives rock its own defaults and an angle', async () => {
    const { plugin } = orbiting(fakePlugin());
    const result: any = await createDispatcher(plugin)('spin', { mode: 'rock' });
    expect(result.mode).toBe('rock');
    expect(result.angle).toBe(10);
  });

  it('stops with an empty parameter group', async () => {
    const { plugin } = orbiting(fakePlugin());
    await createDispatcher(plugin)('spin', { mode: 'off' });
    expect(plugin.canvas3d.props.trackball.animate).toEqual({ name: 'off', params: {} });
  });

  it('refuses an unknown spin mode', async () => {
    const { plugin } = orbiting(fakePlugin());
    await expect(createDispatcher(plugin)('spin', { mode: 'tumble' })).rejects.toThrow(
      /Unknown spin mode 'tumble'/
    );
  });
});

describe('snapshot', () => {
  /** A screenshot helper that records what it was told and what it returned. */
  function withHelper(plugin: any) {
    withCanvas(plugin);
    const helper = plugin.helpers.viewportScreenshot;
    const captured: any[] = [];
    helper.getSizeAndViewport = () => ({ width: 800, height: 600 });
    helper.getImageDataUri = vi.fn(async () => {
      captured.push(JSON.parse(JSON.stringify(helper.values)));
      return 'data:image/png;base64,AAAA';
    });
    helper.imagePass = {};
    helper.autocrop = vi.fn();
    helper.resetCrop = vi.fn();
    helper.cropParams = { auto: false, relativePadding: 0 };
    helper.behaviors.cropParams = { next: vi.fn((v: any) => (helper.cropParams = v)) };
    return { plugin, helper, captured };
  }

  it('captures at the requested width, keeping the viewport aspect', async () => {
    const { plugin, captured } = withHelper(fakePlugin());
    const result: any = await createDispatcher(plugin)('snapshot', { width: 4323 });

    // 800x600 viewport, so 4323 wide implies 3242 tall rather than a square.
    expect(captured[0].resolution).toEqual({
      name: 'custom',
      params: { width: 4323, height: 3242 },
    });
    expect(result.requested_height).toBe(3242);
  });

  it('honours an explicit height', async () => {
    const { plugin, captured } = withHelper(fakePlugin());
    await createDispatcher(plugin)('snapshot', { width: 1000, height: 1000 });
    expect(captured[0].resolution.params).toEqual({ width: 1000, height: 1000 });
  });

  it('always asks Mol* for PNG, whatever the file will end up as', async () => {
    // PNG is lossless and the only format here with an alpha channel; Python
    // converts onward. Capturing JPEG would bake compression artefacts into a
    // TIFF that is meant to be lossless.
    const { plugin, captured } = withHelper(fakePlugin());
    await createDispatcher(plugin)('snapshot', { width: 500 });
    expect(captured[0].format).toEqual({ name: 'png', params: {} });
  });

  it('puts the helper back exactly as it found it', async () => {
    // The load-bearing one. These values persist on the helper, so without
    // restoring them the next ordinary screenshot silently comes back at
    // figure resolution — and nothing about it would look wrong.
    const { plugin, helper } = withHelper(fakePlugin());
    const before = JSON.parse(JSON.stringify(helper.values));

    await createDispatcher(plugin)('snapshot', { width: 4000, transparent: true });

    expect(helper.values).toEqual(before);
  });

  it('puts the helper back even when the capture fails', async () => {
    const { plugin, helper } = withHelper(fakePlugin());
    const before = JSON.parse(JSON.stringify(helper.values));
    helper.getImageDataUri = vi.fn(async () => {
      throw new Error('GL context lost');
    });

    await expect(
      createDispatcher(plugin)('snapshot', { width: 4000 })
    ).rejects.toThrow(/GL context lost/);
    expect(helper.values).toEqual(before);
  });

  it('crops only when asked, and says which it did', async () => {
    const { plugin, helper } = withHelper(fakePlugin());
    const dispatch = createDispatcher(plugin);

    const plain: any = await dispatch('snapshot', { width: 500 });
    expect(helper.resetCrop).toHaveBeenCalled();
    expect(helper.autocrop).not.toHaveBeenCalled();
    expect(plain.cropped).toBe(false);

    const cropped: any = await dispatch('snapshot', { width: 500, crop: true });
    expect(helper.autocrop).toHaveBeenCalled();
    expect(cropped.cropped).toBe(true);
  });

  it('applies transparency for this capture only', async () => {
    const { plugin, captured, helper } = withHelper(fakePlugin());
    await createDispatcher(plugin)('snapshot', { width: 500, transparent: true });

    expect(captured[0].transparent).toBe(true);
    expect(helper.values.transparent).toBe(false);
  });

  it('reports the time a traced capture cost', async () => {
    const { plugin } = withHelper(fakePlugin());
    plugin.canvas3d.props.illumination = { enabled: true };
    const result: any = await createDispatcher(plugin)('snapshot', { width: 500 });
    expect(result).toHaveProperty('traced_ms');
  });

  it('refuses a width that is not a whole number of pixels', async () => {
    const { plugin } = withHelper(fakePlugin());
    await expect(
      createDispatcher(plugin)('snapshot', { width: 0 })
    ).rejects.toThrow(/whole number of pixels/);
  });
});

describe('path tracing', () => {
  /** A canvas with the four WebGL extensions IlluminationPass needs. */
  function tracing(plugin: any, extensions?: Record<string, boolean>) {
    withCanvas(plugin);
    plugin.canvas3d.webgl = {
      extensions: extensions ?? {
        textureFloat: true,
        colorBufferFloat: true,
        depthTexture: true,
        drawBuffers: true,
      },
    };
    plugin.canvas3d.props.illumination = { enabled: false, maxIterations: 5, bounces: 4 };
    const setProps = plugin.canvas3d.setProps;
    plugin.canvas3d.setProps = vi.fn((props: any) => {
      setProps(props);
      if (props.illumination) Object.assign(plugin.canvas3d.props.illumination, props.illumination);
    });
    return plugin;
  }

  it('translates quality into an iteration count and reports the samples', async () => {
    // Mol* counts iterations as a power of two, so asking for 128 samples
    // directly would mean computing a logarithm at the call site.
    const plugin: any = tracing(fakePlugin());
    const result: any = await createDispatcher(plugin)('path_trace', { quality: 'high' });

    expect(plugin.canvas3d.props.illumination.maxIterations).toBe(7);
    expect(result.samples).toBe(128);
    expect(result.enabled).toBe(true);
  });

  it('refuses to enable tracing the browser cannot do', async () => {
    // The whole reason this check exists: IlluminationPass does not throw when
    // its extensions are missing. Its constructor returns early and leaves the
    // pass permanently unsupported, so Mol* would render an ordinary raster
    // image and report success.
    const plugin: any = tracing(fakePlugin(), {
      textureFloat: true,
      colorBufferFloat: false,
      depthTexture: true,
      drawBuffers: false,
    });

    await expect(
      createDispatcher(plugin)('path_trace', { enabled: true })
    ).rejects.toThrow(/cannot path trace.*colorBufferFloat, drawBuffers/s);
  });

  it('still lets tracing be switched off on a browser that cannot do it', async () => {
    // Refusing here would strand anyone who enabled it before a context loss.
    const plugin: any = tracing(fakePlugin(), { textureFloat: false });
    const result: any = await createDispatcher(plugin)('path_trace', { enabled: false });
    expect(result.enabled).toBe(false);
  });

  it('reads enabled back off the canvas rather than echoing the argument', async () => {
    const plugin: any = tracing(fakePlugin());
    // A canvas that refuses the pass leaves the flag false.
    plugin.canvas3d.setProps = vi.fn();
    const result: any = await createDispatcher(plugin)('path_trace', { enabled: true });
    expect(result.enabled).toBe(false);
  });

  it('carries bounces, shadows and denoise when given', async () => {
    const plugin: any = tracing(fakePlugin());
    await createDispatcher(plugin)('path_trace', {
      quality: 'draft',
      bounces: 8,
      shadows: false,
      denoise: false,
    });

    const sent = plugin.canvas3d.setProps.mock.calls.at(-1)[0].illumination;
    expect(sent).toMatchObject({
      enabled: true,
      maxIterations: 3,
      bounces: 8,
      shadowEnable: false,
      denoise: false,
    });
  });

  it('leaves bounces alone when not mentioned', async () => {
    const plugin: any = tracing(fakePlugin());
    await createDispatcher(plugin)('path_trace', {});
    expect(plugin.canvas3d.setProps.mock.calls.at(-1)[0].illumination).not.toHaveProperty(
      'bounces'
    );
  });

  it('refuses an unknown quality and lists the real ones', async () => {
    await expect(
      createDispatcher(tracing(fakePlugin()))('path_trace', { quality: 'cinema' })
    ).rejects.toThrow(/Unknown path-trace quality 'cinema'.*draft, high/s);
  });

  it('refuses an out-of-range bounce count', async () => {
    await expect(
      createDispatcher(tracing(fakePlugin()))('path_trace', { bounces: 99 })
    ).rejects.toThrow(/bounces must be a whole number from 1 to 16/);
  });
});

describe('the jitter size theme', () => {
  const jitterOf = (plugin: any) =>
    plugin.representation.structure.themes.sizeThemeRegistry.get('jitter');

  it('is registered as soon as the dispatcher exists', () => {
    const plugin: any = withCanvas(fakePlugin());
    createDispatcher(plugin);

    expect(jitterOf(plugin)).toBeTruthy();
    expect(
      plugin.representation.structure.themes.sizeThemeRegistry.types
        .map((t: any[]) => t[0])
    ).toContain('jitter');
  });

  it('gives every copy of an atom the same radius', () => {
    // The reason it hashes rather than randomises. A biological assembly holds
    // symmetry copies of one atom, and an RNG makes one mate fatter than
    // another — which reads as a broken structure, not a texture, and changes
    // on every reload. Same element index, same wobble, forever.
    const plugin: any = withCanvas(fakePlugin());
    createDispatcher(plugin);
    const size = jitterOf(plugin).factory({}, {}).size;

    const first = size({ element: 41 });
    expect(size({ element: 41 })).toBe(first);

    const second: any = withCanvas(fakePlugin());
    createDispatcher(second);
    expect(jitterOf(second).factory({}, {}).size({ element: 41 })).toBe(first);
  });

  it('wobbles around the radius rather than replacing it', () => {
    // Falling back to a constant would redraw every atom the same size and
    // look entirely deliberate, so the spread has to stay narrow and centred.
    const plugin: any = withCanvas(fakePlugin());
    createDispatcher(plugin);
    const size = jitterOf(plugin).factory({}, {}).size;

    const radii = Array.from({ length: 200 }, (_, i) => size({ element: i }));
    expect(Math.min(...radii)).toBeGreaterThan(1.7 * 0.92);
    expect(Math.max(...radii)).toBeLessThan(1.7 * 1.08);
    expect(new Set(radii).size).toBeGreaterThan(150);
  });
});

describe('materials', () => {
  async function shown(plugin: any) {
    const dispatch = createDispatcher(plugin);
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
    });
    return dispatch;
  }

  const materialOf = (plugin: any) =>
    plugin.componentRefs[0].representations[0].cell.transform.params.type.params;

  it('applies a finish as PBR values', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    await dispatch('material', { name: 'sele', finish: 'chrome' });

    expect(materialOf(plugin).material).toEqual({
      metalness: 1.0,
      roughness: 0.1,
      bumpiness: 0,
    });
    expect(materialOf(plugin).material.bumpiness).toBe(0);
  });

  it('runs from dull to sharp, unlike Mol*s own preset labels', async () => {
    // Mol* ships Plastic at roughness 0.2 and Glossy at 0.6, so its "glossy" is
    // the duller of the two. Roughness is 0 for a mirror and 1 for fully
    // diffuse, so a model asking for `glossy` would get the opposite of what it
    // wanted. This pins the ordering rather than the numbers.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);

    const roughness: number[] = [];
    for (const finish of ['matte', 'satin', 'glossy', 'chrome']) {
      await dispatch('material', { name: 'sele', finish });
      roughness.push(materialOf(plugin).material.roughness);
    }

    expect(roughness).toEqual([...roughness].sort((a, b) => b - a));
    expect(new Set(roughness).size).toBe(roughness.length);
  });

  it('lets an explicit value override the finish it came from', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    await dispatch('material', { name: 'sele', finish: 'matte', roughness: 0.05 });

    expect(materialOf(plugin).material).toMatchObject({ roughness: 0.05, metalness: 0 });
  });

  it('puts bumpiness in the material group and frequency beside it', async () => {
    // The two halves of a bump live in different places: `bumpiness` is a
    // member of the material group, `bumpFrequency` is a parameter of the
    // representation. Setting one without the other draws nothing.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    const reply: any = await dispatch('material', {
      name: 'sele',
      finish: 'matte',
      bumpiness: 0.9,
      bump_frequency: 6,
    });

    expect(materialOf(plugin).material.bumpiness).toBe(0.9);
    expect(materialOf(plugin).bumpFrequency).toBe(6);
    expect(materialOf(plugin).material).not.toHaveProperty('bumpFrequency');
    expect(reply.bump_frequency_applied_to).toBe(1);
  });

  it('reports that a representation with no surface took no frequency', async () => {
    // `line` declares no bumpFrequency — nor do point and label — so asking
    // for one is a request that cannot be honoured. The count is how the
    // caller finds that out instead of seeing a plain success.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = createDispatcher(plugin);
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'line',
    });
    const reply: any = await dispatch('material', {
      name: 'sele',
      finish: 'matte',
      bumpiness: 0.9,
      bump_frequency: 6,
    });

    expect(reply.representations).toBe(1);
    expect(reply.bump_frequency_applied_to).toBe(0);
  });

  it('leaves a surface smooth unless bumpiness was asked for', async () => {
    // A finish is a claim about gloss, not about texture, so the control has to
    // stay off by default — including after a call that changes other things.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    await dispatch('material', { name: 'sele', finish: 'matte', bumpiness: 0.9 });
    await dispatch('material', { name: 'sele', finish: 'glossy' });

    expect(materialOf(plugin).material.bumpiness).toBe(0);
  });

  it('refuses a frequency outside the range Mol* accepts', async () => {
    // Out of range, Mol* clamps and reports success — the exact shape of thing
    // this project keeps finding, so it is refused here instead.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);

    await expect(
      dispatch('material', { name: 'sele', finish: 'matte', bump_frequency: 40 })
    ).rejects.toThrow(/between 0 and 10/);
    await expect(
      dispatch('material', { name: 'sele', finish: 'matte', bumpiness: 4 })
    ).rejects.toThrow(/between 0 and 1/);
  });

  it('sets emissive separately from the material group', async () => {
    // emissive is a sibling of `material` in Mol*'s params, not a member of it.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    await dispatch('material', { name: 'sele', finish: 'matte', emissive: 0.7 });

    expect(materialOf(plugin).emissive).toBe(0.7);
    expect(materialOf(plugin).material).not.toHaveProperty('emissive');
  });

  it('leaves emissive alone when it was not mentioned', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    await dispatch('material', { name: 'sele', finish: 'matte', emissive: 0.7 });
    await dispatch('material', { name: 'sele', finish: 'glossy' });

    expect(materialOf(plugin).emissive).toBe(0.7);
  });

  it('says whether bloom will actually show anything', async () => {
    // Bloom defaults to mode 'emissive' and emissive defaults to 0, so bloom is
    // on out of the box and correctly draws nothing. Reporting that is the
    // difference between "bloom is broken" and "bloom has nothing to glow".
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);

    const dark: any = await dispatch('material', { name: 'sele', finish: 'matte' });
    expect(dark.bloom_will_show).toBe(false);

    const lit: any = await dispatch('material', {
      name: 'sele',
      finish: 'matte',
      emissive: 0.8,
    });
    expect(lit.bloom_will_show).toBe(true);
  });

  it('reports bloom as not showing when bloom itself is off', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    await dispatch('effects', { bloom: false });

    const result: any = await dispatch('material', {
      name: 'sele',
      finish: 'matte',
      emissive: 0.8,
    });
    expect(result.bloom_will_show).toBe(false);
  });

  it('refuses an unknown finish and lists the real ones', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = await shown(plugin);
    await expect(
      dispatch('material', { name: 'sele', finish: 'velvet' })
    ).rejects.toThrow(/Unknown finish 'velvet'.*chrome, glossy/s);
  });

  it.each(['metalness', 'roughness', 'emissive'])(
    'refuses an out-of-range %s',
    async (key) => {
      const plugin: any = withCanvas(fakePlugin());
      const dispatch = await shown(plugin);
      await expect(
        dispatch('material', { name: 'sele', finish: 'matte', [key]: 5 })
      ).rejects.toThrow(/must be between 0 and 1/);
    }
  );

  it('refuses a handle that was never shown', async () => {
    const dispatch = createDispatcher(withCanvas(fakePlugin()));
    await dispatch('select', { name: 'sele', expression: '(sel.atom.all)' });
    await expect(
      dispatch('material', { name: 'sele', finish: 'matte' })
    ).rejects.toThrow(/no representation to give a material to/);
  });
});

describe('effects, shading and gradients', () => {
  const postOf = (plugin: any) =>
    plugin.canvas3d.setProps.mock.calls.at(-1)[0].postprocessing;

  it('enables an effect with a complete parameter group', async () => {
    // The load-bearing detail: a Mol* MappedStatic that is off carries
    // `params: {}` (verified against a live canvas), so flipping only the name
    // would switch an effect on with no parameters at all.
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('effects', { outline: true });

    expect(postOf(plugin).outline.name).toBe('on');
    expect(postOf(plugin).outline.params).toMatchObject({
      scale: 1,
      threshold: 0.33,
      color: 0x000000,
    });
  });

  it('leaves unmentioned effects alone so calls compose', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('effects', { outline: true });

    expect(postOf(plugin)).not.toHaveProperty('bloom');
    expect(postOf(plugin)).not.toHaveProperty('occlusion');
  });

  it('turns an effect off with an empty group', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('effects', { occlusion: false });

    expect(postOf(plugin).occlusion).toEqual({ name: 'off', params: {} });
  });

  it('reports the canvas state rather than the arguments', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const result: any = await createDispatcher(plugin)('effects', {
      outline: true,
      outline_color: '#ff0000',
    });

    expect(result).toMatchObject({ outline: true, outline_color: '#ff0000' });
    // Off effects report their colour as null rather than a stale value.
    expect(result.shadow).toBe(false);
  });

  it('refuses to tune an outline that is switched off', async () => {
    // Mol* would accept the params and draw nothing with them, so the caller
    // would believe a colour took effect that never will.
    await expect(
      createDispatcher(withCanvas(fakePlugin()))('effects', { outline_color: '#ff0000' })
    ).rejects.toThrow(/Set outline=true/);
  });

  it('refuses a call that changes nothing', async () => {
    await expect(
      createDispatcher(withCanvas(fakePlugin()))('effects', {})
    ).rejects.toThrow(/at least one effect/);
  });

  it('applies a shading style to an existing representation', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = createDispatcher(plugin);
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
    });

    const result: any = await dispatch('shading', { name: 'sele', style: 'xray' });

    const params = plugin.componentRefs[0].representations[0].cell.transform.params;
    expect(params.type.params).toMatchObject({ xrayShaded: true, celShaded: false });
    expect(result).toMatchObject({ style: 'xray', representations: 1 });
  });

  it("carries xray-inverted's string value rather than coercing it to a boolean", async () => {
    // Mol* types xrayShaded as `boolean | 'inverted'`. Sending `true` here
    // would silently give the ordinary ghost look instead.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = createDispatcher(plugin);
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
    });
    await dispatch('shading', { name: 'sele', style: 'xray-inverted' });

    const params = plugin.componentRefs[0].representations[0].cell.transform.params;
    expect(params.type.params.xrayShaded).toBe('inverted');
  });

  it('sets cel steps on the renderer, since they are global', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = createDispatcher(plugin);
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
    });
    await dispatch('shading', { name: 'sele', style: 'cel', cel_steps: 4 });

    expect(plugin.canvas3d.props.renderer.celSteps).toBe(4);
  });

  it('refuses shading a handle that was never shown', async () => {
    const dispatch = createDispatcher(withCanvas(fakePlugin()));
    await dispatch('select', { name: 'sele', expression: '(sel.atom.all)' });
    await expect(dispatch('shading', { name: 'sele', style: 'cel' })).rejects.toThrow(
      /no representation to shade/
    );
  });

  it('refuses an unknown shading style and lists the real ones', async () => {
    const dispatch = createDispatcher(withCanvas(fakePlugin()));
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
    });
    await expect(dispatch('shading', { name: 'sele', style: 'toon' })).rejects.toThrow(
      /Unknown shading style 'toon'.*cel, flat, normal/s
    );
  });

  it('maps a radial gradient onto the stop names Mol* expects', async () => {
    // Mol* names the stops differently per variant — center/edge here,
    // top/bottom for the horizontal one. Sending the wrong pair leaves the
    // gradient at its defaults and looks like the colours were ignored.
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('background', {
      gradient: 'radial',
      gradient_from: '#000000',
      gradient_to: '#ffffff',
    });

    const variant = postOf(plugin).background.variant;
    expect(variant.name).toBe('radialGradient');
    expect(variant.params).toMatchObject({ centerColor: 0x000000, edgeColor: 0xffffff });
  });

  it('maps a horizontal gradient onto top and bottom', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('background', {
      gradient: 'horizontal',
      gradient_from: '#112233',
      gradient_to: '#445566',
    });

    const variant = postOf(plugin).background.variant;
    expect(variant.name).toBe('horizontalGradient');
    expect(variant.params).toMatchObject({ topColor: 0x112233, bottomColor: 0x445566 });
  });

  it('turns a gradient back off', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('background', { gradient: 'off' });
    expect(postOf(plugin).background.variant).toEqual({ name: 'off', params: {} });
  });

  it('refuses gradient colours with no gradient to put them on', async () => {
    await expect(
      createDispatcher(withCanvas(fakePlugin()))('background', { gradient_from: '#000000' })
    ).rejects.toThrow(/Pass gradient=/);
  });

  it('refuses an unknown gradient', async () => {
    await expect(
      createDispatcher(withCanvas(fakePlugin()))('background', { gradient: 'diagonal' })
    ).rejects.toThrow(/Unknown gradient 'diagonal'/);
  });
});

describe('lighting rigs', () => {
  it('applies a rig as a light list and reports what the canvas took', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const result: any = await createDispatcher(plugin)('lighting', { rig: 'three-point' });

    const renderer = plugin.canvas3d.setProps.mock.calls.at(-1)[0].renderer;
    expect(renderer.light).toHaveLength(3);
    expect(renderer.ambientIntensity).toBe(0.3);
    // Read back off the canvas: a rejected light list leaves the previous one
    // in place and the scene just looks unchanged.
    expect(result).toMatchObject({ rig: 'three-point', lights: 3, ambient: 0.3 });
  });

  it('builds the ring rig by generating evenly spaced azimuths', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('lighting', { rig: 'ring' });

    const lights = plugin.canvas3d.setProps.mock.calls.at(-1)[0].renderer.light;
    expect(lights.map((l: any) => l.azimuth)).toEqual([0, 60, 120, 180, 240, 300]);
  });

  it('sends no directional light at all for the flat rig', async () => {
    // dLightCount 0 is valid in Mol*'s shader and means purely ambient. Worth
    // pinning: an empty array is easy to mistake for "nothing was applied".
    const plugin: any = withCanvas(fakePlugin());
    const result: any = await createDispatcher(plugin)('lighting', { rig: 'flat' });

    expect(plugin.canvas3d.setProps.mock.calls.at(-1)[0].renderer.light).toEqual([]);
    expect(result.lights).toBe(0);
    expect(result.ambient).toBe(1);
  });

  it('scales every light in the rig by intensity', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('lighting', { rig: 'three-point', intensity: 2 });

    const lights = plugin.canvas3d.setProps.mock.calls.at(-1)[0].renderer.light;
    expect(lights.map((l: any) => l.intensity)).toEqual([1.2, 0.5, 0.7]);
  });

  it('does not let a scaled call mutate the rig for the next one', async () => {
    // Mol* holds the light list by reference. Scaling a shared preset in place
    // would compound every call: 'standard' at intensity 2 twice would be 4x,
    // and nothing in the reply would say so.
    const plugin: any = withCanvas(fakePlugin());
    const dispatch = createDispatcher(plugin);

    await dispatch('lighting', { rig: 'standard', intensity: 3 });
    await dispatch('lighting', { rig: 'standard' });

    const lights = plugin.canvas3d.setProps.mock.calls.at(-1)[0].renderer.light;
    expect(lights[0].intensity).toBe(0.6);
  });

  it('lets ambient and exposure be overridden without leaving the rig', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('lighting', {
      rig: 'studio',
      ambient: 0.1,
      exposure: 1.5,
    });

    const renderer = plugin.canvas3d.setProps.mock.calls.at(-1)[0].renderer;
    expect(renderer.ambientIntensity).toBe(0.1);
    expect(renderer.exposure).toBe(1.5);
    expect(renderer.light).toHaveLength(3);
  });

  it('leaves exposure alone when it was not mentioned', async () => {
    const plugin: any = withCanvas(fakePlugin());
    await createDispatcher(plugin)('lighting', { rig: 'standard' });

    const renderer = plugin.canvas3d.setProps.mock.calls.at(-1)[0].renderer;
    expect(renderer).not.toHaveProperty('exposure');
  });

  it('refuses an unknown rig and lists the real ones', async () => {
    await expect(
      createDispatcher(withCanvas(fakePlugin()))('lighting', { rig: 'cinematic' })
    ).rejects.toThrow(/Unknown lighting rig 'cinematic'.*flat, rim, ring/s);
  });

  it('refuses a negative intensity', async () => {
    await expect(
      createDispatcher(withCanvas(fakePlugin()))('lighting', {
        rig: 'standard',
        intensity: -1,
      })
    ).rejects.toThrow(/must be 0 or more/);
  });
});

describe('background and opacity', () => {
  it('sets the canvas colour and reports what the canvas now holds', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const result: any = await createDispatcher(plugin)('background', {
      color: '#ff8800',
    });

    expect(plugin.canvas3d.setProps).toHaveBeenCalledWith({
      renderer: { backgroundColor: 0xff8800 },
    });
    // Read back off the canvas, not echoed from the argument: an echo would
    // report success for a value Mol* silently discarded.
    expect(result.background).toBe('#ff8800');
  });

  it('switches the screenshot pipeline to transparent as well as the canvas', async () => {
    const plugin: any = withCanvas(fakePlugin());
    const result: any = await createDispatcher(plugin)('background', {
      transparent: true,
    });

    expect(plugin.canvas3d.props.transparentBackground).toBe(true);
    // The load-bearing assertion. ViewportScreenshotHelper passes its own
    // `transparent` value to the image pass as transparentBackground,
    // overriding the canvas — so setting only the canvas yields a transparent
    // viewer and an opaque PNG from every single capture.
    expect(result.screenshot_transparent).toBe(true);
  });

  it('refuses a colour that is not a hex triplet', async () => {
    // parseInt('#oops'.slice(1), 16) is NaN, and a NaN background paints black
    // without complaint — indistinguishable from a broken renderer.
    await expect(
      createDispatcher(withCanvas(fakePlugin()))('background', { color: 'skyblue' })
    ).rejects.toThrow(/Expected a colour like/);
  });

  it('passes opacity to the representation when showing', async () => {
    const plugin: any = fakePlugin();
    await createDispatcher(plugin)('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
      opacity: 0.3,
    });

    const params = plugin.builders.structure.representation.addRepresentation.mock.calls
      .at(-1)[1];
    expect(params.typeParams).toMatchObject({ alpha: 0.3 });
  });

  it('keeps size and opacity together rather than one overwriting the other', async () => {
    const plugin: any = fakePlugin();
    await createDispatcher(plugin)('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'spacefill',
      size: 0.5,
      opacity: 0.4,
    });

    const params = plugin.builders.structure.representation.addRepresentation.mock.calls
      .at(-1)[1];
    expect(params.typeParams).toEqual({ sizeFactor: 0.5, alpha: 0.4 });
  });

  it('changes opacity on a representation that already exists', async () => {
    const plugin: any = fakePlugin();
    const dispatch = createDispatcher(plugin);
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
    });

    const result: any = await dispatch('opacity', { name: 'sele', opacity: 0.25 });

    expect(result).toMatchObject({ name: 'sele', opacity: 0.25, representations: 1 });
    const repr = plugin.componentRefs[0].representations[0];
    expect(repr.cell.transform.params.type.params.alpha).toBe(0.25);
  });

  it('refuses to set opacity on a handle that was never shown', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await dispatch('select', { name: 'sele', expression: '(sel.atom.all)' });

    // A selection component carries no geometry. Committing an empty update
    // would report success and change nothing on screen, which is exactly how
    // the recolouring bug behaved.
    await expect(dispatch('opacity', { name: 'sele', opacity: 0.5 })).rejects.toThrow(
      /no representation to make transparent/
    );
  });

  it.each([1.5, -0.2, NaN, 50])('refuses an out-of-range opacity (%s)', async (value) => {
    // Mol* clamps silently, so 50 would land as 1 — solid, the exact opposite
    // of what someone passing 50 meant by it.
    await expect(
      createDispatcher(fakePlugin())('show', {
        name: 'sele',
        expression: '(sel.atom.all)',
        representation: 'cartoon',
        opacity: value,
      })
    ).rejects.toThrow(/Opacity must be between 0 and 1/);
  });

  it('refuses an out-of-range opacity on an existing representation too', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await dispatch('show', {
      name: 'sele',
      expression: '(sel.atom.all)',
      representation: 'cartoon',
    });
    await expect(dispatch('opacity', { name: 'sele', opacity: 50 })).rejects.toThrow(
      /Opacity must be between 0 and 1/
    );
  });
});

/** A visible tab still has to wait for the renderer.
 *
 * Mol* commits geometry on the render loop after the state transaction has
 * already resolved, so an action that replies immediately is describing a
 * scene that has not been built. It costs nothing when the answer is a count
 * and everything when it is a picture: CI screenshotted a molecule that had
 * loaded successfully and got a blank canvas, twice, on a runner slow enough
 * for the gap to open.
 */
describe('settling a visible tab', () => {
  beforeEach(() => {
    // Explicitly visible. An earlier test mocks visibilityState to 'hidden'
    // and vitest does not restore it between tests, so without this these
    // tests take the *hidden* path — which has always settled, and would pass
    // just as happily with the visible path deleted. They did, until this line.
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  /** A plugin whose commit loop drains one unit of work per frame. */
  function withCommitLoop(queued: number) {
    const plugin: any = fakePlugin();
    const state = { queued, drawn: 0 };
    plugin.canvas3d = {
      props: { camera: { manualReset: false } },
      setProps: vi.fn((props: any) => {
        if (props.camera) plugin.canvas3d.props.camera = { ...props.camera };
      }),
      commitQueueSize: {
        get value() {
          return state.queued;
        },
      },
      reprCount: {
        get value() {
          return state.drawn;
        },
      },
    };
    const raf = vi.fn((cb: FrameRequestCallback) => {
      if (state.queued > 0) {
        state.queued--;
        state.drawn++;
      }
      cb(0);
      return 0;
    });
    vi.stubGlobal('requestAnimationFrame', raf);
    return { plugin, state, raf };
  }

  it('does not return until the commit queue has drained', async () => {
    const { plugin, state, raf } = withCommitLoop(5);
    window.__protean = { setTurbo: vi.fn() };

    await createDispatcher(plugin)('select', {
      name: 'sele',
      expression: '(sel.atom.all)',
    });

    expect(state.queued).toBe(0);
    expect(raf.mock.calls.length).toBeGreaterThan(5);
  });

  it('lets the work start before reading stillness as completion', async () => {
    // The specific bug: sampling an untouched queue finds 0 and calls it
    // drained. Nothing is pending here at all, and it must *still* give the
    // commit loop a few frames to queue the work the transaction implied.
    const { plugin, raf } = withCommitLoop(0);
    window.__protean = { setTurbo: vi.fn() };

    await createDispatcher(plugin)('select', {
      name: 'sele',
      expression: '(sel.atom.all)',
    });

    // Three frames of stillness alone would be three calls. More than that
    // means it waited before it started counting them.
    expect(raf.mock.calls.length).toBeGreaterThan(3);
  });

  it('waits for the camera the load preset moved, after the scene commits', async () => {
    // The preset frames the new molecule and Mol* tweens that like any other
    // camera move, so settling the *geometry* says nothing about it: a capture
    // taken straight after a load could be mid-flight. That is backlog 27, and
    // CI found it because two loads of identical coordinates drew two visibly
    // different frames on a slower renderer.
    //
    // **The ordering is the whole test.** Mol* resolves a requested camera
    // reset from `commit()`, and only when `commitScene` reports everything
    // committed — "Only reset the camera after the full scene has been
    // commited", canvas3d.js. So the camera cannot start moving while geometry
    // is still queued, and this fake reproduces exactly that: frames drain the
    // commit queue first and only then advance the camera. A camera wait placed
    // *inside* the action — where the first version of this fix put it — runs
    // while the queue is full, finds a camera that has not moved, counts that
    // as arrival, and returns. The tween then plays out under the render pump,
    // which watches only the queue. This test fails against that placement;
    // the version it replaced passed against it, because its camera advanced on
    // every frame no matter who was pumping.
    const plugin: any = fakePlugin();
    const camera = { state: { target: [0, 0, 0], radius: 10 } };
    const queue = { queued: 8, drawn: 0 };
    let moving = 20;
    plugin.canvas3d = {
      props: { camera: { manualReset: false } },
      setProps: vi.fn((props: any) => {
        if (props.camera) plugin.canvas3d.props.camera = { ...props.camera };
      }),
      commitQueueSize: {
        get value() {
          return queue.queued;
        },
      },
      reprCount: {
        get value() {
          return queue.drawn;
        },
      },
      camera,
    };
    const raf = vi.fn((cb: FrameRequestCallback) => {
      if (queue.queued > 0) {
        queue.queued--;
        queue.drawn++;
      } else if (moving > 0) {
        moving--;
        camera.state.radius += 1;
      }
      cb(0);
      return 0;
    });
    vi.stubGlobal('requestAnimationFrame', raf);
    window.__protean = { setTurbo: vi.fn() };

    await createDispatcher(plugin)('load_structure', {
      name: '1ubq', format: 'mmcif', data: 'x',
    });

    expect(queue.queued).toBe(0);
    expect(moving).toBe(0);
    expect(camera.state.radius).toBe(30);
  });

  it('still skips the pump for actions that draw nothing', async () => {
    const { plugin, raf } = withCommitLoop(5);
    const setTurbo = vi.fn();
    window.__protean = { setTurbo };

    await createDispatcher(plugin)('list_selections', {});

    expect(setTurbo).not.toHaveBeenCalled();
    expect(raf).not.toHaveBeenCalled();
  });
});

describe('a camera that never came to rest', () => {
  beforeEach(() => {
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('says so in the reply rather than reporting a still camera', async () => {
    // The budget expiring used to be invisible: the loop ran out, returned,
    // and a camera still travelling looked exactly like one that had arrived.
    // The only symptom was a figure framed for a scene that no longer existed.
    const plugin: any = fakePlugin();
    const camera = { state: { target: [0, 0, 0], radius: 10 } };
    plugin.canvas3d = {
      props: { camera: { manualReset: false } },
      setProps: vi.fn((props: any) => {
        if (props.camera) plugin.canvas3d.props.camera = { ...props.camera };
      }),
      commitQueueSize: { value: 0 },
      reprCount: { value: 0 },
      camera,
    };
    // Never stops moving, and time runs faster than the budget.
    let clock = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => (clock += 500));
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      camera.state.radius += 1;
      cb(0);
      return 0;
    });
    window.__protean = { setTurbo: vi.fn() };

    const loaded: any = await createDispatcher(plugin)('load_structure', {
      name: '1ubq', format: 'mmcif', data: 'x',
    });

    expect(loaded.camera_settled).toBe(false);
    expect(loaded.loaded).toBe('1ubq');
  });

  it('says nothing when it did settle, so the flag means something', async () => {
    const plugin: any = fakePlugin();
    const camera = { state: { target: [0, 0, 0], radius: 10 } };
    plugin.canvas3d = {
      props: { camera: { manualReset: false } },
      setProps: vi.fn((props: any) => {
        if (props.camera) plugin.canvas3d.props.camera = { ...props.camera };
      }),
      commitQueueSize: { value: 0 },
      reprCount: { value: 0 },
      camera,
    };
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
    window.__protean = { setTurbo: vi.fn() };

    const loaded: any = await createDispatcher(plugin)('load_structure', {
      name: '1ubq', format: 'mmcif', data: 'x',
    });

    expect(loaded).not.toHaveProperty('camera_settled');
  });
});

describe('rampColor', () => {
  // The interpolator a registered field paints through. Pure, so it is worth
  // pinning here rather than inferring it from a picture.
  const RWB = [0xd7191c, 0xffffff, 0x2c7bb6];

  it('returns the stops themselves at the ends and the middle', () => {
    expect(rampColor(RWB, 0)).toBe(0xd7191c);
    expect(rampColor(RWB, 0.5)).toBe(0xffffff);
    expect(rampColor(RWB, 1)).toBe(0x2c7bb6);
  });

  it('interpolates between two stops', () => {
    const quarter = rampColor(RWB, 0.25);
    // Halfway from red to white: every channel between the two.
    expect((quarter >> 16) & 0xff).toBeGreaterThan(0xd7);
    expect((quarter >> 8) & 0xff).toBeGreaterThan(0x19);
    expect((quarter >> 8) & 0xff).toBeLessThan(0xff);
  });

  it('clamps rather than wrapping — a value outside the domain is still a value', () => {
    expect(rampColor(RWB, -5)).toBe(0xd7191c);
    expect(rampColor(RWB, 5)).toBe(0x2c7bb6);
  });

  it('handles a single-stop palette without dividing by zero', () => {
    expect(rampColor([0x123456], 0.7)).toBe(0x123456);
  });
});

describe('atomAt', () => {
  // The resolver both registered themes read through. Exported because it is
  // the piece that was wrong first: a bond location carries `aUnit`/`aIndex`
  // rather than `unit`/`element`, so every ball-and-stick stick came back as
  // "no data" until it handled that.
  const unit = { kind: 0, elements: [10, 11, 12] };

  it('reads an atom location directly', () => {
    expect(atomAt({ unit, element: 7 })).toEqual({ unit, element: 7 });
  });

  it('reads the atom at one end of a bond', () => {
    expect(atomAt({ aUnit: unit, aIndex: 2, bUnit: unit, bIndex: 0 })).toEqual({
      unit,
      element: 12,
    });
  });

  it('has nothing to say about a coarse unit', () => {
    // Kind 0 is atomic; a sphere unit indexes spheres, so a per-atom answer
    // would be about the wrong thing entirely.
    expect(atomAt({ unit: { ...unit, kind: 1 }, element: 7 })).toBeUndefined();
  });

  it('has nothing to say about a location that is neither', () => {
    expect(atomAt({})).toBeUndefined();
  });
});
