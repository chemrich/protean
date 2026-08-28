# Timing one Mol\* capture, on every release

This measures the cost of a single Mol\* image-pass capture, on whatever Mol\*
build is put next to it. It exists for one question, [`docs/backlog.md` item
40](../../docs/backlog.md): **which release between 4.18.0 and 5.11.0 doubled
protean's browser CI job?**

```
a92f86a  pre-upgrade              1140 passed, 28 skipped   23m12s
ae1df78  "Move to Mol* 5.11 ..."  1139 passed, 28 skipped   48m18s
```

One *fewer* test, 2.08x the time, same skips, same session count. Chrome is
ruled out (both runs report 151.0.7922.169) and so is a changed `sampleLevel`
default (`PD.Numeric(2)` in both, and the screenshot helper's `? 4 : 2` line is
byte-identical). So the per-sample cost itself grew, somewhere in nineteen
releases.

## Why this is not the browser job

Runner variance on that job is about 40% — the same tree ran 50 minutes as a PR
and 70 as a push on one day — so **anything smaller than about 2x is
unfalsifiable from a single run**. Bisecting five 60-minute runs of a noisy
quantity is both slow and unsound.

So: measure the one thing that got expensive, with repeats, back to back on one
runner. A version costs about a minute instead of an hour, and nineteen of them
fit in a single job where the ratios between them mean something.

## Why it is not protean's viewer either

`viewer/src` imports Mol\* 5 internals — `mol-canvas3d/passes/illumination`,
the trackball `axis`, bloom's `transparency` — so it would not build against
4.18 at all. **A benchmark that cannot run on the pre-upgrade version measures
nothing.** `bench.html` therefore loads the prebuilt UMD bundle that every
release ships at `build/viewer/molstar.js`, and drives it through
`plugin.helpers.viewportScreenshot`, whose `createPass` and `imagePass` getter
are byte-identical between 4.18.0 and 5.11.0. The same page runs unchanged on
both ends of the range; that was checked before anything was measured.

## What it times, and what it deliberately does not

`ImagePass.getImageData` — the render plus the `readPixels` that forces it to
have finished. Not `render` alone: that returns once the GL commands are issued,
so timing it would measure command submission and could report a real regression
as absent.

The runtime handed to the pass is a stub. `ImagePass.render` touches it only
inside the illumination branch, which is off in both versions, so nothing is
skipped — and it keeps Mol\*'s task scheduler, which yields to `requestAnimation
Frame`, out of the number. `helper.getImageDataUri()` — the whole path `snapshot`
actually calls, PNG encode and all — is timed separately for a couple of
repeats, as the check that this benchmark tracks what the job spends its time
on.

**Two sample levels, on purpose.** A regression in the cost of each *sample*
holds the same ratio at level 4 and level 1. A regression in fixed per-capture
work — a pass rebuilt, a program relinked once per capture — is a roughly
constant number of milliseconds, so its ratio falls as the level rises. The two
want different fixes, and one table tells them apart.

**Everything that could explain a difference without being a regression** is
reported beside the timings: the GL renderer string, `colorBufferFloat` and
`textureFloat` (the two capability checks the helper's `? 4 : 2` reads, so a
renderer that gained float support would silently double the sample count and
look exactly like a per-sample regression), the draw and instance counts, the
draw pass's transparency mode, and the entire `canvas3d.props` tree. That last
one is the direct test of item 40's second candidate — *a default that changed
on our side* — and it costs one `JSON.stringify`.

## Running it

On CI, which is where the numbers count:

```
gh workflow run molstar-capture-bench.yml -f versions=4.18.0,5.11.0 -f levels=4,1
```

Until the workflow file reaches `main`, `workflow_dispatch` is not available —
GitHub only offers it for workflows on the default branch. Pushing a branch
under `molstar-bench/**` runs it instead, with the settings in `run.conf`, so
each measurement is a commit that records what it measured.

Locally, for checking the harness rather than for numbers:

```
npm pack molstar@5.11.0 && tar xzf molstar-5.11.0.tgz
python3 run_bench.py --molstar-dir package/build/viewer --label 5.11.0
```

**A local number is not a CI number.** Measured 2026-08-22, a browser session
costs 5–10 s on `ubuntu-latest` against 6.3–27 s here, so CI is the *faster* of
the two per session — the opposite of what everyone assumed, and a local ratio
applied to CI has been wrong in this repo twice, in both directions. The
harness defaults to the browser job's own SwiftShader flags so that a local run
at least exercises the same renderer, but the table that decides anything is the
one CI prints.

## The pieces

| file | what |
|---|---|
| `bench.html` | the page: raf-pump, `molstar.js`, `bench.js`, in that order |
| `bench.js` | boots the viewer, loads the structure, times the captures, POSTs the result |
| `run_bench.py` | serves the page, launches Chrome, waits for the POST, writes JSON. Stdlib only |
| `summarise.py` | a directory of results becomes one markdown table |
| `raf-pump.js` | copied from `viewer/public`; Mol\* needs a live rAF to build a representation |
| `1ubq.pdb` | the subject, committed so no run touches the network |
| `run.conf` | what the next push measures |

`1UBQ` because it is what item 40's own sample-level table was measured on, so
the numbers here can be read against it.

## The result reports itself

The page POSTs its result back to the server that served it. There is no CDP
endpoint and no WebSocket in the harness — which removes the failure that cost
this project a day in PR 89, a reply lost on a socket that closed mid-capture,
because there is no long-lived socket for a capture to outlive. A failure comes
back the same way a success does, so "this version broke" is never confused with
"the page never finished".
