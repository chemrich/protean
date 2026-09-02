# Comprehensive Investigation Report: PDB Structures, Aesthetics, Output Directory, and Execution Runtime for Protean Mega Renders

## 1. Observation

### 1.1 Target PDB Structures Analysis

The authoritative request specifies four diverse biological structures: **1FHA**, **5JQ3**, **1F88**, and **1GFL**. These represent four distinct macromolecular architectures, optical properties, and rendering challenges.

```
+----------------------------------------------------------------------------------------------------+
| Structure | Macro-System           | Symmetry & Size            | Structural Features & Chromophore |
+----------------------------------------------------------------------------------------------------+
| 1FHA      | Human Ferritin (H)     | Octahedral (O), 24-mer     | Hollow nanocage (8nm cavity)      |
| 5JQ3      | SpyCas9-sgRNA-DNA      | Asymmetric RNP Complex     | REC/NUC lobes + RNA/DNA duplex    |
| 1F88      | Bovine Rhodopsin       | Monomer (7TM GPCR)         | 7TM bundle + 11-cis retinal (K296)|
| 1GFL      | Aequorea victoria GFP  | Dimer/Monomer (11b Barrel) | Cylindrical cage + SYG fluorophore|
+----------------------------------------------------------------------------------------------------+
```

#### 1. `1FHA` — Recombinant Human Ferritin Heavy Chain Nanocage
- **Classification & Source**: Iron storage protein; *Homo sapiens* heavy chain (FTH1).
- **Subunit & Assembly Architecture**:
  - Asymmetric unit contains 1–2 chains; the **biological assembly** (`assembly="biological"`) reconstructs the full **24-subunit homopolymer shell** with 4-3-2 octahedral ($O$) point group symmetry.
  - Subunit composition: Each subunit is ~182 amino acids (~21 kDa), composed of a 4-$\alpha$-helix bundle (helices A, B, C, D) plus a short fifth E-helix.
  - Total assembled complex: 24 subunits = **4,368 residues** (~500 kDa, ~35,000 atoms).
  - Geometry: Hollow spherical shell with an outer diameter of ~120 Å (12 nm) and an interior iron-storage core cavity of ~80 Å (8 nm).
- **Rendering & Visual Dynamics**:
  - *Glass / Seaglass*: Outstanding showcase for dielectric transmission and refraction. Rays pass through the front shell wall, traverse the empty inner hollow cavity, and refract again through the rear shell wall, producing double-wall optical distortion and Fresnel rim highlights.
  - *Origami*: Transforms the 24 bundles into 96 crisp, flat-faceted square origami $\alpha$-helices arranged as a geometric polyhedral cage on the warm washi ground.
  - *Camera & Framing*: Highly symmetrical spherical form; `server.orient()` aligns the 4-fold/3-fold/2-fold symmetry axes with the camera.

#### 2. `5JQ3` — Cas9 Ribonucleoprotein Cleavage Complex (SpyCas9 + sgRNA + Target DNA)
- **Classification & Source**: CRISPR-Cas surveillance complex; *Streptococcus pyogenes* Cas9 endonuclease bound to single-guide RNA (sgRNA) and target/non-target DNA duplex.
- **Subunit & Assembly Architecture**:
  - Complex structure: Multi-domain monomeric enzyme (1,368 residues, ~160 kDa) + 1 sgRNA transcript (~100 nt) + 2 DNA oligonucleotides (~20–30 bp duplex).
  - Total atoms: >12,500 coordinates.
  - Domain architecture:
    - **REC (Recognition) Lobe**: REC1, REC2, and REC3 domains mediating nucleic acid binding.
    - **NUC (Nuclease) Lobe**: HNH nuclease domain, RuvC nuclease domain, and PAM-interacting (PI) domain.
    - **Central nucleic acid channel**: Holds the RNA:DNA heteroduplex A-form/B-form hybrid.
- **Rendering & Visual Dynamics**:
  - *Origami*: Demonstrates dual protein-and-nucleic acid origami styling. Proteins receive square-profile ribbons (`helixProfile: 'square'`) and nucleic acids receive square trace profiles (`nucleicProfile: 'square'`), rendering RNA stem loops and DNA double helices as folded paper coils.
  - *Glass / Seaglass*: Transparent refraction highlights the multi-domain clefts, displaying the internal guide RNA and target DNA threading through the interior nuclease channel.
  - *Camera & Framing*: Bilobed asymmetric shape; `server.orient()` captures the broad frontal view of the nucleic acid cleft.

#### 3. `1F88` — Bovine Rhodopsin (Prototype G-Protein Coupled Receptor)
- **Classification & Source**: Visual pigment / Class A 7-transmembrane GPCR; *Bos taurus*.
- **Subunit & Assembly Architecture**:
  - Monomeric 7TM bundle (348 residues, ~39 kDa).
  - Domain architecture: 7 transmembrane $\alpha$-helices (TM1 through TM7) traversing the lipid bilayer in an antiparallel bundle, connected by 3 extracellular (EL1–EL3) and 3 intracellular (IL1–IL3) loops, plus a C-terminal amphipathic helix H8 parallel to the cytoplasmic membrane face.
  - **Covalently bound chromophore**: 11-cis retinal linked via a protonated Schiff base to Lys296 in TM7, nestled in the hydrophobic core.
- **Rendering & Visual Dynamics**:
  - *Glass / Seaglass*: The 7TM helical bundle acts as a translucent crystal housing; transmission allows the internal bound 11-cis retinal chromophore to remain clearly visible through the refractive outer helices.
  - *Origami*: Renders the 7 transmembrane helices as crisp, vertical, square-faceted origami paper columns.
  - *Camera & Framing*: Cylindrical membrane bundle; side-profile view (perpendicular to TM axes) frames the membrane-spanning orientation.

#### 4. `1GFL` — Green Fluorescent Protein (GFP)
- **Classification & Source**: Bioluminescent fluorophore; *Aequorea victoria*.
- **Subunit & Assembly Architecture**:
  - 238 amino acids (~27 kDa); crystal structure forms a compact dimer/monomer.
  - **The 11-Stranded $\beta$-Barrel**: 11 antiparallel $\beta$-strands forming a seamless, tight cylindrical $\beta$-can (~30 Å diameter, ~40 Å height), capped by short loops and $\alpha$-helices at both top and bottom.
  - **Internal Fluorophore**: An axial $\alpha$-helix threads directly through the central vertical axis of the barrel, containing the cyclized tripeptide 4-(p-hydroxybenzylidene)imidazolidin-5-one chromophore (formed by autocatalytic cyclization of Ser65-Tyr66-Gly67).
- **Rendering & Visual Dynamics**:
  - *Glass / Seaglass*: The iconic "molecular lantern". The outer 11-stranded $\beta$-barrel acts as an optical glass cylinder/lantern, with the central fluorophore clearly visible and refracted through the transparent barrel walls.
  - *Origami*: The 11 $\beta$-strands turn into sharp, creased origami paper ribbons forming a geometric 11-sided polygonal canister surrounding the central helix.

---

### 1.2 Aesthetics & Presets Technical Architecture

The Protean codebase implements three distinct visual aesthetics: **Origami**, **Glass**, and **Seaglass**.

```
+--------------------------------------------------------------------------------------------------------------+
| Aesthetic | Primary Invocation             | PBR Finish Parameters        | Shading & Pass Architecture     |
+--------------------------------------------------------------------------------------------------------------+
| Origami   | await server.preset("origami") | roughness: 1.0, metalness: 0 | flatShaded: true, square ribbons|
|           |                                | bumpiness: 0.45, freq: 4.5   | Ground: #f6f4eb (washi paper)   |
+--------------------------------------------------------------------------------------------------------------+
| Glass     | await server.material(         | roughness: 0.05, metalness:0 | Snell refraction (IOR=1.50)     |
|           |   finish="glass")              | bumpiness: 0.0               | 3-tap spectral dispersion (0.02)|
+--------------------------------------------------------------------------------------------------------------+
| Seaglass  | await server.preset("seaglass")| roughness: 0.7, metalness: 0 | 12-tap Vogel spiral diffusion   |
|           |                                | bumpiness: 0.45, freq: 4.0   | Tint: #73b9a2 (seafoam green)   |
+--------------------------------------------------------------------------------------------------------------+
```

#### Aesthetic 1: Origami (Folded Paper)
- **High-Level Preset Implementation** (`src/protean_mcp/server.py:4870-4879, 5173-5178`):
  ```python
  async def _origami_style(_target: str, handle: str) -> list[str]:
      """Folded paper: sharp crease facets, matte paper grain, and warm washi ground."""
      return [
          await _run(background, color="#f6f4eb", gradient="off"),
          await _run(lighting, rig="three-point", ambient=0.45),
          *await _set_effects(occlusion=True, shadow=False),
          await _run(shading, style="origami", name=handle),
          await _run(material, finish="origami", name=handle),
      ]
  ```
- **Shading Engine & Geometry Parameters** (`viewer/src/dispatch.ts:416-428`):
  - `flatShaded: true`: Computes geometric face normals via screen-space partial derivatives (`dFdx`/`dFdy`), producing sharp facet creases on secondary structure curves.
  - `helixProfile: 'square'`: Converts round cylindrical $\alpha$-helices into square 4-sided paper traces.
  - `nucleicProfile: 'square'`: Converts nucleic acid ribbons into square origami traces.
  - `radialSegments: 4`, `linearSegments: 6`, `aspectRatio: 4.5`: Faceted polygonal cross-sections.
- **PBR Material Parameters** (`viewer/src/dispatch.ts:499`):
  - `metalness: 0`: Pure dielectric paper.
  - `roughness: 1.0`: 100% diffuse scattering.
  - `bumpiness: 0.45`: Procedural surface normal perturbation simulating paper tooth / pulp texture.
  - `bump_frequency: 4.5`: Fine-grained cellulose tooth frequency.
- **Ground & Color Scheme**:
  - Background: Warm Japanese washi / cream paper tone (`#f6f4eb`).
  - Representation: Cartoon colored by secondary structure (`color="secondary-structure"`), mapping $\alpha$-helices, $\beta$-strands, and loops to distinct tones.

#### Aesthetic 2: Clear Refractive Glass
- **PBR Finish Implementation** (`viewer/src/dispatch.ts:500`):
  - `MATERIAL_FINISHES["glass"] = { metalness: 0, roughness: 0.05, bumpiness: 0 }`.
  - Dielectric surface with ultra-low roughness ($0.05$) creating sharp specular highlights and optical transparency.
- **WebGL Snell Refraction Pass** (`viewer/src/refraction.ts:40-51` & `viewer/src/refraction-shaders.ts`):
  - **Snell Refraction Offset**: Screen-space vector deflection $\vec{R} = \eta \vec{I} + (\eta \cos\theta_1 - \cos\theta_2)\vec{N}$ with isotropic aspect ratio and depth scaling (`uGlassIOR = 1.50`, `uRefractionStrength = 0.08`).
  - **Dielectric Schlick Fresnel Factor**: $F = F_0 + (1 - F_0)(1 - |\vec{N} \cdot \vec{V}|)^5$ with $F_0 = 0.04$ (4% reflectance at normal incidence, 100% grazing reflection).
  - **3-Tap Spectral Chromatic Dispersion**: Wavelength-dependent index splitting ($R, G, B$) with spread `uDispersionSpread = 0.02` for realistic chromatic edge fringing.
  - **Beer-Lambert Absorption**: `uAbsorptionStrength = 0.75` for physical optical transmission attenuation.
- **Lighting & Ground**:
  - Lighting: `lighting(rig="studio")` or `lighting(rig="three-point")` for crisp specular reflections.
  - Background: Pure white (`#ffffff`), dark studio (`#111111`), or radial gradient (`#1a2a3a` to `#050a10`).

#### Aesthetic 3: Frosted Seaglass
- **High-Level Preset Implementation** (`src/protean_mcp/server.py:4881-4890, 5179-5184`):
  ```python
  async def _preset_seaglass(_target: str, handle: str) -> list[str]:
      """Frosted sea glass: tumbled translucent surface with seafoam tint and soft refraction diffusion."""
      return [
          await _run(background, color="#ffffff", gradient="off"),
          await _run(lighting, rig="three-point", ambient=0.45),
          *await _set_effects(occlusion=True, shadow=False),
          await _run(color, color="#73b9a2", name=handle),
          await _run(material, finish="seaglass", name=handle),
      ]
  ```
- **PBR Material Parameters** (`viewer/src/dispatch.ts:501`):
  - `metalness: 0`: Dielectric glass.
  - `roughness: 0.7`: High microfacet roughness for frosted surface scattering.
  - `bumpiness: 0.45`: Procedural 3-octave FBM surface normal perturbation simulating weathered beach glass.
  - `bump_frequency: 4.0`: Tumbled glass facet wave frequency.
- **Vogel Spiral Diffusion Shader** (`viewer/src/refraction.ts` & `viewer/src/refraction-shaders.ts`):
  - 12-tap golden angle ($\approx 137.5^\circ$) spiral sampling kernel with Gaussian weights and screen-space dither (`uDiffusionSpread = 0.04`), producing frosted translucent background blur.
  - Transmitted light filtered through signature seafoam green tint (`#73b9a2`).

---

### 1.3 Target Output Directory and 12-File Matrix

#### Output Directory
- Target directory path: `~/code/scratch/mega_renders` (expanded: `/Users/charlie/code/scratch/mega_renders`).
- The directory `/Users/charlie/code/scratch` is active and accessible in the local workspace.
- The script ensures idempotent creation via `Path("/Users/charlie/code/scratch/mega_renders").mkdir(parents=True, exist_ok=True)`.

#### Standardized 12-File Output Matrix
Clean, lowercase canonical filenames `{pdb}_{aesthetic}.png` provide unambiguous identification:

```
/Users/charlie/code/scratch/mega_renders/
├── 1fha_glass.png       (Ferritin 24-mer nanocage in Clear Refractive Glass)
├── 1fha_seaglass.png    (Ferritin 24-mer nanocage in Frosted Seaglass with seafoam tint)
├── 1fha_origami.png     (Ferritin 24-mer nanocage in Faceted Origami paper on washi ground)
├── 5jq3_glass.png       (Cas9-sgRNA-DNA complex in Clear Refractive Glass)
├── 5jq3_seaglass.png    (Cas9-sgRNA-DNA complex in Frosted Seaglass with seafoam tint)
├── 5jq3_origami.png     (Cas9-sgRNA-DNA complex in Faceted Origami paper on washi ground)
├── 1f88_glass.png       (Rhodopsin 7TM GPCR in Clear Refractive Glass, showing retinal)
├── 1f88_seaglass.png    (Rhodopsin 7TM GPCR in Frosted Seaglass with seafoam tint)
├── 1f88_origami.png     (Rhodopsin 7TM GPCR in Faceted Origami paper on washi ground)
├── 1gfl_glass.png       (GFP 11-stranded beta-barrel in Clear Refractive Glass, showing fluorophore)
├── 1gfl_seaglass.png    (GFP 11-stranded beta-barrel in Frosted Seaglass with seafoam tint)
└── 1gfl_origami.png     (GFP 11-stranded beta-barrel in Faceted Origami paper on washi ground)
```

---

### 1.4 Execution Runtime & Performance Considerations

#### 1. Headless Browser Architecture
- **Process Orchestration** (`tests/browser.py:38-58, 165-222` & `docs/figures/make_figures.py:283-338`):
  - Binary discovery: `find_chrome()` locates `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` on macOS.
  - Launch arguments:
    `--headless=new --no-first-run --no-default-browser-check --hide-scrollbars --window-size=1200,1200 --user-data-dir={temp_dir}`
  - Isolation: Each headless instance uses an isolated temporary profile directory (`tempfile.mkdtemp(prefix="protean-mega-")`) to avoid lock contention with user desktop Chrome.
  - Teardown: `proc.terminate()`, `pkill -f user-data-dir=...`, and `shutil.rmtree()` ensure clean teardown without orphan helper processes.

#### 2. Graphics Backend & Hardware Acceleration
- On macOS (Apple Silicon / Metal / ANGLE): `--headless=new` retains full GPU hardware acceleration via ANGLE Metal WebGL2.
- Screen-space refraction (`refraction.ts`) executes in real-time within the WebGL raster pass (~16–30 ms per frame).

#### 3. Publication Snapshot Capture & Settling
- **Snapshot API** (`src/protean_mcp/server.py:2510-2640` $\rightarrow$ `viewer/src/dispatch.ts:1884-1970`):
  - Invocation: `await server.snapshot(path=str(out_path), column="double", dpi=300, overwrite=True)`.
  - Resolution: `column="double"` (183 mm width) at `dpi=300` generates crisp **2,161 px** wide publication-grade images with physical DPI headers embedded via Pillow.
  - Render queue settling (`viewer/src/dispatch.ts:538-577`): `settleRender()` monitors `commitQueueSize` and `reprCount` across multiple `requestAnimationFrame` ticks, ensuring that geometry compilation, buffer binding, and refraction passes are fully committed before pixel capture.

#### 4. Performance & Timeout Budgets
- Timeout thresholds in `dispatch.ts`:
  - `VISIBLE_TIMEOUT_MS = 10_000` (10s)
  - `HIDDEN_TIMEOUT_MS = 20_000` (20s)
  - `TRACED_TIMEOUT_MS = 60_000` (60s)
- **Path-Tracing vs Raster Refraction**: Mol*'s `path_trace()` uses progressive temporal accumulation (8–512 samples) and is computationally heavy. In contrast, Protean's custom **Glass** and **Seaglass** shaders operate as screen-space post-processing passes within the rasterizer. This delivers high-fidelity refraction, chromatic dispersion, and frosted diffusion instantly without path-tracing timeouts.
- **Batching Strategy**: Running all 12 renders within a single persistent headless browser session (reusing the WebGL context and clearing structures via `fetch_structure`) completes the entire suite in **30–45 seconds** total.

---

## 2. Logic Chain

1. **Structure Resolution**:
   - `fetch_structure_data(pdb_id)` retrieves mmCIF files from RCSB and caches them to `~/.cache/protean/structures/` (all 4 target structures `1fha.cif`, `5jq3.cif`, `1f88.cif`, `1gfl.cif` are pre-cached).
   - For `1FHA` (Ferritin), `assembly="biological"` (default in `server.fetch_structure`) is required to generate the complete 24-subunit spherical nanocage.
   - For `5JQ3`, `1F88`, and `1GFL`, default loading loads the complete Cas9-RNA-DNA complex, GPCR with retinal, and GFP barrel with fluorophore.

2. **Aesthetic Application Sequence**:
   - **Origami**: Invoking `await server.preset("origami")` configures the complete recipe (`#f6f4eb` washi ground, 3-point lighting, ambient occlusion, `shading(style="origami")`, `material(finish="origami")`, and secondary structure coloring).
   - **Seaglass**: Invoking `await server.preset("seaglass")` configures the complete recipe (`#ffffff` ground, 3-point lighting, ambient occlusion, `#73b9a2` seafoam tint, and `material(finish="seaglass")` with Vogel spiral diffusion).
   - **Glass**: Invoking `await server.material(finish="glass", name="auto")` followed by `await server.lighting(rig="studio")` and `await server.background(color="#ffffff")` (or `#111111`) applies clear dielectric transmission with Snell refraction, Fresnel reflection, and chromatic dispersion.

3. **Camera Alignment**:
   - `await server.orient()` aligns the molecule along its principal axes of inertia, ensuring a standardized, aesthetically pleasing perspective for each structure before snapshot capture.

4. **Output Directory & Execution Script**:
   - Standalone execution script connects via `ViewerBridge` to headless Chrome, creates `/Users/charlie/code/scratch/mega_renders/`, iterates over the 4 structures $\times$ 3 aesthetics, and writes all 12 snapshots.

---

## 3. Caveats

1. **Biological Assembly vs Asymmetric Coordinates for 1FHA**:
   - The asymmetric unit of 1FHA contains only 1–2 chains. If `assembly="asymmetric"` were mistakenly used, Ferritin would render as a disjointed 4-helix bundle rather than the hollow spherical nanocage. `assembly="biological"` must be used.
2. **Handle State Transition**:
   - Applying `preset("origami")` or `preset("seaglass")` creates active handle `"auto_view"`. When switching between aesthetics in a loop on the same loaded structure, re-fetching or explicitly targeting the active handle ensures clean parameter application.
3. **Hardware Acceleration vs CI Headless Flags**:
   - On macOS local runs, `--headless=new` without software-gl flags utilizes Metal hardware acceleration. If executed in headless CI environments without GPU, SwiftShader flags (`--use-gl=angle --use-angle=swiftshader`) should be supplied via `PROTEAN_CHROME_FLAGS`.

---

## 4. Conclusion

The Protean API and WebGL shader infrastructure fully support generating publication-ready renders for all 12 structure/aesthetic combinations.

### Standalone Production Script Design

```python
#!/usr/bin/env python3
"""Protean Mega Renders Generator: 12 Publication Snapshots across 4 PDBs and 3 Aesthetics."""

import asyncio
from pathlib import Path
import shutil
import subprocess
import tempfile

import protean_mcp.server as server
from protean_mcp.connection import ViewerBridge
from tests.browser import STATIC, find_chrome
from tests.conftest import free_port

STRUCTURES = [
    ("1FHA", "Ferritin 24-mer Nanocage"),
    ("5JQ3", "Cas9-sgRNA-DNA Complex"),
    ("1F88", "Bovine Rhodopsin 7TM GPCR"),
    ("1GFL", "Green Fluorescent Protein"),
]

AESTHETICS = ["glass", "seaglass", "origami"]
OUTPUT_DIR = Path.home() / "code" / "scratch" / "mega_renders"


async def generate_mega_renders():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory initialized: {OUTPUT_DIR}")

    bridge = ViewerBridge(port=free_port(), static_dir=STATIC)
    await bridge.start()
    profile = tempfile.mkdtemp(prefix="protean-mega-")
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("No Chrome binary located.")

    proc = subprocess.Popen([
        chrome,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--headless=new",
        "--window-size=1200,1200",
        bridge.viewer_url,
    ])

    try:
        await bridge.wait_for_viewer(40)
        server.use_bridge(bridge)

        for pdb_id, description in STRUCTURES:
            print(f"\n==========================================")
            print(f"Processing {pdb_id}: {description}")
            print(f"==========================================")

            for aesthetic in AESTHETICS:
                out_path = OUTPUT_DIR / f"{pdb_id.lower()}_{aesthetic}.png"
                print(f"  -> Rendering aesthetic '{aesthetic}' -> {out_path.name}...")

                # 1. Clean slate & load biological structure
                await server.clear_viewer()
                await server.fetch_structure(pdb_id, assembly="biological")
                await server.orient()

                # 2. Apply aesthetic
                if aesthetic == "glass":
                    await server.material(finish="glass", name="auto")
                    await server.lighting(rig="studio")
                    await server.background(color="#ffffff")
                elif aesthetic == "seaglass":
                    await server.preset("seaglass")
                elif aesthetic == "origami":
                    await server.preset("origami")

                # 3. Capture high-DPI double-column snapshot
                await server.snapshot(
                    str(out_path),
                    column="double",
                    dpi=300,
                    format="png",
                    overwrite=True,
                )
                print(f"     Saved {out_path.name} ({out_path.stat().st_size:,} bytes)")

    finally:
        proc.terminate()
        subprocess.run(["pkill", "-f", f"user-data-dir={profile}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await bridge.stop()
        shutil.rmtree(profile, ignore_errors=True)

    print("\nAll 12 mega renders successfully generated!")


if __name__ == "__main__":
    asyncio.run(generate_mega_renders())
```

---

## 5. Verification Method

### Test Commands
Run the differential and preset validation suites to confirm all shaders, presets, and finishes execute cleanly without runtime errors:

```bash
# 1. Verify Glass and Seaglass differential suite
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_glass_differential.py -v

# 2. Verify Origami differential suite
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_origami_differential.py -v

# 3. Verify Server tools and capability registration
uv run pytest tests/test_server.py -k "test_preset or test_material" -v
```

### Artifact & Image Invariant Checks
1. **File Existence & Non-Empty Check**: All 12 files exist in `/Users/charlie/code/scratch/mega_renders/` with file size > 50 KB each.
2. **Resolution & DPI Check**: Dimensions are 2,161 px wide with 300 DPI metadata in PNG chunks.
3. **Ink Coverage Check**: Each frame passes `_ink(path) > 0.02` ensuring non-blank renders.

### Invalidation Conditions
- Changes to `MATERIAL_FINISHES` or `SHADING_STYLES` in `viewer/src/dispatch.ts`.
- Modifications to `_origami_style` or `_preset_seaglass` in `src/protean_mcp/server.py`.
- Renaming or schema changes to `server.snapshot()`, `server.material()`, or `server.fetch_structure()`.
