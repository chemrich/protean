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

### 7. A handle cannot address one symmetry copy — fixed

`interface("A", "B")` on 1HHO reported the whole tetramer's A–B interface
(2765.9 Å² per side) and protean could not be asked for one αβ pair. It can
now: `sym N` selects a copy, `interface(copy=N)` describes one, and with no
copy named the reply carries a per-copy breakdown beside the total.

```
interface("A", "B")          -> 5530.2 A^2 total; per_copy 1776.9, 1786.7
interface("A", "B", copy=0)  -> 1776.9 A^2 (892.7 + 884.2)
select("chain A and sym 0")  -> one subunit, and drawn as one subunit
```

**"Needs new handle transport" was wrong.** Mol\* 4.18 has a MolScript
predicate for the symmetry operator, so the emitter simply keys each copy's
atom-id test on the operator name. Three things had hidden it, each of which
parses cleanly and matches nothing: the script alias is `atom.op-name` rather
than the symbol-table path `atom-property.core.operatorName`; MolScript string
literals take **backticks**, so a double-quoted `"ASM_1"` parses as a symbol
and matches nothing while reporting success; and `operatorKey` is `-1` on
every unit, so only the name distinguishes copies.

**The mapping is off by one.** Mol\* pre-increments its operator index, so
`sym_id k` is `ASM_{k+1}`. Counting could never have caught that — every copy
has the same atoms, residues and chains — so it is proven by centroid against
a real viewer on 1HHO and on 1COI, which has three copies because a two-copy
fixture cannot distinguish "correct" from "consistently reversed".

The `rank` refusal added in PR 50 is retired: it existed only because the
transport could not name a copy.

One note on the 873.9 Å² this entry used to compare against. That is *PyMOL's*
number for the asymmetric unit, from `get_area` on split objects. protean's own
answer for the same pair is 892.7 Å² per side, and its `copy=0` result
reproduces its own asymmetric-unit result exactly. The 2.1% gap is dot-based
SASA against biotite's Shrake-Rupley — a method difference, not this item.

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

### 10. Secondary structure — benchmark corrected, then DSSP ported — fixed

**Outcome first.** The ~18% was measured against the deposited header rather
than against any implementation (below). Once that was established, DSSP was
ported in-tree and now replaces P-SEA, so `ss` distinguishes all three helix
types. Against `mkdssp` 4.6.1 over 11 structures and 4,875 residues the port
agrees **99.84%**, and exactly on 8 of them:

| class | mkdssp | protean | matched |
|---|---|---|---|
| H alpha-helix | 1255 | 1255 | **1255** |
| G 3-10 helix | 246 | 248 | 246 |
| I pi-helix | 58 | 58 | **58** |
| E strand | 882 | 880 | 880 |
| B isolated bridge | 112 | 109 | 108 |
| T turn | 504 | 504 | 502 |
| S bend | 496 | 498 | 496 |

`ss H` is now every helix class, as PyMOL's is, so it moves 89 → 148 atoms on
1UBQ. `ss S` is unchanged at 217. The types are addressable as `ss alpha`,
`ss 3-10` and `ss pi`, which is what makes colouring by helix type possible.

**Four rules had to be found by measurement**, and each was wrong in a way that
looked right — every one is mutation-tested:

- **pi-helix outranks alpha-helix.** DSSP 4 reversed the 1983 order. Exactly 15
  residues on 4HHB, and the counts look plausible either way.
- **T covers a turn's interior**, not its two bonded endpoints. Over-assigning
  it also swallowed most of the bends, since T outranks S.
- **An overlapping 3-10 helix is discarded whole**, not trimmed. Trimming
  leaves 1-2 residue G stubs on the ends of alpha-helices: 13 wrong on 4HHB.
- **...but that rule must not apply between pi and alpha**, which are separated
  by summary priority instead. Applying it there costs 8 real alpha residues on
  5FJI. Same rule, two readings, only measurement separates them.

Polyproline II (DSSP 4's `P`) is deliberately not implemented: a 2021 addition,
not part of the published algorithm, and it never overrides a structured class.

What follows is the original correction, kept because the *reasoning* is the
reusable part.

---

#### The ~18% was measured against the wrong thing

**The original claim was that protean disagrees "with every other tool" by
~18%, on the strength of PyMOL and Mol\* agreeing exactly at 132/274. Neither
was computing an assignment.** Both were reading `struct_conf` and
`struct_sheet_range` out of the deposited mmCIF — one depositor's opinion,
counted twice. Parsing those records directly reproduces 132 and 274 exactly.

PyMOL's *own* computed assignment is a third answer:

```
1UBQ as loaded (deposited header):   ss H = 132   ss S = 274
after cmd.dss() (PyMOL computes):    ss H = 135   ss S = 266
```

Measured against real DSSP (`mkdssp` 4.6.1) rather than against the header, in
atoms on 1UBQ:

| | `ss H` | `ss S` |
|---|---|---|
| deposited header (was labelled "PyMOL" and "Mol\*") | 132 | 274 |
| PyMOL 3.1.0, computed | 135 | 266 |
| DSSP, α-helix and extended strand only | 98 | 203 |
| DSSP, all helix classes / strand + bridge | 148 | **217** |
| protean (P-SEA) | 89 | **217** |

**Strand was never 18% short.** protean assigns exactly as much strand as DSSP
— 26 residues, 217 atoms. It does not put all of it in the same place: the two
agree on 20 of those 26 residues, so the equal totals are partly coincidence
and this should not be quoted as "protean's strand is correct".

**The helix gap is 3-10 helix.** P-SEA has a class for α and one for β and
nothing else. Against DSSP's α-helix alone protean is one residue short (89
against 98); the remaining 50 atoms are four short 3-10 segments P-SEA cannot
express at all.

Per residue, over 76 amino acids:

| | agreement |
|---|---|
| protean vs the deposited header | 82% |
| **DSSP vs the deposited header** | **82%** |
| protean vs DSSP | 75% |

DSSP scores against the depositor exactly what protean scores against the
depositor. An ~18% spread is what two accepted assignments of this structure
cost each other, not evidence of a protean defect.

```
DSSP    -EEEEEETTS-EEEEE--TTSBHHHHHHHHHHHH---GGGEEEEETTEEPPTTSBTGGGTPPTT-EEEEEE--S--
protean -SSSSS----SSSSSSS-----HHHHHHHHHHH-------SSSS-------------------SSSSSSSSSS---
deposit SSSSSSS--SSSSSSSS-----HHHHHHHHHHHH-----SSSSSS--SSS-----HHHH----SSSSSSSSS----
```

All of this is asserted in `tests/test_secondary_structure_reference.py`,
including the header numbers, so the correction cannot quietly revert. DSSP is
**not** a dependency — it is a scoring tool, the way PyMOL is, behind
`PROTEAN_DSSP=1`, and CI does not install it.

**What is left to decide is a semantics question, not an algorithm one:**
should `ss H` mean α-helix, or every helix class? P-SEA can only answer the
first, and read that way protean is 89 against DSSP's 98.

Measured on one structure so far. 1L2Y below is the obvious second, because it
is largely 3-10 helix and so sits directly on P-SEA's blind spot.

**A from-scratch Kabsch-Sander implementation was tried and abandoned — and it
was working.** About 250 lines: amide hydrogens placed from the preceding
carbonyl, the published energy term, n-turns, bridges and ladders. It was
measured against the deposited header, judged to have missed, and deleted
without being committed. Against real DSSP it did not miss:

| on 1UBQ | `ss H` | `ss S` |
|---|---|---|
| P-SEA (current) | 89 | 217 |
| **the attempt** | **148** | **217** |
| **DSSP (all helix / strand + bridge)** | **148** | **217** |
| the deposited header it was scored against | 132 | 274 |

Both numbers exact, on both classes. The verdict recorded at the time — "helix
improved; strand did not move at all" — was reading a pass as a failure: strand
did not move because it was already where DSSP puts it, and 148 was not
overshooting 132, it was landing on DSSP.

The code is gone (never committed; only this entry survives), so a second
attempt starts from scratch but on much better footing. Two defects were found
and recorded then, and **both should now be re-verified rather than trusted** —
they were diagnosed while chasing the wrong target:

- the residue index space included waters, so "residue i+4" could be a solvent
  molecule rather than the fourth residue along — and every turn and bridge
  rule is expressed in exactly that adjacency. Plausible on its face and worth
  checking first;
- "only 54 backbone hydrogen bonds where DSSP finds 60-70" — the 60-70 was
  never measured against `mkdssp`, and the assignment it supposedly starved
  came out matching DSSP exactly, so this one is suspect.

Closing this did not mean taking `mkdssp` as a real dependency. `mkdssp` is not
in homebrew-core (it is the `brewsci/bio` tap, or conda-forge) and has no
wheel, so requiring it fails the same test decision 8 applied to APBS: an
optional binary cannot be the only way to get an answer. The one
pip-installable pure-Python option, `pydssp`, declares **torch**.

The route taken was the second one: re-port DSSP in-tree, validated per rule
against `mkdssp` as a scoring tool. That is
`src/protean_mcp/analysis/secondary_structure.py`, and the result is at the top
of this item. The second attempt succeeded where the first was thought to have
failed, for the reason recorded above — the first one had not failed.

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
