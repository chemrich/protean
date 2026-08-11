# protean

Agent-native molecular visualization. An MCP server (Python) drives a
[Mol\*](https://molstar.org)-based viewer in a browser tab — a model does the
driving, you watch and tweak.

protean is built for a model to use, not for a human at a REPL. Selections are
named handles that analysis returns and display tools consume; styles are enums
a model can see in the tool schema rather than strings it has to guess; and
every reply is structured data, read back off the viewer rather than echoed
from the request.

**Status: Phases 1–5 complete.** Selections, analysis, publication rendering
and trajectories all work end to end. See [PLAN.md](PLAN.md) for the roadmap
and the decisions behind it.

## What it can do

```
"Load 1CA2 and show me the catalytic zinc site"
"Colour this dimer's interface by conservation and list the conserved contacts"
"Give me a publication figure of that, double column at 600 dpi"
"Load this trajectory, tell me which loops move, and render a turntable"
```

- **Selections** — PyMOL syntax for leaf predicates, composed through named
  handles: `select`, `combine`, `near`, `invert`.
- **Analysis** — interfaces and buried area, superposition with RMSD,
  conservation from an MMseqs2 alignment, electrostatics (screened Coulomb by
  default, APBS when available).
- **Rendering** — representations, colour themes, lighting rigs, screen-space
  effects, PBR materials, path tracing, and `snapshot()` at a real physical
  size with the DPI written into the file.
- **Trajectories** — XTC/TRR/DCD/NetCDF, frame stepping, RMSF and RMSD series,
  turntables, keyframed camera moves, and ffmpeg encoding.

## Install

protean needs Python 3.11+ and a browser. ffmpeg is optional and only needed
for `movie()`; APBS is optional and only for `electrostatics(method="apbs")`.

### Claude Code

```bash
claude mcp add protean -- uvx protean-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "protean": { "command": "uvx", "args": ["protean-mcp"] }
  }
}
```

### From source

```bash
git clone https://github.com/chemrich/protean
cd protean
uv sync
cd viewer && npm install && npm run build   # the viewer is a build artifact
uv run protean-mcp
```

The viewer build lands in `src/protean_mcp/static/` and is packaged into the
wheel. Without it the server starts and `open_viewer` reports that the app is
not built.

## Tools

49 tools. `capabilities()` reports the live lists — representations, colour
themes, lighting rigs, shading styles, material finishes, gradients, presets,
path-trace quality, and whether ffmpeg is installed.

| Area | Tools |
|---|---|
| Session | `open_viewer`, `fetch_structure`, `clear_viewer`, `save_session`, `load_session`, `capabilities` |
| Selections | `select`, `combine`, `near`, `invert`, `list_selections`, `remove` |
| Display | `show`, `hide`, `unhide`, `color`, `opacity`, `label`, `measure` |
| Camera | `focus`, `orient`, `reset_view`, `spin`, `keyframe`, `list_keyframes` |
| Analysis | `interface`, `superpose`, `conservation`, `electrostatics` |
| Scalar colouring | `color_by_potential`, `color_by_conservation`, `color_by_rmsf` |
| Style | `background`, `lighting`, `effects`, `shading`, `material`, `path_trace`, `preset` |
| Capture | `screenshot`, `snapshot`, `turntable`, `record_trajectory`, `record_timeline`, `movie` |
| Trajectories | `load_trajectory`, `frame`, `rmsf`, `rmsd_series` |

## How it compares to PyMOL

[docs/benchmark.md](docs/benchmark.md) runs five common tasks through both, with
the real output of each. protean loses two of them — PyMOL's `cealign` finds a
rigid core that protean's sequence-based `superpose` cannot, and PyMOL's
selection grammar has no gaps where protean's has several. protean wins where
the answer needs to arrive as structured data a model can compose.

## Development

```bash
uv sync
uv run pytest                      # fast: no browser
cd viewer && npm test              # viewer unit tests
```

The browser suite is opt-in, because it drives a real Chrome and fetches from
RCSB:

```bash
npm run build --prefix viewer      # the suite serves the built app
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_render_differential.py
```

Path tracing needs a real GPU and is gated separately — under software
rendering its WebGL extensions are present and a single capture never
finishes:

```bash
PROTEAN_DIFFERENTIAL=1 PROTEAN_PATHTRACE=1 \
  PROTEAN_CHROME_FLAGS="--headless=new --no-sandbox --window-size=800,600" \
  uv run pytest tests/test_render_differential.py
```

Other gates: `PROTEAN_MSA_LIVE=1` for the live conservation alignment,
`PROTEAN_APBS=1` for APBS.

### How this codebase is tested

The dominant failure mode here is code that reports success and draws nothing,
or draws the wrong thing confidently. Mol\* accepts bad input without
complaint. So:

- Rendering is verified by **reading pixels**, not return values or file sizes —
  byte size cannot tell a transparent background from a black one.
- Replies **read state back** off the canvas rather than echoing arguments.
- New guards are checked by **deliberately breaking them** and confirming the
  test fails. Several tests in this repo exist because that exercise showed the
  first version passed against the bug it was written for.

## Built on

- [Mol\*](https://molstar.org) — the viewer, rendering and path tracer
  ([paper](https://doi.org/10.1093/nar/gkab314), MIT)
- [biotite](https://www.biotite-python.org) — structure and trajectory parsing,
  superposition ([paper](https://doi.org/10.1186/s12859-018-2367-z), BSD-3)
- [FastMCP](https://github.com/jlowin/fastmcp) — the MCP server (Apache-2.0)
- [Pillow](https://python-pillow.org) — TIFF/JPEG output and DPI metadata
- [pdb2pqr](https://www.poissonboltzmann.org) and
  [APBS](https://www.poissonboltzmann.org) — optional electrostatics
  ([paper](https://doi.org/10.1002/pro.3280))
- [ColabFold](https://github.com/sokrypton/ColabFold) — the MMseqs2 API behind
  `conservation()`
- [ffmpeg](https://ffmpeg.org) — optional, for `movie()`

## Licence

MIT. See [LICENSE](LICENSE).
