# Troubleshooting

Two kinds of thing go wrong, and they need different responses.

**Environment failures** mean protean cannot run. Fix your setup.

**Refusals** mean protean *can* run and has decided not to. These are not bugs.
protean would rather say no than hand back a confident picture of the wrong
thing — there are 158 places in `server.py` alone where it does. **A refusal
names what it wants**, so the message is usually the whole answer.

---

## Environment

### `uvx protean-mcp` fails to resolve

Expected. protean is not published to PyPI, and neither is its `wiggles-em`
dependency, which resolves through a `[tool.uv.sources]` git pin that wheel
metadata cannot carry. Install from a clone — see
[getting-started.md](getting-started.md#install).

### "the viewer app is not built"

```
Bridge is listening at http://127.0.0.1:PORT/, but the viewer app is not built.
```

You skipped the viewer build, or pulled a change that touched `viewer/` and did
not rebuild:

```bash
npm run build --prefix viewer
```

It lands in `src/protean_mcp/static/`, which is gitignored because it is a build
artifact. **A browser test against a stale build tests the old viewer and
passes**, so rebuild after any `dispatch.ts` change or after rebasing onto a
commit that touched it.

### "The viewer tab is hidden"

```
Viewer timed out on 'load_structure' after 60s. The viewer tab is hidden:
browsers pause requestAnimationFrame in background tabs, which Mol* needs to
build representations. Bring the protean tab to the front and retry.
```

**The most common failure, and it looks like a hang.** Bring the tab forward.
Nothing is broken.

### "No viewer connected — call open_viewer first."

The server is up but no browser is attached. Call `open_viewer()`. It is
idempotent: if a viewer is already connected it reports its address instead of
opening a second tab.

If the tab opened but never connected, the handshake failed. `open_viewer`
launches the browser at a URL carrying a token, and the page hands that token to
its WebSocket. If your default browser is not the one that opened, or the tab
was blocked, use `open_viewer(reveal_url=True)` to get the tokenised address and
open it yourself.

> `reveal_url=True` puts the handshake token in the reply, and that reply lands
> in a transcript and often a log. Whatever holds the URL can drive the viewer.
> It is off by default for that reason.

### `movie()` does nothing useful

ffmpeg is not installed. `capabilities()` reports `"ffmpeg": true|false` — check
it *before* paying for a long capture. The frames are still written; nothing
encodes them.

### `electrostatics()` says "coulombic" when you asked for APBS

APBS or pdb2pqr is not on the path. `method="auto"` falls back to a screened
Coulomb field and **always reports which one actually ran**, because the two are
not equivalent and a potential whose provenance is unstated is worth nothing.

### Path tracing never finishes

`path_trace()` needs a real GPU. Under software rendering the WebGL extensions
it requires are all present — so it reports as supported — and a single capture
then fails to finish. This is why CI gates it separately behind
`PROTEAN_PATHTRACE=1`.

---

## Preconditions

### "No structure loaded — call fetch_structure first."

Nothing to work on.

### "The loaded structure could not be parsed for analysis"

```
The loaded structure could not be parsed for analysis, so selections are
unavailable: <why>
```

**This is the confusing one.** Mol\* rendered your file, so there is a picture on
screen — but the Python half could not parse it, so every selection and every
analysis tool refuses. You have a viewer that works and an analysis half that
does not, which reads like protean is broken.

The load reply said so at the time, with `[analysis unavailable: ...]`. Convert
the file to standard mmCIF, or load a PDB entry instead.

### "Could not resolve '<id>'"

```
Could not resolve 'x': not an existing file, 4-character PDB ID, or UniProt
accession. Pass source='file'|'pdb'|'alphafold' to disambiguate.
```

`fetch_structure` tries, in order: an existing path, a PDB ID matching
`^[0-9][a-zA-Z0-9]{3}$`, then a UniProt accession. Name the source to skip the
guessing.

---

## Selections

### "Unknown selection keyword"

```
Unknown selection keyword: 'polymerr'. Supported keywords: all, alt, b,
backbone, chain, elem, ...
```

The full valid list comes with the message. See [selections.md](selections.md).

### "'last' is not supported"

Parsed, deliberately not evaluated, and it names the reason:

| Construct | Message |
|---|---|
| `last` | no last-element filter; `first` is available |
| `pepseq` | sequence-motif matching not yet implemented |
| `like`, `beyond`, `near_to` | not implemented |

### "matches no atoms"

Not an error in itself — a well-formed selection is allowed to match nothing.
It *becomes* an error when something is about to be drawn from it:

```
Nothing matched 'resn XYZ', so this view would draw nothing and report success.
The scene is untouched; pass a handle naming what to draw instead.
```

Note **the scene is untouched**. A refusal does not leave you with a blank
viewer.

### "'sym' names a copy of the asymmetric unit…"

You asked for `sym N` on a structure loaded as the asymmetric unit, which has
only one copy. Load with `assembly="biological"` or drop the `sym` term.

### "no atom in this structure has one"

You asked for `alt A` where nothing is modelled in two positions. `alt .` and
`alt ''` select everything in that case.

---

## The confidence guard

This one refuses things that look completely reasonable, so it is worth
understanding rather than working around.

**The B-factor column holds two different quantities that run in opposite
directions.** In a crystal structure it is a B-factor: high means the experiment
is *less* certain. In a predicted model it is pLDDT: high means the prediction
is *more* confident.

protean records which one was loaded, and refuses the wrong reading:

```
'uncertainty' reads the B-factor column as a confidence score, and this
structure is experimental: its column is a crystallographic B-factor.
```

It also refuses when a scene holds **both** — some experimental, some predicted
— because no single ramp is right for the mixture.

**It does not silently do something else.** Where a preset can sensibly
redirect, it does so *and says so*: `preset("putty")` on a predicted model
returns `preset: "plddt"` alongside `asked_for: "putty"`, and `show()` returns a
`size_theme_note` when it swaps the width channel.

---

## Would-draw-nothing refusals

A family with one shape: the call would have succeeded and produced a picture
indistinguishable from a failed search.

| Call | Refuses when |
|---|---|
| `crosslink_view()` | there is neither a disulfide nor a metal |
| `interface_view(a, b)` | the two chains do not touch — *"interface() reports the numbers either way"* |
| `ligand_view(resn)` | nothing lines the ligand within `around` Å |
| `pocket_view(resn)` | the pocket handle would be empty |
| `preset("active-site")` | no handle names the site |
| `preset("hide-sidechains")` | no sidechains are drawn to hide |
| `preset("scaffold")` | the structure is experimental, so there is nothing to cover |
| `near(radius=0)` | *"A non-positive radius matches nothing, which would look like an answer"* |
| `mutation_view("A123G")` | residue 123 is not an alanine |

`mutation_view` is the sharpest: it verifies the residue you named is the
residue that is there. An offset of one is the most common thing that goes wrong
with residue numbering and the least visible, and the picture looks confident
either way.

---

## Writing files

`snapshot`, `save_session`, `record_*` and `boil` all guard the path:

```
<path> already exists and is not png/tiff/jpeg, so writing here would clobber
something else entirely. Pass overwrite=True to do that on purpose, or choose
another path.
```

```
<path> is a directory, so there is no file to write there
```

`electrostatics(path=...)` is an **output**, which is easy to misread from the
name. Pointed at an existing file it used to overwrite it without a word — and
did, over a file named `secret.key` during the security pass. It now refuses
unless the file is an OpenDX grid or you pass `overwrite=True`.

---

## Captures

### "the capture came back incomplete"

```
The capture came back incomplete: parts of the NxM image were never rendered.
Renderers run out of room at large sizes, and software rendering does so well
before a real GPU does. Lower the dpi or the width, or capture on a machine
with a GPU.
```

Read off the pixels, not guessed. There is also a hard ceiling — a frame beyond
120 megapixels is refused before the render is paid for, as is a mistyped
`finish` name.

### JPEG with transparency

```
JPEG has no alpha channel, so it cannot hold a transparent background.
Use png or tiff, or pass transparent=False.
```

### `crop=True` does nothing

**A known defect, not a misunderstanding.** `snapshot(crop=True)` reports
`cropped: true` and returns the frame unchanged — verified against an opaque
ground, a transparent ground, a whole molecule and a single residue, all four
byte-identical to the uncropped capture. Frame with `focus()` instead, or trim
the result yourself, which is what
[`docs/figures/make_figures.py`](figures/make_figures.py) does.

---

## Sessions

`load_session` treats a session file as untrusted input and has ten distinct
refusals — including a file that is too large, one nested too deeply to parse,
one that **tells the viewer to fetch from somewhere else**, and one that holds
state `save_session()` never writes.

The risk in a session file is not reading it; it is what the deserialised state
can be made to do. See [SECURITY.md](../SECURITY.md).

**Not restored:** `define_field` values. Re-register any custom theme after
loading a session.

---

## Volumes

### The contour looks like noise

You probably passed a published contour level as `sigma` when it was
**absolute**. EMDB publishes author-recommended levels as absolute map values;
most viewers contour in sigma.

![The same map at its published absolute level, and the same number read as sigma](images/sigma-vs-absolute.png)

This is why `isosurface()` has no bare-number form — `unit` is required.

### The statistics disagree with the header

They are meant to. `load_volume` reports statistics computed by walking the
voxels, alongside the header's own numbers under `stated`. A large disagreement
says the file has been cropped or rescaled and nobody updated the header. That
is information, not a fault.

---

## Still stuck

[docs/backlog.md](backlog.md) records what is known-broken and what was fixed,
including the wrong turns. Items still open are marked **open**.
