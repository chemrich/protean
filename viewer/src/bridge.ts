/** WebSocket client for the protean bridge: handshake, dispatch, auto-reconnect. */

export type Handler = (action: string, args: Record<string, unknown>) => Promise<unknown>;

const PROTOCOL_VERSION = 1;
const RECONNECT_MS = 1500;

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

  const setStatus = (connected: boolean) => {
    if (!status) return;
    if (superseded) {
      status.textContent = 'superseded — reload to take over';
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
        })
      );
    };

    ws.onmessage = async (ev: MessageEvent) => {
      const msg = JSON.parse(ev.data);
      if (msg.action === 'protean_pong') {
        setStatus(true);
        return;
      }
      if (msg.action === 'protean_superseded') {
        superseded = true;
        setStatus(false);
        return;
      }
      const { id, action, args } = msg;
      try {
        const result = await handle(action, args ?? {});
        ws.send(JSON.stringify({ id, ok: true, result: result ?? {} }));
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        console.error(`protean action '${action}' failed:`, e);
        ws.send(JSON.stringify({ id, ok: false, error }));
      }
    };

    ws.onclose = () => {
      if (current === ws) current = null;
      setStatus(false);
      if (!superseded) setTimeout(open, RECONNECT_MS);
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
