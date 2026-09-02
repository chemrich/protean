# BRIEFING — 2026-08-28T23:03:30Z

## Mission
Investigate the Protean Python API and codebase to determine how to programmatically load PDB structures (1FHA, 5JQ3, 1F88, 1GFL), apply 3 aesthetics (Glass, Seaglass, Origami), configure high-fidelity rendering pipeline (lighting, camera, accumulation, snapshot capture), and identify all relevant modules, classes, and methods.

## 🔒 My Identity
- Archetype: explorer
- Roles: investigator, reporter
- Working directory: /Users/charlie/code/protean/.agents/explorer_survey_1
- Original parent: b7d3febd-c1c2-42ab-bd40-87f88257b971
- Milestone: mega_renders_investigation

## 🔒 Key Constraints
- Read-only investigation — do NOT implement or modify codebase
- Provide complete evidence chain with exact file paths, line numbers, and quotes

## Current Parent
- Conversation ID: b7d3febd-c1c2-42ab-bd40-87f88257b971
- Updated: not yet

## Investigation State
- **Explored paths**:
  - `src/protean_mcp/server.py` — Server RPC tools (`fetch_structure`, `show`, `preset`, `material`, `shading`, `lighting`, `background`, `effects`, `lens`, `path_trace`, `orient`, `focus`, `reset_view`, `snapshot`, `capabilities`)
  - `src/protean_mcp/fetch.py` — Structure download and caching (`fetch_structure_data`, `StructureData`)
  - `src/protean_mcp/connection.py` — WebSocket bridge (`ViewerBridge`)
  - `viewer/src/dispatch.ts` — Action routing and parameter handling
  - `viewer/src/refraction.ts` & `viewer/src/refraction-shaders.ts` — WebGL refraction and frosted transmission shaders
  - `tests/test_glass_differential.py` & `tests/test_origami_differential.py` — Test suites and rendering patterns
  - `tests/browser.py` & `tests/pixels.py` — Session lifecycle and pixel assertions
  - `docs/figures/make_figures.py` — End-to-end figure generation script architecture
- **Key findings**:
  - PDB loading via `fetch_structure` / `fetch_structure_data` with automatic RCSB download and mmCIF parsing.
  - Glass finish (`material(finish="glass")` with roughness=0.05, metalness=0) uses Snell refraction, Schlick Fresnel, and chromatic dispersion.
  - Seaglass preset (`preset("seaglass")` or `material(finish="seaglass")` with roughness=0.7, bumpiness=0.45, bump_freq=4.0) uses 12-tap Vogel spiral diffusion, 3-octave FBM surface normal perturbation, and seafoam green tint `#73b9a2`.
  - Origami aesthetic (`preset("origami")` or `shading(style="origami")` + `material(finish="origami")`) uses flat normal derivative creasing (`flatShaded: true`), square profile cartoons, paper tooth bumpiness (`bumpiness: 0.45, bump_freq: 4.5`), warm washi background `#f6f4eb`, and secondary-structure coloring.
  - Rendering pipeline: `orient()`, `focus()`, `reset_view()`, `lens()`, `lighting()`, `effects()`, `path_trace()`, and `snapshot()` capturing up to 600 DPI publication quality.
- **Unexplored areas**: None, full API mapped and verified against test harnesses and documentation.

## Key Decisions Made
- Mapped all 4 targets (1FHA, 5JQ3, 1F88, 1GFL) and biological characteristics.
- Documented both high-level preset calls and low-level fine-grained shader parameter overrides.
- Outlined end-to-end standalone script pattern based on `ViewerBridge`, `server.use_bridge()`, and headless Chrome.

## Artifact Index
- handoff.md — Final comprehensive investigation report
- progress.md — Liveness & progress tracking
- DISPATCH.md — Task dispatch log
