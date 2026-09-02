# Handoff Report: E2E Test Suite for Refractive Glass and Seaglass Shaders

## 1. Observation
- Read requirements from `/Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md`, `/Users/charlie/code/protean/PROJECT.md`, and `/Users/charlie/code/protean/TEST_INFRA.md`.
- `ORIGINAL_REQUEST.md` lines 18–34 specify:
  - `glass`: perfectly clear, smooth, highly refractive transmission material.
  - `seaglass`: frosted, tumbled glass variant with high surface roughness diffusing refraction.
  - Protean Python API exposure: `material(finish="glass")`, `material(finish="seaglass")`, and `preset("seaglass")` (seafoam green/blue tint `#73b9a2`).
  - Acceptance criteria: programmatic tests loading structures (1ubq, 1crn), applying finishes/presets, capturing snapshots without WebGL or Python runtime errors, coverage > 0.02, delta > 0.005.
- Existing differential infrastructure in `tests/browser.py` and `tests/pixels.py` provides `viewer_session`, `Render`, `coverage`, `difference`, `background`, `corners`, and `transparent_fraction`.
- Existing test patterns in `tests/test_origami_differential.py`, `tests/test_server.py`, and `viewer/src/dispatch.test.ts` established conventions for unit and differential testing.

## 2. Logic Chain
1. Created `tests/test_glass_differential.py` with comprehensive coverage across Tiers 1–4:
   - **Tier 1 (Feature Coverage)**: Direct dispatch and schema verification for `material(finish="glass")`, `material(finish="seaglass")`, `preset("seaglass")`, `capabilities()` reporting both finishes and the preset, and differential delta vs baseline.
   - **Tier 2 (Boundary & Corner Cases)**: Roughness and metalness overrides on `glass`, bumpiness and frequency overrides on `seaglass`, invalid finish name rejection with informative messages, out-of-bounds parameter validation (ranges for roughness, metalness, bumpiness, bump_frequency, emissive), unshown handle error handling, and custom handle targeting for `preset("seaglass")`.
   - **Tier 3 (Cross-Feature Combinations)**: Cross-testing finishes against representations (`cartoon`, `spacefill`, `surface`, `ball-and-stick`), lighting rigs (`three-point`, `studio`, `rim`, `flat`, `ring`), color modes (uniform `#73b9a2`, `secondary-structure`, `chain-id`, `element-symbol`), and backgrounds (white, dark, radial gradient, transparent).
   - **Tier 4 (Real-World Application Scenarios)**:
     - Scenario 1: Ubiquitin (`1ubq`) clear glass rendering, asserting coverage > 0.02 and delta > 0.005, saving snapshot to `tests/snapshots/1ubq_glass_snapshot.png`.
     - Scenario 2: Ubiquitin (`1ubq`) seaglass preset rendering, asserting coverage > 0.02 and delta > 0.005, saving snapshot to `tests/snapshots/1ubq_seaglass_preset_snapshot.png`.
     - Scenario 3: Crambin (`1crn`) glass material with custom roughness and bump overrides, asserting coverage > 0.02 and delta > 0.005.
     - Scenario 4: Multi-representation composite (cartoon + molecular surface) on `1crn` with seaglass finish, asserting coverage > 0.02.
     - Scenario 5: Sequential finish transitions (`matte` -> `glass` -> `seaglass` -> `preset("seaglass")`), asserting differential delta > 0.005 between transitions.
2. Added unit tests in `tests/test_server.py`:
   - `test_material_glass_finish`
   - `test_material_seaglass_finish`
   - `test_material_glass_with_explicit_overrides`
   - `test_preset_seaglass_coordinates_recipe`
   - `test_capabilities_reports_glass_and_seaglass_finishes`
3. Added unit tests in `viewer/src/dispatch.test.ts`:
   - Capabilities check updated to include `glass` and `seaglass` in `material_finishes`.
   - Glass material finish unit test (`metalness: 0`, `roughness: 0.05`, `bumpiness: 0`).
   - Seaglass material finish unit test (`metalness: 0`, `roughness: 0.7`, `bumpiness: 0.45`, `bump_frequency: 4.0`).
   - Updated unknown finish regex to maintain compatibility.
4. Published `TEST_READY.md` at project root with runner commands and coverage breakdown.

## 3. Caveats
- Differential browser tests in `tests/test_glass_differential.py` require a browser environment and `PROTEAN_DIFFERENTIAL=1` to run (standard for all differential suites in this repository). Offline unit tests in `tests/test_server.py` and `viewer/src/dispatch.test.ts` run standalone without browser requirements.

## 4. Conclusion
The comprehensive test suite for Glass and Seaglass shaders in Mol* and Protean has been fully created and integrated. It covers unit, protocol, schema validation, boundary, cross-feature, differential pixel math, and real-world snapshot generation across all four tiers.

## 5. Verification Method
To independently verify the test suite:

1. **Run Python Server Unit Tests**:
   ```bash
   uv run pytest tests/test_server.py -k "glass or seaglass" -v
   ```

2. **Run TypeScript / Vitest Dispatch Tests**:
   ```bash
   cd viewer && npx vitest run src/dispatch.test.ts
   ```

3. **Run Full Differential Test Suite (Tiers 1–4)**:
   ```bash
   PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_glass_differential.py -v
   ```
