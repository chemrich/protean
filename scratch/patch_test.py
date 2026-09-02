with open("viewer/src/dispatch.test.ts", "r") as f:
    code = f.read()

# Fix capabilities assertions
code = code.replace(
    "'chrome', 'glossy', 'matte', 'metallic', 'satin'",
    "'chrome', 'glass', 'glossy', 'matte', 'metallic', 'origami', 'satin', 'seaglass'"
)
code = code.replace(
    "'cel', 'flat', 'normal', 'xray', 'xray-inverted'",
    "'cel', 'flat', 'normal', 'origami', 'xray', 'xray-inverted'"
)

with open("viewer/src/dispatch.test.ts", "w") as f:
    f.write(code)

print("Test patched!")
