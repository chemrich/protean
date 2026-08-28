# Soft matter: where it stands

**Read this before `soft-matter-plan.md` or `soft-matter-review.md`.** The plan
is the original proposal, the review is what six agents found wrong with it, and
this file is the only one that says what is actually true right now.

Last updated **2026-08-26**, at `main` after #139 plus the painterly branch.

---

## The sequence Charlie set is finished, and a second run followed it

Six PRs, #121 to #126, all merged: the finish route's base, `cyanotype`,
`spot-ink-plates`, plates-by-element, `boil(trails=True)`, and `lens()`.

**Then #128 to #138, on 2026-08-25/26.** Two of those change this document's
standing facts and are recorded below: a **fifth finish** (`engraving`, #136),
and the **removal of the reason mesh-based treatments were out of reach**
(#137). `main` is at `829c95c`.

**A sixth finish landed on 2026-08-26, and it is the first that is not a
finish at all.** `brushwork()` is a GPU render pass, not a Pillow pass over
captured pixels — see "The one that runs in the viewer" below. It is the first
thing built on #137, and it changes this document's oldest standing fact: the
sentence in `analysis/hatching.py` that says the cost of drawing a finish in
Python rather than in the renderer is that *the viewer cannot show it*.

**What comes next is not another treatment.** `docs/molstar-capabilities.md` —
an audit of Mol\*'s actual parameter surface, commissioned because `lens()`
found that fog had been on and invisible since the beginning — says the
bottleneck is structural. `show()` forwards two representation parameters and
`color()` forwards none unless you pass a hex literal. Twenty-odd of Mol\*'s
most valuable knobs sit behind that one gap, and six independent readers each
priced their find as "one handler" without noticing they share a prerequisite.

Read that document before planning anything. Its §5 says plainly what it could
not settle without rendering, and that list is where the next measurements go.

## In one paragraph

The plan proposed 36 new ways of drawing molecules. A review cut that to
roughly 6–12 worth building, and **one of them is now built** — `scaffold`,
the review's highest-rated idea. Before that came the groundwork: a fix for a
colouring bug the review found, and a test that can tell whether a treatment's
data binding is real. One earlier treatment, `felt`, is a plain style with no
data attached. The next decision is which treatment, if any, comes second.

## What has been decided

Each of these is settled. Reopening one needs a reason, not a preference.

| Decision | Why |
|---|---|
| Soft matter stays a **separate product**. protean hosts prototypes. | 36 treatments absorbed into protean would bend it out of shape. |
| **No JSON manifest.** Treatments become tools with named arguments. | A model calling protean sees tool arguments, not a manifest schema. A config blob is something it has to guess at. |
| **No PyMOL plugin.** | protean is not PyMOL. That was four weeks of the plan aimed at the wrong program. |
| **No frames-per-second budget.** | protean does not measure fps anywhere, on purpose. |
| `felt` ships as a **style with no data channel**, and says so. | Charlie's call, 2026-08-21. The channel was built and could not be seen. |
| `SM-02` is called **Clay**, not Claymation. | Claymation is a registered trademark. |
| A treatment may not claim it shows data unless a **shuffle test** proves it. | See below. This is the rule the review said the plan could not enforce. |
| **Cyanotype ships as declared decoration**, the way `felt` does. | Charlie's call, 2026-08-23. One ink on one ground has no second plate to assign, so there is nothing cheap to bind. Admitting that is what keeps "bind a channel where one is cheap" honest. |
| After cyanotype: **the plate print, then phosphor-with-trails.** | Charlie's call, 2026-08-23. |
| **`SP-01` Radiolaria stays parked**, though one of its two blockers is gone. | Charlie's call, 2026-08-23, asked directly. #107 measured the build-from-source cost at 1.2 GB and 4.6 s, so "it needs a Mol\* fork" is no longer true; the memory ceiling on large molecules is. Declined anyway. |

## What has been built

- **`felt`** — a style, no data channel. Shipped #109.
- **The polarity fix** — shipped #116. Predicted models store confidence in the
  same slot crystal structures use for uncertainty, and the two mean opposite
  things. protean now refuses to draw one as the other, and ships a `plddt`
  view for predicted models.
- **The shuffle test** — shipped #115, extended in #117. Scramble which atom
  each number belongs to, redraw, and compare. If the picture does not change,
  the colouring was never reading the data. This is what the plan's own
  "every treatment must show data" rule needed in order to mean anything.

- **`boil(trails=True)`** — the boil held open on one plate. Makes `boil`'s
  channel legible in a single frame, where before it could only be seen by
  watching the sequence play. `smear` reads 0.0 exactly when nothing moved.

- **`spot-ink-plates`** — the fourth finish and **the first that carries a
  data channel**. Binds which plate a region prints on, which is a category and
  so survives shading, where a shade-driven binding does not. Proved by taking
  the colour away: two inks and their crossing become one ink and nothing else.
  The capture is coloured by element for the one frame and the scene is put
  back, so the binding is a guarantee rather than a hope about how the caller
  had coloured their scene. Proved twice: taking the colour away turns two inks
  and their crossing into one ink and nothing else, and the arm this file's own
  standing rule requires is in `tests/test_shuffle_differential.py`, where the
  press keeps 0.485 of the difference the render carries against 0.183 for the
  same finish with its separation removed.

- **`engraving`** — the fifth finish, added #136 on 2026-08-26, and the one
  that answers "the hatching should be fine and depth cued". **No new rendering
  code.** It is `_Survey` — the engine behind `cyanotype` — with the paper set
  white and the ink black, at fourteen levels rather than five.

  The finding is that `_Survey` was always a depth-cued renderer and had only
  ever been drawn in blue. It contours the *recovered lighting field* and holds
  constant line width by dividing the residual by the local slope, so the marks
  follow the form because they **are** isolines of it. `cross-hatch` and
  `hedcut` rule strokes at a fixed angle regardless of what is underneath,
  which is why neither reads as having depth.

  Its numbers were chosen by rendering 5 / 9 / 14 / 20 / 28 at plate size and
  looking, not from print convention. `brightest` is raised to 0.9975 because
  the survey's 0.975 clamp takes 3.27% of molecule pixels and all of them sit
  on the summit of an atom.

  A drawing style with no data channel, like `cyanotype`. No shuffle arm.

  **It exposed that the finish-comparison test could not see a fine finish.**
  `_grain` resolves its lattice as `max(2.0, diagonal * pitch)`, so at the
  suite's 240 px fixture every fine finish clamped to the same 2 px floor:
  cyanotype and engraving disagreed on 0.0000 of the frame at 240 px and 0.4811
  at 1200. The comparison now draws at 480 and a guard asserts no two finishes
  share a resolved grain step.

- **`brushwork(look="chiaroscuro")`** — the sixth finish, 2026-08-26, and the
  first that runs in the viewer. An oil painting: the picture abstracted along
  the form, noise dragged through it by line-integral convolution for the
  bristle, that same field read as a height and relit by a raking light for the
  impasto, and a woven ground under both. `preset("painting")` is the scene set
  up for it and is no longer the Geis homage it was.

  **A drawing style with no data channel**, like `cyanotype` and `felt`. No
  shuffle arm, because it claims nothing a shuffle arm could test — the marks
  follow the shading, which is a property of where the light is.

  **It shipped as a Dutch Master and Charlie sent it back**: *"way too earth
  tone, too dark ... brighten the mood. Make it joyful."* It is `spring` now —
  coral against sky on cream — with `poster` and `orchard` beside it. The
  interesting part is what the hunt for the gloom turned up, because the ground
  was the least of it and all four of these reported success:

  1. **The pass was not running a Kuwahara filter.** The sector weight is the
     published one, which operates on 0-255 values; on [0,1] the exponent
     annihilates it, and at `hardness 8` the weight's entire dynamic range is
     1.0000000 to 0.9999847. An anisotropic Gaussian blur had been wearing the
     name of an abstraction for the feature's whole life, and **two comments in
     this repo described behaviour that arithmetic cannot produce**.
  2. The impasto relight took 14.3% off every painted pixel, quoted as an
     absolute range rather than as contrast.
  3. The "edge darkening" was a 21% global dim, keyed on a gradient's *shape*
     rather than its strength.
  4. The shadow could only ever darken, and its luminance band never fired on a
     bright palette.

  **The lesson for the next treatment is the guard, not the bug.** None of these
  could be seen in a picture: an abstraction going missing looks like a slightly
  softer painting, and every other term still runs. Three were found by
  re-deriving the shader's arithmetic on the CPU and printing the numbers. The
  one now guarded is guarded by a **property test** — that the weight can
  discriminate at all — rather than by a value, plus an assertion that the
  TypeScript mirror still matches the GLSL it mirrors.

  And the biggest lever was not in the pass at all: the studio rig with its cast
  shadow was taking the colour out of the palette before the paint saw it. Same
  palette and look, only the light changed — subject luminance 112 to 162,
  saturation 112 to 146. **A treatment that reads the shading is downstream of
  the lighting rig, and the rig is the first thing to look at.**

  **And then the rig bit from the other side**, which is the finding to carry
  into any treatment with an internal direction field. Opening the light cost
  the *flow field* its signal: the flow runs along a ribbon only because the
  shading gradient runs across it, so flattening the light flattened the thing
  the marks steer by. The strokes went haphazard for the same reason the picture
  got brighter, and the two complaints looked unrelated. Fixed by putting a
  depth gradient into the structure tensor beside the colour one.

  Three more, from three more rounds of Charlie looking at plates:

  - **A relit height field reads as metal, always.** A fake impasto on a curved
    ribbon is crumpled foil however the bumps are shaped, because relighting is
    what tells an eye it is looking at a surface with a normal.
  - **Random brightness per mark is the same illusion with no lighting at all.**
    Move the variation into chroma, and make the marks *tile* — a mark covering
    part of a surface is a fleck on it; a surface made of marks is paint.
  - **A stroke is a shape and has to be placed**, not sampled out of a field.
    Three field-based attempts each failed differently and all read as noise.

  **The method that ended it: render the field rather than reasoning about it.**
  One debug build settled a question three rounds of inference had got wrong.
  Any treatment with an internal vector field should have a debug output before
  it has a second parameter.

  The finding worth carrying: **abstraction alone is not a painting.** The first
  version was anisotropic Kuwahara and nothing else, which is what the
  literature says a painterly filter is, and it gave back a clean render with a
  softer silhouette. Kuwahara abstracts texture that is *already there*, and
  every published demonstration runs on a photograph. Whatever the next
  treatment borrows from image processing, ask first what texture in the source
  it is meant to be sorting.

  The other finding is this document's own §2.3 rule arriving on the GPU. Both
  of the pass's own resolution bugs were the same shape: `brush_size` scaled the
  abstraction radius and nothing else, so it changed a reported number and
  almost no pixels; and the impasto relief was quoted as a fixed number while
  the slope it reads goes as one over the grain, so a small plate came back as
  black speckle. **A length inside a shader is exactly as prone to this as a
  length inside Pillow, and harder to see** — there is no `ink_fraction` in the
  reply to catch it. Hence `resolveBrush`, a pure function, and a reply carrying
  both `brush_px` and `stroke_px`.

- **`cyanotype`** — the third finish and the first that is not an engraving.
  A drawing style with no data channel, said outright in its docstring, the way
  `felt` is. No shuffle arm, because it claims nothing a shuffle arm could
  test.

- **The finish route's base** — no new look, and the groundwork for the print
  finishes. A finish declares its own paper and ink; the ink fraction is
  measured against that paper rather than against black; the finish name is
  checked before a figure-resolution render is paid for. Three of the tests
  guarding the route could not fail — the headline one passed for a finish
  that returned its input unchanged.

- **`scaffold`** — `SD-08` from the catalogue, and the first real treatment.
- **`boil`** — `TM-01`. The first temporal treatment, and the first thing here
  that no other molecular viewer has done at all.

## The one treatment that is built

`preset("scaffold")` draws a predicted model's confident regions as cartoon and
**covers** everything below pLDDT 70 with an opaque grey surface, the way
sheeting covers the unfinished part of a building. No legend, no colour to
decode: the parts you cannot see are the parts nobody should be reading.

It refuses on an experimental structure — whose B-factors describe disorder,
not confidence — and names `putty` instead.

A model that is confident everywhere draws no cover at all, which is the
correct picture rather than a failure. The reply reports the count either way,
because "nothing to cover" and "the cover failed to draw" are the same picture
and only the reply can tell them apart.

Proved to be reading the data, twice over. Three arms — nothing below the
line, half below, all below — must produce increasingly different pictures, so
a cover that drapes the molecule whenever *anything* is low fails. And it has
the **shuffle arm** this file requires: the same numbers moved onto different
residues, so the same amount is covered and only the placement changes. That
one measures 0.1129 against a 0.008 threshold, and a cover that counts instead
of reading fails it at `0.0`.

## The temporal one

`boil()` redraws the molecule every few frames with the atoms nudged a little
further, and holds each pose for two frames — animation's "on twos". The
molecule looks *made* rather than computed.

The plan called this the highest charm-per-line-of-code item in all 36, and it
is the clearest case of the brief Charlie set on 2026-08-23: **look better in
ways PyMOL's and Mol\*'s authors would not have discovered.** Both treat the
still frame as the unit and reserve motion for the camera. Nothing in molecular
graphics moves the drawing itself.

It carries a channel and the channel is apt: **how far an atom wanders follows
how sure the data is about it.** A disordered loop swings and an ordered core
holds; on a predicted model the guessed regions swing and the confident ones
hold. That reuses #116's polarity machinery to know which way to read the
column, and it is bound at region scale, which is why it is visible where
`felt`'s per-atom jitter was not.

Measured: poses hold **bit-identically**, poses differ by 0.033–0.040, and a
boil at an amplitude too small to see reads 0.000154 — so the reload each pose
carries contributes about a two-hundredth of the effect. Its shuffle arm
measures 0.0286, and a wobble that ignores the column fails it at 0.0004.

Two things it does not do. The **scene** is not restored, because each pose
reloads the structure and that rebuilds the viewer's components — the same
property `color_by_rmsf` has, reported in the reply. And the coordinates *are*
restored, because a drawing style may not quietly edit the structure.

## What the audit changes about the plan

Read `docs/molstar-capabilities.md` §4 in full. The cost errors cluster by tier
rather than scattering:

- The tier the plan priced **highest** — surface-derived treatments, gated
  behind a two-week prerequisite — is the one Mol\* has most already built.
  Surfaces ship fully parameterised; clipping a molecule open, which the plan
  buries inside an L-effort treatment, is two independent parameter sets that
  ship today and which protean has **none** of.
- The tier the plan priced at **6-8 weeks** — per-atom generated geometry:
  radiolaria, pom-poms, vacuum tubes — is the one the prebuilt bundle genuinely
  cannot do at any price.

So roughly weeks 5 to 20 of that schedule is work that either already exists or
is impossible without bundling Mol\* from source.

**As of #137 on 2026-08-26, protean bundles Mol\* from source.** That last
clause is no longer a hypothetical: per-atom generated geometry and a custom
post-processing pass are reachable. **The post-processing half is now built** —
`brushwork()` — and what it learned about the seam is recorded in
`docs/views.md` §5.11. The short version, for anyone reaching for the same door:
Mol\* has no registry, no props variant and no hook for a third-party pass, so
the passes are wrapped; `ImagePass` owns its *own* copies of them, so patching
the canvas's instances gives a finish that is on screen and absent from every
capture; and the live canvas accumulates four jittered sub-frames where a
capture accumulates sixteen, so anything applied inside that is averaged away by
different amounts in the two places. Per-atom generated geometry is still
untouched.

Three findings worth acting on regardless of the plan:

1. **`color("accessible-surface-area")` already works today** and nobody
   noticed. protean validates theme names against the live registry, and a
   default plugin behavior registers that theme. The plan budgets a week for
   pulling SASA forward.
2. **`spin(speed=-0.1)` is promised by protean's own docstring and refused by
   protean's own code.** Mol\* accepts -2 to 2; `dispatch.ts` throws below 0.
   A model following the tool description gets an error.
3. **`show(representation='interactions')` passes the name check and draws
   almost nothing**, for three independent parameter reasons. A picture protean
   nearly has and does not know it.

## What to do next

### 1. The print finishes

Charlie's direction on 2026-08-23 is visual impact over analytical capability,
and the cheapest striking work left is capture-time image treatments in Pillow.
protean already ships cross-hatch and hedcut by that route. No engine work.

The order, settled 2026-08-23:

1. **The finish route's base** — no new look. Done first because the route had
   *black on white* written into three separate places as though it were a
   rule, and the next finish would have inherited all three. In particular the
   ink fraction — the one number a caller who cannot see the file gets — asked
   whether the red channel was zero, which would have reported a coloured
   finish as a blank page or a solid one at random.
2. **Cyanotype** — *shipped*, as declared decoration. A blueprint: white on
   Prussian blue, contouring the shading rather than hatching it, so every atom
   comes out as nested rings and the frame reads as a survey sheet. Picked from
   a four-way bake-off judged on the pictures; an **opaque background** decided
   it, because two of the four dissolved into a texture of their own making
   once the ground was not paper-white.
3. **The plate print** — *shipped* as `spot-ink-plates`, named for the
   technique rather than for a manufacturer, and assigned by element, both
   Charlie's calls on 2026-08-24. Spot inks, one halftone screen per plate at its own
   angle, each offset a fraction of a frame so the misregistration shows. This
   is the one that carries a data channel, and it binds **which plate a region
   prints in** rather than how dark it is: a category survives shading, where a
   shade-driven binding is downstream of the lighting rig and gets quantised
   away. That is the trap the bake-off fell into.
4. **Phosphor with trails** — *shipped* as `boil(trails=True)`. Accumulates
   the poses into one long exposure with decay, so smear length is the channel.
   It needed no shuffle arm of its own: the binding is `boil`'s, already proven
   by `test_the_boil_wobbles_the_atoms_the_data_is_unsure_of`, and what this
   adds is making that binding **visible in a still** rather than only by
   watching.

   **Not built, deliberately:** a general tool that composites any existing
   frame directory. It would let a turntable or a trajectory take the same
   treatment, and its claim would honestly be "smear shows whatever moved" —
   but that is a second public tool for a caller to reach for, and the plan's
   item was the boil. Worth revisiting if anyone wants a motion-blurred spin.

**Duotone is dropped as its own finish.** It was prototyped in the bake-off,
never committed, and the picture is the weakest of the candidates — a fine dot
screen that erases the sphere shading and does not read as a molecule. Its
halftone screen and tuned constants belong inside the plate print. The
prototype is preserved outside the repo rather than lost.

Two unexposed Mol\* knobs — **fog** and an **orthographic camera lock** —
*shipped* as `lens()`. Neither carries a data claim; fog's channel is camera
depth, which cannot be permuted across atoms, so a shuffle arm is the wrong
instrument for both.

The finding worth carrying past this item: **Mol\*'s default fog is
invisible.** It has been on at intensity 15 since the beginning, so every
protean figure ever made carries it — and measured with no tolerance it is
bit-identical to off below 40. A default being *set* is not evidence that it
*does* anything, and that is a question worth asking of every parameter protean
inherits. It is what the Mol\* capability audit was commissioned to ask.

### 1b. The rest of the painting

Charlie's direction on 2026-08-26, from using the viewer: **an oil painting of a
ribbon drawing — brush strokes and canvas texture. Dutch Master first, then a
Seurat pointillist, then a bold Van Gogh.** They chose **live in the viewer**
rather than a capture-time finish, which is why #137 was done first.

The Dutch Master is shipped as `chiaroscuro`. The other two are entries in
`PAINTERLY_LOOKS` over the same engine — the flow field, the bristle and the
relief already exist — plus one thing each:

- **`divisionist`** (Seurat) needs a dab lattice, and it must not collide with
  `spot-ink-plates`. The difference is structural rather than cosmetic and is
  the thing to build to: **a halftone modulates dot *area* at fixed spacing with
  a fixed ink; a pointillist dab modulates *colour* at near-constant area.**
  Everything else falls out of that — the lattice is jittered rather than ruled
  because the rosette is the failure here and the point there, the ground shows
  as a positive colour rather than as absence of ink, and coverage is held below
  1. A test can assert the difference directly: within one flat-coloured region
  the dabs must not all be the same RGB, which for `spot-ink-plates` they are by
  construction.
- **`impasto`** (Van Gogh) is `chiaroscuro`'s own machinery turned up — longer
  strokes, deeper relief, bolder chroma — with one real addition: the chroma
  boost has to happen in a hue-preserving space, because per-channel clipping in
  sRGB rotates a blue chain toward magenta at the top end and the picture would
  then be lying about protean's own colour coding.

### 2. Decide whether a third treatment is worth building

`scaffold` is evidence that the cheap tier really is cheap. It needed no new
engine work, because protean's selection language already does numeric
thresholds and `b < 70` on a predicted model *is* "the parts the model is
unsure about".

Whether anything else in the catalogue earns the same effort is Charlie's call,
not a technical one. The review's answer was: `SP-01` maybe, the rest probably
not.

### 3. How `SD-08` was estimated — kept because the estimate held

A predicted model is confident in some regions and guessing in others. `SD-08`
draws the confident parts as finished structure and covers the guessed parts,
the way scaffolding and tarps cover an unfinished building. You can see at a
glance which parts to trust, with no legend and no colour key.

The review called it the strongest idea in the catalogue because it **refuses
to show detail the data does not support**, rather than decorating it.

It was blocked on two things, both fixed this week:

- Predicted models could not be loaded. Fixed (backlog 33 and 34).
- Confidence could not be read correctly. Fixed (#116).

**It needs no new engine work.** Verified on 2026-08-23: protean's selection
language already does numeric thresholds — `b < 70` on 1UBQ selects 509 of 660
atoms — and on a predicted model that expression *is* "the parts the model is
unsure about". So the treatment is:

1. Select `b < 70` — the low-confidence regions.
2. Draw those one way (coarse, covered, no fine detail).
3. Draw the rest the normal way.
4. Add it as a view, the way `felt` and `richardson` were added.

Estimated at days, not weeks. **Estimated at days. It took one.**

Caveat to state in its docstring: it has two levels, not a smooth range, and it
means nothing on an experimental structure — so it should refuse there, the way
`plddt` now does.

### 4. `SP-01` Radiolaria — asked and declined, 2026-08-23

**Decided: it stays parked.** Kept below because one of its two blockers is
gone and someone will ask again. The reasoning as it stood:

The other strong idea: draw each atom as an open lattice cage so you can see
into the molecule instead of at its outer shell. The review agreed it delivers
something ordinary rendering cannot.

It was blocked on two expensive things. **The first is now simply done:**

- It needed protean to build Mol\* from source instead of using the prebuilt
  copy. #107 measured that at 1.2 GB and 4.6 seconds; **#137 did it**, on
  2026-08-26, measured at **1.07 GB peak and 5.35 seconds**, bundle 4,800 kB to
  5,147 kB. Three files changed. The "over 4 GB" sentence is gone from
  `main.ts`, and with it the reason `docs/views.md` §5.9 put cross-hatching in
  Pillow rather than in a render pass.

  It carries protean's own machinery, which the 2026-08-21 spike never tested:
  bridge, dispatch, live registries, presets, `lens` read-back, `snapshot`,
  the analysis views, and the raf pump — whose load-order requirement is now
  structural, since a classic script always runs before a deferred module.

  It also fixed Mol\*'s backdrop artwork by construction: those images are a
  bundler-resolved import the prebuilt UMD had frozen to a path protean never
  copied, and that 404 **permanently wedges `snapshot()`** rather than
  degrading quietly.

  The cost that remains is real: a 5-second, 1.1 GB step in every build and CI
  run, and protean now owns a build it used to inherit.
- It runs out of graphics memory on large molecules. The escape route named in
  the bake-off turned out to save 12%, not the order of magnitude claimed.

### 5. Standing rule for any treatment

Before a treatment can claim it shows data, it needs a **shuffle test arm**
in `tests/test_shuffle_differential.py`. If scrambling the numbers does not
change the picture, the treatment is decoration and should be documented as
decoration — the way `felt` is.

This is not optional politeness. It is the only reason the retraction below
cannot happen again.

## What is parked, and why

- **The other ~30 treatments.** Roughly 15 cannot be read by eye, 4 duplicate
  what ordinary colouring already does, 4 are too large to draw, and several
  bind to numbers that do not exist. Details in `soft-matter-review.md`.
- **`SP-09` Gaze.** Fails on three separate grounds at once, including that it
  looks like Minions.
- **Publishing an npm package, a MolViewSpec extension, and a Blender
  importer.** All aimed at consumers protean does not have. The Blender path is
  the one worth revisiting if anyone ever wants hero renders.

## The retraction, in one line

`docs/bakeoff.md` once concluded that binding data to a picture does not make
it readable. It ran on a structure whose numbers were identical on every atom,
so nothing was being drawn from data at all, and the conclusion was withdrawn.
That is why the shuffle test exists.

## Keeping this file true

This file goes stale the moment soft-matter work lands without touching it.

**The rule: any PR that changes soft-matter status updates this file in the
same PR.** That means shipping a treatment, killing one, changing a decision in
the table above, or unblocking something in "what to do next".

If you are reading this and the date at the top is old, trust the date, not the
text — and fix it.
