# Contributing

This file says the things this repo actually enforces and a newcomer cannot
guess. It is short on etiquette and long on the two or three habits that keep
this codebase honest.

## The one thing to read first

**protean's dominant failure mode is code that reports success while drawing
nothing, or drawing the wrong thing.** Mol\*, and this stack generally, accepts
bad input without complaint. `show(representation="cartoonn")` was accepted and
rendered nothing. A colour theme with an empty colour list paints everything
solid black while reporting correct statistics. The viewer and the analysis
were different molecules for months, and every count agreed.

So: **verify the rendered output, not the return value.** Read the pixels, the
label text, the image — not the reply that says it worked. And when a check
cannot run, say so in the reply rather than defaulting to "passed".

## Setup

```bash
uv sync                          # Python side
uv run pre-commit install        # per clone; --no-verify skips it, so not a gate
npm install --prefix viewer      # viewer side
npm run build --prefix viewer    # required before any browser test
```

`src/protean_mcp/static/` is the built viewer and is gitignored. A browser test
against a stale build tests the old viewer and passes.

## Running the tests

```bash
uv run pytest tests/ -q                       # fast: no browser, no network
PROTEAN_DIFFERENTIAL=1 uv run pytest tests/   # the real one, ~9 min
```

Everything expensive is opt-in behind an environment variable:

| Gate | What it turns on |
|---|---|
| `PROTEAN_DIFFERENTIAL=1` | the real-browser suite **and** network fetches from RCSB |
| `PROTEAN_PATHTRACE=1` | path tracing, which needs a real GPU |
| `PROTEAN_DSSP=1` | scoring against `mkdssp` |
| `PROTEAN_APBS=1` | scoring against APBS |
| `PROTEAN_PYMOL=1` | reading colours out of a running PyMOL |
| `PROTEAN_MSA_LIVE=1` | live MMseqs2 alignments |

**Run the browser suite headless unless you want windows opening.** The
`PROTEAN_CHROME_FLAGS` variable is empty by default because a real window is
useful when debugging one test; it is not what you want for a full run:

```bash
export PROTEAN_CHROME_FLAGS="--headless=new --no-sandbox --disable-dev-shm-usage \
  --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader"
```

That is exactly what CI uses. `--headless=new` on its own keeps the real GPU
and is about 4x faster, but the suite is **not green** under it: pixel
thresholds in the render tests are calibrated per renderer.

## CI

Three jobs on every PR: `lint, types, tests` (ruff, ruff format, `mypy
--strict`, pytest), `viewer types and unit tests` (`tsc --noEmit`, vitest,
build), and `selection differential (real browser)` against headless Chrome.
The browser job **needs network** — it fetches structures from RCSB — and it
is the job that catches silent-wrong-answer bugs, which is why it earns its
minutes.

**The browser job runs `uv run pytest tests/ -q` over the whole suite, not a
named file list. Do not optimise that back.** The comment at `ci.yml:88-105`
records why, and it cost real bugs to learn: a named list silently excluded
twenty new tests behind three green ticks, and it meant files never ran
together, which is exactly where backlog item 12 lived. The whole-suite run
costs about 1.3% more.

## Habits that are enforced by review, not by tooling

**A new test must be shown to have *run*, not merely to have passed.** `pytest
-q` prints no file names, so grepping a job log for your test file proves
nothing. Difference the job's `N passed` against the same job on `main`: PR 69
went 894 → 917, and 23 was exactly the number of tests it added. A green tick
is not evidence your tests executed.

**Mutation-test every guard, and check the mutation is not a no-op.** Break the
thing you just fixed and confirm the test fails. Then confirm the *mutation*
did what you think: a `str.replace` that hits a docstring occurrence, a cutoff
changed to a value no assertion depends on, and a `sed` that produced invalid
Python have all counted as false evidence here. Mutate the call site too, not
only the helper — a test asserting five tools take a flag passed with the
guard deleted from all five bodies.

**`ruff format src tests`, never `ruff format .`** — the latter reaches into
`docs/` and reformats Python inside markdown fences.

**One PR per coherent change**, on a branch off `main`, merged with a merge
commit rather than squashed, and **CI observed green before anything is called
done**. Watch the run and report its actual conclusion.

**Check the CI run's `headSha` against the PR's head before trusting a green.**
Every merge moves `main`, and a `pull_request` run tests the PR merged into the
base *as it was when the run started*. Three PRs in one afternoon carried
greens that had gone stale underneath them, and the tick looked identical.

## Where the reasoning lives

The repo records its own history, and the corrections are the useful half:

- [PLAN.md](PLAN.md) — phases and the decisions behind them
- [CHANGELOG.md](CHANGELOG.md) — what shipped
- [docs/backlog.md](docs/backlog.md) — what is broken, and what was fixed
- `docs/*.md` — a plan document per substantial piece of work, **corrected in
  place afterwards**. Every one of them was wrong in ways only measurement
  found; `docs/cryoem.md` is the sharpest case, since it prescribed reading the
  exact field it was warning against.

If you fix something these documents describe wrongly, fix the document in the
same PR. A plan's citations rot, and the ones that rot worst are the ones
aiming later work.

## Security

See [SECURITY.md](SECURITY.md) for the trust model and how to report a
vulnerability. Do not open a public issue for anything exploitable.
