# Mol* Shader & Rendering Architecture Survey: Glass & Seaglass Material Implementation

## 1. Observation

### 1.1 Mol* Codebase Location and Bundling Pipeline
- **Package Location**: Mol* is installed as an npm dependency at `/Users/charlie/code/protean/viewer/node_modules/molstar` (version `^5.11.0`, declared in `/Users/charlie/code/protean/viewer/package.json:17`).
- **Source Structure**:
  - Pre-compiled ESM modules with inlined GLSL string literals reside in `viewer/node_modules/molstar/lib/`.
  - Shader definitions and chunks reside in `viewer/node_modules/molstar/lib/mol-gl/shader/` and `viewer/node_modules/molstar/lib/mol-gl/shader/chunks/`.
  - Render pipeline and passes reside in `viewer/node_modules/molstar/lib/mol-canvas3d/passes/`.
  - Geometry and base values reside in `viewer/node_modules/molstar/lib/mol-geo/geometry/base.js`.
  - Material utilities reside in `viewer/node_modules/molstar/lib/mol-util/material.js`.
- **Bundling Configuration**:
  - `viewer/vite.config.ts:11-13`:
    ```typescript
    build: {
      outDir: '../src/protean_mcp/static',
      emptyOutDir: true,
      chunkSizeWarningLimit: 5000,
    }
    ```
  - `viewer/package.json:6-15`:
    ```json
    "scripts": {
      "sync-molstar": "mkdir -p public && cp node_modules/molstar/build/viewer/molstar.css public/ && cp node_modules/molstar/LICENSE public/molstar-LICENSE.txt",
      "predev": "npm run sync-molstar",
      "prebuild": "npm run sync-molstar",
      "dev": "vite",
      "build": "vite build",
      "preview": "vite preview",
      "test": "vitest run",
      "test:watch": "vitest"
    }
    ```
  - `viewer/src/main.ts:23`: Imports `Viewer` from `molstar/lib/apps/viewer/app`.
  - `docs/molstar-bundling.md:31-38`: Records build measurements: 1.19–1.27 GB peak RSS, 4.6 s build time, producing ~4.9 MB bundle (1.4 MB gzip) at `src/protean_mcp/static/assets/`.

### 1.2 Material & Shader Definitions in Mol*
- **Material Data Structure (`mol-util/material.js:7-11`)**:
  ```javascript
  export function Material(values) {
      return { ...Material.Zero, ...values };
  }
  Material.Zero = { metalness: 0, roughness: 0, bumpiness: 0 };
  ```
- **Geometry Uniform Binding (`mol-geo/geometry/base.js:119-121, 139-141`)**:
  ```javascript
  uMetalness: ValueCell.create(props.material.metalness),
  uRoughness: ValueCell.create(props.material.roughness),
  uBumpiness: ValueCell.create(props.material.bumpiness),
  uEmissive: ValueCell.create(props.emissive),
  uDensity: ValueCell.create(props.density),
  ```
- **Shader Chunk Hierarchy**:
  - `chunks/color-vert-params.glsl.js` & `chunks/color-frag-params.glsl.js`: Declare uniforms `uMetalness`, `uRoughness`, `uBumpiness`, `uBumpFrequency`, `uBumpAmplitude`, `uEmissive`.
  - `chunks/assign-material-color.glsl.js:33-41`:
    ```glsl
    float metalness = uMetalness;
    float roughness = uRoughness;
    float bumpiness = uBumpiness;
    #ifdef dSubstance
        float sf = clamp(vSubstance.a, 0.0, 0.99);
        metalness = mix(metalness, vSubstance.r, sf);
        roughness = mix(roughness, vSubstance.g, sf);
        bumpiness = mix(bumpiness, vSubstance.b, sf);
    #endif
    ```
  - `chunks/apply-light-color.glsl.js:25-29, 44-54`:
    ```glsl
    #ifdef bumpEnabled
        if (uBumpFrequency > 0.0 && uBumpAmplitude > 0.0 && bumpiness > 0.0) {
            normal = perturbNormal(-vViewPosition, normal, fbm(vModelPosition * uBumpFrequency), (uBumpAmplitude * bumpiness) / uBumpFrequency);
        }
    #endif
    ...
    PhysicalMaterial physicalMaterial;
    physicalMaterial.diffuseColor = color.rgb * (1.0 - metalness);
    physicalMaterial.roughness = min(max(roughness, 0.0525), 1.0);
    physicalMaterial.specularColor = mix(vec3(0.04), color.rgb, metalness);
    physicalMaterial.specularF90 = 1.0;
    ```
  - `chunks/light-frag-params.glsl.js:73-84`: Implements Cook-Torrance microfacet specular BRDF using GGX distribution (`D_GGX`), Smith correlated visibility (`V_GGX_SmithCorrelated`), and Schlick Fresnel approximation (`F_Schlick`).
  - `chunks/common-frag-params.glsl.js:108-121`: Implements Mikkelsen bump mapping in `perturbNormal(in vec3 position, in vec3 normal, in float height, in float scale)` using screen-space derivatives `dFdx`/`dFdy`.

### 1.3 Transparency and Rendering Passes in Mol*
- **Pass Execution Sequence (`mol-canvas3d/passes/draw.js:155-371`)**:
  1. `renderOpaque(scene.primitives, camera)`: Draws opaque geometry into `this.colorTarget` with `depthTextureOpaque` bound.
  2. `renderDepthTransparent(scene.primitives, camera, this.depthTextureOpaque)`: If postprocessing requires transparent depth (e.g., DoF, SSAO, outline), captures front transparent depth in `depthTargetTransparent`.
  3. Transparent Primitive Pass:
     - `wboit` mode: `WboitPass` renders accumulation + revealage buffers, evaluated into `transparentColorTarget`.
     - `dpoit` mode: `DpoitPass` executes dual depth peeling iterations into `transparentColorTarget`.
     - `blended` mode: `renderBlendedTransparent` renders with alpha blending (`SRC_ALPHA`, `ONE_MINUS_SRC_ALPHA`) into `transparentColorTarget`, depth-tested against `depthTextureOpaque` (depth mask false).
  4. `postprocessing.render(...)`:
     - Computes SSAO on opaque and transparent depth.
     - Evaluates outlines and shadows.
     - Blends transparent color over opaque color (`postprocessing.frag.js:217`):
       ```glsl
       color = transparentColor + color * (1.0 - alpha);
       ```
     - Composites additive bloom.
  5. Antialiasing (`AntialiasingPass`: SMAA/FXAA) & Depth of Field (`DofPass`).

### 1.4 Protean Material and Preset Systems
- **Material Finish Registry (`viewer/src/dispatch.ts:478-493`)**:
  ```typescript
  const MATERIAL_FINISHES: Record<string, { metalness: number; roughness: number; bumpiness?: number; bump_frequency?: number }> = {
    matte: { metalness: 0, roughness: 1.0 },
    satin: { metalness: 0.15, roughness: 0.6 },
    glossy: { metalness: 0.3, roughness: 0.15 },
    metallic: { metalness: 1.0, roughness: 0.6 },
    chrome: { metalness: 1.0, roughness: 0.1 },
    origami: { metalness: 0, roughness: 1.0, bumpiness: 0.45, bump_frequency: 4.5 },
  };
  ```
- **Material Dispatch Handler (`viewer/src/dispatch.ts:2014-2121`)**:
  - Validates requested finish.
  - Updates representation cell parameters: `old.type.params.material`, `old.type.params.emissive`, `old.type.params.bumpFrequency`.
  - Commits plugin state update.
- **Python MCP Server (`src/protean_mcp/server.py`)**:
  - `material(finish, name, metalness, roughness, emissive, bumpiness, bump_frequency)` (`line 3731`).
  - `capabilities()` reports available finishes and presets (`line 4193`).
  - `_PRESETS` dictionary (`line 5351`) and `preset(name, handle)` tool (`line 5366`).
  - View presets like `origami`, `felt`, `painting` defined via `_View` in `_VIEWS` (`line 5150`).

---

## 2. Logic Chain

1. **Screen-Space Refraction Architecture**:
   - In Mol*'s `DrawPass`, when transparent geometry renders, the opaque background and any internal/enclosed structures are already fully rendered into `this.colorTarget.texture` (`tColor`), and their depth is captured in `this.depthTextureOpaque`.
   - By supplying the opaque scene texture to the transparent render shader as a texture uniform (`tSceneColor` or `tColor`), fragments of refractive surfaces can compute screen-space coordinates `gl_FragCoord.xy / uDrawingBufferSize` and distort them according to the surface normal and view vector.

2. **Optical Model for Clear `glass`**:
   - Surface parameters: `metalness = 0.0`, `roughness = 0.02 - 0.05`, `bumpiness = 0.0`.
   - Normal Vector: Smooth surface normal $\vec{N}$ (or view-space normal from geometry).
   - Refraction vector: Screen-space offset $\Delta \vec{uv} = \vec{N}_{xy} \times k_{\text{refract}} \times \left(1.0 - \frac{1.0}{\eta_{\text{glass}}}\right) \times \frac{1.0}{\max(|viewZ|, 1.0)}$, where $\eta_{\text{glass}} \approx 1.5$.
   - Fresnel reflection: Schlick approximation $F = F_0 + (1 - F_0)(1 - |\vec{N} \cdot \vec{V}|)^5$, with $F_0 = 0.04$.
   - Transmission compositing: The transmitted background color $(1 - F) \times \text{sample}(tSceneColor, uv + \Delta uv)$ is combined with the specular reflection $F \times C_{\text{specular}}$.
   - Prismatic Dispersion: Small spectral offset in $\Delta \vec{uv}$ between R, G, and B sampling taps creates subtle, physically realistic chromatic aberration at curved silhouette edges.

3. **Optical Model for Frosted `seaglass`**:
   - Surface parameters: `metalness = 0.0`, `roughness = 0.65 - 0.85`, `bumpiness = 0.35 - 0.50`, `bump_frequency = 3.5 - 5.0`.
   - Normal perturbation: `perturbNormal` with procedural `fbm` produces microfacet normal displacement, creating tumbled surface tooth.
   - Refraction diffusion: High surface roughness scatters transmitted rays. Instead of a single tap at $uv + \Delta uv$, the shader samples an 8–12 tap Poisson disc or spiral kernel with radius proportional to `roughness * uDiffusionSpread`.
   - Transmitted tint & absorption: Transmitted light is filtered by Beer-Lambert absorption / multiplicative tint with the seaglass color (seafoam green `#73b9a2` / `#88c5b5`).
   - Surface sheen: Diffuse reflection combined with GGX specular highlight at roughness ~0.7 produces the characteristic soft, velvety sheen of tumbled beach glass.

4. **API Integration**:
   - `viewer/src/dispatch.ts`: Add `glass` and `seaglass` entries to `MATERIAL_FINISHES`.
   - `src/protean_mcp/server.py`:
     - Support `finish="glass"` and `finish="seaglass"` in `material()`.
     - Implement `_preset_seaglass` in `server.py`:
       - Sets `color="#73b9a2"` (or `#88c5b5`).
       - Sets `material(finish="seaglass")`.
       - Sets three-point / studio lighting to enhance rim highlights.
       - Adds `"seaglass"` to `_PRESETS`.

---

## 3. Caveats

1. **Single-Layer Screen-Space Limitation**:
   - Screen-space refraction refracts what is currently in the color buffer (the opaque scene and background). Multiple overlapping transparent refractive objects will refract the opaque background rather than recursively refracting each other. For macromolecular visualization, this is standard and visually convincing.
2. **Viewport Boundary Clamping**:
   - When sampling `uv + \Delta uv`, refracted UV coordinates must be clamped to $[0, 1]$ or fallback to undistorted $uv$ to prevent sampling outside the framebuffer boundary.
3. **WebGL1 vs WebGL2 Support**:
   - Derivatives `dFdx`/`dFdy` require `OES_standard_derivatives` in WebGL1 (enabled by default in Mol* via `chunks/common.glsl.js`). WebGL2 natively supports standard derivatives and texture LOD sampling.
4. **Order-Independent Transparency (WBOIT/DPOIT) Interaction**:
   - In blended mode (`_renderBlended`), `colorTarget` is cleanly populated prior to transparent primitive draws. In WBOIT/DPOIT modes, refraction shaders should read from the bound opaque color target.

---

## 4. Conclusion

Implementing `glass` and `seaglass` requires:
1. **Material Definition Extensions**:
   - Add `glass` (`metalness: 0, roughness: 0.05, bumpiness: 0`) and `seaglass` (`metalness: 0, roughness: 0.65, bumpiness: 0.4, bump_frequency: 4.0`) to `MATERIAL_FINISHES` in `viewer/src/dispatch.ts`.
2. **Shader Pipeline Enhancement**:
   - Extend fragment shaders (`apply-light-color.glsl.js` / `assign-material-color.glsl.js` or dedicated transmission chunk) to calculate Snell screen-space refraction, Schlick Fresnel reflection/transmission balance, chromatic dispersion, and multi-tap roughness diffusion.
   - Bind `drawPass.colorTarget.texture` during transparent rendering so shaders can sample the scene behind the glass.
3. **Protean API & Preset Integration**:
   - Expose `finish="glass"` and `finish="seaglass"` in `server.py:material`.
   - Implement `_preset_seaglass` applying the `seaglass` finish with seafoam color tinting (`#73b9a2`) and balanced studio lighting.
4. **Build & Test Validation**:
   - Compile viewer bundle with `npm run build` in `viewer/`.
   - Verify unit tests with `npm test` in `viewer/`.
   - Verify Python integration with `pytest tests/test_server.py` and differential rendering tests in `tests/test_render_differential.py`.

---

## 5. Verification Method

### 5.1 Build Verification
Execute in `viewer/`:
```bash
npm run build
```
Verify that `src/protean_mcp/static/assets/index-*.js` and `index.html` are generated without TypeScript or Vite errors.

### 5.2 Unit Test Verification
Execute in `viewer/`:
```bash
npm test
```
Verify all Vitest test suites in `viewer/src/*.test.ts` pass, including dispatch finish validations.

### 5.3 Python Test Verification
Execute from project root:
```bash
uv run pytest tests/test_server.py -k "material or preset"
```
Verify `capabilities()`, `material(finish="glass")`, `material(finish="seaglass")`, and `preset("seaglass")` execute without error.

### 5.4 Differential Render Verification
Execute differential tests with browser:
```bash
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_render_differential.py -k "finish or preset"
```
Confirm pixel assertions verify distinct coverage, roughness ordering, and visual differentiation for `glass` and `seaglass`.
