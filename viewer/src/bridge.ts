/** WebSocket client for the protean bridge: handshake, dispatch, auto-reconnect. */

export type Handler = (action: string, args: Record<string, unknown>) => Promise<unknown>;

const PROTOCOL_VERSION = 1;
const RECONNECT_MS = 1500;

export function connectBridge(handle: Handler): void {
  const url = `ws://${location.host}/ws`;
  const status = document.getElementById('status');

  const setStatus = (connected: boolean) => {
    if (!status) return;
    status.textContent = connected ? 'connected' : 'disconnected';
    status.classList.toggle('connected', connected);
  };

  const open = () => {
    const ws = new WebSocket(url);

    ws.onopen = () => {
      ws.send(JSON.stringify({ action: 'protean_ping', version: PROTOCOL_VERSION }));
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
      setStatus(false);
      setTimeout(open, RECONNECT_MS);
    };
    ws.onerror = () => ws.close();
  };

  open();
}
