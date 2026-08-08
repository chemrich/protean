"""Named selections as first-class values.

A handle is a set of atoms with a name. Analysis produces them, set operations
combine them, and display tools consume them. This is what lets a caller say
"colour the interface residues" without re-encoding a set the system is
already holding as ``resi 31+114+117+...``.

Composition lives here rather than in the selection grammar. The DSL keeps
leaf predicates, where it is genuinely convenient and where a model writes it
fluently; combining sets is an explicit operation with named arguments, so
there is no operator precedence to get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from biotite.structure import AtomArray

Indices = np.ndarray[Any, Any]

# Runs shorter than this are cheaper to send as set members than as a range.
_MIN_RUN = 3
# subtract is the one operation that is meaningless with a single operand.
_MIN_SUBTRACT_OPERANDS = 2


class HandleError(ValueError):
    """Raised for unknown handles or impossible combinations."""


@dataclass
class Handle:
    """A named set of atoms, and where it came from."""

    name: str
    indices: Indices
    origin: str

    def __len__(self) -> int:
        return len(self.indices)


@dataclass
class HandleRegistry:
    """The handles belonging to one loaded structure."""

    handles: dict[str, Handle] = field(default_factory=dict)

    def set(self, name: str, indices: Indices, origin: str) -> Handle:
        handle = Handle(name=name, indices=np.unique(np.asarray(indices)), origin=origin)
        self.handles[name] = handle
        return handle

    def get(self, name: str) -> Handle:
        try:
            return self.handles[name]
        except KeyError:
            known = ", ".join(sorted(self.handles)) or "(none)"
            raise HandleError(f"No selection named {name!r}. Known: {known}") from None

    def drop(self, name: str) -> None:
        self.get(name)
        del self.handles[name]

    def clear(self) -> None:
        self.handles.clear()

    def names(self) -> list[str]:
        return sorted(self.handles)


def combine(registry: HandleRegistry, op: str, names: list[str]) -> Indices:
    """Union, intersection or difference over existing handles."""
    # Validate up front: checking inside the fold means a single-operand call
    # never reaches the check and an unknown operation passes silently.
    if op not in ("union", "intersect", "subtract"):
        raise HandleError(f"Unknown operation {op!r} (union, intersect, subtract)")
    if not names:
        raise HandleError("Give at least one selection to combine")
    if op == "subtract" and len(names) < _MIN_SUBTRACT_OPERANDS:
        raise HandleError("subtract needs at least two selections")
    sets = [registry.get(name).indices for name in names]
    result = sets[0]
    for other in sets[1:]:
        if op == "union":
            result = np.union1d(result, other)
        elif op == "intersect":
            result = np.intersect1d(result, other)
        else:
            result = np.setdiff1d(result, other)
    return np.asarray(result)


def summarise(
    array: AtomArray[Any], indices: Indices, limit: int = 200
) -> dict[str, Any]:
    """What a set of atoms actually contains.

    Computed here rather than in the viewer, so it is available with no
    browser and is the same answer whether or not anything is being displayed.
    """
    subset = array[indices]
    chains = sorted({str(c) for c in subset.chain_id})
    seen: set[tuple[str, int, str]] = set()
    residues: list[dict[str, Any]] = []
    for chain, seq, ins, comp in zip(
        subset.chain_id, subset.res_id, subset.ins_code, subset.res_name, strict=True
    ):
        key = (str(chain), int(seq), str(ins))
        if key in seen:
            continue
        seen.add(key)
        if len(residues) < limit:
            entry: dict[str, Any] = {
                "chain": str(chain),
                "seq": int(seq),
                "comp": str(comp),
            }
            if str(ins).strip():
                entry["ins_code"] = str(ins).strip()
            residues.append(entry)
    return {
        "atom_count": len(indices),
        "residue_count": len(seen),
        "chains": chains,
        "residues": residues,
        "truncated": len(seen) > len(residues),
    }


def _runs(values: Indices) -> list[tuple[int, int]]:
    ordered = np.sort(np.asarray(values))
    if len(ordered) == 0:
        return []
    breaks = np.flatnonzero(np.diff(ordered) != 1)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [len(ordered) - 1]])
    return [(int(ordered[s]), int(ordered[e])) for s, e in zip(starts, ends, strict=True)]


def to_molscript(array: AtomArray[Any], indices: Indices) -> str:
    """Render an explicit atom set as MolScript for the viewer.

    Contiguous stretches become ranges and the rest set membership, which
    keeps even a fully fragmented selection small: every backbone CA of
    haemoglobin is 574 separate runs and still under 3 kB, while a whole
    polymer chain collapses to a single range.
    """
    if len(indices) == 0:
        return "(sel.atom.empty)"
    atom_ids = np.asarray(array.atom_id)[np.asarray(indices)]
    spans = _runs(atom_ids)
    terms = [
        f"(and (>= atom.id {lo}) (<= atom.id {hi}))"
        for lo, hi in spans
        if hi - lo + 1 >= _MIN_RUN
    ]
    singles = [v for lo, hi in spans if hi - lo + 1 < _MIN_RUN for v in range(lo, hi + 1)]
    if singles:
        members = " ".join(str(v) for v in singles)
        terms.append(f"(set.has (set {members}) atom.id)")
    test = terms[0] if len(terms) == 1 else "(or " + " ".join(terms) + ")"
    return f"(sel.atom.atom-groups :atom-test {test})"
