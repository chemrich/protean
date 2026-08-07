# protean

Agent-native molecular visualization. An MCP server (Python) drives a [Mol*](https://molstar.org)-based viewer running in a browser tab — Claude does the driving, you watch and tweak.

**Status: Phase 1 (skeleton + bridge).** See [PLAN.md](PLAN.md) for the roadmap.

## Development setup

```bash
# Python server
uv sync
uv run pytest

# Viewer (build lands in src/protean_mcp/static)
cd viewer
npm install
npm run build
```

## Try it

Register with Claude Code:

```bash
claude mcp add protean -- uv run --directory /path/to/protean protean-mcp
```

Then: *"Open the protean viewer and show me 1UBQ."*

## Architecture

```
Claude ──stdio──▶ protean MCP server (FastMCP, port 9878)
                        │  WebSocket {id, action, args}
                        ▼
                  Mol* viewer (browser tab)
```

Port 9878 by default (`PROTEAN_PORT` to override), auto-increments on conflict, `protean_ping`/`protean_pong` handshake. Coexists with MCPymol (9876) and proteinblend-mcp (9877).
