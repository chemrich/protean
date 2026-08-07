/** Maps bridge actions to Mol* plugin-state transactions.
 *
 * `plugin` is the PluginUIContext of the prebuilt Mol* viewer. Typed as `any`
 * because molstar is loaded as a prebuilt global rather than bundled (see
 * main.ts); Phase 2 can layer type-only imports on top if wanted.
 */

import type { Handler } from './bridge';

interface LoadStructureArgs {
  name: string;
  format: 'pdb' | 'mmcif';
  data: string;
}

export function createDispatcher(plugin: any): Handler {
  const handlers: Record<string, (args: any) => Promise<unknown>> = {
    async load_structure({ name, format, data }: LoadStructureArgs) {
      const raw = await plugin.builders.data.rawData({ data, label: name });
      const trajectory = await plugin.builders.structure.parseTrajectory(
        raw,
        format === 'pdb' ? 'pdb' : 'mmcif'
      );
      await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default');
      return { loaded: name };
    },

    async clear() {
      await plugin.clear();
      return {};
    },

    async screenshot() {
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
  };

  return async (action, args) => {
    const handler = handlers[action];
    if (!handler) throw new Error(`Unknown action: ${action}`);
    return handler(args);
  };
}
