# Streamlining the browser job

**A plan, 2026-08-22.** Combines backlog item 24 — the browser job doubled in
duration and nobody bought the time back — with a lever that item did not
consider: the suite spends nearly all of its time *starting* browsers, and it
starts eighty-four of them.

## What the job actually spends its time on

Measured on this machine under CI's exact flags (`--headless=new`,
SwiftShader):

| Phase | Cost |
|---|---|
| Chrome launch and connect | **6.99 s** |
| First `load_structure` | **5.41 s** |
| Second load, same browser | **0.48 s** |
| Third load, same browser | **0.52 s** |
| First `screenshot` | 6.17 s |

**A structure reloaded into a live browser costs about a tenth of a fresh
session.** That single ratio is the whole opportunity.

And the suite opens a lot of sessions. Counted from the syntax tree rather than
by grep, so a helper that opens one is not double-counted:

| File | Tests with their own session | Session fixtures |
|---|---|---|
| `test_render_differential.py` | 55 | 13 |
| `test_volumes_differential.py` | 8 | 0 |
| `test_molstar_backend_differential.py` | 4 | 0 |
| `test_altloc_differential.py` | 1 | 1 |
| `test_symmetry_differential.py` | 0 | 1 |
| `test_viewer_chrome_differential.py` | 0 | 1 |
| **Total** | **68** | **16** |

Eighty-four launches. At the 13–33 s backlog 24 measured on CI runners, that is
**18 to 46 minutes of setup** inside a job that takes 31 to 50. Setup is not
part of the cost; setup is the cost.

## Three levers, in the order they should be pulled

### 1. Stop re-testing a tree that was just verified

Backlog 24 found it and nothing was done: **41 of 100 runs were post-merge
pushes to `main`**, re-running the browser job on the result of a PR that had
just passed. About a quarter of all spend.

It is not quite free to remove, and the reason is worth stating: a merge commit
is only the tree the PR tested when `main` has not moved underneath it. Two PRs
that pass separately can break together, and the push-to-`main` run is what
catches that.

So the proposal is not to drop the coverage but to move it: keep the fast job
on every push to `main`, and run the browser job there **on a schedule**
instead — nightly, plus on demand. A semantic conflict between two merges is
found within a day rather than within an hour, and no PR waits for it.

### 2. Skip the browser job when only prose changed

Three docs-only pull requests landed on 2026-08-22 and each paid the full
browser job. A path filter on that job alone would have saved about two and a
half hours of runner for no loss whatever.

It must be a filter on **that job**, not on the workflow: the other two jobs
should still run, because a docs change can still break a link check or a
formatting hook. Either a second workflow file with its own `paths`, or a
`changes` job the browser job depends on.

**Do not path-filter it on source paths.** The browser job drives protean's
*Python* tools against a real viewer — `test_render_differential.py` imports
`fetch_structure_data` and `load_structure` and calls both — so "Python-only"
changes are exactly what it tests. The filter is safe for markdown and nothing
else.

### 3. Reuse the browser, reload the structure

The feedback-loop win, and the one with real design work in it.

One session per *file* rather than per test, with a reset between tests. At the
measured rates that turns roughly 84 × 12.4 s of setup into 6 × 12.4 s plus 84
× 0.5 s — **about two minutes where there are now seventeen**, before CI's
slower runners are taken into account.

**What stands in the way is that `clear` is not a reset.** It drops components,
volumes and registered fields and calls `plugin.clear()`. It leaves every
canvas property exactly where the last test put it: background colour,
lighting rig, cel step count, and every screen-space effect — outline,
occlusion, shadow, depth of field, bloom, sharpening. A shared session would
carry all of that from one test into the next.

That is not a hypothetical. This project already has the scar: a test that drew
a view left `auto_view` in the handle table while the next test's fresh viewer
knew only `auto`, and it failed in whichever test happened to run after one
that drew — the shape of thing that gets called flaky. Relaunching the browser
is the sledgehammer that makes that impossible, and it is why the suite is
written this way.

**So the reset has to be provably complete, and it can be.** A fresh viewer's
`canvas3d.props` is the specification: reset one viewer, launch another, and
diff the two property trees. Anything the reset forgot shows up as a difference,
mechanically, with no judgement about which properties matter. The same
comparison over the state tree covers the components. A reset that passes that
test is a reset nobody has to reason about.

Two smaller savings ride along:

- **Warm the image pass once per session.** The first `screenshot` costs 6.17 s
  against 2–3 s for the rest, because Mol\* builds the `ImagePass` lazily —
  already known, since a capture through a fresh pass is also 2.1% different
  from every one after it. One warm-up per session, not per test.
- **Load the structure once where tests share one.** Most of
  `test_render_differential.py` uses 1UBQ.

**Some tests must keep a virgin browser**, and they should be marked rather
than migrated: the one asserting the first capture of a session differs from
later ones, and anything about a hidden tab or the load path itself. The point
of consolidation is to stop paying for isolation nobody asked for, not to take
it away from the tests whose subject it is.

## What this plan deliberately does not do

- **Not sharding.** `ci.yml` explains at length why the job runs the whole
  suite rather than a list of files: "a list means files never meet, so nothing
  *between* them is tested", and backlog item 12 lived on `main` inside exactly
  that gap. Sharding is that gap by construction.
- **Not a smaller window.** Backlog 24 measured it: the aggressive setting
  removes 0.08 MP per render — a fraction of a second — while moving every
  pixel-fraction threshold in the suite, several of which are calibrated per
  renderer and close to their limits.
- **Not cutting the journal-figure captures.** They are the bulk of what the
  declutter merge added, and dropping them is a decision about coverage
  wearing an efficiency costume.

## Order, and what each step risks

1. **Levers 1 and 2** are workflow configuration, touch no test, and can land
   together. Risk: a semantic conflict between two merges is found the next
   morning instead of within the hour.
2. **The `reset` action and its completeness test**, landed on its own, with
   nothing yet depending on it. Risk: none — it is new surface nothing calls.
3. **Migrate one file at a time**, largest first, watching for the flake the
   reset exists to prevent. `test_render_differential.py` is 68 of the 84
   launches, so it is both the prize and the risk.

The prize is the feedback loop: **31–50 minutes down to something near ten**,
without giving up a single assertion.
