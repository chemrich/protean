# BRIEFING — 2026-08-28T10:15:00Z

## Mission
Design WebGL Refraction GLSL shader mathematics, Snell distortion, Fresnel reflection, and chromatic dispersion for clear glass in Mol*.

## 🔒 My Identity
- Archetype: Explorer
- Roles: WebGL / GLSL Shader Investigation, Refraction Math Modeling
- Working directory: /Users/charlie/code/protean/.agents/explorer_m1_1
- Original parent: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Milestone: M1

## 🔒 Key Constraints
- Read-only investigation — do NOT implement directly in source code
- Produce precise GLSL math equations, uniforms, code snippets, and integration blueprint for implementers

## Current Parent
- Conversation ID: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Updated: 2026-08-28T10:15:00Z

## Investigation State
- **Explored paths**: `PROJECT.md`, `molstar/lib/mol-gl/shader/`, `molstar/lib/mol-canvas3d/passes/draw.js`, `viewer/src/painterly-shaders.ts`, `viewer/src/dispatch.ts`
- **Key findings**:
  - Snell refraction mathematically models air-to-glass interface ($\eta = 0.6667$, $n = 1.50$) with view-space depth normalization and aspect ratio correction.
  - Schlick Fresnel approximation with dielectric $F_0 = 0.04$ provides crisp grazing edge reflections ($F \to 1.0$) and high central transmission ($96\%$).
  - Chromatic dispersion using 3-tap spectral offsets ($\delta = 0.02$) delivers authentic optical color fringes.
  - Implementation blueprint specifies additions to `MATERIAL_FINISHES` in `dispatch.ts` and GLSL chunk in `viewer/src/refraction-shaders.ts`.
- **Unexplored areas**: None for M1.1 scope.

## Key Decisions Made
- GLSL ES 1.00 compliance strictly followed (`texture2D`, `gl_FragCoord.xy / uDrawingBufferSize`, exponential approximation for Fresnel).
- Complete mathematical formulation with full GLSL code chunk delivered in `handoff.md`.

## Artifact Index
- `/Users/charlie/code/protean/.agents/explorer_m1_1/handoff.md` — Final handoff report
