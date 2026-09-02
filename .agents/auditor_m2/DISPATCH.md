# Audit Assignment Dispatch

## 2026-08-28T18:51:35-07:00

You are the Forensic Integrity Auditor for the Protean Mega Renders project.
Your Working Directory: /Users/charlie/code/protean/.agents/auditor_m2/
Workspace Root: /Users/charlie/code/protean
Authoritative Request: /Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md
Scope Document: /Users/charlie/code/protean/PROJECT.md

Tasks:
1. Perform exhaustive forensic integrity analysis on `scripts/generate_mega_renders.py`, `tests/test_mega_renders.py`, and the generated render files in `/Users/charlie/code/scratch/mega_renders/`.
2. Verify that there is NO cheating, NO hardcoding of static/pre-baked images, NO mock or facade implementations, and that genuine WebGL rendering pipeline calls are made via `ViewerBridge` to Mol*.
3. Execute validation:
   ```bash
   uv run pytest tests/test_mega_renders.py -v
   ```
4. Agent-as-Judge Visual Inspection:
   Inspect the generated renders in `/Users/charlie/code/scratch/mega_renders/` for all 4 structures (1FHA, 5JQ3, 1F88, 1GFL) across all 3 aesthetics (Glass, Seaglass, Origami) and confirm that their refraction, scattering, and lighting quality match the standard set by reference shader tests.
5. Write your comprehensive forensic audit report to:
   `/Users/charlie/code/protean/.agents/auditor_m2/handoff.md`
   with an explicit verdict: `CLEAN` or `INTEGRITY VIOLATION`. Send a completion message back to the orchestrator.
