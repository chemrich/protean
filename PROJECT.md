# Project: Protean Mega Renders Generator

## Architecture
A standalone, high-performance Python script utilizing the Protean Python API and Mol* WebGL viewer bridge to produce 12 publication-ready renders across 4 macromolecular structures and 3 visual aesthetics.

- **PDB Structures**:
  1. `1FHA`: Human Ferritin heavy chain 24-mer spherical nanocage (biological assembly).
  2. `5JQ3`: SpyCas9-sgRNA-DNA endonuclease complex.
  3. `1F88`: Bovine Rhodopsin 7-transmembrane GPCR with bound 11-cis retinal.
  4. `1GFL`: Green Fluorescent Protein 11-stranded $\beta$-barrel with central fluorophore.

- **Aesthetics & Rendering Recipes**:
  1. **Glass**: Clear refractive dielectric finish (`roughness=0.05, metalness=0.0, bumpiness=0.0`), studio lighting (`lighting(rig="studio")`), pure background (`#ffffff`), Snell refraction (IOR=1.50, strength=0.08), Schlick Fresnel (F0=0.04), 3-tap spectral chromatic dispersion (spread=0.02), Beer-Lambert absorption.
  2. **Seaglass**: Frosted sea glass preset (`preset("seaglass")` / `material(finish="seaglass", roughness=0.7, bumpiness=0.45, bump_frequency=4.0)`), three-point lighting (`ambient=0.45`), ambient occlusion (`occlusion=True, shadow=False`), seafoam green tint (`#73b9a2`), 12-tap Vogel spiral diffusion blur.
  3. **Origami**: Folded paper preset (`preset("origami")` / `shading(style="origami")`, `material(finish="origami", roughness=1.0, bumpiness=0.45, bump_frequency=4.5)`), square trace profiles, sharp facet creases (`flatShaded: true`), secondary structure coloring, ambient occlusion, three-point lighting, warm washi paper ground (`#f6f4eb`).

- **Snapshot Specifications**:
  - Double column width (183 mm $\rightarrow$ 2,161 px wide), 300 DPI lossless PNG.
  - Settle render queue (`settleRender()`) before snapshot capture.
  - Output directory: `/Users/charlie/code/scratch/mega_renders/`

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | PDB Fetching & Assembly | Programmatic fetching and biological assembly loading for 1FHA, 5JQ3, 1F88, 1GFL | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Glass Aesthetic Pipeline | Refractive transmission, dispersion, studio lighting, clear framing | M1 | ORIGINAL_REQUEST §R1, §R2 |
| 3 | Seaglass Aesthetic Pipeline | Frosted Vogel spiral diffusion, seafoam tint, bump texture, AO | M1 | ORIGINAL_REQUEST §R1, §R2 |
| 4 | Origami Aesthetic Pipeline | Flat-shaded faceted creases, square profiles, paper tooth, washi ground | M1 | ORIGINAL_REQUEST §R1, §R2 |
| 5 | Publication Snapshot Capture | Double-column 300 DPI high-res capture with queue settling | M1 | ORIGINAL_REQUEST §R2 |
| 6 | 12-File Output Matrix Generation | Execution of all 12 renders saved to `/Users/charlie/code/scratch/mega_renders/` | M1 | ORIGINAL_REQUEST §Acceptance |
| 7 | Multi-Agent Review & Challenge | Code review, execution validation, image dimension/ink checks | M2 | Acceptance Criteria |
| 8 | Forensic Integrity & Visual Audit | Independent audit of non-cheating, authentic rendering & visual quality | M2 | Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Script Implementation & Generation | Write standalone script and execute to generate all 12 mega render PNGs in target directory | none | DONE |
| 2 | Verification, Review & Forensic Audit | Reviewers, Challengers, and Forensic Auditor verify code execution, image properties, and visual quality | M1 | DONE |

## Code Layout
- Script location: `scripts/generate_mega_renders.py`
- Test suite: `tests/test_mega_renders.py`
- Output renders: `/Users/charlie/code/scratch/mega_renders/{1fha,5jq3,1f88,1gfl}_{glass,seaglass,origami}.png`

## Interface Contracts
### `generate_mega_renders.py` Execution Contract
- Inputs: None (standalone CLI or executable python module)
- Outputs: 12 PNG files in `/Users/charlie/code/scratch/mega_renders/`:
  - `1fha_glass.png`, `1fha_seaglass.png`, `1fha_origami.png`
  - `5jq3_glass.png`, `5jq3_seaglass.png`, `5jq3_origami.png`
  - `1f88_glass.png`, `1f88_seaglass.png`, `1f88_origami.png`
  - `1gfl_glass.png`, `1gfl_seaglass.png`, `1gfl_origami.png`
- Return code: 0 on clean completion with no WebGL or Python runtime errors.
- Image requirements: Each file > 50 KB, width 2,161 px, 300 DPI metadata, valid non-empty PNG.
