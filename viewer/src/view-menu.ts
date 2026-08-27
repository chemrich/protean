/**
 * The Views menu: protean's one control that a person, rather than a model,
 * drives.
 *
 * Its own module so that it can be tested. `main.ts` boots Mol\* at import
 * time, so anything living there is unreachable from a suite that runs in
 * jsdom — and this is the piece most worth testing, because it is the only
 * place a refusal has to survive the trip from a tool's docstring to
 * somebody's eyes.
 *
 * **It does not draw.** It sends the view's name and the server runs the same
 * `preset()` a model would call, which comes back over the ordinary action
 * channel. A style toggle applied here in the browser would look identical and
 * be safe; the views after this one create selections, and a selection made in
 * the browser is a handle the Python side has never heard of — so the model
 * could not refer to what the person is looking at. Routing everything through
 * the server makes the distinction unnecessary rather than subtle.
 */

import type { PageChannel } from './bridge';

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
export function readable(name: string): string {
  const words = name.replace(/-/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function mountViewMenu(channel: PageChannel): void {
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

  /** Clear any refusal currently on show. */
  const forget = () => {
    for (const why of menu.querySelectorAll('.view-menu-why')) why.remove();
    button.textContent = 'Views';
    button.title = 'Ask protean for a view';
  };

  const ask = async (view: string, item: HTMLButtonElement) => {
    const label = item.textContent ?? view;
    forget();
    item.disabled = true;
    item.textContent = 'asking…';
    const reply = await channel.invoke(view);
    item.disabled = false;
    item.textContent = label;
    if (reply.ok) {
      close();
      return;
    }
    // Said where it was asked, and in full.
    //
    // This used to put the reason in a `title` on the Views button and change
    // that button's text to "Views — refused". A tooltip on a control the
    // person has already moved away from is not a report: what Charlie saw
    // when they clicked Scaffold on a crystal structure was a click that did
    // nothing, and what they said was *"Scaffold doesn't show anything."*
    //
    // It does something. It refuses, and the paragraph it refuses with is one
    // of the more useful things protean writes — that pLDDT and the B-factor
    // are the same mmCIF column read with opposite polarity, that there is
    // nothing to cover because every atom here was observed, and that `putty`
    // is the view that answers the question actually being asked.
    const why = document.createElement('div');
    why.className = 'view-menu-why';
    why.setAttribute('role', 'status');
    why.textContent = reply.error ?? 'protean refused, and said nothing about why';
    item.after(why);
    // The button still carries it, for the case where the menu has been put
    // away before the reply lands.
    button.textContent = 'Views — refused';
    button.title = why.textContent;
  };

  // Drawn from what the server offers, every handshake. Nothing here knows the
  // names, so this menu cannot come to disagree with the allowlist that gates
  // the channel — the drift this project keeps meeting.
  channel.onViews((views) => {
    // A fresh catalogue is a new scene, so whatever was refused about the last
    // one is no longer news.
    menu.replaceChildren();
    button.textContent = 'Views';
    button.title = 'Ask protean for a view';
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
