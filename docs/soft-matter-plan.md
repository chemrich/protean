# Soft Matter

**An implementation plan for alternative space-filling representations in Mol\* and PyMOL**

Status: draft v1 · Target engines: Mol\* (primary), PyMOL (secondary), Blender/Molecular Nodes (hero renders)

---

## 0. Summary

Standard space-filling (CPK) rendering encodes a set of aesthetic conventions that are historically contingent rather than scientifically motivated: hard glossy spheres, a single hard key light, and a mid-century chemistry-set palette. Those conventions carry three real costs — total interior occlusion, an ugly and information-free sphere-intersection artifact, and an implied precision the underlying data usually does not have.

This project delivers **36 alternative treatments** grouped into four geometry strategies plus a temporal modifier layer, built on a single shared framework so that they are configuration rather than 36 one-off forks. Each treatment is required to bind at least one **data channel**, which is what separates a visualization from a skin.

The framework ships as an Mol\* extension package, a PyMOL plugin covering the tractable subset, and a Blender/Molecular Nodes export path for treatments that need real displacement and fiber simulation.

**Headline sequencing:** screen-space treatments first (no geometry work, immediate payoff), then sphere-modifier, then sphere-substitute, then surface-derived, then temporal. The three treatments with the highest expected value — Radiolaria, PCB, and Voxel — are deliberately spread across phases 3 so that one lands early as a credibility anchor.

---

## 1. Goals and non-goals

### Goals

| # | Goal | Measure |
|---|---|---|
| G1 | Ship a reusable treatment framework, not 36 forks | New G4/G2/G1 treatment addable in <300 LOC, no core changes. G3 exempt — surface topology work does not compress to that budget |
| G2 | Every treatment binds ≥1 data channel | Enforced at manifest validation |
| G3 | Interactive performance at realistic sizes | 60 fps @ 50k atoms, 30 fps @ 200k, desktop GPU |
| G4 | Reproducible, shareable views | MolViewSpec extension carries treatment state |
| G5 | Graceful degradation | Every treatment has a documented fallback to plain spacefill |
| G6 | Publication-grade still output | 4K offscreen render path, deterministic |

### Non-goals

- Replacing cartoon/ribbon representations. This project is strictly about the space-filling family.
- Physically accurate material simulation. We want *legible* felt, not radiometrically correct felt.
- Real-time knit-stitch geometry. That is explicitly delegated to the Blender path.
- Molecular editing, docking, or any analysis beyond computing the data channels listed in §3.3.
- PyMOL parity across all 36. PyMOL gets the subset its raytracer can express (see §5, Phase 6).

### Intellectual-property constraint

**Binding on every treatment, palette, and asset in this project.**

No treatment may depict, imitate, or be named after a protected character, film, studio, franchise, or living designer's signature style. Aesthetics are drawn only from unprotected sources: historical art movements, traditional crafts, natural forms, vernacular and historical architecture, and industrial artifacts whose visual conventions are functional rather than authored.

Practical rules:

- **Name for the technique, not the source.** "Cel-shaded painterly," not a studio name. "Duotone spot-ink," not a trademarked printer brand. "Timber lattice," not a living architect.
- **Characters must be original.** Anthropomorphism is permitted and can teach well. The requirement is that a character be *designed*, not *recalled* — the test is whether a reviewer can name a source. If someone says "that's the thing from X," it fails, even where the resemblance was unintentional. Note the convergence trap: the obvious cute-atom designs (dark fuzzy sphere with two white eyes; pale blob with one large eye; sphere with stubby limbs and a dot mouth) each land on a specific existing character almost immediately. Original design here takes deliberate iteration, not the first sketch.
- **Trademarks used descriptively, or not at all.** Naming a tool the project depends on is fine. Naming a brand as the aesthetic is not.
- **Historical is safe; contemporary needs checking.** Byzantine mosaic, ukiyo-e, gothic vaulting, muqarnas, cyanotype, and radiolarian skeletons are all long out of copyright. Anything from the last century gets a naming review before it enters the catalogue.

Enforcement: naming review is a required gate at treatment triage, and again at Phase 7 before the gallery ships. See §8.

---

## 2. Design principles

These five rules were derived from the treatment survey and are binding on every spec sheet in §6. A proposed treatment that satisfies none of them should be cut.

**P1 — Prefer aesthetics that defeat occlusion.**
Spacefill's core failure is that you cannot see inside. Porous, latticed, transparent, and stroke-only treatments fix this as a side effect of being attractive. "Make it porous" is a stronger idea than "make it fuzzy."

**P2 — Prefer aesthetics that redeem the intersection curve.**
The seam where two spheres cut each other is spacefill's ugly secret. Leading, grout, solder mask, and mortar all *want* a line at exactly that curve. Turning the artifact into the motif is the highest-leverage move available.

**P3 — Every treatment must offer a free data channel.**
Lichen density = SASA. Film thickness = electrostatic potential. Spine direction = force vector. Tarp coverage = pLDDT. Halftone dot size = B-factor. If a treatment cannot carry data, it is decoration and belongs in a demo gallery, not the library. **This rule has no exceptions** — a proposal that can only be justified on charm is a proposal to cut. Character-based treatments are not exempt, and do not need to be: a face is an unusually rich carrier. Gaze direction is a legible vector channel (`DC-HBOND`, `DC-FORCE`) that ordinary spacefill has no way to express; eye size maps to `DC-OCC`; expression maps to local strain. Anthropomorphism earns its place by carrying data, same as felt or lattice.

**P4 — Time is unexplored territory.**
Molecular graphics has explored material exhaustively and temporal aesthetics not at all. Stop-motion boil, phosphor persistence, and accretion over a trajectory are cheap and unclaimed.

**P5 — Prefer materials honest about uncertainty.**
Glossy plastic asserts a precision the data does not have. Voxels, scaffolding, blueprint, and breadboard say "provisional" out loud. This is the strongest justification for the whole project: the default representation lies, and these do not.

---

## 3. Architecture

### 3.1 The treatment abstraction

A **treatment** is a declarative manifest resolving to five components:

```
Treatment := {
  geometry:    GeometryStrategy   // how atoms become primitives
  material:    MaterialSpec       // roughness, metalness, bump, emissive, alpha
  post:        PostStack          // outline, occlusion, shadow, halftone, persistence
  bindings:    DataBinding[]      // channel -> visual parameter
  palette:     PaletteRef         // named palette + mapping mode
  temporal?:   TemporalModifier   // optional; see 3.2 / Phase 5
}
```

The manifest is validated against a JSON Schema at load. Invalid manifests fail loudly rather than silently rendering grey spheres.

### 3.2 Geometry strategy taxonomy

All 36 treatments fall into four strategies plus one orthogonal modifier. This taxonomy drives the phase order, because cost rises steeply from G4 to G3.

| ID | Strategy | Mechanism | Cost | Count |
|---|---|---|---|---|
| **G4** | Screen-space | No geometry change. Shading, post-processing, and palette only. Composes over *any* existing representation. | Lowest | 8 |
| **G2** | Sphere-modifier | Keep the sphere primitive; perturb it via bump/normal maps, displacement, per-atom radius jitter, or material swap. | Low | 7 |
| **G1** | Sphere-substitute | Replace each sphere with an instanced alternate mesh or primitive set. | Medium | 9 |
| **G3** | Surface-derived | Compute a molecular surface (SES/SAS), then re-tessellate or decorate it. Requires a surface pass and topology work. | High | 9 |
| **T** | Temporal modifier | Orthogonal layer applied to any of the above; operates across trajectory frames or accumulates state. | Medium | 3 |

**Why this ordering matters:** G4 treatments require zero changes to Mol\*'s geometry pipeline — they are post-processing configs and custom color themes. That means Phase 1 delivers eight visually distinct, shippable treatments before we write a single line of mesh-generation code. It de-risks the project and produces early screenshots.

### 3.3 Data channel registry

Channels are named providers computing a per-atom scalar or vector. Each is implemented once and consumed by many treatments. Per **P3**, no treatment ships without at least one binding.

| ID | Channel | Type | Source | Cost |
|---|---|---|---|---|
| `DC-ELEM` | Element identity | categorical | model | free |
| `DC-CHAIN` | Chain / entity | categorical | model | free |
| `DC-RESID` | Residue identity / number | categorical | model | free |
| `DC-SS` | Secondary structure | categorical | DSSP or model | free |
| `DC-BFAC` | B-factor | scalar | model | free |
| `DC-OCC` | Occupancy | scalar | model | free |
| `DC-PLDDT` | Predicted confidence | scalar | model (AF/ESM) | free |
| `DC-SASA` | Solvent-accessible surface area | scalar | Shrake–Rupley | moderate |
| `DC-DEPTH` | Burial depth from surface | scalar | derived from SASA/SES | moderate |
| `DC-CONS` | Sequence conservation | scalar | external MSA | external |
| `DC-ESP` | Electrostatic potential | scalar | Coulombic approx or APBS import | high |
| `DC-RES` | Map resolution / coordinate uncertainty | scalar | header + B-factor | free |
| `DC-HBOND` | H-bond direction | vector | geometric detection | moderate |
| `DC-FORCE` | Per-atom force / displacement | vector | trajectory diff | moderate |
| `DC-CURV` | Local surface curvature | scalar | SES pass (Phase 4) | free with surface |

`DC-SASA`, `DC-DEPTH`, and `DC-ESP` are computed once per structure load and cached on the model. `DC-ESP` is opt-in — it is expensive and only two treatments consume it.

**Delivery schedule.** A channel must land no later than the earliest phase that consumes it. Verified against the catalogue:

| Phase | Channels delivered | Earliest consumer |
|---|---|---|
| 0 | `DC-ELEM`, `DC-CHAIN`, `DC-RESID`, `DC-SS`, `DC-BFAC`, `DC-OCC`, `DC-PLDDT`, `DC-RES`, `DC-SASA`, `DC-DEPTH` | `SS-01`, `SS-03`, `SS-04` (Phase 1) |
| 1 | `DC-FORCE` | `SS-07` (Phase 1) |
| 2 | `DC-HBOND`, `DC-ESP` | `SM-05`, `SM-07` (Phase 2) |
| 4 | `DC-CURV`, `DC-CONS` | `SD-05`, `SD-01` (Phase 4) |

`DC-SASA` and `DC-DEPTH` are pulled into Phase 0 despite moderate cost, because three Phase 1 treatments bind them. This is why Phase 0 runs 3–4 weeks rather than 2–3.

### 3.4 Palette system

Palettes are first-class and independent of treatments, so any palette composes with any treatment.

- `cpk` — reference/control, for A/B comparison
- `dyed-wool` — madder, indigo, weld yellow, walnut, undyed cream
- `spot-ink` — 2–3 spot inks with defined multiply-blend behaviour
- `sumi` — near-black key plus 3 flat washes
- `pcb` — solder-mask green, ENIG gold, silkscreen white, FR-4 tan
- `oxide` — die-shot aluminium/polysilicon/tungsten false colour
- `mineral` — silica white, limestone, lichen sage, oxidised copper
- `phosphor` — P1 green / P3 amber, single-hue emissive
- `concrete` — greyscale with warm and cool tints for raking light

Mapping modes: `categorical`, `continuous`, `quantized(n)`, `dual-band`. Each palette declares which modes it supports.

### 3.5 Engine targets

| Engine | Role | Coverage |
|---|---|---|
| **Mol\*** | Primary. All new work lands here first. | 36/36 |
| **PyMOL** | Secondary. Ships the subset expressible with `ray_texture`, `ray_trace_mode`, and CGO. | 7/36 (Phase 6) |
| **Blender + Molecular Nodes** | Hero stills and film. Real displacement, hair systems, true subsurface. | Export path only |

---

## 4. Technical foundations

### 4.1 Mol\* — custom representation provider

Every G1/G2/G3 treatment is a `StructureRepresentationProvider` wrapping one or more visuals. The skeleton is stable across treatments:

```ts
export const TreatmentParams = {
  ...UnitsMeshParams,
  porosity:    PD.Numeric(0.4, { min: 0, max: 1, step: 0.01 }),
  shellDetail: PD.Numeric(2,   { min: 0, max: 4, step: 1 }),
  jitterSeed:  PD.Numeric(42),
}

function createTreatmentMesh(ctx, unit, structure, theme, props, mesh) {
  const builder = MeshBuilder.createState(estimateVertexCount(unit), 1024, mesh)
  const pos = unit.conformation.invariantPosition
  for (const i of unit.elements) {
    builder.currentGroup = i
    // emit per-atom geometry here
  }
  return MeshBuilder.getMesh(builder)
}
```

Key facts that constrain design:

- `builder.currentGroup` must be set to the element index so that picking, highlighting, and per-atom colour themes keep working. **Losing picking is an automatic reject at review.**
- Geometry is built per `Unit`, so symmetry mates and assemblies come free via instancing — do not iterate over the whole structure.
- `Spheres`, `Cylinders`, `Lines`, `Points`, and `Text` are cheaper dedicated primitives than `Mesh`. Prefer them where a treatment allows: pom-pom fibers are `Cylinders`, pointillism is `Points`, vector-phosphor is `Lines`.

### 4.2 Mol\* — color and size themes

Two of the six cheapest wins need no representation code at all.

```ts
plugin.representation.structure.themes.colorThemeRegistry.add(DyedWoolColorThemeProvider)
plugin.representation.structure.themes.sizeThemeRegistry.add(HandmadeJitterSizeProvider)
```

`HandmadeJitterSizeProvider` returns `vdw * (0.96 + hash(elementIndex, seed) * 0.08)`. Hash-based rather than RNG-based so it is deterministic across reloads and across symmetry mates — a mate that jitters differently from its parent looks broken.

### 4.3 Mol\* — material and post-processing

Available without any custom code, and sufficient on their own for the entire G4 set:

- **Material:** `metalness`, `roughness`, `bumpiness`, `bumpFrequency`, `bumpAmplitude`, `emissive`
- **Post:** `outline` (scale, threshold, color), `occlusion` (SSAO — radius, bias, blur kernel), `shadow`, `fog`, `dpoit` transparency, global `illumination` mode
- **Fiber halo without shaders:** render a second spacefill layer at 1.10–1.15× radius, high `bumpiness`, alpha ≈ 0.2. Survives rotation and costs one extra draw call.

Treatments needing effects outside this set (halftone, phosphor persistence, film interference) require a **custom post-processing pass**. Budget one week of framework work in Phase 1 to add a user pass slot to the render pipeline; five G4 treatments depend on it.

### 4.4 Mol\* — instancing and LOD

- G1 treatments emit an instanced template mesh, not per-atom unique geometry. Radiolaria emits *one* geodesic shell instanced N times with per-instance scale and rotation.
- Every treatment declares `lodFallback` in its manifest. Above the atom-count threshold the renderer swaps to plain spacefill with the treatment's palette retained, so colour semantics survive even when texture does not.
- Thresholds are per-treatment and measured, not guessed (see §7).

### 4.5 PyMOL — what the raytracer can and cannot do

Available and underused:

```
set ray_texture, 2            # 0 none, 1 matte, 2 fuzzy, 3 crackle
set ray_trace_mode, 3         # quantized colour + ink outline (cel shading)
set ray_trace_color, black
set ray_trace_gain, 0.2
set specular, off
set spec_reflect, 0
set ambient, 0.42
set direct, 0.25
set reflect, 0.65
set ambient_occlusion_mode, 1
set light_count, 3
set depth_cue, on
set sphere_quality, 3
set antialias, 2
```

`ray_texture 2` perturbs surface normals at render time and delivers convincing felt in a single line. `ray_trace_mode 3` delivers cel shading in a single line. These two settings alone cover several treatments essentially for free.

Deterministic jitter, matching the Mol\* hash approach:

```python
def jitter(idx, seed=42, amp=0.04):
    h = (idx * 2654435761 ^ seed) & 0xFFFFFFFF
    return 1.0 - amp + 2 * amp * (h / 0xFFFFFFFF)

cmd.alter("all", "vdw = vdw * j(index)", space={'j': jitter})
cmd.rebuild()
```

**Hard ceiling:** PyMOL has no custom shader path. Its textures are fixed procedurals. Anything requiring bespoke shading is either CGO geometry or out of scope. CGO (`cmd.load_cgo` with `SAUSAGE`, `CYLINDER`, `TRIANGLE`) covers yarn, pom-pom, urchin, and lattice treatments at the cost of writing the geometry by hand.

### 4.6 Blender / Molecular Nodes

For treatments where Mol\* can only approximate:

- Geometry Nodes instancing a stitch loop along a spiral → true amigurumi
- Hair particle system with child interpolation → true felt with self-shadowing
- Subsurface scattering → wax, soap film, translucent minerals
- Cycles volumetrics → glass envelopes, geode interiors

Export path: Mol\* state → treatment manifest JSON → Python importer that reconstructs the treatment as a MN node group. This is a **hero-render pipeline, not an interactive one**, and should be scoped accordingly.

---

## 5. Phased delivery

Durations assume one full-time graphics engineer plus part-time design review. Phases 1–4 are strictly ordered by geometry cost; Phase 5 and 6 can run in parallel with 4 if staffed.

### Phase 0 — Foundations (3–4 weeks)

- Repo scaffold, Mol\* extension package skeleton, CI with visual-regression snapshots
- Treatment manifest schema + validator
- Palette system (§3.4) with all nine palettes stubbed
- Data channel providers: `DC-ELEM`, `DC-CHAIN`, `DC-RESID`, `DC-SS`, `DC-BFAC`, `DC-OCC`, `DC-PLDDT`, `DC-RES` (free-tier), plus `DC-SASA` and `DC-DEPTH` (required by three Phase 1 treatments — see §3.3)
- Benchmark harness: fixed structure set, automated fps + memory capture
- **Exit:** a no-op treatment loads, validates, renders identically to stock spacefill, and benchmarks clean.

### Phase 1 — Screen-space treatments, G4 (4–5 weeks)

Eight treatments, zero geometry code. Includes one week of framework work adding a **user post-processing pass slot**, which five of the eight depend on (`SS-02`, `SS-03`, `SS-04`, `SS-06`, `SS-07`). Adds the `DC-FORCE` provider for `SS-07`.

Delivers: `SS-01` … `SS-08`.

- **Exit:** eight treatments switchable at runtime, all composing correctly over stock spacefill, all with ≥1 binding, visual-regression snapshots locked.

### Phase 2 — Sphere-modifier, G2 (4 weeks)

Seven treatments using bump/displacement/material/jitter. Adds `DC-HBOND` (for `SM-05`) and the opt-in `DC-ESP` provider (for `SM-07`).

Delivers: `SM-01` … `SM-07`.

- **Exit:** all seven at 60 fps @ 50k atoms; jitter deterministic across symmetry mates.

### Phase 3 — Sphere-substitute, G1 (6–8 weeks)

Nine treatments requiring instanced alternate geometry. This is where **Radiolaria**, **PCB**, and **Voxel** land — the three highest-value treatments in the catalogue. No new channels — all bindings already delivered.

Delivers: `SP-01` … `SP-09`.

- **Exit:** instanced template meshes confirmed (one geodesic buffer, N instances); picking preserved on all nine; LOD thresholds measured and recorded.

### Phase 4 — Surface-derived, G3 (8–10 weeks)

Nine treatments requiring an SES/SAS pass and non-trivial topology work — re-tessellation, seam extraction, region growing. Highest technical risk in the project. Adds `DC-CURV` (free with the surface pass) and `DC-CONS` (external MSA).

Delivers: `SD-01` … `SD-09`.

- **Exit:** surface re-tessellation stable across conformational change (no popping between trajectory frames).

### Phase 5 — Temporal modifiers (3 weeks, parallelisable with 4)

Three modifiers applying orthogonally to any treatment. Requires a trajectory-aware render loop and a frame-accumulation buffer.

Delivers: `TM-01` … `TM-03`.

- **Exit:** any Phase 1–3 treatment can take any temporal modifier without treatment-specific code.

### Phase 6 — PyMOL subset (4 weeks, parallelisable)

Ports the 7 treatments expressible via `ray_texture`, `ray_trace_mode`, and CGO: `SS-01`, `SM-01`, `SM-03`, `SP-01`, `SP-02`, `SP-03`, `SP-04`. Ships as a standard PyMOL plugin with a settings-preset library plus a CGO generator module.

- **Exit:** all 7 render from a single `soft_matter.apply("radiolaria")` call; documented list of the 29 with no PyMOL path, and why.

### Phase 7 — Packaging, docs, distribution (3 weeks)

- npm package `molstar-soft-matter`, semver, typed exports
- MolViewSpec extension so treatment state survives share links
- Blender/MN export path (§4.6)
- Gallery site with A/B against stock CPK for all 36
- Contribution guide: adding a treatment in <300 LOC

**Total: 35–41 weeks serial. Critical path with Phases 5 and 6 parallelised against Phase 4: 28–34 weeks.**

---

## 6. Treatment catalogue

Effort scale: **S** ≤2 days · **M** 3–8 days · **L** 2–4 weeks · **XL** >1 month.
Every entry lists its data binding per **P3**; entries also note which principles (**P1**–**P5**) justify them.

### 6.1 Screen-space (G4) — Phase 1

#### SS-01 · Cel-shaded painterly
Flat quantized colour bands, confident ink outline, warm key + cool fill, no speculars. Atmospheric perspective so the far side recedes into blue-grey haze.
**Geometry:** none. **Material:** roughness 1, metalness 0. **Post:** outline + quantize pass + fog.
**Binding:** `DC-DEPTH` → haze density. **Principles:** P1 (partial — depth legibility).
**Engine:** Mol\* + PyMOL (`ray_trace_mode 3`). **Effort:** S. **Risk:** low.

#### SS-02 · Ukiyo-e woodblock
Sumi key block, flat colour areas, *bokashi* hand-wiped gradients, woodgrain in fills, deliberate 1–2px colour/key misregistration.
**Geometry:** none. **Post:** key-block extraction + per-plate offset + grain overlay.
**Binding:** `DC-CHAIN` → plate assignment. **Principles:** P2 (key block sits on the intersection curve).
**Engine:** Mol\* only. **Effort:** M. **Risk:** medium — misregistration must look intentional, not broken.

#### SS-03 · Duotone spot-ink
Two or three spot inks, halftone dots, paper showing through, multiply blending so overlaps produce a third colour.
**Geometry:** none. **Post:** custom halftone pass (needs the Phase 1 user-pass slot).
**Binding:** `DC-BFAC` → dot size. **Principles:** P3, P5 (ink density reads as uncertainty).
**Engine:** Mol\* only. **Effort:** M. **Risk:** low. **Note:** halftone dot size doubling as B-factor is one of the cleanest channel bindings in the catalogue.

#### SS-04 · Pointillism
Dot clouds instead of solids; perceived colour is the local optical average.
**Geometry:** none (uses `Points` primitive if density needs boosting). **Post:** stipple pass.
**Binding:** `DC-DEPTH` → dot density. **Principles:** P1, P3.
**Engine:** Mol\* only. **Effort:** S. **Risk:** low.

#### SS-05 · Flat modernist
No shading whatsoever. Flat circles, primary palette, **pattern fills** — stripes, checks, dots — carrying category instead of colour.
**Geometry:** none. **Post:** flat-fill + screen-space pattern mapping.
**Binding:** `DC-ELEM` → pattern; `DC-CHAIN` → colour. **Principles:** P3.
**Engine:** Mol\* only. **Effort:** S. **Risk:** low. **Note:** genuine accessibility win — patterns survive colour-vision deficiency where CPK does not. Should be promoted in docs as an a11y feature, not a novelty.

#### SS-06 · Cyanotype blueprint
White line on Prussian blue, dimension lines, hatching, title block with structure metadata.
**Geometry:** none. **Post:** invert + line extraction + paper texture; HTML overlay for the title block.
**Binding:** `DC-RES` → title-block resolution field. **Principles:** P5.
**Engine:** Mol\* only. **Effort:** M. **Risk:** low. **Note:** presenting a *predicted* model as an engineering drawing is a deliberate editorial statement; document it as such.

#### SS-07 · Vector display phosphor
No fills. Glowing green strokes with persistence decay. On a trajectory, motion leaves afterimage trails.
**Geometry:** `Lines` primitive only. **Post:** bloom + accumulation buffer.
**Binding:** `DC-FORCE` → trail length. **Principles:** P1, P4.
**Engine:** Mol\* only. **Effort:** M (S without persistence). **Risk:** low. **Note:** persistence is a preview of `TM-02`; build the accumulation buffer here and reuse it in Phase 5.

#### SS-08 · Semiconductor die shot
False-colour decap microscopy: aluminium grey-blue metal, tan polysilicon, regular repeating cell arrays. Beta sheets read as SRAM arrays.
**Geometry:** none. **Post:** oxide palette + orthographic lock + specular sheen.
**Binding:** `DC-ELEM` → layer material. **Principles:** P3.
**Engine:** Mol\* only. **Effort:** S. **Risk:** low. **Note:** the sheet/array pun survives scrutiny — both really are regular repeating arrays and the eye parses them identically.

### 6.2 Sphere-modifier (G2) — Phase 2

#### SM-01 · Felted wool
Speculars killed, ambient raised, fibrous silhouette, dyed-wool palette. The soft edge is a truer depiction of van der Waals falloff than a hard shell.
**Geometry:** sphere + radius jitter. **Material:** roughness 1, metalness 0, high bumpiness. Second 1.12× halo layer at alpha 0.2.
**Binding:** `DC-SASA` → fiber length. **Principles:** P3, P5.
**Engine:** Mol\* + PyMOL (`ray_texture 2`). **Effort:** S. **Risk:** low.

#### SM-02 · Claymation
Thumbprint displacement, waxy subsurface sheen, slightly squashed spheres. **The material is the easy half — the charm is temporal** (see `TM-01`).
**Geometry:** sphere + low-frequency displacement + anisotropic scale. **Material:** mid roughness, faint SSS approximation.
**Binding:** `DC-BFAC` → thumbprint depth. **Principles:** P3, P4.
**Engine:** Mol\* + Blender (true SSS). **Effort:** M. **Risk:** low.

#### SM-03 · Brutalist concrete
Board-formed texture with plank grain and tie-holes, monolithic grey masses, hard raking shadows.
**Geometry:** sphere + grain bump. **Material:** roughness 0.95, concrete palette. **Post:** hard single-source shadow.
**Binding:** `DC-CHAIN` → form-work plank direction. **Principles:** P3.
**Engine:** Mol\* + PyMOL (`ray_texture 3` crackle approximates). **Effort:** S. **Risk:** low. **Note:** with colour noise removed, domain massing reads instantly — funnier *and* more legible than CPK.

#### SM-04 · Pollen SEM
Exine sculpturing — spikes, reticulations, pores — mapped to element identity. Rendered as false-colour SEM: greyscale plus one tint.
**Geometry:** sphere + per-element displacement pattern. **Post:** SEM edge-glow, single tint.
**Binding:** `DC-ELEM` → sculpture type. **Principles:** P3.
**Engine:** Mol\* only. **Effort:** M. **Risk:** low. **Note:** borrowed credibility — it reads as a real micrograph.

#### SM-05 · Frost / rime
Crystalline accumulation along a directional vector; bare sphere on the lee side.
**Geometry:** sphere + directional crystal displacement. **Binding:** `DC-HBOND` → accretion direction. **Principles:** P3, P4.
**Engine:** Mol\* only. **Effort:** M. **Risk:** medium — direction field must be stable across frames or it shimmers. **Pairs with:** `TM-03`.

#### SM-06 · Amigurumi seams
Visible stitch spiral, closing seam per atom, running-stitch dashes along bonds. Per-atom radius jitter ±3–4% so nothing is machine-perfect.
**Geometry:** sphere + spiral bump; bonds as dashed cylinders. **Binding:** `DC-ELEM` → yarn colour; jitter seeded by index.
**Principles:** P2 (seam can be routed onto the intersection curve), P5.
**Engine:** Mol\* (approximation) + Blender (true stitches). **Effort:** M. **Risk:** low. **Note:** the imperfection sells "handmade" more than any texture does.

#### SM-07 · Soap film
Thin-film interference: colour varies with view angle and curvature. Transparency solves occlusion; film thickness maps onto potential.
**Geometry:** sphere shell, alpha. **Material:** custom interference shader. **Post:** `dpoit`.
**Binding:** `DC-ESP` → film thickness → hue. **Principles:** P1, P3.
**Engine:** Mol\* only. **Effort:** L. **Risk:** medium — needs a real custom shader and correct depth-sorted transparency. **Note:** the only treatment here where the iridescence is *physically* derived rather than stylised.

### 6.3 Sphere-substitute (G1) — Phase 3

#### SP-01 · Radiolaria ★ priority
Silica lattices of the kind found in radiolarian skeletons. Each sphere becomes a porous geodesic mineral shell — you can see straight through the molecule.
**Geometry:** instanced geodesic shell, subdivision level and porosity parameterised. One template buffer, N instances.
**Binding:** `DC-SASA` → porosity. **Principles:** P1 (the strongest occlusion fix in the catalogue), P3.
**Engine:** Mol\* + PyMOL (CGO). **Effort:** L. **Risk:** medium — Z-fighting where shells interpenetrate; mitigate with per-instance rotation.
**Why priority:** biggest legibility win, tractable as a `Mesh` visual with no shader work, and porosity dials continuously from solid sphere to lace so it degrades gracefully.

#### SP-02 · Voxel / LED matrix ★ priority
Quantise the vdW volume onto a discrete emissive grid.
**Geometry:** instanced cubes on a lattice; occupancy from vdW union. **Material:** emissive.
**Binding:** `DC-RES` → **voxel edge length**. **Principles:** P5 (the strongest example in the catalogue), P1 (partial — grid gaps admit light).
**Engine:** Mol\* + PyMOL (CGO). **Effort:** M. **Risk:** low.
**Why priority:** a 3.5 Å map genuinely does not know atom positions better than a chunky voxel. Binding voxel size to actual coordinate uncertainty makes this *epistemically better* than a glossy sphere, not merely different. Cheapest of the three priority treatments — consider pulling it forward into Phase 2.

#### SP-03 · Pom-pom fiber
No sphere at all. Each atom is a burst of short radial fibers; the surface emerges as fuzzy volume. Overlapping atoms tangle instead of creasing.
**Geometry:** N `Cylinders` per atom along scattered sphere normals. **Binding:** `DC-SASA` → fiber count and length.
**Principles:** P1, P2 (no intersection curve exists to be ugly), P3, P5.
**Engine:** Mol\* + PyMOL (CGO `SAUSAGE`). **Effort:** M. **Risk:** low — fiber count is the LOD dial.
**Note:** satisfies four of five principles, more than anything else here. Whimsy and science point the same way: a soft statistical boundary drawn as a soft thing.

#### SP-04 · Wound yarn
Helical strand arcs wrapping each sphere; bonds are the strand running between atoms, so a chain is one continuous thread.
**Geometry:** `Cylinders` along helical paths per atom + inter-atom strand. **Binding:** `DC-CHAIN` → strand colour.
**Principles:** P2 (the strand crosses the seam), P3.
**Engine:** Mol\* + PyMOL (CGO). **Effort:** M. **Risk:** low. **Note:** unbroken-thread backbone is a genuinely useful mental model of chain connectivity.

#### SP-05 · Sea urchin test
Tubercles with movable spines whose orientation encodes a vector.
**Geometry:** sphere + instanced spines. **Binding:** `DC-FORCE` or `DC-HBOND` → spine orientation.
**Principles:** P3 (invents a vector channel spacefill currently lacks).
**Engine:** Mol\* only. **Effort:** M. **Risk:** low.

#### SP-06 · Vacuum tube
Atoms as glowing glass envelopes with internal electrode structure and warm cathode glow.
**Geometry:** instanced glass envelope + internal elements. **Material:** transmissive + emissive. **Post:** bloom.
**Binding:** `DC-BFAC` → glow intensity. **Principles:** P1, P3.
**Engine:** Mol\* only. **Effort:** L. **Risk:** medium — transparency sorting. **Note:** spectacular in dark mode; make it the dark-mode default demo.

#### SP-07 · PCB ★ priority
Solder-mask green, ENIG gold pads, white silkscreen labels, copper traces routed at 45°, vias where a bond crosses a plane, card-edge connector at domain boundaries.
**Geometry:** instanced pad/via meshes + routed trace `Cylinders`; `Text` primitive for silkscreen.
**Binding:** `DC-ELEM` → pad type; `DC-CHAIN` → copper layer; `DC-RESID` → silkscreen label.
**Principles:** P2 (traces route along the seam), P3 (three simultaneous channels — the richest in the catalogue).
**Engine:** Mol\* only. **Effort:** L. **Risk:** medium — 45° trace routing is a real autorouting problem; use a simple greedy router and accept imperfect results.
**Why priority:** imports an entire mature information-design grammar. PCBs are *built to be read at a glance*; the visual language already encodes hierarchy, connectivity, and layer separation. Expect people to laugh, then realise they parse it faster than CPK.

#### SP-08 · Breadboard
Atoms seated in a perfboard grid, jumper wires for bonds.
**Geometry:** instanced grid + wire splines. **Binding:** `DC-PLDDT` → wire tidiness (confident regions neatly routed, uncertain regions a rat's nest).
**Principles:** P5, P3. **Engine:** Mol\* only. **Effort:** M. **Risk:** low. **Note:** says "provisional" out loud.

#### SP-09 · Gaze
Each atom is a shell bearing a single recessed aperture with an iris inside it. The iris is offset along a bound vector, and because it sits in a recess, occlusion does the reading — you perceive direction from how much of the iris the rim hides, which is the same mechanism by which real eyes read as looking somewhere. An `expression` parameter dials continuously from a purely geometric aperture (publication mode) to overt anthropomorphism (teaching mode), so one treatment serves both audiences without a fork.
**Geometry:** instanced shell + aperture ring + offset iris disc. **Material:** matte shell, darker iris.
**Bindings:** `DC-HBOND` or `DC-FORCE` → iris offset direction; `DC-OCC` → aperture diameter; `DC-BFAC` → rim softness.
**Principles:** P3 (three simultaneous channels), P1 (partial — apertures admit light into the interior).
**Engine:** Mol\* only. **Effort:** M. **Risk:** medium — originality risk, not technical risk.
**Design constraint:** the gaze vector points at **data, never at the camera**. Camera-tracking is what makes an atom read as a cartoon creature from an existing work; data-tracking is what makes it a glyph. This is simultaneously the originality argument and the functional one, and it is non-negotiable at review.
**Why it earns a slot:** spacefill has no way to express a per-atom vector. Arrow glyphs are the incumbent and they self-occlude badly at density. A face pointing somewhere is read pre-attentively and needs no legend. Subject to the §8 source-recall check before any asset ships.

### 6.4 Surface-derived (G3) — Phase 4

All nine require an SES/SAS pass, and all nine are Mol\*-only — PyMOL has no surface-retessellation path and no custom shader path. Shared prerequisite work: surface generation, seam-curve extraction, region growing, and stable re-tessellation across conformational change. Budget ~2 weeks of that before the first treatment lands.

#### SD-01 · Byzantine mosaic
Surface discretised into flat tesserae with grout gaps, each tile normal jittered a few degrees so the surface shimmers under a moving light. Gold leaf for the ligand.
**Geometry:** low-poly surface, flat shading, inset faces. **Bindings:** `DC-DEPTH` → tile size; `DC-CONS` → gold-leaf tesserae on conserved residues.
**Principles:** P2 (grout occupies the seam), P3. **Engine:** Mol\* only. **Effort:** M. **Risk:** low.
**Note:** cheapest G3 treatment — it is a low-poly surface with flat shading and face insets. Build it first as the pathfinder.

#### SD-02 · Stained glass / cloisonné
Spacefill's worst artifact — the intersection curve — becomes the lead came. Backlit rather than front-lit, so light passes *through* the molecule.
**Geometry:** surface partitioned at seam curves; came extruded along them. **Material:** transmissive glass.
**Binding:** `DC-ESP` → glass tint. **Principles:** P1, P2 (the canonical example — flaw becomes feature), P3.
**Engine:** Mol\* only. **Effort:** L. **Risk:** medium-high — robust seam extraction is the crux. **Note:** the single clearest demonstration of P2; feature it in the paper.

#### SD-03 · Lichen and moss
Crustose growth on solvent-exposed surface, bare stone in buried regions. **Growth density is SASA** — the whimsy carries the data for free.
**Geometry:** surface + region-grown instanced growth clusters. **Binding:** `DC-SASA` → growth density.
**Principles:** P3 (the cleanest binding in the catalogue). **Engine:** Mol\* only. **Effort:** L. **Risk:** medium. **Pairs with:** `TM-03`.

#### SD-04 · Geode
Clip the structure open; line the cavity with crystal terminations. Binding pockets become treasure chambers.
**Geometry:** clipped surface + instanced crystals on cavity walls. **Binding:** `DC-DEPTH` → crystal size.
**Principles:** P1, P3. **Engine:** Mol\* only. **Effort:** L. **Risk:** medium — cavity detection. **Note:** genuinely arresting for an active-site figure, not merely cute.

#### SD-05 · Muqarnas
Girih tiling projected onto convex regions; stalactite vaulting in concave regions. Binding pockets get muqarnas.
**Geometry:** curvature-classified surface + tiling. **Binding:** `DC-CURV` → tile class.
**Principles:** P1, P2. **Engine:** Mol\* only. **Effort:** XL. **Risk:** high — girih projection onto arbitrary curvature is hard.
**Note:** conceptually perfect (muqarnas exist precisely to articulate a concavity) but the most expensive item here. **Candidate for cut if Phase 4 overruns.**

#### SD-06 · Timber lattice
Interlocking wooden members forming a permeable shell. Warm, handmade, see-through.
**Geometry:** surface → member graph → instanced timber. **Binding:** `DC-DEPTH` → member density.
**Principles:** P1, P3. **Engine:** Mol\* only. **Effort:** L. **Risk:** medium.

#### SD-07 · Gothic ribbed vaulting
Clip open and the interior is a cathedral: ribs along the backbone, bosses at disulfides, helices as clustered columns.
**Geometry:** clipped interior + backbone-following ribs. **Binding:** `DC-SS` → member type; disulfides → bosses.
**Principles:** P1, P3. **Engine:** Mol\* only. **Effort:** L. **Risk:** medium. **Note:** the metaphor is almost too available; nobody has built it, which is reason enough.

#### SD-08 · Scaffolding and tarp ★ high value
The structure mid-construction: pipe scaffolding, safety netting, and a tarp thrown over regions the model is unsure about.
**Geometry:** surface → scaffold frame; tarp mesh over low-confidence regions. **Binding:** `DC-PLDDT` → tarp coverage (threshold ~70).
**Principles:** P5 (definitive example), P3. **Engine:** Mol\* only. **Effort:** L. **Risk:** medium.
**Note:** delivered as a joke, it is also the clearest pLDDT visualisation anyone has made. Everyone understands "under the tarp" instantly, with no legend. Strong candidate for promotion out of Phase 4 if an early win is needed.

#### SD-09 · Laser-cut contour model
Stacked plywood contour layers through the density map, like an architect's topographic site study.
**Geometry:** planar slices through SES or map. **Binding:** `DC-RES` → slice thickness.
**Principles:** P5, P3. **Engine:** Mol\* only. **Effort:** M. **Risk:** low. **Note:** physically fabricable — export slices as DXF and the figure becomes an object. Cheap, distinctive, low-risk; consider pulling forward.

### 6.5 Temporal modifiers (T) — Phase 5

Orthogonal layers applying to any treatment from §6.1–6.4 without treatment-specific code. Per **P4**, this is the least-explored axis in molecular graphics and the cheapest novelty available.

#### TM-01 · Stop-motion boil
Per-frame normal-map reseeding and a two-frame hold, rendering an MD trajectory at an effective 12 fps.
**Mechanism:** reseed treatment noise every 2 frames; hold camera and geometry between.
**Applies to:** all G2, most G1. **Engine:** Mol\* + Blender. **Effort:** S. **Risk:** low.
**Note:** the highest charm-per-line-of-code item in the entire plan. The appeal of hand-animated stop motion is frame-to-frame boil, not clay texture — and no molecular viewer has ever exploited temporal aesthetics.

#### TM-02 · Phosphor persistence
Accumulation buffer with exponential decay; motion leaves afterimage trails.
**Mechanism:** reuse the accumulation buffer built in `SS-07`. **Binding:** decay constant ↔ timescale of interest.
**Applies to:** all emissive and stroke treatments. **Engine:** Mol\* only. **Effort:** S (given SS-07). **Risk:** low.
**Note:** trails are a genuine new data encoding — you *see* per-atom mobility as smear, not as a colour ramp.

#### TM-03 · Accretion
Frost, lichen, or rust growing across a trajectory; accumulated state persists between frames.
**Mechanism:** per-atom accumulator updated from a channel each frame. **Binding:** `DC-SASA` or `DC-FORCE` → accretion rate.
**Applies to:** `SM-05`, `SD-03`, and any growth treatment. **Engine:** Mol\* only. **Effort:** M. **Risk:** medium — state must survive camera changes and reset cleanly on trajectory seek.

---

## 7. Performance budget

Measured on the Phase 0 benchmark harness, mid-range discrete GPU, 1440p.

| Structure class | Atoms | Target fps | Notes |
|---|---|---|---|
| Small protein (e.g. crambin, lysozyme) | <3k | 120 | all treatments, full detail |
| Typical protein + ligand | 3k–20k | 60 | all treatments, full detail |
| Large complex | 20k–50k | 60 | G3 may drop one detail level |
| Viral capsid / ribosome | 50k–200k | 30 | LOD active on G1/G3 |
| Mesoscale | >200k | 30 | forced fallback to plain spacefill + palette |

Rules:

- Every treatment declares `lodFallback` and a measured atom-count threshold. No guessed thresholds.
- **Colour semantics survive LOD.** When geometry is dropped, the palette and channel binding stay, so a figure never silently loses its data encoding.
- Instanced template meshes only for G1. A per-atom unique mesh is an automatic reject at review.
- Memory ceiling: 1.5 GB GPU for the 200k case.
- G3 surface passes are cached per conformation and invalidated only on coordinate change, not on camera or parameter change.

---

## 8. Validation and testing

**Visual regression.** Every treatment has locked reference renders across a fixed structure set (1CRN, 1UBQ, 4HHB, 6VXX, an AlphaFold model with mixed pLDDT, a 3.5 Å cryo-EM model). CI diffs each PR.

**Channel correctness.** For each binding, an automated check that the visual parameter is monotonic in the channel value. A treatment claiming "density = SASA" must demonstrate it, not assert it.

**Picking integrity.** Automated test that every treatment returns the correct element index on click. Non-negotiable.

**Determinism.** Same structure + same seed ⇒ byte-identical render. Catches RNG leaks and symmetry-mate mismatches.

**Perceptual study (Phase 7).** Small user study, n ≈ 30, comparing task performance against stock CPK on four tasks: locating a binding pocket, judging model confidence, tracing chain connectivity, and reading a per-atom vector field. Hypotheses worth testing: `SP-01` Radiolaria beats CPK on pocket location; `SD-08` Scaffolding beats the standard pLDDT colour ramp on confidence judgement; `SP-04` Wound yarn beats CPK on connectivity tracing; `SP-09` Gaze beats arrow glyphs on vector reading at high density, where arrows self-occlude.

**Naming and IP review.** A required gate at treatment triage and again before the gallery ships. Checks the treatment name, its description, and every shipped asset against §1's intellectual-property constraint. For any treatment with a character, the gate includes a source-recall check: show the design cold to reviewers who have not seen the proposal and ask what it reminds them of. A named source is a fail and a redesign, not a tweak. A treatment that only reads well when named after a protected work fails the gate — if the technique cannot be described on its own terms, it is borrowing rather than building.

**Accessibility.** `SS-05` Flat modernist validated under all three common colour-vision deficiency simulations. Every palette carries a documented CVD-safe variant.

---

## 9. Packaging and distribution

- **`molstar-soft-matter`** — npm package, semver, typed exports, tree-shakeable per treatment so a consumer importing one treatment does not ship all 36.
- **MolViewSpec extension** — treatment state serialised into shared views so a link reproduces the exact figure. Requires an MVS custom-node proposal; open that conversation early, in Phase 1, since it is an upstream dependency.
- **PyMOL plugin** — `soft_matter` package with a preset library plus a CGO generator module. Distributed as a wheel.
- **Blender importer** — Python addon consuming the treatment manifest and reconstructing it as a Molecular Nodes node group.
- **Gallery site** — all 36 with A/B toggle against stock CPK, live in-browser, plus a downloadable preset file per treatment.
- **Contribution guide** — the <300 LOC target from G1, with `SD-01` documented as the worked example.

---

## 10. Risks and mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| G3 surface re-tessellation pops between trajectory frames | High | Medium | Prototype stability in the Phase 4 prerequisite work; if unsolved, restrict G3 to static structures and document it |
| Seam-curve extraction unreliable (`SD-02`) | High | Medium | Fall back to screen-space edge detection for the came; visually close, topologically wrong, acceptable |
| Muqarnas projection intractable | Medium | High | Pre-agreed cut. `SD-05` is the designated scope-relief valve |
| MVS custom nodes rejected upstream | Medium | Medium | Fall back to a side-channel URL parameter; degrade to plain spacefill for consumers without the extension |
| Treatments read as gimmicks, damaging credibility | High | Medium | Lead every publication and talk with `SP-02` Voxel and `SD-08` Scaffolding — the two epistemic-honesty arguments. Whimsy is the hook, P5 is the thesis |
| Performance collapse on mesoscale | Medium | Low | LOD fallback is mandatory per §7 and enforced at review |
| Scope creep into cartoon/ribbon representations | Medium | High | Explicit non-goal in §1; reject at triage |

---

## 11. Open questions

1. **Should `SP-02` Voxel move into Phase 2?** It is the cheapest priority treatment and the strongest thesis piece. Argument for: earliest possible credibility anchor. Argument against: it needs the vdW-union occupancy pass, which is Phase 3 infrastructure.
2. **Is `DC-ESP` worth the dependency?** Only `SM-07` and `SD-02` consume it. A Coulombic approximation may be adequate; full APBS import may not be worth the integration cost.
3. **Does the PyMOL subset justify Phase 6 at all?** The engine audit puts real coverage at 7 of 36 — PyMOL has no custom shader path, so every screen-space treatment beyond `SS-01` and every surface-derived treatment is out of reach. Four weeks for 19% coverage is a weak trade. Recommend deferring Phase 6 until there is demand signal from the gallery, and reallocating the time to Phase 4, which is the highest-risk phase.
4. **Licensing.** Mol\* is MIT, which makes the extension straightforward. The Blender path pulls in GPL. Keep the manifest format and importer in separate packages so the core stays MIT.
5. **Naming.** "Soft Matter" is a physics term with an existing meaning. Cute, but potentially confusing in a structural-biology context. Alternatives worth considering before the package name is locked.

---

## 12. Appendix — repository layout

```
soft-matter/
├── packages/
│   ├── core/                    # manifest schema, validation, palettes
│   │   ├── manifest.schema.json
│   │   ├── palettes/
│   │   └── channels/            # DC-* providers
│   ├── molstar/
│   │   ├── screen-space/        # SS-01 … SS-08
│   │   ├── sphere-modifier/     # SM-01 … SM-07
│   │   ├── sphere-substitute/   # SP-01 … SP-09
│   │   ├── surface-derived/     # SD-01 … SD-09
│   │   ├── temporal/            # TM-01 … TM-03
│   │   ├── post/                # user post-processing pass slot
│   │   └── themes/              # colour + size theme providers
│   ├── pymol/
│   │   ├── presets/             # ray_texture / ray_trace_mode settings
│   │   └── cgo/                 # geometry generators
│   └── blender/                 # manifest -> Molecular Nodes importer
├── bench/                       # perf harness + fixed structure set
├── test/
│   ├── visual/                  # locked reference renders
│   ├── channels/                # monotonicity checks
│   └── picking/                 # element-index integrity
└── gallery/                     # A/B site
```

### Manifest example

```json
{
  "id": "SP-01",
  "name": "Radiolaria",
  "strategy": "G1",
  "geometry": {
    "kind": "instanced-mesh",
    "template": "geodesic-shell",
    "params": { "subdivision": 2, "porosity": 0.4, "strutRadius": 0.08 }
  },
  "material": { "roughness": 0.6, "metalness": 0.0, "bumpiness": 0.1 },
  "post": ["occlusion", "outline"],
  "bindings": [
    { "channel": "DC-SASA", "target": "geometry.params.porosity",
      "map": "continuous", "domain": [0, 1], "range": [0.1, 0.75] }
  ],
  "palette": { "ref": "mineral", "mode": "categorical", "by": "DC-ELEM" },
  "lodFallback": { "threshold": 60000, "to": "spacefill", "keepPalette": true },
  "principles": ["P1", "P3"],
  "engines": ["molstar", "pymol"]
}
```
