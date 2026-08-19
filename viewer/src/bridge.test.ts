import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { connectBridge, reportable } from './bridge';

/** Matches RECONNECT_MS in bridge.ts. */
const RECONNECT_DELAY = 1500;

/** Stand-in for the browser WebSocket, capturing what the bridge sends. */
class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];

  readyState = FakeSocket.OPEN;
  sent: any[] = [];
  onopen?: () => void;
  onmessage?: (ev: { data: string }) => void;
  onclose?: () => void;
  onerror?: () => void;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }

  send(payload: string) {
    this.sent.push(JSON.parse(payload));
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  /** Deliver a server message. */
  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

const latest = () => FakeSocket.instances[FakeSocket.instances.length - 1];
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  FakeSocket.instances = [];
  document.body.innerHTML = '<div id="status"></div>';
  // Every test but the tokenless one describes a page opened by open_viewer,
  // which is the only way to get a page whose socket the server will accept.
  window.history.replaceState({}, '', '/?token=test-token');
  vi.stubGlobal('WebSocket', FakeSocket as any);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('handshake', () => {
  it('announces the protocol version and the tab visibility', () => {
    connectBridge(async () => ({}));
    latest().onopen!();
    expect(latest().sent[0]).toMatchObject({
      action: 'protean_ping',
      version: 1,
      visibility: 'visible',
    });
  });

  it('marks the status pill connected on pong', () => {
    connectBridge(async () => ({}));
    latest().onopen!();
    latest().receive({ action: 'protean_pong', version: 1 });
    expect(document.getElementById('status')!.textContent).toBe('connected');
  });
});

describe('request handling', () => {
  it('replies with the handler result, keyed by request id', async () => {
    connectBridge(async (action, args) => ({ echoed: action, args }));
    latest().onopen!();
    latest().receive({ id: 'abc', action: 'select', args: { name: 'sele' } });
    await flush();
    expect(latest().sent.at(-1)).toEqual({
      id: 'abc',
      ok: true,
      result: { echoed: 'select', args: { name: 'sele' } },
    });
  });

  it('reports a handler failure as a structured error, not a dropped request', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    connectBridge(async () => {
      throw new Error('kaboom');
    });
    latest().onopen!();
    latest().receive({ id: 'xyz', action: 'boom' });
    await flush();
    expect(latest().sent.at(-1)).toEqual({ id: 'xyz', ok: false, error: 'kaboom' });
  });
});

describe('reconnect behaviour', () => {
  it('reconnects after an unexpected close', () => {
    vi.useFakeTimers();
    connectBridge(async () => ({}));
    const first = latest();
    first.onopen!();
    expect(FakeSocket.instances).toHaveLength(1);

    first.close();
    vi.advanceTimersByTime(2000);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it('stops reconnecting once superseded by another tab', () => {
    // Without this the displaced tab reconnects on its timer, wins the
    // handshake back, and the two tabs trade the connection forever.
    vi.useFakeTimers();
    connectBridge(async () => ({}));
    const socket = latest();
    socket.onopen!();

    socket.receive({ action: 'protean_superseded' });
    socket.close();
    vi.advanceTimersByTime(10_000);

    expect(FakeSocket.instances).toHaveLength(1);
    expect(document.getElementById('status')!.textContent).toContain('superseded');
  });

  it('gives up eventually and says why, instead of retrying forever', () => {
    // The bridge mints a token per process, so restarting the server leaves an
    // open tab refused on every attempt. It used to retry on a 1.5s timer
    // indefinitely, showing only "disconnected" — a silent failure the user
    // cannot act on, and log noise on the server for as long as the tab lives.
    vi.useFakeTimers();
    connectBridge(async () => ({}));

    for (let i = 0; i < 30; i++) {
      latest().close();
      vi.advanceTimersByTime(2000);
    }

    expect(FakeSocket.instances.length).toBeLessThanOrEqual(20);
    const status = document.getElementById('status')!.textContent!;
    expect(status).toContain('not connected');
    expect(status).toContain('open_viewer');
  });

  it('a completed handshake resets the budget, so a long session is not capped', () => {
    // Otherwise a viewer left open across enough brief hiccups would stop
    // reconnecting while the bridge was running perfectly well.
    vi.useFakeTimers();
    connectBridge(async () => ({}));

    for (let round = 0; round < 3; round++) {
      for (let i = 0; i < 15; i++) {
        latest().close();
        vi.advanceTimersByTime(2000);
      }
      latest().onopen!();
      latest().receive({ action: 'protean_pong', version: 1 });
    }
    latest().close();
    vi.advanceTimersByTime(2000);

    expect(document.getElementById('status')!.textContent).not.toContain('not connected');
  });

  it('says the token is missing when the page was opened without one', () => {
    // Navigating to http://127.0.0.1:9878/ by hand or from a bookmark: the
    // page loads and looks alive, and its socket can never be accepted.
    vi.useFakeTimers();
    window.history.replaceState({}, '', '/');
    connectBridge(async () => ({}));

    latest().close();
    vi.advanceTimersByTime(10_000);

    expect(FakeSocket.instances).toHaveLength(1);
    expect(document.getElementById('status')!.textContent).toContain('without a handshake token');
  });
});

describe('visibility reporting', () => {
  it('pushes visibility changes to the server', () => {
    connectBridge(async () => ({}));
    latest().onopen!();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');

    document.dispatchEvent(new Event('visibilitychange'));

    expect(latest().sent.at(-1)).toEqual({
      action: 'protean_visibility',
      visibility: 'hidden',
    });
  });
});

describe('a reply that outlives its socket', () => {
  // The bug this exists for: a figure-sized capture blocks the main thread for
  // tens of seconds, the socket dies inside that window (observed at 62 s into
  // a 68 s capture, 1006 with no close frame), and the reply was then sent on
  // the dead socket — a silent no-op. The work had succeeded and the caller was
  // told the viewer stalled.
  //
  // Fake timers throughout, because the page's reconnect is scheduled by the
  // close itself: switching to them afterwards leaves that timer on the real
  // clock, where it never fires.

  const handshake = (socket: FakeSocket) => {
    socket.onopen!();
    socket.receive({ action: 'protean_pong', version: 1 });
  };

  const reconnect = async () => {
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAY);
    const socket = latest();
    handshake(socket);
    return socket;
  };

  beforeEach(() => vi.useFakeTimers());

  it('delivers a reply completed after the socket died on the next socket', async () => {
    let finish: (value: unknown) => void = () => {};
    connectBridge(() => new Promise((resolve) => (finish = resolve)));
    const first = latest();
    handshake(first);

    first.receive({ id: 'req-1', action: 'snapshot', args: {} });
    first.close(); // dies mid-render
    finish({ pixels: [4323, 1863] }); // the render finishes regardless
    await vi.advanceTimersByTimeAsync(0);

    // Nothing went out on the dead socket, which is where it used to vanish.
    expect(first.sent.some((m) => m.id === 'req-1')).toBe(false);

    const second = await reconnect();
    expect(second.sent.some((m) => m.id === 'req-1' && m.ok === true)).toBe(true);
  });

  it('declares what it still owes on the handshake', async () => {
    let finish: (value: unknown) => void = () => {};
    connectBridge(() => new Promise((resolve) => (finish = resolve)));
    const first = latest();
    handshake(first);
    first.receive({ id: 'req-1', action: 'snapshot', args: {} });
    first.close();
    finish({ ok: true });
    await vi.advanceTimersByTimeAsync(0);

    const second = await reconnect();
    // A page that claims nothing is one that reloaded, and the server ends the
    // request at once. This one still owes an answer, so it must say so.
    expect(second.sent[0].inflight).toContain('req-1');
  });

  it('re-arms a reply whose socket died before the frame left', async () => {
    connectBridge(async () => ({ done: true }));
    const first = latest();
    handshake(first);
    first.receive({ id: 'req-1', action: 'snapshot', args: {} });
    await vi.advanceTimersByTimeAsync(0);
    expect(first.sent.some((m) => m.id === 'req-1')).toBe(true);

    // `send` only queues into bufferedAmount. If the socket dies before the
    // frame is transmitted, dropping it would lose the reply *and* stop the
    // next handshake claiming it — so the server would report a reloaded tab
    // for a page that did nothing of the sort.
    first.close();
    const second = await reconnect();

    expect(second.sent[0].inflight).toContain('req-1');
  });
});

describe('a reply too large to send', () => {
  it('passes an ordinary reply through untouched', () => {
    const payload = JSON.stringify({ id: 'x', ok: true, result: {} });
    expect(reportable('x', payload, 1000)).toBe(payload);
  });

  it('replaces one past the limit with an error that fits', () => {
    // Retrying an oversized reply is worse than dropping it: held in the
    // outbox it would kill every new socket in turn, re-uploading tens of
    // megabytes, while the caller waits out its budget regardless.
    const payload = 'x'.repeat(2000);
    const replaced = JSON.parse(reportable('x', payload, 1000));

    expect(replaced).toMatchObject({ id: 'x', ok: false });
    expect(replaced.error).toContain('beyond what the bridge can carry');
    expect(reportable('x', payload, 1000).length).toBeLessThan(1000);
  });
});

describe('a view the page asks for', () => {
  it('sends the view name and nothing that looks like a tool call', async () => {
    const channel = connectBridge(async () => ({}));
    latest().onopen!();
    void channel.invoke('ghost-heart');
    await flush();

    const asked = latest().sent.at(-1);
    expect(asked).toMatchObject({ action: 'protean_invoke', view: 'ghost-heart' });
    // No args, no tool, no path. The page names a view; the server decides
    // what that means. Anything richer here is a channel to the tool surface
    // wearing a different name.
    expect(Object.keys(asked).sort()).toEqual(['action', 'id', 'view']);
  });

  it('settles the click with what the server said', async () => {
    const channel = connectBridge(async () => ({}));
    latest().onopen!();
    const settled = channel.invoke('ghost-heart');
    await flush();
    const { id } = latest().sent.at(-1);
    latest().receive({ action: 'protean_invoked', id, ok: true, view: 'ghost-heart' });

    await expect(settled).resolves.toEqual({
      ok: true,
      view: 'ghost-heart',
      error: undefined,
    });
  });

  it('carries a refusal back verbatim, rather than reporting success', async () => {
    const channel = connectBridge(async () => ({}));
    latest().onopen!();
    const settled = channel.invoke('nope');
    await flush();
    const { id } = latest().sent.at(-1);
    latest().receive({ action: 'protean_invoked', id, ok: false, error: 'Unknown view' });

    await expect(settled).resolves.toMatchObject({ ok: false, error: 'Unknown view' });
  });

  it('does not hand the reply to the dispatcher as if it were an action', async () => {
    // It would come back as "Unknown action: protean_invoked", the button would
    // wait forever, and the failure would look like a slow server.
    const handled: string[] = [];
    const channel = connectBridge(async (action) => {
      handled.push(action);
      return {};
    });
    latest().onopen!();
    const settled = channel.invoke('ghost-heart');
    await flush();
    const { id } = latest().sent.at(-1);
    latest().receive({ action: 'protean_invoked', id, ok: true });

    await expect(settled).resolves.toMatchObject({ ok: true });
    expect(handled).toEqual([]);
  });

  it('stops waiting when the socket dies, and does not claim it failed', async () => {
    // A reply travelling the page's way has no outbox to wait in: the server
    // sends it on the socket it was asked over. The view may well have been
    // applied, so the honest answer is that we do not know.
    const channel = connectBridge(async () => ({}));
    latest().onopen!();
    const settled = channel.invoke('ghost-heart');
    await flush();
    latest().close();

    const reply = await settled;
    expect(reply.ok).toBe(false);
    expect(reply.error).toMatch(/lost contact/);
  });

  it('refuses to ask at all when no socket is open', async () => {
    const channel = connectBridge(async () => ({}));
    // Never opened: no onopen, so `current` is null.
    await expect(channel.invoke('ghost-heart')).resolves.toMatchObject({
      ok: false,
    });
    expect(latest().sent).toEqual([]);
  });
});

describe('a server that never answers a click', () => {
  afterEach(() => vi.useRealTimers());

  it('gives up rather than leaving the button asking forever', async () => {
    // Not hypothetical: a page outlives the server it was opened against, and
    // a protean older than this bundle has never heard of `protean_invoke`. It
    // logs an unmatched message and replies to nobody, so without a bound the
    // button sits on "asking…" for the rest of the session — which reads as a
    // slow render rather than as a server that cannot do this at all. Found by
    // clicking the button against a two-day-old server, not by review.
    vi.useFakeTimers();
    const channel = connectBridge(async () => ({}));
    latest().onopen!();
    const settled = channel.invoke('ghost-heart');
    // The socket stays open and the server says nothing at all.
    await vi.advanceTimersByTimeAsync(180_000);

    const reply = await settled;
    expect(reply.ok).toBe(false);
    expect(reply.error).toMatch(/never answered/);
  });

  it('does not give up on a view that is merely slow', async () => {
    vi.useFakeTimers();
    const channel = connectBridge(async () => ({}));
    latest().onopen!();
    const settled = channel.invoke('ghost-heart');
    await vi.advanceTimersByTimeAsync(170_000);
    const { id } = latest().sent.at(-1);
    latest().receive({ action: 'protean_invoked', id, ok: true, view: 'ghost-heart' });

    await expect(settled).resolves.toMatchObject({ ok: true });
  });
});

describe('a server from a different build', () => {
  // Backlog 22. Honest about scope: this catches a *deliberate* protocol
  // break. It would not have caught the incident that motivated it, where a
  // stale server and a new page both said version 1 and could not talk. What
  // identifies a stale process is on the server side, in vintage.py.

  it('says so in the status pill when the numbers differ', () => {
    connectBridge(async () => ({}));
    latest().onopen!();

    latest().receive({ action: 'protean_pong', version: 99 });

    const pill = document.getElementById('status')!;
    expect(pill.textContent).toContain('protocol mismatch');
    expect(pill.textContent).toContain('99');
    expect(pill.classList.contains('connected')).toBe(false);
  });

  it('stays quiet when the numbers agree', () => {
    connectBridge(async () => ({}));
    latest().onopen!();

    latest().receive({ action: 'protean_pong', version: 1 });

    const pill = document.getElementById('status')!;
    expect(pill.textContent).not.toContain('mismatch');
    expect(pill.classList.contains('connected')).toBe(true);
  });

  it('stays quiet when the server says nothing about its version', () => {
    // An older server may omit the field entirely, and an absent number is
    // not a mismatch — treating it as one would put a red pill in front of
    // every user of a build that predates this check.
    connectBridge(async () => ({}));
    latest().onopen!();

    latest().receive({ action: 'protean_pong' });

    expect(document.getElementById('status')!.textContent).not.toContain('mismatch');
  });
});

describe('the views a page may ask for', () => {
  // Drawn from the server, never from a list in the bundle. A copy here would
  // drift from the allowlist that actually gates the channel, and a menu
  // offering a view the server refuses is worse than no menu.

  it('hands the server\u2019s catalogue to whoever is drawing the menu', () => {
    const seen: any[] = [];
    const channel = connectBridge(async () => ({}));
    channel.onViews((views) => seen.push(views));
    latest().onopen!();

    latest().receive({
      action: 'protean_pong',
      version: 1,
      views: [
        { name: 'putty', kind: 'draws' },
        { name: 'cinematic', kind: 'styles' },
      ],
    });

    expect(seen).toHaveLength(1);
    expect(seen[0].map((v: any) => v.name)).toEqual(['putty', 'cinematic']);
  });

  it('hands it over again on a reconnect, which may reach a different server', () => {
    vi.useFakeTimers();
    const seen: any[] = [];
    const channel = connectBridge(async () => ({}));
    channel.onViews((views) => seen.push(views));
    latest().onopen!();
    latest().receive({ action: 'protean_pong', version: 1, views: [{ name: 'putty', kind: 'draws' }] });

    latest().close();
    vi.advanceTimersByTime(RECONNECT_DELAY);
    latest().onopen!();
    latest().receive({ action: 'protean_pong', version: 1, views: [] });

    expect(seen).toHaveLength(2);
    expect(seen[1]).toEqual([]);
  });

  it('says nothing when a server too old to offer views answers', () => {
    // An absent list is not an empty one: a menu drawn from `[]` and a menu
    // never drawn look the same on screen, but only one of them is a claim.
    const seen: any[] = [];
    const channel = connectBridge(async () => ({}));
    channel.onViews((views) => seen.push(views));
    latest().onopen!();

    latest().receive({ action: 'protean_pong', version: 1 });

    expect(seen).toHaveLength(0);
  });
});
