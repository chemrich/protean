with open("viewer/src/refraction.ts", "r") as f:
    code = f.read()

code = code.replace(
    "ShaderCode('refraction-composite', quad_vert, refraction_composite_frag)",
    "ShaderCode('refraction-composite', quad_vert, refraction_composite_frag, { standardDerivatives: true })"
)

with open("viewer/src/refraction.ts", "w") as f:
    f.write(code)

print("Added standardDerivatives to ShaderCode!")
