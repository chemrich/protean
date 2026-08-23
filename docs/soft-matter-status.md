# Soft matter: where it stands

**Read this before `soft-matter-plan.md` or `soft-matter-review.md`.** The plan
is the original proposal, the review is what six agents found wrong with it, and
this file is the only one that says what is actually true right now.

Last updated **2026-08-23**, at `main` after #118.

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

- **`scaffold`** — `SD-08` from the catalogue, and the first real treatment.

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

## What to do next

### 1. Decide whether a second treatment is worth building

`scaffold` is evidence that the cheap tier really is cheap. It needed no new
engine work, because protean's selection language already does numeric
thresholds and `b < 70` on a predicted model *is* "the parts the model is
unsure about".

Whether anything else in the catalogue earns the same effort is Charlie's call,
not a technical one. The review's answer was: `SP-01` maybe, the rest probably
not.

### 2. How `SD-08` was estimated — kept because the estimate held

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

### 3. Then decide about `SP-01` Radiolaria

The other strong idea: draw each atom as an open lattice cage so you can see
into the molecule instead of at its outer shell. The review agreed it delivers
something ordinary rendering cannot.

**Do not start this without deciding two things first**, because both are
expensive and neither is decided:

- It needs protean to build Mol\* from source instead of using the prebuilt
  copy. That is possible (measured in #107) but adds a build step.
- It runs out of graphics memory on large molecules. The escape route named in
  the bake-off turned out to save 12%, not the order of magnitude claimed.

### 4. Standing rule for any treatment

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
