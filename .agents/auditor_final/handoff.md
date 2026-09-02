# Final Forensic Integrity Audit Report: Refractive Glass & Frosted Seaglass Shaders

**Work Product**: Refractive Glass & Frosted Seaglass Shaders in Mol* and Protean
**Profile**: General Project (Integrity Forensics)
**Integrity Mode**: Development Mode (from `ORIGINAL_REQUEST.md`)
**Verdict**: 🔴 **INTEGRITY VIOLATION**

---

## 1. Observation

### 1.1 Test Suite Execution Failure
- **Command**: `uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v`
- **Result**: `1 failed, 285 passed, 1 warning in 71.89s (exit code 1)`
- **Verbatim Error**:
  ```
  __________________________ test_preset_seaglass_tool ___________________________

  wired_bridge = <tests.conftest.MockViewer object at 0x10f8007d0>
  tmp_path = PosixPath('/private/var/folders/d8/np7yp0nn6cs0pv8rnjb7mhbw0000gn/T/pytest-of-charlie/pytest-623/test_preset_seaglass_tool0')

      async def test_preset_seaglass_tool(wired_bridge, tmp_path):
          """Test preset tool with seaglass."""
          await _load(wired_bridge, _tiny_protein_pdb(tmp_path / "tiny.pdb"))
      
          async with _serving(wired_bridge, **_quiet_viewer()):
  >           reply = await server_mod.preset("seaglass")
                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

  tests/test_server.py:4135: 
  ...
  src/protean_mcp/server.py:4387: in _frame_the_scene
      await reset_view()
  ...
  src/protean_mcp/server.py:4174: in reset_view
      return await _call("reset_view", {})
  ...
  src/protean_mcp/connection.py:269: in request
  >   raise ViewerError(reply.get("error", f"Viewer error on '{action}'"))
  E   protean_mcp.connection.ViewerError: no handler: reset_view
  ```

### 1.2 Hardcoded External Brain Path in Test
- **File**: `tests/test_server.py:4148-4161`
- **Verbatim Code**:
  ```python
  def test_glass_and_seaglass_snapshot_artifacts_present():
      """Verify that 1ubq_glass_snapshot.png and 1ubq_seaglass_preset_snapshot.png exist and are valid."""
      from pathlib import Path
      from PIL import Image

      brain_dir = Path("/Users/charlie/.gemini/antigravity-cli/brain/beb37d02-ca54-499a-81a3-164aa1980484")
      snapshots_dir = Path(__file__).resolve().parent / "snapshots"
      snapshots_dir.mkdir(parents=True, exist_ok=True)

      glass_jpg = brain_dir / "ubq_glass_snapshot_1787915667296.jpg"
      seaglass_jpg = brain_dir / "ubq_seaglass_snapshot_1787915810527.jpg"
  ```
  `test_glass_and_seaglass_snapshot_artifacts_present` references an ephemeral conversation directory outside the workspace (`/Users/charlie/.gemini/antigravity-cli/brain/beb37d02-ca54-499a-81a3-164aa1980484`) to populate test artifacts.

### 1.3 Optical Physics & Shader Implementation (Verified Genuine)
- **`viewer/src/refraction-shaders.ts`**:
  - Snell refraction calculation in `getSnellRefractionOffset()` derives the refracted ray vector $\vec{R} = \text{refract}(-\vec{V}, \vec{N}, \eta)$ with fallback to reflection $\text{reflect}(-\vec{V}, \vec{N})$ under Total Internal Reflection (TIR), scaled inversely with linear view depth and corrected for viewport aspect ratio $\begin{pmatrix} 1.0 \\ W/H \end{pmatrix}$.
  - Dielectric Schlick Fresnel reflectance with $F_0 = 0.04$ using Epic Games exponential curve `exp2((-5.55473 * dotNV - 6.98316) * dotNV)`.
  - 3-tap spectral chromatic dispersion for clear glass (`roughness < 0.1`) offsetting R, G, B channels with spread $\delta = 0.02$.
  - 12-tap Vogel Golden Angle spiral kernel with Gaussian weights (sum = 5.179) and interleaved screen-space dither rotation for frosted seaglass roughness scattering (`roughness >= 0.1`).
  - Procedural 3-octave FBM surface normal perturbation for tumbled beach glass surface facets.
  - Beer-Lambert absorption tinting deepening path thickness $d_{\text{eff}} \in [1.0, 3.5]$ at grazing silhouettes.
- **`viewer/src/refraction.ts`**:
  - Exports pure TS optical math algorithms (`snellRefractionOffset`, `schlickFresnel`, `spectralDispersionOffsets`, `vogelSpiralKernel`, `gaussianWeights`, `beerLambertAbsorption`, `screenSpaceDitherAngle`).
  - Integrates Mol* postprocessing render pass via `installRefraction()`.
- **`viewer/src/dispatch.ts`**:
  - Defines `glass` (`metalness: 0, roughness: 0.05, bumpiness: 0`) and `seaglass` (`metalness: 0, roughness: 0.7, bumpiness: 0.45, bump_frequency: 4.0`) in `MATERIAL_FINISHES`.
- **`src/protean_mcp/server.py`**:
  - `material()` docstring updated with `glass` and `seaglass`.
  - `_preset_seaglass()` applies white background `#ffffff`, 3-point lighting `ambient=0.45`, screen-space occlusion, seafoam tint `#73b9a2`, and `finish="seaglass"`.
  - `_VIEWS["seaglass"]` and `_PAGE_VIEWS["seaglass"]` registered.
- **`src/protean_mcp/static/`**:
  - Production bundle `src/protean_mcp/static/assets/index-B_bxDz2M.js` and `index.html` contain compiled refraction shaders and `installRefraction`.

---

## 2. Logic Chain

1. **Test Execution Integrity**:
   - The integrity forensics protocol specifies: *"Build the project from source and run its test suite. The build must succeed and tests must execute — a project that doesn't build or whose tests don't run is automatically flagged. If ANY check fails, the verdict is INTEGRITY VIOLATION and the work product must be rejected."*
   - Executing `uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v` results in 1 failure: `tests/test_server.py::test_preset_seaglass_tool`.
   - The failure occurs because `test_preset_seaglass_tool` executes `server_mod.preset("seaglass")` inside `_serving(wired_bridge, **_quiet_viewer())`. When `preset("seaglass")` is called without a handle (`handle=None`), `_draw_view` calls `_frame_the_scene("all")`, which invokes `reset_view()`. Because `_quiet_viewer()` does not provide a mock handler for `"reset_view"`, the bridge raises `ViewerError: no handler: reset_view`.
   - Note that `test_preset_seaglass_coordinates_recipe` at line 2536 correctly uses `_preset_calls` (which includes `"reset_view"`), whereas the redundant `test_preset_seaglass_tool` at line 4130 was added with an incomplete mock fixture.

2. **Test Isolation and Artifact Integrity**:
   - `test_glass_and_seaglass_snapshot_artifacts_present` in `tests/test_server.py:4148` references an absolute path to a previous agent conversation directory (`/Users/charlie/.gemini/antigravity-cli/brain/beb37d02-ca54-499a-81a3-164aa1980484`).
   - Unit tests must be self-contained within the repository workspace and not depend on out-of-band ephemeral agent directory states.

3. **Core Shader & Python API Quality**:
   - The mathematical physics and shader implementations in `viewer/src/refraction-shaders.ts`, `viewer/src/refraction.ts`, and `src/protean_mcp/server.py` are completely genuine and free of hardcoded results or facade implementations.
   - The static production bundle is properly compiled and synchronized.

---

## 3. Caveats

- The core optical shaders, dispatch integration, server tools, and documentation are mathematically genuine and fully functional.
- The failure is strictly localized to two test additions in `tests/test_server.py` (`test_preset_seaglass_tool` missing the `"reset_view"` mock handler in `_quiet_viewer()`, and `test_glass_and_seaglass_snapshot_artifacts_present` hardcoding a path to an external brain folder).

---

## 4. Conclusion

**Verdict**: 🔴 **INTEGRITY VIOLATION**

The work product cannot be approved in its current state because:
1. The test suite command `uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v` fails on `tests/test_server.py::test_preset_seaglass_tool`.
2. `tests/test_server.py` contains hardcoded external brain directory paths (`beb37d02-ca54-499a-81a3-164aa1980484`), violating test independence and artifact integrity.

### Remediation Required (for worker/fix agent):
1. In `tests/test_server.py::test_preset_seaglass_tool`, add `"reset_view": lambda args: {}` to the serving handlers (or include `"reset_view"` in `_quiet_viewer()`).
2. In `tests/test_server.py::test_glass_and_seaglass_snapshot_artifacts_present`, remove the external `brain_dir` fallback copy logic and assert directly on workspace files in `tests/snapshots/`.
3. In `tests/save_snapshots.py`, remove or clean up hardcoded external paths.

---

## 5. Verification Method

To reproduce the findings:

1. **Run Python Server Test Suite**:
   ```bash
   uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v
   ```
   *Expected Observation*: 1 failed (`test_preset_seaglass_tool`), 285 passed.

2. **Inspect External Path in `tests/test_server.py`**:
   ```bash
   grep -n "brain" tests/test_server.py
   ```
   *Expected Observation*: Line 4148 references `/Users/charlie/.gemini/antigravity-cli/brain/beb37d02-ca54-499a-81a3-164aa1980484`.
