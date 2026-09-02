## 2026-08-28T10:12:01Z
You are an Explorer designing the frosted seaglass diffusion shader and roughness scattering in Mol*.

Your working directory is: /Users/charlie/code/protean/.agents/explorer_m1_2
Read the following files before starting:
- /Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md
- /Users/charlie/code/protean/PROJECT.md

Your task:
1. Investigate how to achieve high-quality frosted / diffused transmission for `seaglass` in Mol*.
2. Design the multi-tap sampling kernel (Poisson disc or spiral sampling) that scatters transmitted rays proportionally to surface roughness and bumpiness.
3. Design the integration with Mol*'s bump mapping / `perturbNormal` / `fbm` to produce the tumbled beach glass surface texture.
4. Design the transmitted color filtering / Beer-Lambert absorption tint.
5. Detail the exact code modifications and file locations needed in `viewer/src/`.

Write your report and implementation blueprint to:
/Users/charlie/code/protean/.agents/explorer_m1_2/handoff.md

Maintain progress.md in your working directory.
When done, message your parent.
