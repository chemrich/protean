# An adversarial review of the soft-matter plan

**Six agents against `docs/soft-matter-plan.md`, 2026-08-22.**

The plan proposes 36 alternative space-filling treatments over 28–41 weeks.
Before committing to Track B, six independent reviews were run against it —
scope, scientific validity, legibility, IP and naming, engineering estimates,
and fit with protean's architecture. Each was told to attack, and each was
required to measure rather than assert.

The most important thing they found was not in the plan.

## The bake-off's evidence was void

`docs/bakeoff.md` concluded that binding a channel does not make it legible,
on a ratio of "two of three bound a channel and one could be read". That ratio
does not exist.

**1MBN's B-factor column is `0.00` on all 1,216 `ATOM` records** — one distinct
value, verified directly from the cached mmCIF. All three treatments bound
B-factor:

| Treatment | Binding as coded | Value on 1MBN |
|---|---|---|
| Felt | `amp = 0.04 + min(1, b/60) * 0.14` | constant `0.04` |
| Radiolaria | `bRange` returns `[0,1]` when `hi > lo` is false, so `t = 0` | constant porosity |
| Duotone | dot area from tone, tone from shading | never read a B-factor |

So no channel varied in any of the three pictures. The bake-off's cost
measurements, its four silent-success bugs, and its account of what each
treatment *looks* like are unaffected — none of them depend on the data. Every
claim about whether a channel could be **read** is void, including the
document's central one.

This is the fourth instance this month of the same failure: measure a part,
never check it against the container. The bake-off documented that failure four
times and then committed it in its own verdict.

Corrected in place; `docs/bakeoff.md` now carries the retraction.

Two of its numbers were also wrong. `Cylinders` is a quad impostor, not a
GPU-instanced primitive: `add()` pushes six vertices per strut with start, end
and scale duplicated (`cylinders-builder.js:26-33`). At 360 bytes per strut
against mesh geometry's 408, it saves **12%, not an order of magnitude**, and
moves the memory ceiling from ~122k atoms to ~139k. The per-cylinder `aScale`
claim is correct, so per-atom porosity does survive that route.

## A live hazard in shipped code

`docs/views.md:386-390` says to leave a `plddt` view alone until backlog 33 and
34 are fixed, because protean could not load a predicted model. **Those were
fixed on 2026-08-21.** The blocker is gone, and the hazard the reasoning was
protecting against is now reachable.

pLDDT and B-factor are the same mmCIF column, `B_iso_or_equiv`, with opposite
polarity: high B means *less* certain, high pLDDT means *more* confident.
Mol\*'s `uncertainty` theme assumes the crystallographic reading over a fixed
`[0, 100]` domain (`server.py:310`). So on an AlphaFold model, `size(
"uncertainty")` draws the most trustworthy regions fattest and `color(
"uncertainty")` ramps them backwards — silently, with a confident-looking
picture. No guard exists; `plddt` appears nowhere in `src/protean_mcp/`.

Filed as backlog 41.

## What does not survive in the plan

**P3 is unsatisfiable and already gamed.** It requires at least one data
binding per treatment, "no exceptions", enforced by a schema check that a
`channel` field is non-empty. An existence rule produces token compliance:
`TM-01` has no `Binding:` line at all, `TM-02` binds a decay constant (a render
setting), `SS-06` binds a text field in an HTML overlay, and `SM-06` binds an
array index, which carries zero information. `TM-02` would also fail the §12
JSON schema, so §6 and §12 already contradict — evidence that the validator has
never been run against the catalogue.

**P5 is false as written.** The instinct — materials honest about uncertainty —
is protean's own and worth keeping. The literal claim is not, and there is no
visual variable reserved for uncertainty, so honesty and data bindings compete
for the same pixels. `SS-01` binds haze to both camera depth and burial depth;
`SM-01` binds fuzz to both van der Waals falloff and SASA.

**§4.4 and §7 cannot both hold.** Mol\* instances are symmetry operators, not
atoms: `createUnitsTransform` writes one `Mat4` per unit, and per-instance data
is a 4×4 matrix. Per-atom geometry in a `UnitsMeshVisual` is therefore
necessarily unique mesh, which §7 calls "an automatic reject at review". That
rejects `SP-01`, `SP-03`, `SP-08` and `SP-09` — including the flagship. There
is an escape the plan never mentions: `Shape` accepts arbitrary per-instance
transforms, so one `Mat4` per atom works. But it is a `ShapeRepresentation`,
so picking yields `ShapeGroup.Loci` and protean's `select`, `color` and `focus`
stop working. That is an unbudgeted architectural fork, not a config change.

**§7's budget is unreachable.** The plan's own §12 manifest example, at
`detail: 2`, needs **9.79 GB at 50k atoms** against its stated 1.5 GB ceiling —
6.5× over, and 26× at 200k. Even the bake-off's coarsest configuration uses 41%
of the budget at 50k. Triangle throughput is worse than memory: at detail 0 and
50k atoms the declared post-processing needs roughly 1.6 G tri/s, at or past a
mid-range discrete GPU's peak.

**§5 is under by about 1.9×.** Summing the plan's *own* S/M/L/XL tags per phase
gives 49–92 weeks against a stated 35–41. Phase 3's most optimistic sum (9.6
weeks) already exceeds its stated upper bound (8). Phase 4's optimistic sum is
roughly double its upper bound. Those tags are implementation only; §8's
per-treatment test and review requirements add another 30–50%.

**§4.3's one-week post-processing pass slot is unbounded.** `Passes` is
constructed inside `Canvas3D.create` with no injection point, and
`PostprocessingPass.render` is a fixed sequence composited by one
`#define`-driven fragment shader. There is no hook. The options are an upstream
PR, a fork, or wrapping the final framebuffer — which loses the depth buffer
that `SS-01`, `SS-04` and `SS-06` bind to. Five of eight Phase 1 treatments
block on this, and it appears in neither §5 nor §10.

**"One full-time graphics engineer"** understates the shape of the work: GLSL
authoring, computational geometry, a PyMOL plugin, a Blender addon, an upstream
MolViewSpec proposal, CI infrastructure, a gallery site, and a human-subjects
perceptual study.

### Channels

| Channel | Finding |
|---|---|
| `DC-RES` | Resolution is not coordinate uncertainty, and it is one number per structure, not per atom. Bound to voxel edge length it yields a uniform grid with nothing to read. Real substitutes exist: Cruickshank DPI, deposited ESUs, local-resolution maps. |
| `DC-PLDDT` | Listed as free; not built. Same column as `DC-BFAC` with inverted polarity — see above. |
| `DC-FORCE` | Unobtainable. Trajectories store coordinates only, and without superposition every vector points along rigid-body tumbling. |
| `DC-HBOND` | Not a per-atom quantity. Null on the ~65% of heavy atoms that are carbon, multi-valued on waters, and no treatment specifies a null state. |
| `DC-CURV` | Not "free with surface". Marching cubes emits positions, normals and group ids; curvature is a separate estimation pass, noisy on 0.5 Å output. 3–5 days plus tuning. |
| `DC-OCC` | Constant on every reference structure in §8 — measured 100% occupancy 1.00 on 1CRN, 4HHB, 6VXX, 1MBN and the AlphaFold model, 95.3% on 1UBQ. |
| `DC-SASA` | The §12 example declares `"domain": [0, 1]` on a quantity in Å², so every atom above 1 Å² pins to maximum. That binding is dead in the plan's own showcase snippet. |
| `DC-PAE` | Missing entirely, and it is the channel that would carry what pLDDT cannot. |

### Legibility

Of 36 treatments: **10 legible, 11 marginal, 15 illegible.** Four of the ten
are legible only because their binding is colour-by-element or colour-by-chain,
which stock CPK already does. **Treatments that carry something the incumbent
cannot, and that a viewer can read: 6 of 36.**

Four failure modes generate most of the 15, and naming them kills clusters:

1. **Same-frequency competition.** A per-atom channel modulating a texture that
   already varies at atom scale. This is the felt failure. `docs/views.md` §5.10
   measures bump at 0.036 / 0.018 / 0.004 of the frame at frequency 1 / 3 / 6,
   against a suite detection floor of 0.008–0.01 — a fine texture parameter is
   below the pixel-diff threshold before a human eye is involved.
2. **`DC-DEPTH` is ~0 on anything opaque.** The atoms you can see in an opaque
   render are by construction the ones at depth ≈ 0. Five treatments bind it;
   only `SD-04` escapes, and only because it clips the structure open. §3.3 also
   conflates camera Z, burial depth and cleft depth under one name.
3. **Global properties posing as per-atom channels.** `DC-RES` and anything
   derived from a file header produce a uniform picture within any one render.
4. **The channel is already visible in the geometry.** `DC-SASA`, `DC-DEPTH`,
   `DC-CURV` and `DC-CHAIN` are all recoverable by eye. `SD-03`, praised in §6.4
   as "the cleanest binding in the catalogue", draws lichen density from SASA
   *on the solvent-accessible surface*, where accessibility is the defining
   property of every point.

### Naming and IP

**`SM-02` "Claymation" was a live registered trademark** — Will Vinton, 1981,
now Laika — used three pages after §1 rule 3 forbids exactly that. The naming
gate was never run on its own document. Renamed to **`SM-02` Clay**; "clay" and
"clay animation" are generic. "Stop motion" was avoided because `TM-01` already
uses it.

**`SP-09` "Gaze"** lands on Minions, Mike Wazowski and the D&D beholder,
despite the plan diagnosing that convergence trap elsewhere. It also fails on
legibility and on §7's memory rule, making it the only treatment to fail three
independent gates — and the one the plan spends the most words defending.

Two gate categories are missing: **patents**, which cover technique rather than
name, and **asset provenance** — Fritzing is CC-BY-SA, Shadertoy shaders are
frequently non-commercial or share-alike, the Pantone database is licensed, and
AlphaFold assets are CC-BY.

Two framings should go. `SM-04`'s note that it earns "borrowed credibility — it
reads as a real micrograph" is the exact inverse of P5 and a figure-fabrication
hazard. And §6 places drug molecules inside a mosque vault via muqarnas.

### Architecture

The plan is written for a library with three engine targets — Mol\*, PyMOL,
Blender. protean is none of them and is not mentioned once in 716 lines.

Its central artifact, a JSON manifest validated at load, is an interface for a
consumer protean does not have. `docs/views.md:92-98` records the opposite
stance and the reason: a model calling protean never sees the manifest schema,
only the tool schema, so a manifest arrives as one blob composed from memory
with errors reported *after* the call. protean's discipline is refusal
*before* — `show()`, `preset()` and `define_elements()` each reject an unknown
value and name the valid ones. The manifest survives in exactly one place, and
it is a good one: §4.6's Blender export, where the consumer is a Python importer
in another process with no schema to show anyone.

The five manifest fields already have five separate homes on purpose:
`geometry` → `show(representation=)`, `material` → `material()`, `post` →
`effects()` / `shading()` / `lighting()`, `bindings` → `define_field` /
`define_elements` / `define_atom_classes` then `color()` / `size()`, `palette`
→ `define_elements()`.

**Roughly a third of the cheap tier is already shipped under other names.** Of
the eight G4 treatments: three are effectively built (`SS-01` is `illustrative`,
`textbook` and `richardson`; `SS-05`'s flat half is `shading(style="flat")`;
`SS-08` is `define_elements` plus a metallic rig), one is prototyped and already
reclassified as a finish (`SS-03`), and the three unbuilt ones are image-space
operations that belong on `snapshot(finish=)` rather than in a render pass. The
week §4.3 budgets for a pass slot buys a live preview and nothing else — the
trade `docs/views.md:775-786` already reasoned through and declined.

Two small protean gaps close most of the rest: **fog** and **camera
projection** are stock Canvas3D parameters that the bridge does not expose, and
between them they are most of what `SS-01` and `SS-08` are missing.

**The deepest mismatch:** the plan puts data channels inside the renderer;
protean puts them outside it, behind a per-call WebSocket, keyed by residue.
protean has per-residue scalars and per-atom *categories*. It has no per-atom
scalar channel and no vector channel of any kind. Of the plan's fifteen
channels, five are per-atom scalars and two are per-atom vectors. So the
bindings for `SM-05`, `SP-05`, `SP-09` and `SS-07` have **no route into the
viewer at all**, and P3 is unsatisfiable in protean for a substantial fraction
of the catalogue until a fourth registration mechanism exists. Nobody has
costed it. The residue key is deliberate — `server.py:1000-1005` explains that
a biological assembly holds symmetry copies the analysis array does not, so
index alignment would be silently wrong on exactly the structures where it
matters.

### Temporal

`TM-01`'s per-frame reseed collides with a recorded decision: protean's `jitter`
size theme takes no parameters, and the absence of a seed is deliberate
(`dispatch.ts:648-660`) because an RNG gives each symmetry copy of an atom a
different radius, which reads as a broken structure. Re-registering a theme per
frame would also run `claimName` → `forgetField` and pull a provider out from
under a live component — a bug `dispatch.ts:2462-2468` records already hitting
once.

`TM-02` needs no render loop at all here: exponential decay over captured
frames is arithmetically identical to an accumulation buffer at a fixed camera,
so it is a Pillow job. Its promise of trails *while you rotate* does not hold.

`TM-03` genuinely does not fit — it needs a per-atom accumulator updated in
place, and `define_field` is per-residue and refuses two entries for one
residue rather than merging.

Also: §7 states its budget in interactive fps. protean measures no fps
anywhere, and `docs/benchmark.md:11-14` says why — "timing measures an
architecture choice nobody is deciding between."

## What survives

- **The G4/G2/G1/G3 cost tiering.** The plan's most load-bearing structural
  claim, and it held up under measurement.
- **§4.1's two hard constraints** — `builder.currentGroup` must carry the loop
  index, and geometry must be built per `Unit` so assemblies come free. protean
  needs both stated: a mis-mapped group breaks `select`, `color`, `measure` and
  `label` simultaneously while looking plausible.
- **§4.4's "colour semantics survive LOD"** — protean's silent-success
  discipline applied to a new failure.
- **P5's instinct**, if not its wording. `putty`, `felt`'s docstring and
  `sasa()`'s null-not-zero handling are already this argument.
- **`SD-08` Scaffolding** — the best idea in the catalogue. It refuses to show
  geometry the model does not support, which is information-hiding rather than
  decoration. It is two levels, not a ramp, and it is null on every experimental
  structure.
- **`SP-01` Radiolaria** — a real P1 win protean cannot currently deliver, with
  the caveat that per-atom porosity and a single instanced template cannot both
  hold.
- **§1's IP constraint and §8's naming gate** — better specified than anything
  protean has. They need running, including on the plan itself.

## The shuffle test

The one new tool worth taking. Render twice: once with the true channel, once
with the channel's values **permuted across atoms**. Same marginal
distribution, same palette, same everything. If the two frames differ by less
than the visual-regression threshold, the binding carries nothing.

It is one function, it reuses the differential harness that already exists, and
it would have caught felt immediately. §8's proposed monotonicity check would
not: the visual parameter *is* a one-line monotone function of the channel, so
that check verifies a lambda and passes by construction for every treatment.
The only binding in the catalogue it could fail is `SM-07`'s cyclic
interference hue.

Two companions:

- **Level counting**, replacing the monotonicity assertion. Render at n
  quantiles, band-pass to the frequency an observer integrates at the declared
  pixels-per-atom, and count adjacent levels separated by more than threshold.
  Put that number in the manifest so the claim is falsifiable.
- **A degenerate-input gate.** Reject a binding whose channel has zero variance
  on a structure. On the plan's own six files that fires immediately, and the
  correct behaviour is a loud fallback rather than a picture that renders
  successfully and says nothing.

## What §8 should not do

- **216 locked reference renders** (36 × 6). The differential job is already
  ~80% capture time at 91.
- **Golden images and byte-identical renders.** Not achievable across the
  GPU/software-rendering boundary the free runners straddle, which is why this
  suite is differential-with-a-measured-threshold in the first place.
- **The n ≈ 30 perceptual study.** Between-subjects at that size detects only
  d ≈ 1.02, and four uncorrected comparisons give a family-wise error rate of
  about 18.5%.

One test already in the repo needs a decision before any of this scales:
`test_every_view_looks_different_from_every_other` is O(n²) with n = 10 today,
and the closest measured pair sits 2.2× above the threshold. Nobody has checked
whether 36 treatments produce 36 distinguishable images — and several plainly
would not.

## Where this leaves Track B

The honest catalogue is **6 to 12 treatments**, not 36, and the plan's
strongest ideas (`SD-08`'s refusal, `SP-01`'s see-inside, P5's instinct) are
compatible with protean while its central machinery (the manifest, the npm
package, the PyMOL plugin, the fps budget) is not.

The cheapest next move is not a treatment. It is the shuffle test, because
every question above about which treatments are worth building is a question it
can answer mechanically.
