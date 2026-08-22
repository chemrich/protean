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
