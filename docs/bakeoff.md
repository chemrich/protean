# The soft-matter bake-off

**Three treatments, one per route, built and looked at — 2026-08-21.**

Track B item 3 of the plan in `docs/soft-matter-plan.md`. The point was never
to ship these. It was to find out whether the treatments carry data legibly,
and what they cost, *before* anyone commits to the 28–41 weeks the plan
estimates. One structure throughout: **1MBN, sperm whale myoglobin, 1260
atoms.**

> **Correction, 2026-08-22 — every channel finding below is void.**
> 1MBN's B-factor column is `0.00` on all 1,216 `ATOM` records: one distinct
> value. All three treatments bound B-factor, so all three bindings were
> constant. Felt's jitter amplitude reduces to `0.04 + min(1, 0/60) * 0.14`,
> a constant; radiolaria's `bRange` returns `[0, 1]` when `hi > lo` is false,
> so porosity was constant too. **No channel varied in any of these pictures.**
> What the treatments look like, what they cost, and the four bugs below all
> stand — none of them depend on the data. What does not stand is every claim
> about whether a channel could be *read*, including this document's central
> one. The bake-off needs re-running on a structure with real B-factors —
> 1UBQ's p10→p90 is 5→28. Found by the adversarial review in
> `docs/soft-matter-review.md`.

The three routes protean can reach, one treatment each:

| Route | Treatment | Where it runs |
|---|---|---|
| Themes and materials only | **SM-01 Felted wool** | The prebuilt bundle protean ships today |
| Python, after the capture | **SS-03 Duotone spot-ink** | Pillow, the route `snapshot(finish=)` already takes |
| Custom mesh representation | **SP-01 Radiolaria** | Mol\* bundled from `lib/` — see `docs/molstar-bundling.md` |

## What each one turned out to be

**Felt works, and its channel does not.** Killing the speculars, raising
roughness to 1, driving `bumpiness` to 0.9 with a high bump frequency, and
laying a second spacefill layer at 1.12× and alpha 0.2 produces a convincingly
fibrous surface in the dyed-wool palette. It is the cheapest treatment in the
catalogue and it needs no new engine at all. But **the binding is not visible**.
*(Correct conclusion, void evidence: the jitter was constant here, so this
picture never contained a channel to miss. The reasoning below is a prediction
that has not been tested.)* B-factor scales the per-atom radius jitter, and
against a surface that is
already fuzzy at the same spatial frequency, a few percent of radius is
invisible. The plan's P3 says a treatment that cannot carry data is decoration;
by that rule felt as built here *is* decoration, and fixing it means finding a
channel the eye can separate from the texture — fiber *length* rather than
radius, which needs real geometry and stops being a G2 treatment.

**Duotone works, and its channel only survives because two stages agree.** The
halftone is real: two spot inks at 15° and 75°, dot area following tone, paper
showing through, multiply blending so overlaps make a third colour. But a
capture-time finish **sees pixels and nothing else** — it cannot read a
B-factor. So the render has to carry B-factor as *tone* first, and the screen
then converts tone to dot area. **Whether the channel survives is untested** —
1MBN's B-factors were flat, so the tone this screen converted was shading and
nothing else. What the two-stage route costs is that the binding lives in
two places at once, and a caller who recolours the scene silently breaks it.
**This is a property of the whole Python-at-capture route**, not of duotone:
every SS-treatment reached that way inherits it.

**Radiolaria is the real thing.** A geodesic strut lattice per atom, porosity
from a per-atom scalar (constant in this run — see the correction above),
picking intact. You can see into the molecule in a way
the control simply does not allow — the plan's P1 claim is *true*, and it is
the most convincing picture of the three. Two caveats from actually looking:
the interior reads as a busy haze rather than as legible structure, because
1260 overlapping cages are still 1260 overlapping things; and the effect
depends hard on strut radius. At the plan's own manifest value (0.08) it is
lace; at 0.3 it is a spiky solid and strictly worse than a sphere.

## What it costs, measured

Build time and vertex count for radiolaria, at the coarsest possible cage
(`detail: 0` — a bare icosahedron, 30 struts per atom):

| Structure | Atoms | Vertices | Per atom | Build |
|---|---|---|---|---|
| 1MBN | 1,260 | 457,560 | 363 | 0.3–0.4 s |
| 4HHB | 4,779 | 1,734,840 | 363 | 1.6 s |
| 6VXX | 23,694 | 8,668,080 | 366 | 3.8 s |

Dead linear, as it must be for per-atom geometry. Extrapolated, the plan's
§7 budget rows are **not reachable this way**: 50k atoms is ~18M vertices, and
the 200k mesoscale row is ~73M vertices, which at Mol\*'s ~28 bytes per mesh
vertex is several GB against a stated 1.5 GB ceiling.

So **instancing is not an optimisation for this treatment, it is a
precondition** — and that collides with the plan's own binding. §4.4 says
Radiolaria "emits *one* geodesic shell instanced N times with per-instance
scale and rotation", and §7 makes a per-atom unique mesh "an automatic reject
at review". But instancing can only vary what instancing carries — scale and
rotation — and **porosity is neither**. A per-atom porosity binding and a
single instanced template cannot both hold.

There is a way out the plan half-names, but it is much smaller than stated
here originally. Mol\*'s `Cylinders` primitive is a quad impostor, **not**
GPU-instanced: `add()` pushes six vertices per strut with start, end and scale
duplicated across all six (`cylinders-builder.js:26-33`). Per strut that is
360 bytes against mesh geometry's 408 — **a 12% saving, not an order of
magnitude.** It does not change the O(atoms × struts) growth and it moves the
1.5 GB ceiling from ~122k atoms to ~139k. What it does buy is real: strut
radius *is* per-cylinder (`aScale`, read at `cylinders.vert.js:57`), so
per-atom porosity survives, and struts stay round at any radius. §4.1 already
says to prefer `Cylinders` where a treatment allows and then assigns `Mesh` to
Radiolaria. **Untested here** — still worth trying, but not as a budget fix.

## Four things that went wrong, and what they say

Every one reported success first.

- **The plan's own §4.1 skeleton does not run.** It writes
  `const pos = unit.conformation.invariantPosition`, and `invariantPosition` is
  a class method reading `this._x`
  (`mol-math/geometry/symmetry-operator.js:144`). Detached from its object it
  throws `Cannot read properties of undefined (reading '_x')` on the first
  atom. The same snippet also sets `builder.currentGroup = i` while iterating
  `for (const i of unit.elements)` — that is the *element* index where Mol\*
  wants the loop index, so picking and every per-atom colour theme would map to
  the wrong atoms while looking entirely plausible.
- **Mol\* silently overrode the geometry parameter.** Asking for `detail: 0`
  rendered at `detail: 3`, because auto-quality recomputes it unless
  `quality: 'custom'` is set. That is 1,920 struts per atom instead of 30 and
  **23,040 vertices per atom instead of 360** — a 64× cost, reported as
  success, with a picture that still looked like the treatment. Any manifest
  that declares a subdivision level, as the plan's example does, is subject to
  this.
- **Stale hierarchy refs made a treatment photograph the previous one.**
  Holding one `components` array from boot and reusing it: Mol\* rebuilds the
  hierarchy when representations change, and `addRepresentation` against a dead
  ref resolves successfully. "Radiolaria" drew in 30 ms and the picture was
  felt.
- **The finish painted the empty frame, twice, for two different reasons.**
  First the paper threshold — `analysis/hatching.py` already records that a
  "white" ground is about 252 and needs a 0.96 cutoff, and writing a second
  finish from scratch reproduced the bug immediately. Then the halftone's
  soft-edge term, which adds half a dot at every cell centre even where ink
  coverage is exactly zero. **The constant and the guard belong to the route,
  not to any one finish** — which is an argument for a shared finish base
  before there is a third one.

## The judgement

**Radiolaria justifies more work. Felt and duotone, as specified, do not.**

- Radiolaria delivers what the plan claims for it and is the only one of the
  three whose picture is better than the control at showing you something.
  Its cost problem has a named fix that has not been tried and that, on the
corrected numbers, buys 12% rather than an order of magnitude.
- Felt is charming and carries nothing. Under the plan's own P3 it should be
  cut or respecified.
- Duotone is a good picture with a fragile binding, and the fragility belongs
  to its whole route. Worth having as a *finish* alongside cross-hatch and
  hedcut — which is a small job protean could do today — and not worth
  counting as one of eight screen-space treatments with data channels.

**What this says about the 36.** The plan's own tiering held up: the cheap
tiers are cheap and the expensive tier is expensive. What did not hold up is
the assumption that a treatment binding a channel makes it legible — but this
run produced **no evidence either way**, because the structure it ran on had no
channel to bind. Three treatments declared a binding; zero of them varied.
That, not the engine cost, is the thing to fix before scaling to 36, and the
fix is mechanical: render twice, once with the channel and once with its values
shuffled between atoms, and diff. If the two frames match, the binding carries
nothing. See `docs/soft-matter-review.md`.
