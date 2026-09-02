import re

with open("viewer/src/refraction-shaders.ts", "r") as f:
    code = f.read()

# Fix dFdx / dFdy extension
if "#include common" in code and "#extension GL_OES_standard_derivatives : enable" not in code:
    code = code.replace("#include common", "#if __VERSION__ < 300\n#extension GL_OES_standard_derivatives : enable\n#endif\n#include common")

# Fix array initializers for DIFFUSE_KERNEL and DIFFUSE_WEIGHTS
old_arrays = """const int DIFFUSE_TAPS = 12;
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
const float DIFFUSE_WEIGHT_SUM = 5.179;"""

new_arrays = """const int DIFFUSE_TAPS = 12;
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
const float DIFFUSE_WEIGHT_SUM = 5.179;"""

code = code.replace(old_arrays, new_arrays)

# Replace usage of array in sampleFrostedScattering
code = code.replace("DIFFUSE_KERNEL[i]", "getDiffuseKernel(i)")
code = code.replace("DIFFUSE_WEIGHTS[i]", "getDiffuseWeight(i)")

with open("viewer/src/refraction-shaders.ts", "w") as f:
    f.write(code)

print("Patched!")
