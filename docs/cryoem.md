# Cryo-EM and conformational heterogeneity — what to build

Planned 2026-08-12. **Corrected against `main` on 2026-08-14, and kept current as
§1.1, §1.2, §1.4 and §2 shipped.** What protean needs in order to show density maps, local
resolution, and the parts of a model that are not a single rigid conformer.

The immediate driver is
[`wiggles-em`](https://github.com/chemrich/wiggles-em) — a viewer-neutral
package of cryo-EM views hosted by both MCPymol and protean. But every feature
below is worth building on its own: any map at all — cryo-EM, crystallographic
2Fo-Fc, a segmentation — was out of reach until volume loading and contouring
landed.

Ordered by what unblocks the most. Each section says what already exists.

## Status

| | | |
|---|---|---|
| 1.1 `load_volume` + HTTP route | **shipped** | PR 69; `provenance=` followed in PR 72 |
| 1.4 `volume_info` | **shipped** | PR 69 — *and its advice here was wrong; see below* |
| 1.2 `isosurface` | **shipped** | PR 73 — the backend no longer refuses an `Isosurface` op |
| 1.3 `color_surface_by_volume` | open | unblocked now 1.2 is done |
| 1.5 carve | open | server-side; independent of the viewer |
| 2 altlocs | **shipped** | PRs 58-64, decision 16 |
| 3 scalar colouring | half | the *backend* has `ColorByScalar` (PR 68); the three MCP tools are still separate |
| 4 size by scalar | open | needs 3 |

**What this document got wrong, recorded rather than quietly edited**, because
the errors are more instructive than the plan:

1. **§1.4 named the wrong mechanism.** It said to read statistics off
   `data.grid.stats` "not echoed from the header". For CCP4/MRC `grid.stats`
   **is** the header — Mol\* passes DMIN/DMAX/DMEAN/RMS straight through. The
   principle was right and the implementation it prescribed would have produced
   exactly the failure it was warning about. Corrected in §1.4.
2. **§1.1's provenance parameter was not built** at first; PR 72 closed that.
3. **§1.2's Mol\* API sketch was not reachable.** `VolumeRepresentation3D` and
   `Volume.IsoValue` are not exposed by the prebuilt bundle the viewer loads.
   The working path — `provider.visuals` plus a plain `{kind, absoluteValue}`
   object — is in §1.2, found by asking a real browser what the global actually
   holds.
4. Line references had drifted by ~200 lines, and the `wiggles-em` link pointed
   at `chemrich/wiggles`, which is a **different and private** repository.

---

## 1. Volumes

### 1.1 `load_volume(source, format="auto", provenance=...)` — SHIPPED (PRs 69, 72)

Shipped as `load_volume(path, name=None, format="auto")`, with `volume_info`,
`list_volumes` and `remove_volume` alongside. Formats: MRC/CCP4 (gzipped or
not), DSN6, OpenDX, Gaussian cube, BinaryCIF.

`provenance=` followed in PR 72 — see the end of this section.

The original reasoning, which held up:

`color_by_volume` already proved the whole path works
(`viewer/src/dispatch.ts:1729`): `plugin.dataFormats.get(fmt)` →
`plugin.builders.data.rawData({data, label})` → `provider.parse(plugin, raw)`
→ pull `cell.obj.data` and its `grid.stats`. It just hardcodes `'dx'` and
throws the parsed volume away after theming. It still does; §1.3 is where that
gets retargeted.

Mol\* 4.18 registers providers for **`ccp4`, `dsn6`, `dx`, `cube`, `dscif`,
`segcif`**. So the work was format detection, keeping the volume in the state
tree under a handle, and getting the bytes to the browser.

**Ship the bytes over HTTP, not over the RPC channel.** Structures currently go
inline as `data` in the `load_structure` action, which is fine for a PDB. A
110³ float32 map is ~5 MB and a 400³ one is ~256 MB; base64 through a JSON
WebSocket frame is the wrong pipe. `ViewerBridge` already runs aiohttp and
already serves the viewer's static files, so add a route that serves a
registered volume by handle and have the viewer call
`plugin.builders.data.download({url, isBinary: true})`. This deliberately
diverges from how structures are shipped, and the reason should be in a
comment: the two paths differ because of size, not because of format.

**Decompress server-side.** EMDB ships `.map.gz` and it is the common case.
`wiggles_em.analysis.mapinfo` already reads gzipped MRC headers; the same
`gzip` handling belongs in the loader.

**Provenance is a parameter, never inferred — SHIPPED (PR 72).** A measured
reconstruction and a network-enhanced one are the same isosurface once drawn.
`load_volume` records what it was told and defaults to `UNKNOWN`; it does not
guess from the filename. This is `wiggles-em`'s invariant I1, and protean reuses
that enum rather than defining a second one meaning the same thing — a viewer
that labels a generated map as measured is the failure the whole package exists
to prevent.

Two decisions worth carrying into §1.2:

- **A typo is refused, not coerced to `unknown`.** Coercion turns a caller who
  *did* declare their map into one who appears not to have, losing exactly what
  the parameter carries while reporting success.
- **The label lives on the viewer's handle**, so it shares that handle's
  lifetime — every path that forgets a volume drops its provenance too, and
  there is no second registry to fall out of step. The `caveat` prose is derived
  from the stored value on the way out rather than stored beside it.

Building it also produced a test that could not fail, which is recorded in §5.

### 1.2 `isosurface(volume, level, unit="sigma", style="surface", ...)` — SHIPPED (PR 73)

Shipped as `isosurface(name, level, unit, style, opacity)`, and the backend no
longer refuses a wiggles-em `Isosurface` op — it lowers it, passing the unit
along rather than the number alone. A carve is still refused, which is §1.5.

**Convert σ against the computed statistics, not the header — and this is the
whole feature.** Mol\* accepts `{kind:'relative'}` and converts with
`relativeValue * grid.stats.sigma + grid.stats.mean`; for CCP4/MRC `grid.stats`
is the file header. **Its default isosurface is `relative: 2`**, so out of the
box a map with a stale header contours in the wrong place and looks entirely
normal. protean converts against the sigma and mean measured off the voxels and
hands Mol\* an absolute value it cannot reinterpret. The reply reports the
`sigma` and `mean` used and `stated_absolute` — what the header's numbers would
have given — so a file disagreeing with itself is visible rather than silent.

**The API sketched here was not reachable.** This section proposed
`VolumeRepresentation3D` with `Volume.IsoValue.absolute(v)`. The viewer loads a
**prebuilt Mol\* bundle** (bundling from source needs >4 GB RAM), and that
bundle's global exposes `Viewer` and little else — no `StateTransforms`, no
`Volume` namespace. Checked in a real browser rather than assumed.

What works instead, and is what shipped:

```
provider.visuals(plugin, { volume: selector })   // the provider from dataFormats.get(fmt)
  → creates a `ms-plugin.volume-representation-3d` node, type 'isosurface'

then update that node's params:
  isoValue: { kind: 'absolute', absoluteValue: v }   // a plain object literal —
  visuals: ['solid'] | ['wireframe']                 // which is all IsoValue is
  alpha:   0..1
```

The useful discovery: `IsoValue` is a plain `{kind, absoluteValue}` object, so
the unreachable namespace is not needed to build one.

**Name the unit; never take a bare number.** Mol\* carries the distinction
natively, and making the caller name it is the single most valuable thing
protean can do here.

The reason is worth stating plainly, because it is not obvious and it silently
produces wrong pictures. EMDB publishes author-recommended contour levels as
**absolute** values. Most viewers contour in **σ**. EMD-30913 publishes `0.05`,
which is 3.16 σ for that map; typed into a viewer expecting σ it contours
noise, and the result looks like a perfectly ordinary bad map rather than a
unit error. A tool that makes the caller name the unit cannot be got wrong this
way; one that takes a bare number will be.

`style="surface"` vs `"mesh"` maps onto `visuals: ['solid']` / `['wireframe']`.

### 1.3 `color_surface_by_volume(surface, volume, domain, palette)` — OPEN

Local resolution: colour a density isosurface by a second volume whose values
are resolution in Å.

This is `color_by_volume` retargeted. That handler themes *structure*
components via `updateRepresentationsTheme`; here the target is a volume
representation. The `external-volume` colour theme and the `ValueRef` shape
(`{ref, getValue: () => data}`) carry over unchanged — including the two traps
already documented at `dispatch.ts:1754` and `:1761`, which cost real time to
find and apply identically here: passing the ref without the getter paints
everything default grey, and passing a palette *name* with an empty `colors`
array paints the whole surface black.

**Refuse when the grids do not match.** Two volumes with different voxel grids
can be sampled against each other without error, and the result renders smooth
and plausible while being meaningless. `wiggles_em.analysis.mapinfo` has the
grid comparison, including the tolerance (EMD-30913 reports a voxel axis as
`0.7999967`, so exact equality is wrong). Protean should call it and refuse,
not draw.

### 1.4 `volume_info(volume)` — SHIPPED (PR 69), and this section was wrong

Dimensions, min/max/mean/σ, voxel count — **read back off the parsed volume**,
not echoed from the header protean sent.

Per the repo's own rule: replies report state rather than repeat arguments. It
matters more than usual here because it is the only way a caller can convert a
published absolute contour into σ, and because a volume that parsed to something
unexpected should show up as strange stats rather than as a clean success.

**The mechanism named above — `data.grid.stats` — does the opposite.** For
CCP4/MRC those four numbers are *stored header fields* (DMIN, DMAX, DMEAN, RMS)
and Mol\* passes them through unexamined. Reading them is reading the header,
which is precisely what this section said not to do. The distinction is
invisible in a healthy file and decisive in an unhealthy one: a cropped or
rescaled map keeps whatever header nobody updated.

This was not caught by reasoning. A browser fixture was written with
deliberately false header statistics (−999 / 999 / 42 / 7) and required the
reply to match the *data*; it failed on its first run with

```
min came back as the header's false value -999.0
```

with the dimensions **correct** — so the volume genuinely had parsed, and every
number describing it was the file's claim rather than its contents.

What shipped instead: walk the voxels. Two passes, since a running-mean
sum-of-squares loses precision at 10⁷ voxels and 2× a linear scan is nothing
against the download and parse that just happened. The header's own four numbers
come back under `stated`, because a large disagreement is itself information —
it says the file has been through something.

**The general lesson, which is not about volumes.** "Read it back from the
viewer" is not the same instruction as "read it back from the data". A viewer
will happily report what the file *said* about itself. Ask what the number was
computed from, not where it was fetched from.

### 1.5 Carve — server-side, not a viewer feature — OPEN, now unblocked

PyMOL's `isomesh ..., carve=2.0` shows density only within *n* Å of a
selection. It is how anyone looks at a ligand site.

Mol\* has `selection-box`, but only inside the **volume-streaming** behaviour,
which is built for a remote density server and is a poor fit for a local file.

The better answer is that protean does not need a viewer feature at all: the
server has the map *and* biotite, so cropping a box around a selection is a
numpy slice plus a corrected origin, done before the bytes are ever sent. It
also makes the transfer smaller, which is the other problem. Do it as a
parameter on `load_volume` (`carve_around=`, `carve_radius=`) rather than on
`isosurface`, since it changes what data exists rather than how it is drawn.

---

## 2. Altlocs have to survive parsing — SHIPPED (PRs 58-64, decision 16)

Built essentially as proposed. `alt` is now a normal property
(`selections.py:182`), every conformer is loaded, and analysis resolves one
state **per site** rather than per structure.

Two things the plan did not anticipate, both worth carrying forward:

- **"Per site" is load-bearing and easy to get wrong.** Resolving one letter for
  the whole structure looks equivalent and silently deletes every atom whose
  site lacks that letter — a partially occupied ion labelled `B` with no `A`,
  or 5FJI's 11 atoms labelled `C`. That bug was written despite the plan saying
  "per site", and found by review rather than by the tests written for it.
- **Five defects followed in the same shape**, fixed across PRs 61-64: an index
  or a conformer state resolved in one place and used in another. Read them as a
  class, not as five bugs.

The original argument, which held:

`selections.py` listed `alt` as unsupported:

> coordinates are parsed keeping one conformer per atom site, so no altloc
> field survives to select on. Loading every conformer is possible but would
> make buried areas and potentials be computed over atoms that overlap each
> other

The reason is right and the conclusion is drawn too wide. It argues for the
**analysis** path collapsing conformers. It does not argue for discarding the
field at parse time, and discarding it costs two things:

- `altloc_view` cannot be hosted at all. Showing every modelled alternate,
  coloured per group with occupancies in the labels, is the whole tool.
- `occupancy_view` is quietly wrong rather than unavailable. It would report
  per-atom occupancy over a set of atoms that has already been thinned to one
  conformer per site — a plausible-looking answer to a question nobody asked.

Proposed shape:

- Keep `altloc` on the parsed atom array. One extra column.
- Analysis entry points (`interface`, `electrostatics`, `superpose`) collapse
  to the highest-occupancy conformer per site **explicitly**, at the point of
  use, and say so in their replies when they dropped anything. That is
  strictly better than today, where the collapse happens invisibly at parse.
- `alt` moves from `_UNSUPPORTED` into `PROPERTIES`.
- Selections that reach the viewer already carry every conformer, so nothing
  downstream changes.

This is the largest item here and it touches the parsing layer, so it is the
one most worth disagreeing with early.

There is a nice symmetry worth noting: `wiggles-em` exists because viewers
throw away the parts of a structure that record uncertainty, and its compendium
entry `multiconformer` is specifically about alternate conformations being
dropped. Protean currently drops them. The finding applies to us.

---

## 3. Generalise scalar colouring — HALF DONE, and not the half this predicted

PR 68 landed `ColorByScalar` in the **backend** (`backends/molstar.py`), so the
shared op exists and wiggles-em scenes can use it. What did not happen is the
other half of this section: `color_by_rmsf`, `color_by_conservation` and
`color_by_potential` are still three separate MCP tools computing their own
arrays, unchanged.

So the mechanism is factored out for scene-driven callers and not for protean's
own tools. That is a reasonable place to stop — the backend needed it and the
tools did not — but it means the "keep the named tools as thin callers of it"
half is outstanding, and the explicit-domain argument below now applies to two
code paths instead of one.

The original reasoning, unchanged:

`color_by_rmsf`, `color_by_conservation` and `color_by_potential` each compute
an array in Python and draw it. `wiggles-em` needs the same machinery for
occupancy, Q-score and per-atom spread, and adding three more near-identical
tools is the wrong shape.

Factor out the mechanism as one backend-facing action — values, an **explicit
domain**, a palette, a target selection — and keep the named tools as thin
callers of it.

The explicit domain is the load-bearing part. `color_by_rmsf` currently stretches
into `[0, 100]` because that is the uncertainty theme's range, and it chooses
relative or absolute scaling from a `scale` parameter. A shared op cannot infer
that: colouring occupancy over its observed range rather than a fixed `[0, 1]`
turns a model that is 0.95–1.0 everywhere into a full rainbow implying variation
that is not there. Whether the domain is fixed or data-derived is a property of
the *quantity*, so it has to be passed in, and it should never default to `auto`.

### The B-factor channel

Worth recording, because it is easy to assume otherwise: **Mol\* has the same
constraint PyMOL does.** The `uncertainty` size theme reads
`B_iso_or_equiv` (`mol-theme/size/uncertainty.js:20`), and it is the one
per-atom numeric field Mol\* will ramp over. `color_by_rmsf`'s docstring
already says this and takes the right way out — it builds a *display copy* with
`display.b_factor = scaled` and re-sends it, leaving the analysis copy's
crystallographic values intact.

That pattern should be the rule for every scalar view. It is genuinely better
than PyMOL's, where there is one copy of the object, `alter b=q` destroys the
originals, and `wiggles-em` has to stash and restore them. Protean pays a
re-send instead and keeps the invariant for free — a B-factor that quietly
means something else is exactly what gets read as temperature later.

---

## 4. Size by scalar (putty) — OPEN

`ensemble_spread_view` draws per-residue spread across an ensemble as cartoon
thickness. Viable in Mol\*, via the same B-factor channel: the `uncertainty`
size theme computes `baseSize + B_iso_or_equiv * bfactorFactor`, so a cartoon
representation with `sizeTheme: 'uncertainty'` over a display copy gives a
putty.

Two things to check when building it: that the theme applies to `cartoon` and
not only to ball-and-stick, and what the thickness range looks like once
values are stretched into `[0, 100]`. If it turns out not to read honestly,
the view should fall back to colour and **say** it did, rather than draw a
thickness that means nothing.

---

## 5. How these get tested

Nothing here changes the repo's testing rules, but volumes make one of them
sharper. The dominant failure mode in this codebase is code that reports
success and draws nothing, and a volume has more ways to do that than a
structure: it can parse to an empty grid, contour above its own maximum, or be
sampled against a mismatched grid. All three return cleanly.

- **Isosurface tests read pixels.** A contour level above the map's maximum
  produces an empty surface and a successful return. The only thing that
  distinguishes it from a correct render is what is on the canvas.
- **A contour-level regression test in both units.** Load a map with known
  stats, contour at an absolute value and at the equivalent σ, and assert the
  two produce the same pixels. That is the test that would have caught the
  EMD-30913 trap.
- **A negative test for mismatched grids** — `color_surface_by_volume` must
  refuse, and the test should confirm it refuses rather than confirming it
  returns something.
- **Break each new guard deliberately** and confirm the test fails, per the
  README. For volumes this is not optional: an isosurface test that passes
  against a blank canvas is the default outcome, not an unlucky one.

**What §1.1/§1.4 actually taught, now that they are built.** Three of these were
not on the list above:

- **Write the fixture so the wrong implementation cannot pass it.** The
  false-header volume (§1.4) is the whole reason that bug was found rather than
  shipped. A fixture with *honest* statistics passes against a viewer that reads
  the header and against one that reads the voxels, and cannot tell them apart.
- **Ask what a test's setup order excludes.** The volume tests all ran
  structure-then-volume, because `viewer_session` loads a structure during
  setup — and structure-then-volume is the one ordering in which a stale volume
  handle cannot appear. The bug lived in the order no fixture produced.
- **A count in a reply is not evidence the thing exists.** `volume_info`
  answered in full, with correct dimensions, for a volume `plugin.clear()` had
  already deleted, because the statistics were computed from a `data` object the
  handle map itself kept alive.
- **Test the invariant where it can actually be violated.** The provenance test
  was written against the viewer, with a fixture named
  `emd_30913_deepemhancer_sharpened.map` to bait a guess. Making the viewer
  guess left it green — the viewer is only ever sent a handle and a URL and
  never sees the path, so it *cannot* infer. The assertion had to move to the
  server, which is the only component holding the filename. **A baited fixture
  proves nothing if the code under test cannot see the bait.**

---

## Order, and what depends on what

| | Depends on | State |
|---|---|---|
| 1.1 `load_volume` + HTTP route | — | **done** (PR 69), minus `provenance=` |
| 1.4 `volume_info` | 1.1 | **done** (PR 69) |
| 2 altlocs | — | **done** (PRs 58-64) |
| 3 scalar colouring, backend half | — | **done** (PR 68) — `ColorByScalar` |
| **1.2 `isosurface`** | 1.1 ✓ | **next.** Unblocked, and the last thing refusing a wiggles-em scene |
| 1.3 `color_surface_by_volume` | 1.2 | after 1.2 |
| 1.5 carve | 1.1 ✓ | unblocked; server-side crop, independent of the viewer |
| 3 scalar colouring, tools half | — | outstanding: the three MCP tools are still separate |
| 1.1 `provenance=` | 1.1 ✓ | **done** (PR 72) — landed before 1.2, as intended |
| 4 size by scalar | 3 | smallest once 3 is finished |

**Do 1.2 next.** Everything it needed is on `main`, and
`backends/molstar.py:795` currently refuses an `Isosurface` op by pointing at a
branch that no longer exists — so the refusal message needs correcting whoever
picks this up, and its advice to convert σ "against that map's own header" must
not be followed.

The original note said "**2 is the one to argue about first**, since it changes a
parsing decision other analysis relies on". That was right, and it is settled;
what the argument produced is decision 16 and the per-site rule in §2.

Sections 1–4 are what `wiggles-em` needs from a backend. Sections 1 and 3 are
worth building whether or not that lands — and §1.1 has already paid for itself
outside cryo-EM, since any 2Fo-Fc or segmentation map now loads too.
