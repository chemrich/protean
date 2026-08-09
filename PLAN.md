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

High-resolution snapshot pipeline (Mol* ray-tracing pass, transparent backgrounds, outline/occlusion styles). Preset system: YAML recipes (publication-cartoon, active-site, ghost-surface — a chance to do the ghost-heart transparency properly, with correct per-selection scoping this time). Optional Blender bridge: export scene to proteinblend-mcp for cinematic renders rather than duplicating that work here. *Exit: journal-ready TIFF/PNG at arbitrary DPI from one tool call.*

### Phase 5 — Trajectories + animation (v0.4)

MDAnalysis-backed loading (XTC/DCD/TRR + topology), frame streaming to the viewer, playback controls, per-frame measurements (RMSD/RMSF/distance timeseries as structured data). Animation timeline: keyframed camera + representation states with smooth interpolation; ffmpeg encoding to MP4/GIF. *Exit: load a trajectory, plot RMSF, render a 10-second annotated movie.*

### Phase 6 — Polish and publish (v1.0)

README (installation for Claude Code / Desktop / uvx, tool tables, example prompts — same structure as MCPymol/proteinblend). CHANGELOG, tagged releases, PyPI publish. Attribution: Mol*, MDAnalysis, biotite, APBS, FastMCP. Benchmark doc: side-by-side PyMOL vs protean on 5 common tasks.

## Reuse from existing projects

| From | What |
|------|------|
| MCPymol | conservation pipeline + cache, test patterns, README/release conventions, tool naming |
| proteinblend-mcp | port-scan + handshake code, YAML preset loader, addon dispatch pattern (adapted to TS) |

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
