# Testing and Snapshot Infrastructure Investigation Report

## 1. Observation

### 1.1 Existing Test Suites in the Repository

#### A. Python Test Suite (`pytest`)
- **Configuration**: Defined in `pyproject.toml` (lines 60–63):
  ```toml
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  testpaths = ["tests"]
  ```
- **Structure**: 51 test modules under `/Users/charlie/code/protean/tests/`.
- **Fast / Offline Tests**:
  - Run without browser or network.
  - Tests MCP protocol (`test_server.py`), selections AST (`test_selections.py`, `test_selections_numpy.py`), analysis algorithms (`test_contacts.py`, `test_electrostatics.py`, `test_superposition.py`), session isolation (`test_session_isolation.py`), synthetic pixel math (`test_pixels.py`).
  - Session state isolation is enforced automatically via `conftest.py` lines 145–167 (`@pytest.fixture(autouse=True) def _isolate_session_state()`).
- **Differential / Headless Browser Tests**:
  - Gated behind environment variable `PROTEAN_DIFFERENTIAL=1` via `BROWSER_MARKS` in `tests/browser.py` (lines 64–74):
    ```python
    BROWSER_MARKS = [
        pytest.mark.skipif(
            os.environ.get("PROTEAN_DIFFERENTIAL") != "1",
            reason="needs a browser; set PROTEAN_DIFFERENTIAL=1 to run",
        ),
        pytest.mark.skipif(CHROME is None, reason="no Chrome binary found"),
        pytest.mark.skipif(
            not (STATIC / "index.html").exists(),
            reason="viewer not built (npm run build in viewer/)",
        ),
    ]
    ```
  - Includes `test_render_differential.py`, `test_origami_differential.py`, `test_painterly_differential.py`, `test_selection_differential.py`, `test_viewer_chrome_differential.py`, `test_volumes_differential.py`, etc.
  - Command: `PROTEAN_DIFFERENTIAL=1 uv run pytest tests/ -v`

#### B. Viewer TypeScript Test Suite (`vitest`)
- **Configuration**: `viewer/package.json` (line 13: `"test": "vitest run"`) and `viewer/vite.config.ts` (lines 4–8: `test: { environment: 'jsdom', include: ['src/**/*.test.ts'] }`).
- **Test Modules**:
  - `viewer/src/dispatch.test.ts` (2472 lines): Unit tests for dispatcher actions (`material`, `shading`, `capabilities`, `effects`, `background`, selections, color ramps, summaries) using mock Mol* plugins.
  - `viewer/src/bridge.test.ts`: Tests WebSocket wire protocol, request framing, error handling.
  - `viewer/src/painterly-looks.test.ts`: Tests painterly looks and brush resolution.
- **Command**: `npm test` inside `viewer/` (or `npx vitest run`).

#### C. Continuous Integration (`.github/workflows/ci.yml`)
- Three jobs:
  1. `python`: runs `ruff check`, `ruff format --check`, `mypy` (strict), builds viewer (`npm run build` in `viewer/`), and runs fast `pytest` (`uv run pytest -q -rs`).
  2. `viewer`: runs `npm ci`, `npx tsc --noEmit`, `npm test` (vitest), `npm run build`.
  3. `differential`: installs Chrome stable, builds viewer, runs `PROTEAN_DIFFERENTIAL=1 uv run pytest tests/ -q -rs --durations=25` with flags:
     ```yaml
     PROTEAN_CHROME_FLAGS: >-
       --headless=new --no-sandbox --disable-dev-shm-usage
       --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader
     ```

---

### 1.2 Image & Snapshot Capture Mechanisms

#### A. Headless Browser Driver Architecture (`tests/browser.py`)
- Protean drives Chrome / Chromium directly via Chrome DevTools Protocol (CDP) and WebSocket without external heavy dependencies like Playwright or Puppeteer.
- `viewer_session(pdb_id)` in `tests/browser.py` (lines 165–222):
  1. Downloads/reads structure data via `fetch_structure_data(pdb_id)`.
  2. Starts `ViewerBridge` on a free local port, serving static HTML/JS from `src/protean_mcp/static/`.
  3. Launches Chrome subprocess with temporary `--user-data-dir`, `--remote-debugging-port={cdp_port}`, `--headless=new`, and points to `bridge.viewer_url`.
  4. Waits for WebSocket handshake (`await bridge.wait_for_viewer(40)`).
  5. Dispatches `load_structure` action to the viewer over WebSocket.
  6. Yields `Session(bridge=bridge, url=url, cdp_port=cdp_port)` for test execution.
  7. Cleans up process and kills orphaned Chrome helpers (`pkill -f user-data-dir=...`).

#### B. Viewer-Side Snapshot Pipeline (`viewer/src/dispatch.ts`)
- The viewer provides two rendering capture actions: `screenshot` (lines 3355–3385) and `snapshot` (lines 1865–1950).
- `screenshot`:
  ```typescript
  const helper = plugin.helpers?.viewportScreenshot;
  const pass = helper.imagePass; // builds and caches pass
  const data_uri = await helper.getImageDataUri();
  return { data_uri };
  ```
- `snapshot`:
  - Accepts resolution `{ width, height, transparent, crop, recolour }`.
  - Dynamically configures `helper.behaviors.values.next(...)` with custom resolution.
  - Optional `helper.autocrop(0.05)`.
  - Generates PNG via `helper.getImageDataUri()` and returns `{ data_uri, requested_width, requested_height, ... }`.
- **Render Settling Guard**:
  - In `viewer/src/dispatch.ts` (lines 529–558, 604–665), `settleRender()` monitors `canvas3d.commitQueueSize` and `canvas3d.reprCount` over requestAnimationFrame frames to guarantee that Mol* WebGL geometry and shader compilation are fully committed before pixels are captured.

#### C. Python Server & Test Pixel Harness (`src/protean_mcp/server.py` & `tests/pixels.py`)
- `protean_mcp.server.snapshot()` (lines 2510–2570) and `protean_mcp.server.screenshot()` (lines 7278–7308) request image captures over the bridge, decode base64 data URIs, and write PNG/TIFF/JPEG files with DPI metadata.
- `tests/pixels.py` provides pure functional image analysis over decoded `Render` (shape `(height, width, 4)` uint8 numpy array):
  - `decode(source: str | bytes) -> Render`: decodes PNG bytes/data URIs.
  - `coverage(render: Render, of: RGBA | None, tolerance: int = 8) -> float`: measures non-background pixel ratio (> 0.02 confirms the molecule is rendered and visible).
  - `difference(a: Render, b: Render, tolerance: int = 8) -> float`: measures fraction of pixels differing between two renders (threshold for material/shading changes).
  - `background(render: Render) -> RGBA`: computes corner background color.
  - `color_fraction(render: Render, color: RGBA, tolerance: int = 8) -> float`: fraction of pixels matching an exact color.

---

### 1.3 Available Sample Structure Files

- **Remote / Cached Structures**:
  - `src/protean_mcp/fetch.py` provides `fetch_structure_data(identifier)` (lines 65–123), resolving 4-character PDB IDs from `https://files.rcsb.org/download/{pdb_id}.cif` and caching them to `~/.cache/protean/structures/{pdb_id}.cif`.
  - **`1ubq`** (Ubiquitin, 76 residues, 602 atoms): Primary standard test fixture used in `test_render_differential.py`, `test_origami_differential.py`, and `test_painterly_differential.py`. Well-suited for testing glass refraction across alpha helices and beta sheets.
  - **`1crn`** (Crambin, 46 residues, 327 atoms): Small, highly ordered plant seed protein with helices and sheets. Used in `test_origami_differential.py`.
  - **`4hhb`** (Deoxyhemoglobin, 574 residues): Multi-chain complex with bound heme ligands.
  - **`1tup`** (p53 core domain bound to DNA): Protein-DNA complex for nucleic acid testing.
- **In-Memory Text Structures**:
  - `tests/test_superposition.py` contains `TINY_PDB` and `BACKWARDS_OCCUPANCY_PDB` for synthetic/offline testing.

---

### 1.4 Acceptance Test Architecture (Ref: `test_origami_differential.py`)

- `tests/test_origami_differential.py` (149 lines) provides the direct blueprint for verifying new materials and presets:
  1. Executes `viewer_session("1ubq")` or `viewer_session("1crn")`.
  2. Captures baseline render and verifies `coverage(baseline) > 0.02`.
  3. Adopts server bridge context via `async with _as_server(session, load=True, pdb_id="1ubq"):`.
  4. Invokes server-level or bridge-level actions:
     - `await server_mod.material(finish="glass")` or `await session.request("material", {"name": "auto", "finish": "glass"})`
     - `await server_mod.preset("seaglass")`
  5. Captures output renders using `_capture_shot(session)`.
  6. Asserts pixel coverage (`coverage(render) > 0.02`) and differential delta (`difference(baseline, render) > 0.005`).
  7. Saves snapshots to `tests/snapshots/<filename>.png` for independent Agent-as-Judge inspection.
  8. Checks `capabilities` RPC reports the new finish and preset names.

---

## 2. Logic Chain

1. **Test Infrastructure Selection**:
   - Observations 1.1A and 1.2A show that Protean already contains a fully automated headless Chrome differential test harness in `tests/browser.py` and `tests/pixels.py`.
   - Adding external Playwright or Puppeteer packages is unnecessary and would duplicate existing capabilities; `tests/browser.py` directly handles headless Chrome execution, CDP communication, and WebSocket bridge integration.
2. **Snapshot Generation & Quality Validation**:
   - Observation 1.2B demonstrates that `plugin.helpers.viewportScreenshot.getImageDataUri()` and `settleRender()` guarantee asynchronous WebGL rendering is complete before screenshot capture.
   - Observation 1.2C demonstrates that `tests/pixels.py` provides mathematical verification (coverage > 0.02, difference vs baseline > threshold).
   - Observation 1.4 shows that saving PNG snapshots to `tests/snapshots/` satisfies the requirement for downstream Agent-as-Judge visual verification.
3. **Structure Fixture Selection**:
   - Observation 1.3 shows that `1ubq` and `1crn` are lightweight, reliable, and already cached across existing differential suites, making them the optimal choices for glass and seaglass acceptance tests.
4. **Scope of Test Suite Deliverables**:
   - Verification of the new glass shader and seaglass preset requires:
     a. **Vitest unit tests** in `viewer/src/dispatch.test.ts` for schema validation and material finish resolution.
     b. **Pytest server unit tests** in `tests/test_server.py` for Python MCP tool argument parsing, error checking, and preset step definitions.
     c. **Differential integration acceptance tests** in `tests/test_glass_differential.py` running headless Chrome, capturing snapshots, saving PNG files, and asserting non-zero differential rendering without WebGL or Python runtime errors.

---

## 3. Caveats

1. **Hardware vs Software WebGL Rendering**:
   - In CI or headless Linux environments using SwiftShader (`--use-angle=swiftshader`), WebGL extensions and rendering fidelity may differ slightly from a real GPU (e.g. minor sub-pixel antialiasing differences). Thresholds in `test_pixels.py` (TOLERANCE = 8) account for this, but custom glass refraction shaders must be tested to ensure they do not rely on missing WebGL extensions in headless mode.
2. **Viewer Build Dependency**:
   - Differential tests require `npm run build` in `viewer/` so that `src/protean_mcp/static/index.html` and bundled JS exist.
3. **Opt-in Gate**:
   - Differential tests skip by default unless `PROTEAN_DIFFERENTIAL=1` is explicitly set in the environment.

---

## 4. Conclusion

The testing and snapshot infrastructure in Protean is mature, reliable, and well-designed for verifying the new refractive glass material and seaglass preset.

### Key Deliverables & Test Recipe for Implementation:

1. **TypeScript Unit Tests (`viewer/src/dispatch.test.ts`)**:
   - Verify `finish: 'glass'` and `finish: 'seaglass'` are accepted by `material`.
   - Verify `capabilities` reports `glass` and `seaglass` in `material_finishes`.
   - Verify `preset('seaglass')` dispatches correctly.

2. **Python Server Tests (`tests/test_server.py`)**:
   - Verify `material(finish="glass")`, `material(finish="seaglass")`, and `preset("seaglass")`.
   - Verify `capabilities()` returns `"glass"` and `"seaglass"` in `material_finishes` and `"seaglass"` in `presets`.

3. **Acceptance Test File (`tests/test_glass_differential.py`)**:
   - Model structure:
     ```python
     """Differential acceptance tests for glass and seaglass material shaders."""
     from pathlib import Path
     import pytest
     from PIL import Image as PILImage
     from .browser import BROWSER_MARKS, viewer_session
     from .pixels import Render, coverage, decode, difference
     from .test_render_differential import _as_server
     import protean_mcp.server as server_mod

     pytestmark = BROWSER_MARKS
     SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"

     def _save_snapshot(render: Render, filename: str) -> Path:
         SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
         out_path = SNAPSHOTS_DIR / filename
         PILImage.fromarray(render.pixels, mode="RGBA").save(out_path)
         return out_path

     async def _capture_shot(session) -> Render:
         result = await session.request("screenshot", {})
         return decode(result["data_uri"])

     @pytest.mark.asyncio
     async def test_glass_material_execution_and_snapshot():
         async with viewer_session("1ubq") as session:
             baseline = await _capture_shot(session)
             async with _as_server(session, load=True, pdb_id="1ubq"):
                 await server_mod.material(finish="glass")
             glass_render = await _capture_shot(session)
             assert coverage(glass_render) > 0.02
             assert difference(baseline, glass_render) > 0.005
             _save_snapshot(glass_render, "1ubq_glass_snapshot.png")

     @pytest.mark.asyncio
     async def test_seaglass_preset_execution_and_snapshot():
         async with viewer_session("1ubq") as session:
             baseline = await _capture_shot(session)
             async with _as_server(session, load=True, pdb_id="1ubq"):
                 preset_reply = await server_mod.preset("seaglass")
                 assert preset_reply.get("preset") == "seaglass"
             seaglass_render = await _capture_shot(session)
             assert coverage(seaglass_render) > 0.02
             assert difference(baseline, seaglass_render) > 0.05
             _save_snapshot(seaglass_render, "1ubq_seaglass_preset_snapshot.png")

     @pytest.mark.asyncio
     async def test_glass_capabilities():
         async with viewer_session("1ubq") as session:
             reply = await session.request("capabilities", {})
             assert "glass" in reply.get("material_finishes", [])
             assert "seaglass" in reply.get("material_finishes", [])
             assert "seaglass" in reply.get("presets", [])
     ```

---

## 5. Verification Method

To independently verify all claims in this report, run the following verification commands:

1. **Verify Python test runner and static analysis**:
   ```bash
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run mypy
   uv run pytest tests/test_pixels.py tests/test_server.py
   ```
2. **Verify Viewer build & Vitest suite**:
   ```bash
   cd viewer
   npm test
   npm run build
   ```
3. **Verify Existing Differential Browser Test Execution & Snapshot Generation**:
   ```bash
   PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_origami_differential.py -v
   ```
   Confirm snapshots are generated in `tests/snapshots/` with valid non-zero dimensions and byte sizes.
4. **Invalidation Conditions**:
   - If `tests/browser.py` fails to launch Chrome or connect via CDP on macOS.
   - If `npm test` fails in `viewer/`.
   - If `decode(data_uri)` in `tests/pixels.py` fails to produce an RGBA numpy array.
