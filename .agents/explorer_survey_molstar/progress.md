# Progress — Mol* Shader Survey

- Last visited: 2026-08-28T10:11:30Z
- Status: Complete

## Tasks
- [x] Initialized DISPATCH.md, BRIEFING.md, progress.md
- [x] Located Mol* source code / vendored package / bundle in the repository (`viewer/node_modules/molstar/lib/`)
- [x] Analyzed Mol* material finishes, shader definitions, uniforms, and GLSL code (`chunks/apply-light-color`, `assign-material-color`, `light-frag-params`, `common-frag-params`, `base.js`, `material.js`)
- [x] Analyzed Mol* rendering pipeline: opaque pass, transparent pass, WBOIT/DPOIT, depth/normal buffers, postprocessing (`draw.js`, `postprocessing.js`, `renderer.js`)
- [x] Surveyed screen-space refraction and transmission techniques suitable for Mol* WebGL architecture (Snell refraction, Schlick Fresnel, dispersion, multi-tap roughness diffusion)
- [x] Examined Mol* build and compilation system (`viewer/vite.config.ts`, `viewer/package.json`, bundle output to `src/protean_mcp/static/`)
- [x] Synthesized technical design and wrote handoff.md
