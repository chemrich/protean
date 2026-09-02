## 2026-08-28T10:25:41Z

You are the Worker implementing Milestone M2: Protean Python API Exposure, Seaglass Preset, Tool Reference Sync, and Static Production Bundle Build.

Your working directory is: /Users/charlie/code/protean/.agents/worker_m2
You have write ownership over:
- `src/protean_mcp/server.py`
- `docs/tools.md`, `README.md` (via `docs/generate/tool_reference.py`)
- `src/protean_mcp/static/` (via `npm run build` in `viewer/`)
- `viewer/src/` if any minor adjustments or build fixes are needed

Read these files before starting:
- /Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md
- /Users/charlie/code/protean/PROJECT.md
- /Users/charlie/code/protean/.agents/explorer_survey_protean/handoff.md
- /Users/charlie/code/protean/.agents/worker_m1/handoff.md
- /Users/charlie/code/protean/.agents/reviewer_m1_2/handoff.md

Your tasks:
1. In `src/protean_mcp/server.py`:
   - Update `material()` docstring to describe `glass` (clear, smooth, refractive transmission) and `seaglass` (frosted, tumbled glass with high roughness and surface diffusion).
   - Implement `_preset_seaglass(target: str, handle: str) -> list[str]`:
     - Sets background (e.g. `color="#ffffff", gradient="off"` or transparent).
     - Sets lighting rig (`lighting, rig="three-point", ambient=0.45`).
     - Sets ambient occlusion (`effects, occlusion=True, shadow=False`).
     - Applies seafoam green tint (`color, color="#73b9a2", name=handle`).
     - Applies seaglass material finish (`material, finish="seaglass", name=handle`).
   - Register `"seaglass"` in `_VIEWS` (`selection="polymer", representation="cartoon", color="uniform", style=_preset_seaglass`).
   - Register `"seaglass": ("seaglass", _VIEW_DRAWS)` in `_PAGE_VIEWS`.
   - Update `preset()` docstring in `server.py` to document `"seaglass"`.
2. Sync tool documentation:
   - Run `uv run python docs/generate/tool_reference.py` to regenerate `docs/tools.md` and `README.md`.
   - Verify `uv run python docs/generate/tool_reference.py --check` passes.
3. Build the viewer static bundle:
   - In `viewer/`, run `npm run build` to compile `viewer/src/` cleanly into `src/protean_mcp/static/`. Verify `src/protean_mcp/static/assets/index-*.js` and `index.html` contain `glass`, `seaglass`, and `installRefraction`.
4. Run tests:
   - Run `uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v`.
   - Run `cd viewer && npm test`.
5. Write completion report to `/Users/charlie/code/protean/.agents/worker_m2/handoff.md`.
