# Item 7 — addressing one symmetry copy

Planned 2026-08-12, not started. This is the last item of substance in
[the backlog](backlog.md), and the only one PLAN.md decision 9 left explicitly
unsolved.

**Read this before writing any code.** The central risk is not difficulty; it
is that a wrong answer here looks exactly like a right one.

---

> ## Done, 2026-08-13 — and this plan was wrong in three places
>
> Implemented in PR 53 and recorded as **PLAN.md decision 15**, which is the
> authority; this file is kept as the plan it was, with corrections marked
> where it misled. §5's insistence on proving the mapping by geometry is the
> part that earned its keep — it is what caught the off-by-one below.
>
> **The plan's own central risk landed exactly as described, three times over.**
> Each of these parses cleanly, matches zero atoms, and is reported as a
> successful empty selection:
>
> 1. **§2's predicate does not work as written.** The MolScript *script parser*
>    accepts aliases, not symbol-table paths: it is **`atom.op-name`**, never
>    `atom-property.core.operatorName`. §2's source reading was accurate and
>    still produced an expression that matched nothing. The alias table is
>    `mol-script/script/mol-script/symbols.js`.
> 2. **String literals are delimited by backticks.** `(= atom.op-name "ASM_1")`
>    parses `"ASM_1"` as a *symbol*, which compares unequal to every string; it
>    must be `` (= atom.op-name `ASM_1`) ``. Nothing in protean's emitter had
>    ever sent a string, so this was unexplored ground —
>    `mol-script/language/parser.js:28`.
> 3. **§3 and §4.1 assume `ASM_0`; the first operator is `ASM_1`.** Mol\*
>    pre-increments its operator index in `getAssemblyOperators`, so biotite's
>    0-based `sym_id k` is **`ASM_{k+1}`**. §3 was right that only coordinates
>    could settle this and right that it had not been established — it was
>    simply wrong about the answer.
>
> Also: **`operatorKey` is unusable.** §4.1 offers it as possibly the better
> key. Every unit in an assembly reports key `-1`, so only the name works.
>
> **What the §5.1 mapping proof returned** — centroid distance between
> biotite's copy and Mol\*'s operator, against a real headless Chrome:
>
> ```
> 1HHO  sym_id 0 -> ASM_1  0.0000 A     1COI  sym_id 0 -> ASM_1  0.0000 A
>       sym_id 1 -> ASM_2  0.0000 A           sym_id 1 -> ASM_2  0.0000 A
>                                             sym_id 2 -> ASM_3  0.0000 A
> ```
>
> 1COI is a coiled-coil trimer, added because §5.1 correctly notes that a
> 2-copy fixture cannot tell "correct" from "consistently reversed".
>
> **§7's questions were answered by Charlie**: `sym N` in the grammar *and*
> `copy=` on `interface()`, the grammar being the primitive so every
> handle-taking tool becomes copy-aware at once; and `interface()` reports per
> copy *plus* the total. The third was settled as §7 suggested — the integer
> `sym` is the only vocabulary a caller sees, and `ASM_k` never leaves the
> emitter.
>
> **One task §6 was missing, worth naming.** The list stops at the code. CI's
> browser job names its test files explicitly, so the new suite did not run
> there until it was added — the first run on PR 53 went green with all twenty
> of its centroid tests unexecuted. A plan that adds a browser suite should
> carry "add it to `.github/workflows/ci.yml`, and check the job's `N passed`
> actually moved" as a task of its own.

---

## 1. What is broken

`load_structure` defaults to `assembly="biological"`, so the array protean
analyses is the biological assembly — copies of the asymmetric unit generated
by symmetry operators. Those copies **share chain ids, residue numbers and
`atom_id`**. Only biotite's `sym_id` annotation tells them apart, and it exists
only on the Python side.

A handle is a set of atom indices. It reaches the viewer through
`handles.to_molscript`, which renders it as ranges and set membership over
`atom.id`:

```
(sel.atom.atom-groups :atom-test (or (and (>= atom.id 12) (<= atom.id 480))
                                     (set.has (set 7 9) atom.id)))
```

On an assembly that predicate matches the named atom **in every copy**. So a
set covering one copy cannot be expressed, and any set that is not symmetric
across copies is silently widened when it is drawn.

Decision 9 tolerated this on an explicit invariant: *everything the tools
produce is symmetric across copies, so the transport stays exact.* That
invariant has now been broken twice in one day:

- **`interface("A", "B")` on 1HHO** reports the whole tetramer's A–B interface
  (2765.9 Å² per side) where the one αβ pair is 873.9 Å². Both are true about
  different molecules; protean cannot be asked for the second.
- **`rank` had to be refused** on multi-copy assemblies (PR 50), because it
  picks one atom by array position and the handle lit up one per copy. The
  refusal is a placeholder for this work.

Two patches around one missing capability is the argument for doing it now.

## 2. The finding that unblocks it

**Decision 9 says Mol\* "appears to offer no MolScript predicate" for the
symmetry operator. That is wrong for the bundled version.** Verified in
`molstar` 4.18.0, end to end:

```
mol-script/language/symbol-table/structure-query.js:208
    operatorName: atomProp(Type.Str, 'Name of the symmetry operator applied to this element.')
    operatorKey:  atomProp(Type.Num, 'Key of the symmetry operator applied to this element.')

mol-script/runtime/query/table.js:326
    D(MolScript.structureQuery.atomProperty.core.operatorName,
      atomProp(StructureProperties.unit.operator_name))
```

Symbol table *and* runtime binding. So the viewer can already express "this
copy"; what is missing is protean saying which one.

> **Corrected.** The conclusion holds, the spelling does not. Neither name
> above is usable in a script: the parser takes the alias **`atom.op-name`**,
> and the string needs backticks. Writing
> `(= atom-property.core.operatorName "ASM_1")` from this section returns 0
> atoms and reports success. Symbol table and runtime binding were the wrong
> two files to check — `mol-script/script/mol-script/symbols.js` is the one
> that decides what a script may say.

**This means no new handle transport is needed.** Decision 9 asked for one; the
actual shape is an emitter change plus a verified name mapping. That is a much
smaller job than the backlog implies, and the backlog entry should be corrected
whatever else happens.

## 3. The hard part: which copy is which

Both sides enumerate copies. Neither promises the other's order.

- **Python (biotite):** `get_assembly` adds `sym_id`, "which enumerates the
  copies of the asymmetric unit". Integers from 0.
- **Mol\*:** `mol-model-formats/structure/property/assembly.js:94` creates each
  operator as `SymmetryOperator.create(\`ASM_${index}\`, ...)` — so operator
  names are `ASM_0`, `ASM_1`, … in the order operators are expanded from
  `pdbx_struct_assembly_gen.oper_expression`.

> **Corrected: the names start at `ASM_1`.** The same line increments `index`
> *before* building the name, and `operatorOffset` starts at 0, so the first
> operator of the first generator is `ASM_1`. `ASM_0` exists nowhere and
> selecting it returns 0 atoms with a success reply.

For 1HHO that expression is `1,2` over `pdbx_struct_oper_list` ids `1` (identity,
`x,y,z`) and `2` (`y,x,-z`), and both sides produce 2 copies. It is *plausible*
that biotite's `sym_id = k` is Mol\*'s `ASM_k`. It is not established, and:

> **Corrected: it is `ASM_{k+1}`,** proven by centroid on 1HHO and 1COI. The
> caution in this section was right; only its guess was wrong. Of the three
> risks listed below, the one that actually bit was none of them — it was an
> off-by-one in a single `index++`.

- a product expression like `(1-60)(P)` expands as a nested loop, and the two
  implementations may nest in opposite orders;
- an implementation may or may not emit the identity operator first, or at all;
- Mol\* may deduplicate operators that biotite keeps, or vice versa.

**A wrong mapping is undetectable by counting.** Every copy has the same number
of atoms, the same residues and the same chains, so a permutation of copies
gives identical counts, identical residue lists and a picture that looks
entirely normal — the failure mode this project already knows best. Only
**coordinates** distinguish them.

## 4. Proposed design

Smallest change that makes the invariant true rather than assumed.

### 4.1 Emit the operator alongside the atom ids

`to_molscript` currently takes `(array, indices)`. Group the indices by
`sym_id` and emit one clause per copy:

```
(sel.atom.atom-groups :atom-test
  (or (and (= atom-property.core.operatorName "ASM_0") <atom.id test for copy 0>)
      (and (= atom-property.core.operatorName "ASM_1") <atom.id test for copy 1>)))
```

> **Corrected — this expression matches nothing**, in all three of its parts:
> wrong symbol, wrong quotes, wrong index. What shipped is
>
> ```
> (sel.atom.atom-groups :atom-test
>   (or (and (= atom.op-name `ASM_1`) <atom.id test for copy 0>)
>       (and (= atom.op-name `ASM_2`) <atom.id test for copy 1>)))
> ```

- A structure with no `sym_id`, or one copy, emits exactly what it does today.
  No behaviour change off the assembly path.
- A set that *is* symmetric emits the same atom-id test under each operator,
  which is more verbose but identical in meaning — so existing handles keep
  working and the differential suite should not move.

> **Amended.** A symmetric set is emitted as the *bare* atom-id test, not as n
> identical clauses. It means the same thing, is far smaller, and keeps every
> existing handle byte-for-byte what it was — which turns §5.3 from "the counts
> should not move" into "the emitted string does not move" for those cases.

- `operatorKey` (numeric) may be the better key than `operatorName`; decide
  after §5.1 measures which is stable.

> **Answered: no.** Every unit in an assembly reports `operatorKey` `-1`, so it
> cannot distinguish copies at all. `operatorName` is the only option.

### 4.2 Name a copy in the selection grammar

Add `sym N` as a leaf property, evaluated in Python against the `sym_id`
annotation — the same shape as every other property. Then
`select("chain A and sym 0")` is expressible, and `interface()` can take a copy
argument built from it.

Refuse `sym` on a structure with no copies, naming the reason, exactly as `ss`
now refuses a CA-only model.

### 4.3 Retire the `rank` refusal

PR 50 refuses `rank` on multi-copy assemblies because the handle could not be
transported. Once §4.1 lands the transport is exact and the refusal should go,
with the test inverted to assert `rank` works there.

### 4.4 What not to do

- **Do not renumber `atom_id` per copy.** It is the file's own identifier and
  the viewer's copy is built independently; rewriting it re-creates the
  serialisation bug already recorded in the backlog, where two halves disagreed
  about which atom is which while counts still matched.
- **Do not make `sym` implicit.** A selection with no `sym` term must keep
  meaning "every copy", or every existing answer changes silently.

## 5. Verification — the part that decides whether this is correct

Ordinary count assertions cannot see the failure mode. Two of these are
non-negotiable.

### 5.1 Prove the mapping with geometry, not counts

For each copy `k`:

1. compute the centroid of copy `k` in Python from `sym_id == k`;
2. select `ASM_k` in the viewer and read back the centroid of what it matched;
3. assert the two agree to within a small tolerance.

Do it on a structure whose copies are **not** related by a small displacement,
so a swap is unmistakable. 1HHO is the natural fixture (2 copies, operator
`y,x,-z`). Add one with more than two copies, since a 2-copy test cannot
distinguish "correct" from "consistently reversed" — a 3+ copy case can.

**Deliberately break it:** hard-code `ASM_0` for every copy and confirm the
test fails. If it passes, it is not testing the mapping.

### 5.2 Copies must partition the structure

In the viewer, for the assembly:

- the copies are pairwise disjoint;
- their union is the whole structure;
- each copy's atom count equals the asymmetric unit's.

This catches an operator name that matches nothing (empty selection, success
reported) and one that matches everything.

### 5.3 The existing differential suite must not move

Every current handle is symmetric across copies, so §4.1 changes the emitted
MolScript for all of them while changing none of their answers. If any count in
`test_selection_differential.py` moves, the emitter is wrong. This is the
cheapest regression signal available and should be run before anything else.

### 5.4 Round-trip through a session

`session save` / `load` restores handles. A handle that now carries per-copy
structure must survive that trip; the corpus already checks nine handles
restore, so extend it with a per-copy one.

## 6. Order of work

| # | Task | Depends on | Why this order |
|---|---|---|---|
| 1 | Spike: evaluate an `operatorName` query in the real viewer against 1HHO and print what matches | — | Everything rests on this working in 4.18 as the source suggests. One throwaway script, an hour. |
| 2 | Establish the `sym_id` ↔ `ASM_k` mapping and prove it with §5.1 | 1 | The whole correctness question. Do not proceed on a plausible mapping. |
| 3 | `to_molscript` emits per-copy clauses (§4.1) | 2 | Mechanical once the mapping is known. |
| 4 | Run the existing differential suite unchanged (§5.3) | 3 | Regression gate before adding features. |
| 5 | `sym N` selection property (§4.2) | 3 | Makes the capability reachable. |
| 6 | `interface()` accepts a copy | 5 | The user-visible payoff, and the case in the backlog. |
| 7 | Retire the `rank` refusal (§4.3) | 3 | Small; proves the transport claim from a second direction. |
| 8 | Correct decision 9 and backlog item 7; add a decision recording the operator-name transport | 6 | The repo records its own history. |

Tasks 1 and 2 are the whole risk. If the mapping cannot be established, stop
and re-plan rather than shipping a plausible one — a silently permuted copy is
worse than the current honest limitation.

## 7. Open questions for Charlie

- **Does `sym` belong in the selection grammar at all**, or should a copy be an
  argument to the tools that need one (`interface(copy=0)`)? The grammar is
  cheaper and composes; an argument is more discoverable in the tool schema.
  Design stance says prefer schema-visible named arguments, which argues for
  the second — but `sym` is a leaf predicate, which is the one place PyMOL
  syntax was kept deliberately.
- **What should `interface("A", "B")` do by default on a tetramer?** Today it
  reports the total. Options: keep the total and add a copy argument; or report
  per copy and require an explicit "all". The second is more informative and
  changes an existing answer.
- **Numbering shown to the caller.** `residue_labels` and `summarise` already
  expose `sym` as an integer. If the viewer's names are `ASM_k` there are two
  vocabularies; worth keeping the integer as the only one a caller sees.

## 8. Facts worth not re-deriving

- `molstar` 4.18.0 has `atom-property.core.operatorName` and `operatorKey` in
  the MolScript symbol table (`structure-query.js:208-209`) and bound in the
  runtime (`runtime/query/table.js:326`).
- Mol\* assembly operators are named `ASM_<index>`
  (`mol-model-formats/structure/property/assembly.js:94`).
- biotite `get_assembly` adds `sym_id`, a 0-based enumeration of copies.
- 1HHO: assembly `1`, `oper_expression = "1,2"`, operators `1_555` (identity)
  and `7_555` (`y,x,-z`); 4792 atoms assembled, `sym_id ∈ {0, 1}`.
- Expansion is refused above `MAX_ASSEMBLY_COPIES`, read from the operator list
  before anything is built, so a capsid never reaches this path.
- The biological assembly is not always larger than the asymmetric unit —
  12E8's assembly is half its asymmetric unit — so "copies" is not a multiplier
  and no test should assume it is.
