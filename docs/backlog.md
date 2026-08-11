# Backlog

Gaps and bugs found by running protean against PyMOL
([docs/benchmark.md](benchmark.md)) and against a wider corpus of structures.
Nothing here is scheduled; this is the list to work from once the showdowns
have been run.

Ordered by how wrong the answer is, not by effort.

## Bugs — a wrong answer that looks right

### 1. `sidechain` returns the entire molecule on nucleic acids — fixed

`backbone` is now the sugar-phosphate backbone as well as protein N/CA/C/O, so
`sidechain` is the nucleobase — the variable part that decides which residue
this is, exactly as a protein sidechain does. On 1BNA:

```
backbone   -> 258 atoms   (was 0)
sidechain  -> 228 atoms   (was 486, the whole thing)
```

The sugar is included: the whole ribose ring, not just the phosphodiester
atoms the backlog originally listed. "Sugar-phosphate backbone" is what the
thing is called, and putting C1'/C2'/O4' in `sidechain` would mean the base
plus half its own sugar.

258 and 228 are also what PyMOL 3.1.0 and Mol\*'s bundled transpiler give for
the same file, so the differential suite now pins three independent
implementations rather than one, on top of exact offline counts. Legacy atom
naming is handled too — O1P/O2P and asterisks for primes.

### 2. Viewer and analysis disagree by 217 atoms on 5FJI — fixed

It was not glycans. 5FJI has 206 atom sites with two conformers and 11 with a
third: 423 alternate-location rows over 206 sites, so 217 rows more than there
are atoms. biotite resolves conformers at parse time and keeps one per site;
Mol\* draws all of them. Both are right, about the same molecule.

The invariant now measures that surplus from the file — independently of either
builder, since a difference computed by differencing the two explains any bug
along with itself — and subtracts it before calling anything a mismatch:

```
Loaded 5fji ... [asymmetric assembly, 15712 atoms here and 15929 in the
viewer; the 217 extra are alternate conformers, which analysis resolves to one
per site and the viewer draws all of]
```

A difference the conformers do not fully account for is still a loud mismatch,
and says how much of it they explain. Verified against a real Mol\* in the
differential suite; 1AKE has 12 such rows and was the second case.

### 3. `elem` accepts an element symbol that does not exist — fixed

```
select("elem Zz")   ->  No such element: 'ZZ'. Element symbols are a closed
                        set, so this would have matched nothing whatever the
                        structure held
select("elem Znn")  ->  ... Did you mean 'ZN'? ...
```

A symbol is refused only when it is **neither a real element nor present in
the structure**. Both halves matter:

- checking the periodic table alone would refuse a file that legitimately
  carries a symbol the table has never heard of, turning a real match into an
  error;
- checking the structure alone would refuse `elem Fe` on a structure with no
  iron, which is a true answer about the molecule rather than a mistake.

`resi 999999999999` and `chain \x00` still behave the old way, and should: a
residue number out of range legitimately matches nothing, and neither
vocabulary is closed.

### 4. `near` accepts a radius of zero or less — fixed

`near()` was the entry point the corpus found, but the grammar had the same
hole in all three of its spatial operators:

```
near(handle, -1)             ->  0 atoms, no complaint
"polymer within 0 of ..."    ->  0 atoms, no complaint
"resn ZN expand -3"          ->  the source unchanged, no complaint
```

All four now refuse, naming the operator and the value. The bound lives in a
distance-specific helper rather than in the shared number parser, because a
b-factor comparison may legitimately be zero or negative and a distance may
not.

`nan` and `inf` are refused too. `nan` slips past a bare `<= 0`, and an
infinite radius does not merely answer wrongly — with the guard removed,
`near()` on an infinite radius took six minutes to return in the cell list
rather than answering at all.

### 9. `OXT` is classified as a sidechain atom

Found while fixing item 1, by taking PyMOL's opinion on the same structures.
On 4HHB:

```
protean   backbone -> 2296     PyMOL   backbone -> 2300
```

The four atoms are the C-terminal `OXT`, one per chain. It is the second oxygen
of the terminal carboxylate — backbone by any chemical reading — and it
currently falls into `sidechain` instead.

Unlike item 1 this is not a clear win, which is why it was left alone: Mol\*'s
bundled transpiler *also* says 2296, so two independent implementations agree
with protean and only PyMOL differs. Changing it would move a hand-checked
ground-truth count and break agreement with the transpiler, so it wants a
deliberate decision rather than a drive-by fix. Four atoms per structure.

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
silently.

**A from-scratch Kabsch-Sander implementation was tried and abandoned.** About
250 lines: amide hydrogens placed from the preceding carbonyl, the published
energy term, n-turns, bridges and ladders. It was measured rather than assumed,
and the numbers did not justify shipping it:

| on 1UBQ | agreement with PyMOL | `ss H` | `ss S` |
|---|---|---|---|
| P-SEA (current) | 82% | 89 | 217 |
| the attempt | 82% | 148 | 217 |
| PyMOL / Mol\* | — | 132 | 274 |

Helix improved; strand did not move at all. On 1L2Y it did beat P-SEA (80%
against 65%), so the idea is not wrong — the implementation is. Two concrete
defects were found and are worth knowing before anyone tries again:

- the residue index space included waters, so "residue i+4" could be a solvent
  molecule rather than the fourth residue along — and every turn and bridge
  rule is expressed in exactly that adjacency;
- only 54 backbone hydrogen bonds were detected where DSSP finds 60-70 on this
  structure, so the bridge and ladder rules were being starved of input.

It was dropped rather than fixed because a half-right assignment is worse than
a documented divergence. P-SEA is at least a published algorithm whose
disagreement is characterised above; a home-grown one matching neither
reference would be a third answer with nothing behind it.

Closing this properly means either taking `mkdssp` as a real dependency —
biotite only wraps it — or porting DSSP against a trusted reference assignment
so each rule can be validated as it goes. Both are larger decisions than the
selector that prompted them.

## Not bugs, but worth knowing

Found by the corpus, expected to be refused, and correct as they stand:

- **`lighting(intensity=0)` is accepted.** Zero directional light is a real
  request — it is what `rig="flat"` does — so scaling to nothing is meaningful
  rather than nonsense.
- **`select()` after `clear_viewer()` is refused** with "No structure loaded".
  `clear_viewer` clears the analysis copy as well as the scene, which is what
  "clear" should mean; the corpus expected the Python side to survive.
- **Mol\*'s transpiler counts a smaller polymer in tRNA.** On 1EHZ it reports
  `polymer` as 1329 atoms where protean and PyMOL say 1652. 1EHZ is dense with
  modified nucleotides (2MG, H2U, PSU, 5MC, 7MG, 1MA), and they appear to be
  falling outside their polymer test. protean counting them is the right
  answer — they are in the chain — so this is recorded rather than chased. It
  is why 1BNA, not 1EHZ, is the fixture the nucleic backbone counts are pinned
  to.
- **Mol\*'s transpiler finds nothing for `nucleic`.** It returns 0 on 1BNA,
  which is nothing but DNA. Now asserted both ways in the differential suite,
  alongside the other divergences, so an upstream fix retires the claim.

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
