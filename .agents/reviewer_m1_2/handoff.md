# Milestone M1 Review Report (Reviewer 2)

## Review Summary

**Verdict**: REQUEST_CHANGES

Milestone M1 implements well-structured TypeScript optical mathematics, GLSL ES 1.00 refraction shader passes, finish definitions (`glass`, `seaglass`), and Vitest unit tests in `viewer/src/`. However, the compiled production build artifacts in `src/protean_mcp/static/` are out of date and contain pre-M1 assets (missing `glass`, `seaglass`, `installRefraction`, and the refraction pipeline). Consequently, running Protean will serve stale assets that lack the new shader capabilities.

---

## 1. Observation

### 1.1 Source Code Implementation & Unit Tests
1. **`viewer/src/dispatch.ts:485-502`**:
   `MATERIAL_FINISHES` correctly includes:
   ```typescript
   glass: { metalness: 0, roughness: 0.05, bumpiness: 0 },
   seaglass: { metalness: 0, roughness: 0.7, bumpiness: 0.45, bump_frequency: 4.0 },
   ```
2. **`viewer/src/dispatch.ts:2034-2160`**:
   `material` action handler correctly validates `finish`, `roughness`, `metalness`, `bumpiness`, `bump_frequency` (bounded to $[0, 10]$), and updates Mol* representations.
3. **`viewer/src/dispatch.ts:3204`**:
   `capabilities()` reports sorted `material_finishes` including `glass` and `seaglass`.
4. **`viewer/src/refraction.ts` & `viewer/src/refraction-shaders.ts`**:
   - Implements pure TS optical math: `snellRefractionOffset`, `schlickFresnel`, `spectralDispersionOffsets`, `vogelSpiralKernel`, `gaussianWeights`, `beerLambertAbsorption`, `screenSpaceDitherAngle`.
   - Implements WebGL composite pass `refraction_composite_frag` and patches Mol*'s `PostprocessingPass.prototype.render` via `installRefraction()`.
5. **`viewer/src/refraction.test.ts:1-187`**:
   Comprehensive unit tests for Snell deflection, depth scaling, aspect ratio correction, Schlick Fresnel monotonic increase ($F_0 = 0.04 \to 1.0$), 3-tap spectral dispersion, 12-tap Vogel Golden Angle spiral, Gaussian weights, Beer-Lambert absorption, and dither hash.
6. **`viewer/src/dispatch.test.ts:523, 1634-1672`**:
   Unit tests for `capabilities.material_finishes` containing `glass` and `seaglass`, and `material` action application of `glass` and `seaglass`.

### 1.2 Build Artifact & Static Directory Integrity
1. **`viewer/vite.config.ts:11`**:
   Specifies output directory: `outDir: '../src/protean_mcp/static'`.
2. **`src/protean_mcp/static/index.html:67`**:
   References `<script type="module" crossorigin src="./assets/index-BTYmYBUw.js"></script>`.
3. **`src/protean_mcp/static/assets/index-BTYmYBUw.js`**:
   Inspection via pattern search reveals:
   - Search for `seaglass`: **0 matches** (not present).
   - Search for `installRefraction`: **0 matches** (not present).
   - Search for `uGlassIOR` / `refraction-composite`: **0 matches** (not present).
   - The file contains pre-M1 assets.

---

## 2. Logic Chain

1. **Source Implementation Quality**:
   - `viewer/src/dispatch.ts`, `viewer/src/refraction.ts`, `viewer/src/refraction-shaders.ts`, and `viewer/src/main.ts` fulfill the architectural requirements of M1 (F1: glass finish, F2: seaglass finish, F3: screen-space refraction shader, F4: frosted roughness diffusion).
   - Optical formulas follow standard graphics physics (Snell's law with perspective depth scaling, Epic Games Schlick Fresnel approximation, 12-tap Vogel spiral with Gaussian weights, and Beer-Lambert absorption).
   - Unit tests in `viewer/src/refraction.test.ts` and `viewer/src/dispatch.test.ts` provide strong coverage of mathematical algorithms and dispatch actions.

2. **Integration Gap (Build Artifacts)**:
   - The worker report in `worker_m1/handoff.md` stated that `npm run build` compiles `viewer/src/` into `src/protean_mcp/static/`.
   - However, inspection of `src/protean_mcp/static/assets/index-BTYmYBUw.js` confirms that the static build artifact was not updated to include the M1 changes.
   - When the Python FastMCP server runs and opens the Mol* web viewer in the browser, the browser will load `src/protean_mcp/static/index.html` and `src/protean_mcp/static/assets/index-BTYmYBUw.js`. Because this bundle does not contain `glass` or `seaglass` or the refraction pass hooks, downstream milestones (M2, M3, M4) that test browser rendering will fail.

---

## 3. Findings

### Critical Finding 1: Production Build Artifacts in `src/protean_mcp/static/` are Stale (Pre-M1)
- **What**: The compiled bundle in `src/protean_mcp/static/assets/index-BTYmYBUw.js` does not contain `glass`, `seaglass`, `installRefraction`, or the refraction GLSL shader.
- **Where**: `src/protean_mcp/static/index.html` and `src/protean_mcp/static/assets/index-BTYmYBUw.js`.
- **Why**: Protean serves static assets from `src/protean_mcp/static/` to browser clients. If this directory is not built from `viewer/src/`, the web viewer will not have access to any M1 features.
- **Suggestion**: Run `npm run build` inside `viewer/` so that Vite compiles `viewer/src/` cleanly into `src/protean_mcp/static/`, producing an updated bundle with hash and index reference.

### Major Finding 2: Unhooked `transmission_chunk_glsl` in `refraction-shaders.ts`
- **What**: `transmission_chunk_glsl` (lines 304-392) is exported from `viewer/src/refraction-shaders.ts` but is not imported or registered anywhere in `refraction.ts` or `main.ts`.
- **Where**: `viewer/src/refraction-shaders.ts:304-392`.
- **Why**: Dead or unreferenced shader code creates ambiguity about whether per-fragment transmission chunks or postprocessing passes are active.
- **Suggestion**: Add a clarifying comment or hook it into the shader chunk pipeline if intended for future use.

### Minor Finding 3: Missing Test for Total Internal Reflection (TIR) Fallback
- **What**: `snellRefractionOffset` handles $k < 0$ (Total Internal Reflection) by falling back to `reflect(-V, N)`, but `refraction.test.ts` does not test this branch.
- **Where**: `viewer/src/refraction.ts:113-117` and `viewer/src/refraction.test.ts`.
- **Why**: Boundary conditions where rays exceed the critical angle should be verified.
- **Suggestion**: Add a unit test case in `refraction.test.ts` exercising $k < 0$.

---

## 4. Caveats

- Unit test execution via `run_command` in this turn timed out waiting for user confirmation permissions; the assessment was performed through comprehensive static analysis of the TypeScript/GLSL source files and direct file inspection of the build artifacts in `src/protean_mcp/static/`.

---

## 5. Conclusion

**Verdict**: REQUEST_CHANGES

While the source code and unit tests in `viewer/src/` are well-designed and mathematically sound, `src/protean_mcp/static/` must be rebuilt via `npm run build` so that the compiled bundle matches the source implementation before Milestone M1 can be approved.

---

## 6. Verification Method

1. **Verify Static Bundle Contents**:
   Inspect `src/protean_mcp/static/assets/index-*.js` to ensure strings `glass`, `seaglass`, and `installRefraction` are present.
2. **Execute Build**:
   Inside `viewer/`:
   ```bash
   npm run build
   ```
   Verify that `src/protean_mcp/static/` receives new bundle assets.
3. **Execute Unit Tests**:
   Inside `viewer/`:
   ```bash
   npm test
   ```
   Verify all test suites in `viewer/src/dispatch.test.ts` and `viewer/src/refraction.test.ts` pass.
