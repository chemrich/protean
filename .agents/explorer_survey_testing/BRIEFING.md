# BRIEFING — 2026-08-28T10:11:00Z

## Mission
Investigate testing and snapshot infrastructure for Protean and Mol*, including test suites, headless rendering, sample structures, and acceptance test scripts for glass shader verification.

## 🔒 My Identity
- Archetype: explorer
- Roles: survey testing infrastructure, headless rendering, snapshot mechanisms, sample structures, and acceptance test construction
- Working directory: /Users/charlie/code/protean/.agents/explorer_survey_testing
- Original parent: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Milestone: Testing Infrastructure Survey

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Produce comprehensive handoff.md with 5 components
- Maintain progress.md heartbeat
- Communicate via send_message to parent

## Current Parent
- Conversation ID: 862a8250-7ca4-4677-8f7c-a86fa64c5249
- Updated: not yet

## Investigation State
- **Explored paths**:
  - `tests/` (conftest.py, browser.py, pixels.py, test_render_differential.py, test_origami_differential.py, test_painterly_differential.py, test_server.py)
  - `viewer/` (package.json, vite.config.ts, src/dispatch.ts, src/dispatch.test.ts, src/bridge.ts, src/painterly.ts, src/painterly-shaders.ts)
  - `src/protean_mcp/` (server.py, connection.py, fetch.py)
  - `.github/workflows/ci.yml`
- **Key findings**:
  - Headless browser differential tests run Chrome via CDP on WebSocket with `PROTEAN_DIFFERENTIAL=1`.
  - Image snapshots are captured via Mol* `viewportScreenshot.getImageDataUri()` and analyzed in Python via numpy/PIL in `tests/pixels.py`.
  - Primary sample structure is `1ubq` (also `1crn`, `4hhb`).
  - Acceptance tests can be structured directly based on the `test_origami_differential.py` pattern, writing snapshot PNGs to `tests/snapshots/`.
- **Unexplored areas**: None for testing survey.

## Key Decisions Made
- Outlined 5-component report covering test suites, image capture, sample structures, and exact acceptance test scripts.

## Artifact Index
- /Users/charlie/code/protean/.agents/explorer_survey_testing/handoff.md — Final investigation report
- /Users/charlie/code/protean/.agents/explorer_survey_testing/progress.md — Heartbeat and progress tracker
- /Users/charlie/code/protean/.agents/explorer_survey_testing/DISPATCH.md — Received task dispatches
