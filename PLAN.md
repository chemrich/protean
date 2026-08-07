# Protean — Implementation Plan

**Goal:** replace and exceed PyMOL as an agent-native molecular visualization and analysis platform.

**Foundation decisions** (settled 2026-08-07):

- Rendering: **Mol\*** (molstar) — mature WebGL/WebGPU engine, powers RCSB, handles cartoons/surfaces/volumes/trajectories out of the box.
- Interface model: **agent-native first** — the MCP server + Python API is the product; the viewer window is a display surface Claude drives and the human watches/tweaks.
- Scope: core viz + selections, analysis layers, publication rendering, MD trajectories, animations.

## Architecture

Two components, same pattern proven in MCPymol and proteinblend-mcp:

```
Claude (MCP client)
   │  stdio
   ▼
protean MCP server (Python, FastMCP, uv/uvx)
   │  WebSocket, JSON {action, args, kwargs}
   ▼
protean viewer (TypeScript, Mol* embedded in a local web app)
```

**Viewer** — a Vite + TypeScript app embedding the Mol* plugin. Runs as a local page the server launches (browser tab first; Tauri shell later if a native app is wanted). It exposes a command dispatcher over WebSocket: each action maps to Mol* plugin-state transactions. Includes a `protean_ping`/`protean_pong` handshake so the server can identify it during port scanning (same coexistence trick as proteinblend-mcp).

**Server** — Python ≥3.13, FastMCP, launchable via `uvx protean-mcp`. Owns everything that isn't rendering: structure fetching/caching, selection-language translation, analysis pipelines (MMseqs2 conservation, contacts, electrostatics), MD trajectory handling (MDAnalysis), and movie encoding (ffmpeg). Heavy data flows to the viewer as compact frames; the viewer never does science.

**Port strategy** — default **9878** (MCPymol: 9876, proteinblend: 9877), `PROTEAN_PORT` env override, auto-increment scan up to 10 ports with handshake verification.

**Why this beats PyMOL:** every capability is a typed MCP tool with structured returns (not screen-scraped stdout); state is a serializable snapshot graph (Mol* state) so sessions are diffable and replayable; rendering quality and trajectory support exceed PyMOL's defaults; and the selection layer accepts PyMOL syntax, so migration is free.

## Repo layout

```
protean/
├── pyproject.toml            # uv workspace root, protean-mcp package
├── src/protean_mcp/
│   ├── server.py             # FastMCP tool definitions
│   ├── connection.py         # WS client, port scan, handshake
│   ├── selections.py         # PyMOL-syntax → MolQL translator
│   ├── analysis/             # conservation, contacts, electrostatics, superposition
│   ├── trajectory.py         # MDAnalysis loading + frame streaming
│   ├── movie.py              # keyframes, interpolation, ffmpeg encode
│   └── presets/              # YAML scene recipes (loader ported from proteinblend)
├── viewer/                   # Vite + TS + molstar
│   ├── src/main.ts           # plugin boot, WS server bridge
│   ├── src/dispatch.ts       # action → Mol* state transaction
│   └── src/render.ts         # snapshot/raytrace/export paths
└── tests/                    # pytest (server) + vitest (dispatch), mock WS peer
```

## Phases

### Phase 1 — Skeleton and bridge (v0.0.x)

Scaffold the uv workspace and viewer app. WebSocket bridge with handshake, port scan, reconnect. First three tools: `open_viewer`, `fetch_structure` (PDB + AlphaFold DB + local file), `screenshot`. Mirror MCPymol's test approach with a mock viewer peer. *Exit: Claude fetches 1UBQ and returns a PNG.*

### Phase 2 — Core viz + selections (v0.1)

Representations (cartoon, surface, ball-and-stick, sticks, spacefill, ribbon), per-selection apply/remove. Color schemes: chain, secondary structure, element, spectrum, B-factor, pLDDT. Camera control (orient, zoom-to-selection, turntable). Measurements: distances, angles, dihedrals, labels. **Selection translator:** accept PyMOL algebra (`chain A and resi 50-60 and not solvent`, `byres`, `within`) and compile to MolQL — the single most important migration feature. Sessions: save/load Mol* state snapshots as `.protean` files. *Exit: reproduce a typical published PyMOL figure from a prompt.*

### Phase 3 — Analysis layers (v0.2)

Superposition/alignment (align, cealign-equivalent via biotite) with RMSD reporting. Contacts and interfaces (H-bonds, salt bridges, buried surface area). Conservation: port the MMseqs2 + Shannon entropy pipeline from MCPymol `conservation_view`, including the sequence-keyed cache and `force_refresh`. Electrostatics via pdb2pqr + APBS with surface potential mapping. Structured returns for everything (JSON tables, not prose) so Claude can reason over results. *Exit: "color this dimer's interface by conservation and list the conserved contacts" works in one exchange.*

### Phase 4 — Publication rendering (v0.3)

High-resolution snapshot pipeline (Mol* ray-tracing pass, transparent backgrounds, outline/occlusion styles). Preset system: YAML recipes (publication-cartoon, active-site, ghost-surface — a chance to do the ghost-heart transparency properly, with correct per-selection scoping this time). Optional Blender bridge: export scene to proteinblend-mcp for cinematic renders rather than duplicating that work here. *Exit: journal-ready TIFF/PNG at arbitrary DPI from one tool call.*

### Phase 5 — Trajectories + animation (v0.4)

MDAnalysis-backed loading (XTC/DCD/TRR + topology), frame streaming to the viewer, playback controls, per-frame measurements (RMSD/RMSF/distance timeseries as structured data). Animation timeline: keyframed camera + representation states with smooth interpolation; ffmpeg encoding to MP4/GIF. *Exit: load a trajectory, plot RMSF, render a 10-second annotated movie.*

### Phase 6 — Polish and publish (v1.0)

README (installation for Claude Code / Desktop / uvx, tool tables, example prompts — same structure as MCPymol/proteinblend). CHANGELOG, tagged releases, PyPI publish. Attribution: Mol*, MDAnalysis, biotite, APBS, FastMCP. Benchmark doc: side-by-side PyMOL vs protean on 5 common tasks.

## Reuse from existing projects

| From | What |
|------|------|
| MCPymol | conservation pipeline + cache, test patterns, README/release conventions, tool naming |
| proteinblend-mcp | port-scan + handshake code, YAML preset loader, addon dispatch pattern (adapted to TS) |

## Decisions (2026-08-07)

1. **Viewer shell:** browser tab for now; Tauri revisit post-v1.0 if wanted.
2. **PyMOL selection parity:** ~25 most-used keywords first; full grammar is the ultimate goal. Design the translator so the grammar can grow without rework (proper parser, not regex).
3. **Electrostatics:** optional extra — `protean-mcp[apbs]`.
4. **MCPymol relationship:** coexist for now; may subsume MCPymol long-term but not near-term. Keep tool names and conventions compatible so a later merge is low-friction.
