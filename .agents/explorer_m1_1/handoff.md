# WebGL Refraction GLSL Shader Mathematics & Snell Distortion Blueprint

## 1. Observation

### 1.1 Mol* Shader Chunks and Lighting Architecture
- **GLSL Language & Target**:
  Mol* authors all shaders in WebGL 1 / GLSL ES 1.00 (`viewer/src/painterly-shaders.ts:4-9`), automatically converted to `#version 300 es` on WebGL2 (`molstar/lib/mol-gl/shader-code.js:362`).
  Therefore, all custom GLSL must use GLSL ES 1.00 compliant constructs: `texture2D()` rather than `texture()`, `gl_FragColor` instead of custom fragment outputs, `#include common` for depth unpacking (`unpackRGBAToDepth`, `depthToViewZ`), and `dFdx`/`dFdy` under `#extension GL_OES_standard_derivatives : enable`.
- **Lighting and Material Pipeline (`molstar/lib/mol-gl/shader/chunks/apply-light-color.glsl.js:40-109`)**:
  ```glsl
  GeometricContext geometry;
  geometry.position = -vViewPosition;
  geometry.normal = normal;
  geometry.viewDir = normalize(vViewPosition);

  PhysicalMaterial physicalMaterial;
  physicalMaterial.diffuseColor = color.rgb * (1.0 - metalness);
  physicalMaterial.roughness = min(max(roughness, 0.0525), 1.0);
  physicalMaterial.specularColor = mix(vec3(0.04), color.rgb, metalness);
  physicalMaterial.specularF90 = 1.0;
  ```
- **Cook-Torrance GGX & Schlick Fresnel Implementation (`molstar/lib/mol-gl/shader/chunks/light-frag-params.glsl.js:45-52, 73-84`)**:
  ```glsl
  vec3 F_Schlick(const in vec3 f0, const in float f90, const in float dotVH) {
      // Optimized variant (presented by Epic at SIGGRAPH '13)
      float fresnel = exp2((-5.55473 * dotVH - 6.98316) * dotVH);
      return f0 * (1.0 - fresnel) + (f90 * fresnel);
  }
  ```
- **Global Coordinate Uniforms (`molstar/lib/mol-gl/renderable/schema.js:66-120`)**:
  - `uDrawingBufferSize`: `vec2` holding the current framebuffer dimensions in physical pixels $(W, H)$.
  - `vViewPosition`: `vec3` varying holding view-space vertex coordinates (where camera sits at $(0, 0, 0)$ looking down $-Z$).
  - `vNormal`: `vec3` varying holding interpolated view-space normal vector.
  - `uCameraPosition`: `vec3` camera world position.
  - `uNear`, `uFar`: camera near and far clipping distances.
  - `uIsOrtho`: `float` flag ($1.0$ for orthographic projection, $0.0$ for perspective).

### 1.2 Rendering Passes and Color Buffers
- **Buffer Flow (`molstar/lib/mol-canvas3d/passes/draw.js:155-345`)**:
  - Opaque primitives are rasterized first into `drawPass.colorTarget` (`tColor`) with `this.depthTextureOpaque` attached.
  - Transparent primitives are rasterized next into `drawPass.transparentColorTarget`.
  - `PostprocessingPass` (`molstar/lib/mol-canvas3d/passes/postprocessing.js:34-42`) receives `tColor` (opaque background) and `tTransparentColor` (transparent front layer) and composites them:
    ```glsl
    color = transparentColor + color * (1.0 - alpha);
    ```
- **Protean Material Finish Registry (`viewer/src/dispatch.ts:478-493`)**:
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

---

## 2. Logic Chain

### 2.1 Optical Model for Clear Dielectric Glass
1. **Refractive Index & Relative Optical Density**:
   - Dielectric crown glass has refractive index $n_{\text{glass}} = 1.50$.
   - Surrounding medium is air ($n_{\text{air}} = 1.00$).
   - Relative index of refraction ratio is:
     $$\eta = \frac{n_{\text{air}}}{n_{\text{glass}}} = \frac{1.00}{1.50} \approx 0.6667$$

2. **Geometric View Vectors**:
   - Fragment view direction from surface to eye: $\vec{V} = \text{normalize}(vViewPosition) = -\text{normalize}(P_{\text{view}})$.
   - Surface unit normal: $\vec{N} = \text{normalize}(normal)$.
   - Face orientation check: If $\vec{N} \cdot \vec{V} < 0$, invert $\vec{N} = -\vec{N}$ to ensure consistent orientation.
   - Cosine of incidence angle: $\cos \theta_i = \text{clamp}(\vec{N} \cdot \vec{V}, 0.0, 1.0)$.

3. **3D Snell Refraction Vector $\vec{R}_{\text{refr}}$**:
   $$\vec{I} = -\vec{V} \quad (\text{incident ray direction})$$
   $$k = 1.0 - \eta^2 \cdot (1.0 - (\vec{N} \cdot \vec{V})^2)$$
   - If $k < 0.0$, total internal reflection (TIR) occurs: $\vec{R}_{\text{refr}} = \text{reflect}(-\vec{V}, \vec{N})$.
   - Otherwise, $\vec{R}_{\text{refr}} = \eta \cdot (-\vec{V}) + (\eta \cdot (\vec{N} \cdot \vec{V}) - \sqrt{k}) \cdot \vec{N}$.
   - In GLSL: `vec3 R_refr = refract(-V, N, eta);`

4. **Screen-Space UV Coordinate Projection & Distortion**:
   - Base screen UV coordinate:
     $$\vec{uv}_0 = \frac{\text{gl\_FragCoord.xy}}{\text{uDrawingBufferSize}}$$
   - Perspective depth normalization: Let $Z_v = \text{max}(|vViewPosition.z|, 1.0)$.
   - As the camera moves further from the molecule, linear depth $Z_v$ increases, diminishing the angular screen deflection.
   - Viewport aspect ratio correction factor: $\vec{a} = \begin{pmatrix} 1.0 \\ \frac{\text{uDrawingBufferSize}.x}{\text{uDrawingBufferSize}.y} \end{pmatrix}$.
   - Screen-space deflection vector:
     $$\Delta \vec{uv} = \frac{\vec{R}_{\text{refr}}.xy \cdot uRefractionScale}{Z_v} \times \begin{pmatrix} 1.0 \\ \frac{\text{uDrawingBufferSize}.x}{\text{uDrawingBufferSize}.y} \end{pmatrix}$$
   - When viewing flat on ($\vec{N} \parallel \vec{V}$), $\vec{R}_{\text{refr}}.xy = (0, 0)$, yielding zero distortion ($\Delta \vec{uv} = 0$).
   - At silhouette edges ($\vec{N} \perp \vec{V}$), $\vec{R}_{\text{refr}}.xy$ reaches maximum lateral deflection, compressing background structures behind the curved glass boundary.

5. **Schlick Fresnel Reflection**:
   - Characteristic dielectric reflectance at normal incidence for air-glass interface:
     $$F_0 = \left(\frac{n_{\text{glass}} - n_{\text{air}}}{n_{\text{glass}} + n_{\text{air}}}\right)^2 = \left(\frac{1.5 - 1.0}{1.5 + 1.0}\right)^2 = (0.2)^2 = 0.04$$
   - Schlick approximation:
     $$F(\theta_i) = F_0 + (1.0 - F_0) \cdot (1.0 - \vec{N} \cdot \vec{V})^5$$
   - Using Epic's exponential formula:
     ```glsl
     float fresnel = exp2((-5.55473 * dotNV - 6.98316) * dotNV);
     float F = 0.04 * (1.0 - fresnel) + fresnel;
     ```
   - At normal incidence ($\vec{N} \cdot \vec{V} = 1.0$): $F = 0.04$ ($96\%$ transmitted, $4\%$ reflected).
   - At grazing angles ($\vec{N} \cdot \vec{V} \to 0.0$): $F \to 1.00$ ($100\%$ specular reflection), producing glowing silhouette rims.

6. **Prismatic Chromatic Dispersion**:
   - According to Cauchy's dispersion formula, optical glass has wavelength-dependent refractive indices: $n(\lambda) = A + \frac{B}{\lambda^2}$.
   - Blue light bends more than red light: $n_{\text{blue}} > n_{\text{green}} > n_{\text{red}}$.
   - Dispersion coefficient $\delta = 0.018$:
     - $\eta_{\text{red}} = \frac{1.0}{1.5 - \delta} \approx 0.6748$ (less bending, ray stays closer to center)
     - $\eta_{\text{green}} = \frac{1.0}{1.5} \approx 0.6667$ (nominal Snell ray)
     - $\eta_{\text{blue}} = \frac{1.0}{1.5 + \delta} \approx 0.6588$ (more bending, larger offset)
   - Screen-space sampling positions:
     $$\vec{uv}_R = \text{clamp}(\vec{uv}_0 + \Delta \vec{uv} \cdot (1.0 - \delta), 0.001, 0.999)$$
     $$\vec{uv}_G = \text{clamp}(\vec{uv}_0 + \Delta \vec{uv}, 0.001, 0.999)$$
     $$\vec{uv}_B = \text{clamp}(\vec{uv}_0 + \Delta \vec{uv} \cdot (1.0 + \delta), 0.001, 0.999)$$
   - Sampling background buffer `tColor`:
     $$C_{\text{trans}} = \begin{pmatrix} \text{texture2D}(tColor, \vec{uv}_R).r \\ \text{texture2D}(tColor, \vec{uv}_G).g \\ \text{texture2D}(tColor, \vec{uv}_B).b \end{pmatrix}$$

7. **Transmission Filtering & Specular Compositing**:
   - Transmitted light is tinted by the material base color $C_{\text{base}}$:
     $$C_{\text{tinted}} = C_{\text{trans}} \times \text{mix}(\vec{1}, C_{\text{base}}, uTransmissionTint)$$
   - Specular highlight $C_{\text{spec}}$ is computed via Cook-Torrance microfacet specular BRDF (`BRDF_GGX` in `chunks/light-frag-params.glsl.js`).
   - Final lit color combines transmitted background with Fresnel specular reflection:
     $$C_{\text{final}} = C_{\text{tinted}} \cdot (1.0 - F) + C_{\text{spec}}$$

---

### 2.2 Complete GLSL Implementation Chunk (`refraction.glsl`)

```glsl
// =============================================================================
// WebGL Refraction GLSL Shader: Snell Distortion, Schlick Fresnel, & Dispersion
// =============================================================================

#ifdef dRefraction

uniform sampler2D tSceneColor;
uniform vec2 uDrawingBufferSize;
uniform float uRefractionStrength;  // Default: 0.08
uniform float uDispersionSpread;    // Default: 0.02
uniform float uGlassIOR;            // Default: 1.50
uniform float uGlassTintStrength;   // Default: 0.0 (clear glass)

vec2 clampScreenUV(const in vec2 uv) {
    return clamp(uv, vec2(0.001), vec2(0.999));
}

/**
 * Computes Snell refraction deflection vector in screen UV space.
 */
vec2 getSnellRefractionOffset(
    const in vec3 viewDir,
    const in vec3 normal,
    const in float viewDepth,
    const in float ior,
    const in vec2 bufferSize,
    const in float strength
) {
    float eta = 1.0 / max(ior, 1.0001);
    
    // Invert normal if facing away from camera
    vec3 N = normal;
    if (dot(N, viewDir) < 0.0) N = -N;
    
    // 3D Snell Refraction
    vec3 R = refract(-viewDir, N, eta);
    if (length(R) == 0.0) {
        R = reflect(-viewDir, N);
    }
    
    // Aspect ratio correction: ensure isotropic screen-space distortion
    vec2 aspect = vec2(1.0, bufferSize.x / max(bufferSize.y, 1.0));
    
    // Perspective depth normalization: closer objects distort more on screen
    float zDist = max(viewDepth, 1.0);
    
    // Screen-space deflection offset
    return (R.xy * strength / zDist) * aspect;
}

/**
 * Calculates Schlick Fresnel reflectance with dielectric F0 = 0.04.
 */
float getDielectricFresnel(const in vec3 viewDir, const in vec3 normal) {
    float dotNV = clamp(abs(dot(normal, viewDir)), 0.0, 1.0);
    // Optimized Epic Games approximation
    float fresnelExp = exp2((-5.55473 * dotNV - 6.98316) * dotNV);
    const float F0 = 0.04;
    return F0 * (1.0 - fresnelExp) + fresnelExp;
}

/**
 * Evaluates spectral 3-tap chromatic dispersion sampling of scene background.
 */
vec3 sampleDispersedRefraction(
    const in sampler2D sceneTex,
    const in vec2 baseUV,
    const in vec2 baseOffset,
    const in float dispersion
) {
    vec2 uvR = clampScreenUV(baseUV + baseOffset * (1.0 - dispersion));
    vec2 uvG = clampScreenUV(baseUV + baseOffset);
    vec2 uvB = clampScreenUV(baseUV + baseOffset * (1.0 + dispersion));
    
    float r = texture2D(sceneTex, uvR).r;
    float g = texture2D(sceneTex, uvG).g;
    float b = texture2D(sceneTex, uvB).b;
    
    return vec3(r, g, b);
}

/**
 * Full Glass Transmission & Shading Composite.
 */
vec4 evaluateGlassFragment(
    const in vec3 fragPosView,
    const in vec3 normalView,
    const in vec3 materialColor,
    const in vec3 specularLight
) {
    vec2 screenUV = gl_FragCoord.xy / uDrawingBufferSize;
    vec3 viewDir = normalize(-fragPosView);
    float viewDepth = abs(fragPosView.z);
    
    // 1. Calculate Snell refraction offset in screen space
    vec2 refrOffset = getSnellRefractionOffset(
        viewDir,
        normalView,
        viewDepth,
        uGlassIOR,
        uDrawingBufferSize,
        uRefractionStrength
    );
    
    // 2. Sample background with chromatic dispersion
    vec3 transmitted = sampleDispersedRefraction(
        tSceneColor,
        screenUV,
        refrOffset,
        uDispersionSpread
    );
    
    // 3. Apply optional glass material tint
    vec3 tintedTransmitted = transmitted * mix(vec3(1.0), materialColor, uGlassTintStrength);
    
    // 4. Calculate Fresnel reflectance
    float F = getDielectricFresnel(viewDir, normalView);
    
    // 5. Composite transmitted refraction with specular highlight
    vec3 finalRGB = tintedTransmitted * (1.0 - F) + specularLight;
    
    // Maintain slight physical alpha opacity for boundary definition
    float alpha = mix(0.92, 1.0, F);
    
    return vec4(finalRGB, alpha);
}

#endif
```

---

## 3. Caveats

1. **Single-Layer Screen-Space Background**:
   - Screen-space refraction refracts the opaque background and any already-rendered objects captured in `tColor`. If multiple refractive transparent surfaces overlap in depth, the front glass surface refracts the opaque scene behind rather than recursively tracing internal bounces. This is the standard WebGL real-time transmission model and produces visually compelling results with zero performance penalty.
2. **Viewport Boundary Fallback**:
   - When a fragment near the edge of the screen produces a large refraction offset, `clampScreenUV` clamps the coordinate to $[0.001, 0.999]$ to avoid sampling outside texture bounds or invalid black margins.
3. **Roughness Interaction**:
   - For perfectly clear glass (`finish="glass"`, `roughness: 0.05`), the 3-tap spectral dispersion produces crisp, pristine optical refraction. For frosted seaglass (`finish="seaglass"`, `roughness: 0.7`), the sampling kernel expands to a multi-tap Poisson disk kernel (handled in `explorer_m1_2`).

---

## 4. Conclusion

- **Refraction Math**: The physical model uses Snell's law with $n_{\text{glass}} = 1.50$, $\eta = 0.6667$, converted to screen UV deflection via perspective depth scaling $\frac{1}{Z_v}$ and aspect ratio normalization.
- **Fresnel Optics**: Dielectric Schlick formula with $F_0 = 0.04$ generates realistic edge luminance and total reflection at grazing angles while maintaining $96\%$ transmission at center.
- **Chromatic Dispersion**: 3-tap spectral separation ($\delta = 0.02$) splits Red, Green, and Blue rays, producing authentic rainbow prismatic fringes along curved molecular contours.
- **Code Modifications Blueprint**:
  1. `viewer/src/dispatch.ts`: Add `glass` (`metalness: 0, roughness: 0.05, bumpiness: 0`) and `seaglass` (`metalness: 0, roughness: 0.7, bumpiness: 0.45, bump_frequency: 4.0`) to `MATERIAL_FINISHES`.
  2. `viewer/src/refraction-shaders.ts`: Implement `refraction_frag` GLSL module with Snell math, Schlick Fresnel, and chromatic dispersion taps.
  3. `viewer/src/refraction.ts`: Wrap Mol* render passes or bind `tColor` target for transparent rendering.

---

## 5. Verification Method

### 5.1 Math & Unit Test Verification
1. Run Vitest suite in `viewer/`:
   ```bash
   npm test
   ```
   Verify that dispatch finish validation passes for `glass` and `seaglass`.

### 5.2 Build Verification
1. Build viewer bundle in `viewer/`:
   ```bash
   npm run build
   ```
   Verify clean Vite compilation without GLSL syntax or TypeScript type errors.

### 5.3 Differential Snapshot & Python API Verification
1. Run server tests:
   ```bash
   uv run pytest tests/test_server.py -k "material"
   ```
2. Run differential browser rendering tests:
   ```bash
   PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_render_differential.py -k "finish"
   ```
   Verify differential pixel assertions confirm distinct optical transmission and roughness ordering for `glass`.
