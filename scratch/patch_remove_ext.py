with open("viewer/src/refraction-shaders.ts", "r") as f:
    code = f.read()

code = code.replace("#if __VERSION__ < 300\n#extension GL_OES_standard_derivatives : enable\n#endif\n", "")

with open("viewer/src/refraction-shaders.ts", "w") as f:
    f.write(code)

print("Removed manual extension!")
