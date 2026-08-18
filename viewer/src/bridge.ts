/** WebSocket client for the protean bridge: handshake, dispatch, auto-reconnect. */

export type Handler = (action: string, args: Record<string, unknown>) => Promise<unknown>;

const PROTOCOL_VERSION = 1;
const RECONNECT_MS = 1500;
/** The server ends a socket on a message past 64 MB; stay clear of it. */
export const MAX_REPLY_BYTES = 60 * 1024 * 1024;

/**
 * A reply the server cannot receive, turned into one that says so.
 *
 * aiohttp caps a message at 64 MB and ends the socket on a larger one, and a
 * capture can exceed that — `_MAX_SNAPSHOT_PIXELS` permits 120 MP, whose data
 * URI does not fit. Retrying such a reply is worse than dropping it: held in
 * the outbox it would be re-sent on every reconnect, killing each new socket
 * and re-uploading tens of megabytes, while the caller waits out its whole
 * budget regardless. An error is small, arrives, and names the fix.
 */
export function reportable(id: string, payload: string, limit = MAX_REPLY_BYTES): string {
  if (payload.length <= limit) return payload;
  return JSON.stringify({
    id,
    ok: false,
    error:
      `The reply is ${Math.round(payload.length / 1e6)} MB, beyond what the bridge ` +
      'can carry. Capture at a lower width or dpi.',
  });
}
// Roughly thirty seconds of retrying before the page says why it is not
// connecting. Retrying forever is what a page with a stale token used to do:
// the bridge mints a token per process, so restarting the server leaves an
// open tab refused on every attempt, and the only thing on screen was the word
// "disconnected". Silence for half a minute is a hiccup; silence for an hour
// is a bug the user cannot diagnose.
const MAX_ATTEMPTS = 20;

/** How long a click waits for the server before deciding it is not coming.
 *
 * Generous, because a view can mesh a molecular surface and that is genuinely
 * slow under software rendering — the measured worst case in this project is a
 * capture near two minutes. The point of the bound is not to police a slow
 * render but to end a wait that will never end. */
const INVOKE_TIMEOUT_MS = 180_000;

/** What the server says about a view the page asked for. */
export interface InvokeReply {
  ok: boolean;
  view?: string;
  error?: string;
}

/** What a control in the page may do: ask for a view, and be told the outcome. */
export interface PageChannel {
  invoke(view: string): Promise<InvokeReply>;
}

export function connectBridge(handle: Handler): PageChannel {
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
  // The server's protocol number when it differs from this bundle's, else null.
  let mismatched: number | null = null;
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
  // Sent on the current socket but not known to have left it: `send` only
  // queues into bufferedAmount, so a socket dying before the frame goes out
  // would otherwise lose the reply *and* drop its id from the next handshake's
  // claim — the server would then report a reloaded tab for a page that did
  // nothing of the sort. Held here until the socket closes cleanly.
  let unacked = new Map<string, string>();

  // Views this page has asked for and not yet been told the outcome of.
  //
  // These run the other way round from everything else here: the page asks and
  // the server answers, rather than the server asking. So they get their own
  // map — putting them in `running` would offer them to the server on the next
  // handshake as work this page owes an answer for, which is the opposite of
  // what they are.
  const invocations = new Map<string, (reply: InvokeReply) => void>();
  let asked = 0;

  /** Settle every waiting click, because its answer can no longer arrive.
   *
   * Deliberately not silent, and deliberately not claiming failure. The server
   * may well have applied the view — the reply is what was lost, not
   * necessarily the work — and a control that says "failed" about something
   * that happened is worse than one that says it does not know. */
  const abandonInvocations = (why: string) => {
    for (const [id, settle] of invocations) {
      invocations.delete(id);
      settle({ ok: false, error: why });
    }
  };

  const deliver = (id: string, payload: string) => {
    running.delete(id);
    if (current && current.readyState === WebSocket.OPEN) {
      current.send(payload);
      unacked.set(id, payload);
      return;
    }
    outbox.set(id, payload);
  };

  const flushOutbox = (ws: WebSocket) => {
    for (const [id, payload] of outbox) {
      ws.send(payload);
      unacked.set(id, payload);
      outbox.delete(id);
    }
  };

  // A socket that closed may or may not have delivered what was queued on it,
  // so everything unacked goes back to be sent again. A duplicate is harmless:
  // the server matches a reply to a pending id and ignores one it has already
  // resolved. Losing it is not.
  const rearm = () => {
    for (const [id, payload] of unacked) if (!outbox.has(id)) outbox.set(id, payload);
    unacked = new Map();
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
    if (mismatched !== null) {
      status.textContent =
        `protocol mismatch — this viewer speaks ${PROTOCOL_VERSION}, the server ` +
        `speaks ${mismatched}. One of them is from a different build; restart ` +
        'the protean MCP server and reload.';
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
          // `unacked` is deliberately not included: a handshake only ever
          // follows a close, and the close moves everything unacked back into
          // the outbox. Adding it looked prudent and was unreachable — the
          // mutation that deleted it failed nothing.
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
        // The server's protocol number, which until now neither side read.
        //
        // Honest about what this catches: **not** the incident that motivated
        // it. `PROTOCOL_VERSION` has been 1 since the first commit and did not
        // move when the handshake gained a required token, so the stale server
        // and the new page that could not talk to each other agreed on this
        // number exactly. It catches a *deliberate* break, from here on, and
        // costs nothing. What identifies a stale process is on the server side
        // — see `vintage.py`.
        mismatched = typeof msg.version === 'number' && msg.version !== PROTOCOL_VERSION
          ? msg.version
          : null;
        setStatus(true);
        flushOutbox(ws);
        return;
      }
      if (msg.action === 'protean_superseded') {
        superseded = true;
        setStatus(false);
        return;
      }
      if (msg.action === 'protean_invoked') {
        // The answer to a click, not an action to perform. Routed here rather
        // than handed to the dispatcher, which would report it as an unknown
        // action and leave the button waiting forever.
        const settle = invocations.get(msg.id);
        invocations.delete(msg.id);
        settle?.({ ok: !!msg.ok, view: msg.view, error: msg.error });
        return;
      }
      const { id, action, args } = msg;
      running.add(id);
      try {
        const result = await handle(action, args ?? {});
        deliver(id, reportable(id, JSON.stringify({ id, ok: true, result: result ?? {} })));
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        console.error(`protean action '${action}' failed:`, e);
        deliver(id, JSON.stringify({ id, ok: false, error }));
      }
    };

    ws.onclose = () => {
      if (current === ws) current = null;
      rearm();
      // A reply travelling the page's way has no outbox to wait in: the server
      // sends it on the socket it was asked over, and that socket is gone.
      abandonInvocations(
        'lost contact with protean before it said whether the view was applied'
      );
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

  return {
    invoke(view: string): Promise<InvokeReply> {
      if (!current || current.readyState !== WebSocket.OPEN) {
        return Promise.resolve({
          ok: false,
          error: 'not connected to protean, so nothing was asked for',
        });
      }
      const id = `invoke-${++asked}`;
      const settled = new Promise<InvokeReply>((resolve) => {
        invocations.set(id, resolve);
        // A socket that closes settles the wait; a server that simply never
        // answers does not, and that is not hypothetical. A page outlives the
        // server it was opened against — reconnect a viewer to a protean older
        // than this bundle and `protean_invoke` is an action it has never heard
        // of: it logs an unmatched message and replies to nobody. The button
        // would sit on "asking…" for the rest of the session, which reads as a
        // slow render rather than as a server that cannot do this.
        setTimeout(() => {
          if (!invocations.delete(id)) return;
          resolve({
            ok: false,
            error:
              'protean never answered. It may be older than this page — ' +
              'reload the tab, and if that does not help, restart the server.',
          });
        }, INVOKE_TIMEOUT_MS);
      });
      current.send(JSON.stringify({ action: 'protean_invoke', id, view }));
      return settled;
    },
  };
}
