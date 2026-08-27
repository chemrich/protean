import { beforeEach, describe, expect, it, vi } from 'vitest';

import { mountViewMenu, readable } from './view-menu';

/** The channel `main.ts` hands the menu, with a scripted set of replies.
 *
 * Nothing here touches Mol\*: the menu asks the *server* for a view and the
 * server drives the viewer, which is the whole design and also what makes this
 * testable in jsdom.
 */
function fakeChannel(replies: Record<string, { ok: boolean; error?: string }>) {
  let publish: ((views: Array<{ name: string; kind: string }>) => void) | null = null;
  const invoke = vi.fn(async (view: string) => replies[view] ?? { ok: true });
  return {
    invoke,
    onViews(handler: (views: Array<{ name: string; kind: string }>) => void) {
      publish = handler;
    },
    offer(views: Array<{ name: string; kind: string }>) {
      publish?.(views);
    },
  };
}

const OFFERED = [
  { name: 'textbook', kind: 'draws' },
  { name: 'scaffold', kind: 'draws' },
  { name: 'illustrative', kind: 'styles' },
];

/** Word for word, from `_polarity_view`. It is long on purpose: the point of
 *  this control is that a paragraph like this reaches somebody. */
const SCAFFOLD_REFUSAL =
  "'scaffold' covers the regions a predicted model is unsure about, and this " +
  'structure is experimental: its `B_iso_or_equiv` column holds a ' +
  'crystallographic B-factor rather than a confidence score. ... Use ' +
  "'putty' to see which parts are least well ordered.";

/** Open the menu, the way a person does. It starts closed. */
function open() {
  button().click();
  expect(menu().hidden).toBe(false);
}

const item = (view: string) =>
  document.querySelector(`.view-menu-item[data-view="${view}"]`) as HTMLButtonElement;
const why = () => document.querySelector('.view-menu-why') as HTMLElement | null;
const menu = () => document.getElementById('view-menu') as HTMLElement;
const button = () => document.getElementById('view-menu-button') as HTMLButtonElement;

describe('the view menu', () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  it('names each view the way a person would say it', () => {
    expect(readable('hydrophobic-surface')).toBe('Hydrophobic surface');
    expect(readable('felt')).toBe('Felt');
  });

  it('offers what the server offers and nothing it knows itself', () => {
    const channel = fakeChannel({});
    mountViewMenu(channel as any);
    channel.offer(OFFERED);

    const names = [...document.querySelectorAll('.view-menu-item')].map(
      (el) => (el as HTMLElement).dataset.view
    );
    expect(names).toEqual(['textbook', 'scaffold', 'illustrative']);
  });

  // The defect this file exists for. `scaffold` refuses on a crystal structure
  // — correctly, and with the most useful paragraph protean writes — and the
  // menu used to put that paragraph in a `title` attribute on the *Views*
  // button, a tooltip on a control the person has already moved away from. So
  // what a click on Scaffold looked like was a click that did nothing, and
  // what it got called was "Scaffold doesn't show anything".
  it('says why a view was refused, where it was asked for', async () => {
    const channel = fakeChannel({ scaffold: { ok: false, error: SCAFFOLD_REFUSAL } });
    mountViewMenu(channel as any);
    channel.offer(OFFERED);
    open();

    item('scaffold').click();
    await vi.waitFor(() => expect(why()).not.toBeNull());

    expect(why()!.textContent).toBe(SCAFFOLD_REFUSAL);
    // Under the thing that was asked for, not somewhere else in the menu.
    expect(item('scaffold').nextElementSibling).toBe(why());
    // And still on screen: a menu that closes takes the explanation with it.
    expect(menu().hidden).toBe(false);
  });

  it('still carries the reason on the button, for a menu already put away', async () => {
    const channel = fakeChannel({ scaffold: { ok: false, error: SCAFFOLD_REFUSAL } });
    mountViewMenu(channel as any);
    channel.offer(OFFERED);

    item('scaffold').click();
    await vi.waitFor(() => expect(button().textContent).toBe('Views — refused'));
    expect(button().title).toBe(SCAFFOLD_REFUSAL);
  });

  it('says something even when the server refuses without saying why', async () => {
    const channel = fakeChannel({ scaffold: { ok: false } });
    mountViewMenu(channel as any);
    channel.offer(OFFERED);

    item('scaffold').click();
    await vi.waitFor(() => expect(why()).not.toBeNull());
    expect(why()!.textContent).toMatch(/said nothing about why/);
  });

  it('takes the refusal down when another view is asked for', async () => {
    const channel = fakeChannel({ scaffold: { ok: false, error: SCAFFOLD_REFUSAL } });
    mountViewMenu(channel as any);
    channel.offer(OFFERED);

    item('scaffold').click();
    await vi.waitFor(() => expect(why()).not.toBeNull());

    item('textbook').click();
    await vi.waitFor(() => expect(menu().hidden).toBe(true));
    // A stale refusal beside a view that just worked is worse than no refusal:
    // it reads as a report about the thing that succeeded.
    expect(why()).toBeNull();
    expect(button().textContent).toBe('Views');
  });

  it('takes the refusal down when a new catalogue arrives', async () => {
    const channel = fakeChannel({ scaffold: { ok: false, error: SCAFFOLD_REFUSAL } });
    mountViewMenu(channel as any);
    channel.offer(OFFERED);

    item('scaffold').click();
    await vi.waitFor(() => expect(why()).not.toBeNull());

    // A fresh catalogue is a new scene, and a refusal about the last one is no
    // longer news — it may not even be true any more, which is the case that
    // matters: loading a predicted model is exactly what makes `scaffold` work.
    channel.offer(OFFERED);
    expect(why()).toBeNull();
    expect(button().textContent).toBe('Views');
  });

  it('closes and leaves nothing behind when the view was drawn', async () => {
    const channel = fakeChannel({});
    mountViewMenu(channel as any);
    channel.offer(OFFERED);

    item('textbook').click();
    await vi.waitFor(() => expect(menu().hidden).toBe(true));
    expect(why()).toBeNull();
    expect(channel.invoke).toHaveBeenCalledWith('textbook');
  });

  it('files a kind it has no section for rather than hiding it', () => {
    const channel = fakeChannel({});
    mountViewMenu(channel as any);
    channel.offer([...OFFERED, { name: 'boil', kind: 'temporal' }]);

    expect(item('boil')).not.toBeNull();
  });
});
