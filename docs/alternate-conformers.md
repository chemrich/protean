# Alternate conformers — making `alt` selectable without lying about geometry

Planned 2026-08-13, not started. Closes the `alt` decision left open in
[backlog](backlog.md) item 8, which said the refusal stood "by choice rather
than impossibility" and wanted a decision. The decision is: **load every
conformer**.

**Read §2 before §4.** The obvious implementation — and the one this plan was
originally going to propose — is wrong for a reason that only shows up when
you look at which atoms actually carry an altloc label.

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

### 4.2 `alt` becomes a leaf predicate

`alt A` on the `altloc_id` annotation, with `alt ''` for the untagged atoms as
in PyMOL. Refused with a named reason on a structure that has no alternates,
the way `sym` is refused on an asymmetric unit.

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
4. **`alt A` and `alt B` must differ, and neither may be empty.** On 5FJI both
   are 206 atoms; assert they are disjoint and that `alt ''` is neither.
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

- **Should `alt` default to `''+A` semantics when a caller writes `alt A`?**
  PyMOL's `alt A` returns *only* the tagged atoms, not the shared ones, so
  `alt A` alone is a fragment of a residue. Matching PyMOL is probably right,
  but it means the obvious-looking selection is not the conformer state.
  The state is `alt ''+A`.
- **Should the reply name the state per residue or once per call?** Once is
  simpler; per residue is honest about structures where the dominant conformer
  differs between sites.
- **Is highest-occupancy right when occupancies tie?** 1AKE's ARG167 is 0.5/0.5.
  Falling back to the first letter is defensible and must be stated.
