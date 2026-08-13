# Item 12 — session state that nothing resets

Planned 2026-08-13, not started. Found while running the full suite locally
during item 7; recorded as [backlog](backlog.md) item 12.

**The test failure is the symptom, not the item.** Two refusal tests fail on
`main` when run in the wrong company. Chasing only that would fix the tests
and leave the reason they were able to fail.

---

> ## Done, 2026-08-13 — and §3.1's check was itself a no-op
>
> Implemented in PR 55, CI change in PR 56. Backlog item 12 is closed. Kept as
> the plan it was, with the one thing it got wrong marked.
>
> **§3.1 says to mutation-test the fixture by disabling it and confirming the
> two original tests fail again. That mutation passes.** With the fixture
> disabled the full gated suite is still green, because §2.1 *also* clears the
> pollution — `test_server.py` fetches a structure long before it reaches
> those two refusals. Two independent fixes mask one symptom, so no
> full-suite run can say whether the fixture does anything at all.
>
> This is the same trap §3 was written to avoid, one level up: the plan
> correctly insisted the fixture be mutation-tested, then specified a mutation
> that could not fail. `tests/test_session_isolation.py` is the replacement —
> one test leaks on purpose, the next asserts the leak was cleaned, and that
> pair *does* fail when the fixture is disabled. **When two changes fix one
> symptom, a test over the symptom cannot attribute the fix to either.**
>
> **§4's cost estimate was too pessimistic, in the useful direction.** It
> priced the full suite in the browser job at ~1.3%. Measured in CI: 765 s for
> `pytest tests/ -q` against 766 s for the old five-file list — no measurable
> cost at all, because the fast tests are trivial beside the browser ones.
>
> The rest held. Keeping §2.1 and §2.2 as separate commits is what made the
> no-op visible; bundled, a green suite would have taken credit for a fixture
> that might have been doing nothing.

---

## 1. What is actually wrong

`server.py` keeps the session in module globals:

| global | set by | cleared by |
|---|---|---|
| `_structure`, `_structure_error`, `_structure_identifier` | `fetch_structure`, `load_trajectory`, `superpose` | nothing |
| `_handles` | `select`, analysis tools | `fetch_structure`, `load_trajectory` |
| `_conservation_scores` | `conservation` | `fetch_structure` |
| `_trajectory` | `load_trajectory` | **nothing** |
| `_keyframes` | `keyframe` | **nothing** |
| `_path_tracing` | `path_trace` | itself |
| `_bridge` | `open_viewer` | nothing (deliberate singleton) |

Two of those blanks are the item.

### 1.1 The test symptom

```
FAILED tests/test_server.py::test_rmsf_needs_a_trajectory_first
FAILED tests/test_server.py::test_rmsd_series_needs_a_trajectory_first
```

Both assert `ViewerError("No trajectory loaded")`. Both pass alone.
`tests/test_render_differential.py` calls the real `load_trajectory` tool,
sorts before `test_server.py`, and runs only under `PROTEAN_DIFFERENTIAL=1`.
It leaves `_trajectory` set, and the two refusals stop refusing.

Tests that set globals with `monkeypatch.setattr` are not the problem — that
reverts. The polluters are tests calling the real tools, which assign through
`global` and never undo it.

**CI cannot see this**, which is why it survived. The fast job runs `pytest -q`
with no gate, so the polluting file skips. The browser job names four files
explicitly and `test_server.py` is not among them. The two only meet in a local
full run: `PROTEAN_DIFFERENTIAL=1 uv run pytest tests/ -q`, about 6.5 minutes.
Confirmed identical on `main` at `efc42e0` — 2 failed, 766 passed.

### 1.2 The product bug underneath it

`fetch_structure` clears `_handles` and `_conservation_scores`. It does not
clear `_trajectory`. So:

```
load_trajectory("run.xtc")     # _trajectory = frames of molecule Y
fetch_structure("1ubq")        # viewer now shows X; _trajectory still Y
rmsf()                         # answers about Y, in numbers, with no warning
```

`rmsf` reads `stack[0]` rather than `_structure`, so it does not mismatch and
does not complain — it describes a molecule that is no longer loaded while the
viewer shows a different one. That is the failure decision 9 exists to prevent,
reappearing through a different door.

`load_trajectory` guards the pairing at load time by refusing an atom-count
mismatch. Nothing re-checks it afterwards, and `fetch_structure` is exactly the
call that invalidates it.

`_keyframes` has the same shape and lower stakes: camera positions saved
against one structure survive into the next, so `record_timeline` interpolates
between views framed for a molecule that is gone.

## 2. What to do

Two changes, deliberately separate — one is a behaviour fix, the other is a
test-harness fix, and merging them would let a green suite claim credit for
the first.

### 2.1 Loading a structure ends the session that preceded it

`fetch_structure` already treats a new structure as a new session for handles
and conservation. Extend that to `_trajectory` and `_keyframes`, in the same
place, for the same reason.

Say so in the reply rather than doing it silently — a caller who had a
trajectory loaded should be told it is gone, not discover it from a refusal
later:

```
Loaded 1ubq ... [the trajectory of 4ake was discarded: a trajectory belongs
to the structure it was loaded onto]
```

`superpose` also reassigns `_structure` (line ~2043) and needs the same
treatment or an explicit argument for why not.

**The alternative, rejected:** keep the trajectory and have `rmsf` refuse when
it no longer matches `_structure`. That preserves work the caller might want,
but it means carrying a known-inconsistent state and remembering to re-check
it at every use — the same "everything is symmetric across copies" invariant
that item 7 showed does not survive contact.

### 2.2 Tests restore session state between cases

An autouse fixture in `tests/conftest.py` that snapshots the session globals
before each test and restores them after. Scoped to function.

Care needed in three places, each of which would make the fixture a no-op:

- **`_handles` and the dicts are mutable.** Restoring the same object restores
  nothing. Snapshot the contents (`dict(...)`, and the registry's mapping),
  or replace with a fresh `HandleRegistry`.
- **`_bridge` must not be reset to `None`.** `test_server.py` installs it via
  module-scoped fixtures; a function-scoped reset would tear that down
  mid-module. Leave it out and say why in a comment.
- **Async tools mutate globals after the fixture's snapshot** if anything is
  still in flight. Everything here is awaited, but a future background task
  would break the assumption silently.

## 3. Verification

**The two failing tests passing again is not sufficient evidence**, because
reordering, deleting the polluter, or the fixture accidentally doing nothing
all produce that result.

1. **Mutation-test the fixture.** Make it restore nothing and confirm the two
   tests fail again in a full local run. A fixture that cannot fail is worse
   than none, and this repo has already been caught by the mirror image of it:
   a `vi.spyOn(document, 'visibilityState')` that was never restored leaked
   into later vitest cases and made three new tests pass against the very bug
   they were written for (commit `dfa7faa`). Leaked state can manufacture a
   pass as easily as a failure.
2. **Test §2.1 directly, without the fixture in play.** Load a trajectory,
   fetch a different structure, assert `rmsf()` refuses. This is the product
   claim and must hold on its own.
3. **Assert the reply says the trajectory was discarded**, not merely that the
   global is `None`. The silent version of this fix is a smaller version of
   the same bug.
4. **Run the full local suite**, which is the only place the original symptom
   is visible at all.

## 4. Making CI able to see this at all

**CI never runs the whole suite in one process**, which is why a cross-file
interaction can live on `main` indefinitely. The fast job runs without
`PROTEAN_DIFFERENTIAL`, so the polluting file skips; the browser job runs a
named list that does not include `test_server.py`. They meet only in a local
full run.

**Priced, rather than guessed at** — from the PR 52 merge run:

| job | pytest time |
|---|---|
| fast, `pytest -q` | 8.3 s (518 tests) |
| browser, 5 named files | 690 s (242 tests) |

So folding the fast tests into the browser job adds **~9 s to an 11.5-minute
job, about 1.3%**. An earlier draft of this section warned that it would cost
browser-job minutes and cited the browser job being 76.5% of CI spend. That was
wrong and the number says so: the 76.5% is the browser *tests* being slow, and
adding fast tests does not move it.

**Recommendation: have the browser job run `pytest tests/ -q` with the gate
set.** It is the run that found this, it covers every future pair rather than
the one pair we happen to know about, and it costs about a percent.

Two real consequences, neither large:

- The fast tests run twice per PR, once in each job. Redundant, and a fast-test
  failure surfacing in the browser job is marginally more confusing to place.
- **It cannot land before §2.1 and §2.2.** That full run fails on `main` today,
  so switching CI first turns it red immediately. Sequenced after the fix, it
  doubles as the proof the fix worked and the guard against its regression.

The narrower alternative — adding `tests/test_server.py` to the existing list —
is cheaper still and closes only the known pair. It is the right choice only if
the duplicate-run objection outweighs covering the class.

## 5. Not in scope

- Making the session an object rather than module globals. It would prevent
  this class outright, and it touches every tool in a 2,700-line file. Worth
  proposing separately, on its own merits, not as the tail of a bug fix.
