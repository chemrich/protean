import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { connectBridge } from './bridge';

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
