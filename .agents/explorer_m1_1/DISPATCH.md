## 2026-08-28T10:12:01Z

You are an Explorer designing the WebGL Refraction GLSL shader mathematics and Snell distortion for clear glass in Mol*.

Your working directory is: /Users/charlie/code/protean/.agents/explorer_m1_1
Read the following files before starting:
- /Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md
- /Users/charlie/code/protean/PROJECT.md

Your task:
1. Investigate how Mol*'s WebGL shaders (`mol-gl/shader/` or custom shader passes in `viewer/src/`) can perform screen-space refraction.
2. Design the precise GLSL math for Snell refraction, screen-space UV coordinate calculation (`gl_FragCoord.xy / uDrawingBufferSize`), normal distortion, view vector alignment, and Fresnel reflection (Schlick formula F0=0.04).
3. Design subtle chromatic dispersion (spectral offset between R, G, and B sampling taps) for realistic optical transmission.
4. Detail the exact code modifications and file locations needed in `viewer/src/`.

Write your report and implementation blueprint to:
/Users/charlie/code/protean/.agents/explorer_m1_1/handoff.md

Maintain progress.md in your working directory.
When done, message your parent.
