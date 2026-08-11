# protean vs PyMOL, on five common tasks

PLAN asks for this comparison. What follows is the real output of both, captured
on 2026-08-11 from PyMOL 3.1.0 and protean at `c6caed3`. The scripts are in
[`docs/benchmark/`](benchmark/) so the comparison can be re-run and disagreed
with.

**protean loses two of the five outright** and carries a serious caveat on a
third. A benchmark that only showed wins would not be evidence.

## What is and is not measured

**Not timings.** protean has a browser round-trip PyMOL does not, and PyMOL has
25 years of C behind it. Timing measures an architecture choice nobody is
deciding between. The PyMOL on this machine also autoloads an MCPymol plugin
that fails to bind its socket, so it is not a clean-room install and any number
from it would already be suspect.

**Not first-try success rate.** protean's design bet is that a model gets things
right more often against schema-visible enums and structured returns than
against free-text commands and stdout. Testing that needs models in a loop, so
it is not claimed here. What is shown is the artifacts each side requires and
returns; judge the bet from those.

**What is measured:** the code each side needs, the shape of what comes back,
what happens on bad input, and whether the task is possible at all.

---

## 1. Catalytic site — a tie on the answer

Both find the zinc coordination of carbonic anhydrase II exactly: HIS 94, 96 and
119, 30 atoms.

```python
# PyMOL
cmd.select("zn", "resn ZN")
cmd.select("site", "byres (polymer within 2.6 of zn)")
model = cmd.get_model("site and name CA")
[(a.chain, a.resi, a.resn) for a in model.atom]   # -> parse it yourself
```

```python
# protean
select("byres (polymer within 2.6 of metals)", name="site")
# -> {"atom_count": 30, "residue_count": 3,
#     "residues": [{"chain": "A", "seq": 94, "comp": "HIS"}, ...]}
```

The difference is not the answer, it is what a caller holds afterwards. PyMOL
returns a chempy model to iterate; protean returns the residues already
enumerated, and `site` is now a handle that `combine`, `near` and the analysis
tools take directly. `metals` is a protean keyword; PyMOL needs the element
named.

**Even.** protean saves a parse; PyMOL's `get_model` gives more per atom.

## 2. Interface buried area — protean wins on the naive script, and the numbers are not comparable

The obvious PyMOL script is wrong:

```python
cmd.set("dot_solvent", 1)
a, b = cmd.get_area("chain A"), cmd.get_area("chain B")
ab = cmd.get_area("chain A or chain B")
(a + b - ab) / 2          # -> 0.3 A^2 per side
```

**0.3 Å² is a confidently wrong answer.** `get_area` measures a selection *in
the context of the whole loaded object*, so "chain A" is already occluded by
chain B and the subtraction cancels. The correct PyMOL is to split the chains
into separate objects first:

```python
cmd.create("chA", "chain A"); cmd.create("chB", "chain B")
(cmd.get_area("chA") + cmd.get_area("chB") - cmd.get_area("1hho")) / 2
# -> 873.9 A^2 per side
```

protean's one call returns a table:

```python
interface("A", "B")
# buried_area_a 2765.9   buried_area_b 2764.3
# interface_residues_a: 70 entries, each {"chain", "seq", "comp", "buried", "sym"}
# contacts: 24, each {"a", "b", "distance", "kind"}
# handles: {"a": "iface_a", "b": "iface_b"}
# criterion: "heavy-atom N/O within 3.5 A (no hydrogens ... so angles cannot be checked)"
```

**The two numbers measure different molecules and must not be read as protean
being three times better.** protean loads the *biological assembly* by default
(decision 9), so 1HHO is the α2β2 tetramer and `interface("A","B")` reports the
total A–B interface across it. PyMOL loaded the deposited asymmetric unit, one
αβ dimer. protean's own residue entries carry `sym: 1`, which is how you can
tell. Addressing a single symmetry copy is a known protean limitation, recorded
in decision 9 and still unsolved.

**protean wins** on not needing the split-objects trick, on returning contacts
and per-residue buried area in the same call, and on stating its criterion. It
**loses** on not being able to ask about one copy.

## 3. Superposition — PyMOL wins

1AKE against 4AKE, adenylate kinase's hinge motion:

| | RMSD | Over |
|---|---|---|
| PyMOL `align` | 18.491 Å | 428 residues |
| **PyMOL `cealign`** | **3.460 Å** | **112 residues** |
| protean `superpose` | 17.706 Å | 414 residues |

protean aligns by sequence and superposes everything it matched, which for a
hinge motion is the wrong thing: the two lobes cannot be fitted at once, and
17.7 Å is the honest number for trying. `cealign` finds the rigid core
structurally and reports 3.46 Å over the 112 residues that actually superpose —
which is the answer a structural biologist wants.

**PyMOL wins.** protean has no structural-alignment mode, only sequence-based.
That is a real gap, not a presentation difference.

## 4. Publication figure at 600 dpi — protean wins, narrowly

PyMOL renders and does write DPI:

```python
cmd.ray(4323, 3242)
cmd.png("figure.png", dpi=600)
```

protean:

```python
snapshot("figure", column="double", dpi=600, format="tiff")
# -> {"pixels": [4323, 3242], "dpi": 600.0, "width_mm": 183.0, "bytes": ...}
```

Both produce a 600-dpi file. The differences:

- protean takes a **physical size**; PyMOL takes pixels. Asking a model for 600
  dpi and leaving it to multiply is how a "600 dpi" figure ends up 900 pixels
  wide, which no return value would catch.
- protean writes **TIFF**; PyMOL writes PNG only.
- protean **refuses an incomplete capture** — at large sizes a renderer can
  return an image of exactly the right dimensions with most of it never
  written, and protean checks the alpha channel and fails rather than saving it.
- PyMOL's `ray` is a mature CPU ray tracer that needs no GPU. protean's path
  tracer needs a real one and hangs under software rendering, which is why its
  tests are gated.

**protean wins narrowly** on physical sizing, TIFF, and the capture check.
PyMOL wins on rendering anywhere without a GPU.

## 5. Selection grammar — PyMOL wins clearly

Every one of these works in PyMOL and is refused by protean:

| Selection | PyMOL | protean |
|---|---|---|
| `ss H` | 132 atoms | refused — secondary structure is not assigned by the evaluator |
| `ss S` | 274 atoms | refused |
| `byres (name CA extend 1)` | 602 atoms | refused — `extend` unsupported |
| `bymolecule (resi 10)` | 602 atoms | refused — connected-molecule grouping unsupported |
| `rank 5` | 1 atom | refused — per-object atom rank is not tracked |
| `alt A` | 0 atoms | refused — altlocs are resolved at parse time |
| `bound_to (...)` | 2 atoms | refused |

**PyMOL wins.** Its grammar is complete and protean's is a subset. The one thing
protean does better here is *how* it loses: each refusal names the reason rather
than returning zero atoms, which is the failure mode that costs the most time.

There is a second PyMOL advantage no table shows: **a model already knows PyMOL**.
It writes `byres (chain A within 4 of chain B)` correctly with no tool schema at
all, because the syntax is deep in its training data. protean keeps PyMOL syntax
for leaf predicates for exactly that reason (decision 6).

---

## Summary

| Task | Winner |
|---|---|
| 1. Catalytic site | Even |
| 2. Interface buried area | protean (with a symmetry caveat) |
| 3. Superposition | **PyMOL** |
| 4. Publication figure | protean, narrowly |
| 5. Selection grammar | **PyMOL** |

The pattern is consistent with what protean set out to be. It wins where the
answer needs to arrive as **structured data a model can compose** — a table of
contacts with handles attached, a figure with its DPI in the file. It loses
where the field has 25 years of accumulated capability: a structural aligner
that finds a rigid core, and a selection grammar with no gaps.

Two of these losses are fixable and are worth recording as such: a
structural-alignment mode for `superpose`, and secondary-structure assignment so
`ss` becomes selectable. The symmetry-copy limitation in task 2 needs new
handle transport and is harder.
