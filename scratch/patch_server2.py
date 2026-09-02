with open("src/protean_mcp/server.py", "r") as f:
    code = f.read()

preset = """@preset_factory("origami")
async def _preset_origami(connection: ClientConnection) -> list[str]:
    \"\"\"Apply the folded paper origami aesthetic.\"\"\"
    steps = [
        "Applying studio lighting.",
        "Applying origami material."
    ]
    await connection.request("lighting", {"rig": "studio"})
    await connection.request(
        "material",
        {
            "name": "auto",
            "finish": "origami"
        }
    )
    return steps
"""

if "def _preset_origami" not in code:
    code = code.replace("@preset_factory(\"dark-ground\")", preset + "\n@preset_factory(\"dark-ground\")")
    with open("src/protean_mcp/server.py", "w") as f:
        f.write(code)
    print("Added origami preset!")
