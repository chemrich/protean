# Review findings — PR #59, "Load every alternate conformer, and analyse one state"

**Target:** `9bc94e4` (the merge commit), which is `main`'s HEAD as of
2026-08-13. Everything below applies to merged code.

**Provenance, because it matters for how much to trust each item.** These came
from a code review that was aimed at the wrong repository: it was meant to
review `chemrich/MCPymol#59` and resolved `59` against protean instead. So this
was not a review anybody asked for, and it was not scoped or briefed the way a
deliberate review of this PR would have been. Read it as a useful accident.

Two findings were **reproduced by running code** in this repo's venv; the rest
are analysis with concrete file:line citations. Marked individually below.

**Nothing here has been changed.** No protean file other than this one has been
touched, no branch created, no git state altered. I confirmed only that every
cited line exists and matches its description.

---

## 1. HIGH — `conservation()` registers handles indexed into the wrong array

`src/protean_mcp/server.py:2470`

*Analysis, not reproduced.*

Line 2426 binds:

```python
array, _ = _resolve_conformers(_require_structure())
```

which returns `array[state]` — a **shorter** copy, with non-chosen alternates
dropped. Line 2470 then computes

```python
indices = _residue_indices(array, keys)
```

against that shortened copy. But `_register`/`_display` interpret those indices
against `_require_structure()` — the **full** array — via `_summarise`
(`server.py:199`) and `_indices_to_molscript` (`server.py:193`).

On 5FJI (217 alternate rows) every index past the first alternate site is
shifted by up to 217. The consequence is a silent mismatch: the returned
per-residue conservation scores are **correct**, while the `conserved` and
`variable` handles **name and draw the wrong atoms**.

`interface()` already gets this right — `_interface` in `contacts.py` keeps
`origin_index` for precisely this reason. `conservation()` does not.

**Suggested check:** run `conservation()` on 5FJI, then compare the atoms in the
`conserved` handle against the residues the scores say are conserved.

---

## 2. HIGH — `altloc="all"` breaks `superimpose_homologs`' residue correspondence

`src/protean_mcp/analysis/superposition.py:93`

*Reproduced.*

biotite's `_get_backbone_anchor_indices` takes **every** CA atom, but
`_find_matching_anchors` maps alignment columns — one per **residue**, from
`to_sequence` — back into that anchor-atom array. A residue carrying an
alternate backbone contributes two CAs and one sequence position, so every
residue after it is paired off by one.

Reproduced on an 8-residue synthetic chain with an alternate CA at residue 3:
mobile anchors came back as

```
[1, 2, 3, 3, 4, 5, 6, 7]      against fixed [1, 2, 3, 4, 5, 6, 7, 8]
```

So `rmsd`, `sequence_identity`, `aligned_residues` and the outlier list are all
silently wrong, and the resulting transform is wrong with them.

Unlike every other coordinate path this PR touched, `superpose` never resolves a
conformer state.

---

## 3. MEDIUM — `parse_structure` passes no `extra_fields`, so occupancy-based conformer choice degrades to alphabetical

`src/protean_mcp/analysis/superposition.py:93`

*Reproduced.*

`conformer_state` falls back to `np.zeros` when `occupancy` is absent
(`selections_numpy.py:337`), so ties break on the altloc **letter** instead.

Reproduced: the same PDB text resolves to conformer **A** through
`parse_structure` and **B** through `load_structure`.

Concretely, `interface("5fji", "A", "B")` standalone and `interface("A", "B")`
after `fetch_structure("5fji")` compute buried areas **over different atoms**.
The reply's `conformer` field, and the load note's "each site resolved to its
highest occupancy" (`server.py:422`), are false for the standalone path.

The new comment at `superposition.py:88` says the two paths "must hold the same
atoms". They do — but they no longer resolve the same **state**, which is the
claim that matters here.

---

## 4. MEDIUM — `_conservation_gradient` re-sends every conformer with a blanked altloc column

`src/protean_mcp/server.py:2695`

*Verified: biotite's mmCIF writer emits `_atom_site.label_alt_id` as `.` for all rows.*

`_structure_as_mmcif` uses biotite's writer, which blanks the altloc column.
`color_by_conservation(mode="gradient")` sends the full `_require_structure()`
array, so after

```
fetch_structure("5fji"); conservation(); color_by_conservation()
```

the viewer holds 15,929 atoms with overlapping positions and no way to tell
conformers apart — bonds inferred across states, doubled spheres.

This is exactly the hazard `_display_superposition` guards against at
`server.py:2097-2106`. `viewer_atom_count` still agrees, so nothing flags it.

---

## 5. LOW — `_check_alt_is_available` refuses `alt ''` / `alt .`, which have a well-defined answer

`src/protean_mcp/selections_numpy.py:654`

The guard fires for any `alt` term when `has_altlocs(array)` is false. But
"atoms with no alternate" on a structure with no alternates is **every atom**.

`select("chain A and alt ''")` on 4HHB raises "no atom in this structure has
one" instead of returning chain A.

The guard is right for a letter; it should let the no-alternate spellings
through.

---

## Reading these together

Findings 1–3 share one shape: **an index or a state resolved in one place and
used in another.** `_resolve_conformers` returns a different array than the one
the caller later indexes into; `parse_structure` and `load_structure` resolve
different states from the same bytes; biotite's anchors are per-atom while the
alignment is per-residue.

That is worth noting beyond the individual fixes, because the altloc work
necessarily introduced "two arrays that look alike and are not" in several
places at once, and the failures are all silent — correct-looking numbers
attached to the wrong atoms. Wherever a resolved-state array and a full array
both exist in scope, the question to ask is which one an index belongs to.

`contacts.py`'s `origin_index` is the existing answer to that question, and
finding 1 is what happens where it was not applied.
