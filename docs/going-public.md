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
| Secrets in source, viewer, or CI | none found — see the scope note below |
| Absolute local paths committed | none on `main`; **one on `cryoem-volumes`** |
| History size | 6.3 MB; largest tracked file is `uv.lock` |
| Bridge bind address | `127.0.0.1` (`connection.py`), not `0.0.0.0` |
| `protean-mcp` on PyPI | **available** |
| `protean` on PyPI | taken, by an unrelated project |
| `CONTRIBUTING.md`, `SECURITY.md` | absent |

Four of those deserve a sentence.

**"No secrets" is narrower than it looks, and this is the claim that cannot be
withdrawn.** Precisely what was checked: `ci.yml` references no `secrets.*`, and
a *pattern* scan across all 187 commits on all refs — AWS keys, GitHub PATs,
`sk-` keys, `BEGIN PRIVATE KEY` headers, Slack tokens — returned nothing. What
has **not** run is an entropy-based tool, which is what catches a credential
that matches no known prefix. An unqualified "none" resting on a prefix grep is
the exact silent-success shape §3.1 warns about, so the row says "none found"
rather than "none". **Fold a real scan into §3.1** before treating it as
settled; `gitleaks` is not currently installed on the dev machine, so that is an
install step and not a one-liner.

**One absolute path is in flight, and `main` being clean will not stop it.** The
`cryoem-volumes` branch commits `viewer/node_modules` as a mode-120000 symlink
holding an absolute path from one machine, and `.gitignore:12` is
`viewer/node_modules/` — the trailing slash matches a directory, not a symlink,
which is why it slipped through and why the gap is still open on `main`. The
row above is true today and stops being true on the first merge of that branch.
**Re-run the check immediately before the flip, not only now**, and strip the
symlink when that branch lands.

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

> **Run 2026-08-15. It found one blocker, now fixed; the file-serving paths
> themselves came out clean.** What follows is the plan; the results are
> recorded inline under each part.

#### Result: an unauthenticated viewer takeover (fixed)

**The worst finding was not on the paths this section names.** The bridge's
WebSocket accepted any connection: `_ws_handler` called `ws.prepare()` with no
`Origin` check and no token.

A WebSocket is not subject to the same-origin policy, so any page the user
happens to be visiting can open `ws://127.0.0.1:<port>/ws`, and the port is
`DEFAULT_PORT` (9878) plus a small scan range — guessable in a few tries. Worse,
the handshake is *designed* to displace: a `protean_ping` from any connection
closes the incumbent and takes the socket.

Demonstrated before the fix, with a socket carrying
`Origin: https://evil.example`: it was accepted, and the real viewer received
`protean_superseded` and a close. Every action would then go to that page, and
every number the model read would come from it.

The impact lands precisely on this project's thesis. protean exists to
guarantee the viewer and the analysis describe the same molecule; a spoofed
viewer returning fabricated counts defeats that entirely, while every call
returns cleanly.

**Fixed**: a per-bridge token, minted with `secrets.token_urlsafe(32)`, required
on the handshake and compared with `compare_digest`; plus an `Origin` check that
refuses a present-but-foreign origin. Both run *before* `prepare()`, so a
refused caller never reaches the message loop and cannot land a `protean_ping`
on the way past. `ViewerBridge.viewer_url` is now the single place the URL is
built, so a viewer cannot be opened that its own socket would refuse.

**This is the argument for doing the pass before the flip, not after.** The
vulnerability predates it, but the repo was private; publishing ships the port,
the handshake string and the message schema, turning "someone would have to
reverse-engineer this" into "the README explains it".

#### Result: the file-serving paths held

The static route was attacked with 15 traversal attempts — `..`, `%2e%2e`,
double-encoded, backslashes, absolute paths, `..;/`, and **symlinks planted
inside `static_dir`** pointing out. All returned 404; nothing escaped. The guard
below is as strict as it claims.

Still open from this section: the nine path-taking tools have been enumerated
but not individually attacked, and the entropy-based secret scan has not run.

---

The original plan for this section follows.

The server reads local files named by a model and serves bytes over HTTP. That
is the design, and on loopback it is defensible. The question for the pass is
whether a path *outside* the intended roots can be published.

**Start by reading the guard that is already there, rather than looking for a
missing one.** The file route is not aiohttp's `add_static` — `connection.py:64`
registers a hand-rolled `_file_handler` on `/{filename:.+}`, and it asserts
containment at `connection.py:173-176`:

```python
root = self.static_dir.resolve()
target = (root / request.match_info["filename"]).resolve()
if not target.is_relative_to(root) or not target.is_file():
    raise web.HTTPNotFound()
```

Because `.resolve()` follows symlinks *before* the containment test, a symlink
planted inside `static_dir` that points outside it is already rejected — this is
stricter than `add_static`'s default, not looser. `..` and absolute paths are
handled by the same two lines. So the pass should be an attempt to defeat that
specific guard, and the live questions are narrower than "is there a check":

- the window between `is_file()` and `FileResponse` — a TOCTOU swap
- whether `static_dir` itself can ever be attacker-influenced, since every
  guarantee above is relative to it
- what a `%2e%2e` or non-UTF-8 request path does to `match_info` before
  `Path` ever sees it

The second surface is **every tool taking a `path` or `directory` argument**,
and those have no `_file_handler` between them and the filesystem. This list is
the scope of the pass, so it is given in full from `server.py` — a partial list
silently narrows the review, which is how the first draft of this document
omitted the riskiest entry:

| tool | `server.py` | takes |
|---|---|---|
| `snapshot` | 838 | `path` |
| `load_trajectory` | 937 | `path` |
| `record_trajectory` | 1243 | `directory` |
| `movie` | 1377 | `directory` **and** `path` |
| `save_session` | 1949 | `path` |
| `load_session` | 1978 | `path` |
| `electrostatics` | 2296 | `path` |
| `screenshot` | 2872 | `path` |

Plus `load_volume` once the volume work lands. **Read `load_session` first**: it
reads an arbitrary local path, gzip-decompresses it and parses the result, which
is the read-and-deserialize shape this kind of pass exists to catch. Note also
that several of these *write* rather than read, so the question for them is what
can be overwritten, not what can be disclosed.

The third piece is **an entropy-based secret scan over history**. §2's pattern
sweep only catches credentials with a recognisable prefix; a bare 40-character
token matches nothing it looked for. Run `gitleaks detect` or equivalent across
all refs and record the output. History is the part the flip publishes that no
later commit can retract.

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
  cross-file pollution — and that the browser job runs **exactly that command**
  (`ci.yml:106`), over the whole suite rather than a named file list. It was a
  named list once, and backlog item 12 lived precisely in the gap that left;
  the comment at `ci.yml:88-105` records why the list went away and what the
  1.3% it costs is buying. Do not "optimise" it back.
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
- Nothing in the history reads badly in public. It should contain no secrets —
  on the strength of §3.1's history scan, not the working-tree check in §2;
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

Related: [wiggles-em integration](wiggles-em-integration.md) §5.3 notes that
wiggles-em is public and protean is not, and that anything upstreamed there
becomes public ahead of protean itself. Once this plan lands, that ordering
constraint disappears.

## 6. What would make this plan wrong

- **If §3.1 finds a path that escapes its root**, this stops being preparation
  and becomes a fix, and the flip waits for it. *(It found something adjacent —
  an unauthenticated WebSocket takeover — and that is exactly what happened:
  the flip waited and the fix landed first.)*
- **If the history scan finds a secret**, the whole premise inverts. That is not
  a fix but a history rewrite plus a credential rotation, and it is the one
  outcome that has to complete before the flip rather than alongside it.
- **If a dependency turns out to be copyleft**, §3.3 becomes a licensing
  decision about protean's own terms rather than a checkbox.
- **If the free-Actions assumption in §2.1 is wrong**, the cost argument
  inverts: the browser job would then be a bill paid in public, and
  path-filtering it becomes urgent rather than optional.
