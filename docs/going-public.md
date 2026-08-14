# Taking protean public — what is already true, and what has to happen first

Planned 2026-08-14, not started. protean is a private repo under `chemrich` and
the stated intent is to open it. This is the order to do that in, and the
audit that says nothing blocks it today.

Audited against `main` at `033aa85`.

**There is no blocker.** Everything below is preparation, not repair. The one
item that genuinely has to come before the flip is the security pass in §3.1,
and only because the flip cannot be undone.

---

## 1. Why the ordering matters at all

**Going public is effectively irreversible.** Flipping back to private does not
recall forks, clones, or anything a crawler has already indexed. Every check
worth doing is therefore worth doing *before*, and anything discovered
afterwards is a disclosure rather than a fix.

That is the whole reason this is a document and not a button.

## 2. What the audit found

Checked directly against the tree rather than recalled.

| | |
|---|---|
| `LICENSE` | MIT, © 2026 Charlie Emrich |
| Secrets in source, viewer, or CI | none — `ci.yml` references no `secrets.*` at all |
| Absolute local paths committed | none on `main` |
| History size | 6.3 MB; largest tracked file is `uv.lock` |
| Bridge bind address | `127.0.0.1` (`connection.py`), not `0.0.0.0` |
| `protean-mcp` on PyPI | **available** |
| `protean` on PyPI | taken, by an unrelated project |
| `CONTRIBUTING.md`, `SECURITY.md` | absent |

Two of those deserve a sentence.

**The bridge binds loopback.** The obvious finding for a tool that runs a local
HTTP server — "it is listening on every interface" — does not apply. That is
worth knowing before §3.1 rather than discovering during it.

**The package name and the repo name will differ.** `protean` is taken on PyPI,
`protean-mcp` is free and is already what `pyproject.toml` declares. Nothing to
rename; it just means someone searching PyPI for "protean" finds something else.

### 2.1 An upside worth counting

Public repositories get GitHub-hosted Actions minutes free. The browser job is
**76.5% of current CI spend** (measured 2026-08-12, 842 billable minutes over
four days), so opening the repo is a cost *reduction*, not only an exposure.

**Not verified.** This is how GitHub's billing is documented to work for
standard runners on public repos, not something measured here. Confirm against
the account's own billing page before treating the saving as real — the same
rule this repo applies to everything else.

## 3. Before the flip

### 3.1 A security pass on the file-serving paths — do this first

The server reads local files named by a model and serves bytes over HTTP. That
is the design, and on loopback it is defensible. What has not been asserted is
that a path *outside* the intended roots cannot be published.

Worth walking deliberately:

- the static route in `connection.py`, and what it will and will not follow —
  note aiohttp's static handler does not follow symlinks, which is a
  constraint that has already bitten a test harness here
- every tool taking a `path` argument — `load_trajectory`, `electrostatics`,
  `save_session`, `snapshot`, and `load_volume` once the volume work lands
- what happens to a path containing `..`, an absolute path, or a symlink out

The repo has a `/security-review` skill; this is what it is for. The output
should be a statement of what *is* reachable, not a clean bill — "no findings"
from a scan nobody scoped is the silent-success shape this codebase keeps
meeting.

### 3.2 `SECURITY.md` and `CONTRIBUTING.md`

Neither exists. For a tool that launches a browser and shells out to APBS and
pdb2pqr, a stated disclosure path matters more than for an ordinary library.
`CONTRIBUTING.md` should say the things this repo actually enforces and a
newcomer cannot guess:

- the three CI jobs, and that the browser one needs network
- `PROTEAN_DIFFERENTIAL=1 uv run pytest tests/` is the run that catches
  cross-file pollution, and CI cannot reproduce it
- `ruff format src tests`, never `ruff format .`, because it reaches into
  `docs/` and reformats Python inside markdown fences
- that a new browser test must be shown to have *run*, not merely passed

### 3.3 A dependency licence check

biotite, pdb2pqr, Mol\*, aiohttp, httpx, pillow. All believed permissive; none
verified. **This is an unverified claim in a document about publishing, which
is exactly the kind this repo does not get to make.** Cheap to settle with
`uv pip list` and a look at each project's licence, and it wants doing before
the repo carries an implicit claim that its dependencies are redistributable.

### 3.4 The README as a front door

167 lines, written for someone who already knows what protean is. The first
screen should answer what it is, what it needs, and what it looks like — a
rendered image would do more than a paragraph. Lowest-urgency item here, and
the one most visible on day one.

## 4. The flip

Once §3 is done: change the visibility, then confirm rather than assume.

- The three CI jobs still pass on a public runner.
- A PR from a fork behaves — first-time contributors need workflow approval,
  which is a setting to see rather than a surprise to discover.
- Nothing in the history reads badly in public. It will not contain secrets;
  it *will* contain `Co-Authored-By: Claude Opus 5` throughout, and the full
  decision record in `PLAN.md` plus the plan documents in `docs/`, corrected in
  place. That is a deliberate choice to publish the reasoning along with the
  code, and on balance an argument for the project rather than against it.

## 5. PyPI, as a separate decision

Being public does not require being published. `protean-mcp` is available and
the metadata is already complete enough to build, but publishing adds a
release discipline — a version to bump, a changelog entry that has to be true,
and an install path people will report bugs against.

If it happens, it should follow the repo being public rather than accompany it,
so the first release points at a repository someone can actually read.

Related: [wiggles-em integration](wiggles-em-integration.md) §6 notes that
wiggles-em is public and protean is not, and that anything upstreamed there
becomes public ahead of protean itself. Once this plan lands, that ordering
constraint disappears.

## 6. What would make this plan wrong

- **If §3.1 finds a path that escapes its root**, this stops being preparation
  and becomes a fix, and the flip waits for it.
- **If a dependency turns out to be copyleft**, §3.3 becomes a licensing
  decision about protean's own terms rather than a checkbox.
- **If the free-Actions assumption in §2.1 is wrong**, the cost argument
  inverts: the browser job would then be a bill paid in public, and
  path-filtering it becomes urgent rather than optional.
