# Backlog

Gaps and bugs found by running protean against PyMOL
([docs/benchmark.md](benchmark.md)) and against a wider corpus of structures.
Nothing here is scheduled; this is the list to work from once the showdowns
have been run.

Ordered by how wrong the answer is, not by effort.

## Bugs — a wrong answer that looks right

### 1. `sidechain` returns the entire molecule on nucleic acids

On 1BNA (B-DNA dodecamer, 486 polymer atoms):

```
backbone   ->   0 atoms
sidechain  -> 486 atoms   <- the whole thing
```

`backbone` is defined as protein N/CA/C/O, so it correctly finds nothing in
DNA — visibly nothing, which is survivable. `sidechain` is defined as *not
backbone*, so it returns every nucleic atom and looks like a real answer.
Anyone colouring or measuring "sidechains" of a nucleic acid gets the whole
molecule and no indication.

Fix: either define nucleic backbone (P, OP1/OP2, O5', C5', C4', C3', O3') and
let sidechain be the bases, or refuse both on non-protein polymers with a
reason. The differential suite already asserts `backbone` is zero on 1BNA; it
does not test `sidechain`, which is why this survived.

### 2. Viewer and analysis disagree by 217 atoms on 5FJI

```
Loaded 5fji ... MISMATCH: 15712 atoms here but 15929 in the viewer.
```

Decision 9's invariant is doing its job — the divergence is reported loudly and
the reply says to treat counts, buried areas and potentials as unreliable. But
the divergence itself is a bug: on a glycoprotein, biotite and Mol\* build
different numbers of atoms from the same file, and 5FJI is in the test corpus
precisely because branched glycan entities are handled differently from other
het groups.

Until it is understood, analysis on glycoproteins is unreliable. Worth
diagnosing before anything else here, because it is the one finding that makes
numbers wrong rather than missing.

### 3. `elem` accepts an element symbol that does not exist

```
select("elem Zz")  ->  0 atoms, no complaint
```

Element symbols are a closed set, so `Zz` is a typo, not a query — and 0 atoms
reads as "this structure has none of those" rather than "you misspelled it".
protean already refuses an unknown representation and an unknown colour theme
by checking against the live registry; `elem` has the same shape and no check.

`resi 999999999999` and `chain \x00` behave the same way. Those are weaker
cases — a residue number out of range legitimately matches nothing — but the
element one is a straightforward typo the tool could catch.

### 4. `near` accepts a radius of zero or less

```
near(handle, -1.0)  ->  0 atoms, no complaint
near(handle,  0.0)  ->  0 atoms, no complaint
```

A non-positive radius is not a question anyone means to ask, and the empty
answer looks like a legitimate result. Every other numeric argument in the
project is bounds-checked — opacity, metalness, cel steps, bounces, dpi, frame
counts — so this is an omission rather than a decision.

## Gaps — the answer is unavailable

### 5. No structural-alignment mode for `superpose`

The benchmark's clearest loss. On 1AKE/4AKE, PyMOL's `cealign` finds the rigid
core and reports **3.460 Å over 112 residues**; protean aligns by sequence and
superposes everything it matched, giving **17.706 Å over 414**. Both numbers are
honest, but only one answers the question a structural biologist asked.

Fix: add a structural mode that finds the largest well-fitting subset rather
than trusting the sequence alignment — iterative outlier rejection over the
sequence-aligned pairs would get most of the way, and biotite has the
superposition primitives already.

### 6. Secondary structure cannot be selected — added, with a caveat

`ss H`, `ss S` and `ss L` now resolve. The long names work too (`ss helix`),
several classes can be asked for at once (`ss H+S`), and an unrecognised class
is refused rather than matching nothing.

It is assigned rather than read from the file, using biotite's P-SEA over
backbone geometry. Deposited HELIX/SHEET records are the depositor's opinion
and are absent from anything predicted or minimised, so computing it means
`ss` answers the same way for every structure — including AlphaFold models.
Mol\*'s computable `SecondaryStructure` was the other candidate and was not
used: it would tie a selection to having a rendering session open, which no
other selection needs.

**The caveat, which is item 10 below:** P-SEA is not what PyMOL and Mol\* use,
and the numbers differ.

### 7. A handle cannot address one symmetry copy

Decision 9's unsolved limitation, now visible in a published comparison:
`interface("A", "B")` on 1HHO reports the whole tetramer's A–B interface
(2765.9 Å² per side) where PyMOL on the asymmetric unit reports one αβ pair
(873.9 Å²). Both are right about different molecules, and protean cannot
currently be asked for the second.

Needs new handle transport — atom-id ranges cannot distinguish copies that
share ids. Hardest item here.

### 8. Selection keywords still unsupported

`extend`, `bymolecule`, `rank`, `bound_to` all raise. `alt` cannot work as
things stand, because biotite resolves altlocs at parse time and no altloc
field survives. Each refusal names its reason, which is the right failure — but
the grammar is a subset of PyMOL's and the benchmark says so.

### 10. Secondary structure disagrees with every other tool by ~18%

On 1UBQ:

| | `ss H` | `ss S` |
|---|---|---|
| PyMOL 3.1.0 | 132 | 274 |
| Mol\* transpiler | 132 | 274 |
| protean (P-SEA) | 89 | 217 |

PyMOL and Mol\* agree exactly with each other, and protean is the outlier —
both of them use a DSSP-style hydrogen-bond criterion where P-SEA works off
backbone geometry alone.

Per residue the two assignments agree **82%** of the time, and every
disagreement is the same kind: P-SEA trims one or two residues from the ends of
each element and misses the shortest ones altogether. The elements are in the
same places; their edges are not.

```
PyMOL   SSSSSSS--SSSSSSSS-----HHHHHHHHHHHH-----SSSSSS--SSS-----HHHH----SSSSSSSSS----
protean -SSSSS----SSSSSSS-----HHHHHHHHHHH-------SSSS-------------------SSSSSSSSSS---
```

Both numbers are asserted in the differential suite, so this cannot drift
silently. Closing it means implementing the Kabsch-Sander hydrogen-bond
criterion in Python — biotite ships only a wrapper around an external `mkdssp`
binary, which is not a dependency worth taking for this.

## Not bugs, but worth knowing

Found by the corpus, expected to be refused, and correct as they stand:

- **`lighting(intensity=0)` is accepted.** Zero directional light is a real
  request — it is what `rig="flat"` does — so scaling to nothing is meaningful
  rather than nonsense.
- **`select()` after `clear_viewer()` is refused** with "No structure loaded".
  `clear_viewer` clears the analysis copy as well as the scene, which is what
  "clear" should mean; the corpus expected the Python side to survive.

## Verified working

Recorded so the corpus is not re-run to rediscover them. From 499 probes across
two personalities — 398 expected successes, 101 expected refusals, 7 surprises
— over B-DNA, a glycoprotein, haemoglobin, carbonic anhydrase, a 38-model NMR
ensemble and ubiquitin:

- Structure loading, polymer selection and chain enumeration at every size
  tried, including 21 chains and 8,015 residues.
- Distance, angle and dihedral measurement; `combine`, `near` and `invert`.
- Session save and load, with all nine handles restored and none dropped.
- Every enum refused rubbish with the valid list attached: representations,
  colour themes, lighting rigs, shading styles, material finishes, presets,
  path-trace quality, gradients, measurement kinds, label levels, snapshot
  columns and formats, movie containers and spin modes.
- Every numeric bound held where one exists: opacity, metalness, cel steps,
  lighting intensity below zero, dpi, snapshot size, turntable frame counts.
- Every operation on a handle that does not exist was refused, by name, with
  the known handles listed — across show, color, opacity, material, shading,
  focus, label, remove, measure, combine, near and invert.
- Every trajectory and movie operation attempted with nothing loaded was
  refused: `frame`, `rmsf`, `rmsd_series`, `record_trajectory`,
  `record_timeline`, `movie`, and `load_trajectory` on a missing file and on a
  file that is not a trajectory.
- 27 malformed selections were refused, including empty strings, unbalanced
  parentheses, dangling operators, and keywords protean does not support.
- Awkward-but-legal handle names were accepted: spaces, slashes, dots, digits,
  300 characters, and an emoji.
- Structures loaded and selected correctly at every size tried, including 21
  chains and 8,015 residues; measurements and set operations were exact;
  sessions restored every handle with none dropped.
