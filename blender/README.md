# blender/

Scene rigs, materials and render harnesses for producing high-end stills of
molecular structures in Blender via the Molecular Nodes add-on.

This directory follows `viewer/`'s pattern: a **foreign toolchain living at the
top level**, not part of the installed Python package. `pyproject.toml` packages
only `src/protean_mcp`, so nothing here ships with `pip install protean`, and
nothing here is imported by the MCP server. Everything runs inside Blender's own
bundled Python, launched as a subprocess.

## Why this is separate from `src/protean_mcp/`

Three reasons, all load-bearing:

**Licensing.** protean is MIT. Molecular Nodes is GPL-3.0. Keeping the
Blender-facing code out of the packaged tree — and driving Blender at arm's
length as a subprocess rather than importing it — keeps the core's licensing
uncomplicated. `docs/soft-matter-plan.md` reached the same conclusion
independently before any of this was written.

Two details worth stating exactly, because "nothing here ships" is easy to read
more broadly than it is meant. The **wheel** contains only `src/protean_mcp`;
verified by building it, and by building a wheel *from* the sdist, both of which
have zero `blender/` entries. So nothing here is ever installed or importable.
The **sdist** is a source snapshot of the repository and does contain these
files, as does the public repository itself. They sit under the root MIT
`LICENSE` while importing a GPL-3.0 add-on and running inside GPL Blender. No
Molecular Nodes or Blender code is vendored or copied here — these are original
scripts that call an API — but if you redistribute them, that is the
relationship to be aware of.

**Python version.** Molecular Nodes and the `bpy` wheel are pinned to Python
3.13 exactly. protean supports 3.11 and up. These cannot share an interpreter,
which is fine, because they do not need to: Blender brings its own.

**Latency.** Every protean tool is synchronous and returns in well under a
second. A Cycles render here takes 15–180 seconds for a small protein, and tens
of minutes for a large assembly. That does not fit protean's tool contract and
should not be forced into it.

## Environment

Verified working on macOS / Apple Silicon, September 2026. Headless GPU
rendering via Metal works — no CPU fallback needed, and no virtual display.

```
brew install --cask blender                       # Blender 5.2.1 LTS
blender --online-mode -c extension install -s -e molecularnodes
```

The `--online-mode` flag is **required**. Without it the extension system
refuses network access and the install fails with a bare
`RuntimeError: Operator ... Online access required`.

Facts that cost time to discover, recorded so nobody rediscovers them:

- The add-on is namespaced under the extension system. Use
  `bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.molecularnodes")`
  then `import bl_ext.blender_org.molecularnodes as mn`. A bare
  `import molecularnodes` fails.
- **Never pass `--factory-startup`** — it disables the add-on entirely.
- `mn.Canvas()` with its default template *replaces the scene* and injects a
  camera plus three sun lamps, which silently contaminates any lighting
  comparison. Pass `template=None`.
- `Canvas.load_preset()` supplies **zero meshes**. There is no ground, no
  backdrop, no floor — despite a docstring calling it a studio. The cyclorama
  and contact shadow in `stylespace_render.py` exist because nothing ships them.
- A Molecular Nodes object has **zero material slots**; material lives inside
  the Geometry Nodes tree, so `ob.material_slots[0]` raises `IndexError`.
- Selections are **MDAnalysis** syntax, not PyMOL: `chainID A` not `chain A`,
  `resname HEM` not `resn HEM`. A wrong token raises inside Molecular Nodes and
  is swallowed as a `UserWarning`, after which you render a perfectly good
  picture of nothing highlighted. Assert
  `mol.universe.select_atoms(sel).n_atoms > 0` before every `add_style`.
- Force `CYCLES`. Under EEVEE, sphere styles silently yield zero instances.
- `Camera.frame_points()` reads `matrix_world`, which Blender does not refresh
  after writing `.rotation_euler` or `.location`. Call
  `bpy.context.view_layer.update()` in between, or you will silently render the
  subject out of frame — a valid PNG that reports success and shows nothing.
- Run Blender **serially**. Two concurrent `--background` processes once broke
  biotite's import through a bytecode-cache race.
- Cold start is roughly 75 s for Metal kernel compilation; warm renders are
  15–40 s. Do as much as possible inside one process.

## What is here

| file | what it is |
|---|---|
| `stylespace_render.py` | The studio rig, the base material set, the discipline harness, and the style-space panel definitions. The rig is the reusable part. |
| `materials_render.py` | The material library — roughly eighteen surfaces, built on the same rig. |
| `measure_stylespace.py` | Measurement instrument for the style-space panels. |
| `measure_materials.py` | Measurement instrument for the material sheet. |
| `probe_env.py` | Environment probe used to check what the installed Molecular Nodes actually exposes, rather than trusting recalled API shapes. |
| `chonk_render.py` | Sheet B: Style Cartoon thickened and reshaped. Two ladders — everything scaled together, and thickness raised with width pinned, which is the one that turns a ribbon into a bar. |
| `surface_render.py` | Sheet A: six kinds of SDF surface — dilated, eroded, melted, filleted, inflated, and fused instead of per-chain. |
| `section_render.py` | Sheets C and D: the backbone swept with arbitrary cross-sections, and ribbon thickness driven by a per-residue measurement against both a flat and a shuffled control. |

Run them as:

```
blender --background --python blender/chonk_render.py -- --subject 4HHB
blender --background --python blender/surface_render.py -- --subject 1HSG
blender --background --python blender/section_render.py -- --mode sections --subject 4HHB
blender --background --python blender/section_render.py -- --mode databound --subject P04637 --plddt
```

There is no `--help`. Each script parses only its own flags and ignores
anything else, so a typo in a flag name is silent — but a typo in a panel id
passed to `--only` is not, because the id is checked against the sheet.

The three sheet scripts take `--subject`, `--samples`, `--only` and `--out`.
`stylespace_render.py` and `materials_render.py` take `--only`, `--samples`,
`--out` (and `--tag` for the latter). Output defaults under `research/`, which
is gitignored in full; pass `--out` to put it anywhere else.

**Run Blender serially.** Two concurrent `--background` processes once broke
biotite's import through a bytecode-cache race.

## The discipline harness

`stylespace_render.py` carries the piece most worth keeping. Each panel
**declares** which of eight scene dimensions it varies — camera, depth of field,
lights, world, ground, material, colour, geometry. After the scene is built, the
harness hashes all eight and asserts the undeclared ones are bit-identical to
the carrier. It caught zero drift across twenty-one panels, and independent
verification put the carrier's background within 0.0002 luminance across every
panel and the subject bounding box inside ±3 pixels.

This is "change one thing per green" applied to pictures, and it is the reason
any comparison rendered here can be trusted. Do not remove it to save time.

**A measurement is only as good as its ability to see the thing it names.** One
metric in an early run measured an edge against the ground immediately beside
it, which collapses toward zero on a dark ground no matter what the edge does —
and it duly reported a 63% loss as confirmation of a prediction the image
plainly contradicted. State what a metric would show if the effect were absent,
and render the control that produces that.

## `research/` — never committed

`blender/research/` is gitignored as a whole directory and must stay that way.
It holds working material for this exploration: our own renders, and written
analysis of published figures and of the illustration literature.

**No copyrighted work, and no art that is not ours, belongs in this repository.**
Some of the analysis was derived from third-party cover artwork; the artwork
itself is deliberately not here, and the source images stay outside the project
tree entirely. Local copies are fine for development. Committing them is not.

The guard is checked, not assumed: `git add blender/` stages the scripts and
this README and nothing else, and naming `blender/research/` directly is refused
without `-f`. If you ever find yourself reaching for `-f` here, that is the
signal to stop.

## Status

The rig, the materials and the harness are working scratch code, landed as-is
rather than refactored, because they have been verified by rendering and a
speculative split would need re-rendering to re-verify.

**The rig is now copied into five scripts rather than imported, and that is a
debt with a due date.** The original note here said to factor it out "when
there is a second consumer"; three more arrived at once. The copies are close
but not identical — the studio, the camera solve and the signature functions
were carried over, while the material, the subject handle and the panel loop
differ per sheet, and the newer scripts fixed a menu-socket bug in the
signature that the older ones had. Anyone touching two of these files should
expect to make the same edit twice. The refactor was still deferred because
each script has been verified by rendering, and a shared module would put all
five sheets back in the queue to re-verify at once.

One consequence worth knowing before you copy the rig a sixth time: the
discipline harness declares `geometry` varied on every panel of every sheet,
so the geometry signature guards nothing between a panel and the carrier. What
it does guard is panel-against-panel — a panel whose evaluated geometry is
bit-identical to an earlier one is reported as a violation, which is how a
"per-chain" panel that merely set a socket to its own default was caught.

Nothing here is wired to protean. There is no MCP tool, no bridge, and no
import in either direction.
