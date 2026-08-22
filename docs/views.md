# Views, and driving them from the viewer

Planned 2026-08-17. **§5.1 (the six style presets) and §4 (the `protean_invoke`
slice) are done**; §5.2 onward are not. Two things that turn out to be one
thing:

1. **MCPymol has fifteen named view types and protean has four presets.** If
   protean is a general PyMOL replacement, the good ones belong here.
2. **Some of those views want a switch a person can flick** while looking at the
   molecule, rather than a sentence typed at a model.

The second is what makes this a document rather than a pull request. Putting
controls back into a viewer that [deliberately removed
them](../CHANGELOG.md) needs an argument, and putting a control anywhere other
than in front of the server needs a better one.

---

## 1. What was measured first

Against `main` at `f59237d`, before any of the plan below was written. Three of
these numbers changed what the plan says.

**Mol\* already has the representations.** From its live registry:

```
backbone  ball-and-stick  carbohydrate  cartoon  ellipsoid  gaussian-surface
gaussian-volume  label  line  molecular-surface  orientation  plane  point
putty  spacefill
```

`putty` and `point` are there, and protean's `show()` validates against that
same registry, so **both are reachable today**. Confirmed by rendering 1UBQ:
putty differs from a cartoon by 0.0144 of the frame, point by 0.0263.

**Mol\* already has most of the colour themes.** `hydrophobicity`,
`uncertainty` (B-factor), `illustrative`, `partial-charge`, `formal-charge`,
`secondary-structure`, `residue-name`, `molecule-type`, `occupancy`,
`volume-value`, `external-volume`. `color()` validates against the theme
registry, so these are reachable too.

So **almost none of the fifteen views need new rendering.** They are recipes
over primitives that exist. That is the finding that makes this cheap.

**Three things the measurement changed:**

- ~~**pLDDT is not in the base theme registry.**~~ **Wrong, corrected
  2026-08-18.** It is not in `mol-theme/color/`, which is where this was read
  from — but the *prebuilt bundle protean serves* registers the extensions, and
  `capabilities()` on a live viewer lists `plddt-confidence` along with
  `qmean-score` and the RCSB and PDBe quality themes. Reading the source tree
  answered a question about a build that is not the one shipped. **Ask the
  running viewer, which is what `capabilities()` is for.**
- **Pharmacophore is much cheaper than it looks.** Mol\* ships an `interactions`
  extension that computes `hydrophobic`, `ionic`, `cation-pi`, `pi-stacking` and
  `metal-coordination`, with visuals. Most of a pharmacophore is already there.
- **`ellipsoid` drew nothing on 1UBQ and reported success.** ~~Probably correct
  behaviour — anisotropic displacement parameters are absent from that entry —
  but it is indistinguishable from the failure this project exists to catch.~~
  **Wrong, corrected 2026-08-17.** It draws. This was the broken instrument
  again, not a defect: anything drawn over the load preset's own representation
  appears to change nothing.

**One thing was deliberately not claimed, and is now settled.** An attempt to
verify that colour themes reach the pixels failed: a literal `#ff0000` control
also measured as zero change, so the instrument was broken rather than the
colouring.

**Rebuilt and answered, 2026-08-17.** The fault was that the probe drew its test
representation *on top of* the load preset's `auto` scene, where the two are
coincident and nothing it did could show. Hide `auto` first and everything
measures. All five themes the catalogue needs — `uncertainty`, `hydrophobicity`,
`illustrative`, `partial-charge`, `secondary-structure` — reach the pixels, as
do `putty`, `point` and `ellipsoid`. Eight differential tests in
`test_render_differential.py` now pin it.

**The instrument was wrong about three separate things**, each time producing a
confident answer: that colour themes were unverifiable, that `ellipsoid` was a
silent no-op, and — implicitly — that the primitives needed checking one by one
rather than the harness needing fixing once. Worth more than the result.

## 2. The decision: presets or tools

MCPymol makes each view a tool. protean should not, and the deciding factor is
parameters rather than taste.

`textbook`, `cinematic`, `hydrophobic-surface`, `putty`, `spacefill` and
`skeleton` take nothing but an optional handle. `preset(name, handle)` expresses
them exactly.

`ligand_view(resn)`, `interface_view(chain_a, chain_b)`,
`mutation_view("A123G,V45L")` and `pocket_view(resn)` each take different
arguments. Putting those behind one `preset()` means a generic keyword blob,
which is the "strings it has to guess" that protean was built to avoid.

**So: pure style goes in the `preset()` enum; anything that takes a target or
computes something gets its own tool.** This is the line protean already draws —
`interface()` is a tool, `publication-cartoon` is a preset — and it keeps
fifteen views from becoming fifteen entries in a schema every client pays for on
every request.

## 3. The architecture for a control in the viewer

### 3.1 The rule

**A button never draws. It asks the server, and the server drives the viewer.**

```
page   →  server   {action: 'protean_invoke', view: 'ghost-heart', args: {}}
server →  server   the same code path preset() runs for a model
server →  page     {id, action: 'show', args: {…}}      ← the ordinary channel
```

One code path, two entry points. Any other arrangement lets the GUI and the
model render the same view differently, and eventually they will.

### 3.2 Why not draw locally, which is easier

Because of what a view *is*. A style toggle changes appearance only, and would
be safe to apply in the browser. But `pharmacophore` and `pocket` create
selections, and a selection created in the browser is a handle the Python side
has never heard of — so the model cannot refer to what the user is looking at.
That is the desync protean exists to prevent, arriving through a new door.

Routing everything through the server makes the distinction unnecessary: handles
created by a click are ordinary handles, because an ordinary tool made them.

### 3.3 Where the button lives

protean already draws its own control. The 16 px right-panel tab is a plain
`document.createElement('button')` appended to `document.body`, positioned over
Mol\*'s layout, which reaches into `plugin.layout.updateProps`. That is the
pattern to reuse: no React, no coupling to Mol\*'s component versions, and it
already has a differential test.

Mol\*'s own `PluginUISpec` extension points are the alternative and are more
idiomatic, but they put controls *inside* panels that protean keeps collapsed —
which defeats the point of collapsing them.

### 3.4 The constraint that is not negotiable

**`protean_invoke` takes an allowlist of presentation verbs, never the tool
surface.**

The socket is token-authenticated, but the security pass established what a page
holding that token can already do. If `protean_invoke` reached every tool, a
hostile page would gain `snapshot(path=)`, `save_session(path=)`,
`movie(path=)` and `electrostatics(path=)` — every one of which writes to a
caller-chosen path. The write-protection added in backlog 21 refuses to *change
what a file is*, which is not the same as refusing to write.

So the channel carries view names from a fixed list and nothing else. **No
path-taking tool is ever reachable from the page.** A test asserts the
allowlist contains no tool that accepts a path.

### 3.5 Telling the model the user did something

Without this, the model answers about a scene it did not produce and has no way
to know changed.

MCP can push notifications, but client support is uneven, so the cheap and
robust answer fits what protean already does — replies read state back rather
than echoing the request. The server records user-initiated actions, and the
next tool reply carries them:

```
"…, and since your last call the user applied the ghost-heart view."
```

No client support needed, and a model cannot act on a stale picture without
being told.

## 4. The vertical slice — shipped 2026-08-17

One button, one view, end to end. Deliberately not a framework: it proves the
loop, the allowlist and the state reporting together, and everything afterwards
is adding rows to a list.

**Done, and the design survived contact.** A real click in a real browser moves
the pixels, and the handle it leaves behind is `auto_ghost` on the Python side —
which is the claim that matters, because pixels alone would pass with the page
drawing for itself. All eight criteria are met; what the work added to the plan
is below.

**Chosen view: `ghost-heart`**, because it already exists as a preset, takes
only a handle, and its effect is obvious in a screenshot.

### 4.1 Acceptance criteria

| # | Criterion | How it is checked |
|---|---|---|
| 1 | ~~Every theme and representation the later phases need reaches the pixels~~ | **Done 2026-08-17** — eight differential tests, instrument rebuilt |
| 2 | ~~`ellipsoid` either draws or refuses~~ | **Void** — it draws; the planning probe was wrong |
| 3 | ~~A page-initiated `protean_invoke` runs the same code path as `preset()`~~ | **Done** — the click's viewer calls are compared with the tool's, action for action |
| 4 | ~~The scene arrives back over the ordinary action channel~~ | **Done** — a real click in Chrome, pixels compared, and `auto_ghost` asserted on the Python side |
| 5 | ~~The allowlist admits no tool taking a path~~ | **Done** — nine path-taking tools enumerated from the live registry; none reachable |
| 6 | ~~An unlisted view name is refused, and says what is available~~ | **Done**, and the first version of the test was a no-op — see below |
| 7 | ~~The next tool reply names the user's action~~ | **Done** — wrapped at the tool decorator, drained so it is said once |
| 8 | ~~A click while no structure is loaded fails like the tool does~~ | **Done** — the two error strings are asserted equal |

### 4.1a What the slice turned up that the plan did not

**The handler cannot run inside the socket's message loop.** It drives the
viewer, so it sends an action and waits for the reply — and that reply is a
message the same loop has to read. Awaited inline, the loop sits inside the
handler while the handler waits on the loop; the click hangs until its own
budget expires and then blames the viewer. It runs as a task. Mutating that back
to an inline `await` fails three tests by timeout, which is why each carries its
own bound rather than relying on pytest to notice a hang.

**A reply travelling the page's way has no outbox.** The dropped-reply work of
PR 89 protects replies going *to* the server; the answer to a click goes the
other way, on the socket it was asked over, and if that socket dies the button
would wait forever. It now settles as "lost contact before it said whether the
view was applied" — deliberately not "failed", because the server may well have
applied it and a control that claims failure about something that happened is
worse than one that admits it does not know.

**The mutation test for the allowlist was a no-op, twice.** Opening the channel
to forward any name left every test green: `preset()` refuses a bogus name too,
and its refusal reads much the same. The tests now separate the two — the
refusal must offer the *view* vocabulary rather than the preset one, and
`putty`, a real preset that is not a listed view, must be refused *with handlers
registered* so that a forwarded name would genuinely have drawn. The second
condition was itself missing at first, and the test passed for the wrong reason.

**Adopting a bridge is now one function**, `use_bridge()`, because the
interesting half is not the assignment but `on_invoke`. A bridge assigned
straight into the module global is a socket a page can talk to with no rule
about what it may ask for, and the differential harness was doing exactly that.

### 4.2 The failure modes to write tests against first

Each of these has already happened once in this repo, in a different costume.

- **The button reports success and the panel does not move.** The right-panel
  tab did exactly this: `setProps` wrote layout state without firing the event
  React redraws on. Assert the *pixels*, never the control's own state.
- **The reply is lost.** A click during a long render hits the same dropped-reply
  path that cost a day — the outbox handles it, and the test should exercise
  a click while a capture is in flight.
- **The allowlist is checked against a hand-written list** that drifts from the
  registry. The going-public pass found a nine-item list where fourteen tools
  existed, because it read a file instead of asking.

### 4.3 Out of scope for the slice

More than one button; parameterised views; anything that computes; styling the
control beyond what the existing tab uses.

---

## 5. Stubs — what comes after, and what each still needs

Nothing below is designed yet. These are placeholders with their known
unknowns, to be filled in as each is taken up, and **corrected in place
afterwards** as every other plan document in this repo has been.

### 5.1 The style presets — shipped 2026-08-17

Six recipes: `textbook`, `cinematic`, `pointillist`, `bfactor`,
`hydrophobic-surface`, `putty`. All six are in `preset()`, and the estimate
held: no new rendering, no new dependency, nothing but compositions of tools
that already existed.

**Two of the six were replaced after a look at the pictures** (2026-08-18).
`bfactor` said what `putty` says, in colour alone rather than colour and width,
and `pointillist` was a novelty rather than a way of reading a structure.
`spacefill` and `skeleton` took their places, answering the two questions
nothing else in the catalogue answered: how the molecule packs, and what the
atoms are. The count is unchanged; the names below are the ones that shipped
that day, not the ones in `preset()` now.

**The one open question is answered, and the answer removes a dependency.**
This stub asked whether `putty`'s tube width varies with B-factor by default or
needs the `uncertainty` **size** theme, which protean does not expose — and
concluded that if it did, this work was tied to cryo-EM §4 ("size by scalar").
It does not. `putty`'s provider declares `defaultSizeTheme: uncertainty`, so
width follows B-factor with nothing passed to it.

The source says so, and the source is where this document has gone wrong
before, so it was measured too: the same coordinates loaded twice, once with the
deposited B-factors and once with every B-factor flattened to their mean. The
two putty frames differ by **0.0202** of the frame; the cartoon control, whose
size theme is uniform, differs by **0.000125**. Nothing else about the two files
differs, so the width is the B-factor. **§5.1 and cryo-EM §4 are
independent.**

**What the work turned up that the plan did not predict**, and it is not about
presets at all: **drawing the same handle twice through `show()` lands on two
different cameras.** The first draw keeps the framing the load preset chose; the
second refits to what is actually on screen and then holds — 0.1438 of the frame
between them on 1UBQ, reproduced with plain `show()`/`hide()` calls and no preset
involved. So the first figure anyone captured after taking the scene over was
framed for a scene that was no longer there, and applying a view twice gave two
pictures. The presets now ask for the frame outright with `reset_view()`, listed
in the steps, which makes a view idempotent — the property §5.2's switcher needs.
The underlying `show()` behaviour is backlog 26.

**A second camera defect fell out of the first, and CI found it rather than this
machine.** `load_structure` never waited for the camera the load preset moves,
though `focus`, `orient` and `reset_view` always have — so a capture taken
straight after a load could be mid-tween. Locally the gap is 0.000125 of the
frame and invisible; on a CI runner it is 0.0080, which failed a control
assertion in the putty test above. Backlog 27, fixed in the viewer. Worth noting
*how* it surfaced: not by inspecting `load_structure`, but because a test
asserted that something which must not change had not changed, and then ran on a
machine that was not this one.

**Two limits worth stating rather than discovering later:**

- **A view coloured by B-factor uses only the cold half of its ramp on an
  ordinary crystal structure.** Mol\*'s `uncertainty` colour theme has a fixed
  `[0, 100]` domain and `color()` passes no theme parameters, so 1UBQ's
  B-factors — 2 to 47 — occupy 0.02 to 0.47 of it. Found on `bfactor`; it
  outlived that view, because `putty` colours the same way. The picture is correct and the contrast is lower
  than PyMOL's `spectrum b`, which fits the ramp to the data. The fix is a
  domain on `color()`, not a change to the preset; `color_by_rmsf` works around
  the same limit today by rescaling values before it sends them.
- **A whole-scene view discards a camera the caller had moved.** Stated in the
  tool description, because the alternative — leaving it — is the
  non-deterministic behaviour above.

**`textbook` calls `illustrative` rather than repeating it.** The two would
otherwise be near-duplicates: `illustrative` is the styling, `textbook` is the
styling plus the decision about what to draw. Composing them keeps one recipe
and makes the relationship visible in the reply's `steps`.

### 5.2 The view switcher — shipped 2026-08-18

Turning one button into several. Depends entirely on how §4 feels: if the
round-trip is sluggish, the answer is a different UI, not more buttons.

**§4 is done and the round trip is fine**, so this stays "more buttons" rather
than becoming a redesign. Two of the unknowns are answered by the work below it:
the drawing views are **exclusive**, because they all draw through the one
shared handle and so replace their predecessor rather than stack; and a view a
structure cannot take **refuses and says so on the control**, which is what
`textbook` on a ligand-only entry already does. Adding a view is now two lines —
an entry in `_PAGE_VIEWS` and a button that names it.

**It is a menu**, grouped into what draws, what restyles and what layers on
top — which answers the affordance question the other way from how it was
posed: the grouping carries the distinction, so a click that changes lighting
and a click that changes the whole picture do not read identically. Keyboard
shortcuts were never needed.

One thing the stub did not anticipate, found by watching someone use it: a run
of views leaves no way back, because each hides `auto` and replaces the shared
handle. `default` is the first entry now.

### 5.3 The rest of the catalogue — planned 2026-08-18

Seven MCPymol views remain. Re-read against their implementations rather than
their names, they fall into three tiers, and two of them are not where this
document previously put them.

**Tier 1 — a recipe plus a parameter.** Each takes a target, so each is a tool
rather than a menu entry, per §2. Doing them together because they share that
shape and three are recipes over machinery that already exists.

| view | what it needs |
|---|---|
| `plddt` | a colour theme — but **blocked**, see below |
| `interface` | **shipped**: `interface_view(a, b)` |
| `ligand` | **shipped**: `ligand_view(resn)` |
| `mutation` | **shipped**: `mutation_view("A123G,V45L")`, and it verifies |

Three further improvements, approved 2026-08-18:

- **Carbon colour on the all-atom views.** Mol\*'s `element-symbol` theme
  defaults `carbonColor` to `chain-id`, which is why `skeleton` and `spacefill`
  came out with green carbons rather than CPK grey. A neutral carbon would look
  markedly more like a textbook model. Needs `show()` to pass theme parameters,
  which it cannot today.
- **A `default` entry restoring the load-time picture.** Watched go wrong:
  clicking eight views in a row leaves no way back to what the load produced,
  because every view replaces the last and nothing restores the start.
- **Views report what they drew** — `interface` its contact count, `ligand`
  which ligand and how many copies. The replies are already structured; making
  them informative costs nothing and tells a model what to do next.

**`plddt` is the exception and it is not cheap, because it cannot be reached.**
The recipe is one theme, and Mol\* ships the official AlphaFold palette. But
pLDDT is a property of a predicted model and protean cannot load one:
`fetch_structure(source="alphafold")` is pinned to a retired `model_v4`, and a
model fetched by hand fails the analysis parser under the default biological
assembly. Backlog 33 and 34. **Do those first or leave `plddt` alone** — a view
of a structure nobody can load is a view nobody can use, and building it would
report progress that is not there.

**Stale as of 2026-08-21: backlog 33 and 34 are fixed.** Predicted models load,
so the blocker above is gone — and with it the protection it was accidentally
providing. pLDDT and B-factor are the same column with opposite polarity, so
`size("uncertainty")` on a predicted model now draws the most confident regions
fattest. Backlog 41, and `docs/soft-matter-review.md`.

**Every one of these refuses when its target is not there, and says what is.**
Charlie's call, 2026-08-18: the improvement below is not `mutation`'s alone.

| view | what it refuses |
|---|---|
| `plddt` | an experimental structure — see why this one is a correctness fix, below |
| `ligand`, `pocket` | a residue name the structure does not contain, naming the ligands it does |
| `interface` | two chains that do not touch, rather than drawing an empty highlight |
| `crosslink` | a structure with no disulfides and no metals, rather than a bare cartoon |
| `mutation` | a residue that is not what the notation claims |

**`plddt`'s refusal is a correctness fix and not a nicety.** pLDDT rides in the
B-factor column, so colouring a crystal structure with the AlphaFold palette
*works*: it maps B-factors of 2-80 onto a 0-100 confidence scale and produces a
confident-looking picture that means nothing, from which someone reads "low
confidence" off a well-ordered loop. It has to detect that the model is not
predicted and refuse.

`mutation` is the one worth doing better than the original. It should **verify
the stated residue is what the file says it is** and refuse when it is not: a
mutation view that silently highlights the wrong residue because the numbering
is offset is this project's failure mode wearing a lab coat. MCPymol does not
check.

**Tier 2 — small analysis from pieces that exist. Shipped 2026-08-21.**

`crosslink_view()` is disulfides and metal coordination: cysteine SG pairs
inside bonding distance, plus metals and what they touch — a distance filter
over the `metals` keyword rather than new machinery, as estimated. It refuses a
structure with neither, because a cartoon with nothing picked out looks the
same as a search that failed. Four disulfides on lysozyme, all under 2.5 A.

`pocket_view(resn)` is the lining as a half-transparent surface with the ligand
inside it. Tier 2 as §5.5's correction said, and **not** cavity detection,
which stays unbuilt and unasked for.

**Tier 3 — `pharmacophore`, shipped 2026-08-21, and it cost what §5.4 said it
would.** Both halves had to be built, because the estimate that they did was
the one thing this document got right about it after getting it wrong twice:
per-atom chemical typing from element and heavy-atom connectivity, and per-atom
*categorical* colouring — a third kind of registered theme, since fields are
per-residue scalars and palettes are per-element.

The honest part is the one worth keeping: most crystal structures carry no
hydrogens, so donor and acceptor are **inferred rather than read**, and the
reply says which rules fired. A picture of a typing decision looks equally
confident whichever rule produced it.

Answered: `mutation` verifies, and **refuses** when the file disagrees —
"position 1 holds MET, not TRP" rather than a confident picture of the wrong
residue. The new residue is not checked, because it is not there.

Two things the tier-1 work turned up that this table did not have:

- **A ligand drawn in the pocket's own colours disappears into it.** There is a
  second palette — the same colours with a warmer carbon — for whatever the
  picture is *about*. Before that the maltose came out brown from Mol\*'s
  chain-id carbon default, which reads acceptably by luck rather than design.
- **`carbohydrate` is a representation and a colour theme in Mol\***, and the
  load preset already draws sugars with both. So a bound sugar was visible on
  load and vanished under `textbook` — the ligand gap was easy to miss for that
  one class of ligand and obvious for every other.

### 5.4 Pharmacophore and pLDDT — stub, and cheaper than it says

~~Both are extension work rather than recipe work.~~

**Wrong on the premise, corrected 2026-08-17.** Neither needs an extension
registered, because the *prebuilt* Mol\* bundle protean ships already registers
them. Read from the live registries through `capabilities()`, twice, against two
different server processes:

- **`plddt-confidence` is in the colour-theme registry.** So is the rest of that
  family — `qmean-score`, `pdbe-structure-quality-report`, `rcsb-density-fit`,
  `sb-ncbr-partial-charges`.
- **`interactions` is in the representation registry**, alongside
  `interaction-type` as a colour theme.

The stub's reasoning was sound and its fact was not: it assumed protean builds
Mol\* from source and registers a chosen set. protean loads `molstar.js`, the
prebuilt bundle — that is why bundling from source needs >4 GB of RAM and this
project does not do it — and the prebuilt bundle carries the extensions. **The
bundle-size question therefore does not arise; the wheel already ships them.**

What is *not* measured, and is the same trap this document has fallen into
before: **whether either reaches the pixels.** Being in the registry means
`show()` and `color()` will accept the name, which is exactly what the planning
probe once mistook for the theme working. Each needs the differential treatment
§5.1's themes got — drawn against a hidden scene, compared as pixels — before
anything is built on it.

Still open, and untouched by the correction: whether to use Mol\*'s pLDDT theme
or protean's own banded palette, since the bands are conventional and readers
expect the standard colours.

**And the `interactions` question is now answered, against this document.**
Reading MCPymol's `pharmacophore_view`: it colours *a ligand's own atoms* by
feature type — donor, acceptor, hydrophobe. The `interactions` extension
computes **interactions between atoms**, which is a different claim entirely.
It cannot type a ligand's atoms and was never going to.

So pharmacophore needs two things protean does not have: per-atom chemical
typing from element and connectivity, and **per-atom categorical colouring** —
`color_by_potential`, `_conservation` and `_rmsf` all colour by a *scalar*, and
a feature class is not one. Neither is enormous; neither is free, and "nearly
free via the interactions extension" was wrong.

§6 predicted exactly this — *"It computes contacts. A pharmacophore is a claim
about what a site wants, which is not the same thing"* — and the prediction was
written, then ignored, then confirmed. Predicting an error does not prevent it.

**The blocker is elsewhere, and it is real.** pLDDT is a property of a predicted
model, and protean cannot currently load one: `fetch_structure(source=
"alphafold")` is pinned to `model_v4`, which AlphaFold DB has retired — 404 for
every accession tried — and a model fetched by hand fails the analysis parser
under the default `assembly="biological"` because predicted models carry no
`pdbx_struct_assembly_gen`. Backlog 33 and 34. Both were found by trying to
measure this section rather than by reading it.

Decided already: **pLDDT is not a GUI toggle.** It is a property of the model,
meaningful for a predicted structure and meaningless for an experimental one, so
it belongs where it can be offered when applicable rather than sitting in a menu
that is wrong most of the time.

### 5.5 Pocket detection — ~~the one genuinely open problem~~ two problems

**Corrected 2026-08-18 by reading MCPymol's implementation instead of its
name.** Its `pocket_view` is *"all residues within 5 Å of the ligand shown as a
surface"* — a selection and a surface. **Matching it is Tier 2 work**, and it
was called the hardest thing here on the strength of the word "pocket".

The hard thing is real cavity detection: finding pockets *without being told
where to look*, which is an algorithm and probably a dependency (fpocket and
its kin). That is still genuinely open — and **nobody has asked for it**, which
is the more useful half of this correction. The version people want is the
cheap one, and it shipped 2026-08-21 as `pocket_view(resn)`.

### 5.6 Dynamics — decided, and the decision is to not build it

Mol\* ships built-in animations: `model-index` (frame playback), `camera-spin`,
`camera-rock`, `explode-units`, `assembly-unwind`, `state-interpolation`,
`state-snapshots`.

protean already owns the analysis side (`rmsf`, `rmsd_series`) and the capture
side (`record_trajectory`, `movie`). **Playback is the piece worth borrowing
rather than rebuilding**, and rebuilding it would be the clearest possible case
of reimplementing the thing protean is built on.

### 5.7 Electrostatics — mostly shipped, and one dependency worth removing

MCPymol has two electrostatics views. They are not the same kind of thing, and
the difference is the whole point.

**`poisson_boltzmann_view` is already shipped, and with better manners.**
`electrostatics(method="apbs")` followed by `color_by_potential()` does what it
does. protean adds what MCPymol's version does not report: *which solver
actually ran*. `method="auto"` uses APBS when a runnable binary is present and a
screened Coulomb field otherwise, and the reply always says which — on the
grounds that a potential whose provenance is unstated is worth nothing. The
fallback is calibrated rather than asserted: against APBS on ubiquitin it tracks
surface potential at r = 0.96 with 94% sign agreement, running about 1.6x low in
magnitude.

**`electrostatic_view` is not a solve at all**, and that is easy to miss. It
assigns charges to terminal charged atoms — or to whole charged residues — and
colours from those. It is a fast qualitative proxy: *roughly where are the
charges*, with no solver and no wait. Mol\*'s `partial-charge` and
`formal-charge` themes give protean the same thing for nothing.

**So it is worth having, and the naming is the trap.** Shipping something called
an electrostatics view next to a tool that runs Poisson-Boltzmann would create
exactly the confusion the provenance rule exists to prevent. **It gets named for
what it is — charge colouring, not potential** — and its description says it is
a proxy, in the same sentence a reader meets it.

#### The wart to fix on the way past

`color_by_potential()` takes a **file path** to an OpenDX grid, defaulting to
whatever `electrostatics()` last wrote. `load_volume()` produces **handles**.
The two do not meet: a potential map loaded from anywhere else cannot reach a
colouring call at all.

That is already open as cryo-EM §1.3 (`color_surface_by_volume`). Doing it here
collapses two colouring paths into one and makes electrostatics consistent with
every other volume.

#### The solver, and the seam that is already there

**Decision, 2026-08-17: keep APBS for now, and keep the ability to switch.**

The switch is worth designing for because APBS is the reason
`method="auto"` needs a fallback at all — it is an external binary, absent on
most machines, and it is why the Coulombic path had to be calibrated in the
first place. **sashimi** — a sibling electrostatics project, not public at the
time of writing, so deliberately unlinked — is gaining a **pure-Python
solver**, which would make a real Poisson-Boltzmann answer available with no
binary to install. For a tool people add with one line, that is the difference
between a feature most users get and one most users fall back from.

**The seam already exists and needs nothing built.** `method` is an enum whose
contract is "say which one ran", so a third value slots in beside `apbs` and
`coulombic` without changing any caller. That is the whole reason this can be
deferred safely.

**Two ways to consume it, and they are not equally cheap.** Worth deciding
before, not during:

- **As a library** — protean imports it and calls it in-process. Cheap, testable
  the way everything else here is testable, and it keeps protean self-contained.
  This is the attractive one, and it is what a pure-Python solver makes possible.
- **As an MCP server** — protean becomes an MCP *client* of sashimi. That is a
  new process to run, a new failure surface, and a dependency a user has to
  install and configure separately. It buys nothing the library route does not,
  unless sashimi's solver stays unavailable as a package.

**What has to be true before switching**, none of it checked yet: the solver
agrees with APBS to within something stated (the same calibration the Coulombic
path got, not a weaker one); it emits or can be made to emit OpenDX, or
`color_by_potential` has been moved to handles by then; and its runtime on a
protein of ordinary size is not so much worse that `auto` would rather have the
binary.

### 5.8 A menu that knows what is in force — stub, and wanted

Every entry the menu offers is **one-shot**: ask for a view, the server applies
it. Nothing anywhere records what was applied, which has two visible costs.

**The menu cannot tick the active view**, so it looks unfinished, and someone
has no way to see which of the five drawing views they are looking at.

**And it forced two entries where one control belongs.** Charlie asked for
toggles — light/dark ground, show/hide sidechains — and they shipped as pairs
(`light-ground`/`dark-ground`, `sidechains`/`hide-sidechains`) because a toggle
has to know which way it is currently set. Tracking that in the page would be a
lie the moment a model changes the background, which it can at any time. A
control that misreports its own state is worse than one that needs two entries,
so the pairs are honest rather than good.

**What a stateful version needs**, none of it built:

- The server records what is applied — which drawing view, which styling,
  which layers are up. `_handles` already knows the layers by name, so
  `sidechains` and `ghost-heart` are nearly free; the drawing and styling
  presets record nothing today.
- That state reaches the page. It cannot ride the handshake alone: a model can
  change the scene at any moment, so the page has to be *told* when it changes,
  the way `protean_views` is pushed when the catalogue arrives late.
- A model is told too, or the two disagree. The user-action channel already
  carries the reverse direction — what the person did, into the model's next
  reply — and this is the same problem pointing the other way.

**The trap to avoid**, and it is the reason this is a stub rather than a patch:
a menu that shows state it infers rather than state it was told is the
silent-success failure in a new place. It would look right and be wrong exactly
when a model and a person are both working, which is the case protean exists
for. Better no checkmark than a stale one.

### 5.9 Illustration styles — planned 2026-08-18, built 2026-08-21

Three looks borrowed from how structures were drawn before they were rendered.
The first two are recipes over controls protean already has; the third is not,
and the difference is the whole of this section.

**What is already exposed**, checked rather than assumed: shading `cel` (with a
step count), `flat`, `normal`, `xray`; lighting rigs `flat`, `rim`, `ring`,
`standard`, `studio`, `three-point`; materials `matte` through `chrome`; and
effects for outline (colour and scale), occlusion, **shadow**, **depth of
field**, bloom and sharpening.

#### `painting` — after Irving Geis

All-atom, painterly, depth carried by shading rather than by line. Sketch:
spacefill or ball-and-stick, occlusion strong, **shadow on**, `ring` or `studio`
lighting, matte, **no outline**, a warm muted ground, possibly a little depth of
field. Geis's myoglobin is all-atom with soft modelled light, so the levers line
up with the subject.

**Named `painting` rather than `geis`, deliberately.** A view named after a
person implies more than a recipe can deliver, and Geis's name does not have
currency as a style term the way some do. The docstring credits him and says
plainly that it is an homage and not a facsimile.

**Built, and the sketch's open choice is settled: spheres.** Both were drawn on
myoglobin and looked at. Ball-and-stick — the nearer relative of what Geis
actually drew — came out as a thicket of green wire with no depth in it at all,
because occlusion and a cast shadow need a surface to fall across and a stick
model gives them almost nothing. The sphere model is the only one of the two
this lighting can model. What it costs is the interior, which a spacefill always
costs.

**Two things the sketch did not have, both visible only in the picture:**

- **CPK is what makes a spacefill read as a rendering.** Hard green carbon
  against hard red oxygen is the loudest thing in an all-atom frame, and no
  amount of ground and light gets past it. `painting` registers its own element
  palette through `define_elements` — warm stone carbon, slate nitrogen, brick
  oxygen, ochre sulfur, and the iron named so a heme is findable — which is
  machinery that already existed for the ligand views. It is applied *after* the
  draw, because a palette has to be registered with the viewer before anything
  can name it and registering is itself a viewer call; the reported steps say
  so rather than leaving it implied.
- **Depth of field was left off.** The sketch allowed "possibly a little".
  `cinematic` is the view that owns blur, and a figure that quietly defocuses
  part of the molecule is a cost paid by every caller who did not ask for it.

`ring` versus `studio`: `studio` — a warm key against a cool fill is the closer
description of how the original was lit, and `ring` is nearly shadowless, which
throws away the one effect the recipe most depends on.

#### `richardson` — the ribbon diagram

Cartoon, pale flat palette, cel shading at two steps rather than four, a thinner
outline, white ground. Mol\*'s cartoon already draws arrowed strands and coiled
helices, which is her invention; this is mostly a matter of restraint in the
styling.

**Kept separate from `textbook` on purpose, for now.** They overlap heavily —
`textbook` is cartoon, secondary-structure colours, cel and outline — and the
honest expectation is that one of them ends up absorbing the other. Building
both and looking at them side by side is the cheapest way to find out which,
and *that* is the argument for two entries rather than a guess at one.

Unlike `painting`, this name is the standard term: a Richardson diagram is what
these are called, so using it credits her rather than borrowing her.

**Built, and the answer to the overlap is that both stay.** `richardson` and
`textbook` render measurably differently from each other and from every other
view in the catalogue, which is what the suite asserts — so neither absorbs the
other. This section expected one of them to, and building both rather than
guessing is what settled it.

**The thinner outline does not exist.** Mol\*'s `outline.scale` is
`min: 1, step: 1` (`mol-canvas3d/passes/outline.js`), and `illustrative` already
sits at 1, so the floor *is* the default and a smaller number would have been
clamped and reported as applied — this project's own failure mode, arriving
through a lever the plan named. The available way to make an edge recede is
colour: the line is `#4a4a4a`, and the suite measures it, because a substitution
like that is exactly the kind that silently does nothing.

What the picture turned out to want, and the sketch had not said: `standard`
lighting rather than `flat`. Two cel bands under a purely ambient rig have
nothing to band, and the lit-side/shaded-side pair is what makes a ribbon read
as a drawn object rather than a silhouette. On myoglobin the lit side goes
nearly white, which is what her drawings look like.

#### Cross-hatching and hedcuts — a capture finish, not a view

**Mol\* has no hatching, stippling or halftone anywhere.** The whole
`mol-canvas3d` post-processing vocabulary is antialiasing, background, bloom,
depth of field, occlusion, outline, shadow and sharpening. Checked across the
tree, not inferred.

Three routes, and the middle one is chosen:

1. **Approximate with what exists.** Cel shading at two steps, black outline,
   greyscale, white ground — a posterised line drawing. Print-like and
   striking, and *not* cross-hatching. Worth building anyway because it is
   nearly free, and it may turn out to be enough.
2. **Do it in Python, at capture. Chosen — and shipped 2026-08-19.**
   `snapshot()` already returns a PNG through Pillow. Hatching from a luminance
   map is ordinary image processing: band the tones, overlay line patterns at a
   different angle per band, composite. That gives a real hedcut, works over
   *any* view, and touches Mol\* not at all.

   **Three things the sketch above did not know**, all found by looking at the
   output rather than by reasoning about it:

   - **A white ground is not white.** The publication preset's is about 252, so
     the lightest band caught the entire background and sprinkled strokes
     across the empty half of the frame. It looked like dirt on the plate.
     Anything brighter than 0.94 luma takes no ink at all now.
   - **Proportional tone is far too dark.** Mapping darkness straight onto
     bands put a mid grey two thirds of the way to solid, and a cartoon whose
     shading sits mostly in the middle came out as a black mass with holes in
     it. Darkness is raised to 1.7 before banding, which is what engravers do
     by hand and for the same reason.
   - **Strokes have to be visibly apart.** At a 240th of the frame they landed
     about three pixels apart, and four crossed directions at that spacing is
     not cross-hatching but a dot screen. A 110th, with three directions.

   The reply also reports the **ink fraction**, which was not planned. The
   caller is usually a model and cannot look at the file, and the difference
   between a good print and a solid black rectangle is not visible in a byte
   count.
3. **A custom Mol\* post-processing pass.** The right answer technically and
   blocked practically: it needs Mol\* built from source, which this project
   deliberately does not do — the prebuilt bundle is shipped precisely because
   a source build wants more than 4 GB. Charlie has raised forking Mol\* to
   extend it, which would unblock this and more; recorded here as a direction
   under consideration rather than a decision. It would buy a live preview and
   cost a fork to maintain and a heavier CI.

**It belongs on `snapshot()`, not in the menu**, and that is the design point
rather than an implementation detail. Every entry in the menu changes the
*scene*; a hatch is applied to the *capture*. Two consequences follow and both
have to be stated where a caller meets them:

- **The viewer will not show it.** There is no live preview, which cuts against
  the menu being where looks are tried. A caller sees it only in the file.
- **`snapshot()` would return something the viewer did not draw.** Still the
  same molecule, rendered by a second renderer — but protean's whole claim is
  that the picture and the analysis describe the same thing, so the reply has
  to say the finish was applied after capture rather than leave it implied.

### 5.10 `felt` — felted wool, 2026-08-21

The first treatment out of `docs/soft-matter-plan.md` to land in protean, and
it lands **as a style rather than as a treatment**, which is the whole point of
the entry.

The plan's SM-01 specifies felted wool with a data channel: surface area drives
fiber length. `docs/bakeoff.md` built that and could not read it. A per-atom
number bound to radius jitter is invisible against a surface already textured
at the same spatial frequency, and the plan's own P3 says a treatment that
cannot carry data is decoration. Charlie's call was to ship it anyway, as a
look — so the docstring says it carries no data instead of implying a
measurement, the way `painting` does.

**It is not only decoration, though, and the reason is worth keeping.** A hard
glossy shell asserts that the van der Waals surface is a boundary. It is not:
it is where a probability has fallen off to a threshold somebody chose. A fuzzy
edge is the more honest picture of the same number — the argument `putty`
already makes about B-factor, applied to the surface itself.

**What it needed that protean did not have.** One control and one theme:

- **`material(bumpiness=, bump_frequency=)`.** `bumpiness` had been pinned to
  zero and undocumented since the material tool was written, on the stated
  grounds that it does nothing unless `bumpFrequency` is above zero "and that
  defaults to 0". Counted across the whole registry rather than sampled:
  **eleven representations declare the parameter and seven default non-zero** —
  spacefill, molecular-surface, gaussian-surface, orientation and polyhedron at
  1, cartoon and putty at 2. Four default to zero (ball-and-stick, backbone,
  carbohydrate, ellipsoid) and five declare none at all. Pinning `bumpiness`
  killed the control everywhere; on those seven it would have worked untouched.

  A first pass at this sentence said "dead on four of the five representations
  it would have worked on", from a sample of five. A review caught it, counted
  nine, and missed two more. The registry is the thing to count.
- **A `jitter` size theme**, hash-based rather than random. An RNG gives each
  symmetry copy of an atom a different radius, which reads as a broken
  structure rather than a texture and changes on every reload.

**Three things measured while building it**, none of which were guessable:

- **Frequency is fineness, so raising it makes a surface look smoother.** On
  1UBQ spacefill: 0.036 of the frame moves at frequency 1, 0.018 at 3, 0.004 at
  6. A caller reaching for "more texture" by turning it up gets less. `felt`
  uses 3.
- **A cartoon is too little surface to test a bump on.** The strongest cartoon
  setting moves 0.0073 of the frame against the suite's 0.008 threshold; the
  same setting on spacefill moves 0.036. A test written against the load
  preset's cartoon would have failed for a working control.
- **`ring` lighting was the wrong first guess.** It was chosen because it suits
  a surface whose curvature would vanish into one hard highlight — but felt has
  no speculars to begin with, so all it did was flatten the form. `three-point`,
  picked by looking at both.

**The halo is the part that can vanish unnoticed.** A second spacefill at
1.12x and alpha 0.2, under its own handle for the reason `ghost-heart` uses
one. At that opacity nobody can tell from the picture whether it drew, so the
test asserts the handle exists rather than pretending to measure it.

---

## 6. What this plan will get wrong

Every plan document in this repo has been wrong in ways only measurement found,
and the corrections were more useful than the originals — `cryoem.md`
prescribed reading `data.grid.stats`, which *is* the MRC header, the exact
failure it was warning about.

The candidates here, stated in advance so they can be checked off or laughed at:

- **That the round-trip through the server feels instant.** It is a WebSocket on
  loopback, so it should. If it does not, §5.2 changes shape entirely.

  **Right, with a caveat nobody predicted, checked 2026-08-17.** The transport
  is not the cost — the *view* is. `ghost-heart` meshes a molecular surface,
  which takes as long from a click as it does from a tool, and under software
  rendering that is seconds rather than milliseconds. So the button disables
  itself for the round trip and says so, which is the affordance a switcher
  needs anyway. §5.2 does not change shape; it inherits a control that already
  knows how to be busy.
- **That "recipe work" is as cheap as it sounds.** The four existing presets are
  small, but each needed a differential test proving the picture changed, and
  the threshold for "changed" was argued over.

  **Half right, checked 2026-08-17 against §5.1.** The recipes themselves were
  as cheap as promised — six presets, no new rendering, the existing threshold
  reused unchanged. What was not free was everything around them: taking the
  scene over from the load preset without leaving two coincident
  representations, making a second view replace the first rather than stack,
  and the camera behaviour in backlog 26, which nothing in the plan anticipated
  and which is most of what the work turned out to be about.
- **That the interactions extension is a pharmacophore.** It computes contacts.
  A pharmacophore is a claim about what a site *wants*, which is not the same
  thing, and the gap may be most of the work.
