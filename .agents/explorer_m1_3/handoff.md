# Mol* Render Pass Integration and Material Finish Definitions Design Blueprint

## 1. Observation

### 1.1 Mol* Render Pass Architecture and Texture Buffers
- **`DrawPass` Implementation (`viewer/node_modules/molstar/lib/mol-canvas3d/passes/draw.js:46-55`)**:
  - `this.colorTarget = webgl.createRenderTarget(width, height, true, 'uint8', 'linear')`: The primary render target where opaque scene geometry and background are rasterized.
  - `this.transparentColorTarget = webgl.createRenderTarget(width, height, false, 'uint8', 'linear')`: The secondary render target where transparent primitives are rendered when postprocessing is active.
  - `this.depthTextureOpaque`: Depth texture attached to `colorTarget` during opaque primitive rendering.
  - `this.depthTargetTransparent`: Render target storing transparent surface depth.
- **Pass Execution Sequence in `_renderBlended` (`draw.js:255-305`)**:
  1. Opaque Draw:
     ```javascript
     this.depthTextureOpaque.attachFramebuffer(this.colorTarget.framebuffer, 'depth');
     renderer.clear(true);
     if (scene.hasOpaque) {
         renderer.renderOpaque(scene.primitives, camera);
     }
     ```
  2. Transparent Draw:
     ```javascript
     if (scene.opacityAverage < 1) {
         if (isPostprocessingEnabled) {
             this.transparentColorTarget.bind();
             renderer.clear(false, false, true);
             this.depthTextureOpaque.attachFramebuffer(this.transparentColorTarget.framebuffer, 'depth');
         }
         renderer.renderBlendedTransparent(scene.primitives, camera);
     }
     ```
  3. Postprocessing & Alpha Blending (`draw.js:312`, `postprocessing.js:166`, `postprocessing.frag.js:212-220`):
     - `PostprocessingPass` receives `colorTarget.texture` (`tColor`) and `transparentColorTarget.texture` (`tTransparentColor`).
     - In `postprocessing.frag.js:217`:
       ```glsl
       color = transparentColor + color * (1.0 - alpha);
       ```
- **Shared Texture Binding in `mol-gl/renderer.js:113-115, 343, 686-688`**:
  - `renderer.js` maintains a `sharedTexturesList: Array<[string, Texture]> = [['tDepth', depthTexture]]`.
  - In `renderDpoitTransparent`, DPOIT textures are appended: `arrayMapUpsert(sharedTexturesList, 'tDpoitDepth', ...)`, etc.
  - In `renderObject`: `program.bindTextures(sharedTexturesList, 0)` is called for every primitive draw.
  - In `render-item.js:168, 178`: `program.bindTextures(textures, sharedTexturesCount)` ensures per-renderable textures do not collide with shared global textures.

### 1.2 Material Finish Registry and Dispatcher in Protean
- **Finish Registry (`viewer/src/dispatch.ts:478-493`)**:
  ```typescript
  const MATERIAL_FINISHES: Record<
    string,
    {
      metalness: number;
      roughness: number;
      bumpiness?: number;
      bump_frequency?: number;
    }
  > = {
    matte: { metalness: 0, roughness: 1.0 },
    satin: { metalness: 0.15, roughness: 0.6 },
    glossy: { metalness: 0.3, roughness: 0.15 },
    metallic: { metalness: 1.0, roughness: 0.6 },
    chrome: { metalness: 1.0, roughness: 0.1 },
    origami: { metalness: 0, roughness: 1.0, bumpiness: 0.45, bump_frequency: 4.5 },
  };
  ```
- **Material Action Handler (`viewer/src/dispatch.ts:2052-2114`)**:
  - Reads `base = MATERIAL_FINISHES[finish]`.
  - Copies `metalness`, `roughness`, `bumpiness` into `material`.
  - Sets `effBumpFrequency = bump_frequency !== undefined ? bump_frequency : base.bump_frequency`.
  - Writes `old.type.params.material = { ...material }` and `old.type.params.bumpFrequency = effBumpFrequency`.
  - Emissive parameter is written directly to `old.type.params.emissive`.
  - Commits transaction via `plugin.state.data.build().commit()`.
- **Capabilities & Dispatch Unit Tests (`viewer/src/dispatch.test.ts:523, 1559-1780`)**:
  - Validates `capabilities()` returns sorted `material_finishes: ['chrome', 'glossy', 'matte', 'metallic', 'origami', 'satin']`.
  - Verifies PBR roughness ordering, bump parameter separation, and error handling for unknown finishes.

### 1.3 Mol* Shader Chunks and Parameter Handling
- **Physical Material in `chunks/apply-light-color.glsl.js:44-54`**:
  ```glsl
  PhysicalMaterial physicalMaterial;
  physicalMaterial.diffuseColor = color.rgb * (1.0 - metalness);
  #ifdef enabledFragDepth
      physicalMaterial.roughness = min(max(roughness, 0.0525), 1.0);
  #else
      vec3 dxy = max(abs(dFdx(normal)), abs(dFdy(normal)));
      float geometryRoughness = max(max(dxy.x, dxy.y), dxy.z);
      physicalMaterial.roughness = min(max(roughness, 0.0525) + geometryRoughness, 1.0);
  #endif
  physicalMaterial.specularColor = mix(vec3(0.04), color.rgb, metalness);
  physicalMaterial.specularF90 = 1.0;
  ```
- **Procedural Bump Perturbation in `chunks/apply-light-color.glsl.js:25-29`**:
  ```glsl
  #ifdef bumpEnabled
      if (uBumpFrequency > 0.0 && uBumpAmplitude > 0.0 && bumpiness > 0.0) {
          normal = perturbNormal(-vViewPosition, normal, fbm(vModelPosition * uBumpFrequency), (uBumpAmplitude * bumpiness) / uBumpFrequency);
      }
  #endif
  ```
- **Bump Frequency Support across Representations (`viewer/src/dispatch.ts:2045-2049`)**:
  - Supported with non-zero defaults on 7 representations: `cartoon` (default 2), `putty` (default 2), `spacefill` (default 1), `molecular-surface` (default 1), `gaussian-surface` (default 1), `orientation` (default 1), `polyhedron` (default 1).

---

## 2. Logic Chain

1. **Accessing the Opaque Scene Color Texture during Refractive Passes**:
   - In Mol*'s standard pipeline, when transparent primitives render (`scene.opacityAverage < 1`), all opaque primitives and background elements have already been fully rasterized into `drawPass.colorTarget` with opaque depth captured in `drawPass.depthTextureOpaque` (Obs §1.1).
   - Because `this.transparentColorTarget` is bound as the active render framebuffer during `renderBlendedTransparent`, `this.colorTarget.texture` is in an unbound/read-only state.
   - Therefore, WebGL permits sampling `this.colorTarget.texture` within transparent shader passes without causing framebuffer attachment feedback loops or WebGL invalid operation errors.
   - Exposing `this.colorTarget.texture` to transparent draw calls can be achieved cleanly either:
     a. By registering `tOpaqueColor` / `tColor` in `sharedTexturesList` inside `mol-gl/renderer.js` during `renderBlendedTransparent` / `renderWboitTransparent` / `renderDpoitTransparent`, and adding `tOpaqueColor` to `GlobalTextureSchema`.
     b. By post-processing/composition pass interception (wrapping `DrawPass.prototype._render` and `MultiSamplePass.prototype.render` as established in `viewer/src/painterly.ts`), where both `colorTarget.texture` and `transparentColorTarget.texture` are bound as input uniforms to a screen-space refraction quad shader.

2. **Physical Parameter Selection for `glass` in `MATERIAL_FINISHES`**:
   - Physical definition: A clear, smooth, pure dielectric transmission medium ($n \approx 1.5$, dielectric Fresnel $F_0 \approx 0.04$).
   - `metalness: 0`: Ensures dielectric behavior where diffuse color is governed by transmission rather than metallic absorption ($1.0 - \text{metalness} = 1.0$).
   - `roughness: 0.05`: Minimum physical roughness for optical glass, yielding sharp specular reflections and crystal-clear background transmission. Sits cleanly at Mol*'s internal shader threshold $\min(\max(\text{roughness}, 0.0525), 1.0)$ without triggering artifact clamping.
   - `bumpiness: 0`: Smooth surface with zero normal perturbation.

3. **Physical Parameter Selection for `seaglass` in `MATERIAL_FINISHES`**:
   - Physical definition: Frosted, weathered beach glass subjected to wave and sand abrasion, producing high surface roughness and micro-facet pitting.
   - `metalness: 0`: Dielectric transmission medium.
   - `roughness: 0.7`: High microfacet roughness that scatters transmitted rays over a wide cone (diffuse transmission/frosting) and creates a soft, velvety specular sheen.
   - `bumpiness: 0.45`: Moderate-to-high normal displacement that reproduces the tactile, chipped surface topography of beach glass.
   - `bump_frequency: 4.0`: Procedural noise frequency tuned to generate fine, natural tumbled microfacets across ribbons, surfaces, and spacefill representations.

4. **Representation and Render Pass Integration**:
   - When `material(finish="glass")` or `material(finish="seaglass")` is dispatched:
     - `material` assigns `{ metalness, roughness, bumpiness }` to `old.type.params.material`.
     - `effBumpFrequency` is assigned to `old.type.params.bumpFrequency` on representations that support bump mapping.
     - `emissive` defaults to `0.0`, leaving self-illumination unlit unless explicitly set by the caller.
   - For `preset("seaglass")`:
     - Sets seafoam tint (`color="#73b9a2"`).
     - Applies `material(finish="seaglass")`.
     - Sets three-point lighting rig (`lighting(rig="three-point", ambient=0.45)`), providing crucial rim light highlights that emphasize the curved 3D silhouettes of transparent geometry.
     - Enables ambient occlusion (`effects(occlusion=true)`) to anchor contact shadows beneath the frosted glass structure.

---

## 3. Caveats

1. **Screen-Space Refraction Depth Ordering**:
   - Screen-space refraction refracts geometry and background present in `colorTarget.texture` at the moment of transparent rendering. Multiple overlapping refractive surfaces in single-pass blended mode will each refract the opaque scene behind them rather than recursively refracting preceding transparent surfaces. For macromolecular visualization, this is standard and visually authentic.
2. **Representation Bump Frequency Availability**:
   - `bumpFrequency` is available on 7 representations (`cartoon`, `putty`, `spacefill`, `molecular-surface`, `gaussian-surface`, `orientation`, `polyhedron`). Representations without bump frequency (e.g. `ball-and-stick`, `label`, `line`) render the base material finish (`metalness` and `roughness`) smoothly without error.
3. **Vite Bundle Boundary**:
   - `viewer/src/dispatch.ts` is imported in unit tests running under Vitest/jsdom without WebGL context (`fakePlugin()`). Pass-patching and WebGL renderable creations must remain encapsulated in `viewer/src/main.ts` or dedicated pass modules (`refraction.ts` / `painterly.ts`) so that `dispatch.ts` remains pure and unit-testable in jsdom.

---

## 4. Conclusion

### 4.1 Specification for `MATERIAL_FINISHES` in `viewer/src/dispatch.ts`

Replace `MATERIAL_FINISHES` at `viewer/src/dispatch.ts:478-493` with:

```typescript
const MATERIAL_FINISHES: Record<
  string,
  {
    metalness: number;
    roughness: number;
    bumpiness?: number;
    bump_frequency?: number;
  }
> = {
  matte: { metalness: 0, roughness: 1.0 },
  satin: { metalness: 0.15, roughness: 0.6 },
  glossy: { metalness: 0.3, roughness: 0.15 },
  metallic: { metalness: 1.0, roughness: 0.6 },
  chrome: { metalness: 1.0, roughness: 0.1 },
  origami: { metalness: 0, roughness: 1.0, bumpiness: 0.45, bump_frequency: 4.5 },
  glass: { metalness: 0, roughness: 0.05, bumpiness: 0 },
  seaglass: { metalness: 0, roughness: 0.7, bumpiness: 0.45, bump_frequency: 4.0 },
};
```

### 4.2 Representation and Canvas Render Pass Configuration Matrix

| Parameter / Pass | `glass` Finish | `seaglass` Finish | `preset("seaglass")` |
|---|---|---|---|
| `metalness` | `0.0` | `0.0` | `0.0` |
| `roughness` | `0.05` | `0.7` | `0.7` |
| `bumpiness` | `0.0` | `0.45` | `0.45` |
| `bumpFrequency` | representation default | `4.0` | `4.0` |
| `emissive` | `0.0` | `0.0` | `0.0` |
| `color` | user-specified / theme | user-specified / theme | `#73b9a2` (seafoam green) |
| Lighting Rig | `three-point` (recommended) | `three-point` (recommended) | `three-point` (ambient: `0.45`) |
| Ambient Occlusion (`ssao`) | On (`occlusion=True`) | On (`occlusion=True`) | On (`occlusion=True`) |
| Outlines | Off / Subtle (`#4a4a4a`, scale 1) | Off / Subtle (`#4a4a4a`, scale 1) | Off / Subtle |
| Background | Neutral / Light | Neutral / Light | `#ffffff` / `#f6f4eb` |

### 4.3 Opaque Scene Texture (`tColor`) Pipeline Integration Architecture

```
[ DrawPass Execution ]
        │
        ▼
1. renderOpaque() ──────────────────► Writes to colorTarget (Opaque Primitives + Background)
        │                             with depthTextureOpaque attached
        │
        ▼
2. renderDepthTransparent() ────────► Captures transparent front depth in depthTargetTransparent
        │
        ▼
3. Transparent Primitive Pass:
   - renderBlendedTransparent() ────► Binds transparentColorTarget as FBO
        │                             Reads colorTarget.texture (tColor / tOpaqueColor)
        │                             Computes Snell refraction + Fresnel + Roughness diffusion
        │
        ▼
4. PostprocessingPass.render() ─────► Receives:
                                      - tColor: colorTarget.texture
                                      - tTransparentColor: transparentColorTarget.texture
                                      - tDepthOpaque: depthTextureOpaque
                                      - tDepthTransparent: depthTextureTransparent
                                      Composites transparent refractive buffer over scene
```

---

## 5. Verification Method

### 5.1 Vite Bundling Verification
Execute in `viewer/`:
```bash
npm run build
```
**Expected Outcome**:
- `prebuild` executes `npm run sync-molstar`.
- `vite build` completes successfully producing `../src/protean_mcp/static/assets/index-*.js` without TypeScript compiler or bundle resolution errors.

### 5.2 Unit Test Verification in `viewer/`
Execute in `viewer/`:
```bash
npm test
```
**Target Test Updates in `viewer/src/dispatch.test.ts`**:
1. **Capabilities Test (`dispatch.test.ts:523`)**:
   ```typescript
   material_finishes: [
     'chrome',
     'glass',
     'glossy',
     'matte',
     'metallic',
     'origami',
     'satin',
     'seaglass',
   ],
   ```
2. **Finish Application Unit Tests (`dispatch.test.ts:1633+`)**:
   ```typescript
   it('applies glass finish as smooth dielectric transmission material', async () => {
     const plugin: any = withCanvas(fakePlugin());
     const dispatch = await shown(plugin);
     const reply: any = await dispatch('material', { name: 'sele', finish: 'glass' });

     expect(materialOf(plugin).material).toEqual({
       metalness: 0,
       roughness: 0.05,
       bumpiness: 0,
     });
     expect(reply).toMatchObject({
       finish: 'glass',
       metalness: 0,
       roughness: 0.05,
       bumpiness: 0,
     });
   });

   it('applies seaglass finish with frosted roughness and tumbled bump parameters', async () => {
     const plugin: any = withCanvas(fakePlugin());
     const dispatch = await shown(plugin);
     const reply: any = await dispatch('material', { name: 'sele', finish: 'seaglass' });

     expect(materialOf(plugin).material).toEqual({
       metalness: 0,
       roughness: 0.7,
       bumpiness: 0.45,
     });
     expect(materialOf(plugin).bumpFrequency).toBe(4.0);
     expect(reply).toMatchObject({
       finish: 'seaglass',
       metalness: 0,
       roughness: 0.7,
       bumpiness: 0.45,
       bump_frequency: 4.0,
       bump_will_show: true,
     });
   });
   ```

### 5.3 Python MCP Server Verification
Execute from project root:
```bash
uv run pytest tests/test_server.py -k "material or preset"
```
**Expected Outcome**:
- `capabilities()` returns `"glass"` and `"seaglass"` in `material_finishes`.
- `material(finish="glass")` and `material(finish="seaglass")` pass validation and dispatch to the viewer.
- `preset("seaglass")` executes successfully without runtime errors.
