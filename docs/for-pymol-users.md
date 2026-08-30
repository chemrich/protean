# For PyMOL users

You already know what you want. This is where the buttons moved, and what is
genuinely missing.

---

## The one structural difference

**PyMOL's selection algebra all lives in one string. protean's does not.**

Leaf predicates are PyMOL's, unchanged — `chain A and resi 50-60`,
`byres (polymer within 5 of resn ZN)`, `ss H`, `b > 30`. What moved out is
composition *across selections you have already named*:

```python
# PyMOL
cmd.select("both", "sele1 or sele2")
cmd.select("shell", "byres (sele1 around 5)")
cmd.select("rest", "not sele1")

# protean
combine("union", of=["a", "b"], name="both")
near(of="a", radius=5, name="shell")
invert(of="a", name="rest")
```

The reason is that a selection in protean is a **handle** — a computed set, not
a string to re-evaluate. Analysis returns handles, display consumes them, so the
residues `interface()` found are the residues you colour without anyone
re-deriving them from a description. It also means there is no operator
precedence to get wrong across a composition built over four calls.

Everything else about the grammar is in [selections.md](selections.md).

---

## Command translation

| PyMOL | protean |
|---|---|
| `fetch 1ubq` | `fetch_structure("1ubq")` |
| `load file.pdb` | `fetch_structure("file.pdb")` |
| `select site, chain A and resi 50-60` | `select("chain A and resi 50-60", name="site")` |
| `show cartoon, site` | `show(handle="site", representation="cartoon")` |
| `hide everything` | `hide("auto")` — see below |
| `color red, site` | `color("#c0504d", name="site")` |
| `spectrum b, blue_white_red` | `color("uncertainty", name="site")` |
| `set cartoon_transparency, 0.5` | `opacity(0.5, name="site")` |
| `orient` | `orient()` |
| `zoom site` | `focus("site")` |
| `turn y, 90` | `spin(...)` / `keyframe()` + `record_timeline()` |
| `distance d, sele1, sele2` | `measure(kind="distance", names=["a", "b"])` |
| `label site, resn` | `label(name="site", level="residue")` |
| `align mobile, target` | `superpose(mobile, target, mode="sequence")` |
| `super mobile, target` | `superpose(mobile, target, mode="sequence")` |
| `cealign target, mobile` | `superpose(..., mode="structural")` — **not equivalent**, see below |
| `get_area sele` | `sasa(selection="...")` |
| `png out.png, dpi=300` | `snapshot(path="out.png", column="single", dpi=300)` |
| `save scene.pse` | `save_session("scene.protean")` |
| `set ray_trace_mode, 1` | `shading(style="cel") + effects(outline=True)` |
| `ray 1200, 900` | `path_trace(enabled=True, quality="high")` |
| `bg_color white` | `background(color="#ffffff")` |
| `set_color name, [r,g,b]` | `define_elements(colors={...})` |
| `alter b, ...` then `spectrum b` | `define_field("name", values=[...])` |

### `hide everything` has no exact analogue

PyMOL hides representations globally. protean's scene is built from **named
components**, and the load preset's is called `auto`. So:

```python
hide("auto")                                   # take the scene over
show(selection="polymer", name="fold", representation="cartoon")
```

Every preset that decides what is drawn does exactly this first. Drawing a
second representation over the load's own leaves two coincident pictures, which
read as one muddy one.

`preset("default")` is the way back.

---

## Things protean does that PyMOL does not

**Analysis returns handles.** `interface("A","B")` gives you buried area per
side, every interface residue with how much of it is buried, every contact with
its distance and kind, **the criterion it used**, and handles for both sides. In
PyMOL the equivalent is a script, and the obvious version of that script is
wrong — `get_area` measures a selection in the context of the whole loaded
object, so `chain A` is already occluded by chain B and the subtraction cancels
to 0.3 Å². [benchmark.md](benchmark.md) works this through.

**Biological assembly by default.** `fetch` gives you the asymmetric unit;
`fetch_structure` gives you the molecule as it exists, and `sym N` names one
copy. This is a real difference in what the numbers describe — see
[selections.md](selections.md#sym--copies-in-a-biological-assembly).

**Secondary structure is computed, not read.** `ss` runs DSSP, so it answers the
same way for a predicted model as for a deposited one.

**Figures carry a physical size.** `snapshot(column="double", dpi=600)` writes
the resolution into the file. `png` in PyMOL takes a DPI too, but protean
derives the pixel count from a millimetre width rather than making you.

**It refuses.** `show(representation="cartoonn")` is an error naming the valid
list. In PyMOL a mistyped selection frequently returns an empty set and a
successful-looking command.

**Print finishes and the boil.** Nine raster finishes applied after the capture,
and a stop-motion wobble whose amplitude follows how sure the data is about each
atom. Nothing in PyMOL does this. See [the gallery](gallery.md#print-finishes).

---

## What PyMOL does better

Honestly, from [benchmark.md](benchmark.md) — five tasks, protean wins two,
draws two, **loses one**.

**The selection grammar.** PyMOL's has no gaps where protean's has several.
`pepseq`, `beyond`, `near_to` and `last` are parsed and refused rather than
implemented. If you need those, protean cannot express them.

**`cealign` finds rigid cores `superpose` cannot.** On 1AKE against 4AKE —
adenylate kinase's hinge motion — `cealign` reports 3.46 Å over 112 residues
where protean's sequence mode reports 17.7 Å over 414. Both answer correctly;
they are answering different questions. `mode="structural"` is protean's nearest
equivalent and is more permissive: it maximises how much it superposes, so
expect more residues at a worse RMSD.

**Twenty-five years of accumulated capability**, and a REPL you already have
muscle memory for. protean is not trying to be a better REPL; it is trying to be
a better thing for a model to drive.

**If you want to explore a structure with your hands**, use
[Mol\*](https://molstar.org/viewer/) directly. It is better at that than
anything driving it can be, and protean does not try to replace it.

---

## What is not there yet

- `pepseq`, `beyond`, `near_to`, `last` in selections
- Ray-traced output is Mol\*'s path tracer, which needs a real GPU
- No scripting language of protean's own — the tools *are* the API, and the
  intended caller is a model

---

## See also

- [Selections](selections.md) — the grammar in full, with the gaps named
- [Tool reference](tools.md) — every tool, generated from the source
- [Cookbook](cookbook.md) — the tasks, worked
- [benchmark.md](benchmark.md) — the five-task comparison, with real output
