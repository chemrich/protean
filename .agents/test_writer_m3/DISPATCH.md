## 2026-08-28T10:12:01Z
You are an E2E Test Writer creating the comprehensive test suite for the Refractive Glass and Seaglass shaders in Mol* and Protean.

Your working directory is: /Users/charlie/code/protean/.agents/test_writer_m3
Read the following files before starting:
- /Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md
- /Users/charlie/code/protean/PROJECT.md
- /Users/charlie/code/protean/TEST_INFRA.md

Your task:
1. Create `tests/test_glass_differential.py` implementing comprehensive opaque-box requirement-driven tests across Tiers 1-4:
   - Tier 1: Feature coverage for `material(finish="glass")`, `material(finish="seaglass")`, `preset("seaglass")`, and `capabilities()`.
   - Tier 2: Boundary and corner cases (invalid finishes, parameter overrides, error handling).
   - Tier 3: Cross-feature combinations (finishes x representations x lighting x color modes).
   - Tier 4: Real-world application scenarios on structures 1ubq and 1crn, asserting coverage > 0.02 and differential delta > 0.005, and saving PNG snapshots to `tests/snapshots/1ubq_glass_snapshot.png` and `tests/snapshots/1ubq_seaglass_preset_snapshot.png`.
2. Add unit tests for `glass`, `seaglass`, and `preset("seaglass")` in `tests/test_server.py` and `viewer/src/dispatch.test.ts`.
3. When the test suite is created, publish `/Users/charlie/code/protean/TEST_READY.md` at project root with the runner command and coverage breakdown.
4. Write your completion report to `/Users/charlie/code/protean/.agents/test_writer_m3/handoff.md`.

Maintain progress.md in your working directory.
When done, message your parent with a summary and path to handoff.md.
