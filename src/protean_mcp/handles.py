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
    # Symmetry copies share chain ids and residue numbers, so the copy is part
    # of a residue's identity in an assembly; without it two distinct residues
    # count as one and the total comes out half.
    has_sym = "sym_id" in array.get_annotation_categories()
    syms = subset.get_annotation("sym_id") if has_sym else [0] * len(subset)
    multiple = has_sym and len({int(s) for s in syms}) > 1

    seen: set[tuple[str, int, str, int]] = set()
    residues: list[dict[str, Any]] = []
    for chain, seq, ins, comp, sym in zip(
        subset.chain_id,
        subset.res_id,
        subset.ins_code,
        subset.res_name,
        syms,
        strict=True,
    ):
        key = (str(chain), int(seq), str(ins), int(sym))
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
            if multiple:
                entry["sym"] = int(sym)
            residues.append(entry)
    return {
        "atom_count": len(indices),
        "residue_count": len(seen),
        "chains": chains,
        "residues": residues,
        "truncated": len(seen) > len(residues),
    }


def _multi_copy(array: AtomArray[Any]) -> bool:
    """Does this array hold more than one symmetry copy?

    Keyed on the *structure*, not on the selection: a set covering only copy 0
    of a three-copy assembly still needs its operator clause, and asking the
    selection would drop it.
    """
    if "sym_id" not in array.get_annotation_categories():
        return False
    return int(np.unique(np.asarray(array.sym_id)).size) > 1


def _runs(values: Indices) -> list[tuple[int, int]]:
    ordered = np.sort(np.asarray(values))
    if len(ordered) == 0:
        return []
    breaks = np.flatnonzero(np.diff(ordered) != 1)
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [len(ordered) - 1]])
    return [(int(ordered[s]), int(ordered[e])) for s, e in zip(starts, ends, strict=True)]


def _id_test(atom_ids: Indices) -> str:
    """An atom-id predicate over one set of ids: ranges plus set membership."""
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
    return terms[0] if len(terms) == 1 else "(or " + " ".join(terms) + ")"


def operator_name(sym_id: int) -> str:
    """Mol\\*'s name for the symmetry operator biotite calls ``sym_id``.

    Mol\\* builds assembly operators in ``getAssemblyOperators`` and names them
    ``ASM_<index>`` where the index is **pre-incremented**, so the first
    operator is ``ASM_1`` and biotite's 0-based ``sym_id`` is one less. Proven
    by centroid against a real viewer on 1HHO (2 copies) and 1COI (3 copies);
    counting cannot check it, because every copy has the same atoms.
    """
    return f"ASM_{sym_id + 1}"


def to_molscript(array: AtomArray[Any], indices: Indices) -> str:
    """Render an explicit atom set as MolScript for the viewer.

    Contiguous stretches become ranges and the rest set membership, which
    keeps even a fully fragmented selection small: every backbone CA of
    haemoglobin is 574 separate runs and still under 3 kB, while a whole
    polymer chain collapses to a single range.

    On a biological assembly the copies of the asymmetric unit **share their
    atom ids**, so an atom-id predicate alone matches the named atom in every
    copy. Where the set does not cover every copy identically, each copy gets
    its own clause keyed on the symmetry operator::

        (or (and (= atom.op-name `ASM_1`) <ids in copy 0>)
            (and (= atom.op-name `ASM_2`) <ids in copy 1>))

    A set that *is* symmetric across every copy is emitted as the bare atom-id
    test, which means exactly the same thing and is what this has always sent.
    """
    if len(indices) == 0:
        return "(sel.atom.empty)"
    indices = np.asarray(indices)
    ids = np.asarray(array.atom_id)
    if not _multi_copy(array):
        return f"(sel.atom.atom-groups :atom-test {_id_test(ids[indices])})"

    sym = np.asarray(array.sym_id)
    per_copy = {int(k): np.sort(ids[indices[sym[indices] == k]]) for k in np.unique(sym)}
    present = {k: v for k, v in per_copy.items() if len(v)}
    # Symmetric across every copy: the operator clauses would be n identical
    # id tests, so the bare test is equivalent and far smaller. Emitting it
    # keeps every existing handle byte-for-byte what it was.
    first = next(iter(present.values()))
    if len(present) == len(per_copy) and all(
        np.array_equal(v, first) for v in present.values()
    ):
        return f"(sel.atom.atom-groups :atom-test {_id_test(first)})"

    terms = [
        f"(and (= atom.op-name `{operator_name(k)}`) {_id_test(v)})"
        for k, v in sorted(present.items())
    ]
    test = terms[0] if len(terms) == 1 else "(or " + " ".join(terms) + ")"
    return f"(sel.atom.atom-groups :atom-test {test})"
