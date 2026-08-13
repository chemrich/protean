# Alternate conformers — making `alt` selectable without lying about geometry

Planned 2026-08-13, not started. Closes the `alt` decision left open in
[backlog](backlog.md) item 8, which said the refusal stood "by choice rather
than impossibility" and wanted a decision. The decision is: **load every
conformer**.

**Read §2 before §4.** The obvious implementation — and the one this plan was
originally going to propose — is wrong for a reason that only shows up when
you look at which atoms actually carry an altloc label.

---

> ## Done, 2026-08-13 — and a code review found what the plan and the build both missed
>
> Implemented in PR 59 and recorded as **PLAN.md decision 16**. Kept as the
> plan it was, with two corrections marked.
>
> **§4.3's "highest occupancy per site" was written correctly and built
> wrongly.** The implementation chose one letter for the *whole structure*,
> which looks equivalent and is not: an atom labelled `B` with no `A`
> counterpart — a routine way to model a partially occupied ion or ligand —
> was **deleted from the geometry entirely**, silently, contributing no buried
> area and appearing in no handle. 5FJI has 11 atoms labelled `C` that a
> global `A` discards the same way. Now resolved per site, as §4.3 said.
>
> A consequence worth reading: 5FJI does not resolve to "conformer A". Its
> sites disagree, so the answer is **`A+B`**, and any reply naming a single
> letter for that structure was describing something that was not measured.
>
> **§4.3's list of affected tools was right and only `interface` got the
> filter.** `electrostatics` computed potentials over every conformer at once —
> and because biotite's PDB writer blanks the altLoc column, pdb2pqr received
> two rows for one atom with no way to tell them apart and silently kept
> whichever came first. `conservation` scored duplicated residues. Both now
> resolve a state through the shared helper, which had been written and then
> not used: `contacts.py` open-coded it instead, which is exactly how the
> other tools came to be missed.
>
> **§4.4's promise is narrower than it reads.** Dropping direct A–B bonds
> keeps `extend 1`, `bound_to` and `neighbor` inside one state. It cannot keep
> a longer walk there: the file bonds the backbone N to *both* alternate CAs,
> so `extend 2` crosses through the shared atom. Removing one of those bonds
> would be inventing topology rather than filtering it.
>
> Still true and worth keeping: the counts match the viewer exactly, handle
> transport is unaffected because every conformer row has its own
> `atom_site.id`, `alt` is literal, and the empty-string spelling was the trap
> §4.2 predicted — it returned nothing until the quotes were stripped.

---

## 1. What an altloc is, and what protean does now

Some atoms are resolved in more than one position: a side chain caught between
two rotamers, each row tagged `A`/`B` with an occupancy. The physical fact that
governs everything below is that **the states never coexist** — each molecule
in the crystal is in one of them. The file describes a population.

biotite keeps one conformer per site (`altloc="first"`), so no label survives
and `alt` is refused. The viewer draws all of them, so the two halves differ by
a measured, explained amount (item 2).

```
5FJI   analysis 15712   viewer 15929   (+217, stated in the load message)
1AKE   analysis  3804   viewer  3816   (+12)
```

## 2. The correction that shapes the design

The plan this replaces proposed *"load everything and put altloc into residue
identity"*, by analogy with decision 9, which put `sym_id` into the residue key
so two symmetry copies could not merge.

**The analogy is false, and measurement is what shows it.** Symmetry copies
partition the atoms; conformer states **overlap**. Only the atoms that actually
differ carry a letter — the rest of the residue is shared and tagged `.`:

```
5FJI: 32 residues contain alternates
        19 of them are entirely alternate
        13 of them ALSO contain '.' atoms   <- the states share atoms

  A|320: 9 atoms — 3 shared ('.'), plus A and B variants of the other 6
```

So state A is `{'.'} ∪ {'A'}` and state B is `{'.'} ∪ {'B'}`. Appending altloc
to the residue key would split `A|320` into **three** entries — the shared
atoms, the A atoms, the B atoms — each holding a fraction of a residue. That
is worse than the merging it was meant to prevent: instead of one residue with
double the area, three residues with none of them real.

**Residue identity therefore does not change.** What changes is that analysis
picks a *state* before computing, and within one state each residue appears
once with all of its atoms.

## 3. What PyMOL does, measured

Relevant because "migration is free" is a project goal, and because it sets the
expectation a caller arrives with.

```
1AKE   3816 atoms   alt ''=3792   alt A=12    alt B=12               alt ''+A =  3804
5FJI  15937 atoms   alt ''=15514  alt A=206   alt B=206   alt C=11   alt ''+A = 15720
```

PyMOL loads everything and exposes `alt`, with `alt ''` meaning "no alternate".
The idiom users know is `alt ''+A`.

**PyMOL does not protect the caller.** `get_area` over all conformers against
the filtered set:

```
1AKE   21668.0  vs  21698.3   (-0.14%)
5FJI   54917.3  vs  54846.6   (+0.13%)
```

It computes surface area over mutually exclusive states and says nothing. That
is the part not to copy: a human has absorbed `alt ''+A` from a decade of
forum posts, and a model calling `interface()` has not.

*(Unrelated loose end: PyMOL reports 8 more atoms than both biotite and Mol\*
on 5FJI, in the filtered counts too, so it is not an altloc difference. Not
part of this work; worth its own look.)*

## 4. Design

### 4.1 Load every conformer

`get_structure(..., altloc="all")`, keeping the `altloc_id` annotation biotite
only provides in that mode. Verified: this gives **15929 atoms on 5FJI**,
exactly the viewer's count, so the item-2 surplus goes to zero and the load
message stops needing to explain it.

**Handle transport is unaffected** — every conformer row carries its own
`atom_site.id` (3816/3816 and 15929/15929 distinct), so the atom-id predicate
a handle travels as stays exact. This is the item-7 hazard and it does not
apply here; assert it anyway, because that is what item 7 taught.

### 4.2 `alt` becomes a leaf predicate, and it is literal

`alt A` on the `altloc_id` annotation. Refused with a named reason on a
structure that has no alternates, the way `sym` is refused on an asymmetric
unit.

**Decided: `alt A` means the atoms labelled `A`, exactly as PyMOL means it —
not the conformer state.** The distinction matters, and the file shows why.
SER320 of chain A in 5FJI is stored as:

```
  N    .     1.00     <- shared (backbone)
  CA   A     0.50
  CA   B     0.50
  C    .     1.00     <- shared
  O    .     1.00     <- shared
  CB   A     0.50
  CB   B     0.50
  OG   A     0.50
  OG   B     0.50
```

Only the atoms that genuinely differ carry a letter. So `alt A` is three atoms
— a side chain with no backbone, neither a residue nor a conformer. The
conformer *state* is `alt ''+A`, six atoms, a complete serine. Structure-wide
the same: `alt A` is 206 atoms where state A is **15712, which is exactly what
protean analyses today**.

The alternative — making `alt A` mean the state — was rejected because it
destroys the one property the labels obviously have. Under it, `alt A` and
`alt B` would both contain the shared backbone, so `alt A and alt B` returns
N/C/O rather than nothing, and two labels that read as mutually exclusive
overlap. Literal keeps them disjoint and keeps PyMOL scripts working.

The useful case is not lost, it simply lives elsewhere: "analyse conformer A"
is §4.3's state filter, inside the tools, which is where a caller wants it
applied anyway. The selection keyword is for display and inspection.

**"No alternate" is spelled `alt ''` or `alt .`.** PyMOL writes `''`; biotite
stores `.`; both are accepted, because a caller arriving from either will
reach for the one they know. So displaying a whole conformer is `alt ''+A`,
and the two spellings must give identical sets — worth an assertion, since a
value list containing an empty string is exactly the sort of thing a parser
quietly drops.

### 4.3 Analysis resolves a conformer state first

Every tool that computes geometry takes the atoms of one state — `'.'` plus one
letter — before doing anything, and **says which state in its reply**.

Default: **highest occupancy per site**, which is biotite's `altloc="occupancy"`
rather than `"first"`. They are not the same: 70 atoms differ on 5FJI, and the
occupancy mode has the higher mean occupancy, so it is choosing the dominant
conformer where `"first"` was choosing whichever came first in the file. This
**changes some existing answers slightly** and that is the point — the current
ones are arbitrary.

Affected: `interface`, `electrostatics`, `superpose`, `rmsf`/`rmsd_series`,
`conservation`, and anything else reading coordinates. The filter belongs in
one helper the tools call, not copied into each.

### 4.4 Topology must be derived on the filtered state, not before it

Found while planning, and the sharpest reason the filter cannot be optional.
`connect_via_residue_names` matches by atom name, and cannot tell conformers
apart, so it **bonds them to each other**:

```
1AKE: 3513 bonds with all conformers loaded, against 3484 with one
      16 of them join conformer A directly to conformer B
      e.g. A/ARG167/CD(A) <-> A/ARG167/NE(B)
      leaving CD(A) with 3 bonds where it should have 2
```

`extend`, `bymolecule`, `bound_to` and `neighbor` would walk across states
through a path no molecule has. Deriving topology *after* the state filter
removes the possibility rather than correcting for it.

(biotite's `include_bonds=True` refuses outright under `altloc="all"` and says
to use `connect_via_residue_names` afterwards — which is already protean's
fallback in `_bond_pairs`, so nothing new is needed beyond ordering.)

### 4.5 What not to do

- **Do not put altloc in the residue key** (§2).
- **Do not compute over mixed states**, which is what PyMOL does and what makes
  its aggregate numbers quietly meaningless for those residues.
- **Do not make the state choice implicit.** A reply that does not say which
  conformer it used is a smaller version of the bug this fixes.

## 5. What will change, and what must not

| | before | after |
|---|---|---|
| atoms, analysis vs viewer | differ by a stated surplus | equal |
| `alt A` | refused | selects |
| `interface()` numbers | one conformer, arbitrary choice | one conformer, highest occupancy, stated |
| bond selectors | one conformer | one conformer, after filtering |
| handle transport | exact | exact |

Existing answers that involve a residue with alternates **will move slightly**,
because the conformer choice changes from first-in-file to highest-occupancy.
Everything else must not move at all: 5FJI has alternates in 32 residues out of
several thousand, so the differential suite should be almost entirely static.

## 6. Verification

The failure here is a plausible number, so counting is not enough.

1. **The atom counts must equal the viewer's**, on 5FJI and 1AKE, with the
   surplus wording gone from the load message. This is the cheapest signal that
   4.1 landed.
2. **Per-residue areas must not double.** Take a residue with two conformers and
   assert its buried area is within tolerance of the single-conformer answer,
   not ~1.5x it. Without a state filter this test fails by +42% to +48% on
   5FJI's worst residues, which is the mutation.
3. **No bond may join two conformers.** Assert directly over the derived
   topology that no bond has two different letters at its ends. Mutation:
   derive topology before filtering and watch 16 appear on 1AKE.
4. **`alt` is literal, and the numbers say which reading shipped.** On 5FJI
   `alt A` is **206** atoms and `alt ''+A` is **15712** — three orders of
   magnitude apart, so a test asserting both cannot be satisfied by the wrong
   reading. Assert too that `alt A` and `alt B` are disjoint (they are not,
   under the rejected state reading) and that `alt ''` and `alt .` give the
   same set, since an empty string in a value list is the sort of token a
   parser drops without complaining.
5. **Handle transport for a one-conformer set**, read back from a real viewer by
   atom id, as item 7 does for symmetry copies. The claim is that a set of
   conformer-A atoms draws conformer A and not its twin.
6. **The differential suite must barely move** (§5).

## 7. Order of work

| # | Task | Why here |
|---|---|---|
| 1 | Load `altloc="all"`, keep `altloc_id`, assert counts match the viewer | Everything rests on it, and it is the one step with a cheap loud signal |
| 2 | State filter helper + topology ordering (§4.3, §4.4) | Must land **with** 1, or every analysis tool is briefly wrong |
| 3 | `alt` in the grammar (§4.2) | The user-visible payoff, and trivial once 1 is done |
| 4 | Run the differential suite unchanged | Regression gate before adding tests |
| 5 | The verification suite (§6) | |
| 6 | Retire the `alt` refusal text; close backlog item 8's open decision; record a decision in PLAN.md | The repo records its own history |

Tasks 1 and 2 are one commit, not two: between them the tools would compute
over duplicated atoms, which is precisely the bug.

## 8. Open questions

- **Should the reply name the state per residue or once per call?** Once is
  simpler; per residue is honest about structures where the dominant conformer
  differs between sites.
- **Is highest-occupancy right when occupancies tie?** 1AKE's ARG167 is 0.5/0.5.
  Falling back to the first letter is defensible and must be stated.
