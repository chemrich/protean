# Investigation Report: Protean Mega Renders API & Pipeline Survey

## 1. Observation

### 1.1 Structure Fetching and Loading Mechanisms
- **`src/protean_mcp/fetch.py` (lines 65-123, 165-181)**:
  - `fetch_structure_data(identifier: str, source: str = "auto", *, cache_dir: Path | None = None, ...)` resolves 4-character PDB IDs matching `PDB_ID_RE = re.compile(r"^[0-9][a-zA-Z0-9]{3}$")` (line 14).
  - Fetches mmCIF from RCSB URL: `https://files.rcsb.org/download/{pdb_id}.cif` (line 20).
  - Caches to `~/.cache/protean/structures/{pdb_id}.cif` (lines 47-50, 98-101).
  - Returns `StructureData(name=pdb_id, format="mmcif", data=text, source="cache"|"pdb")`.
- **`src/protean_mcp/server.py` (lines 600-655)**:
  - Tool `@_tool() async def fetch_structure(identifier: str, source: str = "auto", name: str | None = None, assembly: str = "biological") -> str`:
    - Calls `fetch_structure_data(identifier, source)`.
    - Parses structure into Biotite `AtomArray` via `_load_structure(structure.data, structure.format, assembly)` (`src/protean_mcp/selections_numpy.py`).
    - Pushes RPC to Mol* bridge: `bridge.request("load_structure", {"name": label, "format": structure.format, "data": structure.data, "assembly": assembly})`.
    - `assembly="biological"` (default) builds the biological macromolecular assembly (e.g. 24-mer nanocage for Ferritin 1FHA); `assembly="asymmetric"` loads deposited asymmetric coordinates.
- **Target PDBs Profile**:
  - `1FHA`: Human Ferritin heavy chain 24-mer nanocage ($O$ octahedral symmetry with large central iron-storage cavity).
  - `5JQ3`: Cas9 endonuclease complexed with guide RNA and target DNA.
  - `1F88`: Bovine Rhodopsin (7-transmembrane GPCR bundle with bound 11-cis retinal).
  - `1GFL`: Green Fluorescent Protein (GFP 11-stranded $\beta$-barrel enclosing tripeptide fluorophore).

### 1.2 Display, Selections, and Handle Management
- **`src/protean_mcp/server.py` (lines 908-988)**:
  - `@_tool() async def show(representation: str = "cartoon", selection: str | None = None, handle: str | None = None, color: str | None = None, size: float | None = None, opacity: float | None = None, pickable: bool | None = None, name: str = "sele") -> dict[str, Any]`
  - Representations available: `"cartoon"`, `"ball-and-stick"`, `"spacefill"`, `"molecular-surface"`, `"gaussian-surface"`, `"putty"`, `"line"`, `"point"`, `"ellipsoid"`, `"backbone"`, `"carbohydrate"`.
  - Handle management (`lines 4214-4367`): Default structure load creates handle `"auto"`. When a preset takes over the scene (`_take_the_scene`), `"auto"` is hidden, and `"auto_view"` is registered over selection `"polymer"`. If ligands exist (`_draw_the_ligands`, `lines 5295-5322`), handle `"auto_ligand"` is registered for `"not polymer and not solvent"`.

### 1.3 The Three Aesthetics: Glass, Seaglass, and Origami

#### Aesthetic 1: Clear Refractive Glass
- **`src/protean_mcp/server.py` (lines 3730-3780)**:
  - `@_tool() async def material(finish: str = "matte", name: str = "sele", metalness: float | None = None, roughness: float | None = None, emissive: float | None = None, bumpiness: float | None = None, bump_frequency: float | None = None) -> dict[str, Any]`
- **`viewer/src/dispatch.ts` (lines 500, 2044-2060)**:
  - `MATERIAL_FINISHES["glass"] = { metalness: 0, roughness: 0.05, bumpiness: 0 }`.
- **`viewer/src/refraction.ts` (lines 1-150) & `viewer/src/refraction-shaders.ts` (lines 1-150)**:
  - Implements screen-space Snell's law refraction: $\vec{R} = \eta \vec{I} + (\eta \cos\theta_1 - \cos\theta_2)\vec{N}$ with depth scaling and aspect ratio correction (`uGlassIOR = 1.50`, `uRefractionStrength = 0.08`).
  - Evaluates Dielectric Schlick Fresnel factor: $F = F_0 + (1 - F_0)(1 - |\vec{N} \cdot \vec{V}|)^5$ with $F_0 = 0.04$.
  - 3-tap spectral chromatic dispersion for optical splitting (`uDispersionSpread = 0.02`).
  - Beer-Lambert absorption tinting (`uAbsorptionStrength = 0.75`).
  - Tested in `tests/test_glass_differential.py` (lines 93-103, 363-392): Loaded under `lighting(rig="studio")` or `lighting(rig="three-point")` on white, dark (`#111111`), radial gradient (`gradient="radial", gradient_from="#1a2a3a", gradient_to="#050a10"`), or transparent background.

#### Aesthetic 2: Frosted Seaglass
- **`src/protean_mcp/server.py` (lines 4881-4890, 5179-5184, 5420-5422)**:
  - `_preset_seaglass(_target: str, handle: str) -> list[str]`:
    ```python
    await _run(background, color="#ffffff", gradient="off"),
    await _run(lighting, rig="three-point", ambient=0.45),
    *await _set_effects(occlusion=True, shadow=False),
    await _run(color, color="#73b9a2", name=handle),
    await _run(material, finish="seaglass", name=handle),
    ```
- **`viewer/src/dispatch.ts` (lines 479-481, 501)**:
  - `MATERIAL_FINISHES["seaglass"] = { metalness: 0, roughness: 0.7, bumpiness: 0.45, bump_frequency: 4.0 }`.
- **`viewer/src/refraction.ts` & `viewer/src/refraction-shaders.ts`**:
  - 12-tap Vogel Golden Angle spiral kernel with Gaussian weights and screen-space dither for frosted transmission diffusion (`uDiffusionSpread = 0.04`).
  - Procedural 3-octave FBM surface normal perturbation for tumbled beach glass facets (`bumpiness: 0.45, bump_frequency: 4.0`).
  - Seafoam green tint (`#73b9a2`) applied via `color(color="#73b9a2")`.
  - Tested in `tests/test_glass_differential.py` (lines 105-129, 394-421).

#### Aesthetic 3: Origami (Folded Paper)
- **`src/protean_mcp/server.py` (lines 4870-4879, 5173-5178, 5417-5419)**:
  - `_origami_style(_target: str, handle: str) -> list[str]`:
    ```python
    await _run(background, color="#f6f4eb", gradient="off"),
    await _run(lighting, rig="three-point", ambient=0.45),
    *await _set_effects(occlusion=True, shadow=False),
    await _run(shading, style="origami", name=handle),
    await _run(material, finish="origami", name=handle),
    ```
- **`viewer/src/dispatch.ts` (lines 416-428, 472-474, 499)**:
  - `SHADING_STYLES["origami"]`:
    - `flatShaded: true` (flat normal derivative shading giving sharp creases on secondary structure folds).
    - `helixProfile: 'square'`, `nucleicProfile: 'square'`.
    - `radialSegments: 4`, `linearSegments: 6`, `aspectRatio: 4.5`.
    - `ignoreLight: false`, `celShaded: false`, `xrayShaded: false`.
  - `MATERIAL_FINISHES["origami"]`:
    - `{ metalness: 0, roughness: 1.0, bumpiness: 0.45, bump_frequency: 4.5 }` (dielectric matte paper properties paired with fine procedural paper tooth bump grain).
  - Background: Warm washi paper ground (`color="#f6f4eb"`).
  - Coloring: Secondary structure coloring (`color="secondary-structure"` on cartoon representation).
  - Tested in `tests/test_origami_differential.py` (lines 55-105).

### 1.4 Rendering Pipeline, Lighting, Camera, and Snapshot API
- **Camera & Framing**:
  - `server.orient()` (`server.py:4178-4181`): Aligns camera along structure's principal inertial axes.
  - `server.focus(name="sele")` (`server.py:4165-4169`): Zooms and centers on a named selection.
  - `server.reset_view()` (`server.py:4171-4175`): Frames the entire scene.
  - `server.lens(projection="perspective"|"orthographic", fog=15.0)` (`server.py:3929-3992`): Perspective vs orthographic projection and distance fog attenuation.
- **Lighting Rigs**:
  - `server.lighting(rig="three-point"|"studio"|"standard"|"rim"|"ring"|"flat", intensity=1.0, ambient=0.45, exposure=1.0)` (`server.py:3994-4029`):
    - `"three-point"`: Key, fill, and back light.
    - `"studio"`: Warm key against cool fill, photographic low-contrast.
- **Screen-Space Effects**:
  - `server.effects(occlusion=True, shadow=False, depth_of_field=False, bloom=False, outline=False, sharpening=True)` (`server.py:2300-2345`).
- **Path-Tracing / Illumination**:
  - `server.path_trace(enabled=True, quality="standard"|"high"|"ultra"|"draft", bounces=4, shadows=True, denoise=True)` (`server.py:3686-3728`).
  - Mapped in `viewer/src/dispatch.ts:443-448`: `draft` (8 samples), `standard` (32 samples), `high` (128 samples), `ultra` (512 samples).
  - Uses Mol* `IlluminationPass` with progressive accumulation. Requires GPU WebGL extensions (`textureFloat`, `colorBufferFloat`, `depthTexture`, `drawBuffers`).
- **Snapshot Capture API**:
  - `server.snapshot(path: str, column: str | None = None, width_mm: float | None = None, dpi: int = 300, format: str = "png", transparent: bool | None = None, crop: bool = False, finish: str | None = None, overwrite: bool = False)` (`server.py:2510-2640`).
  - Resolution computation (`server.py:2594`):
    - `column="single"`: 89 mm $\rightarrow$ 1051 px at 300 DPI, 2102 px at 600 DPI.
    - `column="double"`: 183 mm $\rightarrow$ 2161 px at 300 DPI, 4323 px at 600 DPI.
    - Custom: `width_mm=101.6` $\rightarrow$ 1200 px at 300 DPI.
  - Snapshot workflow in `viewer/src/dispatch.ts:1884-1969`:
    - Dispatches to Mol* `viewportScreenshot` helper `getImageDataUri()`.
    - Preserves aspect ratio (`height = Math.round(width * aspect)`).
    - Calls `settleRender()` to wait for WebGL commit queues and passes.
    - Encodes lossless PNG with physical DPI metadata via Pillow (`server.py:2533`).

---

## 2. Logic Chain

1. **PDB Ingestion**:
   - The user requires renders of 4 structures (`1FHA`, `5JQ3`, `1F88`, `1GFL`).
   - Observations in `fetch.py` and `server.py` prove that calling `server.fetch_structure(pdb_id, assembly="biological")` automatically downloads mmCIF from RCSB, caches locally, and loads into Mol* and Biotite.
   - For `1FHA` (Ferritin 24-mer nanocage), `assembly="biological"` builds the 24-subunit cage.
   - For `5JQ3`, `1F88`, and `1GFL`, default assembly appropriately resolves complex, receptor-ligand, and GFP fluorophore.

2. **Aesthetic Mapping**:
   - **Glass Aesthetic**: Directly invoking `await server.material(finish="glass", name="auto")` or `name="auto_view"` applies clear refractive glass ($F_0=0.04$, roughness=0.05, Snell refraction, dispersion). Paired with `lighting(rig="studio")` or `lighting(rig="three-point")` over a dark `#111111`, radial gradient, or pure white background.
   - **Seaglass Aesthetic**: Invoking `await server.preset("seaglass")` automatically applies frosted roughness ($0.7$), surface bump ($0.45$ at frequency $4.0$), seafoam green tint (`#73b9a2`), three-point lighting (ambient $0.45$), ambient occlusion, and Vogel spiral diffused refraction.
   - **Origami Aesthetic**: Invoking `await server.preset("origami")` applies `shading(style="origami")` (`flatShaded: true`, square profiles), `material(finish="origami")` (paper grain bumpiness $0.45$, frequency $4.5$, roughness $1.0$), secondary structure coloring, ambient occlusion, three-point lighting, and warm washi background (`#f6f4eb`).

3. **Rendering & Snapshot Pipeline**:
   - Camera can be positioned using `await server.orient()` (aligning to principal axes) and framed with `await server.reset_view()`.
   - Figures should be captured using `await server.snapshot(path=..., column="double", dpi=300, format="png", overwrite=True)` to produce high-resolution, publication-ready images (2161 px wide at 300 DPI, or 4323 px wide at 600 DPI).
   - If running in headless Chrome with a real GPU, `await server.path_trace(enabled=True, quality="high")` can be enabled; for headless software rendering / standard Mol* passes, the custom WebGL screen-space refraction pass (`refraction.ts`) provides high-fidelity refraction, dispersion, and diffusion out of the box.

4. **Standalone Orchestration Pattern**:
   - As observed in `docs/figures/make_figures.py` and `tests/browser.py`, a standalone script can instantiate `ViewerBridge(port=free_port(), static_dir=STATIC)`, launch Chrome with `--headless=new` (or headed), attach `server.use_bridge(bridge)`, and execute async tool calls.

---

## 3. Caveats

1. **Hardware Path-Tracing vs Software Rendering**:
   - Mol*'s `path_trace()` requires GPU WebGL float texture extensions. In purely software-rasterized CI environments (SwiftShader), path-tracing will timeout or be skipped. However, the screen-space refraction shaders for Glass and Seaglass run in standard WebGL raster passes and execute across all environments.
2. **Biological Assembly Size for 1FHA**:
   - Human ferritin `1FHA` biological assembly contains 24 subunits (approx. 4,300 residues). When applying cartoon representations and refraction passes, ensure sufficient WebGL heap and timeout budgets (`settleRender` handles this).
3. **Handle State Persistence**:
   - Calling presets switches the primary drawn handle from `"auto"` to `"auto_view"`. When reloading structures in loops, call `fetch_structure` or reset handles cleanly to ensure subsequent preset applications target the active handle.

---

## 4. Conclusion

The Protean codebase provides a complete, programmatic Python API for loading the 4 target structures, applying the 3 required aesthetics (Glass, Seaglass, Origami), and rendering high-resolution figures.

### Summary Table of API Calls for the 3 Aesthetics

| Aesthetic | High-Level Tool Call | Equivalent Low-Level Direct Calls | Shader / Geometry Mechanics |
| :--- | :--- | :--- | :--- |
| **Glass** | `await server.material(finish="glass", name="auto")` | `await server.material(finish="glass", roughness=0.05, metalness=0.0, bumpiness=0.0, name="auto")`<br>`await server.lighting(rig="studio")`<br>`await server.background(color="#111111")` | Snell's law refraction ($\text{IOR}=1.50$), Schlick Fresnel ($F_0=0.04$), 3-tap spectral chromatic dispersion, Beer-Lambert transmission. |
| **Seaglass** | `await server.preset("seaglass")` | `await server.color(color="#73b9a2", name="auto")`<br>`await server.material(finish="seaglass", roughness=0.7, bumpiness=0.45, bump_frequency=4.0, name="auto")`<br>`await server.lighting(rig="three-point", ambient=0.45)`<br>`await server.background(color="#ffffff")`<br>`await server.effects(occlusion=True, shadow=False)` | 12-tap Vogel spiral diffusion kernel, 3-octave procedural FBM normal bump facets, seafoam green tint (`#73b9a2`). |
| **Origami** | `await server.preset("origami")` | `await server.show(representation="cartoon", color="secondary-structure", name="auto")`<br>`await server.shading(style="origami", name="auto")`<br>`await server.material(finish="origami", roughness=1.0, bumpiness=0.45, bump_frequency=4.5, name="auto")`<br>`await server.lighting(rig="three-point", ambient=0.45)`<br>`await server.background(color="#f6f4eb")`<br>`await server.effects(occlusion=True, shadow=False)` | `flatShaded: true` (crisp facet creases on secondary structures), square trace profiles, paper tooth bump grain, warm washi ground (`#f6f4eb`). |

### Execution Script Template
```python
import asyncio
from pathlib import Path
import protean_mcp.server as server
from protean_mcp.connection import ViewerBridge
from tests.browser import STATIC, find_chrome
from tests.conftest import free_port
import subprocess, tempfile, shutil

STRUCTURES = ["1FHA", "5JQ3", "1F88", "1GFL"]
AESTHETICS = ["glass", "seaglass", "origami"]
OUTPUT_DIR = Path("scratch/mega_renders")

async def render_mega_suite():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bridge = ViewerBridge(port=free_port(), static_dir=STATIC)
    await bridge.start()
    profile = tempfile.mkdtemp(prefix="protean-mega-")
    chrome = find_chrome()
    proc = subprocess.Popen([
        chrome, f"--user-data-dir={profile}", "--no-first-run",
        "--headless=new", "--window-size=1200,1200", bridge.viewer_url
    ])
    try:
        await bridge.wait_for_viewer(40)
        server.use_bridge(bridge)
        for pdb_id in STRUCTURES:
            for aesthetic in AESTHETICS:
                out_png = OUTPUT_DIR / f"{pdb_id.lower()}_{aesthetic}.png"
                # 1. Fetch & Load Structure
                await server.fetch_structure(pdb_id, assembly="biological")
                await server.orient()
                # 2. Apply Aesthetic
                if aesthetic == "glass":
                    await server.material(finish="glass", name="auto")
                    await server.lighting(rig="studio")
                    await server.background(color="#ffffff")
                elif aesthetic == "seaglass":
                    await server.preset("seaglass")
                elif aesthetic == "origami":
                    await server.preset("origami")
                # 3. Capture Publication Snapshot
                await server.snapshot(str(out_png), column="double", dpi=300, overwrite=True)
    finally:
        proc.terminate()
        await bridge.stop()
        shutil.rmtree(profile, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(render_mega_suite())
```

---

## 5. Verification Method

### Test Suite Execution
Run the existing differential test suites to verify that the shader passes, material finishes, and presets compile and render cleanly:

```bash
# 1. Verify Glass and Seaglass differential suite
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_glass_differential.py -v

# 2. Verify Origami differential suite
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_origami_differential.py -v

# 3. Verify Server tools and capabilities
uv run pytest tests/test_server.py -k "test_capabilities or test_preset" -v
```

### Key Files Inspected
- `src/protean_mcp/server.py` — Server tool declarations and preset recipes.
- `src/protean_mcp/fetch.py` — PDB retrieval and caching.
- `viewer/src/dispatch.ts` — Material, shading, and snapshot dispatch.
- `viewer/src/refraction.ts` & `viewer/src/refraction-shaders.ts` — WebGL refraction and diffusion shaders.
- `tests/test_glass_differential.py` & `tests/test_origami_differential.py` — Reference execution and pixel validation.
- `docs/figures/make_figures.py` — Reference figure rendering architecture.

### Invalidation Conditions
- Any changes to `MATERIAL_FINISHES` or `SHADING_STYLES` in `viewer/src/dispatch.ts`.
- Modification to `_PRESETS` in `src/protean_mcp/server.py`.
- Renaming or schema modification of `snapshot()`, `material()`, or `fetch_structure()`.
