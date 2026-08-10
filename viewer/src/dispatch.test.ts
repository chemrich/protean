import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { colorParams, createDispatcher, lociOf, summarise } from './dispatch';

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
          types: [['cartoon'], ['spacefill']],
          get: (type: string) => ({
            getParams: () =>
              type === 'spacefill'
                ? { sizeFactor: 1, alpha: 1 }
                : { alpha: 1, aspectRatio: 1 },
          }),
        },
        themes: { colorThemeRegistry: { types: [['chain-id'], ['element-symbol']] } },
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
                  type: { name: params.type, params: { ...(params.typeParams ?? {}) } },
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
  const screenshotValues: any = { transparent: false, format: { name: 'png' } };
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
        values: { next: vi.fn((v: any) => Object.assign(screenshotValues, v)) },
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
    ).rejects.toThrow(/Unknown representation 'cartoonn'\. Available: cartoon, spacefill/);
  });

  it('rejects an unknown colour theme', async () => {
    const dispatch = createDispatcher(fakePlugin());
    await expect(
      dispatch('show', {
        name: 's', expression: '(sel.atom.all)', representation: 'cartoon', color: 'nope',
      })
    ).rejects.toThrow(/Unknown colour theme 'nope'/);
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
      representations: ['cartoon', 'spacefill'],
      color_themes: ['chain-id', 'element-symbol'],
      // Named styles are reported for the same reason the registries are: a
      // model can only choose from what it can see at the point of use.
      lighting_rigs: ['flat', 'rim', 'ring', 'standard', 'studio', 'three-point'],
      shading_styles: ['cel', 'flat', 'normal', 'xray', 'xray-inverted'],
      gradients: ['off', 'horizontal', 'radial'],
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

  it('still skips the pump for actions that draw nothing', async () => {
    const { plugin, raf } = withCommitLoop(5);
    const setTurbo = vi.fn();
    window.__protean = { setTurbo };

    await createDispatcher(plugin)('list_selections', {});

    expect(setTurbo).not.toHaveBeenCalled();
    expect(raf).not.toHaveBeenCalled();
  });
});
