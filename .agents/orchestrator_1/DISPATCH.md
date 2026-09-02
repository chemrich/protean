## 2026-08-28T10:07:08Z
You are the Project Orchestrator.
Your working directory is: /Users/charlie/code/protean/.agents/orchestrator_1/
The project workspace is: /Users/charlie/code/protean
The user's original request is recorded in: /Users/charlie/code/protean/.agents/ORIGINAL_REQUEST.md

Task Summary:
Implement a new refractive glass material shader in Mol* and expose it in Protean. This should include a standard clear glass material and a frosted "seaglass" variant that distorts the background and internal structures.

Requirements:
1. Mol* Glass Implementation: Implement a transmission/refraction material shader within the Mol* viewer codebase capable of true or screen-space refraction. It must support two distinct finish types:
   - `glass`: A perfectly clear, smooth, highly refractive transmission material.
   - `seaglass`: A frosted, tumbled glass variant with high surface roughness that diffuses the refraction.
2. Protean API Integration: Expose both materials via the Protean Python API (e.g., `material(finish="glass")` and `material(finish="seaglass")`). Additionally, implement a high-level `preset("seaglass")` that automatically applies the seaglass material and applies a baked-in seafoam green or sea-glass blue color tint to the structure.
3. Acceptance Criteria:
   - Programmatic test script successfully loading a structure, applying both `glass` and `preset("seaglass")` via Protean API, and capturing snapshots without WebGL or Python runtime errors.
   - Visual inspection confirming refraction / transmission and frosted/tinted aesthetic.
