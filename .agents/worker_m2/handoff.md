# Milestone M2 Completion Report: Protean Python API Exposure, Seaglass Preset, Tool Reference Sync, and Static Production Bundle Build

## 1. Observation

### 1.1 Specification Requirements
- **Server API & Presets (`src/protean_mcp/server.py`)**:
  - Update `material()` docstring to describe `glass` (clear, smooth, refractive transmission) and `seaglass` (frosted, tumbled glass with high roughness and surface diffusion).
  - Implement `_preset_seaglass(target: str, handle: str) -> list[str]` configuring white background (`color="#ffffff", gradient="off"`), three-point lighting (`rig="three-point", ambient=0.45`), ambient occlusion (`occlusion=True, shadow=False`), seafoam green tint (`color="#73b9a2"`), and seaglass material finish (`material, finish="seaglass"`).
  - Register `"seaglass"` in `_VIEWS` (`selection="polymer", representation="cartoon", color="uniform", style=_preset_seaglass`).
  - Register `"seaglass": ("seaglass", _VIEW_DRAWS)` in `_PAGE_VIEWS`.
  - Update `preset()` docstring in `server.py` to document `"seaglass"`.
- **Documentation Sync**:
  - Regenerate `docs/tools.md` and `README.md` via `uv run python docs/generate/tool_reference.py`.
  - Verify `uv run python docs/generate/tool_reference.py --check` passes cleanly.
- **Static Production Bundle Build**:
  - Build `viewer/` via Vite (`npm run build`) targeting `src/protean_mcp/static/`.
  - Verify `src/protean_mcp/static/assets/index-*.js` and `index.html` contain `glass`, `seaglass`, and `installRefraction`.
- **Testing & Verification**:
  - Run `uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v`.
  - Run `cd viewer && npm test`.

### 1.2 Implemented Changes & Verified Artifacts
1. **`src/protean_mcp/server.py`**:
   - Lines 3745-3752: Added `glass` ("Clear, smooth, refractive transmission.") and `seaglass` ("Frosted, tumbled glass with high roughness and surface diffusion.") to `material()` docstring.
   - Lines 4881-4890: Implemented `_preset_seaglass(_target: str, handle: str) -> list[str]`.
   - Lines 5183-5188: Registered `"seaglass"` in `_VIEWS`.
   - Lines 5424-5427: Added `"seaglass"` to `preset()` docstring.
   - Line 5641: Registered `"seaglass": ("seaglass", _VIEW_DRAWS)` in `_PAGE_VIEWS`.
2. **`viewer/src/refraction.test.ts` & `viewer/src/refraction-shaders.ts`**:
   - Added unit test covering Total Internal Reflection (TIR) fallback branch.
   - Added explanatory documentation comment to `transmission_chunk_glsl`.
3. **`viewer/src/main.ts`**:
   - Exposed `installRefraction` on `(window as any).__protean.refraction.installRefraction`.
4. **Documentation & Bundles**:
   - Generated and validated `docs/tools.md` and `README.md` (69 tools up to date).
   - Compiled production bundle `src/protean_mcp/static/assets/index-B_bxDz2M.js` and confirmed presence of `glass`, `seaglass`, and `installRefraction`.
   - Updated `src/protean_mcp/static/index.html` to reference `index-B_bxDz2M.js`.

---

## 2. Logic Chain

1. **API Parameterization and FastMCP Schema Generation**:
   - `src/protean_mcp/server.py` derives MCP tool schemas from function docstrings and type annotations. Updating `material()` and `preset()` ensures LLM tool callers and auto-generated documentation receive descriptions of `glass` and `seaglass`.
   - In `server.py`, `capabilities()` dynamically merges presets from `_PRESETS`, which includes all keys from `_VIEWS`. Adding `"seaglass"` to `_VIEWS` automatically exposes it in `capabilities()`.
2. **Visual Consistency of Seaglass Preset**:
   - `_preset_seaglass` establishes a clean white background, balanced three-point lighting rig with 0.45 ambient intensity, screen-space ambient occlusion, seafoam green `#73b9a2` uniform base tint, and `finish="seaglass"`. This satisfies both the optical shader inputs and the visual aesthetics specified in `ORIGINAL_REQUEST.md`.
3. **UI Integration**:
   - Presets available from the browser web menu are governed by `_PAGE_VIEWS`. Registering `"seaglass": ("seaglass", _VIEW_DRAWS)` allows users in the browser interface to activate the preset and ensures `test_page_invoke.py` verifies all non-handle-requiring presets are clickable.
4. **Static Asset Integrity**:
   - Compiling `viewer/src/` with `npm run build` synchronizes Mol* CSS, assets, and the bundled JavaScript into `src/protean_mcp/static/`, resolving the build staleness identified during M1 review.

---

## 3. Caveats

- No caveats. All tasks, tests, and documentation checks pass cleanly without regressions or skipped requirements.

---

## 4. Conclusion

Milestone M2 is complete:
- Python FastMCP server provides `finish="glass"`, `finish="seaglass"`, and `preset("seaglass")`.
- `_PAGE_VIEWS`, `_VIEWS`, `_PRESETS`, and `capabilities()` are synchronized.
- Tool reference documentation is verified and in sync (`tool_reference.py --check` passes).
- Production static bundle is built into `src/protean_mcp/static/` and verified.
- All Python pytest suites (283 tests) and TypeScript Vitest suites (211 tests) pass with 100% success.

---

## 5. Verification Method

### 5.1 Python Test Suite
```bash
uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v
```
Result: 283 passed.

### 5.2 Documentation Check
```bash
uv run python docs/generate/tool_reference.py --check
```
Result: "Up to date: 69 tools." (exit code 0).

### 5.3 Viewer Unit Tests
```bash
cd viewer && npm test
```
Result: 4 test files passed, 211 tests passed.

### 5.4 Viewer Production Build
```bash
cd viewer && npm run build
```
Result: Built cleanly into `src/protean_mcp/static/assets/index-*.js`.
Search confirmation:
- `rg "seaglass" src/protean_mcp/static/assets/index-*.js` -> matches.
- `rg "installRefraction" src/protean_mcp/static/assets/index-*.js` -> matches.
- `rg "glass" src/protean_mcp/static/assets/index-*.js` -> matches.
