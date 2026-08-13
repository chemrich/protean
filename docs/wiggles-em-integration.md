# wiggles-em for two hosts — what protean has to build, and what it must not

Planned 2026-08-13, not started. How protean comes to depend on
[wiggles-em](https://github.com/chemrich/wiggles-em) without either repo
growing a copy of the other, and without wiggles-em bending toward whichever
host was integrated last.

Measured against wiggles-em at `a86bd70` and protean at `9bc94e4`.

**The headline: most of this is already built, and it was built for protean.**
`scene.py`'s `Sel` docstring says *"Scene ops cannot carry PyMOL selection
strings: protean parses a subset with real gaps"*; `Sel.first` says *"PyMOL and
protean both have a first-of operator"*; `pyproject.toml` justifies zero runtime
dependencies with *"Two hosts depend on this package and a third-party pin here
becomes a constraint in both of them."* This plan finishes a two-host
architecture rather than proposing one.

---

## 1. The two seams, and why only one of them works

wiggles-em has two boundaries between its views and a viewer. They are at very
different levels of maturity, and conflating them is why the integration looks
harder than it is.

### 1.1 The output seam is done

`wiggles_em/scene.py` is a viewer-neutral intermediate representation, and a
genuinely good one:

- Fifteen op types — `ColorByScalar`, `SizeByScalar`, `ColorFlat`, `Show`,
  `Hide`, `Label`, `Opacity`, `Isosurface`, `ColorSurfaceByMap`, `Arrows`,
  `Frames`, `Morph`, `Delete`, `Legend`, `Scatter`.
- `Sel` as a structured **value**, not a string — `obj`, `prop`, `lt`,
  `residues`, `first`, `all`, combined with `&`, `|`, `~`. A backend can
  inspect a selection before deciding whether it can honour it.
- `ScalarField` keyed rather than positional, with `Granularity` explicit.
- `Unit` (`ABSOLUTE` / `SIGMA`) never defaulted anywhere.
- `Refused` for any op a backend cannot honour — never skipped.

`backends/__init__.py` states the rule this plan is built on:

> Backends are "the only code in this package that knows a viewer exists.
> Everything above here is viewer-neutral" — and normalisation is the
> backend's job, because "a backend whose viewer takes absolute levels passes
> them through."

That last clause has no PyMOL meaning. It was written for protean.

### 1.2 The input seam is not

`wiggles_em/port.py` is PyMOL's wire protocol: `iterate_to_list`, `get_names`,
`count_states`, `get_coords`, `count_atoms`, `get`. Twelve of the seventeen
modules in `src/wiggles_em/` import it.

**But the port's problem is not only that it is PyMOL-shaped. It is doing two
different jobs under one name.**

- It **reads data**: per-atom occupancy, altloc and B-factor, coordinates,
  state counts.
- It **drives the viewer**: `maps.py` and `heterogeneity.py` issue `load` and
  `delete`; `bfactors.py` issues `alter` and `rebuild`.

The second job already has a home — the backend — and some code paths never
moved into it. `scene.py` defines `Delete` and `Isosurface` ops, so the
vocabulary exists; the views bypass it.

**So the driving half needs no new abstraction, only the migration it was
already promised. Only the reading half needs a new protocol.**

---

## 2. The decision: split the port into a source and a sink

**Sink** — the existing Scene and its backends. protean supplies a backend.
Nothing new is invented.

**Source** — a narrow protocol phrased in *structural* terms: atoms carrying
occupancy, altloc, B-factor and coordinates; state counts; object names.
**Not PyMOL actions.** `PymolPort` continues to satisfy it through
`iterate_to_list`. protean satisfies it from the biotite arrays it already
parses in `selections_numpy.load_structure`.

protean must never read atoms back through a browser round-trip. It has better
data locally, and asking Mol\* for per-atom occupancy would be forcing PyMOL's
model onto a codebase that has its own — and would reintroduce the viewer /
analysis divergence that decision 9 exists to prevent.

### 2.1 The B-factor channel stops being shared machinery

Pushing a scalar through the `b` column is a PyMOL idiom; it is the only reason
`restore_bfactors` exists. protean has real scalar colouring already
(`color_by_rmsf`, `color_by_conservation`, `color_by_potential`).

Under the split, the B-factor stash becomes `PymolBackend`'s private
implementation of `ColorByScalar`, and protean's backend simply colours.
`restore_bfactors` stays a PyMOL-only affordance rather than a concept protean
has to explain to a model.

### 2.2 The migration helps PyMOL too

Moving `load` / `delete` / `alter` / `rebuild` out of the views is worth doing
with no second host at all: today those paths touch the session without passing
through the refusal rule, which is the one invariant the package is built to
enforce.

---

## 3. Where each backend lives

`PymolBackend` lives inside wiggles-em, so symmetry argues for putting
protean's there beside it: one place to keep both honest, one test suite, and
wiggles-em could guarantee every op has an implementation on both sides.

**The recommendation is still to put `ProteanBackend` in protean.**

The deciding question is whether wiggles-em's CI could meaningfully test it.
It could not: wiggles-em has zero dependencies and no browser, so a Mol\*
backend living there could only ever be exercised against a fake. And a Mol\*
backend that accepts every op and draws nothing is this codebase's **default**
outcome, not its unlucky one — that is what every entry under
[backlog](backlog.md) §"Bugs — a wrong answer that looks right" has in common.
protean's differential browser job is the only thing on either side that
catches that class of failure. Put the backend where the viewer is.

**The cost, and how to buy it back.** wiggles-em can no longer enforce
completeness across backends. It should therefore publish a **conformance
suite** — an importable test module asserting that every op is either honoured
or explicitly `Refused` — which protean runs in its own CI. Enforcement without
coupling.

The symmetric end-state is `PymolBackend` moving out to MCPymol for the same
reason. Real, but low payoff. Leave it unless the asymmetry starts to hurt.

---

## 4. Plan

### Step 0 — write the boundary down, in wiggles-em

Source versus sink, and which side owns normalisation. One short document, no
code. It is what both hosts get judged against, and writing it first is what
stops the second integration from redefining it.

### Step 1 — give wiggles-em CI

It has none: no `.github/workflows` at all. It is about to be load-bearing for
two public repositories. Zero runtime dependencies makes this nearly free.

**Do this before anything else**, because every later step increases the blast
radius of a package that currently has no automated check that it still works.

### Step 2 — finish moving viewer-driving into the Scene

`load`, `delete`, `alter` and `rebuild` out of the views and into backend ops.
Entirely internal to wiggles-em; verifiable against MCPymol alone, with no
protean involvement.

### Step 3 — define the source protocol

Re-express `PymolPort` as one implementation of it. Still no protean
involvement — and that is the test: **if this step breaks nothing in MCPymol,
the abstraction is honest.** If it needs PyMOL-shaped escape hatches to keep
MCPymol working, it is the old port with a new name.

### Step 4 — build `ProteanSource` and `ProteanBackend` in protean

Against the conformance suite from step 3 *and* the differential browser job.
This is the first step where protean takes the dependency.

### Step 5 — resolve the volume duplication

Today both repos independently decompress `.map.gz`, recognise the MRC magic,
maintain a registry of loaded maps, and convert between absolute and sigma
contour levels. Both were calibrated on EMD-30913.

Split by ownership:

- **wiggles-em owns file interpretation** — header parse, voxel size, the
  `cella/mx` versus `cella/nx` trap, sigma conversion, provenance.
- **protean owns transport and dispatch** — the HTTP path for large volumes,
  Mol\*'s format ids (`ccp4`, `dsn6`, `dx`, `cube`, `dscif`), `dispatch.ts`.

One API change makes this possible: `density.to_sigma` / `to_absolute`
currently take a `MapHeader`. They should take a small statistics value —
mean, rms, and whether the rms is trustworthy — so **protean can feed
statistics measured off the volume Mol\* actually parsed** while MCPymol feeds
header fields. Keep `usable_rms` and `rms_meaning`: that judgment about when a
header's rms cannot be believed is the part protean does not have.

### Step 6 — release discipline

Once protean is public: wiggles-em on PyPI, protean pins a version, and a
compatibility test that fails in CI rather than at a user.

---

## 5. Risks, sharpest first

### 5.1 The atom key is the real hazard

`ScalarField` keys atoms by `(model, rank)` at `Granularity.ATOM`. Its own
docstring warns that a key matching nothing renders as "an ordinary-looking
ramp of the *previous* column" — the field is silently ignored and the
structure keeps whatever colour it had, under a legend naming a quantity never
drawn.

Within one host that is safe **only if the source and the sink enumerate atoms
identically**. protean's conformer resolution deliberately drops atoms from
geometry paths (decision 16). If the source reads every conformer and the sink
colours the resolved state, the keys are drawn from different arrays and every
atom past the first alternate site is off by one.

This is the `sym_id k == ASM_{k+1}` bug in different clothing, and counting
cannot catch it: the field has the right length either way. It needs a direct
conformance test that a key built by the source resolves to the same atom in
the sink.

### 5.2 `Sel.raw` is the one place neutrality leaks

`Sel.raw(text, dialect="pymol")` carries caller-supplied selection text —
`composition_view` takes a selection from the user and scopes it to an object.
A backend that does not speak the dialect must **refuse**, which the design
already provides for.

Decide deliberately whether protean also accepts a `dialect="protean"`, or
whether that view is simply unavailable there. Guessing at the text is how a
selection silently matches the wrong atoms, and the refusal is the honest
answer until someone writes the lowering.

### 5.3 Sequencing against protean going public

protean being private blocks nothing today: a private repository can depend on
a public package. **Do not wait for protean to open to start.**

The one ordering point is the other direction — anything protean upstreams into
wiggles-em becomes public immediately, ahead of protean's own opening. That is
usually fine and occasionally not; it should be a conscious choice per change
rather than a discovery.

---

## 6. What this means for the `cryoem-volumes` branch

The unpushed branch (`bfbf4b4`, based at `efc42e0`, 36 commits behind `main`)
splits along exactly the line in step 5. `volumes.py`, `connection.py`,
`dispatch.ts` and the four tools are protean's own transport layer and should
land here as a normal PR — rebased, with the sigma and statistics logic
deferred to wiggles-em.

Two things to fix on the way in:

- It commits `viewer/node_modules` as a **symlink** (mode 120000) pointing at
  an absolute path on one machine. `.gitignore:12` reads `viewer/node_modules/`
  — the trailing slash matches a directory, not a symlink, which is why it
  slipped through. It would break any other clone.
- Its `docs/cryoem.md` has been partly overtaken. **§2 "Altlocs have to survive
  parsing" is done** — PRs 58–59, decision 16. **§3 "Generalise scalar
  colouring" is no longer a cryo-EM item**: it is the prerequisite for
  `ColorByScalar`, and therefore step 4's critical path.

---

## 7. What would make this plan wrong

Stated up front, because the last three plan documents in this directory were
each wrong in a way only measurement found.

- **If step 3 cannot be done without PyMOL-shaped escape hatches**, the source
  protocol is not a protocol and protean should own its own read path entirely,
  taking only the pure modules (`mapinfo`, `density`, and the maths) from
  wiggles-em. That is a smaller, duller integration and it is still better than
  a shared abstraction that leaks.
- **If `ProteanBackend` has to `Refuse` most of the fifteen ops**, the Scene is
  more PyMOL-shaped than its docstrings claim, and the honest response is to
  narrow what protean advertises rather than to fake the rest.
- **If the conformance suite passes while the browser draws nothing**, the
  suite is testing the fake. That is the failure mode this repository has hit
  most often, and it should be assumed until a rendered pixel says otherwise.
