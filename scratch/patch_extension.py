with open("viewer/src/refraction-shaders.ts", "r") as f:
    code = f.read()

# Remove old extension
code = code.replace("#if __VERSION__ < 300\n#extension GL_OES_standard_derivatives : enable\n#endif\n", "")

# Add extension to the very top of refraction_composite_frag
frag_start = "export const refraction_composite_frag = `\n"
new_frag_start = "export const refraction_composite_frag = `\n#if __VERSION__ < 300\n#extension GL_OES_standard_derivatives : enable\n#endif\n"
code = code.replace(frag_start, new_frag_start)

with open("viewer/src/refraction-shaders.ts", "w") as f:
    f.write(code)

print("Extension moved to top!")
