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

**`alt` — decided and implemented 2026-08-13: load every conformer.** Planned
in [docs/alternate-conformers.md](alternate-conformers.md), which is corrected
in place against what building it found.

```
5FJI   15929 atoms, matching the viewer exactly (was 15712, +217 explained)
       alt A     ->   206    the labelled atoms, as PyMOL means it
       alt ''+A  -> 15712    the conformer state — what analysis used before
```

Analysis resolves one conformer state **per site** — each atom keeps its own
highest-occupancy alternate — and says which letters it used. Per site, not
per structure: a lone `B`-labelled ion with no `A` counterpart would otherwise
be deleted from the geometry without a word. 5FJI resolves to `A+B`, not to
"conformer A". Bonds joining one conformer to another are dropped — templates
match by atom name and wired 16 of them on 1AKE. One caveat found while
building and recorded rather than papered over: `extend 1` stays inside a
conformer, but `extend 2` can cross through a shared atom, because the file
genuinely bonds the backbone N to both alternate CAs.

The original decision note: The obvious
implementation is wrong: conformer states *overlap* (13 of 32 residues with
alternates on 5FJI also carry shared atoms), so altloc cannot go into the
residue key the way `sym_id` did — it would split one residue into three.
Analysis resolves a conformer state instead, and topology must be derived
after that filter, since template matching otherwise bonds conformer A to
conformer B (16 such bonds on 1AKE).

The original entry, kept because the tradeoff it names is the one that was
decided:

**`alt` is still refused, but now by choice rather than impossibility.**
`get_structure(altloc="all")` exists, so every conformer *can* be loaded — and
doing so would make the viewer and analysis atom counts agree exactly, which is
the other half of item 2. The reason not to is that buried areas, potentials
and contact counts would then be computed over atoms sitting on top of each
other. The refusal now names that tradeoff instead of claiming it cannot be
done, so it can be argued with. **Wants a decision.**

### 11. The two engines disagree about what a bond is — explained, and kept

Found while implementing item 8. `resn HEM extend 1` on 4HHB returns the hemes
unchanged for protean and for PyMOL — a residue template gives iron no bond to
the protein — where Mol\* returns four atoms more, having modelled the Fe-NE2
coordination bond to the proximal histidine.

Neither is wrong. It is a real question about whether metal coordination is a
bond, and the two answers are both defensible.

**The difference is now explained rather than merely recorded, and asserted by
atom identity rather than by count.** "They differ by four" is consistent with
any four atoms, including a bond model that differs somewhere else and happens
to land on the same number. What the differential suite asserts is stronger:
the set Mol\* adds is *exactly* the set protean's own distance selection finds
coordinating the iron, and it drops nothing protean keeps.

```
resn HEM extend 1                                 -> 172 atoms (ours, PyMOL's)
                                                  -> 176 atoms (Mol*)
polymer within 2.6 of (resn HEM and elem Fe)      ->   4 atoms
```

Those four are the proximal histidine nitrogens — HIS87 in the alpha chains,
HIS92 in the beta — and they are precisely Mol\*'s surplus.

**Which is also the argument for keeping the template bond model.** The
capability is not missing, only spelled differently: `byres (polymer within 2.6
of (resn HEM and elem Fe))` gives the whole coordinating histidines, with the
cutoff visible and chosen by the caller rather than implied by someone's bond
model. That is the same idiom PLAN.md's phase 2 exit uses for the catalytic
zinc of 1CA2. A bond model that silently decides 2.6 Å is a bond and 2.7 Å is
not would make `extend` less predictable, not more useful.

Sensitive to the cutoff, and mutation-tested as such: at 3.5 Å the same
selection finds 12 atoms, and the suite fails.

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

## 12. Two refusal tests pass only because CI never runs them together — fixed

Found 2026-08-13 while running the whole suite with the browser gate on:

```
FAILED tests/test_server.py::test_rmsf_needs_a_trajectory_first
FAILED tests/test_server.py::test_rmsd_series_needs_a_trajectory_first
```

Both assert `ViewerError("No trajectory loaded")`, and both pass alone and in
the fast job. `tests/test_render_differential.py` calls `load_trajectory`,
sorts *before* `test_server.py`, and only runs when `PROTEAN_DIFFERENTIAL=1`
— so it leaves `server._trajectory` set and the two refusals stop refusing.

**CI cannot see it.** The fast job runs `pytest -q` with no gate, so the
polluting file skips; the browser job names its files explicitly, so
`test_server.py` never runs beside it. The two only meet in a local full run.
Confirmed identical on `main` at `efc42e0`, so this is not new.

**Fixed in two independent changes, because there were two problems.**

*The product bug.* `fetch_structure` cleared `_handles` and
`_conservation_scores` but not `_trajectory`, so loading a new structure left
`rmsf()` answering about the previous one — it reads the trajectory's own
`stack[0]`, never `_structure`, so nothing mismatched and nothing complained
while the viewer showed a different molecule. Loading a structure now ends the
session before it, `_keyframes` included, and says so in the reply. `superpose`
does the same, since it also replaces the loaded structure.

*The test isolation.* An autouse fixture in `conftest.py` restores the session
globals around every test, rather than reordering the files — ordering is not
something a test should have to rely on.

**The obvious way to verify the fixture does not work, and this is the
interesting part.** Disabling it leaves the whole suite passing, because the
product fix above *also* clears the pollution: `test_server.py` fetches a
structure long before it reaches the two refusals. Two independent fixes mask
one symptom. `tests/test_session_isolation.py` therefore checks the fixture
directly — one test leaks on purpose, the next asserts the leak was cleaned —
and that pair does fail when the fixture is disabled.

**Planned in [docs/session-state.md](session-state.md), which found a product
bug underneath the test one.** `fetch_structure` clears `_handles` and
`_conservation_scores` but not `_trajectory`, so loading a new structure leaves
`rmsf()` answering about the previous one — `rmsf` reads the trajectory's own
`stack[0]`, not `_structure`, so nothing mismatches and nothing complains while
the viewer shows a different molecule.

Worth noting as a class: `server.py` keeps `_structure`, `_trajectory`,
`_keyframes` and `_handles` as module globals, and only `_handles` is cleared
anywhere. Any test asserting "nothing is loaded" is at the mercy of whatever
ran first.

## Six defects in the alternate-conformer work — five fixed, one open

Found 2026-08-13 by a code review aimed at `chemrich/MCPymol#59` that resolved
`59` against this repo instead. Nobody asked for it and it was not briefed, so
every finding was reproduced before being believed. All six were real.

They are worth reading together rather than as six bugs. **Four of them are the
same shape: an index or a state resolved in one place and used in another.**
The altloc work necessarily created "two arrays that look alike and are not" in
several places at once, and every failure is silent — correct-looking numbers
attached to the wrong atoms. Wherever a resolved-state array and a full array
are both in scope, the question to ask is which one an index belongs to.
`analysis/contacts.py`'s `origin_index` is the existing answer.

### 13. `conservation()` registers handles indexed into the wrong array — fixed

PR 61. Scoring reads a resolved conformer state — 15712 atoms on 5FJI — and
computed its handle indices there, while `_register` and `_display` resolve
indices against `_require_structure()`, the full 15929. Every residue past the
first alternate site was off by however many alternate rows preceded it.

```
40 residues of 5FJI chain A: 171 of 317 atoms landed on a different residue
```

The scores were right, the atom count was right, and the handle drew a
neighbouring residue. Fixed with the `origin_index` pattern, both arrays taken
off one mask — deriving the state twice would be two enumerations assumed to
agree, which is the `sym_id`/`ASM` bug from item 7.

`conservation()` now also refuses if `_structure` changed while the alignment
was being fetched. Nothing locks the session globals, and that await runs for
minutes.

### 14. `superpose` never resolved a conformer state — fixed

PR 62. The one coordinate path PR 59 did not reach. biotite's anchors are one
entry per CA **atom** while the alignment columns they index are one per
**residue**, so a residue with an alternate backbone contributed two anchors to
one column and paired every later residue off by one — rmsd, sequence identity,
aligned count, outliers and the transform all wrong together.

**RMSD is nearly useless for detecting this**, which is the part worth keeping.
`superimpose_homologs` iteratively discards anchors that fit badly, so it throws
the mispaired ones away and reports a clean fit over the rest:

```
              rmsd       aligned  identity
correct       0.000000   9        1.0000
bug           0.000295   7        0.0000
```

Sequence identity is what discriminates. The reply now also states which
conformer each side was reduced to, labelled over the atoms actually
superposed rather than over the whole file.

### 15. `parse_structure` ranked conformers with no occupancy to rank them by — fixed

PR 63. It matched the main loader on `altloc="all"` but not on `extra_fields`,
so occupancy was absent, `conformer_state` fell back to `np.zeros`, every
alternate tied, and the winner was decided by sort order — the letter.

```
5FJI, same bytes:  load_structure 'A+B'  vs  parse_structure 'A'
```

So `interface("5fji", "A", "B")` standalone and `interface("A", "B")` after
`fetch_structure("5fji")` measured different conformer states while reporting
the same totals. The old comment claimed the two paths "must hold the same
atoms" — they did; that was never the failure.

### 16. Every re-sent structure reached the viewer as one blob — fixed

PR 64. biotite's mmCIF writer emits `_atom_site.label_alt_id` as `.` for every
row whatever the array holds. So `color_by_conservation(mode="gradient")`,
`rmsf` and trajectory display — everything that writes a scalar into the
B-factor column and re-sends — handed Mol\* 15929 atoms sitting on top of each
other with nothing to separate them: template bonds inferred across conformer
states, doubled spheres, no `alt` selection possible in the picture.
`viewer_atom_count` still agreed, which was the only cross-check those paths
had.

Fixed by writing the column, not by dropping conformers: sending one state
would make the viewer hold fewer atoms than the analysis, which is the
divergence decision 9 exists to prevent, and would break handles, which travel
as `atom.id` into an array numbered from the full structure.

A trajectory is an `AtomArrayStack`, and biotite writes one row per atom **per
model**, so the column is tiled to the rows actually written. The first version
was not, and killed every trajectory and `rmsf` render — invisible to the fast
suite, since both paths are gated.

### 17. `alt ''` was refused where it has an answer — fixed

PR 64. The guard fired for any `alt` term on a structure with no alternates,
but "atoms carrying no alternate label" is every atom there.
`select("chain A and alt ''")` failed on 4HHB while `select("chain A")`
succeeded, for a selection that means the same thing. It now applies only to
letters, and names the letter it refused.

### 18. `parse_structure` and `load_structure` still hold different molecules — open

**Not fixed.** Found while reviewing item 15, and unrelated to occupancy:
`parse_structure` reads the asymmetric unit while `load_structure` defaults to
the biological assembly.

```
1HHO: parse_structure 2396  vs  load_structure 4792   (2 copies)
1AKE: parse_structure 3816  vs  load_structure 1966   (assembly < ASU)
```

So `interface("1hho", "A", "B")` standalone and `interface("A", "B")` after a
fetch still describe different things, and `sym_id` is absent on the standalone
path, so `copy=N` refuses there for a structure the loaded path accepts.

**Wants a decision, because the obvious fix changes behaviour.** Making
`parse_structure` call `load_structure` would superpose the biological assembly
— a monomer rather than the deposited pair on 1AKE — which is a choice about
what `superpose` means, not a repair. The comment at
`analysis/superposition.py` states the divergence with these numbers rather
than claiming a parity that does not hold.

## Three findings from the going-public security pass, 2026-08-15

Found by attacking every path-taking tool (`docs/going-public.md` §3.1). One is
fixed here; the other two are open and want a decision.

### 19. `load_session` trusted the file it was given — fixed

A `.protean` file's embedded Mol\* state tree was handed straight to
`plugin.state.setSnapshot`, which applies it as given. A file naming a URL made
the browser fetch it and draw whatever came back, while `load_session` returned
a normal reply — demonstrated against a live viewer with an outbound GET to a
server standing in for the file's author. The format exists to be shared, so
"a session someone sent you" is its ordinary use, not an exotic threat.

**The first fix was wrong, and a review found it.** It checked strings sitting
under a `url` or `uri` key, chosen by grepping molstar for `PD.Url(`. Two live
bypasses: `create-volume-streaming-info` fetches from `serverUrl`, which is a
`PD.Text` and so absent from that grep, and a URL inside a list has no key at
all. **The lesson is about the shape, not the omission** — a guard built by
enumerating the attacker's options is only as complete as the enumeration, and
this one was refuted the same day it was written.

What replaced it checks two things, because neither alone is enough:

- **No string anywhere may name a location to fetch from** — a scheme, a
  `//host`, or a leading `/`, which the browser resolves against the viewer's
  own origin. Two exceptions, both measured from real sessions rather than
  assumed: this bridge's `/volumes/<handle>` route, and by *exact value* the
  three third-party URLs Mol\* serialises as its own custom-property defaults
  (PDBe validation, RCSB validation reports, RCSB symmetry). A blanket "no
  URLs" rule would have refused every real session; allowing the key would have
  let those same providers fetch from anywhere.
- **No transformer may appear that `save_session` never writes**, measured by
  building a scene with every state-adding tool. **That list was already too
  narrow when it landed.** Naming the transformers one by one meant a session
  saved from a `.pdb` file was refused by protean seconds after protean wrote
  it: a PDB reaches Mol\* through `trajectory-from-pdb` where an mmCIF uses
  `trajectory-from-mmcif`, and every structure in the census came from RCSB,
  which serves mmCIF. This is the half that does not
  depend on spotting a URL: `create-volume-streaming-info` fetches Mol\*'s own
  public default when the file names no URL at all. The `parse-*`,
  `volume-from-*` and `trajectory-from-*` decoder families are admitted by
  pattern, since which one appears depends on the format the caller loaded and
  none of those transforms fetches — a narrower claim than "their files do not
  fetch", since `model.js` fetches in the two custom-property transforms, which
  are allowlisted by name with their URLs pinned by value.

The match is anchored, so text that merely mentions a URL — an mmCIF header
cites `http://mmcif.pdb.org/...` — is not a reference. Decompression is bounded
too: 9 kB of gzip reaches 2 GB, and the file was read whole before anything
checked it.

**Two things worth keeping from the diagnosis.** The first two attempts
reported the attack as *refused* when nothing had been tested: Mol\* re-runs a
transform only when its `version` differs (`mol-state/state.js:473`), so
hand-edits to a snapshot are silently ignored unless the version is bumped. A
control — the same edit truncating the structure to 100 atoms — is what exposed
it: it also "passed", which no real refusal would have done. And two of the
tests written for the *second* fix could not fail either: one looped over the
set it was testing, so emptying the set passed it, and one relied on a skip
that the anchored match had already made unreachable. Both were caught by
mutating the guard, not by reading the tests.

### 20. Viewer and analysis are different molecules after `load_session` — fixed

**Both halves are restored now, or neither is.** `load_session` rebuilds the
analysis structure from the session's own embedded copy — no network, and no
question about which file, since it is the same bytes the viewer parsed.

**The viewer's atom count decides how to build it**, which is the part worth
keeping. The same deposited text assembles two ways, and *nothing in the file
records which was chosen*: 1HHO reads 4792 atoms as a biological assembly and
2396 as the asymmetric unit. A fixed default would have been silently wrong for
half of all sessions, so both readings are tried and the one matching the
viewer wins. Verified live on both: a session saved from `assembly="biological"`
restores 4792/4792, one saved from `assembly="asymmetric"` restores 2396/2396.

If neither reading matches, the analysis is left empty and the reply says so
with both numbers. A structure that disagrees with the picture is the failure
this item exists to remove, and keeping it with a caveat attached would be that
failure with a note on it.

**The original finding.** No attacker needed. `load_session` restores the viewer and never
touches the Python side, so every measurement afterwards describes whatever was
loaded before. Measured: viewer `atom_count` 100, `_structure` 660 atoms,
`_structure_identifier` still `'1ubq'`. Nothing reports a discrepancy.

This is item 2's shape again, and it is the guarantee protean exists to make.
The fix wants a decision: parse the session's embedded mmCIF back into
`_structure` (the data is right there), or clear `_structure` so analysis
refuses loudly rather than answering about the wrong molecule.

### 21. Every writing tool overwrites any path it is given — fixed

`save_session`, `snapshot`, `screenshot`, `movie(path=)` and
`electrostatics(path=)` write wherever they are pointed, creating parent
directories, with no overwrite check. Demonstrated: `save_session` replaced a
21-byte JSON file with 32 kB of gzip, and `electrostatics(path=…)` — an
*output* path despite reading like an input — wrote an OpenDX grid over a file
named `secret.key`. `record_trajectory`/`turntable` create arbitrary directory
trees for their frames.

The caller is the model, so the realistic route is a tool call the model was
talked into.

**Fixed with a rule narrower than "never overwrite".** Overwriting is half of
how these tools are used — capture a figure, adjust the scene, capture it again
over the same name — so a blanket refusal would have been reverted within a
week. What is never intended is a write that changes what a file *is*, and that
is the shape both demonstrations had. So an existing file is replaced only when
it already holds what that tool writes, and `overwrite=True` says do it anyway.

Confining writes to a cache directory was the other candidate and was not used:
"save this figure to ~/paper/figs" is a core workflow for a PyMOL replacement.

**What the flag does and does not buy.** Anything that can set the path can set
the flag, so this is not a barrier against a hostile call. It moves destruction
from something that happens invisibly to something a caller has to ask for by
name, in a call a reader can see.

### 22. Nothing says which vintage of protean you are talking to — fixed

**Found by being bitten, 2026-08-15.** `open_viewer` timed out, and twenty
minutes went into finding out why: the MCP server process had been started
three days earlier, so it was running none of that day's code, while serving
that day's viewer page off disk. New page, old server, no handshake in common.

The diagnosis had to be done from the outside, and the decisive evidence was a
hand-rolled WebSocket:

```
GET /ws with no token  ->  HTTP/1.1 101 Switching Protocols
```

That is a server predating the token, still accepting anything — **which is
the second half of the problem.** A long-lived MCP server keeps running the code it
started with, so a security fix is not in effect on a machine until someone
restarts the process, and nothing anywhere reports that. Rebuilding does not
help; the Python was loaded at start.

**What to build.** `open_viewer` should report the server's own version, and
the handshake should compare it against the page's. The mechanism is already
on the wire and unused: `bridge.ts` sends `{action: 'protean_ping', version:
PROTOCOL_VERSION}` and the server answers `{action: 'protean_pong', version:
PROTOCOL_VERSION}` — **neither side reads the other's number.** `__version__`
exists in `__init__.py` and is reported nowhere.

Three pieces, smallest first:

1. `open_viewer`'s reply states the server's `__version__`. One line, and it
   would have turned this into a glance.
2. The page compares the pong's version with its own and says so in the status
   pill when they differ — the pill already exists and already explains the
   other refusal cases.
3. A build stamp, since `__version__` will read `0.1.0.dev0` across dozens of
   incompatible builds. The viewer bundle's asset hash is one candidate; the
   server has no equivalent, so this needs a decision rather than a patch.

Worth doing before the flip, or soon after: a public repo means users running
whatever they installed weeks ago and reporting bugs against a version neither
of you can identify.

**Fixed 2026-08-17, and the plan above is wrong in a way worth keeping.**
Pieces 1 and 2 were checked before being built, and neither would have caught
the incident they were written for:

- `__version__` has read `0.1.0.dev0` for **every build there has ever been**,
  so the server from three days ago and the server from today report the same
  string. Piece 1 identifies a machine, never a moment.
- `PROTOCOL_VERSION` has been `1` since the first Phase 1 commit and did not
  move when the handshake gained a required token — *the change that caused
  this*. Piece 2 would have compared 1 against 1 and said nothing.

Ordered smallest-first, the list put the two ineffective pieces before the only
one that works. Piece 3 — called "a decision rather than a patch" — was the
whole item.

**The decision it needed: report whether the running process still matches the
code on disk.** `vintage.py` fingerprints every `.py` in the package by size
and mtime as this process imported them; `open_viewer` compares against disk
and names what changed. It catches exactly the failure, needs no version-bump
discipline — the discipline that failed — and never fires for an installed
wheel, because nothing rewrites the files under one.

Pieces 1 and 2 shipped anyway. They cost nothing, and the version is the right
answer for two *machines* comparing notes even though it is useless for two
moments. The page's check says in its own comment that it only catches a
deliberate break from here on.

### What the review is worth

Every fix above is mutation-tested, and **a review ran on each of the four
PRs and found something on every one** — six further findings, several in code
written specifically to fix the findings above. The most valuable were about
tests that could not fail: PR 61's compared residue sets while its fixture's
only alternate sat outside the conserved quartile, so the obvious wrong
implementation passed it.

### 35. A screenshot cannot tell a model the user changed the scene — open

Found by being caught by it, 2026-08-18, within a day of building the thing
that reports user actions.

Every tool reply carries what the person did in the viewer since the last call
— *"since your last call the user applied the skeleton view"* — except one.
`screenshot` replies with image content and has nowhere to put a sentence, so
`_carrying_user_actions` leaves the news queued for the next reply that can
take it. That was a deliberate choice and it is written down as one.

**It is deliberate in the wrong place.** A screenshot is the moment a model is
looking at pixels and about to draw conclusions from them, which is precisely
when "the user just applied a view" is load-bearing. It happened exactly that
way: a viewer was loaded, a screenshot taken, and the picture showed an
all-atom stick model where a cartoon was expected. The reasonable reading was a
regression in the default load, and two headless reproductions were run against
it before `capabilities()` — the next reply that *could* carry the news —
reported eight view clicks. Nothing was broken. The instrument had simply been
unable to say so.

**What to build.** MCP content can carry more than one part: an image and a
short text part in the same reply. If that works through the clients protean is
used from, the news rides along and this closes. If it does not, the fallback
is worse but still better than silence — hold the queue and let the *following*
reply say when the news is stale, or refuse to drain it into anything that
cannot show it.

Worth doing because the failure is silent and points at the model rather than
at the tool: what it looks like is protean rendering the wrong thing.

## Two findings from chasing a flaky test, 2026-08-16

### 23. A capture's budget did not depend on the capture — fixed

`test_a_real_journal_figure_reaches_disk` (png/tiff/jpeg) failed CI on PR 84
and passed when the same commit was re-run. All three failed the same way —
`Viewer timed out on 'snapshot' after 300.0s` — which is a timeout, not an
assertion: nothing had computed a wrong answer.

**It was chased rather than re-run, and the evidence contradicted two
explanations in a row — including the one this entry was first written
around.** Keeping both wrong turns, because each was plausible enough to have
been shipped as the answer.

*"A slow runner"* died first: the re-run that passed took 32:58 against the
failure's 34:56.

*"The declutter made every render more expensive"* died second, and it is the
one that had already been written down here as fact. It rested on a number
nobody had measured — the canvas is **722x311, 0.22 MP**, far too small for
viewport rendering to double a fifteen-minute suite. What is true is that the
job stepped at that merge: 15-17 minutes before, 31-34 after. What changed with
it is that **skips fell from 31 to 28**. Three tests stopped skipping and
started running, and the suite has only three runtime skip sites — two of them
the wheel build and ffmpeg, which a viewer-layout change cannot touch. The
third is `_figure_or_skip`, "this renderer cannot capture at 4323px", and it
guards exactly three tests: these ones. So the extra time is real work that had
been silently absent, not waste.

The first CI run with `-rs` tightened this. All **28** of today's skips are
accounted for by the six gates — 8 path tracing, 8 ffmpeg, 8 mkdssp, 2 APBS, 1
PyMOL, 1 ColabFold — and **none is `_figure_or_skip`**. Since those gates have
not changed, the three extra skips before the declutter were not gate skips,
and only one other runtime skip exists. **Still not proof**: the older run used
`-q` with no `-rs`, so its reasons were never recorded and cannot be recovered.
That hole is what `-rs` closes going forward.

The lesson that outlives the specific answer: **`pytest -q` prints a skip
count and no reasons, so a test can stop running and the job stays green.**
The suite was passing while its Phase 4 exit criterion never executed.

Measured under SwiftShader on the development machine, one capture per width:

```
 1200 px   0.6 MP     6.5 s   10.4 s/MP
 2000 px   1.7 MP    17.7 s   10.3
 3000 px   3.9 MP    42.9 s   11.1
 4323 px   8.1 MP   105.2 s   13.1     <- 183 mm at 600 dpi
 6000 px  15.5 MP   209.7 s   13.5
 8000 px  27.6 MP   467.3 s   16.9
```

Nearly linear in the pixel count. So the fixed 300 s was never a statement
about health: it was ample below 2000 px, a coin toss for the journal figure on
a CI runner, and **unreachable above about 5000 px on any renderer this slow,
including this one** — a size the tool accepts without complaint. The budget is
now 60 s per megapixel of the requested size with a 300 s floor, and scene
positioning keeps the old flat value under its own name.

**Why not a heartbeat, which would be the better answer.** Mol\*'s ordinary
image pass renders in one synchronous call — `MultiSamplePass.render`, with no
`runtime.update` between samples — so the page's main thread is blocked for the
whole capture and cannot send anything. Silence is what a healthy large capture
looks like. Only the illumination path reports progress, and CI does not take
it.

### 24. The browser job takes twice as long since the viewer was decluttered — open

Found while chasing the above, and it wants a decision rather than a patch.
Measured from the run history of 2026-08-15:

```
before declutter-the-viewer merged   15–17 min per browser job
after                                31–34 min
```

**Most of it is probably not waste.** Three tests began running at that merge
(item 23), and three journal-figure captures at CI speed account for the bulk
of the difference. Buying that time back means choosing not to run the Phase 4
exit criterion on every PR — a legitimate choice, but a coverage decision
rather than an efficiency one.

**Shrinking the window is the wrong lever**, and this is the measurement that
says so rather than an argument:

```
headless default   canvas 722x311   0.22 MP
--window-size=800,600     766x355   0.27 MP
--window-size=640,480     606x235   0.14 MP
```

Even the aggressive setting removes 0.08 MP per viewport render — a fraction of
a second each — while moving every pixel-fraction threshold in
`test_render_differential.py`, several of which are calibrated per renderer and
close to their limits. Setup dominates many of those tests anyway: browser
launch and structure load run 13–33 s apiece, against captures of 2–3 s.

The levers that cost no fidelity at all come first, and neither is about
pixels: **41 of 100 runs were post-merge pushes to `main` re-testing a tree the
PR had just verified** (~a quarter of all spend), and docs-only PRs could be
path-filtered. The dollars are small either way and go to zero if a public repo
gets free Actions minutes; what survives the flip is the 15→33 min feedback
loop, which is the thing actually worth buying back.

### 25. A capture's reply could be lost with its socket — fixed

Found by the failure in item 23 finally being legible. During a figure-sized
capture the page's main thread is blocked for tens of seconds, and in that
window the WebSocket can die: observed twice out of two runs of
`test_render_differential.py`, closing **62 s into a 68 s capture** with

```
close_code = ABNORMAL_CLOSURE (1006)
exception  = ClientConnectionResetError('Cannot write to closing transport')
```

1006 means no close frame — the connection died rather than either side
choosing to end it. No renderer crash appears in Chrome's own log, so the page
survives it.

**A confounder found afterwards, and worth knowing before trusting the
frequency.** Both reproductions ran while two leaked headless Chromes from
killed background commands had been burning CPU for eight hours — enough to
take the same suite from 12:46 to 44 minutes. CPU starvation is a plausible
cause of an abnormal close, so **"2/2" describes a contended machine, not a
clean one**, and the drop may be far rarer than that suggests. It does not
change the fix: a reply that was computed and then lost should be recoverable
whatever killed the socket. If anything it strengthens the link to CI, whose
runners are resource-constrained in the same way.

**The reply was then dropped in silence.** `bridge.ts` replied on the socket
captured when the request arrived, and `send` on a closed socket is a no-op, so
the answer went nowhere while the work had actually succeeded. The server, with
nothing failing a pending request, waited out the entire budget and reported a
stall — *"Viewer timed out on 'snapshot' after 300.0s"*, which is exactly what
CI printed on PR 84. **The timeout was never the bug; it was the only
instrument pointed at it.**

Three changes, and the order matters:

1. the page keeps a reply it cannot send and delivers it on the next
   authenticated socket;
2. the handshake declares what the page still owes (`inflight`), so a page that
   reconnects mid-render keeps its request alive while one that reloaded ends
   it at once;
3. a plain disconnect deliberately fails nothing. The first version of this
   work did fail on disconnect, which reads as obviously right and destroys a
   reply that is still coming — it turned a recoverable drop into a certain
   failure.

## One finding from building the style presets, 2026-08-17

### 26. `show()` on a handle moves the camera the second time, not the first — fixed

Found while making a view idempotent, and it belongs to `show()` rather than to
the presets: reproduced with plain `hide()`/`select()`/`show()` calls and no
preset anywhere.

Drawing the same handle three times in a row, screenshotting after each, gives:

```
show #1 vs #2   0.143831
show #2 vs #3   0.000000
```

The first draw keeps the framing the load preset chose. The second refits the
camera to what is actually on screen and then holds. Frames are otherwise
perfectly stable — three captures of an unchanged scene differ by exactly 0.0 —
so this is not a settling artefact caught mid-animation; the camera genuinely
lands somewhere else and stays.

Two consequences, both silent:

- **A figure captured after the first draw is framed for a scene that is no
  longer there.** Hide the load preset's representation, draw your own, capture:
  the camera is still fitted to what you hid.
- **Applying the same view twice gives two pictures**, which makes anything
  built on repeated `show()` — a view switcher, most obviously — non-idempotent.

`reset_view()` and `focus()` both pin it: with either called after the draw, all
three frames are identical. The style presets take the `reset_view()` route for
whole-scene views (docs/views.md §5.1), which fixes it *there* and leaves
`show()` itself as it is.

**Corrected 2026-08-18: it is intermittent, not deterministic.** The reading of
Mol\* this item asked for was done, and so was the measurement, and the
measurement is the part that changed the entry. Running the reproduction above
unchanged, fourteen times:

```
12 runs   show #1 vs #2   0.000000
 2 runs   show #1 vs #2   0.030043
```

So the numbers above were one observation of an event that fires roughly one
time in seven, written up as though it always happened. Fourteen runs is enough
to say "intermittent" and not enough to put a rate on it with any confidence,
which is why the two counts are given rather than a percentage. Everything else in the entry
holds — caught in the act, the camera genuinely moves and then stays:

```
after show #1:  camera z = 80.952   coverage 0.0050
after show #2:  camera z = 36.061   coverage 0.0251   <- refit, closer
after show #3:  camera z = 36.061   coverage 0.0251   <- and holds
```

**Where it comes from.** `commitScene` in `canvas3d.js` snapshots the visible
bounding sphere *before* committing, then requests a camera reset if
`reprCount === 0 || shouldResetCamera()`. `shouldResetCamera` asks whether any
visible renderable has moved outside that old sphere and whether the camera
sphere overlaps anything. A commit has a 250 ms budget and returns early when it
cannot finish, so **which commit boundary a `hide` and a `show` land on decides
whether the test is taken against the old scene or the new one** — and that is
the race.

**Which end is wrong, decided on the evidence:** the defect is that `show()` is
neither. A caller cannot rely on the camera moving or on it staying, which is
worse than either. `p.camera.manualReset` is Mol\*'s own lever — it gates every
auto-reset in `commitScene`, defaults to `false`, and **protean has never set
it**. Turning it on after the initial load would make `show()` deterministic and
leave the camera to `focus()` and `reset_view()`, which is where a caller can
see it move. The cost is that protean would then owe an explicit fit at load,
where it currently gets one free.

**Possibly the same thing as item 32**, which is also an intermittent framing
difference at a similar rate. Not established: 32's control renders identical
geometry, and whether its camera moves has not been measured. Doing that
measurement is the cheapest way to find out, and would either merge the two
items or separate them properly.

**Fixed 2026-08-18 by taking the route above.** `load_structure` sets
`manualReset` on the canvas after `clear()`, which gates off every automatic
request in `commitScene`, and then asks for the one fit a load actually wants —
`managers.camera.reset()` sets the flag directly, so an explicit reset still
works. `focus()` and `reset_view()` are unaffected, which leaves the camera
moving only where a caller asked for it.

Both halves are load-bearing and each has its own test: remove `manualReset`
and the mechanism test fails; remove the explicit fit and the load frames
nothing, which the second test catches. The prop is set after `clear()` because
clearing rebuilds canvas3d state and a prop set before it does not survive.

**A code review found four gaps in the first version of this fix**, and they
are the reason the prop is set where it is:

- `manualReset` gates more than the auto-fit. `commitScene` ends with
  `if (!p.camera.manualReset) camera.setState({ radiusMax: getSceneRadius() })`,
  and the trackball's min/max distances are recomputed only inside
  `resolveCameraReset`. So the camera's *limits* stopped tracking the scene, and
  a volume spanning more than the protein would be clipped away by a slab drawn
  from the protein's radius. The dispatcher now maintains `radiusMax` after
  every draw, deliberately without moving the camera: framing belongs to the
  caller, bounds belong to the scene.
- Setting it inside `load_structure` left every path that draws *without*
  loading one still auto-fitting — a volume into an empty viewer, or anything
  after `clear_viewer`. It is set once at start-up instead.
- The first version's comment justified that placement by claiming a prop set
  before `plugin.clear()` would not survive. **That was asserted, not checked,
  and it is false**: `clear()` takes `resetViewportSettings` and protean passes
  nothing, so canvas3d props are untouched. The same wrong reason was written
  into this entry as fact.
- `load_session` re-applies a snapshot's canvas3d props, where `manualReset`
  defaults to false — so restoring any session written before this existed
  would hand automatic fitting back for the viewer's life. Re-asserted after
  `setSnapshot`.

**One of those is fixed but untested, and that is worth stating.** The
`radiusMax` maintenance has no test, because three attempts to build a scene
that actually grows — a large spacefill, a 300 A volume cell, a volume at an
offset — each failed to move `boundingSphere.radius` at all, and a test whose
own guard says "the scene did not grow" proves nothing about what happens when
it does. The mechanism is read straight out of `canvas3d.js` and the fix is
four lines, but nothing in the suite would catch its removal. Finding a scene
that grows is the next piece of work here.

**The tests that do exist assert the property, not the picture**, and the rate
is why: at one
in seven, a repeated-`show()` comparison would pass six times out of seven
against a regression — a test that mostly cannot fail. Verified with the
mechanism engaged in a live viewer (`manualReset: true` after load *and* after
a draw) and fourteen consecutive clean runs of the reproduction, where 0 in 14
alone would have been ~11% likely by luck.

### 27. `load_structure` never waited for the camera it moved — fixed

Found by CI disagreeing with this machine, which is the only way it could have
been found: the gap is invisible on a fast renderer.

A differential test loaded the same coordinates twice and compared cartoon
frames as its control — the two must be identical, because nothing that cartoon
draws depends on what changed between them. Locally the control read
**0.000125**. On CI it read **0.007983** and failed the test.

The cause is one missing wait. `applyPreset` frames the newly loaded molecule,
and Mol\* tweens that move over ~250 ms exactly like any other camera move.
`load_structure` is marked `render: true`, which settles the *geometry* — it
waits for the commit queue to drain — and that says nothing at all about the
camera. `focus`, `orient` and `reset_view` have called `settleCamera` since they
were written; this one never did.

So **a capture taken straight after `fetch_structure()` could be framed
mid-tween**, and the slower the renderer the wider the window. The symptom is a
figure framed slightly wrong, which is exactly the class of defect that survives
a green test suite: nothing errors, the picture looks plausible, and only two
captures of the same thing side by side show it.

`load_structure` now settles the camera too. The unit test asserts it by frame
count — with a drained commit queue the render pump alone spends six frames, so
anything past twenty can only have come from waiting on a camera that was still
moving — and removing the wait fails that test rather than hanging.

**The threshold was the second lesson.** The control's ceiling was an absolute
number measured on one machine, and an absolute number is a claim about a
renderer rather than about the thing being tested. It is now a ratio against the
signal it is controlling for, which is what the test was always trying to say.

## Four findings a code review left open, 2026-08-17

Raised against the style presets (PR 93) and deliberately not fixed there: each
belongs to a tool the presets merely exposed, so fixing it at the preset layer
would close the door for six recipes and leave it open for every direct caller.

### 28. The handle table and the viewer disagree about what is drawn — open

`_handles` is protean's mirror of the viewer's `components` map, and the viewer
is the authority. They drift:

- `clear_viewer()` empties the viewer without calling `_discard_session_state`,
  so every handle survives a cleared scene.
- `hide(name)` leaves the handle registered, which is correct — the selection
  still exists — but means the table cannot answer "is this on screen?".

`_styleable()` asks exactly that question, and answers it from the table. After
`preset("putty")` followed by `unhide("auto")` both components are drawn, and
the next styling preset restyles only `auto_view` while reporting success over
the whole scene. The fix is to ask the viewer which components exist rather
than to infer it; `remove()` dropping its handle (done in PR 93) closed the
worst case but not the question.

### 29. A representation that cannot draw a handle still reports success — open

`preset("putty", handle="lig")` where `lig` is a ligand: the handle has atoms,
so the guard passes; `putty` is a polymer-trace representation and draws no
geometry; `show` returns the *component's* atom count rather than the built
representation's, so the reply is a full step list and an unchanged screen.

This is the silent success the project exists to catch, and it is `show()`'s,
not the preset's — `show(representation="putty", handle="lig")` does the same
thing on its own. The fix is for the viewer to report what the representation
actually built, which would let every caller notice, and is the same number
`isApplicable` already knows.

### 30. shading, material and opacity succeed on a hidden component — open

They change state nobody can see and report success. `_styleable()` in the
presets is a workaround for exactly this, and only for the preset path: a
caller doing `hide('auto'); shading(style='cel', name='auto')` by hand still
gets a cheerful reply and no change. `dispatch.ts` already reads
`isHiddenComponent` for hide/unhide, so the check is available; the question is
whether these three should refuse or warn.

### 31. `show()` moves the camera on the second draw of a handle, not the first — open

Recorded as item 26 while the presets were being built and still open. Item 27
fixed the same class of defect for `load_structure`; this one is the remaining
half, and `_frame_the_scene()` is a preset-level workaround for it.

### 32. A B-factor differential test fails about twice in twelve runs — open

Found by a full local suite run on 2026-08-17, and **not explained**. Recorded
now rather than when it is understood, because two confident explanations have
already been wrong about it and a third guess is worth less than the numbers.

`test_putty_width_follows_the_bfactor_it_claims` loads the same coordinates
twice, once with the deposited B-factors and once with every B-factor flattened
to their mean, and compares putty against a cartoon control that must read zero.
Ordinarily the control reads **0.000125**. On the failing run it read
**0.279658** — 28% of the frame between two renders of identical geometry — and
the putty number rose with it, from 0.020 to 0.120.

**Both numbers moving together is the shape of a differently *framed* scene
rather than a differently *drawn* one.** A camera that landed somewhere else
makes every per-pixel comparison larger at once.

What has been ruled out, each by measurement:

| hypothesis | test | result |
|---|---|---|
| CPU contention | 12 spinners, load average 124 | passed |
| the test alone | isolation, eight runs | passed |
| within-file pollution | the whole differential file | 108 passed |
| a concurrent browser suite | second suite running alongside | passed |

Two failures in about twelve observations. Both happened while something else
was running, but neither deliberate reproduction — CPU load or a competing
Chrome — brought it back.

**CI cannot see this.** The browser job has passed every run, and it skips eight
tests this machine runs for want of ffmpeg, so the suite it executes is not the
suite that failed. That qualifies backlog item 12's claim that CI now reproduces
cross-file pollution: it reproduces the kinds that do not depend on optional
binaries.

The leading remaining suspect is `settleCamera` giving up. It waits
`CAMERA_TIMEOUT_MS = 3000` for the camera to come to rest and then returns
regardless, so a camera still travelling was indistinguishable from one that had
arrived. **That is now reported** — `load_structure` replies with
`camera_settled: false` and `fetch_structure` says so in words — which does not
fix anything but means the next occurrence can be read rather than guessed at.
The test's failure message now also carries each frame's size and coverage.

Deliberately not done: raising the budget. A bigger number would make the
symptom rarer and the diagnosis no easier, and the budget was never measured
against anything in the first place.

## Two findings from trying to use AlphaFold support, 2026-08-17

Both found while measuring docs/views.md §5.4, which needs a predicted model to
say anything about pLDDT. Together they mean **`fetch_structure(source=
"alphafold")` does not work at all** — one of the three sources that tool
documents.

### 33. The AlphaFold URL is pinned to a retired version — fixed

`fetch.py` builds `https://alphafold.ebi.ac.uk/files/AF-{accession}-F1-model_v4.cif`.
AlphaFold DB is now on **v6** and v4 is gone:

```
P69905  v4: 404   v6: 200
P0DTC2  v4: 404   v6: 404
Q8N158  v4: 404   v6: 200
```

Not one accession's problem — the database version bumped and every AlphaFold
fetch fails with *"Not found upstream"*, which reads as "no such protein" rather
than "protean is asking for a file that no longer exists". P0DTC2 fails on both,
so some accessions are genuinely absent and the two causes are indistinguishable
from the message.

**Bumping the number is the wrong fix**, or at least not the whole one: it would
work until the next release and then fail the same way, silently, at whatever
distance in time. The database publishes the answer —
`https://alphafold.ebi.ac.uk/api/prediction/{accession}` returns `cifUrl` with
the current version and a `latestVersion` field — so the version is something to
*ask for* rather than to hardcode. One extra request, and it cannot go stale.

Worth a test that the URL protean builds is one the database serves, which no
existing test covers: the fetch tests use local files and a mocked upstream, so
the whole suite passes with the tool completely broken.

**Fixed 2026-08-22, and this entry's own diagnosis was wrong in a way that
matters.** The URL is now asked for — `cifUrl` from the API — rather than
built, which was the recommendation above. But "P0DTC2 fails on both, so some
accessions are genuinely absent" is not what is happening. The API answers 200
for it and points at

    https://alphafold.ebi.ac.uk/files/AF-0000000365840314-model_v1.cif

— an internal numeric id, **no `-F1` fragment**, and version 1 while its
neighbours are on 6. It is a real 2.1 MB file. **The template was wrong in
shape, not only in version**, so no amount of bumping would ever have reached
it, and the absence this entry inferred was a symptom of the bug rather than a
fact about the database.

The live test the last paragraph asked for exists now, behind
`PROTEAN_NETWORK=1`. It is the only thing in the suite that can catch this
class of failure, because everything else mocks the upstream.

### 34. A predicted model fails the analysis parser by default — fixed

An AlphaFold mmCIF loaded from disk:

```
Loaded af-P69905 ... [analysis unavailable: Could not parse mmcif coordinates:
File has no 'pdbx_struct_assembly_gen' category]
```

The viewer draws it; selections and every analysis tool are unavailable. Loading
the same file with `assembly="asymmetric"` works and reports 1077 atoms in both
copies, so the failure is the **default**, not the parser.

`assembly="biological"` is the right default for a deposited structure and
meaningless for a predicted one, which has no crystallographic assembly to
build. The fix is presumably to fall back to the deposited coordinates when
there is no assembly category rather than to fail the analysis half — but that
touches the same question as backlog 18, which is already open about
`parse_structure` and `load_structure` disagreeing on what "the structure"
means. Worth settling together.

The reply does say what happened, which is the difference between this and a
silent failure. It says it in a note a caller has to read, at the end of a line
about a successful load.

**Fixed 2026-08-22.** `load_structure` now checks for the
`pdbx_struct_assembly_gen` category and, when it is absent, loads the deposited
coordinates and says so — the same shape as the too-many-copies fallback
directly above it in the same function. Checked by reading the category rather
than by catching the exception, because that exception's message is the only
thing separating it from any other parse failure, and matching on a library's
prose is a dependency on wording nobody promised to keep.

Verified on AF-P69905: 1077 atoms under either assembly, where the default
previously failed outright. **Backlog 18 is untouched** — this does not settle
what "the structure" means when the two halves disagree, it stops one specific
file type from losing its analysis half.

## Three findings from building the view catalogue, 2026-08-19 to 21

### 36. A structure will not load into a hidden tab — open

`open_viewer` reports "rendering runs on the background-tab pump", and captures
do work that way: the raf-pump exists precisely because browsers stop
`requestAnimationFrame` in a background tab and Mol\* needs it to build
representations.

A **load** does not. Observed twice in a row, with the viewer tab hidden:

```
Viewer timed out on 'load_structure' after 60s. The viewer tab is hidden:
browsers pause requestAnimationFrame in background tabs, which Mol* needs to
build representations. Bring the protean tab to the front and retry.
```

Two minutes of wall clock for a structure that loads in under a second with the
tab in front. The error message diagnoses it correctly and says what to do,
which is why this is a backlog entry rather than a bug — but the pump's claim is
broader than what it delivers, and the reply that makes the claim is
`open_viewer`'s, at the start of a session, where it will be read once and
believed for the rest.

Unknown: whether the pump could carry a load at all, or whether representation
building needs something a synthetic rAF cannot provide. Worth measuring before
deciding, because "bring the tab forward" is a fine answer if the alternative is
a pump that lies less often but still lies.

### 39. A port test asserted a fact about the machine — fixed

CI, 2026-08-22, on PR 110: `assert 48519 == (48517 + 1)` in
`test_port_scan_increments_on_conflict`. The bridge had scanned correctly; the
test was wrong.

It bound a blocker on `base` and asserted the bridge landed on exactly
`base + 1` — but nothing reserves `base + 1`. `free_port()` asks the OS for a
port and closes it again, and this job runs the **whole** suite, where a dozen
servers are taking ports at the same moment. The assertion was about the
machine rather than about the bridge.

Now binds two blockers and asserts the bridge got past both, which is the
behaviour the test is named for. Worth noting that it very likely became more
likely to fail when the fast tests were folded into this job — more concurrent
port users, same assumption.

### 37. One fallback colour, written twice, in two languages — open

`_ELEMENT_PALETTE["X"] = "#b0a8b9"` in `server.py` names the colour an element
palette paints anything it was not given. `dispatch.ts` writes the same value
again as `table.X ?? 0xb0a8b9`, because the viewer has to have an answer when a
palette omits `X` entirely.

Editing one leaves the other silently authoritative for exactly the case nobody
tests: a caller-supplied palette with no `X` in it. The two agree today because
they were written in the same hour.

The same shape as the `EFFECT_PARAMS` table, and the reason that one has a
comment about drift. Fixing it means either sending the fallback with every
palette — so the viewer never needs a default of its own — or accepting the
duplication and pinning it with a test that reads both files. The first is
cleaner and is what the next person should do.

### 38. `_draw_the_ligands` and `_preset_sidechains` were one recipe twice — fixed

Both did: require the structure, evaluate a selection, `np.flatnonzero`,
`_register`, register the element palette, `show` as ball-and-stick in it.
Only the selection, the handle and the thickness differed.

Flagged in review on 2026-08-19 and left alone at the time, because that branch
was already fifteen fixes deep and a refactor on top would have made the diff
harder to read than the bugs it removed. Folded into `_element_coloured` on
2026-08-21 when a fourth and fifth caller arrived — and the fourth was where it
was noticed, because it asked for the palette by name without registering it
first and was refused.

Recorded because the shape recurs: the moment to fold duplication is when the
third caller appears, and the cost of waiting is that the third caller is
usually the one that gets it wrong.
