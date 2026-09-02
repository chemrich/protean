# Progress — Milestone M4

Last visited: 2026-08-28T11:12:30Z

## Plan
1. [x] Step 1: Run fast test suites (`pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v` [283 passed] & `cd viewer && npm test` [211 passed])
2. [x] Step 2: Run headless browser differential test suite (`tests/test_glass_differential.py`) - Verified Tier 1 capabilities, direct dispatch for glass and seaglass, seaglass preset, Tier 2 parameter boundaries, parameter overrides, error handling, custom handles, and multi-representation rendering.
3. [x] Step 3: Refactor and optimize differential test suite (`tests/test_glass_differential.py`):
   - Added studio directional lighting rig activation before differential baseline captures (per `test_render_differential.py:288-296` specification for directional lighting / roughness contrast).
   - Fixed `show()` invocation signatures with `selection="polymer"` and `handle="nterm"`.
   - Replaced multi-session parameter boundary loop with consolidated single-session assertions.
   - Synchronized snapshot artifact generation paths to `tests/snapshots/1ubq_glass_snapshot.png` and `tests/snapshots/1ubq_seaglass_preset_snapshot.png`.
4. [ ] Step 4: Verify snapshot PNG files in `tests/snapshots/` (`1ubq_glass_snapshot.png` and `1ubq_seaglass_preset_snapshot.png`)
5. [ ] Step 5: Write handoff report `handoff.md` and message parent
