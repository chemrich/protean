# Test Ready: Refractive Glass & Frosted Seaglass Shaders

Comprehensive test suite for the Refractive Glass and Frosted Seaglass shaders in Mol* and Protean across Tiers 1–4.

---

## 1. Test Suite Overview

| Test Target | File Path | Test Count | Description |
|-------------|-----------|:----------:|-------------|
| **Differential & Snapshot Suite (Tiers 1–4)** | `tests/test_glass_differential.py` | 24+ parameterized | Full headless Chrome session validating WebGL/Python execution, pixel coverage (>0.02), differential delta (>0.005), parameter boundaries, cross-feature combinations, and artifact saving |
| **Server Tool Unit Tests** | `tests/test_server.py` | 5 | Fast offline unit tests verifying `material(finish="glass")`, `material(finish="seaglass")`, overrides, `preset("seaglass")`, and `capabilities()` |
| **Viewer Dispatch Unit Tests** | `viewer/src/dispatch.test.ts` | 4 | Vitest unit tests verifying `MATERIAL_FINISHES` definitions, capabilities registration, and finish validation |

---

## 2. Test Execution Commands

### Fast Offline Unit Tests (Python)
```bash
uv run pytest tests/test_server.py -k "glass or seaglass" -v
```

### Viewer Dispatch Unit Tests (TypeScript / Vitest)
```bash
cd viewer && npx vitest run src/dispatch.test.ts
```

### Full Headless Differential Test Suite (Tiers 1–4)
```bash
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_glass_differential.py -v
```

---

## 3. Detailed Coverage Breakdown

### Tier 1: Feature Coverage
- `test_tier1_capabilities_reports_glass_and_seaglass`: Validates that `capabilities()` reports `"glass"` and `"seaglass"` in `material_finishes`, and `"seaglass"` in `presets`.
- `test_tier1_material_finish_glass_direct_dispatch`: Validates direct dispatch of `material(finish="glass")` sets `metalness: 0`, `roughness: 0.05`, `bumpiness: 0`.
- `test_tier1_material_finish_seaglass_direct_dispatch`: Validates direct dispatch of `material(finish="seaglass")` sets `metalness: 0`, `roughness: 0.7`, `bumpiness: 0.45`, `bump_frequency: 4.0`.
- `test_tier1_preset_seaglass_direct_dispatch`: Validates `preset("seaglass")` applies seafoam tint `#73b9a2`, frosted finish, and three-point lighting.
- `test_tier1_glass_material_differential_vs_baseline`: Validates `glass` produces non-zero delta (>0.005) vs baseline matte render.
- `test_tier1_seaglass_material_differential_vs_baseline`: Validates `seaglass` produces non-zero delta (>0.005) vs baseline matte render.

### Tier 2: Boundary & Corner Cases
- `test_tier2_material_glass_roughness_override`: Validates explicit `roughness` overrides on `glass`.
- `test_tier2_material_glass_metalness_override`: Validates explicit `metalness` overrides on `glass`.
- `test_tier2_material_seaglass_bump_override`: Validates `bumpiness` and `bump_frequency` overrides on `seaglass`.
- `test_tier2_material_seaglass_roughness_override`: Validates custom `roughness` overrides on `seaglass`.
- `test_tier2_material_invalid_finish_rejected`: Validates informative error when invalid finish name is provided.
- `test_tier2_material_out_of_bounds_parameters_rejected`: Validates rejection of out-of-range parameter values (roughness, metalness, bumpiness, bump_frequency, emissive).
- `test_tier2_material_unshown_handle_refused`: Validates error handling for unshown/non-existent selection handles.
- `test_tier2_preset_seaglass_with_custom_handle`: Validates `preset("seaglass")` targeting specific selection handle.

### Tier 3: Cross-Feature Combinations
- `test_tier3_glass_finishes_across_representations`: Parameterized testing across representations (`cartoon`, `spacefill`, `surface`, `ball-and-stick`).
- `test_tier3_glass_finishes_with_lighting_rigs`: Parameterized testing across lighting rigs (`three-point`, `studio`, `rim`, `flat`, `ring`).
- `test_tier3_glass_finishes_with_color_modes`: Parameterized testing across color themes (seafoam `#73b9a2`, `secondary-structure`, `chain-id`, `element-symbol`).
- `test_tier3_glass_finishes_with_backgrounds`: Parameterized testing across backgrounds (white `#ffffff`, dark `#111111`, radial gradient, transparent canvas).

### Tier 4: Real-World Application Scenarios & Artifact Generation
- `test_tier4_scenario1_ubiquitin_1ubq_glass_snapshot`:
  - Structure: Ubiquitin (`1ubq`)
  - Target: Clear glass material
  - Assertions: coverage > 0.02, differential delta > 0.005
  - Artifact: `tests/snapshots/1ubq_glass_snapshot.png`
- `test_tier4_scenario2_ubiquitin_1ubq_seaglass_preset_snapshot`:
  - Structure: Ubiquitin (`1ubq`)
  - Target: High-level seaglass preset (seafoam tint `#73b9a2`, frosted glass, three-point lighting)
  - Assertions: coverage > 0.02, differential delta > 0.005
  - Artifact: `tests/snapshots/1ubq_seaglass_preset_snapshot.png`
- `test_tier4_scenario3_crambin_1crn_glass_roughness_override`:
  - Structure: Crambin (`1crn`)
  - Target: Glass material with custom roughness and bump overrides
  - Assertions: coverage > 0.02, differential delta > 0.005
- `test_tier4_scenario4_multi_representation_complex_seaglass`:
  - Structure: Crambin (`1crn`)
  - Target: Multi-representation composite (cartoon + molecular surface) with seaglass finish
  - Assertions: coverage > 0.02
- `test_tier4_scenario5_sequential_finish_transitions`:
  - Structure: Ubiquitin (`1ubq`)
  - Transitions: `matte` -> `glass` -> `seaglass` -> `preset("seaglass")`
  - Assertions: coverage > 0.02 and differential delta > 0.005 across consecutive transitions

---

## 4. Pass Criteria & Thresholds
- **Pixel Coverage**: `coverage(render) > 0.02` (2% minimum frame coverage to verify non-empty render).
- **Differential Delta**: `difference(render_a, render_b) > 0.005` (0.5% frame difference confirming visual effect application).
- **Snapshot Artifacts**: Output PNGs in `tests/snapshots/` verified to exist and have non-trivial size (>1000 bytes).
