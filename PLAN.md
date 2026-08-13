# Protean — Implementation Plan

**Goal:** replace and exceed PyMOL as an agent-native molecular visualization and analysis platform.

**Foundation decisions** (settled 2026-08-07):

- Rendering: **Mol\*** (molstar) — mature WebGL/WebGPU engine, powers RCSB, handles cartoons/surfaces/volumes/trajectories out of the box.
- Interface model: **agent-native first** — the MCP server + Python API is the product; the viewer window is a display surface Claude drives and the human watches/tweaks.
- Scope: core viz + selections, analysis layers, publication rendering, MD trajectories, animations.

## Architecture

Two components, same pattern proven in MCPymol and proteinblend-mcp:

```
Claude (MCP client)
   │  stdio
   ▼
protean MCP server (Python, FastMCP, uv/uvx)
   │  WebSocket, JSON {action, args, kwargs}
   ▼
protean viewer (TypeScript, Mol* embedded in a local web app)
```

**Viewer** — a Vite + TypeScript app embedding the Mol* plugin. Runs as a local page the server launches (browser tab first; Tauri shell later if a native app is wanted). It exposes a command dispatcher over WebSocket: each action maps to Mol* plugin-state transactions. Includes a `protean_ping`/`protean_pong` handshake so the server can identify it during port scanning (same coexistence trick as proteinblend-mcp).

**Server** — Python ≥3.13, FastMCP, launchable via `uvx protean-mcp`. Owns everything that isn't rendering: structure fetching/caching, selection-language translation, analysis pipelines (MMseqs2 conservation, contacts, electrostatics), MD trajectory handling (MDAnalysis), and movie encoding (ffmpeg). Heavy data flows to the viewer as compact frames; the viewer never does science.

**Port strategy** — default **9878** (MCPymol: 9876, proteinblend: 9877), `PROTEAN_PORT` env override, auto-increment scan up to 10 ports with handshake verification.

**Why this beats PyMOL:** every capability is a typed MCP tool with structured returns (not screen-scraped stdout); state is a serializable snapshot graph (Mol* state) so sessions are diffable and replayable; rendering quality and trajectory support exceed PyMOL's defaults; and the selection layer accepts PyMOL syntax, so migration is free.

## Repo layout

```
protean/
├── pyproject.toml            # uv workspace root, protean-mcp package
├── src/protean_mcp/
│   ├── server.py             # FastMCP tool definitions
│   ├── connection.py         # WS client, port scan, handshake
│   ├── selections.py         # PyMOL-syntax → MolQL translator
│   ├── analysis/             # conservation, contacts, electrostatics, superposition
│   ├── trajectory.py         # MDAnalysis loading + frame streaming
│   ├── movie.py              # keyframes, interpolation, ffmpeg encode
│   └── presets/              # YAML scene recipes (loader ported from proteinblend)
├── viewer/                   # Vite + TS + molstar
│   ├── src/main.ts           # plugin boot, WS server bridge
│   ├── src/dispatch.ts       # action → Mol* state transaction
│   └── src/render.ts         # snapshot/raytrace/export paths
└── tests/                    # pytest (server) + vitest (dispatch), mock WS peer
```

## Phases

### Phase 1 — Skeleton and bridge (v0.0.x)

Scaffold the uv workspace and viewer app. WebSocket bridge with handshake, port scan, reconnect. First three tools: `open_viewer`, `fetch_structure` (PDB + AlphaFold DB + local file), `screenshot`. Mirror MCPymol's test approach with a mock viewer peer. *Exit: Claude fetches 1UBQ and returns a PNG.*

### Phase 2 — Core viz + selections (v0.1)

Representations (cartoon, surface, ball-and-stick, sticks, spacefill, ribbon), per-selection apply/remove. Selections are **handles**: `select`/`combine`/`near`/`invert` produce named atom sets that display tools and analysis consume (see decisions 6-7). Color schemes: chain, secondary structure, element, spectrum, B-factor, pLDDT. Camera control (orient, zoom-to-selection, turntable). Measurements: distances, angles, dihedrals, labels. **Selection translator:** accept PyMOL algebra (`chain A and resi 50-60 and not solvent`, `byres`, `within`) and compile to **MolScript source** (see decision 5) — the single most important migration feature. Sessions: save/load Mol* state snapshots as `.protean` files (gzipped JSON; the snapshot embeds the structure data, so a session reopens without refetching, and the named selection handles round-trip with it). *Exit: reproduce a typical published PyMOL figure from a prompt.* **Met** (2026-08-08) — the catalytic zinc site of carbonic anhydrase II (1CA2) built entirely through the tool surface: `byres (polymer within 2.6 of metals)` returns exactly HIS 94/96/119, labelled, with Zn–N coordination distances of 1.91–2.10 A. Two publication features are still missing and belong to Phase 4: surface opacity and canvas background colour.

### Phase 3 — Analysis layers (v0.2)

Superposition/alignment (align, cealign-equivalent via biotite) with RMSD reporting. **Done** — `superpose()` aligns by sequence so numbering need not match, and returns RMSD, aligned-residue count, sequence identity, the 4x4 transform and the worst-fitting residues. Applying that transform in the viewer needs multi-structure support, which the dispatcher does not yet have (it assumes `structures[0]`). Contacts and interfaces (H-bonds, salt bridges, buried surface area). **Done for chain-vs-chain** — `interface()` reports buried area per side, interface residues with how much each buries, and classified contacts. Arbitrary PyMOL selections for analysis would need a second parser backend evaluating the existing AST over a biotite `AtomArray`; the grammar is already there, only the emitter differs. Conservation: port the MMseqs2 + Shannon entropy pipeline from MCPymol `conservation_view`, including the sequence-keyed cache and `force_refresh`. Electrostatics via pdb2pqr + APBS with surface potential mapping. Structured returns for everything (JSON tables, not prose) so Claude can reason over results. *Exit: "color this dimer's interface by conservation and list the conserved contacts" works in one exchange.*

### Phase 4 — Publication rendering (v0.3)

High-resolution snapshot pipeline, a render-style surface, and presets. Scoped in decisions 10-13 after auditing what Mol\* 4.18 actually ships, which is considerably more than this line originally assumed: a progressive path tracer, an array of lights, PBR materials, skybox and gradient backgrounds, bloom, depth of field, cel shading, and built-in camera motion. Almost none of the phase is new rendering machinery; it is surfacing knobs Mol\* already has in a form a model can see, plus the two things Mol\* genuinely lacks — DPI and TIFF. Seven changes, in order: pixel-assertion harness; background and opacity; lighting rigs; effects and backgrounds; PBR materials; path-traced rendering; `snapshot()`; then presets. Turntable and rocking capture land here (decision 13): `spin()` for a live turning view and `turntable()` for a reproducible frame sequence, the camera stepped a fixed angle and captured rather than a live animation sampled. The keyframed timeline and encoding stay in Phase 5. The Blender bridge is dropped — proteinblend-mcp does it better and nothing here needs it. Skybox and image backgrounds were deferred out of the effects work for needing image transport, and landed afterwards: both take URLs, so a local file travels inline as a data URI and nothing has to stay reachable when the figure is captured. *Exit: journal-ready TIFF/PNG at arbitrary DPI from one tool call.* **Met** (2026-08-10) — `snapshot(column="double", dpi=600, format="tiff")` writes a 4323x3242 file carrying 600 dpi, checked by reopening it rather than by trusting the reply. Presets landed too: `preset(name, handle)` with publication-cartoon, illustrative, ghost-surface and active-site, composed from the display tools rather than read from YAML (decision 10), each reporting the calls it made so none of it is reachable only through a preset.

### Phase 5 — Trajectories + animation (v0.4)

Trajectory loading (XTC/TRR/DCD/NetCDF — biotite rather than MDAnalysis, see decision 14), frame streaming to the viewer, playback controls, per-frame measurements (RMSD/RMSF/distance timeseries as structured data). Animation timeline: keyframed camera with smooth interpolation, and ffmpeg encoding to MP4/GIF/WebM. Keyframed *representation* states are not built — a timeline interpolates the camera, and changing what is drawn part-way through is a cut rather than a tween. Phase 4 lands turntable and rocking capture ahead of this (decision 13), so the frame-capture loop already exists and this phase adds keyframes, trajectories and encoding rather than starting from nothing. *Exit: load a trajectory, plot RMSF, render a 10-second annotated movie.*

### Phase 6 — Polish and publish (v1.0)

README (installation for Claude Code / Desktop / uvx, tool tables, example prompts — same structure as MCPymol/proteinblend). CHANGELOG, tagged releases, PyPI publish. Attribution: Mol*, biotite, APBS/pdb2pqr, FastMCP, Pillow, ColabFold, ffmpeg (not MDAnalysis — see decision 14). Benchmark doc: side-by-side PyMOL vs protean on 5 common tasks.

## Reuse from existing projects

| From | What |
|------|------|
| MCPymol | conservation pipeline + cache, test patterns, README/release conventions, tool naming |
| proteinblend-mcp | port-scan + handshake code, addon dispatch pattern (adapted to TS). Its YAML preset loader is *not* reused — see decision 10 |

## Decisions (2026-08-07)

1. **Viewer shell:** browser tab for now; Tauri revisit post-v1.0 if wanted.
2. **PyMOL selection parity:** ~25 most-used keywords first; full grammar is the ultimate goal. Design the translator so the grammar can grow without rework (proper parser, not regex).
3. **Electrostatics:** optional extra — `protean-mcp[apbs]`.
4. **MCPymol relationship:** coexist for now; may subsume MCPymol long-term but not near-term. Keep tool names and conventions compatible so a later merge is low-friction.

## Decisions (2026-08-08b) — selections for a model, not a CLI

6. **Selections compose through handles and set operations, not through the DSL.**

   PyMOL's grammar is optimised for typing into a REPL. Terseness and muscle
   memory are worth nothing to a model, while the costs transfer in full: the
   precedence trap we hit with `byres (X) and Y`, free text that the tool
   schema cannot validate, and no way to name a set. One virtue does transfer —
   the syntax is deep in a model's prior, so `chain A and resi 50-60` comes out
   right with no instruction, cheaply in tokens.

   So the DSL shrinks to *leaf predicates* and composition moves into the tool
   layer: analysis returns named handles, and `combine` / `near` / `invert`
   operate on them. The decisive observation is that analysis rarely wants a
   predicate — it wants a set that already exists (the interface just computed,
   the residues that superposed badly, the conserved positions). Re-encoding
   those as `resi 31+114+117+...` is lossy and absurd when the system holds the
   set. `byres (X) and Y` simply stops being expressible.

7. **Selections are evaluated in Python; the viewer receives atom indices.**

   One engine, so a selection cannot mean two things depending on whether it
   was drawn or analysed. It also makes selection and analysis work headlessly,
   with no browser at all. Index-based loci are already proven to work in the
   viewer (see the measurement work in decision 5's wake).

   Migration is guarded by the differential harness, which now runs both
   engines over the same corpus and asserts agreement: 34 of 35 selections
   match on first implementation. The one divergence is deliberate and better —
   Mol*'s `bychain` widens over its chain key (label_asym_id) while `chain A`
   matches auth_asym_id, so the MolScript backend disagrees with itself about
   what a chain is; the Python engine widens over the same id it selects on.

## Decisions (2026-08-08)

5. **Selection pipeline: Python parses PyMOL syntax → emits MolScript source → Mol\* evaluates.**

   Mol\* ships a PyMOL/VMD/Jmol selection transpiler in the prebuilt bundle, so "just use theirs" looked tempting. Measured against 51 PyMOL idioms on 4HHB, all 51 *parsed* — but ~8 returned silently wrong answers, which for an agent is worse than an error:

   | Idiom | Mol\* transpiler | Correct |
   |---|---|---|
   | `chain A and not hydro` | 0 | 1168 (`not` breaks on an empty operand, poisoning the whole expression) |
   | `within 5 of resn HEM` | 0 | 535 (bare `within` needs an explicit left operand) |
   | `metals` | 0 | 4 (keyword is a `@desc`-only stub, no implementation) |
   | `first` / `last` / `rank` / `bychain` / `bymolecule` | 0 | non-empty |
   | `bound_to resn HEM` | the HEM atoms themselves | atoms bonded to HEM |
   | `byres (X) and Y` | 502 | 295 — it swallows the `and` across the parenthesis boundary, computing `byres (X and Y)`; writing that form explicitly returns 0 |

   Emitting MolScript instead was validated against the same ground truth and is exact, including every case above (`and` 141, `not` 3611, `metals` 4, `first` 1, `byres` 4384). We own the grammar and its correctness; Mol\* owns execution; we never write a query engine. This satisfies decision 2's "proper parser" and keeps science out of the viewer.

   Mechanics worth recording: reach it via `plugin.builders.structure.tryCreateComponent(ref, {type: {name: 'script', params: {language: 'mol-script', expression}}})` — the prebuilt global exposes only ~12 names, so the transform registry is not directly reachable. MolScript modifiers take **positional arg 0 plus named args** (`(sel.atom.intersect-by A :by B)`, not two positionals). Verified vocabulary: `sel.atom.{all,empty,atom-groups,merge,intersect-by,except-by,expand-property,include-surroundings,include-connected,within,first}`, properties `atom.{el,is-het,atomic-number,B_iso_or_equiv,occupancy,auth_seq_id,auth_asym_id,label_atom_id,label_comp_id,entity-type}`, keys `atom.key.{res,chain,molecule,entity}`, and `(set.has (set ...) prop)`.

   **mol-script string literals are delimited with backticks.** Double and
   single quotes parse without error and then match nothing, so `` (= atom.entity-subtype "polypeptide(L)") ``
   silently selects zero atoms while `` `polypeptide(L)` `` selects 2039.
   Apostrophes are safe bare, so nucleic atom names (`C1'`) must *not* be
   quoted. Both failure modes are invisible to unit tests — only the
   differential suite catches them.

   **Entity typing beyond globular proteins.** Glycans are `branched` entities,
   not `non-polymer`, so `organic` must span both or glycoproteins lose their
   sugars entirely. Verified entity subtypes include `polypeptide(L)`,
   `polydeoxyribonucleotide`, `oligosaccharide`, and `ion` — the last two give
   us `glycan` and `ion` selectors that PyMOL has no equivalent for. Fixtures:
   1BNA (DNA), 5FJI (glycoprotein), 1CA2 (ion), 4HHB (the main corpus).

   Keep Mol\*'s transpiler wired up as a **differential test oracle**: agreement is a regression signal, and the 8 divergences above are our first "we beat PyMOL" test cases. Open gap: `bymolecule` (`atom.key.molecule`) returned 0 and needs investigation.

## Decisions (2026-08-09) — electrostatics without the binary

8. **Screened Coulomb is the default; APBS is an optional backend.** Amends
   decision 3, which scoped electrostatics as `protean-mcp[apbs]` and left the
   solver unquestioned.

   The two halves of the inherited stack have diverged. `pdb2pqr` — protonation,
   charges and radii — is healthy: pushed April 2026, a pure-Python `any.whl` on
   PyPI, so it is a plain dependency and does the same job for both backends.
   APBS is not: last code commit December 2022 (2024 activity is dependabot
   only), last release 3.4.1 in April 2022, 85 open issues, and no PyPI presence
   because it is C/C++/Fortran linking metis and SuiteSparse. The APBS installed
   on the development machine did not run at all — Homebrew's metis had gone
   missing, leaving a binary that exists and cannot start.

   Requiring that to see which face of a protein is acidic is a bad trade, and
   seeing which face is acidic is what surface potential is overwhelmingly for.
   So `electrostatics()` defaults to a screened Coulomb field summed in numpy —
   no binary, ~1s for ubiquitin on a 1 A grid — and uses APBS when a *runnable*
   binary is found. ChimeraX made the same call: `coulombic` is its default.

   **What the approximation costs, measured rather than asserted.** One uniform
   dielectric everywhere means no low-dielectric protein interior and no
   reaction field at the solvent boundary — precisely what PB exists to model.
   Against APBS on 1UBQ from an identical PQR, sampled on a shell 3 A outside
   the atoms: Pearson r = 0.958, sign agreement 94.1%, and Coulombic magnitudes
   about 1.6x low (-1.87..1.38 vs -2.94..2.88 kT/e). The shape is right; the
   scale is not. So the field is safe to look at and unsafe to integrate, and
   every result carries the `method` that produced it rather than leaving a
   caller to guess which physics they got.

   Both backends emit the same artifact — a kT/e grid, writable as OpenDX — so
   they are directly comparable and the viewer never learns which ran. Mol\*
   already parses `dx`/`ccp4`/`dsn6`/`cube` and ships `external-volume` and
   `volume-value` colour themes, so displaying either is plumbing rather than
   new capability.

   Rejected: building APBS wheels ourselves (real, with precedent in
   `pymol-open-source-wheel`, but it means adopting a dormant Fortran codebase
   and its four-platform CI); requiring conda for the extra (conda-forge does
   have apbs for linux-64, osx-64, osx-arm64 and win-64, and remains the
   recommended way to get it, but it cannot be the only way to get a picture);
   and `delphi4py`, which ships manylinux x86_64 wheels only and so does not
   exist on Apple Silicon.

## Decisions (2026-08-09b) — one molecule, chosen once

9. **The viewer and the analysis load the same assembly, and say so.**

   They did not. Mol\*'s default preset builds the biological assembly; the
   Python side parsed the asymmetric unit. Nothing compared them, and for
   1UBQ, 1CA2, 4HHB, 1BNA and 5FJI the two are identical, so the entire test
   corpus was blind to it. On 1HHO they differ by exactly 2x — the asymmetric
   unit is one alpha-beta dimer, the assembly is the alpha2beta2 tetramer.

   The consequences were not cosmetic. Buried surface area treated
   symmetry-contacted surface as solvent-exposed. Interfaces that exist only
   between symmetry copies were invisible, and for a homodimer deposited as a
   single chain, `interface()` cannot see the dimer on screen at all. An
   electrostatic potential grid computed on the asymmetric unit does not even
   span the displayed molecule.

   So `fetch_structure(assembly=...)` is one choice honoured by both halves,
   defaulting to `biological`: analysis should describe what is on screen, and
   what is on screen should be the molecule as it exists. The reply states the
   atom count each half arrived at, so a future divergence is visible in the
   answer rather than latent. A differential test asserts the two agree on
   1HHO in both settings, and asserts the settings differ from each other so
   agreement on the wrong molecule cannot pass.

   **Symmetry copies are part of a residue's identity.** Copies share chain id
   and residue number, so keying on those alone merges two physically distinct
   residues, halving the count and summing their buried areas — numbers that
   all look plausible. Residue keys, handle summaries and interface residues
   now carry `sym`, which appears only when more than one copy is present.

   **Not solved here: addressing one copy.** Handles reach the viewer as
   `atom.id` ranges, and an assembly duplicates those ids, so a set covering
   one copy cannot be expressed. Everything the tools currently produce is
   symmetric across copies, which is why the transport stays exact — but until
   it can say "this copy", `interface("A", "B")` on a tetramer reports the
   total A-B interface rather than alpha1beta1 alone.

   **Correction (2026-08-12): the blocker this stated was wrong.** The
   paragraph above used to end "Mol\* exposes `operator-name` for colouring but
   appears to offer no MolScript predicate for it, so this needs new transport,
   not a bigger array." Mol\* 4.18 does offer one, and no new transport was
   needed. Decision 15 records what was actually in the way and closes this.

   Two things worth recording. The biological assembly is not always larger:
   12E8's asymmetric unit holds two Fabs and its assembly is *half* its size,
   so "copies" is not a multiplier. And a capsid is 60 copies, so expansion is
   refused above `MAX_ASSEMBLY_COPIES`, read from the operator list before
   anything is built. PDB input keeps assemblies in REMARK 350, which biotite
   does not parse; that falls back to the deposited coordinates and says so.

## Decisions (2026-08-10) — publication rendering

10. **Styles are named enums in the tool schema, not YAML recipes on disk.**
    Supersedes the "YAML recipes" wording in Phase 4 and strikes the YAML
    preset loader from the proteinblend-mcp reuse table.

    A YAML file is the right shape for a human curating recipes in an editor.
    For a model it is the wrong shape in three ways: it must first discover the
    file exists, its names are invisible at the point of use because they are
    not in the tool schema, and a typo cannot be refused because nothing
    validates against a list. That last one is not hypothetical here —
    `show(representation="cartoonn")` was accepted and rendered nothing, which
    is why `checkName` exists and reads the live Mol\* registries rather than a
    hardcoded list.

    So presets, lighting rigs, effect styles and background variants are all
    enums declared in the tool signature and reported by `capabilities()`,
    alongside the representations and colour themes it already reports. The
    recipes themselves live in code. A user-authored YAML overlay can be added
    later if anyone actually wants to write one; nothing in this design
    forecloses it, and building it before there is a second author would be
    speculative.

    The same reasoning settles the scope of the style surface. Mol\* 4.18
    already carries far more than Phase 4 assumed, and the work is to name it,
    not to build it:

    - **Lighting rigs.** `renderer.light` is an *array* of
      `{inclination, azimuth, color, intensity}` with separate `ambientColor`
      and `ambientIntensity`, so `ring`, `three-point`, `rim`, `studio` and
      `flat` are generated light lists behind one enum rather than five
      features.
    - **Effects and backgrounds.** `postprocessing` carries `outline`
      (with its own colour and threshold), `occlusion` (multi-scale SSAO),
      `shadow`, `dof`, `bloom` (luminosity or emissive), `sharpening`,
      SMAA/FXAA, and a `background.variant` of skybox, image, radial gradient
      or horizontal gradient. Cel shading is `celShaded` per representation
      plus `renderer.celSteps`; the flat look is `ignoreLight`; the ghost look
      is `xrayShaded`, which also accepts `'inverted'`.
    - **PBR materials.** `material: {metalness, roughness, bumpiness}` and
      `emissive` are ordinary per-representation params, so they travel the
      same path `sizeFactor` and `alpha` already do.

    Opacity and canvas background colour, carried in from Phase 2, are the same
    kind of work and land first because everything else composes with them.

11. **Figure size is physical, and DPI is a real number written into the file.**

    Mol\* thinks in pixels and journals think in millimetres — Nature's single
    column is 89 mm, its double column 183 mm. Requiring a model to multiply
    those into a pixel count invites a figure that claims 600 dpi and is 900
    pixels wide, which is a wrong answer that looks like a right one and which
    no return value would catch. So `snapshot()` takes a physical width and a
    `dpi`, computes the pixels itself, and stamps the resolution into the file
    (PNG `pHYs`, TIFF tags) so the claim survives outside the tool reply.
    Explicit pixel `width`/`height` stays available as an escape hatch.

    This adds **Pillow** as a dependency. It writes TIFF, writes the DPI
    metadata, and decodes PNG for the pixel assertions of decision 12; without
    it the PNG side is a hand-built chunk and the TIFF side is a project.

    Resolution is not DPI-bound. Capture goes through a framebuffer, so the cap
    is `gl.MAX_RENDERBUFFER_SIZE` — commonly 16384, which at double-column
    width is about 2270 dpi. The binding constraint is memory: 16384² RGBA is
    roughly a gigabyte per target and postprocessing allocates several, so the
    real ceiling arrives well before the advertised one. The limit is probed
    and reported, and exceeding it is an error rather than a truncated or black
    image.

12. **A render is verified by its pixels, and a render pass must prove it ran.**

    Phase 4 is the phase most exposed to protean's standing failure mode, and
    the usual cheap signal fails here. Screenshot byte size cannot separate a
    transparent background from a black one, barely moves when an outline is
    switched on, and moves the *wrong way* for reduced opacity. So the phase
    opens with a harness, not a feature: decode the returned PNG and assert on
    actual pixels — corner RGBA for background colour, the alpha channel for
    transparency, decoded dimensions for sizing, an edge-pixel count for
    outline, mean alpha over the molecule's footprint for opacity. Each
    assertion is confirmed by breaking the thing it guards.

    The path tracer needs this most, because it fails silently by construction.
    `IlluminationPass.isSupported()` checks for the `textureFloat`,
    `colorBufferFloat`, `depthTexture` and `drawBuffers` extensions, and when
    any is missing the constructor simply returns early with `_supported =
    false`. Asking for a ray-traced image on such a machine yields an ordinary
    raster image and a perfectly successful reply. The capability is therefore
    probed explicitly and the answer is reported in the response, in the
    tradition of `size_validated` and `handles_note`: a render that could not
    be traced says so rather than defaulting to "passed".

    Two consequences follow. Progressive accumulation means completion is an
    iteration count, not an event — the existing render pump settles on a quiet
    commit queue, which a converging tracer does not signal, so tracing needs
    its own convergence wait or it captures a noisy image and reports success.
    And the differential CI job runs headless SwiftShader, where those float
    extensions are the likely casualties and where a path tracer would be
    punishingly slow even if present; ray-traced output is expected to be
    opt-in behind an environment gate and verified on a real GPU, as
    `PROTEAN_APBS` and `PROTEAN_MSA_LIVE` already are.

13. **Turntables land in Phase 4; the keyframed timeline stays in Phase 5.**

    Mol\* already ships `camera-spin`, `camera-rock`, `spin-structure`,
    `explode-units`, `assembly-unwind` and `state-interpolation`, plus
    `trackball.animate` in `spin` and `rock` modes, so orbiting a molecule is
    configuration rather than construction. What it does not ship is an
    encoder: frames must be captured and handed to ffmpeg, which is exactly
    what Phase 5 was already scoped to build.

    Splitting there keeps the phase boundary honest. Phase 4 exposes the motion
    Mol\* hands us and the frame-capture loop that a turntable needs, both of
    which reuse the snapshot pipeline it is already building. Phase 5 then adds
    keyframes, trajectory frames and encoding on top of a capture loop that
    exists, rather than starting from nothing.

    The Blender bridge is dropped rather than deferred. It was already marked
    optional, proteinblend-mcp does it better, and with a path tracer in the
    viewer nothing in this phase needs it.

## Decisions (2026-08-11) — trajectories

14. **Trajectories are read with biotite, not MDAnalysis.** Amends Phase 5's
    first line.

    PLAN named MDAnalysis before anyone checked what was already here. biotite
    is already a dependency and already reads XTC, TRR, DCD and NetCDF —
    verified by writing and reading both XTC and DCD and getting the
    coordinates back exactly. MDAnalysis would be a large addition, with a
    correspondingly large transitive tree, for formats this project has no
    call for.

    What it would have brought and we give up: Amber PRMTOP and GROMACS TPR
    topology parsing, the LAMMPS formats, and its analysis library. The first
    two matter least here because a trajectory in protean is laid onto a
    structure that is already loaded, so the topology comes from the mmCIF or
    PDB that structure came from. The third is RMSD, which Phase 3 already
    computes, and RMSF, which is three lines of numpy over a coordinate stack.

    The loader keeps its formats in one table, so an optional MDAnalysis
    backend can be added behind it if a format ever actually turns up.

    **The check this design makes necessary.** A trajectory file carries
    coordinates and nothing else — no atom names, no chains. Pairing one with
    the wrong structure produces a file that parses cleanly and animates
    smoothly and describes nothing, and the only evidence available is the
    atom count. So the counts must match exactly and a mismatch is refused,
    naming both numbers, rather than warned about.

## Decisions (2026-08-12) — addressing one symmetry copy

15. **A handle names a symmetry copy with Mol\*'s operator, and the mapping is
    proven by geometry.** Closes decision 9's open limitation and backlog
    item 7.

    A biological assembly repeats the asymmetric unit, and the copies share
    chain ids, residue numbers and `atom_id`. A handle travels as an atom-id
    predicate, so it matched the named atom in *every* copy: a set covering
    one copy could not be expressed at all. Two patches had accumulated around
    that one gap — `rank` was refused on multi-copy assemblies, and
    `interface()` could only report a fused total.

    `to_molscript` now groups a set by biotite's `sym_id` and keys each group
    on the operator, `(= atom.op-name \`ASM_k\`)`. A set that covers every copy
    identically is still emitted as the bare atom-id test, which means exactly
    the same thing, so every existing handle sends byte-for-byte what it always
    sent. `sym N` joins the grammar as a leaf predicate and `interface()` takes
    `copy=N`; with no copy named it reports the total *and* a per-copy
    breakdown, because the total is several interfaces fused and used to read
    like one.

    **Three findings, each of which parses cleanly and matches nothing.** This
    is why decision 9 recorded the wrong blocker.

    - The MolScript **alias** is `atom.op-name`. The symbol-table path
      `atom-property.core.operatorName` is real, and is what a source reading
      finds, but the script parser does not accept it.
    - MolScript delimits string literals with **backticks**. A double-quoted
      `"ASM_1"` parses as a *symbol*, compares unequal to every string, and
      comes back as a successful empty selection.
    - `operatorKey` is unusable: every unit in an assembly reports key `-1`,
      so only the name distinguishes copies.

    **The mapping is off by one, and only coordinates could have caught it.**
    Mol\* pre-increments its operator index, so the first operator is `ASM_1`
    while biotite's `sym_id` is 0-based: `sym_id k` is `ASM_{k+1}`. Every copy
    has the same atoms, residues and chains, so a permuted or offset mapping
    gives identical counts, identical residue lists and a normal-looking
    picture. The tests therefore assert **centroids**, on 1HHO (2 copies) and
    1COI (3 copies — a 2-copy fixture cannot tell "correct" from "consistently
    reversed"), and each copy must be nearer its own operator than any other.
    Hard-coding `ASM_0`, restoring the off-by-one, and dropping the operator
    clause each fail that suite.

    **What was verified in the picture, not the numbers.** `chain A` on 1HHO's
    assembly reds two subunits; `chain A and sym 0` reds one. The first attempt
    at that check was inconclusive in a way worth recording: from the default
    camera the view is nearly down the assembly's 2-fold, so copy 0's spacefill
    sits exactly in front of copy 1's and the two selections give almost the
    same red-pixel count (5004 vs 4937) despite differing by 1167 atoms. That
    was occlusion, not a bug — but it means a pixel count from that angle is
    not evidence either way. Orbiting 90 degrees separates them.

## Decisions (2026-08-13) — alternate conformers

16. **Every alternate conformer is loaded; analysis resolves one state.**
    Closes backlog item 8's open `alt` decision, planned and corrected in
    `docs/alternate-conformers.md`.

    An atom resolved in two positions is stored twice, tagged `A`/`B`, each
    with an occupancy. The fact that governs the whole design is that **the
    states never coexist**: each molecule in the crystal is in one of them.
    The file describes a population, not a molecule.

    Previously biotite kept one conformer per site, so no label survived and
    `alt` was refused. Both halves now load every conformer, which makes the
    viewer and the analysis hold the same atoms — 15929 on 5FJI, where the
    Python side held 15712 and the 217-atom difference had to be explained in
    every load message.

    **The obvious implementation is wrong, and this is the part worth
    keeping.** The plan first proposed putting the conformer into residue
    identity, by analogy with decision 9 putting `sym_id` there so symmetry
    copies could not merge. Symmetry copies *partition* the atoms; conformer
    states **overlap**. Only the atoms that actually differ carry a letter, so
    on 5FJI 13 of the 32 residues with alternates also hold shared atoms —
    SER320 is 3 shared plus A and B variants of 3 more. Keying on the letter
    would have split that residue into three fragments, none of them a
    residue: worse than the double-counting it was meant to prevent.

    So residue identity is unchanged, and instead every tool that reads
    coordinates resolves a **conformer state** — the shared atoms plus one
    letter — before computing, defaulting to the letter with the most
    occupancy and saying which it used. Without that, both states land in one
    residue entry and their areas sum: up to +48% on 5FJI's worst residues,
    while the structure total moves 0.14%. Small where anyone would look.

    **`alt` is literal**, as PyMOL means it: `alt A` is the 206 labelled atoms
    on 5FJI, not the conformer. The state is `alt ''+A`, 15712 atoms — exactly
    what the Python side used to hold. Making `alt A` mean the state was
    rejected because both states contain the shared backbone, so `alt A and
    alt B` would return it rather than nothing, and two labels that read as
    mutually exclusive would overlap. `alt ''` and `alt .` both spell "no
    alternate", PyMOL's way and biotite's.

    **What PyMOL does, measured rather than recalled.** It loads everything,
    exposes `alt`, and computes `get_area` over mutually exclusive conformers
    without comment — 0.13% from the filtered answer, silently. The `alt ''+A`
    filter is folklore the user is expected to know. Copying the surface was
    right; copying the silence was not, because a model calling `interface()`
    has not read a decade of forum posts.

    **Three things only measurement found**, each recorded where it bites:

    - Template topology cannot tell conformers apart and bonds them to each
      other: 16 such bonds on 1AKE, `ARG167/CD(A)—NE(B)`, leaving CD(A) with
      three bonds. They are dropped, so `extend 1`, `bound_to` and `neighbor`
      stay inside a state. **`extend 2` can still cross** through a shared
      atom, because the file genuinely bonds N to both alternate CAs and
      removing one would invent topology rather than filter it. A caveat, not
      a fix; analysis never walks bonds.
    - `alt ''` returned nothing until the quotes were stripped. They survive
      tokenisation, so the value arrives as a two-character string, and the
      selection read as "this structure has no unlabelled atoms" — the
      silent-empty answer the grammar exists to prevent.
    - `altloc="occupancy"` is not `altloc="first"`: 70 atoms differ on 5FJI.
      Choosing the dominant conformer rather than whichever came first moves
      some existing answers, deliberately.

    Handle transport is unaffected — every conformer row carries its own
    `atom_site.id` — and that is asserted against a real viewer by atom
    identity rather than assumed, because assuming it is what item 7 punished.
