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

### 9. `OXT` is classified as a sidechain atom — fixed

`backbone` is now N/CA/C/O **plus OXT**, the second oxygen of the C-terminal
carboxylate. It hangs off the same carbonyl carbon as O, so calling it a
sidechain atom was the odd position; four atoms per structure, one per
modelled C-terminus.

```
                before   after    PyMOL
backbone         2296     2300     2300
sidechain        2088     2084     2084
```

This was recorded as wanting a decision rather than a fix, because Mol\*'s
transpiler agreed with the old answer and two implementations against one is
usually the wrong side to be on. The tiebreak is that the chemistry is not
actually in dispute: OXT is main chain, and `sidechain` returning it was
indefensible whatever any transpiler says.

The three affected counts moved out of the differential suite's agreement
table and into its recorded divergences, so the 4-atom disagreement with Mol\*
is asserted from both sides rather than dropped. Transport coverage did not
shrink with them: `test_handles_survive_the_trip_to_the_viewer` now runs over
the divergences too, which it always should have — whether our atom ids
survive the trip to the viewer has nothing to do with whether their parser
agrees about the selection.

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

### 8. Selection keywords still unsupported — mostly closed

`extend`, `bymolecule`, `bound_to`, `neighbor` and `rank` all work now. Every
count was checked against PyMOL 3.1.0 on the same file and matches exactly:

| on 1UBQ | PyMOL | protean |
|---|---|---|
| `byres (name CA extend 1)` | 602 | 602 |
| `name CA extend 1` | 298 | 298 |
| `name CA extend 2` | 464 | 464 |
| `bymolecule (resi 10)` | 602 | 602 |
| `bymolecule (resn HOH)` | 58 | 58 |
| `bound_to (resi 10 and name CA)` | 2 | 2 |
| `rank 5` | 1 | 1 |

Two of the original reasons for refusing them were wrong. Bond topology *is*
available — biotite assigns it from residue templates, at 25 ms for a
59,000-atom assembly, so it is derived on demand rather than at load. And
`rank` needed no tracking at all: it is the atom's position in the array,
distinct from `index`, which is the file's own `atom_site.id`.

`extend 0` and a fractional `extend` are refused, on the same argument as a
non-positive radius in item 4.

**`alt` is still refused, but now by choice rather than impossibility.**
`get_structure(altloc="all")` exists, so every conformer *can* be loaded — and
doing so would make the viewer and analysis atom counts agree exactly, which is
the other half of item 2. The reason not to is that buried areas, potentials
and contact counts would then be computed over atoms sitting on top of each
other. The refusal now names that tradeoff instead of claiming it cannot be
done, so it can be argued with. **Wants a decision.**

### 11. The two engines disagree about what a bond is

Found while implementing item 8. `resn HEM extend 1` on 4HHB returns the hemes
unchanged for protean and for PyMOL — a residue template gives iron no bond to
the protein — where Mol\* returns four atoms more, having modelled the Fe-NE2
coordination bond to the proximal histidine.

Neither is wrong. It is a real question about whether metal coordination is a
bond, and the two answers are both defensible. Recorded and asserted from both
sides so that either engine changing its mind is visible, rather than folded
into the agreement table where it would look like a bug.

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
