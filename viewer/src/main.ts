/**
 * Boots the prebuilt Mol* viewer (loaded globally from molstar.js — bundling
 * molstar from source needs >4 GB RAM, the prebuilt bundle needs none) and
 * connects the protean bridge.
 */

import { connectBridge, type PageChannel } from './bridge';
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
    // The status pill rests in the opposite corner from Mol*'s own furniture —
    // the sequence strip owns the top, the axes widget the bottom left — but
    // the panel spans the full height, so the pill still has to step aside for
    // it. Only horizontally now, which is why the strip no longer figures.
    const status = document.getElementById('status');
    if (status) status.style.right = `${width + 8}px`;
  };

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

/**
 * One button, asking the server for one view.
 *
 * **It does not draw.** It sends the view's name and the server runs the same
 * `preset()` a model would call, which comes back over the ordinary action
 * channel. A style toggle applied here in the browser would look identical and
 * be safe; the views after this one create selections, and a selection made in
 * the browser is a handle the Python side has never heard of — so the model
 * could not refer to what the person is looking at. Routing everything through
 * the server makes the distinction unnecessary rather than subtle.
 *
 * The button reports what the server said, including refusals, because a
 * control that cannot report failure is a control that reports success.
 */
/** Section headings, and the order the kinds appear in.
 *
 * The kinds are not decoration. A `draws` view replaces what is on screen and
 * brings its own styling with it, so choosing one *after* a `styles` silently
 * discards the styling — while `styles` chosen after a `draws` keeps the
 * geometry and only changes the look. One flat list of nine equal items would
 * hide that, and the first surprise would be a dark cinematic ground vanishing
 * when someone asked to see B-factors.
 */
const VIEW_SECTIONS: ReadonlyArray<{ kind: string; heading: string }> = [
  { kind: 'draws', heading: 'What is drawn' },
  { kind: 'styles', heading: 'How it looks' },
  { kind: 'layers', heading: 'Over the top' },
];

/** A label a person reads, from the name the server uses. */
function readable(name: string): string {
  const words = name.replace(/-/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function mountViewMenu(channel: PageChannel): void {
  const button = document.createElement('button');
  button.id = 'view-menu-button';
  button.textContent = 'Views';
  button.title = 'Ask protean for a view';
  document.body.appendChild(button);

  const menu = document.createElement('div');
  menu.id = 'view-menu';
  menu.hidden = true;
  document.body.appendChild(menu);

  const close = () => {
    menu.hidden = true;
  };
  button.addEventListener('click', () => {
    menu.hidden = !menu.hidden;
  });
  // Clicking the molecule should put the menu away, the way a menu does.
  document.addEventListener('click', (event) => {
    if (event.target !== button && !menu.contains(event.target as Node)) close();
  });

  const ask = async (view: string, item: HTMLButtonElement) => {
    const label = item.textContent ?? view;
    item.disabled = true;
    item.textContent = 'asking…';
    const reply = await channel.invoke(view);
    item.disabled = false;
    item.textContent = label;
    // Reported on the button rather than swallowed: a control that cannot
    // report failure is a control that reports success.
    button.textContent = reply.ok ? 'Views' : 'Views — refused';
    button.title = reply.ok
      ? 'Ask protean for a view'
      : (reply.error ?? 'protean refused, and said nothing about why');
    if (reply.ok) close();
  };

  // Drawn from what the server offers, every handshake. Nothing here knows the
  // names, so this menu cannot come to disagree with the allowlist that gates
  // the channel — the drift this project keeps meeting.
  channel.onViews((views) => {
    menu.replaceChildren();
    for (const section of VIEW_SECTIONS) {
      const offered = views.filter((v) => v.kind === section.kind);
      if (!offered.length) continue;
      const heading = document.createElement('div');
      heading.className = 'view-menu-heading';
      heading.textContent = section.heading;
      menu.appendChild(heading);
      for (const view of offered) {
        const item = document.createElement('button');
        item.className = 'view-menu-item';
        // The name the server uses, kept as an attribute because the label is
        // not stable: it reads "asking…" for the length of a round trip, and
        // anything identifying the item by its text loses it exactly then.
        item.dataset.view = view.name;
        item.textContent = readable(view.name);
        item.addEventListener('click', () => void ask(view.name, item));
        menu.appendChild(item);
      }
    }
    // A kind the server grew that this page has no section for still has to
    // reach someone, or the menu quietly hides half of what is available.
    const unplaced = views.filter(
      (v) => !VIEW_SECTIONS.some((s) => s.kind === v.kind)
    );
    for (const view of unplaced) {
      const item = document.createElement('button');
      item.className = 'view-menu-item';
      item.dataset.view = view.name;
      item.textContent = readable(view.name);
      item.addEventListener('click', () => void ask(view.name, item));
      menu.appendChild(item);
    }
    button.hidden = menu.childElementCount === 0;
  });
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
  mountViewMenu(connectBridge(createDispatcher(viewer.plugin)));
}

init();
