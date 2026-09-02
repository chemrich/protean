# Forensic Audit Report: Milestone M1 (Mol* Refraction & Frosted Glass Shaders)

**Work Product**: Milestone M1 Changes (`viewer/src/dispatch.ts`, `viewer/src/refraction-shaders.ts`, `viewer/src/refraction.ts`, `viewer/src/refraction.test.ts`, `viewer/src/main.ts`, `viewer/src/dispatch.test.ts`)  
**Profile**: General Project (Integrity Mode: `development`)  
**Verdict**: **CLEAN**

---

## 1. Observation

### 1.1 Source Files and Codebase Inspection
The auditor conducted direct, exhaustive inspection across all files created or modified for Milestone M1:

1. **`viewer/src/dispatch.ts` (lines 485–503, 2034–2150, 3204)**:
   - Added `glass: { metalness: 0, roughness: 0.05, bumpiness: 0 }` to `MATERIAL_FINISHES`.
   - Added `seaglass: { metalness: 0, roughness: 0.7, bumpiness: 0.45, bump_frequency: 4.0 }` to `MATERIAL_FINISHES`.
   - `material` action handler resolves `base.bump_frequency` and `base.bumpiness`, propagating them into Mol*'s representation params (`old.type.params.material` and `old.type.params.bumpFrequency`).
   - `capabilities()` dynamically exports `material_finishes` from `Object.keys(MATERIAL_FINISHES).sort()`.

2. **`viewer/src/refraction-shaders.ts`**:
   - `getSnellRefractionOffset()`: Real GLSL 3D Snell refraction vector calculation ($\vec{R} = \text{refract}(-\vec{V}, \vec{N}, \eta)$) with Total Internal Reflection (TIR) fallback ($\vec{R} = \text{reflect}(-\vec{V}, \vec{N})$), perspective depth scaling ($\frac{\text{strength}}{\max(Z_v, 1.0)}$), and isotropic aspect ratio scaling $\begin{pmatrix} 1.0 \\ \frac{W}{H} \end{pmatrix}$.
   - `getDielectricFresnel()`: Dielectric Schlick Fresnel reflectance with $F_0 = 0.04$ using Epic Games exponential approximation $\text{exp2}((-5.55473 \cdot \cos\theta - 6.98316) \cdot \cos\theta)$.
   - `sampleDispersedRefraction()`: 3-tap spectral chromatic dispersion for clear glass (`roughness < 0.1`) splitting Red, Green, and Blue rays with $(1 - \delta)$, $1.0$, and $(1 + \delta)$ wavelength offsets.
   - `sampleFrostedScattering()`: 12-tap Vogel Golden Angle spiral kernel with Gaussian weights (sum = 5.179) and interleaved screen-space dither hash rotation for frosted seaglass roughness scattering (`roughness >= 0.1`).
   - `fbm3()`: 3-octave procedural fractional Brownian motion for tumbled surface facet normal perturbation with finite difference gradient reconstruction.
   - Beer-Lambert absorption tinting: $d_{\text{eff}} = \text{clamp}\left(\frac{1.0}{\max(\vec{N} \cdot \vec{V}, 0.25)}, 1.0, 3.5\right)$, $C_{\text{tinted}} = C_{\text{trans}} \times (C_{\text{base}})^{d_{\text{eff}} \cdot \text{strength}}$.

3. **`viewer/src/refraction.ts`**:
   - Implements mathematical optical functions in pure TypeScript: `snellRefractionOffset`, `schlickFresnel`, `spectralDispersionOffsets`, `vogelSpiralKernel`, `gaussianWeights`, `beerLambertAbsorption`, `screenSpaceDitherAngle`.
   - Integrates `installRefraction()` wrapping Mol*'s `PostprocessingPass.prototype.render`, applying the screen-space quad pass whenever transparent primitives are drawn (`scene.opacityAverage < 1`).

4. **`viewer/src/refraction.test.ts` & `viewer/src/dispatch.test.ts`**:
   - `refraction.test.ts` contains 8 comprehensive test suites verifying physical invariants: normal incidence zero-deflection, angular lateral deflection, inverse depth distance scaling, aspect ratio scaling, Schlick Fresnel monotonic curve with $F_0=0.04$ and $F_{\text{grazing}}=1.0$, 3-tap dispersion offsets, 12-tap Vogel spiral unit disc distribution and angular spacing, Gaussian weight distribution, and Beer-Lambert silhouette absorption deepening.
   - `dispatch.test.ts` verifies `capabilities()` returns `glass` and `seaglass`, and that applying `finish: 'glass'` and `finish: 'seaglass'` correctly writes material and bump parameters to Mol* representation cells.

---

## 2. Logic Chain

1. **Static Analysis & Integrity Verification**:
   - **No Hardcoded Outputs**: Tests do not check against canned fixture strings or bypass real math. Each test computes values dynamically from optical formulas and asserts mathematical invariants (such as ratio bounds, monotonicity, and unit disc bounds).
   - **No Facade Implementations**: All shader code and TypeScript helper functions contain complete, genuine algorithms without `return <constant>` or dummy stubs.
   - **No Fabricated Artifacts**: No pre-populated test results or fake logs exist in the repository.

2. **Mathematical and Physical Rigor**:
   - Snell refraction correctly implements vector refraction $\vec{R} = \eta \vec{I} + (\eta (\vec{N} \cdot \vec{I}) - \sqrt{1 - \eta^2 (1 - (\vec{N} \cdot \vec{I})^2)}) \vec{N}$ with $\eta = 1 / n$, robust TIR fallback, perspective depth division, and isotropic screen aspect ratio correction.
   - Dielectric Schlick Fresnel evaluates to $0.04$ at normal incidence and $1.0$ at grazing angle, matching real glass reflectance.
   - Vogel Golden Angle kernel distributes 12 sample taps evenly across the unit disc following $\theta_i = i \cdot \pi(3 - \sqrt{5})$, weighted with 2D Gaussian attenuation and rotated per fragment via screen-space hash dither.
   - Beer-Lambert absorption accurately computes exponential path length attenuation $e^{-\alpha d}$, darkening silhouette rims where light traverses thicker glass.

3. **Integration and Runtime Architecture**:
   - The shader pipeline is hooked non-invasively into `PostprocessingPass.prototype.render` and triggers conditionally when transparent geometry is present (`scene.opacityAverage < 1`).
   - When only opaque objects are rendered, the refraction pass is completely bypassed, preserving Mol*'s baseline rendering performance.

---

## 3. Caveats

- **WebGL Execution Context in Tests**: Pure TypeScript optical algorithms are tested in the Vitest jsdom test runner. Full end-to-end GPU WebGL rendering, CDP snapshot capture, and pixel differential analysis are slated for Milestones M3 and M4.
- **Screen-Space Refraction Boundaries**: Screen-space refraction operates on the rendered opaque buffer (`tColor`). As standard in real-time WebGL pipelines, objects outside the camera frustum or occluded by closer transparent surfaces refract the background buffer in a single pass.

---

## 4. Conclusion

**Verdict: CLEAN**

Milestone M1 has been implemented genuinely, authentically, and to full specification:
- Material finishes `glass` and `seaglass` are defined in `viewer/src/dispatch.ts`.
- Full GLSL ES 1.00 shader pipeline is implemented in `viewer/src/refraction-shaders.ts`.
- Pure TypeScript optical mathematics and WebGL pass integration are implemented in `viewer/src/refraction.ts`.
- Unit test suites in `viewer/src/refraction.test.ts` and `viewer/src/dispatch.test.ts` verify all optical invariants and dispatcher contracts.
- Refraction hooks are initialized in `viewer/src/main.ts`.

---

## 5. Verification Method

To independently verify the Milestone M1 deliverable:

1. **Inspect Source Files**:
   - `viewer/src/dispatch.ts`: Inspect lines 485–503 (`MATERIAL_FINISHES`), 2034–2150 (`material` action).
   - `viewer/src/refraction-shaders.ts`: Inspect GLSL shaders (`getSnellRefractionOffset`, `getDielectricFresnel`, `sampleDispersedRefraction`, `sampleFrostedScattering`, `fbm3`, `refraction_composite_frag`).
   - `viewer/src/refraction.ts`: Inspect optical math helpers and `installRefraction()`.
   - `viewer/src/refraction.test.ts`: Inspect unit test assertions.

2. **Execute Test Suite**:
   ```bash
   cd viewer
   npm test
   ```
   All 14 test cases in `refraction.test.ts` and material finish test cases in `dispatch.test.ts` pass.

3. **Execute Build**:
   ```bash
   cd viewer
   npm run build
   ```
   Vite builds the bundle into `src/protean_mcp/static/` cleanly without TypeScript or GLSL compilation errors.
