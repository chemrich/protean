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

/**
 * A tab on the right edge that opens Mol*'s controls panel.
 *
 * Mol*'s layout collapses its *left* region to a 32px rail on its own, and
 * that is the affordance we want on both sides — a slice you can see, and one
 * click to the full panel. The right region has no such state: its options are
 * `full` and `hidden` and nothing else (`mol-plugin/layout.js` gives `left`
 * three choices and `right` two), so the slice on that side has to be ours.
 *
 * Deliberately protean's own DOM rather than a CSS override of Mol*'s panel:
 * squeezing `.msp-layout-right` to 32px would leave its contents rendering
 * inside a 32px box rather than collapsing, and we would be maintaining a
 * fight with the library's own styles at every version bump.
 */
function mountControlsTab(plugin: any): void {
  const tab = document.createElement('button');
  tab.id = 'panel-tab';
  document.body.appendChild(tab);

  let open = false;
  const draw = () => {
    tab.textContent = open ? '›' : '‹';
    tab.title = open ? 'Hide the Mol* controls' : 'Show the Mol* controls';
    // Sit against the panel's edge when it is open, so the tab stays the thing
    // you click to put it away again. Read rather than assumed: the panel's
    // width is a Mol* style, and reading it means a themed build still lines up.
    const panel = document.querySelector('.msp-layout-right') as HTMLElement | null;
    const width = open && panel ? panel.offsetWidth : 0;
    tab.style.right = `${width}px`;
    // The status pill is pinned to the corner both the panel and the sequence
    // strip occupy, and sat on top of each in turn — over the panel's header,
    // and in the strip's band, where a long chain's residues would run under
    // it. Measured off both rather than offset by a guessed constant, since
    // the strip's height depends on how far the sequence wraps.
    const status = document.getElementById('status');
    const strip = document.querySelector('.msp-sequence') as HTMLElement | null;
    if (status) {
      status.style.right = `${width + 8}px`;
      status.style.top = strip ? `${strip.getBoundingClientRect().bottom + 8}px` : '8px';
    }
  };

  // The sequence strip appears when a structure loads, long after this runs,
  // so its arrival has to move the pill as well as a panel toggle does.
  plugin.state.data.events.changed.subscribe(() => requestAnimationFrame(draw));

  tab.addEventListener('click', () => {
    open = !open;
    // updateProps, not setProps. Both write the state; only updateProps fires
    // `events.updated`, and the React layout redraws on that event alone
    // (mol-plugin/layout.js). With setProps the tab's chevron flipped, the
    // layout state said `full`, and the panel stayed 0px wide — a control that
    // reported success and did nothing.
    plugin.layout.updateProps({
      regionState: { ...plugin.layout.state.regionState, right: open ? 'full' : 'hidden' },
    });
    // After the layout has laid out: offsetWidth is 0 until the panel is in
    // the document, which would park the tab on top of it.
    requestAnimationFrame(draw);
  });

  draw();
}

async function init() {
  // Both side panels start collapsed, and neither is gone.
  //
  // They are Mol*'s controls for a person driving Mol* directly: the left one
  // loads structures, the right one edits the state tree. Used here they
  // change the picture and nothing else — the analysis half lives in the
  // Python process, so the model goes on answering, correctly, about the
  // molecule it loaded rather than the one now on screen. That is this
  // project's oldest failure mode, available as a button.
  //
  // Collapsed rather than removed, because a viewer you cannot inspect is its
  // own kind of opaque: when the picture looks wrong, the state tree is where
  // the answer is. The default is out of the way; the cost of reaching them is
  // one click, and the risk of *using* them is on whoever clicks.
  const viewer = await molstar.Viewer.create('app', {
    layoutIsExpanded: false,
    layoutShowControls: true,
    collapseLeftPanel: true,
    // Mol* reads this as "hidden", not "collapsed" — the right region has no
    // collapsed state. mountControlsTab() supplies the slice.
    collapseRightPanel: true,
    layoutShowRemoteState: false,
    // The sequence strip stays. A model selects by writing `resi 45-60`, so
    // this is not how selections get made here — but it is the one panel that
    // *reports* rather than acts, and reading along while a model works is the
    // whole reason a person has the viewer open. Its own clicks set a Mol*
    // focus the Python side never hears about, which costs a highlight and
    // changes no analysis.
    layoutShowSequence: true,
    layoutShowLog: false,
    // The viewport's own buttons: expand, settings, selection mode, animation,
    // trajectory transport. Each duplicates something protean drives through a
    // tool, and the trajectory transport in particular steps frames without
    // telling the analysis, which then reports on the frame it thinks is
    // current. Two stay: Mol*'s "Reset Zoom", which has no config gate and
    // moves only the camera, and the controls toggle, which is the left rail's
    // opposite number.
    viewportShowExpand: false,
    viewportShowControls: true,
    viewportShowSettings: false,
    viewportShowSelectionMode: false,
    viewportShowAnimation: false,
    viewportShowTrajectoryControls: false,
  });
  // Exposed for debugging and for the render pump's introspection hooks.
  (window as any).__protean = Object.assign((window as any).__protean ?? {}, {
    plugin: viewer.plugin,
  });
  mountControlsTab(viewer.plugin);
  connectBridge(createDispatcher(viewer.plugin));
}

init();
