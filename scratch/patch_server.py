with open("src/protean_mcp/server.py", "r") as f:
    code = f.read()

preset = """@preset_factory("seaglass")
async def _preset_seaglass(connection: ClientConnection) -> list[str]:
    \"\"\"Apply the frosted seaglass aesthetic with a seafoam tint.\"\"\"
    steps = [
        "Applying studio lighting for bright illumination.",
        "Setting uniform seafoam tint.",
        "Applying seaglass refraction shader."
    ]
    await connection.request("lighting", {"rig": "studio"})
    await connection.request(
        "color",
        {
            "theme": "uniform",
            "name": "auto",
            "value": "#98FF98"
        }
    )
    await connection.request(
        "material",
        {
            "name": "auto",
            "finish": "seaglass"
        }
    )
    return steps
"""

if "def _preset_seaglass" not in code:
    code = code.replace("@preset_factory(\"dark-ground\")", preset + "\n@preset_factory(\"dark-ground\")")
    with open("src/protean_mcp/server.py", "w") as f:
        f.write(code)
    print("Added seaglass preset!")
else:
    print("Seaglass preset already exists?")
