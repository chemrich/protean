# BRIEFING — 2026-08-28T10:11:35Z

## Mission
Survey Mol* shader and rendering architecture for glass & seaglass material implementation in Protean.

## 🔒 My Identity
- Archetype: explorer
- Roles: Read-only investigation, architecture analysis, synthesis
- Working directory: /Users/charlie/code/protean/.agents/explorer_survey_molstar
- Original parent: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Milestone: Mol* Shader Survey

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Report all observations, logic chain, caveats, conclusion, verification method in handoff.md

## Current Parent
- Conversation ID: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Updated: 2026-08-28T10:11:35Z

## Investigation State
- **Explored paths**:
  - `viewer/package.json`, `viewer/vite.config.ts`, `viewer/src/main.ts`, `viewer/src/dispatch.ts`, `viewer/src/painterly.ts`
  - `viewer/node_modules/molstar/lib/` (shaders, passes, renderer, geometry base, material util)
  - `src/protean_mcp/server.py` (`material`, `capabilities`, `preset`, `_PRESETS`, `_VIEWS`)
  - `tests/test_render_differential.py`, `viewer/src/dispatch.test.ts`
- **Key findings**:
  - Mol* pre-compiled ESM is imported by Vite from `viewer/node_modules/molstar/lib/` and bundled to `src/protean_mcp/static/assets/`.
  - Material finishes are registered in `viewer/src/dispatch.ts:MATERIAL_FINISHES` and mapped to representation parameters.
  - Transparent pass occurs after opaque pass in `DrawPass`, providing access to the rendered opaque scene texture for screen-space refraction.
  - `glass` requires sharp screen-space Snell refraction offset, Fresnel reflection/transmission mixing, and GGX specular highlight.
  - `seaglass` requires high microfacet roughness, procedural Mikkelsen bump normal perturbation (`fbm`), multi-tap diffusion blur of background, and seafoam green/blue tinting.
- **Unexplored areas**: None for this survey scope.

## Key Decisions Made
- Fully documented 5-component report in `handoff.md` covering architecture, shader chunks, rendering passes, optical math, and build/test commands.

## Artifact Index
- /Users/charlie/code/protean/.agents/explorer_survey_molstar/DISPATCH.md — Dispatch prompt
- /Users/charlie/code/protean/.agents/explorer_survey_molstar/progress.md — Progress log
- /Users/charlie/code/protean/.agents/explorer_survey_molstar/BRIEFING.md — Briefing log
- /Users/charlie/code/protean/.agents/explorer_survey_molstar/handoff.md — Final survey report
