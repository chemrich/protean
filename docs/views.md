# Views, and driving them from the viewer

Planned 2026-08-17, not started. Two things that turn out to be one thing:

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

- **pLDDT is not in the base theme registry.** It lives in the
  `model-archive/quality-assessment` extension, which protean's build does not
  register. Colouring by pLDDT is therefore a build change, not a recipe.
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

`textbook`, `cinematic`, `pointillist`, `bfactor`, `hydrophobic-surface` and
`putty` take nothing but an optional handle. `preset(name, handle)` expresses
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
page   →  server   {action: 'protean_invoke', view: 'ghost-surface', args: {}}
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
"…, and since your last call the user applied the ghost-surface view."
```

No client support needed, and a model cannot act on a stale picture without
being told.

## 4. The vertical slice — what gets built now

One button, one view, end to end. Deliberately not a framework: it proves the
loop, the allowlist and the state reporting together, and everything afterwards
is adding rows to a list.

**Chosen view: `ghost-surface`**, because it already exists as a preset, takes
only a handle, and its effect is obvious in a screenshot.

### 4.1 Acceptance criteria

| # | Criterion | How it is checked |
|---|---|---|
| 1 | ~~Every theme and representation the later phases need reaches the pixels~~ | **Done 2026-08-17** — eight differential tests, instrument rebuilt |
| 2 | ~~`ellipsoid` either draws or refuses~~ | **Void** — it draws; the planning probe was wrong |
| 3 | A page-initiated `protean_invoke` runs the same code path as `preset()` | Assert on the calls the server issues, not on the picture alone |
| 4 | The scene arrives back over the ordinary action channel | Differential test: click, then compare pixels |
| 5 | The allowlist admits no tool taking a path | Enumerate from the tool registry, not from a list in a file |
| 6 | An unlisted view name is refused, and says what is available | Mutation-tested |
| 7 | The next tool reply names the user's action | Assert the string, then assert it clears |
| 8 | A click while no structure is loaded fails like the tool does | Same refusal, same wording |

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

### 5.1 The style presets — stub

Six recipes: `textbook`, `cinematic`, `pointillist`, `bfactor`,
`hydrophobic-surface`, `putty`.

Known: primitives all exist. Unknown: whether `putty`'s tube width varies with
B-factor by default, or needs the `uncertainty` **size** theme, which protean
does not expose at all — `size` scales uniformly today. That gap is also
cryo-EM §4 ("size by scalar"), so the two should be done together.

### 5.2 The view switcher — stub

Turning one button into several. Depends entirely on how §4 feels: if the
round-trip is sluggish, the answer is a different UI, not more buttons.

Unknown: whether the control is a strip, a menu, or keyboard shortcuts; whether
views are exclusive or compose; what happens on a structure the view makes no
sense for.

### 5.3 Parameterised view tools — stub

`ligand`, `mutation`, `crosslink`. Each takes a target and gets its own tool per
§2.

Known: `crosslink` is nearly free — residue-pair geometry is `near()` plus a
distance filter. Unknown: whether `mutation` should verify the stated residue
actually matches the structure, which it should, and what it does when it does
not.

### 5.4 Pharmacophore and pLDDT — stub

Both are extension work rather than recipe work.

Pharmacophore rides on Mol\*'s `interactions` extension. Unknown: what
registering an extension does to the bundle size the wheel ships, and whether
its interaction types are the ones a pharmacophore wants or merely adjacent.

pLDDT needs `model-archive/quality-assessment` registered. Unknown: whether to
use its theme or protean's own banded palette — the bands are conventional and
readers expect the standard colours.

Decided already: **pLDDT is not a GUI toggle.** It is a property of the model,
meaningful for a predicted structure and meaningless for an experimental one, so
it belongs where it can be offered when applicable rather than sitting in a menu
that is wrong most of the time.

### 5.5 Pocket detection — stub

The one genuinely open problem. Everything else here is exposure or recipe; this
is an algorithm and probably a dependency.

Unknown: everything. Whether to compute it, borrow it, or decline it.

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

---

## 6. What this plan will get wrong

Every plan document in this repo has been wrong in ways only measurement found,
and the corrections were more useful than the originals — `cryoem.md`
prescribed reading `data.grid.stats`, which *is* the MRC header, the exact
failure it was warning about.

The candidates here, stated in advance so they can be checked off or laughed at:

- **That the round-trip through the server feels instant.** It is a WebSocket on
  loopback, so it should. If it does not, §5.2 changes shape entirely.
- **That "recipe work" is as cheap as it sounds.** The four existing presets are
  small, but each needed a differential test proving the picture changed, and
  the threshold for "changed" was argued over.
- **That the interactions extension is a pharmacophore.** It computes contacts.
  A pharmacophore is a claim about what a site *wants*, which is not the same
  thing, and the gap may be most of the work.
