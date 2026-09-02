## 2026-08-28T10:12:01Z

You are an Explorer designing the Mol* Render Pass Integration and Material Finish definitions in `viewer/src/`.

Your working directory is: /Users/charlie/code/protean/.agents/explorer_m1_3
Read the following files before starting:
- /Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md
- /Users/charlie/code/protean/PROJECT.md

Your task:
1. Investigate how the opaque scene color texture (`colorTarget.texture` / `tColor`) is accessed during transparent / refractive rendering passes in Mol* or via custom post-processing/composition passes in `viewer/src/`.
2. Specify the exact entries for `glass` and `seaglass` in `MATERIAL_FINISHES` in `viewer/src/dispatch.ts`.
3. Detail how representation parameters (`material`, `bumpFrequency`, `emissive`) and canvas render passes should be configured.
4. Ensure the solution cleanly builds with Vite (`npm run build` in `viewer/`) without breaking standard Mol* dependencies.

Write your report and implementation blueprint to:
/Users/charlie/code/protean/.agents/explorer_m1_3/handoff.md

Maintain progress.md in your working directory.
When done, message your parent.
