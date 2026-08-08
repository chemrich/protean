/**
 * Boots the prebuilt Mol* viewer (loaded globally from molstar.js — bundling
 * molstar from source needs >4 GB RAM, the prebuilt bundle needs none) and
 * connects the protean bridge.
 */

import { connectBridge } from './bridge';
import { createDispatcher } from './dispatch';

declare const molstar: {
  Viewer: {
    create(target: string | HTMLElement, options?: Record<string, unknown>): Promise<{ plugin: any }>;
  };
};

async function init() {
  const viewer = await molstar.Viewer.create('app', {
    layoutIsExpanded: true,
    layoutShowControls: true,
    layoutShowRemoteState: false,
    layoutShowSequence: true,
    layoutShowLog: false,
    viewportShowExpand: true,
    viewportShowSelectionMode: true,
  });
  // Exposed for debugging and for the render pump's introspection hooks.
  (window as any).__protean = Object.assign((window as any).__protean ?? {}, {
    plugin: viewer.plugin,
  });
  connectBridge(createDispatcher(viewer.plugin));
}

init();
