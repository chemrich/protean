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
tell.

**Since fixed** (decision 15). `interface("A","B")` now returns a `per_copy`
breakdown beside the total, and `interface("A","B", copy=0)` answers the
question PyMOL was asked: 1776.9 Å², of which 892.7 Å² on the A side. That is
2.1% above PyMOL's 873.9 because `get_area` uses dot-based SASA where biotite
uses Shrake-Rupley. protean's `copy=0` reproduces protean's *own*
asymmetric-unit answer to the decimal, which is the comparison that separates
this change from the method difference.

**protean wins** on not needing the split-objects trick, on returning contacts
and per-residue buried area in the same call, on stating its criterion, and on
reporting the copies separately rather than fusing them into one number.

## 3. Superposition — a draw, and the first table asked the wrong question

1AKE against 4AKE, adenylate kinase's hinge motion. Both are dimers, and the
original table superposed them whole:

| both chains | RMSD | Over |
|---|---|---|
| PyMOL `align` | 18.491 Å | 428 residues |
| PyMOL `super` | 18.515 Å | 427 residues |
| protean `superpose` | 17.706 Å | 414 residues |
| **PyMOL `cealign`** | **3.460 Å** | **112 residues** |

Read again, that table says something other than what it was first taken to
say. protean is being compared against `cealign`, but its like-for-like
counterparts are `align` and `super` — and it beats both. Asking a single
rigid transform to satisfy two chains that have moved relative to each other
has no good answer, and all three sequence-based tools correctly report that
it has none. `cealign` scores well by answering a different question — *find a
common substructure* — which is free to keep one lobe and discard the rest.

Point them all at a single chain and the comparison inverts:

| chain A only | RMSD | Over |
|---|---|---|
| PyMOL `align` | 2.069 Å | 214 residues |
| PyMOL `cealign` | 3.460 Å | 112 residues |
| **protean `superpose`** | **1.083 Å** | **112 residues** |

Over the same 112 residues `cealign` keeps, protean fits three times tighter.

**A draw.** The gap was never "no rigid core" — biotite's sequence mode already
discards outliers, which is where that 1.083 Å comes from. It was the absence
of a *structure-based* correspondence for proteins too diverged for a sequence
alignment to mean anything. `superpose(mode="structural")` now provides one: on
haemoglobin's alpha and beta chains it superposes 139 residues of the shared
fold where sequence mode anchors only 64.

The durable lesson is about the benchmark rather than the code. A table
comparing one tool's default against another tool's *different algorithm*
reports a loss regardless of which is better.

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

## 5. Selection grammar — PyMOL wins, by less than it did

Every one of these worked in PyMOL and was refused by protean when this was
first run. Five have since been implemented; the protean column is current:

| Selection | PyMOL | protean |
|---|---|---|
| `ss H` | 132 atoms | **148 atoms** — DSSP's count; PyMOL is echoing the file |
| `ss S` | 274 atoms | **217 atoms** — also DSSP's own count |
| `byres (name CA extend 1)` | 602 atoms | **602 atoms** |
| `bymolecule (resi 10)` | 602 atoms | **602 atoms** |
| `rank 5` | 1 atom | **1 atom** |
| `alt A` | 0 atoms | refused — one conformer per site is loaded, by choice |
| `bound_to (...)` | 2 atoms | **2 atoms** |

**PyMOL still wins, but by much less than this table first showed.** Four of
the seven rows have since been implemented and match PyMOL exactly, along with
`ss`. What is left is `alt`, which is a deliberate choice rather than a gap:
every conformer can be loaded, and loading them would mean computing buried
areas over atoms that sit on top of each other.

The one thing protean does better here is *how* it loses: each refusal names
the reason rather than returning zero atoms, which is the failure mode that
costs the most time.

`ss` looked like the partial one, and the comparison above is not measuring
what it claims. **PyMOL's 132 and 274 are the deposited `struct_conf` and
`struct_sheet_range` records read out of the mmCIF, not an assignment PyMOL
computed** — parsing those records directly gives both numbers exactly, and
PyMOL's own computed answer (`cmd.dss()`) is a different one again, 135 and
266. Mol\* reports 132/274 for the same reason.

Per residue, DSSP agrees with that deposited header 82% of the time — precisely
what protean's old P-SEA assignment scored against it. So this row was a
computed assignment against a file annotation, and the ~18% was never evidence
of a defect.

**protean now assigns secondary structure with its own DSSP port**, which
agrees with `mkdssp` 4.6.1 **99.84%** over 11 structures and 4,875 residues,
and exactly on 8 of them. That is why `ss H` reads 148 above: like PyMOL's, it
means every helix class. Unlike PyMOL's, the classes are separable —
`ss alpha`, `ss 3-10` and `ss pi` each select their own helix type, which
PyMOL cannot do at all.

Recorded as item 10 in [the backlog](backlog.md), with the full table and the
four rules that had to be found by measurement.

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
| 3. Superposition | Even (was recorded as PyMOL) |
| 4. Publication figure | protean, narrowly |
| 5. Selection grammar | **PyMOL** |

The pattern is consistent with what protean set out to be. It wins where the
answer needs to arrive as **structured data a model can compose** — a table of
contacts with handles attached, a figure with its DPI in the file. It loses
where the field has 25 years of accumulated capability, most clearly in a
selection grammar with no gaps.

Task 3 was recorded as a loss and re-measured as a draw; the note there is
worth reading, because the error was in the comparison rather than the code.
`superpose` has since gained the structural mode it was missing. The remaining
recorded gap is secondary-structure assignment, so `ss` becomes selectable. The
symmetry-copy limitation in task 2 needs new handle transport and is harder.
