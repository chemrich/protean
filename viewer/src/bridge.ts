/** WebSocket client for the protean bridge: handshake, dispatch, auto-reconnect. */

export type Handler = (action: string, args: Record<string, unknown>) => Promise<unknown>;

const PROTOCOL_VERSION = 1;
const RECONNECT_MS = 1500;

export function connectBridge(handle: Handler): void {
  const url = `ws://${location.host}/ws`;
  const status = document.getElementById('status');

  // Tracked so the visibilitychange listener (registered once) can reach the
  // live socket across reconnects.
  let current: WebSocket | null = null;

  const setStatus = (connected: boolean) => {
    if (!status) return;
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
      setTimeout(open, RECONNECT_MS);
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
