# Frosted Seaglass Diffusion Shader, Roughness Scattering & Bump Texture Blueprint

## 1. Observation

### 1.1 Mol* Shader Architecture and Material Pipeline
- **Shader Chunk Engine (`viewer/node_modules/molstar/lib/mol-gl/shader-code.js:45-98`)**:
  Mol* defines shader chunks in an internal registry `ShaderChunks` containing `apply_fog`, `apply_interior_color`, `apply_light_color`, `assign_material_color`, `color_frag_params`, `common_frag_params`, `common`, etc. Includes are resolved via regex:
  ```javascript
  const reInclude = /^(?!\/\/)\s*#include\s+(\S+)/gm;
  function addIncludes(text) {
      return text.replace(reInclude, (_, p1) => {
          const chunk = ShaderChunks[p1];
          if (!chunk) throw new Error(`empty chunk, '${p1}'`);
          return chunk;
      });
  }
  ```
- **Existing Bump Mapping Implementation (`viewer/node_modules/molstar/lib/mol-gl/shader/chunks/apply-light-color.glsl.js:25-29`)**:
  ```glsl
  #ifdef bumpEnabled
      if (uBumpFrequency > 0.0 && uBumpAmplitude > 0.0 && bumpiness > 0.0) {
          normal = perturbNormal(-vViewPosition, normal, fbm(vModelPosition * uBumpFrequency), (uBumpAmplitude * bumpiness) / uBumpFrequency);
      }
  #endif
  ```
- **Mikkelsen Screen-Space Bump Perturbation (`viewer/node_modules/molstar/lib/mol-gl/shader/chunks/common-frag-params.glsl.js:108-122`)**:
  ```glsl
  // "Bump Mapping Unparametrized Surfaces on the GPU" Morten S. Mikkelsen
  vec3 perturbNormal(in vec3 position, in vec3 normal, in float height, in float scale) {
      vec3 sigmaS = dFdx(position);
      vec3 sigmaT = dFdy(position);

      vec3 r1 = cross(sigmaT, normal);
      vec3 r2 = cross(normal, sigmaS);
      float det = dot(sigmaS, r1);
      if (det == 0.0) return normal;

      float bs = dFdx(height);
      float bt = dFdy(height);

      vec3 surfGrad = sign(det) * (bs * r1 + bt * r2);
      return normalize(abs(det) * normal - scale * surfGrad);
  }
  ```
- **3-Octave Fractional Brownian Motion (`viewer/node_modules/molstar/lib/mol-gl/shader/chunks/common.glsl.js:300-309`)**:
  ```glsl
  float fbm(in vec3 p) {
      float f = 0.0;
      f += 0.5 * noise(p);
      p *= 2.01;
      f += 0.25 * noise(p);
      p *= 2.02;
      f += 0.125 * noise(p);
      return f;
  }
  ```
- **Physical Material Shading & Microfacet BRDF (`viewer/node_modules/molstar/lib/mol-gl/shader/chunks/light-frag-params.glsl.js:73-84`)**:
  Mol* evaluates specular highlights using Cook-Torrance BRDF with GGX distribution (`D_GGX`), Smith correlated visibility (`V_GGX_SmithCorrelated`), and Schlick Fresnel (`F_Schlick`).
- **Material Parameter Registry (`viewer/src/dispatch.ts:478-493`)**:
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

---

## 2. Logic Chain

### 2.1 Optical Physics of Tumbled Beach Seaglass
1. **Clear vs. Frosted Transmission**:
   - Clear glass (`glass`: `roughness: 0.05`, `bumpiness: 0.0`) behaves as an optically smooth dielectric interface: transmitted light follows a single refracted Snell trajectory.
   - Frosted seaglass (`seaglass`: `roughness: 0.70`, `bumpiness: 0.45`, `bump_frequency: 4.0`) has undergone decades of abrasive sand tumbling, creating two coupled optical phenomena:
     1. **Mesoscopic Pitting (Bumpiness & FBM)**: Irregular undulating surface facets bend the macroscopic surface normal $\vec{N}_{\text{perturbed}}$, creating undulating refractive displacement.
     2. **Microfacet Roughness Scattering (Roughness & Diffusion Kernel)**: Sub-wavelength abrasive etching scatters incoming transmitted rays across a wide solid angle (rough microfacet BTDF), blurring the view of whatever lies behind the glass.

2. **Screen-Space Multi-Tap Diffusion Architecture**:
   - When transparent geometry is drawn in Mol*, the opaque scene is stored in `tSceneColor` (the bound `colorTarget.texture`), and its depth is in `tDepth`.
   - The base refraction UV offset is calculated from the perturbed view-space normal:
     $$\Delta uv_{\text{refract}} = \vec{N}_{xy} \times k_{\text{refract}} \times \left(1.0 - \frac{1.0}{\eta_{\text{glass}}}\right) \times \frac{1.0}{\max(|viewZ|, 1.0)}$$
     where $\eta_{\text{glass}} = 1.52$ and $k_{\text{refract}} \approx 0.045$.
   - **Diffusion Radius Scaling**:
     The spatial spread of the frosted blur kernel scales with the square of the surface roughness (Disney/GGX $\alpha = \text{roughness}^2$):
     $$R_{\text{diffuse}} = \text{roughness}^2 \times uDiffusionSpread \times \frac{1.0}{\max(|viewZ|, 1.0)}$$
     For `roughness = 0.05` (clear glass), $R_{\text{diffuse}} \approx 0.0001$ (negligible/sharp).
     For `roughness = 0.70` (seaglass), $R_{\text{diffuse}} \approx 0.025$ (broad, creamy frosted blur).

3. **Multi-Tap Sampling Kernel: 12-Tap Vogel Golden Angle Spiral**:
   - Uniform disc distribution using Fermat's spiral with golden angle $\phi = \pi(3 - \sqrt{5}) \approx 2.39996323\text{ rad}$:
     $$r_i = \sqrt{\frac{i + 0.5}{12}}, \quad \theta_i = i \times 2.39996323$$
   - Precomputing the 12 offsets into a constant GLSL `vec2[12]` table eliminates runtime trigonometric calls.
   - Weighted by 2D Gaussian attenuation $w_i = \exp(-2.0 \cdot r_i^2)$:
     $$\text{SampledColor} = \frac{\sum_{i=0}^{11} w_i \cdot \text{sample}(tSceneColor, uv + \Delta uv + \vec{K}_i \cdot R_{\text{diffuse}})}{\sum_{i=0}^{11} w_i}$$
   - **Screen-Space Interleaved Dither**:
     Applying a subtle per-pixel rotation to $\vec{K}_i$ using screen-space hash $\text{hash}(\text{gl\_FragCoord.xy})$ breaks up sampling rings and adds the characteristic fine sandblasted micro-grain of tumbled glass.

4. **Integration with `perturbNormal` and `fbm`**:
   - In `apply-light-color`, normal perturbation must occur **before** computing refraction and diffusion:
     $$\vec{N}_{\text{perturbed}} = \text{perturbNormal}(-vViewPosition, \vec{N}_{\text{geom}}, \text{fbm}(vModelPosition \times uBumpFrequency), \frac{uBumpAmplitude \times \text{bumpiness}}{uBumpFrequency})$$
   - $\vec{N}_{\text{perturbed}}$ is passed to the transmission kernel, causing refracted structures to undulate organically through the tumbled facets.

5. **Transmitted Color Filtering: Beer-Lambert Spectral Absorption Tint**:
   - Beer-Lambert absorption law: $T(\lambda, d) = \exp(-\sigma_a(\lambda) \cdot d_{\text{eff}})$.
   - Path length approximation from viewing angle:
     $$d_{\text{eff}} = \text{clamp}\left(\frac{1.0}{\max(\vec{N} \cdot \vec{V}, 0.25)}, 1.0, 3.5\right)$$
   - At silhouette edges ($\vec{N} \cdot \vec{V} \to 0$), the ray travels through more glass, producing deeper emerald/seafoam absorption saturation.
   - Transmittance filtering:
     $$C_{\text{transmitted}} = \text{SampledColor} \times (C_{\text{base}})^{d_{\text{eff}} \times 0.65} + C_{\text{base}} \times uAmbientColor \times \text{roughness} \times 0.25$$

6. **Full Physical Compositing**:
   - Schlick Fresnel reflectance: $F = F_0 + (1 - F_0)(1 - \vec{N} \cdot \vec{V})^5$, with $F_0 = 0.04$.
   - GGX Specular Highlight: evaluated via Mol*'s `BRDF_GGX`.
   - Surface Microfacet Sheen: $C_{\text{sheen}} = C_{\text{base}} \times (\text{irradiance} / \pi) \times \text{roughness} \times 0.15$.
   - Final composite:
     $$C_{\text{final}} = (1.0 - F) \times C_{\text{transmitted}} + F \times C_{\text{specular}} + C_{\text{sheen}}$$

---

## 3. Caveats

1. **Screen-Space Background Limitation**:
   - Screen-space refraction reconstructs transmitted rays by sampling the opaque framebuffer. If multiple frosted transparent objects overlap, the frontmost surface diffuses the opaque scene rather than recursively blurring the interior transparent layer. For macromolecular visualization, this is standard, visually stunning, and avoids $O(N^2)$ render passes.
2. **Foreground Occlusion Artifacts**:
   - If a refracted ray offset $uv + \Delta uv$ lands on a foreground structure in front of the glass, sampling it directly would create false foreground bleeding. The shader compares `getDepth(uv + \Delta uv)` with `fragmentDepth`; if the sample is in front of the fragment ($z_{\text{sample}} < z_{\text{frag}} - 0.002$), the offset is clamped.
3. **Loop Bounds in GLSL ES 1.00 (WebGL1 vs WebGL2)**:
   - GLSL ES 1.00 requires constant loop indices. The 12-tap sampling loop uses a fixed constant count `const int NUM_TAPS = 12` and `#pragma unroll_loop_start` to guarantee compatibility across WebGL1 and WebGL2.

---

## 4. Conclusion

### 4.1 Material Finish Parameter Configurations
Add to `MATERIAL_FINISHES` in `viewer/src/dispatch.ts`:
```typescript
glass: {
  metalness: 0,
  roughness: 0.05,
  bumpiness: 0,
},
seaglass: {
  metalness: 0,
  roughness: 0.70,
  bumpiness: 0.45,
  bump_frequency: 4.0,
},
```

### 4.2 Complete GLSL Transmission & Frosted Diffusion Kernel Implementation
Below is the complete GLSL chunk `transmission_frag` designed for injection into Mol*'s shader pipeline (`viewer/src/transmission-shaders.ts`):

```glsl
// transmission-shaders.ts
export const transmission_frag = `
#ifdef dTransmission
    uniform sampler2D tSceneColor;
    uniform vec2 uDrawingBufferSize;
    uniform float uRefractionRatio; // default: 1.0 / 1.52 = 0.6579
    uniform float uDiffusionSpread;  // default: 0.04

    // 12-tap Vogel Golden Angle Spiral Kernel
    const int DIFFUSE_TAPS = 12;
    const vec2 DIFFUSE_KERNEL[12] = vec2[12](
        vec2( 0.146,  0.146),
        vec2(-0.315,  0.134),
        vec2( 0.228, -0.386),
        vec2( 0.081,  0.537),
        vec2(-0.479, -0.352),
        vec2( 0.601, -0.043),
        vec2(-0.301,  0.609),
        vec2(-0.245, -0.686),
        vec2( 0.749,  0.221),
        vec2(-0.699,  0.373),
        vec2( 0.194, -0.842),
        vec2( 0.655, -0.638)
    );

    const float DIFFUSE_WEIGHTS[12] = float[12](
        0.920, 0.779, 0.659, 0.556, 0.472, 0.401,
        0.339, 0.286, 0.242, 0.204, 0.174, 0.147
    );
    const float DIFFUSE_WEIGHT_SUM = 5.179;

    // Fast screen-space hash for tactile grain
    float grainHash(vec2 p) {
        return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
    }

    vec3 sampleTransmittedFrosted(
        in vec2 baseUV,
        in vec2 refractOffset,
        in float rough,
        in float viewDist,
        in vec3 baseColor,
        in float fragZ
    ) {
        vec2 uv = clamp(baseUV + refractOffset, 0.001, 0.999);

        // Check depth occlusion: avoid sampling foreground occluders
        float sampleDepth = getDepth(uv);
        if (sampleDepth < fragZ - 0.002) {
            uv = baseUV; // fallback to unrefracted UV
        }

        // Optical path thickness estimation for Beer-Lambert absorption
        float nDotV = saturate(dot(normal, normalize(vViewPosition)));
        float pathThickness = clamp(1.0 / max(nDotV, 0.25), 1.0, 3.5);
        vec3 absorptionTint = pow(max(baseColor, vec3(0.02)), vec3(pathThickness * 0.75));

        // When roughness is very low (clear glass), take single chromatic tap
        if (rough < 0.08) {
            vec2 dispR = refractOffset * 1.03;
            vec2 dispG = refractOffset;
            vec2 dispB = refractOffset * 0.97;
            vec3 sampled;
            sampled.r = texture2D(tSceneColor, clamp(baseUV + dispR, 0.001, 0.999)).r;
            sampled.g = texture2D(tSceneColor, clamp(baseUV + dispG, 0.001, 0.999)).g;
            sampled.b = texture2D(tSceneColor, clamp(baseUV + dispB, 0.001, 0.999)).b;
            return sampled * absorptionTint;
        }

        // Frosted Seaglass Multi-Tap Scattering Loop
        float spreadRadius = (rough * rough) * uDiffusionSpread / max(viewDist, 1.0);
        float aspect = uDrawingBufferSize.x / max(uDrawingBufferSize.y, 1.0);
        vec2 scale = vec2(spreadRadius / aspect, spreadRadius);

        // Random jitter rotation per pixel
        float rotAngle = grainHash(gl_FragCoord.xy) * TWO_PI;
        float cosA = cos(rotAngle);
        float sinA = sin(rotAngle);
        mat2 rotMat = mat2(cosA, -sinA, sinA, cosA);

        vec3 accumColor = vec3(0.0);
        #pragma unroll_loop_start
        for (int i = 0; i < 12; ++i) {
            vec2 tapOffset = (rotMat * DIFFUSE_KERNEL[i]) * scale;
            vec2 tapUV = clamp(uv + tapOffset, 0.001, 0.999);
            vec3 tapColor = texture2D(tSceneColor, tapUV).rgb;
            accumColor += tapColor * DIFFUSE_WEIGHTS[i];
        }
        #pragma unroll_loop_end

        vec3 scatteredBg = accumColor / DIFFUSE_WEIGHT_SUM;

        // Subsurface forward scattering body light
        vec3 internalScatter = baseColor * uAmbientColor * rough * 0.25;

        return (scatteredBg * absorptionTint) + internalScatter;
    }
#endif
`;
```

### 4.3 Python Server Preset Integration (`src/protean_mcp/server.py`)
```python
async def _preset_seaglass(target: str, handle: str) -> list[str]:
    """Frosted tumbled beach glass: diffused transmission, seafoam tint (#73b9a2), and tactile grain."""
    return [
        await _run(background, color="#ffffff", gradient="off"),
        await _run(lighting, rig="three-point", ambient=0.45),
        *await _set_effects(occlusion=True, shadow=False),
        await _run(color, color="#73b9a2", name=handle),
        await _run(material, finish="seaglass", name=handle),
    ]

_VIEWS["seaglass"] = _View(
    selection="polymer",
    representation="cartoon",
    color="uniform",
    style=_preset_seaglass,
)
```

---

## 5. Verification Method

### 5.1 Viewer Build Verification
Execute in `viewer/`:
```bash
npm run build
```
Verify that Vite compiles `viewer/src/` into `src/protean_mcp/static/` without GLSL syntax errors, TypeScript errors, or bundle warning regressions.

### 5.2 Unit Test Verification
Execute in `viewer/`:
```bash
npm test
```
Verify all Vitest tests in `viewer/src/dispatch.test.ts` pass, confirming:
- `MATERIAL_FINISHES` contains `glass` and `seaglass`.
- `dispatch('material', { finish: 'seaglass' })` correctly applies `roughness: 0.70`, `bumpiness: 0.45`, `bump_frequency: 4.0`.
- Capabilities reporting includes `glass` and `seaglass`.

### 5.3 Differential Snapshot & Visual Quality Verification
Execute from project root:
```bash
uv run pytest tests/test_glass_differential.py -v
```
Inspect generated snapshots (`tests/snapshots/1ubq_seaglass_preset_snapshot.png`):
- Background structure lines behind seaglass must exhibit soft, diffused Gaussian-like blurring rather than sharp lines.
- Tumbled facets from FBM bumpiness (`bumpiness: 0.45`, `bump_frequency: 4.0`) are visible as velvety surface highlights.
- Silhouette edges exhibit Beer-Lambert color deepening in the seafoam green palette (`#73b9a2`).
