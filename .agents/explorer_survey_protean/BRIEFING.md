# BRIEFING — 2026-08-28T10:10:45Z

## Mission
Investigate Protean Python codebase, API, Mol* bridge, material finish types, presets, schema/validation, and serialization for sea-glass material support.

## 🔒 My Identity
- Archetype: Explorer
- Roles: Investigation, Synthesis
- Working directory: /Users/charlie/code/protean/.agents/explorer_survey_protean
- Original parent: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Milestone: Survey Protean Python API and Mol* Bridge

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Deliver structured 5-component handoff report to /Users/charlie/code/protean/.agents/explorer_survey_protean/handoff.md
- Message parent agent with summary and path

## Current Parent
- Conversation ID: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Updated: 2026-08-28T10:10:45Z

## Investigation State
- **Explored paths**:
  - `src/protean_mcp/server.py` (FastMCP tools: `material`, `preset`, `capabilities`, `show`, `color`, `shading`, `_PRESETS`, `_VIEWS`, `_PAGE_VIEWS`, etc.)
  - `src/protean_mcp/connection.py` (`ViewerBridge` WebSocket / HTTP server)
  - `viewer/src/bridge.ts` (browser WebSocket client)
  - `viewer/src/dispatch.ts` (`ACTIONS`, `MATERIAL_FINISHES`, `SHADING_STYLES`, `MaterialArgs`, `createDispatcher`)
  - `viewer/src/main.ts` (Mol* mounting, UI panel / view menu tabs)
  - `docs/generate/tool_reference.py` (AST-based tool reference doc generator)
  - `tests/test_server.py`, `tests/test_origami_differential.py`, `tests/test_page_invoke.py`, `viewer/src/dispatch.test.ts`
- **Key findings**:
  - Material finishes are defined in `viewer/src/dispatch.ts:478-493` (`MATERIAL_FINISHES`) and exposed dynamically through `capabilities`.
  - Python `material()` in `src/protean_mcp/server.py:3730-3806` does not hardcode finish checks; it forwards `finish` directly via JSON-RPC. Docstrings document the choices.
  - Presets are Python-level recipes defined in `_PRESETS` and `_VIEWS` (`src/protean_mcp/server.py:5150-5362`). Web UI menu is in `_PAGE_VIEWS` (lines 5606-5634).
  - FastMCP introspects type annotations and docstrings; no custom Pydantic models need explicit changes for new finish types or presets, but docstrings and documentation generation (`tool_reference.py`) need updating.
- **Unexplored areas**: None for survey scope.

## Key Decisions Made
- Mapped full end-to-end integration and pinpointed every exact file, line, interface, and test requirement for adding `glass` / `seaglass` finishes and `preset("seaglass")`.

## Artifact Index
- /Users/charlie/code/protean/.agents/explorer_survey_protean/DISPATCH.md — Dispatch log
- /Users/charlie/code/protean/.agents/explorer_survey_protean/progress.md — Progress heartbeat
- /Users/charlie/code/protean/.agents/explorer_survey_protean/handoff.md — Final handoff report
