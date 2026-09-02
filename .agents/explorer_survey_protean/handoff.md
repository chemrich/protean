# Investigation Report: Protean Python API & Mol* Integration for Glass/Seaglass Materials and Presets

## 1. Observation

### 1.1 Architecture & Bridge to Mol*
The Protean system consists of a Python FastMCP backend communicating bidirectionally with a browser-based Mol* frontend over a dedicated HTTP + WebSocket connection.

- **Python Server & Bridge Entrypoint:**
  - `src/protean_mcp/server.py:37`: Server script defined as `protean-mcp = "protean_mcp.server:main"`.
  - `src/protean_mcp/server.py:432-476`: `get_bridge()`, `use_bridge()`, `_require_viewer()`, and `_call(action, args)`.
  - `_call(action, args)` sends JSON-RPC over the bridge:
    ```python
    async def _call(action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        bridge = _require_viewer()
        result = await bridge.request(action, args or {})
        if not isinstance(result, dict):
            raise ViewerError(
                f"Viewer returned {type(result).__name__} for {action!r}, expected an object"
            )
        return result
    ```
- **WebSocket Transport (`src/protean_mcp/connection.py`):**
  - `ViewerBridge` class (`connection.py:43-538`) runs an `aiohttp` web application on port 9878 (`DEFAULT_PORT = 9878`, configurable via `PROTEAN_PORT` env var).
  - Serves static web assets from `src/protean_mcp/static/` (built from `viewer/` via Vite).
  - Listens on `/ws` with a per-session random token (`self.token = secrets.token_urlsafe(32)`).
  - Protocol message format:
    - Outgoing request: `{"id": str(uuid.uuid4()), "action": action, "args": args}`
    - Incoming response: `{"id": id, "ok": True, "result": {...}}` or `{"id": id, "ok": False, "error": str}`.
- **Frontend Bridge Client (`viewer/src/bridge.ts`):**
  - `connectBridge(handle: Handler)` (`bridge.ts:74-270`) connects to `ws://${location.host}/ws?token=...`
  - Sends `protean_ping` on connection.
  - On incoming message: invokes `handle(data.action, data.args)` and returns `{id: data.id, ok: true, result}` or `{id: data.id, ok: false, error: err.message}`.
- **Frontend Action Dispatcher (`viewer/src/dispatch.ts`):**
  - `createDispatcher(plugin: any): Handler` (`dispatch.ts:1101-1150`) manages the `ACTIONS` registry (`Record<string, ActionHandler>`).
  - Directly drives Mol* via `plugin` (`PluginContext`), updating state tree via `plugin.state.data.build()` and modifying `Canvas3D` render parameters.

---

### 1.2 Python API Definitions for `material`, `preset`, and `capabilities`

- **`material` tool (`src/protean_mcp/server.py:3730-3806`):**
  ```python
  @_tool()
  async def material(
      finish: str = "matte",
      name: str = "sele",
      metalness: float | None = None,
      roughness: float | None = None,
      emissive: float | None = None,
      bumpiness: float | None = None,
      bump_frequency: float | None = None,
  ) -> dict[str, Any]:
      """Give a displayed selection a surface finish.

      finish: one of — capabilities() reports the live list. They run from dull to
        sharp, so a shinier name really is shinier.

        matte      Fully diffuse. Mol*'s default, and the way back.
        origami    Folded paper finish: matte dielectric with paper grain texture.
        satin      A soft, broad sheen.
        glossy     A tight highlight — wet or lacquered.
        metallic   Brushed metal: the highlight takes the surface colour.
        chrome     Polished metal, close to a mirror.
      ...
      """
      args: dict[str, Any] = {"name": name, "finish": finish}
      for key, value in (
          ("metalness", metalness),
          ("roughness", roughness),
          ("emissive", emissive),
          ("bumpiness", bumpiness),
          ("bump_frequency", bump_frequency),
      ):
          if value is not None:
              args[key] = value
      return await _call("material", args)
  ```
- **`preset` tool (`src/protean_mcp/server.py:5365-5475`):**
  ```python
  @_tool()
  async def preset(name: str, handle: str | None = None) -> dict[str, Any]:
      if name not in _PRESETS:
          raise ViewerError(
              f"Unknown preset {name!r}. Available: {', '.join(sorted(_PRESETS))}"
          )
      _require_viewer()
      drawn, note = _polarity_view(name)
      steps = await _PRESETS[drawn](handle or _WHOLE_SCENE)
      return {
          "preset": drawn,
          "applied_to": handle or _WHOLE_SCENE,
          "steps": ([note, *steps] if note else steps),
          **({"asked_for": name} if drawn != name else {}),
      }
  ```
- **`capabilities` tool (`src/protean_mcp/server.py:4192-4208`):**
  ```python
  @_tool()
  async def capabilities() -> dict[str, Any]:
      reported = await _call("capabilities", {})
      reported["presets"] = sorted(_PRESETS)
      reported["ffmpeg"] = _ffmpeg_binary() is not None
      return reported
  ```

---

### 1.3 Material Properties, Finishes, Validation, and Serialization

- **Finish Registry (`viewer/src/dispatch.ts:478-493`):**
  ```typescript
  const MATERIAL_FINISHES: Record<
    string,
    {
      metalness: number;
      roughness: number;
      bumpiness?: number;
      bump_frequency?: number;
    }
  > = {
    matte: { metalness: 0, roughness: 1.0 },
    satin: { metalness: 0.15, roughness: 0.6 },
    glossy: { metalness: 0.3, roughness: 0.15 },
    metallic: { metalness: 1.0, roughness: 0.6 },
    chrome: { metalness: 1.0, roughness: 0.1 },
    origami: { metalness: 0, roughness: 1.0, bumpiness: 0.45, bump_frequency: 4.5 },
  };
  ```
- **Action Interface (`viewer/src/dispatch.ts:128-141`):**
  ```typescript
  interface MaterialArgs {
    name: string;
    /** A key of MATERIAL_FINISHES. */
    finish: string;
    /** Each 0-1, overriding the finish where given. */
    metalness?: number;
    roughness?: number;
    /** Self-illumination. Bloom's default mode only glows where this is > 0. */
    emissive?: number;
    /** 0-1. Needs bump_frequency above zero on the representation to show. */
    bumpiness?: number;
    /** 0-10. Mol*'s own param, per representation rather than per material. */
    bump_frequency?: number;
  }
  ```
- **Material Handler & Validation (`viewer/src/dispatch.ts:2014-2158`):**
  - Validates `const base = MATERIAL_FINISHES[finish];` -> throws `Unknown finish '${finish}'. Available: ${Object.keys(MATERIAL_FINISHES).sort().join(', ')}`.
  - Validates numeric bounds using `checkFraction(key, value)` (verifies finite number in `[0, 1]`).
  - Validates `bump_frequency` (finite number in `[0, 10]`).
  - Resolves target selection representations in Mol* state hierarchy.
  - Updates representation type params: `old.type.params.material = { ...material }` and `old.type.params.bumpFrequency = effBumpFrequency`.
  - Returns result dictionary including `{name, finish, representations, metalness, roughness, bumpiness, ...}`.
- **Capabilities Handler (`viewer/src/dispatch.ts:3184-3203`):**
  - `material_finishes: Object.keys(MATERIAL_FINISHES).sort()` dynamically extracts finishes from `MATERIAL_FINISHES`.

---

### 1.4 Presets Architecture, Color Schemes, and Tinting

- **Presets Registry (`src/protean_mcp/server.py:5351-5362`):**
  ```python
  _PRESETS: dict[str, Any] = {
      "publication-cartoon": _preset_publication_cartoon,
      "illustrative": _preset_illustrative,
      "ghost-heart": _preset_ghost_heart,
      "active-site": _preset_active_site,
      "light-ground": _preset_light_ground,
      "dark-ground": _preset_dark_ground,
      "default": _preset_default,
      "sidechains": _preset_sidechains,
      "hide-sidechains": _preset_hide_sidechains,
      **{name: functools.partial(_draw_view, name) for name in _VIEWS},
  }
  ```
- **Drawing Views (`src/protean_mcp/server.py:5123-5270`):**
  - Encapsulated via `_View` dataclass:
    ```python
    @dataclass(frozen=True)
    class _View:
        selection: str
        representation: str
        color: str
        style: Any
    ```
  - `_draw_view(name, target)` (`src/protean_mcp/server.py:5305-5349`):
    1. Takes over scene handle `auto_view` (`_SCENE_HANDLE`).
    2. Hides default `auto` representation and draws `view.representation` with `view.color`.
    3. Hides extra handles from other presets (like `auto_felt_halo`, `auto_scaffold_tarp`).
    4. Calls `await view.style(target, handle)`.
    5. Frames the scene (`_frame_the_scene`).
- **Existing Styling & Tinting Patterns in Protean:**
  - **Pattern A (Material + Creases + Custom Ground): `origami` (`server.py:4868-4876`):**
    ```python
    async def _origami_style(_target: str, handle: str) -> list[str]:
        return [
            await _run(background, color="#f6f4eb", gradient="off"),
            await _run(lighting, rig="three-point", ambient=0.45),
            *await _set_effects(occlusion=True, shadow=False),
            await _run(shading, style="origami", name=handle),
            await _run(material, finish="origami", name=handle),
        ]
    ```
    Registered as:
    ```python
    "origami": _View(
        selection="polymer",
        representation="cartoon",
        color="secondary-structure",
        style=_origami_style,
    )
    ```
  - **Pattern B (Uniform Color Tinting): `richardson` (`server.py:4879-4901`, `5259-5270`):**
    Sets `color="#d8d3c8"` directly on the `_View` to tint the whole fold.
  - **Pattern C (Custom Element Theme / Palette): `felt` (`server.py:4908-5007`, `5250-5258`):**
    Registers custom element color dictionary via `define_elements(name=..., colors=...)` and applies via `color(color=..., name=handle)`.
- **Web UI Preset Menu (`src/protean_mcp/server.py:5606-5645`):**
  - `_PAGE_VIEWS` lists presets available in the browser tab menu.
  - Drawing presets are categorized under `_VIEW_DRAWS`, styling under `_VIEW_STYLES`, and layers under `_VIEW_LAYERS`.

---

### 1.5 Schema Validation, Doc Generators, and Test Harnesses

- **FastMCP Schema Generation (`src/protean_mcp/server.py:227-263`):**
  - Tool schemas are generated dynamically from Python function signatures and type annotations by FastMCP (`mcp.tool`).
  - No separate Pydantic model classes are declared for flat tool inputs.
- **Documentation Generator (`docs/generate/tool_reference.py`):**
  - Uses `ast` to parse `src/protean_mcp/server.py` and regenerates `docs/tools.md` and `README.md`.
  - Enforces that all tools are categorized in `AREAS` (`tool_reference.py:37-105`), where `material` and `preset` are under `"Style"`.
  - `tests/test_docs_generated.py:38-49` verifies `--check` passes against current source code.
- **Test Suites:**
  - `tests/test_server.py:1430-1467`: tests `material(finish=...)`.
  - `tests/test_server.py:2450-2492`: tests `preset(...)` calls and `capabilities()`.
  - `tests/test_page_invoke.py:151-195`: tests `_PAGE_VIEWS` consistency with `_PRESETS`.
  - `viewer/src/dispatch.test.ts:1559-1640`: Vitest suite for `material` action and `MATERIAL_FINISHES`.
  - `tests/test_origami_differential.py`: Headless browser snapshot differential test validating render execution, coverage > 2%, delta > 5%, and snapshot image artifact generation.

---

## 2. Logic Chain

1. **Material Dispatch & Validation:**
   - From Observation 1.2 and 1.3, Python's `material()` accepts `finish: str` without hardcoding valid finish names. It passes `{"name": name, "finish": finish, ...}` to `_call("material", args)`.
   - In `viewer/src/dispatch.ts:2025-2031`, the viewer validates `finish` strictly against `MATERIAL_FINISHES`.
   - Therefore, adding support for `finish="glass"` and `finish="seaglass"` requires:
     - Adding entries for `"glass"` and `"seaglass"` to `MATERIAL_FINISHES` in `viewer/src/dispatch.ts:478-493` with their respective PBR/roughness/bumpiness parameters.
     - Updating the docstring of `material()` in `src/protean_mcp/server.py:3742-3751` so callers and FastMCP schema generators know about them.
     - Because `capabilities` in `viewer/src/dispatch.ts:3195` calls `Object.keys(MATERIAL_FINISHES).sort()`, adding them to `MATERIAL_FINISHES` automatically exposes them via `capabilities()`.

2. **Preset System & `seaglass` Preset:**
   - From Observation 1.4, drawing presets are implemented via `_View` in `_VIEWS` (`server.py:5150-5270`), which are merged into `_PRESETS` (`server.py:5361`).
   - A `seaglass` preset requires:
     - A style recipe `_seaglass_style(target, handle)` that sets lighting (e.g. `rig="three-point"` or `rig="studio"`), background (e.g. transparent or neutral ground), effects (occlusion/shadow), and calls `material(finish="seaglass", name=handle)`.
     - Baked-in color tinting: using seafoam green / sea-glass blue (e.g. hex color like `#73c2a6` or `#7fcdbb` or a dedicated palette/theme) in `_View(selection="polymer", representation="cartoon", color=..., style=_seaglass_style)`.
     - Registering `"seaglass"` in `_VIEWS`.
     - Registering `"seaglass": ("seaglass", _VIEW_DRAWS)` in `_PAGE_VIEWS` (`server.py:5606-5634`) so the frontend tab menu offers it and passes `test_page_invoke.py`.
     - Updating `preset()` docstring in `src/protean_mcp/server.py:5390-5434`.

3. **Documentation & Quality Gates:**
   - From Observation 1.5, `tests/test_docs_generated.py` runs `docs/generate/tool_reference.py --check`.
   - Updating `material` and `preset` docstrings in `src/protean_mcp/server.py` and running `uv run python docs/generate/tool_reference.py` ensures `docs/tools.md` and `README.md` remain strictly in sync.

4. **Testing & Differential Snapshots:**
   - Unit tests in `tests/test_server.py` and `viewer/src/dispatch.test.ts` verify argument serialization, capability listings, and error handling.
   - Differential pixel tests modeled after `tests/test_origami_differential.py` can load structures (e.g., `1crn`, `1ubq`), apply `material(finish="glass")` and `preset("seaglass")`, verify no WebGL/Python errors, and capture snapshot PNGs to `tests/snapshots/`.

---

## 3. Caveats

1. **Mol* Internal Shader Hook:**
   - This survey focused on the Protean Python API, WebSocket bridge, and TypeScript dispatcher integration layers. The internal WebGL shader implementation inside Mol* (transmission pass, screen-space refraction, IOR, and roughness blur) will interface with `type.params.material` or custom render passes (similar to `viewer/src/painterly.ts`).
2. **Representation Support:**
   - As noted in `src/protean_mcp/server.py:3759-3782` and `viewer/src/dispatch.ts:2044-2051`, bumpiness requires a representation that declares `bumpFrequency` (e.g. `cartoon`, `spacefill`, `molecular-surface`, `gaussian-surface`, `putty`). For `glass` / `seaglass`, representations that support transmission/refraction must be targeted (e.g., cartoon / molecular surface).

---

## 4. Conclusion

The integration path in Protean for `glass` and `seaglass` materials and the `preset("seaglass")` is clean, modular, and well-isolated:

1. **Material Finishes (`glass`, `seaglass`):**
   - Define in `viewer/src/dispatch.ts:478-493` (`MATERIAL_FINISHES` Record).
   - Document in `src/protean_mcp/server.py:3742-3751` (`material()` docstring).
   - Automatically surfaced via `capabilities()` in both Python and TypeScript.
2. **`seaglass` Preset:**
   - Implement `_seaglass_style` recipe function in `src/protean_mcp/server.py`.
   - Add `"seaglass"` entry in `_VIEWS` (`server.py:5150-5270`) with seafoam green / sea-glass blue color tint.
   - Add `"seaglass": ("seaglass", _VIEW_DRAWS)` to `_PAGE_VIEWS` (`server.py:5606-5634`).
   - Document in `src/protean_mcp/server.py:5390-5434` (`preset()` docstring).
3. **Doc Regeneration & Tests:**
   - Run `python docs/generate/tool_reference.py` to regenerate `docs/tools.md` and `README.md`.
   - Add unit tests to `tests/test_server.py` and `viewer/src/dispatch.test.ts`.
   - Create differential render tests in `tests/test_glass_differential.py` to capture and verify snapshots.

---

## 5. Verification Method

To independently verify these findings:

1. **Inspect Code Locations:**
   - Python `material()`: `src/protean_mcp/server.py:3730-3806`
   - Python `preset()`: `src/protean_mcp/server.py:5365-5475`
   - Python `_VIEWS` & `_PRESETS`: `src/protean_mcp/server.py:5150-5362`
   - Python `_PAGE_VIEWS`: `src/protean_mcp/server.py:5606-5634`
   - TypeScript `MATERIAL_FINISHES`: `viewer/src/dispatch.ts:478-493`
   - TypeScript `ACTIONS.material`: `viewer/src/dispatch.ts:2014-2158`
   - TypeScript `ACTIONS.capabilities`: `viewer/src/dispatch.ts:3184-3203`
   - Reference Differential Test: `tests/test_origami_differential.py`

2. **Execute Test Commands:**
   - Run Python tests: `uv run pytest tests/test_server.py tests/test_page_invoke.py -v`
   - Run Doc Check: `uv run python docs/generate/tool_reference.py --check`
   - Run TypeScript Vitest: `cd viewer && npm test`
