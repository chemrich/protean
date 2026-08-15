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
  // The side panels are gone deliberately, and not only for the room.
  //
  // The left one loads structures by hand and the right one edits the state
  // tree — delete a component, swap a representation. Either changes the
  // picture and nothing else: the analysis half lives in the Python process,
  // so the model goes on answering, correctly, about the molecule it loaded
  // and no longer about the one on screen. That is this project's oldest
  // failure mode, offered as a button.
  //
  // Anything that changes what is loaded goes through a tool call instead,
  // where both halves see it.
  const viewer = await molstar.Viewer.create('app', {
    layoutIsExpanded: false,
    layoutShowControls: false,
    layoutShowLeftPanel: false,
    layoutShowRemoteState: false,
    // The residue strip is a navigation control for a person picking residues
    // by eye. A model selects with `select("resi 45-60")`, and the strip's own
    // clicks set a focus the Python side never hears about.
    layoutShowSequence: false,
    layoutShowLog: false,
    // The viewport's own buttons: expand, settings, selection mode, animation,
    // trajectory transport. Each duplicates something protean drives through a
    // tool, and the trajectory transport in particular steps frames without
    // telling the analysis, which then reports on the frame it thinks is
    // current. Mol*'s "Reset Zoom" has no config gate and stays; it moves the
    // camera and nothing else, which is the one thing a watcher wants and
    // cannot break.
    viewportShowExpand: false,
    viewportShowControls: false,
    viewportShowSettings: false,
    viewportShowSelectionMode: false,
    viewportShowAnimation: false,
    viewportShowTrajectoryControls: false,
  });
  // Exposed for debugging and for the render pump's introspection hooks.
  (window as any).__protean = Object.assign((window as any).__protean ?? {}, {
    plugin: viewer.plugin,
  });
  connectBridge(createDispatcher(viewer.plugin));
}

init();
