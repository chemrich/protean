# E2E Test Infra: Refractive Glass & Frosted Seaglass Shaders

## Test Philosophy
- Opaque-box, requirement-driven. Derives from ORIGINAL_REQUEST.md.
- Verification methodology:
  1. Category-Partition + Boundary Value Analysis for Python tools and WebSocket RPC.
  2. Pairwise combinations (finishes x representations x lighting x coloring).
  3. Real-world application scenarios (loading structures 1ubq, 1crn, applying finishes/presets, capturing snapshots).
  4. Mathematical pixel differential assertions (coverage > 0.02, delta vs baseline > 0.005).
  5. Saved snapshot PNG artifacts for visual inspection.

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| 1 | `material(finish="glass")` | ORIGINAL_REQUEST §R1, §R2 | 5 | 5 | ✓ |
| 2 | `material(finish="seaglass")` | ORIGINAL_REQUEST §R1, §R2 | 5 | 5 | ✓ |
| 3 | `preset("seaglass")` | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 4 | Capabilities & Schema Sync | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 5 | Snapshot & Visual Quality | ORIGINAL_REQUEST §Acceptance Criteria | 5 | 5 | ✓ |

## Test Architecture
- **Unit & Protocol Tests**:
  - `tests/test_server.py`: Fast offline tests verifying `material(finish="glass")`, `material(finish="seaglass")`, `preset("seaglass")`, and `capabilities()`.
  - `viewer/src/dispatch.test.ts`: Vitest unit tests verifying `MATERIAL_FINISHES` values, parameter overrides, error cases for invalid finishes.
  - `tests/test_page_invoke.py`: Verification that `_PAGE_VIEWS` includes `"seaglass"`.
  - `tests/test_docs_generated.py`: Verification that `docs/generate/tool_reference.py --check` passes.
- **Differential & Integration Tests**:
  - `tests/test_glass_differential.py`: Full headless Chrome CDP session (`tests/browser.py`), loading standard structures (`1ubq`, `1crn`), applying `glass` material and `preset("seaglass")`, asserting non-crash execution and non-zero pixel coverage / delta, saving PNG snapshots in `tests/snapshots/`.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Ubiquitin (1ubq) clear glass rendering & snapshot | F1, F3, F5, F8, F9 | Medium |
| 2 | Ubiquitin (1ubq) seaglass preset with seafoam tint & snapshot | F2, F4, F6, F8, F9 | Medium |
| 3 | Crambin (1crn) glass material with custom roughness/bump override | F1, F2, F5, F8 | High |
| 4 | Multi-representation complex (cartoon + surface) with seaglass finish | F2, F4, F5, F8 | High |
| 5 | Sequential finish transitions (matte -> glass -> seaglass -> preset("seaglass")) | F1, F2, F5, F6, F8 | High |

## Coverage Thresholds
- Tier 1: ≥5 per feature
- Tier 2: ≥5 per feature (boundary and error cases: invalid finish, bounds on roughness/bumpiness, missing structure)
- Tier 3: pairwise combinations of finishes with representations and color modes
- Tier 4: ≥5 realistic application scenarios with snapshot generation
