/** WebSocket client for the protean bridge: handshake, dispatch, auto-reconnect. */

export type Handler = (action: string, args: Record<string, unknown>) => Promise<unknown>;

const PROTOCOL_VERSION = 1;
const RECONNECT_MS = 1500;
// Roughly thirty seconds of retrying before the page says why it is not
// connecting. Retrying forever is what a page with a stale token used to do:
// the bridge mints a token per process, so restarting the server leaves an
// open tab refused on every attempt, and the only thing on screen was the word
// "disconnected". Silence for half a minute is a hiccup; silence for an hour
// is a bug the user cannot diagnose.
const MAX_ATTEMPTS = 20;

export function connectBridge(handle: Handler): void {
  // The token the page was opened with, handed straight to the socket. The
  // server demands it, because a WebSocket is not subject to the same-origin
  // policy: without it any site the user is visiting could connect to
  // 127.0.0.1 on a guessable port, send `protean_ping`, displace this tab and
  // answer for it.
  const token = new URLSearchParams(location.search).get('token') ?? '';
  const url = `ws://${location.host}/ws?token=${encodeURIComponent(token)}`;
  const status = document.getElementById('status');

  // Tracked so the visibilitychange listener (registered once) can reach the
  // live socket across reconnects.
  let current: WebSocket | null = null;
  // Set when the server hands the bridge to a newer tab. Reconnecting after
  // that would take the connection straight back off the tab that now owns it,
  // and the two would trade it on every retry timer.
  let superseded = false;
  // Consecutive failed attempts, reset by a pong. A refused handshake is
  // indistinguishable from an unreachable server here: the WebSocket API
  // deliberately hides the HTTP status, so a 403 and a closed port arrive as
  // the same event. The message therefore names both causes rather than
  // guessing at one.
  let attempts = 0;
  let gaveUp = false;

  // Requests received and not yet answered, and replies that were ready while
  // no socket was open to carry them.
  //
  // A long action blocks this thread — Mol*'s image pass at figure resolution
  // renders in one synchronous call, tens of seconds of it — and the socket can
  // die in that window. Nothing here notices until the render returns, and by
  // then `ws.send` on the closed socket is a silent no-op: the reply is dropped,
  // the server waits out the request's whole budget, and reports a stall for
  // work that had actually succeeded. Observed against a 68 s journal-figure
  // capture, whose socket closed 62 s in.
  //
  // So a reply that cannot be sent is kept and delivered on the next
  // authenticated socket, and the ids are declared on the handshake so the
  // server can tell "still coming" from "that page is gone".
  const running = new Set<string>();
  const outbox = new Map<string, string>();

  const deliver = (id: string, payload: string) => {
    running.delete(id);
    if (current && current.readyState === WebSocket.OPEN) {
      current.send(payload);
      return;
    }
    outbox.set(id, payload);
  };

  const flushOutbox = (ws: WebSocket) => {
    for (const [id, payload] of outbox) {
      ws.send(payload);
      outbox.delete(id);
    }
  };

  const setStatus = (connected: boolean) => {
    if (!status) return;
    if (superseded) {
      status.textContent = 'superseded — reload to take over';
      status.classList.remove('connected');
      return;
    }
    if (gaveUp) {
      status.textContent = token
        ? 'not connected — the bridge is not running, or this tab’s handshake ' +
          'token is no longer the one it expects (a restart mints a new one). ' +
          'Call open_viewer for a fresh tab.'
        : 'not connected — this page was opened without a handshake token, so ' +
          'its socket is refused. Call open_viewer and use the URL it opens.';
      status.classList.remove('connected');
      return;
    }
    const hidden = document.visibilityState !== 'visible';
    status.textContent = connected ? (hidden ? 'connected (hidden)' : 'connected') : 'disconnected';
    status.classList.toggle('connected', connected);
  };

  const open = () => {
    const ws = new WebSocket(url);

    ws.onopen = () => {
      current = ws;
      // The server records visibility so it can explain a stalled action rather
      // than reporting a bare timeout.
      ws.send(
        JSON.stringify({
          action: 'protean_ping',
          version: PROTOCOL_VERSION,
          visibility: document.visibilityState,
          // What this page still owes an answer for. A reconnecting page that
          // claims nothing has lost the work — it reloaded — and the server
          // can say so at once instead of waiting out the timeout.
          inflight: [...running, ...outbox.keys()],
        })
      );
    };

    ws.onmessage = async (ev: MessageEvent) => {
      const msg = JSON.parse(ev.data);
      if (msg.action === 'protean_pong') {
        // A completed handshake, not merely an open socket: the server closes
        // an unauthenticated one without ever answering. Replies held while
        // the socket was down go now, for the same reason — an unauthenticated
        // socket would never carry them.
        attempts = 0;
        setStatus(true);
        flushOutbox(ws);
        return;
      }
      if (msg.action === 'protean_superseded') {
        superseded = true;
        setStatus(false);
        return;
      }
      const { id, action, args } = msg;
      running.add(id);
      try {
        const result = await handle(action, args ?? {});
        deliver(id, JSON.stringify({ id, ok: true, result: result ?? {} }));
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        console.error(`protean action '${action}' failed:`, e);
        deliver(id, JSON.stringify({ id, ok: false, error }));
      }
    };

    ws.onclose = () => {
      if (current === ws) current = null;
      // A page opened with no token at all cannot succeed on any attempt, so
      // it says so immediately rather than after thirty seconds of pretending
      // the server might come up.
      attempts += 1;
      if (attempts >= MAX_ATTEMPTS || !token) gaveUp = true;
      setStatus(false);
      if (!superseded && !gaveUp) setTimeout(open, RECONNECT_MS);
    };
    ws.onerror = () => ws.close();
  };

  document.addEventListener('visibilitychange', () => {
    setStatus(current !== null && current.readyState === WebSocket.OPEN);
    if (current?.readyState !== WebSocket.OPEN) return;
    current.send(
      JSON.stringify({ action: 'protean_visibility', visibility: document.visibilityState })
    );
  });

  open();
}
