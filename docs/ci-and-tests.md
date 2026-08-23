# Streamlining the browser job

**Rewritten 2026-08-22, the same day it was written.** The first version was
audited by four adversarial reviews and its central premise turned out to be
false. That version is in the history; this one keeps what survived and records
what did not, because the errors are more instructive than the conclusions.

## What the first version got wrong

It said: *"Setup is not part of that job's cost, it is the cost."* It is not.
Measured on CI rather than extrapolated from a laptop, **browser setup is 12 to
30% of the job.**

Five specific numbers were wrong, and they were wrong in a family:

| Claim | Reality | How it was found |
|---|---|---|
| 84 browser launches | **104** | Instrumenting `viewer_session` and counting, instead of reading code |
| 18–46 min of setup | 12–30% of the job | 46 minutes cannot fit in a 31-minute job — the claim failed its own arithmetic |
| Job takes 31–50 min | **49–79 min** | The 31–50 figure was five days stale when it was written |
| First capture costs 2–3x the rest | **+8%** | Six fresh sessions, first against second |
| CI is slower per session than this laptop | **CI is faster** — 5–10 s against 6.3–27 s | Measured both |

The 84 came from an abstract syntax tree, which cannot see a launch behind
`@pytest.mark.parametrize`, cannot see a session opened inside a helper, and
missed an entire file. Two independent static counts undercounted the same way.
**Runtime launches cannot be counted by reading code.**

The rest share one cause: a part was measured, extrapolated to the whole, and
never checked against the container. That is precisely what this plan accused
backlog 24 of doing.

## What is actually true

**Mol\* 5.11 doubled the job.** `a92f86a` ran 1140 tests in 23m12s; `ae1df78`,
the upgrade, ran 1139 in 48m18s. One fewer test, 2.08x the time, same skips,
same session count. This is a CI-to-CI comparison, immune to the local
contention that spoiled everything else. Filed as backlog 40, and it is the
largest unrecovered cost in the suite.

**One file is about 80% of the job.** `test_render_differential.py`, by CI's
own per-line log timestamps. Within it the expensive items are captures, not
launches.

**Reloading a structure into a live browser really is about ten times cheaper
than a fresh session** — median 10x over six structures, and not a caching
artifact: a *different* structure loaded after a warm-up costs 0.35 s. The
range is wide (3.2x to 22x), so it is a median rather than a rule.

## What was done

- **`--durations=25`** on the browser job. Every estimate anyone has made about
  this job has been wrong, in both directions, and a per-test distribution
  costs nothing. This is the item that ends the guessing.
- **The journal figure rendered the same scene three times.** Parametrized over
  png/tiff/jpeg, each opening its own browser at 4323 px, to establish what a
  file extension does. Suffix, colour mode and the DPI round-trip are not
  functions of pixel count, so those run at 1200 px from one session and the
  journal-size capture runs once. **15m51s to 9m23s**, measured on the same
  assertions, nothing dropped.
- **The browser job is skipped for markdown-only changes**, via a `changes`
  job with a hand-rolled `git diff` that fails toward *running* the job.

## What was measured and rejected

- **A shared Chrome `--disk-cache-dir`.** 94.96 s with, 93.41 s without.
  SwiftShader compiles in-process; the disk cache holds GPU program binaries it
  never produces.
- **Turning down capture supersampling.** The ImagePass runs at `sampleLevel:
  4`, and each level down halves capture cost — level 2 is 3.8x cheaper. It is
  the largest single lever anyone found, and **Charlie's decision on
  2026-08-22 was not to take it**: it shifts about 1% of pixels while several
  thresholds sit at 0.008 to 0.01, and buying a faster suite by loosening what
  the suite can see is the wrong trade for a tool whose product is a picture.
- **Moving the post-merge run to a nightly.** Dropped. The repo is public so
  minutes are free, queue delay is 2–4 seconds so it buys no latency, and in
  **103 post-merge runs the only failure was a network flake**. The nightly
  would also have been cancelled by the `concurrency` block already in
  `ci.yml`, on a schedule nobody is notified about.
- **Sharding.** `ci.yml` already explains why the job runs the whole suite:
  "a list means files never meet", and backlog item 12 lived on `main` inside
  exactly that gap.
- **Warming the image pass per session.** Worth about 0.3 s, not the 3 s it was
  claimed to be.

## What is left, honestly

1. **Backlog 40.** A 2x regression beats every remaining optimisation combined.
   Chrome is ruled out (both runs report 151.0.7922.169) and so is a changed
   `sampleLevel` default (identical in 4.18 and 5.11), so the per-sample cost
   itself grew. Bisecting the 19 releases between them would name the one that
   did it. **Do it on CI, not on a laptop** — see below.
2. **The `--durations` output**, which lands with the next run of this branch
   and should be read before anything else is attempted.
3. **One browser per file, a fresh tab per test.** The re-scoped version of the
   original lever 3: it captures the launch saving with isolation guaranteed by
   construction — new plugin, new dispatcher closure, new canvas props — rather
   than by a reset whose completeness has to be proved. Worth roughly 11
   minutes of a 60-minute job, which is real and is *not* the headline the
   first version claimed.

The original lever 3 — one shared session per file with a `reset` action — is
not recommended. `pytest-asyncio` binds a module-scoped fixture to a
module-scoped event loop that is not running during the test body, which is why
every existing module-scoped fixture opens its session *inside* the fixture and
returns inert data. And the proposed completeness check, diffing
`canvas3d.props`, is blind to the screenshot helper's own values, the camera
pose, the theme registries, and the dispatcher's closure state.

## What the journal-figure gate actually cost, measured after landing

The estimate that justified it was ~600 s by subtraction. The measurement,
from the `--durations` output either side of the change, is **496.7 s**: the
`journal_figures` fixture cost 641.95 s with the capture and 145.23 s without.
So one call was **15% of the job**, not the 19% the estimate claimed.

**And the job total barely moved — 53:49 to 51:08.** Not because the saving is
not real, but because that runner was about 20% slower at everything else:
`finishes` went 235 s to 288, `styled_effects` 197 to 243, `views` 155 to 175,
the lighting rigs 115 to 142. The variance ate the gain.

This is the clearest illustration in this document of its own closing rule. The
saving is attributable *because it was read off a fixture's own cost on both
sides*, not inferred from two job totals — which is what makes a 15% change
measurable at all when a single run's noise is larger than the change.

## Lowering capture supersampling on CI — built, measured, not taken

Kept here because it was built and measured rather than argued about, and
because the measurement is the interesting part.

**The mechanism.** A capture builds its own ImagePass at `sampleLevel: 4` — 16
samples — via `mol-plugin/util/viewport-screenshot.js`. Mol\*'s `imagePass`
getter re-applies `cameraHelper`, `transparentBackground`, `postprocessing`,
`marking` and `illumination` on every access but deliberately leaves
`multiSample` alone, so a level set once after the pass exists survives every
later capture in the session. That makes it a purely test-side knob: touch
`plugin.helpers.viewportScreenshot.imagePass`, then `setProps({multiSample:
{mode: 'on', sampleLevel: N}})` over CDP from `tests/browser.py`, gated on an
environment variable. **No production code changes and nothing protean ships is
affected.**

**What it buys, and it is far less than the per-capture figure suggests:**

```
per capture   sampleLevel 4  3.86 s      sampleLevel 2  1.01 s    3.8x
whole file    sampleLevel 4  28:09       sampleLevel 2  22:25     1.25x
```

That gap is the point. **A 3.8x speedup on captures is a 20% speedup on the
job**, because captures are a minority of wall time even in the
capture-heaviest file — the rest is scene building, draws, settling and session
launches. Anyone reaching for this lever from the per-capture ratio alone will
overestimate it by about three times, which is the same part-for-whole error
that produced the first version of this document.

**What it costs: bit-exact capture reproducibility.** Of 160 tests only one
fails at level 2, and it is the one that cannot cancel out.
`test_going_back_to_a_frame_reproduces_it_exactly` asserts that revisiting a
trajectory frame is bit-identical; at level 2 it differs by a single pixel,
exceeding the comparison tolerance. Measured 3/3 failing at level 2 and 3/3
passing at level 4 — caused, not flaky.

Every other threshold held, and the reason is worth stating: `DISTINCT`,
`STYLED` and `RELIT` compare two frames captured *the same way*, so a uniform
sampling change cancels. Only a claim about exactness cannot.

Keeping CI green at level 2 therefore means skipping that test on CI, which
leaves the determinism guarantee enforced nowhere — a poor trade for 20%, on a
tool whose product is a picture.

**The lead worth following instead.** That reproducibility depends on sample
count at all is suspicious: it suggests a capture reads before something has
finished settling, and more samples merely hide it. That may be the same defect
as backlog 40's doubled capture cost. If it is, fixing it would remove this
lever's only objection — which is a better outcome than spending the guarantee.

## The shuffle test, and what it adds to the job

`tests/test_shuffle_differential.py`, added 2026-08-22. It answers one
question about any data-driven treatment: **does the binding carry its data?**
Render once with the true channel, once with the same numbers permuted across
residues, and diff. Identical frames mean the binding reads nothing.

It exists because `docs/bakeoff.md` drew a confident conclusion from three
treatments rendered on a structure whose B-factor column is `0.00` on all 1,216
atoms. Every channel was constant, every picture rendered, every picture looked
right, and the conclusion had to be retracted. **A binding test on a flat
column is vacuous and looks exactly like a passing one**, which is why the file
carries a degenerate-input guard as well as the diff: `_checked_shuffle`
refuses a channel that takes one value, before anything is rendered. That
guard, not the diff, is what would have caught the bake-off.

**Cost: 8 captures**, two per binding, on a job that already takes 91 of them.
At the measured 3.86 s each that is about 31 s of capture plus four scene
builds — a fraction of a percent of a 49–79 minute job. It is stated here
because silent growth in this file's capture count was the subject of four
recent PRs.

No workflow edit was needed: the `differential` job runs `pytest tests/` whole,
so a new `test_*_differential.py` with `pytestmark = BROWSER_MARKS` is picked
up on its own.

What the four arms measure, in a 722x311 local viewport, against the existing
`STYLED = 0.008`:

| Arm | Difference | Margin |
|---|---|---|
| `define_field` → `color`, ramp on a cartoon | 0.0281 | 3.5x |
| `define_field` → `size`, ramp on a putty | 0.0105 | 1.3x |
| `sasa()` → `define_field` → `color`, burial | 0.0264 | 3.3x |
| identity control: chain id on single-chain 1UBQ | 0.0000 | must be 0 |

Every shipped binding passes. The size arm's 1.3x is the thinnest margin and
looks thin only against the whole frame: a putty tube covers 0.014 of that
viewport where a cartoon covers 0.033, so 0.0105 is 73% of the tube's own
pixels. The figures are also conservative — CI renders into a taller viewport
where the molecule fills more of the frame.

**The control is the load-bearing part.** 1UBQ has one chain, so a chain-id
channel has no permutation but the identity, and the arm must read exactly 0.0.
Without it a shuffle test that always passes is indistinguishable from one that
works. It reads 0.0 because renders here are bit-deterministic once the
ImagePass exists — the same property three assertions in
`test_render_differential.py` already depend on — so a dead binding reads a
clean zero rather than noise.

**Proved able to fail**, which for a test of this shape is the only
verification that counts: with the shuffle replaced by the identity and the
identity guard disabled, all three positive arms failed at exactly
`0.0 > 0.008` and the control still passed.

Three things it deliberately does not do. `conservation()` is not
shuffle-tested — it reaches MMseqs2/ColabFold over the network, and a CI test
that depends on someone else's server is a flake with extra steps. `rmsf()` is
not either: it needs a loaded trajectory, which is a second structure and a
second load for a binding that goes through the same `define_field` path the
ramp arm already covers. `felt` is not shuffle-tested because it has no data
channel to shuffle.

## The rule this document exists to enforce

**Measure the whole thing, on the machine that runs it.**

Local timings here are worthless for CI: this laptop ran the suite in 56:12
against a runner's 49:33, and two leaked Chromes once took the same suite from
12:46 to 44 minutes. Runner variance alone is about 40% — the same tree ran 50
and 70 minutes on one day — so **any improvement smaller than about 2x is
unfalsifiable from a single run.**

Three documents have now attributed this job's cost to three different things
from three partial measurements. The next one should start from
`--durations=25`.
