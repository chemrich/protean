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

### 5. No structural-alignment mode for `superpose` — added, and the
diagnosis was wrong

`superpose(mode="structural")` matches residues by the shape of their local
backbone instead of by sequence, using biotite's TM-align-inspired
`superimpose_structural_homologs`. On haemoglobin's alpha and beta chains it
superposes **139 residues of the shared fold where sequence mode anchors only
64** — the remote-homolog case, which is what this class of algorithm is for.

The proposed fix here — iterative outlier rejection over the sequence-aligned
pairs — was tried first and does not work, for two reasons worth recording:

1. **biotite already does it.** `superimpose_homologs` removes outliers
   internally. That is why chain A of 1AKE/4AKE gives 112 residues at 1.083 Å,
   which is *better* than `cealign`'s 3.460 Å over its 112.
2. **On the case that motivated it, rejection has nothing to find.** Superposing
   the two dimers whole, RMSD falls smoothly from 17.7 Å with no knee, and the
   largest sub-2 Å core is 18 residues. The correspondence across two chains
   that have moved relative to each other is the problem, not the subset.

The 17.706 Å figure was also compared against the wrong baseline. PyMOL's
`align` gives 18.491 Å and `super` 18.515 Å on that same whole-dimer task, so
protean was already winning the like-for-like comparison and losing only to a
different class of algorithm. See [benchmark.md](benchmark.md), which has been
corrected.

### 6. Secondary structure cannot be selected

`ss H` and `ss S` work in PyMOL and are refused here, because secondary
structure is never assigned. Colouring *by* it works, since Mol\* assigns its
own for the theme.

Fix: assign secondary structure on load — Mol\*'s computable
`SecondaryStructure` is one route, a DSSP-style implementation over the
analysis copy is another. This is the most visible hole in the selection
grammar.

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
