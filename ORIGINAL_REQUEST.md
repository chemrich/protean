# Original User Request

## 2026-08-28T10:06:47Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: The full multi-agent team

Implement a new refractive glass material shader in Mol* and expose it in Protean. This should include a standard clear glass material and a frosted "seaglass" variant that distorts the background and internal structures.

Working directory: ~/teamwork_projects/glass_shader
Integrity mode: development

## Requirements

### R1. Mol* Glass Implementation
Implement a transmission/refraction material shader within the Mol* viewer codebase capable of true or screen-space refraction. It must support two distinct finish types:
1. `glass`: A perfectly clear, smooth, highly refractive transmission material.
2. `seaglass`: A frosted, tumbled glass variant with high surface roughness that diffuses the refraction.

### R2. Protean API Integration
Expose both materials via the Protean Python API (e.g., `material(finish="glass")` and `material(finish="seaglass")`). Additionally, implement a high-level `preset("seaglass")` that automatically applies the seaglass material and applies a baked-in seafoam green or sea-glass blue color tint to the structure.

## Acceptance Criteria

### Compilation & Execution
- [ ] A programmatic test script successfully loads a structure, applies both the `glass` material and the `preset("seaglass")` via the Protean API, and captures snapshots of each without throwing WebGL or Python runtime errors.

### Visual Quality (Agent-as-Judge)
- [ ] An independent Agent-as-Judge visually inspects the generated snapshot of the `glass` material and confirms it demonstrates clear refraction/transmission of the background or internal structures.
- [ ] An independent Agent-as-Judge visually inspects the generated snapshot of the `seaglass` preset and exhibits a diffused/frosted glass aesthetic with a seafoam/blue color tint.

## 2026-08-28T15:43:09Z

# Teamwork Project Prompt — Draft

> Status: Launched (Restarted after quota reset)
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: The full multi-agent team

Implement a new refractive glass material shader in Mol* and expose it in Protean. This should include a standard clear glass material and a frosted "seaglass" variant that distorts the background and internal structures.

Working directory: ~/teamwork_projects/glass_shader
Integrity mode: development

## Requirements

### R1. Mol* Glass Implementation
Implement a transmission/refraction material shader within the Mol* viewer codebase capable of true or screen-space refraction. It must support two distinct finish types:
1. `glass`: A perfectly clear, smooth, highly refractive transmission material.
2. `seaglass`: A frosted, tumbled glass variant with high surface roughness that diffuses the refraction.

### R2. Protean API Integration
Expose both materials via the Protean Python API (e.g., `material(finish="glass")` and `material(finish="seaglass")`). Additionally, implement a high-level `preset("seaglass")` that automatically applies the seaglass material and applies a baked-in seafoam green or sea-glass blue color tint to the structure.

## Acceptance Criteria

### Compilation & Execution
- [ ] A programmatic test script successfully loads a structure, applies both the `glass` material and the `preset("seaglass")` via the Protean API, and captures snapshots of each without throwing WebGL or Python runtime errors.

### Visual Quality (Agent-as-Judge)
- [ ] An independent Agent-as-Judge visually inspects the generated snapshot of the `glass` material and confirms it demonstrates clear refraction/transmission of the background or internal structures.
- [ ] An independent Agent-as-Judge visually inspects the generated snapshot of the `seaglass` preset and confirms it exhibits a diffused/frosted glass aesthetic with a seafoam/blue color tint.

## 2026-08-28T23:00:41Z

# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: The full multi-agent team

Write a standalone Python script that uses the Protean API to generate extremely high-quality, publication-ready renders (Glass, Seaglass, and Origami variants) for a specific set of PDB structures (1FHA, 5JQ3, 1F88, and 1GFL). These renders must perfectly match the visual fidelity, lighting, and accumulation quality of the original shader tests.

Working directory: ~/code/scratch/mega_renders
Integrity mode: development

## Requirements

### R1. Script Implementation
Write a Python script that programmatically loads the four structures (1FHA, 5JQ3, 1F88, 1GFL) and applies all three distinct aesthetics (Glass, Seaglass, and Origami) to each. 

### R2. Visual Fidelity Parity
The script must perfectly replicate the high-fidelity rendering pipeline used by the original shader tests. The team must identify the correct rendering parameters (e.g., path-tracing, effects, viewport dimensions, or snapshot accumulation settings) needed to achieve the identical pristine look of the initial test outputs.

## Acceptance Criteria

### Compilation & Execution
- [ ] The Python script executes from start to finish without any Python or WebGL runtime errors and successfully writes all 12 snapshot PNGs to the target directory.

### Visual Quality (Agent-as-Judge)
- [ ] An independent Agent-as-Judge visually inspects the new Seaglass and Glass renders and confirms that their refraction, scattering, and lighting quality perfectly matches the standard set by the original `glass.png` and `seaglass.png` artifacts.

