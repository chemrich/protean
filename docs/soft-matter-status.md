# Soft matter: where it stands

**Read this before `soft-matter-plan.md` or `soft-matter-review.md`.** The plan
is the original proposal, the review is what six agents found wrong with it, and
this file is the only one that says what is actually true right now.

Last updated **2026-08-23**, at `main` after #121.

---

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

Two unexposed Mol\* knobs ride along cheaply afterwards and unlock the
painterly looks: **fog** and an **orthographic camera lock**. Neither carries a
data claim; fog's channel is camera depth, which cannot be permuted across
atoms, so a shuffle arm is the wrong instrument for both.

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

It was blocked on two expensive things. **One of them is no longer true:**

- It needs protean to build Mol\* from source instead of using the prebuilt
  copy. #107 measured that at **1.2 GB of memory and 4.6 seconds**, producing a
  bundle the same size as the prebuilt one — so the "over 4 GB" figure still
  written in `main.ts` is wrong, and no fork is required. What remains is that
  it adds a build step protean does not have today.
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
