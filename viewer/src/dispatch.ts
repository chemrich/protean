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

declare global {
  interface Window {
    __protean?: {
      setTurbo?: (on: boolean) => void;
      pumpState?: () => { turbo: boolean; queued: number };
      plugin?: any;
    };
  }
}

/** Actions that need Mol*'s rAF-driven render loop to make progress. */
const RENDER_ACTIONS = new Set(['load_structure', 'clear', 'screenshot']);

/** Must stay below the bridge's own request timeout so our error wins the race. */
const HIDDEN_TIMEOUT_MS = 30_000;

export function isHidden(): boolean {
  return document.visibilityState !== 'visible';
}

/**
 * Waits for Mol* to actually commit its renderables.
 *
 * Building the state tree and drawing it are separate: `applyPreset` resolves as
 * soon as the representations are queued, and the animation loop drains that
 * queue over subsequent frames. Returning at that point would drop the pump
 * mid-flight and leave a hidden tab with a built-but-unrendered scene — a load
 * that reports success and screenshots blank.
 */
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

/**
 * Runs a render-dependent action, keeping the render loop alive if the tab is
 * hidden (see public/raf-pump.js) and surfacing a real error if it still stalls.
 *
 * Without this, a hidden tab turns every load into an unexplained bridge
 * timeout; the pump normally prevents that outright, and the deadline is the
 * backstop for when it can't (no pump installed, or a browser that clamps
 * MessageChannel too).
 */
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
    if (!RENDER_ACTIONS.has(action)) return handler(args);
    return withRenderPump(plugin, action, () => handler(args));
  };
}
