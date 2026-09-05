# Changelog

Notable changes to protean. Versions follow [semantic versioning](https://semver.org);
nothing is released yet, so everything below is unreleased.

## Unreleased

### Analysis

- **`sasa()`** — what the solvent reaches, per residue, plus how deep the rest
  sits. Three numbers from one Shrake-Rupley pass: area in A^2, relative
  exposure (that area over the most the residue type could have), and depth in
  angstroms. **Relative is the one to draw**: a tryptophan showing 60 A^2 is
  buried and a glycine showing 60 A^2 is wide open.

  It can exceed 1 — a terminal residue has surface a Gly-X-Gly reference does
  not, and 1UBQ's C-terminal glycine reads 1.42 — and clamping would report the
  most exposed residue in the structure as merely ordinary. Depth is a proxy
  and says which one: distance to the nearest atom the probe reached, not to a
  solvent-excluded surface.

  Verified on 1UBQ: 4802 A^2 against a literature ~4800, and the most buried
  residues are ILE3, VAL5 and ILE23 — its actual hydrophobic core.

  Three things that were true and surprising. **A coarser probe reaches more
  area, not less**, because accessible area is measured at the probe centre;
  what falls is what it can squeeze into. **biotite's default radii raise
  rather than default** for a ligand the chemical component dictionary has
  never seen, and for any atom whose *name* starts with H regardless of its
  element. And **hydrogens inflated depth for every residue in a protonated
  file** until depth was restricted to atoms that carry a radius — on the NMR
  structure 1L2Y, a residue 81% exposed read 0.52 A deep.

### Fetching and parsing

- **AlphaFold URLs are asked for, not built** (backlog 33). The template was
  pinned to `model_v4`, which the database retired, so every AlphaFold fetch
  failed with "Not found upstream" — which reads as "no such protein".

  Bumping the version would have been the wrong fix. **The template was wrong
  in shape**: P0DTC2, the SARS-CoV-2 spike, is served as
  `AF-0000000365840314-model_v1.cif` — an internal numeric id, no `-F1`
  fragment, version 1 while its neighbours are on 6. The backlog had concluded
  such accessions were absent from the database; they were not.

  The whole suite passed with the tool completely broken, because it mocks the
  upstream. There is an opt-in live test now, behind `PROTEAN_NETWORK=1`.

- **A predicted model keeps its analysis half** (backlog 34). A file that
  declares no biological assembly used to load in the viewer and lose every
  selection and analysis tool to "File has no 'pdbx_struct_assembly_gen'
  category". The deposited coordinates are now loaded with a note saying so.

### The viewer

- **A refused view says why, on the page.** Charlie, clicking Scaffold on a
  crystal structure: *"Scaffold doesn't show anything."* It shows a refusal —
  and the menu was putting that refusal in a `title` attribute on the Views
  button, a tooltip on a control the person had already moved away from, so
  what the click looked like was a click that did nothing.

  The reason now renders in the menu, under the item that was asked for, and
  clears when another view is asked for or a fresh catalogue arrives. Several
  of these are the most useful sentences protean writes: `scaffold` explains
  that pLDDT and the B-factor are the same mmCIF column with opposite polarity,
  that there is nothing to cover because every atom here was observed, and that
  `putty` answers the question actually being asked.

  The menu moved to `viewer/src/view-menu.ts` so it could be tested at all —
  `main.ts` boots Mol\* at import time, which put protean's one human-facing
  control out of reach of a suite running in jsdom. Nine unit tests, and one
  that drives a real page.

- **Mol\* is bundled from source rather than loaded as a prebuilt global.**
  `viewer/src/main.ts` had said since the first commit that bundling "needs
  >4 GB RAM, the prebuilt bundle needs none", and a great deal followed from
  it — `docs/views.md` §5.9 put cross-hatching in Pillow rather than in a
  render pass *because of* that sentence.

  It is wrong. `>4 GB` is the cost of building Mol\*'s own repository from
  TypeScript; the published package ships `lib/` as already-compiled ESM with
  the GLSL inlined as JavaScript strings, so Vite bundles JavaScript and never
  compiles Mol\*. Measured on the change: **1.07 GB peak, 5.35 s**. The bundle
  goes 4,800 kB to 5,147 kB.

  This unblocks a custom post-processing pass and mesh-based representations,
  neither of which is built yet.

  **It fixes Mol\*'s backdrop artwork as a side effect.**
  `extensions/backgrounds/index.js` imports `./images/cells.jpg` as a module —
  a bundler-resolved import that the prebuilt UMD had frozen into a relative
  URL protean never copied. That 404 does not degrade quietly: it leaves
  `updateBackground()` awaiting a promise nothing resolves and **permanently
  wedges `snapshot()`**. All seven images are now emitted and referenced.

  **And it found that the packaging tests have never run in CI.** They skip
  when `static/index.html` is absent, and the python job never built the
  viewer — so every test asserting a wheel can actually draw has been skipping
  silently. Bundling removed `static/molstar.js`, the file
  `test_the_wheel_carries_the_viewer` looked for, and CI stayed green. That job
  now builds the viewer, and the assertion checks for Mol\*'s *code* rather
  than a filename.


### Views

- **`divisionist` ships — `brushwork()`'s second look, Seurat's mechanism.**
  Dabs, not a continuous filter: each one coloured once at its own centre,
  never re-sampled per pixel, at full coverage — foreground and background
  both, with no gap back to the smooth render underneath. That coverage was
  a change from the original plan, made live against real renders rather
  than decided in advance: *"the image should be only points, not points
  over a ribbon."*

  A single jittered lattice kept a visible grid no matter how hard it was
  jittered — the partition stays periodic underneath the jitter, and that is
  what the eye catches. Fixed by unioning nine independently rotated, offset
  and scaled lattices, separated by the golden angle. A residual diagonal
  ripple survived that fix, visible to the eye but invisible to an isotropic
  FFT check that averages within radius bands rather than by angle — traced
  to the classic `fract(sin(dot(p, vec2(127.1, 311.7))))` hash, which carries
  a well-documented directional bias invisible at the coarser, interpolated
  uses of it elsewhere in the shader. Non-uniform dab size needed a power
  diagram, not just a per-dab radius, or a bigger dab still loses to a
  nearer small one and its edge clips against its neighbour's boundary.

  Full technical account in `docs/soft-matter-status.md` §1b.

- **`painting` goes back to the Dutch Master, and keeps only that look.** Four
  plates of one scene were compared and the first was chosen: *"original is
  still the best. Keep it, remove all the rest."* `brushwork()` now offers
  `chiaroscuro` and `off`; `preset("painting")` builds the dark umber ground,
  studio rig and cast shadow the look was made for. The `spring`, `poster` and
  `orchard` *palettes* stay registered and are still reachable through
  `color()` — only the looks are gone.

- **`edgeBreak` is removed.** It shipped at 1.0 on every look, wired to the
  shader every frame, under a commit message that said *"This has never
  produced a picture. It typechecks and nothing more."* A commit message is a
  claim about intent; the default is the claim about state.

- **The abstraction is kept general and set to the value that does nothing.**
  The Kuwahara sector weight was arithmetically inert — the published formula
  is for 0-255 values, and on the [0,1] the shader carries, its whole range at
  hardness 8 is 1.0000000 to 0.9999847. The reference variance is a uniform
  now, and `chiaroscuro` asks for the ungoverned form explicitly. It is
  bit-for-bit the old behaviour, verified at every sample and then on the plate
  by identical MD5. The guard survives with a real subject: it asserts the
  formula *can* discriminate, and separately that this look chooses not to.

- **Reverted with it: two corrections that were only corrections against an
  intent nobody held.** A relight costing 14.3% of the mean painted pixel and a
  one-sided weave costing 4.3% were replaced with mean-neutral forms during the
  brightening; the darkness they removed is the darkness that was chosen. The
  glaze goes back from a tint to a multiply for the same reason — a tint floors
  the darks at 0.2.

- **Kept regardless:** the `atan(0.0, 0.0)` guard, whose absence made the pass
  return a transparent pixel and got whole captures refused as incomplete.

### Print finishes

- **`hedcut` bows around the form, and three dot finishes are new.** Answering
  the second half of "Hedcut is also way too coarse. They read like bad modern
  art" — the size half shipped in #143, the mechanism half is this. `hedcut`
  keeps its one direction and its swelling stroke and pushes the ruled plane
  aside with the light the render already carries, so the rules bend around
  each atom instead of running through it. Chosen from a bracket of five
  plates. It stays a *control* for the rim guard, measured: the bow moves the
  rim lift by 0.002, so it is a texture rather than a depth cue.

- **`dotty`, `dotty-mixed` and `dotty-confetti`.** A jittered dot lattice
  where each disc grows with the tone. The two colour variants ink that one
  field rather than laying a second over it — same lattice, same pitch, same
  dot size, each cell taking either the near-black key or one of three print
  colours — so the plates partition one mask and never cross. They differ only
  in how much colour: about a fifth of the ink against about half.

  They sort by the hue already on screen and claim nothing about it, so they
  follow whatever you coloured by and a greyscale render gives them nothing to
  sort. The reply now carries `chromatic`, the share of ink that came off a
  plate other than the key, because otherwise that case returns a path, a
  success and a sensible ink fraction describing a picture with no colour in
  it.

- **A finish can ask for its capture to be taken larger.** A plate has exactly
  two grey levels — `ink_mask` recovers the mask bit for bit *because* of it —
  so an antialiased edge cannot be drawn, only averaged out of a bigger plate.
  The four new finishes are captured at 2x and averaged down; the other five
  are unchanged, and `spot-ink-plates` must stay hard-edged because its
  boundaries are a category rather than a shade. The size ceiling is
  re-checked against the pixels actually captured, refusing above 5477 px
  rather than letting a `DecompressionBombError` escape as a tool failure.

- **Three guards could not see what they checked.** The mark-size guard
  resolved the frame diagonal as `hypot(w, h)` where the product uses
  `sqrt(w * h)`, so it reported `engraving` clear of the grain floor by 6%
  while the finish drew the floor exactly — and it checked `pitch` only, so
  the stroke finishes were never checked at the comparison size at all. With
  both fixed, four of the six shipped finishes turned out to be pinned there:
  the pairwise comparison had never seen the marks the product draws. The rim
  guard named `hedcut` in prose as its control, with a number (+0.086) that no
  longer matched the code (+0.0668) and a finish that was not even the highest
  control (`engraving`, +0.0922). Both are now derived from the field carrying
  the mechanism, and the rim bar is two-sided so it cannot drift.

- **The hatching now follows the form, in two treatments.** `linear-hatch` is
  new; `cross-hatch` keeps its name and is rebuilt on the same mechanism.
  Answering "the hatching should have separate linear and cross treatments.
  Hedcut is also way too coarse", which turned out to be two independent
  defects rather than one.

  **Coarseness was only a number, and no test could see it.** An atom is about
  40 px on a 1890 px plate, and `apply_finish`'s `max(4.0, longest / 110)` is
  17 px there and 34 at 600 dpi — two or three lines per sphere, so every
  sphere vanished into a grid. Every guard in `tests/test_hatching.py` draws at
  240 or 480 px, where that expression returns its own 4 px floor: the suite
  had only ever drawn the finish at its finest while the product shipped its
  coarsest. Stated as strokes per feature, the `_dome` fixture is a 173 px
  sphere at a 4 px interval, 43 to 1, against the product's 2.35 to 1 —
  eighteen times better sampled. A finish can now declare its own interval, the
  way `_Survey` already declares `pitch` and `line`.

  **Form-blindness was the mechanism, and fineness does not touch it.**
  Re-drawn at 17, 12, 8, 6, 4, 3 and 2 px, the old `cross-hatch`'s ink landed
  on the form's edges no more often than chance every single time: +0.014,
  +0.001, +0.011, +0.010, +0.011, +0.012, +0.003, against `engraving`'s +0.202.
  It ruled three fixed angles over the frame regardless of what was underneath,
  at every scale.

  `_Lozenge` draws marks that are level sets of a ruled plane warped by the
  recovered light — flat frame, straight rules; a sphere bows them around it,
  the way ruled lines on a rubber sheet bend when a ball is pushed through from
  behind. At constant *duty* rather than constant width, because the warp
  changes the local interval and a constant-width stroke would then change its
  coverage wherever lines bunch. And the stroke swells where the lighting turns
  over — the burin's burr, and what draws the seam where one atom passes in
  front of another. **That swell is the entire rim mechanism**: the assumption
  that a tone-driven width would find the rims by itself is false, because mean
  darkness over the steepest decile is 0.625 against the whole subject's 0.622.
  The rim is not darker. It is only steeper. Measured, the pair scores +0.259.

  The two treatments differ past `hold`: the linear opens a second thread half
  an interval over at the same angle, so the whites split rather than the
  blacks closing; the crossed one lays a second family at -41 degrees, into
  lozenges that shear as they pass over a dome. **They are not one drawing at
  two strengths.** An earlier version shared a carrier angle, which made them
  bit-identical below `hold` and left them disagreeing on 0.064 of a real
  cartoon subject; with their own angles they disagree on 0.433 / 0.401, where
  the old `cross-hatch` against `hedcut` is 0.466 / 0.426.

  6 px for the linear and 4 for the crossed, chosen by looking at a 6/4/3
  bracket at plate size rather than from print convention. The floor is about
  3: at 2 px the lattice beats against the pixel grid and ink jumps from 0.42
  to 0.64 of the subject with no change in tone at all. `hedcut` keeps its
  mechanism, which is its style rather than a defect, and takes only the half
  of the complaint that was about size: 5 px.

- **A guard the numbers could not provide.** Setting `relief` to 0 leaves
  straight ruled lines with the swelling still on, and that mutant scores
  **every scalar the real finish does** — ink 0.506 against 0.507, rim lift
  +0.259 against +0.259, tone fidelity 0.934 against 0.936 — while the picture
  goes from strokes that bend over each dome to a flat ruling with dark blobs
  at the seams. A scalar over a whole frame cannot see a local geometric
  property.

  So the guard is a differential against the finish with its own mechanism
  removed, which is the arm `test_shuffle_differential.py` uses for the same
  reason. Sabotaged, it reports 0.0000; the warp moves 0.42 of a real spacefill
  subject and 0.32 of a cartoon.

  The three new tests draw at 1890x956 on a field of small overlapping spheres
  rather than on `_dome`, and the fixture carries a guard of its own. Lit as it
  was first written its median tone was 0.72 against a real capture's 0.32,
  coverage came out at 0.04, and every finish scored at chance for want of any
  ink on the page — which reads as a broken finish and was a broken fixture.

- **`capabilities()` now reports the print finishes.** It already reported
  `presets` for exactly this reason — composed in Python, so the viewer cannot
  report them — and the finishes were left out. The only ways to learn the list
  were to read `snapshot()`'s docstring or to guess a name and read the error,
  which is discovery by exception offered to a caller who cannot see the file
  it is asking for. The docstring guard stays, because a name in a list tells
  nobody whether `cyanotype` is blue.

- **`engraving` was missing from its own figure.** It shipped and never reached
  `print-finishes.png`, the gallery's finish table, the cookbook or the README,
  all of which showed four finishes and named four while the product offered
  five. The figure now derives its list from `FINISHES`, and captures at 900 px
  rather than 520 — at 520 a hatch resolves to about 2 px and every finish
  turns into the same grey, which is the figure claiming the finishes are
  indistinguishable when they are not.

  Two hardcoded finish lists in the suite are now derived as well. Adding a
  finish failed four tests that had nothing to do with it.

- **`engraving` — depth-cued line work, in ink on paper.** A fifth finish, and
  no new rendering code: it is the engine that already draws `cyanotype`, with
  the paper set white and the ink black.

  `_Survey` was written to draw a survey sheet and has always been a depth-cued
  renderer. It contours the *recovered lighting field* — each element's colour
  divided back out of the render — and holds constant line width by dividing
  the residual by the local slope, so a steep face gets a thin line rather than
  a fat smear. The marks follow the form because they **are** isolines of it.
  That is what the two hatchings cannot do: `cross-hatch` and `hedcut` rule
  strokes at a fixed angle regardless of what is underneath, which is why
  neither reads as having depth.

  Fourteen levels rather than cyanotype's five, chosen by rendering
  5 / 9 / 14 / 20 / 28 on carbonic anhydrase at plate size and looking, not from
  print convention.

  Every fifth contour is drawn heavy — a relief map indexing its own levels.
  That weighting was tuned when `bands` was 5, where it fell on the silhouette
  alone; at 14 it lands mid-dome as well. Kept deliberately.

  `brightest` is raised from 0.975 to 0.9975. The clamp flattens everything
  brighter to one elevation, and measured on the capture it takes **3.27% of
  molecule pixels** — a small area that sits on the summit of every single
  atom, so each dome lost its innermost ring.

  **The test that compares finishes could not have seen this one.** `_grain`
  resolves its lattice step as `max(2.0, diagonal * pitch)`, so at the suite's
  240 px fixture every fine finish clamps to the same 2 px floor and draws an
  identical lattice: cyanotype and engraving disagreed on **0.0000** of the
  frame at 240 px and on **0.4811** at 1200 — a failure reporting a perfect
  score. The comparison now draws at 480, the first size past the cliff, and a
  new guard asserts no two finishes share a resolved grain step, so the next
  fine finish fails on the mechanism rather than on a number.


### Views

- **`painting` is an oil painting now, and the paint is real.** Charlie, from
  using the viewer: *"Painting just reproduces felt."* It did — both drew
  `not solvent` as spacefill, their carbons differed by 13 counts of 255 and
  their grounds by exactly 8, which `tests/pixels.py` counts as identical, so
  protean's own differ could not tell the two views apart.

  It is now a ribbon in earth pigments on a warm dark ground, painted by
  **`brushwork()`** — protean's own GPU render pass, patched into Mol\*'s. The
  first thing built on #137's decision to bundle Mol\* from source, and the
  thing the print finishes could never be: the viewer shows the finish, and
  `snapshot()` returns what the viewer is showing.

  - **The finding that shaped it: abstraction alone is not a painting.** The
    first version was anisotropic Kuwahara and nothing else, which is what a
    painterly filter is made of — and it gave back a clean cartoon with a
    softer silhouette. Kuwahara abstracts texture that is *already there*, and
    every published demonstration runs on a photograph. A Mol\* cartoon is a
    smooth surface under a smooth light. So the paint is made rather than
    found: noise dragged along the flow field for the bristle, the same field
    read as a height and relit by a raking light for the impasto, a woven
    ground under both.
  - **Three seams, not one.** `ImagePass` owns its own `DrawPass`, so patching
    the canvas's instance would paint the screen and leave every capture plain
    — with a success message on it. And the live canvas accumulates four
    jittered sub-frames where a capture accumulates sixteen, so a finish
    applied *inside* that is averaged away by different amounts on screen and
    in the file. So the pass sits after accumulation, on all three routes.
  - **`brush_size` was almost a no-op and the reply hid it.** It scaled the
    abstraction radius, which over a textureless render changes nearly
    nothing; `fine` and `broad` came back as the same picture with different
    numbers. Every length moves together now, and the guard walks all three.
  - **The new `pigment` colour theme painted the molecule solid black** on its
    first render and reported itself applied. It is Mol\*'s own
    secondary-structure theme wearing earth colours, and that theme reads
    `props.saturation` and `props.lightness` — a props object carrying only the
    colour map hands it two undefineds and every channel comes out NaN.
  - **`snapshot(crop=True)` is refused while a look is on.** `autocrop` finds
    the molecule by testing each pixel for exact equality with the background
    colour, and a painted ground leaves none to match, so the box would come
    back as the whole frame while the reply said it had cropped.

  **It shipped as a Dutch Master and that was the wrong idea.** Charlie, on the
  plates: *"way too earth tone, too dark ... brighten the mood. Make it
  joyful."* `painting` is coral against sky on a cream ground now — `spring` —
  with `poster` and `orchard` beside it and `chiaroscuro` still available.

  Chasing the gloom found four defects, every one of which reported success:

  - **The pass was not running a Kuwahara filter.** Its sector weight is
    Kyprianidis and Döllner's, which operates on 0-255 values; on the [0,1]
    values a shader has, the exponent annihilates it. At `hardness 8` the
    weight's entire dynamic range across every spread a luminance can have is
    1.0000000 to 0.9999847 — so every sector was weighted the same, the
    least-variance selection never happened, and an anisotropic Gaussian blur
    had been wearing the name of an abstraction. Two comments in this repo
    described behaviour that arithmetic cannot produce.
  - **The impasto relight took 14.3% off every painted pixel**, quoted as an
    absolute range rather than as contrast. A flat pixel suggests 7.3%; the mean
    is twice that, because a textured surface tilts 53° off the screen and a
    third of it has its light clamped to zero.
  - **The "edge darkening" was a 21% global dim**, keyed on anisotropy — which
    measures a gradient's *shape*, not its strength, so a smoothly shaded ribbon
    saturated it over 87% of the subject. It is called `shade` now.
  - **The shadow could only ever darken and its band never fired.** A multiply
    by a colour cannot lift; the band was a literal tuned around a subject at
    luminance 0.18-0.40. Both are look fields now and the shadow tints.

  And the biggest lever was not in the pass: the studio rig with its cast shadow
  was taking the colour out of the palette before the paint saw it. Same palette
  and look, only the light changed — subject luminance 112 → 162, saturation
  112 → 146.

  `felt` is untouched, as asked. It shares no code path with the pass, and its
  numbers were the target: ground 237, subject 114, saturation 41.

  **Then four more rounds, and each was a defect rather than a preference.**
  *"Crumpled foil or mylar"* was the fake impasto: a relit height field reads as
  metal, always, because relighting is what tells an eye it is looking at a
  surface with a normal. *"Still like crumpled mylar"* was the same illusion
  without any lighting — random brightness per mark, on marks that covered only
  part of the ribbon, which is light catching facets at random angles. The marks
  tile now and their variation lives in chroma. *"The strokes aren't obvious and
  the direction is haphazard"* was **caused by brightening the picture**: the
  flow field reads the shading gradient, so opening the light up flattened the
  very signal the marks steer by, and one change had made both problems. The
  structure tensor takes a second gradient on depth, which does not care how a
  scene is lit.

  The method worth keeping: **render the field rather than reasoning about it.**
  Direction as red and green, confidence as blue, straight out of the shader.
  One build settled a question three rounds of inference had got wrong.

  What is not built: `divisionist` (Seurat) and `impasto` (Van Gogh), the other
  two Charlie named. The engine is shared and waiting. And `painting`'s brush
  volume is still open — `spring`, `orchard` and `poster` bracket it at 0.55,
  0.85 and 1.2 rather than converging on a guess.

- **`cinematic` is withdrawn.** Nineteen presets, not twenty. It was a
  near-black ground and a rim light, and the only thing it did that nothing
  else does was turn on a shallow depth of field — which
  `effects(depth_of_field=True)` reaches directly.

  Its reading of "cinematic" also leaned on a cast shadow: it asked for
  `shadow=True` on top of ambient occlusion, and measurement has since
  established that **no cast shadow is possible at any setting**, because
  Mol\*'s shadow pass is screen-space self-shadowing and its background is a
  flat colour with no geometry to receive one. So the effect it was named for
  was never there.

  `_preset_cinematic` is kept dormant rather than deleted, unreachable from
  `_PRESETS`, so the one idea worth saving stays visible.

  Two things this exposed. The guard that proves a preset states all six effect
  toggles rather than inheriting them was written with `cinematic` as its
  polluter, which tied a general invariant to one preset; it now turns the blur
  on directly, which is what it always meant. And
  `docs/benchmark/protean_corpus.py` has been probing `preset("cinematic")`
  expecting a refusal for as long as the preset existed — an adversarial probe
  that was wrong the whole time and is now right.

- **`conservation_view()` and `electrostatic_view()` — the two answers that
  needed more than one call.** protean had six one-call views and two analyses
  that were only ever useful in sequence: `conservation()` then
  `color_by_conservation()`, and `electrostatics()` then a surface then
  `color_by_potential()`. Running half of either gets you numbers with no
  picture, or a picture with nothing painted on it.

  Both halves stay reachable on their own, and each view's reply lists the calls
  it made, so nothing here is available only through the composition.

  **Neither view restates a judgement the half it wraps already made.** The
  first draft of `conservation_view` carried its own shallow-alignment
  threshold of 50 sequences, beside the `SHALLOW_MSA` of 10 that
  `analysis/conservation.py` has always used — two numbers for one idea, which
  disagree at every depth between them. It now raises the scorer's existing
  `warning` to the top of its own reply instead. `electrostatic_view` does the
  same with the Coulombic fallback's caveat: the whole point of a one-call view
  is that nobody reads the half they did not call, so a caveat left nested is a
  caveat nobody sees.

  `electrostatic_view` takes `spacing` and `padding` because solving cost goes
  as the cube of 1/spacing, and on a large assembly that is the knob you need.

- **`lens()` — the projection, and how far the distance fades.** Two stock Mol*
  parameters protean had never exposed. `projection` is `perspective` or
  `orthographic`; `fog` is 0 for off, or 1 to 100.

  Orthographic is the projection of technical illustration: no convergence, so
  two atoms the same size are drawn the same size wherever they sit in depth. A
  helix down its axis is honest in orthographic and subtly tapered in
  perspective. Measured against perspective on 1UBQ spacefill: 0.1066 of the
  frame, no tolerance.

  **Fog has been on this whole time and has never been visible.** Mol*'s
  default is on at intensity 15, so every figure protean has ever produced
  carries it — and measured with no tolerance, 5, 15 and 25 are *bit-identical*
  to fog off. The first flicker is at 40 (0.00009); it reaches 0.026 at 60 and
  0.103 at 100. The default is not a mild version of the effect, it is the
  absence of one, which is the reason this is worth exposing rather than
  leaving alone.

  `cameraFog` is a **mapped** parameter — `{name, params}` — and Mol* accepts a
  bare `{intensity}` without complaint while leaving the fog exactly as it was.
  The reply is therefore read back off `canvas3d.props`, never echoed. That
  matters more here than usual: `fog=0` produces no pixel change at all, so
  read-back is the only possible evidence that off ever happened.

  Neither knob carries data. Fog's channel is distance from the camera — a
  property of where you are standing rather than of the molecule — so there is
  nothing to permute across atoms and a shuffle arm is the wrong instrument.

  **One guard was nearly written from reasoning instead of measurement.** The
  intuitive depth-cue test is that fog changes fewer pixels than a global dim
  would, since only the far ones fade. Measured, that is false: fog at 100
  moves **0.9994** of the drawn pixels, because even the nearest atom has some
  depth. What separates a depth cue from a dim is the *spread* of the shift —
  mean 46.35, standard deviation 25.26, a spread of **0.545**, against 0.000
  for a uniform dim of the same average strength.

- **`boil(trails=True)` — the boil, held open on one plate.** Every pose
  accumulated into one long exposure written as `exposure.png`, the newest
  sharp and the ones before it fading behind it.

  **It makes `boil`'s channel visible in a still.** How far an atom wanders
  already follows how sure the data is about it, and that binding is real and
  separately tested — but it is invisible in any single frame, because one
  frame of a boil is just the molecule slightly displaced. Held open, certainty
  becomes shape: a confident core stays sharp and a loop the data is guessing
  at smears. The reply carries `smear`, which reads **0.0 exactly** when
  nothing moved, so a structure whose column is flat is told rather than handed
  a picture that looks fine.

  Decay rather than a flat average, and that is the whole difference between a
  trail and a blur. Averaging leaves the last pose at a quarter strength and
  the subject is a smudge; weighting it fully gives the smear a head and a
  tail. The ground is read off the capture's own corners — ink darkens paper,
  phosphor brightens a dark screen, and a caller should not have to tell a tool
  which of those their scene is.

  **A bug its own tests could not see at first.** On a transparent capture,
  untouched pixels carry RGB `(0, 0, 0)`; faded towards white they were still
  dark enough to win a `minimum`, so an older frame's *background* composited
  its own blackness over the newest pose and turned a red atom to mud. Every
  fixture in the suite was opaque, so nothing could observe it. `smear` reading
  exactly 1.0 — the whole drawing changed — is what gave it away.

- **`snapshot(finish="spot-ink-plates")` — a two-colour press, and the first
  finish that carries data.** The frame is sorted into colour families, each
  family screened onto its own plate at its own angle, and the plates printed a
  little out of register so the inks cross in fringes along every boundary and
  make a colour neither carries alone.

  **What it binds is which plate a region prints on** — a category rather than
  a shade, and that is the point. A shade-driven binding sits downstream of the
  lighting rig, so the screen converts *shading* into dot area and the
  measurement never reaches the page: that is the trap `docs/bakeoff.md` fell
  into. A plate assignment cannot be quantised away, because shading multiplies
  brightness and leaves hue alone.

  **The capture is coloured by element for the one frame, and the scene is put
  back.** A finish reads pixels and cannot know what a hue was *made* to mean,
  so "a plate per element" could only ever have been a hope about how the
  caller had coloured their scene. `snapshot()` asks the viewer to apply
  `element-symbol` for the capture and restores the previous theme in a
  `finally`; the reply carries `separated_by` and `scene_restored` so neither
  has to be inferred from the picture.

  The first version read whatever hues were present and claimed only "a plate
  per colour family", on the grounds that a capture tool silently changing a
  caller's scene is a worse surprise than a narrow claim. That is true and it
  is answerable: restoring removes the surprise and keeps the guarantee.

  The channel is proved by taking the colour away: the same subject reaches the
  paper in two inks and their crossing, and its own greyscale reaches it in one
  ink and nothing else — categorical, not a margin. Measured on myoglobin:
  madder 0.261, indigo 0.083, crossing 0.010 in colour; madder 0.364 and no
  other ink in greyscale. `hedcut` is the control at 0.0022 of the frame.

  **The crossing colour very nearly never happened.** Family assignment is
  exclusive, so shifting each plate's *screen* off register moves its dots and
  leaves the regions pinned — no two plates could cover the same pixel, and the
  overlap was declared in the palette, described in the docstring, and reached
  the page zero times. Found by counting what actually printed. The separation
  now travels off register with its screen.

- **`snapshot(finish="cyanotype")` — a blueprint, and the third finish.** White
  on Prussian blue, and the first one that is not an engraving: it **contours**
  the shading rather than hatching it. The render's lighting is read as
  elevation, so every atom comes out as a set of nested rings and the frame
  reads as a survey sheet.

  **A drawing style carrying no data, and it says so** the way `felt` does. No
  shuffle arm, because it makes no claim one could test.

  Picked from a four-way bake-off — drafting rules, contours, photographic
  grain, halation — judged on the rendered pictures rather than on the
  descriptions. **An opaque background decided it**: all four look competent
  over a transparent capture, and on an ordinary grey field two dissolved into
  a texture of their own making.

  It needed three things a hatch did not. The element colour has to be divided
  out, or the rings count per element rather than per atom and the sheet reads
  as noise. The levels are spaced for a sphere rather than evenly in
  brightness, or they crowd into a rind at the rim and leave the summit bare.
  And it needs grain, because a contour is an edge and a flat tone has none —
  a pure contour finish reads zero ink at every step of the ramp.

  `FINISHES` now holds two families rather than one style: `_Engraving` bands
  the frame and fills each band with strokes, `_Survey` contours it. Both
  declare their paper, their ink, and how many tones they separate; the mark
  making moved onto the style, so `apply_finish` builds one description of the
  frame and lets the finish draw.

  One knob was prototyped and **dropped rather than shipped**: a floor under
  the heavy contour's width. It fires only below about 700 px, protean captures
  at 1051 and up, and at the size where it does fire the difference could not
  be seen in a 3x comparison.

- **The finish route grew a base, before there is a third finish.** No new
  look. `snapshot(finish=)` hosts cross-hatch and hedcut, both of which print
  black on white, and three separate pieces of the route had that coincidence
  written into them as though it were a rule:

  - **The ink fraction asked whether the red channel was zero.** That is a test
    for black wearing a disguise. It is the one number the reply carries so a
    caller who cannot look at the file can tell a good print from a solid mass,
    and for a finish printing in any other colour it would have answered
    "blank page" or "solid page" at random. There is now one definition of
    where the ink is — `ink_mask`, read by the measure and by the tests — and
    it is **exact**: `apply_finish` writes each pixel as either the paper or
    the ink and nothing between, so "not the paper" recovers the mask it drew
    bit for bit rather than estimating it within a tolerance. Measured, that
    also took the cost of the measure at 20 MP from 425 MB back to 180.
  - **A finish now declares its own paper and ink** rather than having white
    and black compiled into the compositing step, and a malformed colour is
    refused where it is written. Left to numpy, a two-channel paper surfaces as
    a broadcasting error and a channel of 300 as an `OverflowError`, both from
    inside a finish that has already been handed a figure-resolution capture.
  - **The name is checked before the render, not after it.** A finish is
    applied to the finished PNG, so a mistyped name cost a full
    figure-resolution capture — up to a hundred seconds — before anything
    looked at the string. The refusal is a `ValueError` rather than a
    `KeyError`, because `str(KeyError(msg))` is `repr(msg)`: the caller had to
    strip the quotes back off, and a name containing one arrived mangled — a
    finish named `a'b` reached the model as `Unknown finish "a\'b"`.

  Three tests could not have caught any of it, and were the reason to look.
  The headline one, whose docstring calls itself "the whole claim of the
  technique", **passes for a finish that hands its input straight back**: white
  takes no ink, black fills in, and a sorted list tolerates a run of identical
  values in between. It now asserts that a finish separates tone into the
  `bands + 1` levels it declares — measured at 5 for cross-hatch, 7 for hedcut,
  and **2 for a passthrough**. Over every tone rather than a sample of them:
  at the step of 15 this was first written with, a finish declaring 12 bands
  lands in 12 of its 13 and one declaring 14 lands in 13 of its 15, so the
  assertion would have failed a correct finish and blamed it for the test's
  own sampling. "The two finishes differ" named the two that
  existed, so a third registered with fields the code ignores would have
  rendered byte-identically to one already there while the test went on
  passing; it now runs over every pair, compares where the ink is rather than
  what colour it is, and a duplicate reads exactly 0.0 against a 0.1 floor.
  And the suite's own ink measure was a second copy of the shipped one's
  mistake, so the two agreed because both had been written from the same wrong
  idea; the tests now call the shipped function.

  Every `FINISHES` key is asserted present in `snapshot`'s docstring, which is
  the only place a finish is discoverable — `capabilities()` does not report
  them, so one left undocumented exists for a reader of the source and for
  nobody calling the tool.

  `docs/bakeoff.md` argued for exactly this after writing a second finish
  reproduced the paper-threshold bug from scratch: "the constant and the guard
  belong to the route, not to any one finish".

- **The output path is checked before the render too.** Same defect as the
  finish name, one argument along: `_writable` refuses a destination holding
  something that is not a figure, and it ran after the capture — so pointing a
  600 dpi double-column snapshot at `notes.txt` paid for the whole render
  before being told no. It is still checked at the write as well, because a
  render takes up to a hundred seconds and a file can appear inside that
  window; checking early must not mean checking only early. The test for that
  **passed with the late check deleted** until it was rewritten to make the
  file appear *during* the render — a mutation found it, not review.

- **The paper cutoff is 0.96, and two documents said 0.94.** It has been 0.96
  since the finish shipped and 0.94 was never in the code, so both were
  describing a number that never existed.

- **`felt` — felted wool, and the first thing out of the soft-matter plan.**
  All-atom spheres in a dyed-wool palette, no speculars, a fibrous surface and
  a soft halo layer at 1.12x. **Shipped as a style, not as a treatment**: the
  plan's SM-01 binds surface area to fiber length, `docs/bakeoff.md` built that
  and could not read it, so the docstring says it carries no data rather than
  implying a measurement.

  It still argues something. A hard glossy shell asserts that the van der Waals
  surface is a boundary; it is where a probability fell off to a threshold
  somebody chose, and a fuzzy edge is the more honest picture of that number.

- **`material(bumpiness=, bump_frequency=)`**, which also fixes a control that
  was dead. `bumpiness` had been pinned to zero and undocumented on the grounds
  that it "does nothing unless bumpFrequency is above 0, and that defaults to
  0". Counted across the registry rather than sampled: eleven representations
  declare the parameter and **seven default non-zero** — spacefill,
  molecular-surface, gaussian-surface, orientation and polyhedron at 1, cartoon
  and putty at 2. Four default to zero and five declare none. Pinning
  `bumpiness` killed the control everywhere, and on those seven it would have
  worked with no other change.

  Three things measured rather than assumed: the shader needs *three* non-zero
  values and not two; **frequency is fineness, so raising it makes a surface
  read smoother** (0.036 of the frame moves at frequency 1, 0.018 at 3, 0.004
  at 6); and a cartoon is too little surface to test a bump on at all, which is
  why the test draws its own spacefill.

- **A `jitter` size theme**, hash-based rather than random, because an RNG gives
  each symmetry copy of an atom a different radius — which reads as a broken
  structure rather than as a texture, and changes on every reload.

- **`painting` and `richardson`, the last two entries in the catalogue**
  (docs/views.md §5.9). `painting` is all-atom spheres in a gouache palette
  over a paper ground, lit with a warm key against a cool fill, occluded,
  shadowed, and with **no outline at all** — depth from light rather than from
  line, an homage to Irving Geis and named for the technique rather than the
  man. `richardson` is the ribbon diagram held back to what Jane Richardson
  drew: cartoon in one pale tone, cel shaded at two steps rather than four, a
  grey line, white paper.

  **Four things neither the plan nor the first draft of the tests knew**, each
  found by looking or by mutation rather than by reasoning:

  - **The thinner outline §5.9 asked for does not exist.** Mol\*'s
    `outline.scale` is `min: 1, step: 1` and `illustrative` already sits at the
    floor, so a smaller number would have been clamped and reported as applied.
    `richardson`'s line is quieter by being grey rather than by being thin.
  - **`painting` had to be spheres, not sticks.** The plan offered either.
    Ball-and-stick came out as a thicket of wire with no depth: occlusion and a
    cast shadow need something to fall across, and a stick model gives them
    almost nothing. The cost is the interior, which a spacefill always costs.
  - **The obvious test for "draws no line" is wrong, and convincingly so.**
    Counting near-black pixels makes `painting` the *darkest* view in the
    catalogue — 0.0056 of the frame against 0.0017 for the black outline
    `textbook` really draws — because ambient occlusion and a cast shadow drive
    the crevices between spheres to near-black. The claim is made against the
    renderer's own state instead, read out of the page.
  - **The test written for `richardson`'s grey line could not fail.** It
    asserted more near-grey than near-black pixels, which is true of a *black*
    line too: an antialiased black line has a grey halo wider than its core, so
    `textbook`'s black outline reads 0.0023 grey against 0.00088 black. It
    passed with the colour mutated back to black. The discriminating number is
    the absence of near-black, and both halves of the claim are now checked by
    mutation: against a line that is black, and against no line at all.

  They also settle the question §5.9 left open. `richardson` and `textbook`
  overlap on paper and do not overlap in pixels: every view in the catalogue
  now renders measurably differently from every other, so neither absorbs the
  other and both stay.

- **A review found fifteen things wrong with the views below.** The worst:
  `not metals within X of metals` parses as `not (metals within X of metals)`,
  so `crosslink_view` on a metalloprotein selected everything *not* near a
  metal — 1260 atoms of 1260 on myoglobin — drew the whole structure as
  ball-and-stick and called every residue coordinating. Neither structure in
  its own tests has a metal, which is why nothing caught it.

  The others, in one line each: `_residue_count` was handed index arrays where
  it expects masks, so interface contact counts described the first N atoms of
  the structure; `buried_area_a2` is a key that has never existed, so every
  interface reply said `null`; disulfide detection paired the two conformers of
  one cysteine with each other; `pocket` and `pharmacophore` hid the scene by
  hand and so left `_styleable` pointing at a hidden component; `default` knew
  three handles when eight views register their own; `define_atom_classes`
  deleted the theme it was replacing *before* validating; and the
  pharmacophore's "greasy" and "no feature" colours were two near-identical
  greys with opposite meanings.

- **`crosslink_view()` picks out what holds a fold together** — cysteine sulfurs
  within bonding distance, plus metals and whatever coordinates them. A
  distance filter over pieces that already existed, as the plan estimated.
  Refuses a structure with neither: a cartoon with nothing picked out looks the
  same as a search that failed. Four disulfides on lysozyme, all under 2.5 A.

- **`pocket_view(resn)` shows the cavity a ligand sits in**, as a
  half-transparent surface over the lining residues with the ligand inside.
  **Not cavity detection** — it shows the pocket around a ligand you name and
  cannot find one in an apo structure, which is an algorithm and probably a
  dependency. The plan called that the hard part, then found the view everyone
  actually wants is this one.

- **`pharmacophore_view(resn)` types a ligand's atoms by what each can do.** It
  cost what the plan finally said it would, after two wrong estimates: Mol\*'s
  `interactions` extension computes interactions *between* atoms and cannot
  type one ligand's atoms at all. Both halves are new — chemical typing from
  element and heavy-atom connectivity, and **per-atom categorical colouring**,
  a third kind of registered theme now that fields are per-residue scalars and
  palettes are per-element.

  **The typing is inferred, not measured, and the reply says so.** Most crystal
  structures carry no hydrogens, so an oxygen with one heavy neighbour is
  treated as a hydroxyl that both donates and accepts, and one with two as an
  ether that only accepts. Rules of thumb, wrong where a chemist would be wrong
  — and the picture looks equally confident whichever fired, which is why the
  counts come back with it. Twelve tests pin the rules against molecules whose
  answer chemistry already gives.


- **`default` is the way back.** Every drawing view hides the scene the load
  built and replaces the one handle they share, so a run of them left no way to
  the picture you started from — watched go wrong, eight clicks in. It restores
  what is *drawn* and leaves lighting and ground alone: a "default" that
  silently reset carefully built lighting because someone wanted the cartoon
  back would be a worse surprise than the one it fixes.

- **Three views that take an argument**, which is why they are tools rather
  than menu entries — a button has nothing to type into.

  `ligand_view("GLC")` takes the name a caller actually has where `active-site`
  wanted a handle, draws the ligand and the residues lining its pocket, and
  reports which ligand, how many copies and how many residues line it. Refused
  when the structure does not contain it, naming what is bound instead.

  `interface_view(a, b)` puts two chains down in flat contrasting colours and
  brings the contact residues up as sticks. Refused when the chains do not
  touch, rather than drawing an empty highlight over an ordinary two-colour
  cartoon — which looks like an interface with nothing in it.

  `mutation_view("A123G,V45L")` draws the positions a mutation would change,
  **and checks the residue is what the notation says it is.** MCPymol does not,
  and this is the one worth doing better: a view that highlights the wrong
  residue because the numbering is offset by a construct tag looks exactly like
  one that worked. It refuses with "position 1 holds MET, not TRP".

- **A second palette, for whatever the picture is about.** A ligand drawn in
  the pocket's own grey disappears into the sidechains around it; drawn in
  Mol\*'s default it comes out chain-coloured brown, which is the thing the
  palette exists to fix and only looks deliberate by accident. Same colours,
  warmer carbon.


- **A review found fifteen things wrong with the three entries below**, and
  they are fixed rather than filed. The ones worth naming: `define_elements`
  checked a name against the colour registry alone, so `"physical"` — a size
  theme with no colour twin — could be claimed and then **deleted from Mol\***
  by the cleanup path; `superpose`'s deviations dropped insertion codes, so
  residue 8 could be painted with 8A's motion; registering the field could
  fail a superposition that had already happened, discarding the rmsd and the
  transform with it; the ligand stayed on screen when the next view took over,
  which is the double-draw its own handle exists to prevent; and two identical
  structures stretched a colour ramp across floating-point noise and painted a
  speckle that read as a hinge.

- **A view draws what is bound, not only the polymer.** `textbook` and `putty`
  select `polymer`, and a ligand is not polymer — so maltose-binding protein
  came up with no maltose in it, which is most of the reason anyone loads that
  structure. Non-solvent hetero is drawn alongside, under its own handle and in
  the same element palette the sidechains use. Solvent stays out: a crystal
  structure's waters are most of its non-polymer atoms and none of its point.

- **`superpose()` registers a field of how far each residue moved.** A
  superposed pair drawn in two colours is close to unreadable — where the two
  agree the backbones interleave at one depth and read as a mottle of both, and
  where they disagree looks no different. Painting one copy by the distance the
  other moved says the thing the picture was for.

  **Measured over every residue the two share, not the ones the fit kept**, and
  the difference is the whole point. `superimpose_homologs` discards outliers
  to find its transform, and on a hinge motion the residues it discards are
  exactly the ones that moved: superposing the open and closed states of
  maltose-binding protein keeps 185 residues of 370, and the 185 are the half
  that stayed put. A field built from the fit paints the rigid lobe and leaves
  the hinge blank. Residues are matched by chain, number *and* name — two
  structures numbered differently would otherwise pair off residues that are
  not the same residue and report the mismatch as motion.

  A handle for the target copy is registered with it, because the mobile copy
  carries no value and colouring the whole scene paints half of it the no-data
  grey.

- **Sidechains are attached to the molecule now.** They floated: `sidechain` is
  "polymer and not backbone" and the alpha carbon *is* backbone, so every stick
  began at CB with no bond back to anything — a cloud of fragments beside the
  ribbon they belong to. The view draws the anchor as well. The selection
  keyword is untouched, because its definition is right; only what this view
  draws changed.

  That broke the refusal, and a test caught it before it shipped: every polymer
  has an alpha carbon, so "does this have sidechains" became always true and
  glycine — whose sidechain is a hydrogen — would have reported success for a
  view of nothing but anchors. What exists and what is drawn are now asked
  separately.

- **An element palette you can choose, because Mol\* has none.** Its
  `element-symbol` theme takes exactly one parameter, `carbonColor`; oxygen,
  nitrogen and sulfur come from a fixed CPK table with no way in. So an
  all-atom view could not be made to agree with the cartoon under it.
  `define_elements()` registers a theme that reads the element symbol, and the
  sidechains view uses it: light grey carbon, teal nitrogen, mauve oxygen,
  burnt sienna sulfur, and a fallback colour so an unusual metal is never
  invisible. Chosen to sit quietly next to a secondary-structure cartoon rather
  than to match CPK.

- **And they are thicker.** Mol\*'s default of 0.15 drew hairlines that read as
  noise. 0.22, picked by rendering three widths and looking; 0.4 was tried
  first and buried the ribbon completely, which is the opposite failure and
  just as useless.


- **`snapshot(finish="hedcut")` redraws a capture in ink.** Tone becomes line:
  the image is banded by brightness and each band filled with strokes, more of
  them where it is darker, the way an engraving carries shading without any
  greys. Two styles — `cross-hatch` lays a second and third direction across
  the first as the tone deepens, `hedcut` keeps one direction and thickens the
  stroke, which is what makes it read as engraved rather than sketched.

  **Mol\* has no hatching, stippling or halftone anywhere** — its whole
  post-processing vocabulary is antialiasing, background, bloom, depth of
  field, occlusion, outline, shadow and sharpening, checked across the tree.
  Adding one would mean a custom render pass and a Mol\* built from source. So
  this runs afterwards, on the pixels, which is where engravers worked too.

  The cost is stated where a caller meets it: **the viewer cannot show it.**
  There is no live preview and no menu entry, a caller sees it only in the
  file, and the reply says the finish was applied after the capture rather than
  leaving that to be noticed. It also reports the **ink fraction**, because the
  caller is usually a model that cannot look: a near-black ground engraves to
  an almost solid rectangle with the molecule showing through as a few light
  strokes, and nothing else in the reply would say so.

  Three things the plan for this had wrong, all found by looking at the output.
  A "white" ground is about 252, not 255, so the lightest band caught the whole
  background and sprinkled strokes over the empty half of the frame. Mapping
  darkness straight onto bands put a mid grey two thirds of the way to solid,
  so a cartoon came out a black mass with holes; darkness is raised to 1.7
  first. And strokes three pixels apart with four crossed directions is not
  cross-hatching but a dot screen.


- **Any number you have computed can be drawn, without borrowing a column to
  carry it.** `define_field(name, values)` registers a per-residue scalar as
  both a colour theme and a size theme, after which `color(name)` paints it and
  `size(name)` gives it width — an ordinary theme from that point on, listed in
  `capabilities()` beside Mol\*'s own.

  What it replaces: protean's existing scalar colouring writes the numbers into
  the **B-factor column and re-sends the whole structure**, so that Mol\*'s
  `uncertainty` theme has something to ramp over. That costs an upload per
  colouring, flattens the values into that theme's fixed [0, 100] domain so they
  stop meaning what they measured, and allows exactly one scalar at a time
  because there is one B-factor column.

  It takes the shape the analysis tools return — entries with `chain`, `seq`,
  and a number — and finds the number whatever it is called, so `rmsf()`'s
  residues go in unchanged. `conservation()`'s carry two numbers, entropy and
  conservation, so that one needs `key=` to say which; an entry with more than
  one number refuses rather than guessing, and names the choices. An earlier
  draft of this entry claimed both tools went in unchanged, which was not true
  of the second and was caught in review rather than by using it.

  The reply says how many residues on screen the field did *not* reach, because
  analysis replies truncate their residue list to `limit` and a field built from
  a truncated one covers part of the molecule while looking deliberate.

  **Keyed by residue, not by atom index.** Index alignment is shorter and breaks
  on the first biological assembly, where the viewer holds symmetry copies the
  analysis array does not — silently wrong on exactly the structures where it
  matters. A residue key gives every copy the value that residue earned.

  Refused when the field matches no residue in the loaded structure. Such a
  field registers cleanly and paints the whole molecule the no-data grey, which
  looks like a rendering fault rather than a mistake in the numbers.

- **Width is a channel now, not a thing `putty` happened to do.** `size()` sets
  what decides the width of a drawn selection — `uncertainty` for B-factor,
  `physical` for van der Waals radius, `uniform` to flatten it — the same way
  `color()` sets what decides its colour, validated against Mol\*'s live size
  registry and reported in `capabilities()`.

  This was written up as blocked. docs/views.md recorded `putty`'s tube width
  as possibly needing "a size theme protean does not expose", which would have
  tied it to the cryo-EM "size by scalar" work; and Mol\* 4.18 had a bug that
  made a size-only theme update silently do nothing, so it could not have been
  exposed cleanly anyway. Mol\* 5 fixed that bug, and nothing else was in the
  way.

  It also nearly shipped with a refusal for representations that "have no
  width", written on the assumption that only tubes and spheres do. Measured
  before shipping: `physical` moves 0.0337 of the frame on a **cartoon**, more
  than it moves on a putty. Every representation has a width. The refusal would
  have blocked something that works, and there is a test carrying that
  measurement so the assumption is harder to make twice.

### Mol\* 5

- **The Mol\* upgrade that doubled the browser CI job has a cause, and it is
  Mol\* 5.4.2.** A standalone capture benchmark (`bench/molstar-capture`,
  driven by `molstar-capture-bench.yml`) timed one image-pass capture on each
  of the nineteen releases between 4.18.0 and 5.11.0, all in one job on one
  runner. Nine of them cost nothing; **5.4.2 costs 2.91x**; the remaining nine
  add 18% between them.

  The cause is one line of GLSL. `ssao.frag`'s `isBackground()` became
  `depth == 1.0` where it had read `depth > 0.999`, taking with it the comment
  saying the tolerance was there for precision. On the *transparent* occlusion
  path depth comes from `unpackRGBAToDepthWithAlpha` over a uint8 target that
  `clearDepth` fills with (1,1,1,1), and that unpacks to
  `16777215/16777216 = 1 - 2^-24`, the largest value the encoding can produce
  and not 1.0. The early-out in front of the sample loop is therefore dead for
  every texel, and a level-4 capture pays sixteen full-screen 128-sample
  occlusion evaluations over the whole framebuffer.

  **Occlusion is about 91% of a 5.11 capture and about 73% at 4.18** — those two
  come from different jobs on different runners, so read them as +/-2 rather
  than as exact.

  The nine releases after 5.4.2 are not drift either: **5.6.0 is a second,
  smaller step of 1.15x in the same shader**, and it is *not* fixed by the patch
  below. It is the whole of the 15% that separates the patched build from
  5.4.1.

- **Captures are 2.72x cheaper, and look the same.** The Vite build now applies
  upstream's own repair — `depth >= 0.99999994`, with their comment — to the six
  shaders they have not reached yet. Mol\* met this bug and fixed three of the
  nine it landed in; `postprocessing.frag`, `illumination/compose.frag` and
  `bloom/luminosity.frag` already read the corrected constant at 5.11.0, and
  `ssao.frag`, `ssao-blur.frag`, `outlines.frag`, `dof.frag`, `shadows.frag` and
  `illumination/trace.frag` do not.

  Measured on the prebuilt 5.11.0 bundle: a capture goes from 9,829 ms to
  3,619 ms, against 3,136 ms for 5.4.1 — the last release before the bug. That
  is a laptop measurement with n=1 per condition, so the band it supports is
  **2.2x-2.9x**, not a point.

  **The picture changes slightly, and an earlier version of this entry said it
  did not.** At full resolution 56 of 480,000 pixels differ by at most 2/255,
  none on the background, most at the silhouette. With the outline pass on —
  `preset('illustrative')` — 2,425 pixels change by up to 161/255, and *that* is
  a correction: patched outline coverage matches 5.4.1's to five decimals, where
  stock 5.11.0 does not.

  **On the browser CI job: 56:33 and 45:39 without the patch, 32:48 and 43:07
  with it.** Identical test counts across all four (1457 passed, 31 skipped), so
  it is not faster because less ran. Job wall time cannot carry the ratio — the
  two unpatched runs are 1.24x apart and one of them beats a patched run — so
  the figure is taken from per-test durations instead: about **1.3x-1.45x**
  overall, 2.0x-2.6x on the render-heavy fixtures.

  **Not one constant, and assuming it was is a bug this shipped with.**
  `ssao-blur.frag` reads a 16-bit `packUnitIntervalToRG` encoding rather than the
  24-bit depth texture, so a background texel reaches it as 0.99998468 and
  `>= 0.99999994` can never fire there. It gets `>= 0.999` instead — what Mol\*
  itself had in that file at 5.4.1. `shadows.frag` and `illumination/trace.frag`
  read only opaque depth, where the patch is a no-op today.

  It is a find-and-replace against someone else's source, so it is guarded in
  both directions it can fail. The build errors if it matched nothing, and a
  test asserts the exact list of shaders still needing it — and now computes the
  16-bit round trip from Mol\*'s own pack/unpack, so the wrong-constant bug
  cannot come back.

- **The viewer runs on Mol\* 5.11, up from 4.18.** Fourteen months and 32
  releases behind, which was making every "can Mol\* do this?" answer
  unreliable. Nothing protean uses went away: the live registries gained a
  `polyhedron` representation and the `residue-charge` and `volume-instance`
  colour themes, and lost nothing. Every render differential passes, and all
  but one at its existing threshold — which is the claim worth making, because
  a renderer that shaded differently would have moved numbers tuned to three
  decimals.

  The exception is the outline, and it is worth stating plainly because the
  first version of this entry claimed the clean sweep: 5.11 draws a thinner
  outline than 4.18. Measured on the same fixture and flags at CI's headless
  frame size, the green it puts on screen fell from 0.00107 to 0.00074, and CI
  itself came in at 0.00047 against a bar of 0.0005. The bar had been derived
  from a measurement taken at a different frame size, so it was never really
  2.5x of margin. It is now a noise floor, with the fidelity claim moved to
  something the frame cannot affect: widening the outline has to widen the
  outline, which it does by 44x on the frame that failed.

- **`spin()` and `rock()` turned nothing at all, briefly.** Mol\* 5 added a
  required `axis` parameter to both animation groups and dereferences it every
  frame, and `TrackballControls.setProps` shallow-assigns rather than filling
  in group defaults — so protean's params object replaced the animation with
  one Mol\* could not run. The tool answered `{mode: 'spin', speed: 1}` and the
  camera sat byte-identical. Caught in review of this branch, not by the suite,
  which had been asking the viewer what it had been told rather than asking the
  camera where it was. There is a test for that now, and it fails when the axis
  is taken away again.

  Same shape as the `bloom` parameter below, and the reason to state it twice:
  the audit that caught bloom went through the six postprocessing effects and
  did not think to check the trackball. "Nothing protean uses went away" was
  true and beside the point — this was something new that became required.

- **`spin(speed=)` means turns per second now, not radians.** Mol\* 5 changed
  what the number means without changing its name, so the same call spins
  2\*pi times faster. protean follows the new unit rather than converting: one
  value, held in one place, rather than a reported number that disagrees with
  the viewer's. A model reading the docstring gets "1 is one revolution a
  second", which is the more useful thing to be told anyway.

- **Mol\*'s licence notice is shipped — for the first time, it turns out.**
  4.18 built with webpack, which extracted bundled licences into
  `molstar.js.LICENSE.txt`; 5.11 builds with esbuild, which emits no such file
  and leaves its dependencies' notices inline in the JavaScript instead.
  Noticing that file was gone is what prompted a look at what it had contained,
  and the answer was: safe-buffer, immutable, and React. **Not Mol\*'s own.**

  Neither published bundle carries it. Searched in both, "mol\* contributors"
  appears zero times in 4.18 and zero times in 5.11 — the only copyright line
  in either is a third-party shader's. Mol\*'s own notice lives in the npm
  package's top-level LICENSE, which nothing was copying. So this is not
  something the upgrade broke: protean had been redistributing Mol\* without
  Mol\*'s notice for as long as it has redistributed Mol\* at all.

  `sync-molstar` copies that LICENSE now, as `molstar-LICENSE.txt`. The
  packaging test written to catch exactly this had been passing throughout, on
  React's and immutable's notices, because it asked whether the file contained
  "MIT License" and "Copyright" rather than whose. It asks for "Mol\*
  contributors" now. Reported upstream: their own source headers say
  `/** Copyright ... mol* contributors` with no `@license` marker, so their
  bundler treats them as ordinary comments and drops them, while React's
  `/** @license` survives.

- **Mol\*'s PyMOL transpiler changed what `within` means, and we did not.**
  Five selections that agreed exactly under 4.18 disagree under 5.11, all of
  them a `within` with an explicit left operand: they return 456 where we
  return 121 for `polymer within 4 of resn HEM`. Nothing in their changelog
  mentions it. Checked against a third opinion before assuming the other
  implementation was the one that moved — a plain numpy distance calculation,
  owing nothing to either transpiler, returns our numbers. They are recorded
  as divergences, which assert both halves, so if upstream restores the old
  behaviour the test fails and the claim gets retired rather than carried.

- **CI runs Node 22.** Mol\* 5.11 declares `node >=22.0.0`; CI was on 20, which
  npm reported as a warning and then built anyway. A dependency's stated
  engine requirement is not a suggestion, and finding out which parts of it
  were load-bearing during a later debugging session is the expensive way.

- **`bloom` gained a parameter.** protean spells every screen-space effect's
  parameters out rather than toggling a name, because a Mol\* effect enabled
  with an empty params object renders from something nobody chose. A key
  missing from that table is the same hazard, and 5.11 added `transparency`
  to bloom. Checked key by key across all six effects; bloom was the only one.

### Views

- **A control in the viewer that asks the server rather than drawing.** One
  button, one view — `ghost-heart` — and the rule that makes it worth having:
  **a button never draws, it asks**, and the server runs the same `preset()` a
  model would call. One code path, two entry points, so a handle made by a click
  is an ordinary handle and the picture a click makes is the picture the model
  would have made. Any other arrangement lets the GUI and the model render the
  same view differently, and eventually they will.

  **The channel carries view names from a fixed list and nothing else.** The
  socket is token-authenticated, but a page holding that token can already reach
  the viewer, and the tool surface would hand it `snapshot(path=)`,
  `save_session(path=)`, `movie(path=)` and `electrostatics(path=)` — each of
  which writes where the caller says. A test enumerates the live tool registry,
  finds the nine tools taking a path, and asserts none is reachable from the
  page.

- **`ghost-heart` no longer wraps the water.** A molecular surface is computed
  per atom, so an isolated solvent molecule gets its own closed blob: 1UBQ drew
  fifty-eight of them, detached spheres floating around the fold and 14% of
  everything on screen — coverage fell from 0.1154 to 0.0996 with them gone.
  Ligands and ions stay, because they are part of the molecule's shape and the
  envelope should bulge around a bound ligand rather than ignore it. Found by
  someone looking at the picture and saying it looked strange, which is the only
  instrument that was ever going to catch it.

- **Every tool reply says what the person at the viewer did.** Without it the
  model answers about a scene it did not produce and has no way to know changed
  — this project's oldest failure mode, arriving through a door we opened
  ourselves. Drained rather than repeated, so one click is reported once. MCP
  can push notifications and client support is uneven, so it rides out on the
  next reply instead, which needs no client support at all.

- **More presets, so `preset()` covers the styles worth borrowing from
  MCPymol.** `textbook`, `putty`, `hydrophobic-surface`, `spacefill` and
  `skeleton` decide what is drawn; `cinematic`, `light-ground` and `dark-ground`
  only restyle what is there, as `publication-cartoon` and `illustrative`
  already did. None of them needed new rendering — Mol\* has the
  representations and the themes, and protean validates against its live
  registries, so every one is a composition of tools that already existed. The
  reply lists every call each one made, so any of it can be adjusted afterwards.

  Two of these replaced earlier drafts. `bfactor` said the same thing `putty`
  does, in one channel instead of two, and `pointillist` was a novelty rather
  than a way of reading a structure; `spacefill` and `skeleton` answer questions
  — how does this pack, and what are the atoms — that nothing else in the
  catalogue answered.

  The drawing presets hide what the load preset built and draw through one
  shared handle, `auto_view`. Sharing it is the point: applying a second view
  rebuilds that component rather than adding to it, so switching views ends at
  a view instead of at all of them at once.

- **`putty`'s tube width follows B-factor, and that is Mol\*'s own default
  rather than anything protean adds.** The plan for these views recorded it as
  an open question — whether putty needed a size theme protean does not expose,
  which would have tied it to the cryo-EM "size by scalar" work. It does not.
  Measured against the same coordinates loaded twice, once with the deposited
  B-factors and once with every B-factor flattened to their mean: the putty
  frames differ by 0.020 of the frame, the cartoon control by 0.000125.

- **A whole-scene preset reframes the camera, deliberately and in the reply.**
  Drawing the same handle twice through `show()` lands on two different
  cameras — the first draw keeps the framing the load preset chose, the second
  refits to what is on screen and then holds, 0.144 of the frame apart on 1UBQ
  with no preset involved. So a view applied once was framed for a scene that
  was no longer there, and applying it twice gave two pictures. The presets now
  ask for the frame outright, which costs a camera the caller had moved and
  says so; given a handle they leave the camera alone.

- **A view refuses rather than drawing an empty scene.** A handle with no atoms,
  or a whole-scene view of a structure with no polymer, previously drew nothing
  and reported success.

- **Loading a structure now waits for the camera the load preset moved.**
  `focus`, `orient` and `reset_view` have always waited; `load_structure` never
  did. Mol\* tweens the preset's framing over ~250 ms like any other camera
  move, and waiting for the *geometry* to stop changing says nothing about it,
  so a capture taken straight after a load could be mid-flight. Found by CI
  rather than by reasoning: two loads of identical coordinates produced frames
  0.008 apart on a runner where this machine reads 0.000125.

  **The wait has to come after the render pump, not inside the action**, which
  the first version of this fix got wrong. Mol\* resolves a requested camera
  reset from `commit()` and only once `commitScene` reports everything
  committed — "Only reset the camera after the full scene has been commited",
  `canvas3d.js` — so a wait placed before the geometry settles watches a camera
  that has not started moving, counts stillness as arrival, and returns just in
  time for the tween to begin behind it.

- **A preset states every screen-space effect, rather than only the ones it
  changes.** `effects()` leaves anything omitted exactly as it was, which is
  right for a tool composing calls and wrong for a recipe declaring a whole
  look. `cinematic` is the only preset that turns depth of field on, so
  `textbook`, `illustrative` and `hydrophobic-surface` — none of which mentioned
  it — rendered blurred after it and reported success.

- **The steps a preset reports are derived from the calls it makes.** They were
  written out by hand beside each call and had drifted: three omitted an
  argument that had been sent, so replaying the reported steps produced a
  different picture than the preset did.

- **A refused view leaves the scene alone.** The refusal path hid the viewer's
  own scene and rebuilt the shared handle *before* checking the selection had
  matched anything, so declining to draw left a blank viewer, an empty
  `auto_view` in the handle table, and an error mentioning neither.

- **`remove()` drops the handle as well as the component.** The handle survived
  on the Python side while its component was deleted in the viewer, so the two
  disagreed about what existed and a later call on that name resolved here and
  then failed there.

### The viewer

- **The viewer opens as a canvas, with Mol\*'s panels collapsed to slices.**
  Both were shown in full by default, and they are Mol\*'s controls for a
  person driving Mol\* directly: the left one loads structures, the right one
  edits the state tree. Used here they change the picture and nothing else —
  the analysis half lives in the Python process, so the model goes on
  answering, correctly, about the molecule it loaded rather than the one now on
  screen.

  They are collapsed rather than removed, because a viewer you cannot inspect
  is its own kind of opaque: when the picture looks wrong, the state tree is
  where the answer is. Mol\* collapses its left region to a 32 px icon rail on
  its own; its right region has no collapsed state, so protean supplies a 16 px
  tab that opens the panel and moves to sit against its edge. Measured on a
  1280×800 window: Mol\*'s panel greys fell from 42% of it to 0.5%, and the
  molecule rose from 20.5% to 36%.

  The sequence strip stays — it is the one panel that *reports* rather than
  acts, and reading along while a model works is most of why a person has the
  viewer open. The viewport's buttons go, except Mol\*'s camera reset and the
  controls toggle. The status pill moves to the lower right, the one corner
  Mol\* leaves empty.

### Packaging

- **The wheel ships Mol\*'s licence notice, which it is obliged to carry.** The
  built viewer travels inside the wheel, so `pip install protean-mcp` delivers
  `molstar.js` and everything bundled into it — React, immutable, safe-buffer,
  all MIT. The bundle's first line points at `molstar.js.LICENSE.txt`, and the
  sync step copied the script and the stylesheet but not that file, so the
  artifact carried a dangling reference to the notice MIT requires. A packaging
  test now fails if the wheel loses it.
- **protean's own licence is machine-readable.** `license = { file = "LICENSE" }`
  left `License` empty in installed metadata — an audit of the dependency tree
  read protean-mcp itself as `UNSTATED`. Now an SPDX expression (PEP 639), so
  the wheel reports `License-Expression: MIT`.

### Fixed

- **A stale server now says so, in the first reply of every session.** An MCP
  server is long-lived: it keeps running the code it loaded at start while
  serving the viewer page off disk, so a rebuilt page can meet a server from
  three days ago. That happened, and took twenty minutes and a hand-rolled
  WebSocket to diagnose. `open_viewer` now reports the build that answered,
  when the process started, and — the part that carries the information —
  whether its source still matches what is on disk, naming what changed.

  **Version numbers could not have done this**, which is why they are not what
  it relies on: `__version__` has read `0.1.0.dev0` for every build there has
  been, and `PROTOCOL_VERSION` has been `1` since the first commit including
  across the change that caused the incident. Both are reported anyway — they
  are the right answer when two *machines* compare notes, just never when two
  moments do. The viewer also compares the handshake's protocol number and says
  so in its status pill, which catches a deliberate break from here on.

  For an installed wheel none of this fires, because nothing rewrites the files
  under one.
- **`show()` no longer takes the camera at a moment nobody chose.** Drawing a
  handle moved the camera roughly one time in seven and held still the rest,
  because Mol\* requests an automatic camera fit whenever a scene commit decides
  the visible bounding sphere has moved out from under it — and a commit has a
  250 ms budget, so which boundary a `hide` and a `show` landed on decided
  whether that test ran against the old scene or the new one. A caller could
  rely neither on the camera moving nor on it staying, which is worse than
  either: a figure captured after a draw was framed unpredictably, and applying
  the same view twice could give two pictures.

  The viewer now takes the camera off automatic fitting and asks for the one
  fit a load wants. `focus()` and `reset_view()` are unchanged, so the camera
  moves where a caller asked for it and nowhere else. The camera's *limits*
  still follow the scene, because the flag that stops Mol\* re-framing also
  stops it maintaining them — left alone, a map spanning more than its protein
  would be clipped away by a slab drawn from the protein's radius.

- **A capture is allowed time in proportion to the pixels it asks for.** Every
  capture shared one fixed 300 s budget, which the range of sizes makes
  meaningless: 12000×9000 takes about 20 s on a real GPU, while under software
  rendering the same machine takes 6.5 s for a 1200 px capture and 105 s for a
  4323 px one — 183 mm at 600 dpi, an ordinary journal figure. A CI runner is
  roughly three times slower again. Above about 5000 px the fixed budget could
  not be met on any renderer that slow, including locally, for sizes the tool
  accepts without complaint.

  The budget is now 60 s per megapixel of the requested size, with a 300 s
  floor for small captures — about 10x what the development machine needs for a
  journal figure and 3x what a CI runner needs. Positioning the scene (a
  trajectory frame, a camera move, an orbit step) borrowed the capture's budget
  when there was only one, and keeps the old 300 s under its own name, so a
  camera move that never answers is not given a render's patience.

  There is no progress signal to use instead: Mol\*'s ordinary image pass
  renders in a single synchronous call, so the page's main thread is blocked
  for the whole capture and could not send a heartbeat if asked for one.
  Silence is what a healthy large capture looks like, and only the pixel count
  separates it from a stall.

  This is not what made the journal-figure test flaky in CI — that was a lost
  reply, below — but the two share a cause worth naming: a long render is
  indistinguishable from a stall from the outside, so both the budget and the
  failure reporting were guessing.

- **A capture's reply is no longer lost with its socket.** During a
  figure-sized capture the page's main thread is blocked for tens of seconds,
  and the WebSocket can die inside that window — observed closing 62 s into a
  68 s capture, abnormally (1006, no close frame), with the page itself
  surviving. The page then replied on the socket the request had arrived on,
  and `send` on a closed socket does nothing, so the answer vanished although
  the work had succeeded. Nothing failed the waiting request either, so it ran
  out its whole budget and reported a stall — *"Viewer timed out on
  'snapshot'"*, which is what CI had been printing.

  The page now keeps a reply it cannot send and delivers it on the next
  authenticated socket, and the handshake declares what that page still owes.
  A viewer that reconnects mid-render keeps its request alive; one that
  reloaded, or a second tab that takes the connection, ends it immediately with
  the reason rather than at the end of the budget. A plain disconnect
  deliberately fails nothing: the reply may still be on its way.

- **`screenshot` works again through an MCP client.** It failed for every
  caller with `Unable to serialize unknown type: Image`, while the test suite
  stayed green. FastMCP derives an output schema from the return annotation,
  and `-> list[Any]` gets one — so the reply was encoded as *structured*
  content, which an image cannot be. A bare `list` gets no schema and worked,
  which is how the floating `mcp[cli]>=1.2.0,<2` pin brought this in without a
  line of protean changing. The tool now declares `structured_output=False`,
  putting the image back in unstructured content where it belongs.

  **The tests could not have caught it**, and that gap is now closed too: every
  test called tools as plain Python functions, so nothing ever crossed the
  serialisation boundary a real client goes through.
  `tests/test_mcp_boundary.py` calls them the way a client does.

- **`load_session` no longer leaves the analysis describing the previous
  molecule.** It restored the viewer and never touched the Python side, so
  every count, distance and selection afterwards answered about whatever was
  loaded before — measured at viewer 100 atoms against `_structure`'s 660,
  with the identifier still reading `1ubq` and nothing reporting a
  discrepancy.

  Both halves are restored now, or neither is. The analysis structure is
  rebuilt from the session's own embedded copy — no network, and no question
  about which file, since it is the same bytes the viewer parsed. **The
  viewer's atom count decides how to build it**: the same deposited text
  assembles two ways and nothing in the file records which was chosen (1HHO
  reads 4792 biological, 2396 asymmetric), so a fixed default would have been
  silently wrong for half of all sessions. If neither reading matches the
  viewer, the analysis is left empty and the reply says so with both numbers,
  because a structure that disagrees with the picture is the failure this fixes
  rather than a caveat to attach.

- **A viewer that cannot connect now says why, instead of retrying forever.**
  The WebSocket API hides the handshake's HTTP status from the page, so a
  refused socket and an unreachable server arrive as the same event — and the
  page retried every 1.5 s indefinitely, showing only "disconnected". Two cases
  make that a silent failure rather than a hiccup: the bridge mints a token per
  process, so restarting the server leaves an open tab refused on every attempt
  for as long as it lives; and a page opened by hand at
  `http://127.0.0.1:9878/` has no token at all, loads, looks alive, and can
  never connect. The page now stops after ~30 s and names both causes, or says
  immediately that it was opened without a token, and points at `open_viewer`
  either way. A completed handshake resets the budget, so a long session is not
  capped.

- **A writing tool will no longer turn one kind of file into another.**
  `snapshot`, `screenshot`, `save_session`, `movie` and `electrostatics` wrote
  wherever they were pointed, with no check: during the security pass
  `save_session` replaced a 21-byte JSON file with 32 kB of gzip, and
  `electrostatics(path=…)` — an *output* path that reads like an input — wrote
  an OpenDX grid over a file named `secret.key`. An existing file is now
  replaced only when it already holds what that tool writes, so capturing a
  figure over its own earlier version still works while replacing prose,
  a key or a config file is refused. `overwrite=True` asks for it explicitly.

### Security

- **`open_viewer` no longer hands the handshake token to the model.** The URL
  it returned carried the token, so the credential that authenticates a viewer
  socket landed in the model's context, in transcripts and in any log of tool
  results — and the `Origin` check is no backstop for a leaked token, because
  an *absent* Origin is allowed so non-browser clients can connect at all. The
  address now comes back without it while the real one goes straight to the
  browser; `reveal_url=True` asks for it deliberately, for a second browser or
  a forwarded port. All three of `open_viewer`'s return paths were leaking it.

- **A session file is no longer trusted to say where the viewer should look.**
  `load_session` handed the file's embedded Mol\* state tree straight to
  `setSnapshot`, which applies it as given, so a `.protean` file could name a
  URL and the browser would fetch it — and then draw whatever came back, while
  `load_session` returned a normal reply naming the atom count it had been
  handed. Demonstrated against a live viewer with an outbound GET to a stand-in
  attacker server. The format exists to be shared, so a session someone sent
  you is its ordinary use, not an exotic one.

  A session is now checked two ways, because neither alone is enough:

  - **No string in it may name a location to fetch from**, except this bridge's
    own relative `/volumes/<handle>` route and — by exact value — the three
    third-party URLs Mol\* serialises as its own custom-property defaults. Both
    exceptions were measured from real sessions; a blanket "no URLs" rule would
    have refused every session, and allowing the *key* would have permitted the
    same providers to fetch from anywhere.
  - **No transformer may appear that `save_session` never writes.** This is the
    half that does not depend on spotting a URL: `create-volume-streaming-info`
    fetches from Mol\*'s own public default when the file names no URL at all,
    so there is nothing for the first check to find.

  Decompression is bounded at 512 MB as well: 9 kB of gzip reaches 2 GB, and
  the file was read whole before anything checked it. Malformed files now
  refuse rather than raising `AttributeError`, `KeyError` or `RecursionError`.

- **The viewer handshake is authenticated.** The bridge's WebSocket accepted any
  connection: no `Origin` check, no token. A WebSocket is not subject to the
  same-origin policy and the port is `DEFAULT_PORT` plus a small scan range, so
  any site the user was visiting could connect, send `protean_ping` — which is
  designed to displace the incumbent — and from then on receive every action and
  answer every one of them. Demonstrated with a socket carrying
  `Origin: https://evil.example`: accepted, and the real viewer was superseded
  and closed.

  A spoofed viewer returning fabricated counts defeats the one guarantee this
  project exists to make, while every call returns cleanly.

  Now a per-bridge token (`secrets.token_urlsafe(32)`, compared with
  `compare_digest`) plus an `Origin` check, both **before** `prepare()` so a
  refused caller never reaches the message loop. `ViewerBridge.viewer_url` is the
  single place the URL is built, so a viewer cannot be opened that its own socket
  would refuse. Found by the going-public security pass, which is the argument
  for running that pass before the flip rather than after.

### Volumes

- **Density maps can be contoured.** `isosurface(name, level, unit, style,
  opacity)` draws a volume as a solid surface or a wireframe mesh. The unit is
  named, never assumed: EMDB publishes author-recommended levels as ABSOLUTE
  values while most viewers contour in sigma, and EMD-30913's published 0.05 is
  3.16 sigma for that map — typed in as sigma it contours noise and looks like
  an ordinary bad map rather than a unit error.

  **A sigma level is converted against the sigma measured off the voxels**, and
  Mol\* is handed an absolute value it cannot reinterpret. Left to itself Mol\*
  converts using `grid.stats`, which for CCP4/MRC is the file header — its own
  default isosurface is 2 sigma against exactly those stored fields. The reply
  reports the `sigma` and `mean` used, and `stated_absolute`: what the header
  would have given for the same request. A large gap between the two says the
  file disagrees with itself.

  The wiggles-em backend no longer refuses an `Isosurface` op; it lowers it,
  carrying the unit rather than the number alone. A carve is still refused.

- **A volume can say where it came from, and protean never guesses.**
  `load_volume(..., provenance=)` takes one of `measured`, `sharpened`,
  `nn_enhanced`, `generated`, `unknown`, and every volume reply carries a
  `caveat` line to show beside a picture of the map. A filename saying
  `deepemhancer` is not evidence: a guessed label is believed, where a missing
  one prompts a question, so an undeclared map stays `unknown`. A typo is
  refused rather than coerced, since coercion would turn a caller who declared
  their map into one who appears not to have. The vocabulary is wiggles-em's
  `Provenance`, reused rather than duplicated, because the backend lowers its
  scenes onto this viewer and the two have to agree.
- Density maps load into the viewer: MRC/CCP4 (gzipped or not), DSN6, OpenDX,
  Gaussian cube and BinaryCIF. Four tools — `load_volume`, `volume_info`,
  `list_volumes`, `remove_volume` — taking the tool count from 49 to 53.
  protean could parse exactly one volume format before this, OpenDX, because
  that is what APBS writes.
- Format is detected from the MRC magic at byte 208 first and the extension
  second, on the *decompressed* bytes — `emd_30913.map.gz` has suffixes
  `['.map', '.gz']` and carries its magic only once unwrapped.
- Volumes travel over HTTP, not inline in the RPC message. A 110³ float32
  reconstruction is ~5 MB and a 400³ one ~256 MB, and base64 through a JSON
  WebSocket frame is the wrong pipe. The bridge serves them from a
  handle-keyed table, so only what was explicitly published is reachable.
- **The reported statistics are computed from the voxels, not read from the
  file header.** Mol\*'s `grid.stats` passes through MRC's stored DMIN, DMAX,
  DMEAN and RMS, which are not always true: a cropped or rescaled map keeps
  whatever header nobody updated. Since those numbers exist to convert a
  published absolute contour into sigma, a stale header would put the contour
  in the wrong place while every call returned cleanly. The header's own
  claims are still reported, under `stated`, because a large disagreement says
  the file has been through something.

  Found by a browser test written so it could not pass on the old behaviour:
  its fixture writes deliberately false header statistics (−999/999/42/7) and
  requires the reply to match the data instead. On its first run it failed with
  `min came back as the header's false value -999.0`.

### Alternate conformers

- Every alternate conformer is loaded, so the viewer and the analysis hold the
  same atoms — 15929 on 5FJI, where analysis previously held 15712 and the
  difference had to be explained in the load message.
- `alt A` selects atoms carrying that label, as PyMOL does; the whole
  conformer is `alt ''+A`, and `alt ''` and `alt .` both mean "no alternate".
- Analysis resolves one conformer state before computing — each site keeping
  its own highest-occupancy alternate — and reports which letters it used.
  Resolving per structure instead would delete any site not carrying the
  winning letter, which is how a partially occupied ion is routinely modelled. Alternate conformers never coexist, so
  a buried area computed over both belongs to no molecule, and because a
  residue's shared atoms carry no label both states would otherwise land in
  one residue entry and sum.
- Bonds joining one conformer to another are dropped, so `extend` and
  `bound_to` do not step between mutually exclusive states.

### Session state

- Loading a structure now ends the session before it. A trajectory and any
  saved keyframes belong to the molecule they were made for, and were
  previously carried across: `rmsf()` kept answering about the old trajectory
  while the viewer showed the new structure, with nothing to say so. The reply
  now names what was discarded.
- Tests restore the server's session globals between cases, so a test that
  loads something no longer changes what the next test sees.

### Addressing one symmetry copy

- `sym N` selects one copy of the asymmetric unit in a biological assembly,
  numbered from 0. Copies share chain ids and residue numbers, so `chain A`
  on an assembly means every copy of that chain and `chain A and sym 0` is
  the single subunit. A selection with no `sym` term still means every copy.
- Handles now carry the copy to the viewer. They travel as atom-id
  predicates, which an assembly duplicates, so a set covering one copy could
  not previously be drawn as one copy; each copy's ids are now keyed on its
  Mol* symmetry operator. Sets that are symmetric across copies are emitted
  exactly as before.
- `interface()` takes `copy=N`, and with no copy named reports a `per_copy`
  breakdown beside the total. On 1HHO the A-B total is 5530.2 A^2 where one
  alpha-beta pair buries 1776.9 -- the same number the deposited asymmetric
  unit gives.
- `rank` is no longer refused on multi-copy assemblies; it was refused only
  because the handle could not name a copy.

### Trajectories and animation (Phase 5)

- `load_trajectory()` reads XTC, TRR, DCD and NetCDF onto the loaded structure,
  with `stride` and `max_frames` for long runs. Atom counts must match: a
  trajectory carries no atom names, so the wrong pairing animates smoothly and
  means nothing.
- `frame()` steps through it; `rmsf()` and `rmsd_series()` return structured
  numbers, superposed first so bulk drift is not read as fluctuation.
- `color_by_rmsf()` draws the same values on the molecule.
- `record_trajectory()`, `turntable()` and `record_timeline()` capture frame
  sequences; `movie()` encodes them with ffmpeg to MP4, GIF or WebM.
- `keyframe()` and `record_timeline()` interpolate a camera between saved
  views, swinging around the subject rather than sliding between positions.
- `spin()` sets the viewer turning for on-screen reading.

### Publication rendering (Phase 4)

- `snapshot()` renders at a real physical size — Nature's column widths or an
  explicit width in millimetres — and writes the DPI into the file, as PNG,
  TIFF or JPEG.
- `background()` for colour, transparency, gradients, images and skyboxes;
  `lighting()` with six named rigs; `effects()` for outline, occlusion, shadow,
  depth of field, bloom and sharpening.
- `shading()` (cel, xray, flat), `material()` with five PBR finishes, and
  `path_trace()` for Mol\*'s progressive path tracer.
- `preset()` composes those into publication-cartoon, illustrative,
  ghost-heart and active-site.
- A pixel-assertion harness underpins all of it: rendering is verified by
  reading the image, not the reply.

### Analysis (Phase 3)

- `interface()` reports buried area per side and classified contacts;
  `superpose()` aligns by sequence and applies the transform.
- `superpose(mode="structural")` matches residues by the shape of their local
  backbone rather than by sequence, for proteins too diverged for a sequence
  alignment to mean anything. On haemoglobin's alpha and beta chains it
  superposes 139 residues of the shared fold where sequence mode anchors 64.
  The reply now names the mode that produced it.
- `conservation()` scores an MMseqs2 alignment; `electrostatics()` computes a
  screened Coulomb potential, or runs APBS when it is installed.
- Scalar colouring for potential and conservation, as a gradient or as bands.

### Selections and core (Phases 1–2)

- Selections are named handles: `select()` takes PyMOL syntax for leaf
  predicates, and `combine()`, `near()` and `invert()` compose them.
- `extend`, `bymolecule`, `bound_to`, `neighbor` and `rank` all resolve, having
  been refused as unimplemented. Bond topology comes from residue templates and
  is derived on demand, since only these four selectors need it. Every count
  matches PyMOL 3.1.0 exactly on the same file. `alt` is still refused, but the
  refusal now names the tradeoff — every conformer can be loaded, at the cost
  of computing buried areas over atoms that overlap each other — rather than
  claiming it cannot be done.
- `backbone` includes `OXT`, the C-terminal carboxylate's second oxygen, which
  used to fall into `sidechain`. Four atoms per structure, and the reason
  PyMOL and protean disagreed about `backbone` on anything with a modelled
  C-terminus. Mol\*'s transpiler still excludes it; the difference is asserted
  in the differential suite rather than left to drift.
- `ss H`, `ss S` and `ss L` select secondary structure, which used to be
  refused outright. It is computed from backbone geometry with P-SEA rather
  than read from the file, so it answers the same way for a predicted model as
  for a deposited one — but it is not the DSSP-style criterion PyMOL and Mol\*
  use, and assigns slightly smaller elements than either. The difference is
  asserted in the differential suite and recorded in docs/backlog.md.
- Representations, colour themes, camera control, measurements and labels.
- Sessions save and load as gzipped `.protean` files that embed the structure.

### Fixed

- The wheel now carries the built viewer. It is a gitignored npm artifact and
  hatchling honours VCS ignores, so an installed protean had a server and no
  viewer.
- A visible tab now settles before replying: actions used to answer before
  Mol\* had built what they described, so a screenshot could photograph an
  empty canvas.
- `screenshot()` builds the image pass before capturing. The first capture of a
  session went through a freshly created pass and came back measurably worse
  than every identical one after it.
- `snapshot()` refuses an incomplete capture. At large sizes a renderer can
  return an image of exactly the right dimensions with most of it never
  written.
- `superpose()` applies its transform and displays the pair.
- The viewer and the analysis load the same assembly, and say so.
- A structure with alternate conformers is no longer reported as a mismatch.
  The analysis keeps one conformer per atom site and the viewer draws all of
  them, which on 5FJI is a 217-atom difference between two descriptions of the
  same molecule; the load reply now says so instead of declaring every count,
  buried area and potential in the session unreliable. A difference the
  conformers do not fully account for is still a mismatch.
- A distance must be greater than zero, in `near()` and in the grammar's
  `within`, `around` and `expand`. A non-positive radius used to answer with an
  empty set — or, for `expand`, the source unchanged — both of which read as
  results rather than as the rejected questions they are. `nan` and `inf` are
  refused with them.
- `backbone` and `sidechain` understand nucleic acids. `backbone` was protein
  N/CA/C/O only, so it found nothing in DNA and `sidechain` — "polymer and not
  backbone" — returned every atom of the molecule as though that were an
  answer. `backbone` is now the sugar-phosphate backbone as well, which leaves
  `sidechain` meaning the nucleobase: 258 and 228 atoms on 1BNA, matching both
  PyMOL and Mol\*'s transpiler.
- `elem` refuses a symbol that is not an element, with a suggested correction:
  `elem Zz` used to return 0 atoms and no complaint, which reads as "this
  structure has none of those" rather than "you misspelled it". A symbol is
  refused only if it is neither a real element nor present in the file, so a
  real element that is simply absent still answers 0.
