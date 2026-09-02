/**
 * Refraction & Frosted Transmission Shaders in Mol* / WebGL.
 *
 * Authors shaders in GLSL ES 1.00 compliant syntax:
 * - Snell's law screen-space refraction with perspective depth scaling and isotropic aspect ratio correction.
 * - Dielectric Schlick Fresnel factor (F0 = 0.04).
 * - 3-tap spectral chromatic dispersion for clear glass.
 * - 12-tap Vogel Golden Angle spiral kernel with Gaussian weights and screen-space dither for frosted seaglass.
 * - Procedural 3-octave FBM surface normal perturbation for tumbled beach glass facets.
 * - Transmitted color filtering / Beer-Lambert absorption tinting.
 */

export const refraction_composite_frag = `
precision highp float;
precision highp sampler2D;

uniform sampler2D tColor;
uniform sampler2D tTransparentColor;
uniform sampler2D tDepthOpaque;
uniform sampler2D tDepthTransparent;
uniform vec2 uTexSize;
uniform float uNear;
uniform float uFar;
uniform float uIsOrtho;
uniform float uGlassIOR;
uniform float uRefractionStrength;
uniform float uDispersionSpread;
uniform float uDiffusionSpread;
uniform float uRoughness;
uniform float uBumpiness;
uniform float uBumpFrequency;
uniform float uAbsorptionStrength;
uniform float uFresnelF0;

#if __VERSION__ < 300
#extension GL_OES_standard_derivatives : enable
#endif
#include common

float getDepthOpaque(const in vec2 coords) {
    #ifdef depthTextureSupport
        return texture2D(tDepthOpaque, coords).r;
    #else
        return unpackRGBAToDepth(texture2D(tDepthOpaque, coords));
    #endif
}

float getDepthTransparent(const in vec2 coords) {
    #ifdef depthTextureSupport
        return texture2D(tDepthTransparent, coords).r;
    #else
        return unpackRGBAToDepth(texture2D(tDepthTransparent, coords));
    #endif
}

float getViewZ(const in float depth) {
    if (uIsOrtho > 0.5) {
        return orthographicDepthToViewZ(depth, uNear, uFar);
    } else {
        return perspectiveDepthToViewZ(depth, uNear, uFar);
    }
}

vec2 clampScreenUV(const in vec2 uv) {
    return clamp(uv, vec2(0.001), vec2(0.999));
}

// Fast screen-space hash for tactile grain / interleaved dither
float grainHash(const in vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

// 3D Procedural Value Noise and 3-Octave FBM
float hash31(const in vec3 p) {
    vec3 p3 = fract(p * 0.3183099 + 0.1);
    p3 *= 17.0;
    return fract(p3.x * p3.y * p3.z * (p3.x + p3.y + p3.z));
}

float noise3(const in vec3 p) {
    vec3 i = floor(p);
    vec3 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
        mix(
            mix(hash31(i + vec3(0.0, 0.0, 0.0)), hash31(i + vec3(1.0, 0.0, 0.0)), f.x),
            mix(hash31(i + vec3(0.0, 1.0, 0.0)), hash31(i + vec3(1.0, 1.0, 0.0)), f.x),
            f.y
        ),
        mix(
            mix(hash31(i + vec3(0.0, 0.0, 1.0)), hash31(i + vec3(1.0, 0.0, 1.0)), f.x),
            mix(hash31(i + vec3(0.0, 1.0, 1.0)), hash31(i + vec3(1.0, 1.0, 1.0)), f.x),
            f.y
        ),
        f.z
    );
}

float fbm3(const in vec3 p) {
    float f = 0.0;
    f += 0.500 * noise3(p);
    f += 0.250 * noise3(p * 2.02);
    f += 0.125 * noise3(p * 4.07);
    return f;
}

// 12-tap Vogel Golden Angle Spiral Kernel
const int DIFFUSE_TAPS = 12;
vec2 getDiffuseKernel(int i) {
    if (i == 0) return vec2( 0.146,  0.146);
    if (i == 1) return vec2(-0.315,  0.134);
    if (i == 2) return vec2( 0.228, -0.386);
    if (i == 3) return vec2( 0.081,  0.537);
    if (i == 4) return vec2(-0.479, -0.352);
    if (i == 5) return vec2( 0.601, -0.043);
    if (i == 6) return vec2(-0.301,  0.609);
    if (i == 7) return vec2(-0.245, -0.686);
    if (i == 8) return vec2( 0.749,  0.221);
    if (i == 9) return vec2(-0.699,  0.373);
    if (i == 10) return vec2( 0.194, -0.842);
    return vec2( 0.655, -0.638);
}

float getDiffuseWeight(int i) {
    if (i == 0) return 0.920;
    if (i == 1) return 0.779;
    if (i == 2) return 0.659;
    if (i == 3) return 0.556;
    if (i == 4) return 0.472;
    if (i == 5) return 0.401;
    if (i == 6) return 0.339;
    if (i == 7) return 0.286;
    if (i == 8) return 0.242;
    if (i == 9) return 0.204;
    if (i == 10) return 0.174;
    return 0.147;
}
const float DIFFUSE_WEIGHT_SUM = 5.179;

/**
 * Computes Snell refraction deflection in screen UV space.
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
    vec3 N = normal;
    if (dot(N, viewDir) < 0.0) N = -N;

    vec3 R = refract(-viewDir, N, eta);
    if (length(R) == 0.0) {
        R = reflect(-viewDir, N);
    }

    vec2 aspect = vec2(1.0, bufferSize.x / max(bufferSize.y, 1.0));
    float zDist = max(viewDepth, 1.0);
    return (R.xy * strength / zDist) * aspect;
}

/**
 * Calculates dielectric Schlick Fresnel reflectance.
 */
float getDielectricFresnel(const in vec3 viewDir, const in vec3 normal, const in float f0) {
    float dotNV = clamp(abs(dot(normal, viewDir)), 0.0, 1.0);
    float fresnelExp = exp2((-5.55473 * dotNV - 6.98316) * dotNV);
    return f0 * (1.0 - fresnelExp) + fresnelExp;
}

/**
 * 3-tap spectral chromatic dispersion for clear glass.
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
 * 12-tap Vogel Golden Angle spiral scattering for frosted seaglass roughness.
 */
vec3 sampleFrostedScattering(
    const in sampler2D sceneTex,
    const in vec2 baseUV,
    const in vec2 refractOffset,
    const in float rough,
    const in float viewDist,
    const in vec2 bufferSize,
    const in float diffSpread
) {
    vec2 centerUV = clampScreenUV(baseUV + refractOffset);
    float spreadRadius = (rough * rough) * diffSpread / max(viewDist, 1.0);
    float aspect = bufferSize.x / max(bufferSize.y, 1.0);
    vec2 scale = vec2(spreadRadius / aspect, spreadRadius);

    float rotAngle = grainHash(gl_FragCoord.xy) * 6.28318530718;
    float cosA = cos(rotAngle);
    float sinA = sin(rotAngle);
    mat2 rotMat = mat2(cosA, -sinA, sinA, cosA);

    vec3 accum = vec3(0.0);
    #pragma unroll_loop_start
    for (int i = 0; i < 12; ++i) {
        vec2 tapOffset = (rotMat * getDiffuseKernel(i)) * scale;
        vec2 tapUV = clampScreenUV(centerUV + tapOffset);
        accum += texture2D(sceneTex, tapUV).rgb * getDiffuseWeight(i);
    }
    #pragma unroll_loop_end

    return accum / DIFFUSE_WEIGHT_SUM;
}

void main(void) {
    vec2 coords = gl_FragCoord.xy / uTexSize;
    vec4 opaqueColor = texture2D(tColor, coords);
    vec4 transparentColor = texture2D(tTransparentColor, coords);

    float depthTransparent = getDepthTransparent(coords);
    float depthOpaque = getDepthOpaque(coords);

    // If no transparent fragment exists or transparent alpha is 0, pass opaque color through
    if (transparentColor.a <= 0.001 || depthTransparent >= 0.99999994) {
        gl_FragColor = opaqueColor;
        return;
    }

    // Reconstruct linear view Z and view-space position
    float viewZT = abs(getViewZ(depthTransparent));
    vec3 posView = vec3(
        (coords.x * 2.0 - 1.0) * (uTexSize.x / max(uTexSize.y, 1.0)) * viewZT,
        (coords.y * 2.0 - 1.0) * viewZT,
        -viewZT
    );

    // Reconstruct view-space normal from derivatives
    vec3 dX = dFdx(posView);
    vec3 dY = dFdy(posView);
    vec3 geomNormal = normalize(cross(dX, dY));
    if (geomNormal.z < 0.0) geomNormal = -geomNormal;

    vec3 normal = geomNormal;

    // Apply procedural FBM bump perturbation for tumbled beach glass facets
    if (uBumpiness > 0.0 && uBumpFrequency > 0.0) {
        vec3 p = posView * uBumpFrequency * 0.05;
        float eps = 0.02;
        float f0 = fbm3(p);
        float fx = fbm3(p + vec3(eps, 0.0, 0.0)) - f0;
        float fy = fbm3(p + vec3(0.0, eps, 0.0)) - f0;
        vec3 bumpGrad = vec3(fx, fy, 0.0) * (uBumpiness * 2.5 / eps);
        normal = normalize(normal - bumpGrad);
    }

    vec3 viewDir = normalize(-posView);

    // 1. Calculate Snell refraction offset in screen space
    vec2 refrOffset = getSnellRefractionOffset(
        viewDir,
        normal,
        viewZT,
        uGlassIOR,
        uTexSize,
        uRefractionStrength
    );

    // Occlusion check against opaque foreground
    vec2 testUV = clampScreenUV(coords + refrOffset);
    float testOpaqueDepth = getDepthOpaque(testUV);
    if (testOpaqueDepth < depthTransparent - 0.002) {
        refrOffset = vec2(0.0);
    }

    // 2. Transmitted background sampling: 3-tap dispersion for clear glass vs 12-tap Vogel for frosted
    vec3 transmitted;
    if (uRoughness < 0.1) {
        transmitted = sampleDispersedRefraction(tColor, coords, refrOffset, uDispersionSpread);
    } else {
        transmitted = sampleFrostedScattering(tColor, coords, refrOffset, uRoughness, viewZT, uTexSize, uDiffusionSpread);
    }

    // 3. Transmitted color filtering: Beer-Lambert absorption tinting
    float nDotV = clamp(dot(normal, viewDir), 0.0, 1.0);
    float pathThickness = clamp(1.0 / max(nDotV, 0.25), 1.0, 3.5);
    vec3 baseColor = transparentColor.rgb / max(transparentColor.a, 0.001);
    vec3 absorptionTint = pow(max(baseColor, vec3(0.02)), vec3(pathThickness * uAbsorptionStrength));
    vec3 tintedTransmitted = transmitted * absorptionTint;

    // 4. Dielectric Schlick Fresnel reflectance
    float F = getDielectricFresnel(viewDir, normal, uFresnelF0);

    // 5. Specular highlight and surface sheen composite
    vec3 surfaceSpecular = transparentColor.rgb;
    vec3 compositeRGB = mix(tintedTransmitted, surfaceSpecular, F);

    // Blend over background with physical alpha
    float alpha = mix(transparentColor.a, 1.0, F * 0.5);
    vec3 finalColor = compositeRGB * alpha + opaqueColor.rgb * (1.0 - alpha);

    gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0);
    return;
}
`;

/**
 * Optional per-fragment transmission shader chunk for forward passes
 * supporting dTransmission uniforms and internal material evaluations.
 */
export const transmission_chunk_glsl = `
#ifdef dTransmission
    uniform sampler2D tSceneColor;
    uniform vec2 uDrawingBufferSize;
    uniform float uRefractionRatio;
    uniform float uDiffusionSpread;
    uniform float uAbsorptionStrength;

    const int DIFFUSE_TAPS = 12;
    vec2 getDiffuseKernel(int i) {
        if (i == 0) return vec2( 0.146,  0.146);
        if (i == 1) return vec2(-0.315,  0.134);
        if (i == 2) return vec2( 0.228, -0.386);
        if (i == 3) return vec2( 0.081,  0.537);
        if (i == 4) return vec2(-0.479, -0.352);
        if (i == 5) return vec2( 0.601, -0.043);
        if (i == 6) return vec2(-0.301,  0.609);
        if (i == 7) return vec2(-0.245, -0.686);
        if (i == 8) return vec2( 0.749,  0.221);
        if (i == 9) return vec2(-0.699,  0.373);
        if (i == 10) return vec2( 0.194, -0.842);
        return vec2( 0.655, -0.638);
    }
    float getDiffuseWeight(int i) {
        if (i == 0) return 0.920;
        if (i == 1) return 0.779;
        if (i == 2) return 0.659;
        if (i == 3) return 0.556;
        if (i == 4) return 0.472;
        if (i == 5) return 0.401;
        if (i == 6) return 0.339;
        if (i == 7) return 0.286;
        if (i == 8) return 0.242;
        if (i == 9) return 0.204;
        if (i == 10) return 0.174;
        return 0.147;
    }
    const float DIFFUSE_WEIGHT_SUM = 5.179;

    float grainHash(vec2 p) {
        return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
    }

    vec3 evaluateTransmissionRefraction(
        in vec2 screenUV,
        in vec3 normalView,
        in vec3 viewPos,
        in vec3 baseColor,
        in float roughnessVal
    ) {
        vec3 V = normalize(-viewPos);
        vec3 N = normalView;
        if (dot(N, V) < 0.0) N = -N;

        float eta = uRefractionRatio > 0.0 ? uRefractionRatio : 0.6667;
        vec3 R = refract(-V, N, eta);
        if (length(R) == 0.0) R = reflect(-V, N);

        vec2 aspect = vec2(1.0, uDrawingBufferSize.x / max(uDrawingBufferSize.y, 1.0));
        float zDist = max(abs(viewPos.z), 1.0);
        vec2 refrOffset = (R.xy * 0.08 / zDist) * aspect;

        vec2 uv = clamp(screenUV + refrOffset, 0.001, 0.999);

        float nDotV = clamp(dot(N, V), 0.0, 1.0);
        float pathThickness = clamp(1.0 / max(nDotV, 0.25), 1.0, 3.5);
        vec3 absorption = pow(max(baseColor, vec3(0.02)), vec3(pathThickness * uAbsorptionStrength));

        if (roughnessVal < 0.1) {
            vec2 uvR = clamp(screenUV + refrOffset * 1.02, 0.001, 0.999);
            vec2 uvG = clamp(screenUV + refrOffset, 0.001, 0.999);
            vec2 uvB = clamp(screenUV + refrOffset * 0.98, 0.001, 0.999);
            vec3 sampled = vec3(
                texture2D(tSceneColor, uvR).r,
                texture2D(tSceneColor, uvG).g,
                texture2D(tSceneColor, uvB).b
            );
            return sampled * absorption;
        }

        float spread = (roughnessVal * roughnessVal) * uDiffusionSpread / zDist;
        vec2 scale = vec2(spread / aspect.y, spread);

        float rot = grainHash(gl_FragCoord.xy) * 6.28318530718;
        mat2 rotMat = mat2(cos(rot), -sin(rot), sin(rot), cos(rot));

        vec3 accum = vec3(0.0);
        #pragma unroll_loop_start
        for (int i = 0; i < 12; ++i) {
            vec2 tapUV = clamp(uv + (rotMat * getDiffuseKernel(i)) * scale, 0.001, 0.999);
            accum += texture2D(tSceneColor, tapUV).rgb * getDiffuseWeight(i);
        }
        #pragma unroll_loop_end

        return (accum / DIFFUSE_WEIGHT_SUM) * absorption;
    }
#endif
`;
