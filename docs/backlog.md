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

## Gaps — the answer is unavailable

### 3. No structural-alignment mode for `superpose`

The benchmark's clearest loss. On 1AKE/4AKE, PyMOL's `cealign` finds the rigid
core and reports **3.460 Å over 112 residues**; protean aligns by sequence and
superposes everything it matched, giving **17.706 Å over 414**. Both numbers are
honest, but only one answers the question a structural biologist asked.

Fix: add a structural mode that finds the largest well-fitting subset rather
than trusting the sequence alignment — iterative outlier rejection over the
sequence-aligned pairs would get most of the way, and biotite has the
superposition primitives already.

### 4. Secondary structure cannot be selected

`ss H` and `ss S` work in PyMOL and are refused here, because secondary
structure is never assigned. Colouring *by* it works, since Mol\* assigns its
own for the theme.

Fix: assign secondary structure on load — Mol\*'s computable
`SecondaryStructure` is one route, a DSSP-style implementation over the
analysis copy is another. This is the most visible hole in the selection
grammar.

### 5. A handle cannot address one symmetry copy

Decision 9's unsolved limitation, now visible in a published comparison:
`interface("A", "B")` on 1HHO reports the whole tetramer's A–B interface
(2765.9 Å² per side) where PyMOL on the asymmetric unit reports one αβ pair
(873.9 Å²). Both are right about different molecules, and protean cannot
currently be asked for the second.

Needs new handle transport — atom-id ranges cannot distinguish copies that
share ids. Hardest item here.

### 6. Selection keywords still unsupported

`extend`, `bymolecule`, `rank`, `bound_to` all raise. `alt` cannot work as
things stand, because biotite resolves altlocs at parse time and no altloc
field survives. Each refusal names its reason, which is the right failure — but
the grammar is a subset of PyMOL's and the benchmark says so.

## Verified working

Recorded so the corpus is not re-run to rediscover them. All of the following
behaved correctly across B-DNA, a glycoprotein, a 58,870-atom GroEL/GroES
complex and a 38-model NMR ensemble:

- Structure loading, polymer selection and chain enumeration at every size
  tried, including 21 chains and 8,015 residues.
- Distance, angle and dihedral measurement; `combine`, `near` and `invert`.
- Session save and load, with all nine handles restored and none dropped.
- Every error case refused loudly with a reason and, where relevant, the list
  of valid alternatives: unknown representation, unknown colour theme, missing
  handle, malformed selection syntax, `frame()` with no trajectory, an
  interface against a chain that is not there, and `snapshot()` given two
  conflicting widths.
