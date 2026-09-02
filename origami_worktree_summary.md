# Protean Origami & Glass Shader Worktree Summary

**Status:** Parked / Pending Teardown
**Worktree:** `/Users/charlie/code/protean-origami`

This document summarizes the WebGL debugging and rendering fixes achieved during the attempt to implement the `glass`, `seaglass`, and `origami` shaders. It is intended to preserve the root-cause analysis and patches discovered before the worktree is torn down.

## Accomplishments

### 1. Identified the Root Bug (`GL_INVALID_OPERATION` Feedback Loop)
We tracked down a deep WebGL bug that was causing the glass shaders to render completely blank/transparent PNGs (resulting in `0.0` test coverage). The issue was traced to MolStar’s compute renderables: MolStar permanently caches a placeholder `scratch.texture` at initialization. This caused the refraction shader to simultaneously read from and write to the same buffer (`scratch`), triggering a GPU feedback loop and wiping out the render target with zeros.

### 2. Applied a WebGL Fix (`refraction.ts`)
We successfully patched the MolStar rendering pipeline in `viewer/src/refraction.ts`. The fix modified `buildRefractionState` to accept and directly bind the active source textures (`tColor`, `tDepthOpaque`, etc.) during lazy initialization inside `applyRefraction`. This bypassed the placeholder cache entirely, structurally fixing the initial WebGL feedback loop.

### 3. Automated Testing & Logging Setup
We converted the automated snapshot tester (`test_coverage.py`) from a `pytest` framework into a standard `asyncio` script. This change was critical because `pytest` was swallowing the necessary WebGL and Chrome browser logs. Running it directly allowed us to surface the headless Chrome GPU processes and CDP (Chrome DevTools Protocol) logs, which is how we identified the WebGL feedback loop in the first place.

---

## User Feedback & Next Steps
* The generated test renders for `glass` and `seaglass` missed the mark visually and did not match the original pristine reference images (`glass.png` and `seaglass.png`).
* The current directive is to drop the "origami" aesthetic, park this WebGL debugging effort, and search back through the original conversation trajectories to rediscover the exact method/script used to create the original high-fidelity reference images.
