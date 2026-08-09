import { beforeEach, describe, expect, it, vi } from 'vitest';

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
        representation: { addRepresentation: vi.fn(async () => {}) },
        tryCreateComponent: vi.fn(async (_ref: string, params: any) => {
          const ref = `component-${params.label}`;
          const cell = { transform: { ref }, state: { isHidden: false }, obj: { data: structure } };
          componentRefs.push({ cell });
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
        build: () => ({ delete: () => ({ commit: async () => {} }) }),
      },
    },
  };
}

describe('createDispatcher', () => {
  beforeEach(() => {
    // jsdom reports 'visible', so render actions take the un-pumped path.
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
