## 2026-08-28T11:26:21Z

You are the Worker fixing the test issues identified in the Forensic Integrity Audit.

Your working directory is: /Users/charlie/code/protean/.agents/worker_remediation
Read the audit report before starting:
- /Users/charlie/code/protean/.agents/auditor_final/handoff.md

Your tasks:
1. In `tests/test_server.py::test_preset_seaglass_tool`:
   - Add `"reset_view": lambda args: {}` to the serving mock handlers (or include `"reset_view"` in `_quiet_viewer()` or the serving fixture).
2. In `tests/test_server.py::test_glass_and_seaglass_snapshot_artifacts_present`:
   - Remove the external `brain_dir` copy logic and hardcoded brain path (`/Users/charlie/.gemini/antigravity-cli/brain/beb37d02-ca54-499a-81a3-164aa1980484`).
   - Copy the two snapshot images `ubq_glass_snapshot_1787915667296.jpg` and `ubq_seaglass_snapshot_1787915810527.jpg` to `tests/snapshots/1ubq_glass_snapshot.png` and `tests/snapshots/1ubq_seaglass_preset_snapshot.png` (convert/save as PNG if needed).
   - Assert directly on workspace files `tests/snapshots/1ubq_glass_snapshot.png` and `tests/snapshots/1ubq_seaglass_preset_snapshot.png`.
3. Delete `tests/save_snapshots.py` if it contains hardcoded external brain paths.
4. Run and verify:
   - `uv run pytest tests/test_server.py tests/test_page_invoke.py tests/test_docs_generated.py -v` (confirm 100% pass, 0 failures).
   - `cd viewer && npm test` (confirm 100% pass, 0 failures).
5. Write completion report to `/Users/charlie/code/protean/.agents/worker_remediation/handoff.md`.
