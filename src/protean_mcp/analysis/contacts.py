"""Interface analysis: buried surface area, interface residues, and contacts.

Like superposition, this runs in Python on the coordinates so that every
reported number is checkable without a browser.

A note on hydrogen bonds. A real hydrogen bond is defined by donor-H...acceptor
geometry, and most crystal structures contain no hydrogens at all. Rather than
quietly relabel distance-based contacts as hydrogen bonds, this module detects
whether hydrogens are present, uses the proper geometric criterion when they
are, falls back to a heavy-atom distance criterion when they are not, and
reports in ``criterion`` which of the two produced the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from biotite.structure import AtomArray, CellList, filter_solvent, hbond, sasa

from ..selections_numpy import (
    conformer_state,
    conformers_used,
    has_altlocs,
    residue_labels,
)

# Heavy-atom fallback: N/O pairs this close are conventionally treated as
# putative polar contacts when hydrogens are absent.
POLAR_CUTOFF = 3.5
SALT_BRIDGE_CUTOFF = 4.0
# A residue counts as interfacial if complexation buries this much of it.
INTERFACE_DELTA_SASA = 1.0

_ANIONIC = {("ASP", "OD1"), ("ASP", "OD2"), ("GLU", "OE1"), ("GLU", "OE2")}
_CATIONIC = {
    ("ARG", "NE"),
    ("ARG", "NH1"),
    ("ARG", "NH2"),
    ("LYS", "NZ"),
    ("HIS", "ND1"),
    ("HIS", "NE2"),
}


class ContactError(ValueError):
    """Raised when an interface cannot be computed as asked."""


@dataclass
class Contact:
    chain_a: str
    seq_a: int
    comp_a: str
    atom_a: str
    chain_b: str
    seq_b: int
    comp_b: str
    atom_b: str
    distance: float
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "a": f"{self.chain_a}/{self.comp_a}{self.seq_a}/{self.atom_a}",
            "b": f"{self.chain_b}/{self.comp_b}{self.seq_b}/{self.atom_b}",
            "distance": round(self.distance, 2),
            "kind": self.kind,
        }


@dataclass
class InterfaceResult:
    chain_a: str
    chain_b: str
    buried_area: float
    buried_area_a: float
    buried_area_b: float
    interface_residues_a: list[dict[str, Any]]
    interface_residues_b: list[dict[str, Any]]
    # Every atom of the residues listed above, indexed into the array as it was
    # passed in — before solvent was dropped. These are what the caller turns
    # into handles, so the set never has to be re-encoded as a selection string.
    indices_a: np.ndarray[Any, Any]
    indices_b: np.ndarray[Any, Any]
    contacts: list[Contact]
    criterion: str
    solvent: str
    # Which symmetry copy this describes, or None for the whole structure.
    copy: int | None = None
    # Which alternate conformer the geometry was computed over, "" when the
    # structure has none. Stated because a buried area over conformer A while
    # the picture shows A and B is a quiet mismatch.
    conformer: str = ""
    # On a multi-copy assembly with no copy named: one summary per copy, so a
    # total that fuses several interfaces cannot be mistaken for one of them.
    per_copy: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        # indices_a/indices_b are deliberately absent: thousands of integers
        # are not something a caller should read. They become handles instead.
        kinds: dict[str, int] = {}
        for contact in self.contacts:
            kinds[contact.kind] = kinds.get(contact.kind, 0) + 1
        out = {
            "chain_a": self.chain_a,
            "chain_b": self.chain_b,
            "buried_area": round(self.buried_area, 1),
            "buried_area_a": round(self.buried_area_a, 1),
            "buried_area_b": round(self.buried_area_b, 1),
            "interface_residues_a": self.interface_residues_a,
            "interface_residues_b": self.interface_residues_b,
            "contact_counts": kinds,
            "contacts": [c.as_dict() for c in self.contacts],
            "criterion": self.criterion,
            "solvent": self.solvent,
        }
        if self.copy is not None:
            out["copy"] = self.copy
        if self.conformer:
            out["conformer"] = self.conformer
        if self.per_copy:
            out["per_copy"] = self.per_copy
        if self.note:
            out["note"] = self.note
        return out

    def summary(self) -> dict[str, Any]:
        """The compact form used for one entry of ``per_copy``."""
        kinds: dict[str, int] = {}
        for contact in self.contacts:
            kinds[contact.kind] = kinds.get(contact.kind, 0) + 1
        return {
            "copy": self.copy,
            # Each copy resolves its own conformers, and on an assembly whose
            # copies differ in occupancy they can differ. Omitting it let the
            # breakdown mix states with no way to see it.
            **({"conformer": self.conformer} if self.conformer else {}),
            "buried_area": round(self.buried_area, 1),
            "buried_area_a": round(self.buried_area_a, 1),
            "buried_area_b": round(self.buried_area_b, 1),
            "residue_count_a": len(self.interface_residues_a),
            "residue_count_b": len(self.interface_residues_b),
            "contact_counts": kinds,
        }


def _chain(array: AtomArray[Any], chain_id: str) -> np.ndarray[Any, Any]:
    mask = array.chain_id == chain_id
    if not mask.any():
        available = ", ".join(sorted(set(array.chain_id.tolist())))
        raise ContactError(
            f"No chain {chain_id!r} in structure; chains present: {available}"
        )
    return np.asarray(mask)


def _residues(
    array: AtomArray[Any], mask: np.ndarray[Any, Any], delta: np.ndarray[Any, Any]
) -> tuple[list[dict[str, Any]], np.ndarray[Any, Any]]:
    """Per-residue buried area for the residues that lose surface on binding.

    Returns the residue entries and the indices of every atom belonging to
    them. The atom set is deliberately wider than the atoms that lost surface:
    a side chain packed against the partner has atoms with zero delta, and a
    caller colouring the interface wants the whole residue, not the rind of it.
    """
    # Keyed by the residue's full identity, symmetry copy included. In a
    # biological assembly two copies share a chain id and a residue number, so
    # a key without the copy silently folds two physically distinct residues
    # into one and sums their buried areas.
    all_labels = residue_labels(array)
    symmetric = _sym_ids(array)

    out: dict[str, dict[str, Any]] = {}
    for i in np.flatnonzero(mask):
        if delta[i] <= 0:
            continue
        entry = out.setdefault(
            str(all_labels[i]),
            {
                "chain": str(array.chain_id[i]),
                "seq": int(array.res_id[i]),
                "comp": str(array.res_name[i]),
                "buried": 0.0,
                **({"sym": int(symmetric[i])} if symmetric is not None else {}),
            },
        )
        entry["buried"] += float(delta[i])
    kept = {k: r for k, r in out.items() if r["buried"] > INTERFACE_DELTA_SASA}
    for r in kept.values():
        r["buried"] = round(r["buried"], 1)

    side = np.flatnonzero(mask)
    indices = side[np.isin(all_labels[side], list(kept))]
    entries = sorted(kept.values(), key=lambda r: (-r["buried"], r["seq"]))
    return entries, indices


def _sym_ids(array: AtomArray[Any]) -> Any:
    """Per-atom symmetry copy, or None when there is only one copy."""
    if "sym_id" not in array.get_annotation_categories():
        return None
    ids = np.asarray(array.get_annotation("sym_id"))
    return ids if np.unique(ids).size > 1 else None


def _delta_sasa(
    array: AtomArray[Any], mask_a: np.ndarray[Any, Any], mask_b: np.ndarray[Any, Any]
) -> np.ndarray[Any, Any]:
    """Surface area each atom loses when the two chains come together.

    sasa() returns NaN for atoms it has no radius for (water, unknowns);
    treating those as zero area is right, and is why buried area is
    solvent-free whatever include_water says.
    """
    pair_mask = mask_a | mask_b
    sasa_complex = np.nan_to_num(sasa(array[pair_mask]))
    alone = np.concatenate(
        [np.nan_to_num(sasa(array[mask_a])), np.nan_to_num(sasa(array[mask_b]))]
    )

    order = np.concatenate([np.flatnonzero(mask_a), np.flatnonzero(mask_b)])
    position = {int(idx): k for k, idx in enumerate(np.flatnonzero(pair_mask))}
    complex_in_order = np.array([sasa_complex[position[int(idx)]] for idx in order])

    delta = np.zeros(array.array_length())
    delta[order] = alone - complex_in_order
    return delta


def _classify(
    array: AtomArray[Any],
    i: int,
    j: int,
    distance: float,
    hbond_pairs: set[tuple[int, int]],
) -> str | None:
    """Label one close atom pair, or None if it is not worth reporting."""
    res_i = (str(array.res_name[i]), str(array.atom_name[i]))
    res_j = (str(array.res_name[j]), str(array.atom_name[j]))
    charged = (res_i in _ANIONIC and res_j in _CATIONIC) or (
        res_j in _ANIONIC and res_i in _CATIONIC
    )
    if charged and distance <= SALT_BRIDGE_CUTOFF:
        return "salt_bridge"
    if (i, j) in hbond_pairs or (j, i) in hbond_pairs:
        return "hydrogen_bond"
    polar = str(array.element[i]) in "NO" and str(array.element[j]) in "NO"
    if polar and distance <= POLAR_CUTOFF:
        return "polar_contact"
    return None


def _copy_ids(array: AtomArray[Any]) -> list[int]:
    """The symmetry copies present, or [] on an asymmetric unit."""
    if "sym_id" not in array.get_annotation_categories():
        return []
    return sorted({int(v) for v in np.asarray(array.sym_id)})


def interface(  # noqa: PLR0913
    array: AtomArray[Any],
    chain_a: str,
    chain_b: str,
    contact_limit: int = 200,
    include_water: bool = False,
    *,
    copy: int | None = None,
) -> InterfaceResult:
    """Describe the interface between two chains of one structure.

    On a biological assembly the copies of the asymmetric unit share chain
    ids, so "chain A" names one chain in *every* copy and an A-B interface is
    really several interfaces at once. Haemoglobin's assembly reports 2765.9
    A^2 between A and B where the one alpha-beta pair buries 873.9 A^2 — both
    true, about different molecules.

    ``copy`` names one copy, numbered from 0 as ``sym N`` numbers them. With
    no copy given the answer still describes the whole structure, as it always
    has, but carries a ``per_copy`` breakdown so a fused total cannot be
    mistaken for a single interface. The total is **not** the sum of the
    parts: chain A of one copy also touches chain B of another, and that
    contribution belongs to neither.

    Solvent is excluded by default: ordered waters sit in the gap between
    subunits and fill the contact list with water-to-water pairs that say
    nothing about the interface. Cofactors and ligands are kept, since they
    are genuinely part of the subunit they belong to.

    ``include_water`` affects the **contact list only**. Buried area is always
    solvent-free, because biotite assigns water no van der Waals radius and
    returns NaN for it, which contributes nothing either way.
    """
    if chain_a == chain_b:
        raise ContactError("chain_a and chain_b must differ")
    copies = _copy_ids(array)

    if copy is not None:
        if copy not in copies:
            available = ", ".join(str(c) for c in copies) or "none"
            raise ContactError(
                f"No symmetry copy {copy} in this structure; copies present: "
                f"{available}. A structure loaded as the asymmetric unit has "
                'no copies — load with assembly="biological" to address one'
            )
        keep = np.flatnonzero(np.asarray(array.sym_id) == copy)
        result = _interface(array[keep], chain_a, chain_b, contact_limit, include_water)
        # The indices have to mean something in the array the caller handed
        # us, not in this private subset, or every handle names wrong atoms.
        return replace(
            result,
            copy=copy,
            indices_a=keep[result.indices_a],
            indices_b=keep[result.indices_b],
        )

    result = _interface(array, chain_a, chain_b, contact_limit, include_water)
    if len(copies) <= 1:
        return result

    per_copy: list[dict[str, Any]] = []
    for k in copies:
        keep = np.flatnonzero(np.asarray(array.sym_id) == k)
        subset = array[keep]
        # An assembly generator can select a subset of chains, so do not
        # assume every copy holds both. Skipping is right where raising would
        # lose the copies that do.
        if (
            not (subset.chain_id == chain_a).any()
            or not (subset.chain_id == chain_b).any()
        ):
            continue
        one = _interface(subset, chain_a, chain_b, contact_limit, include_water)
        per_copy.append(replace(one, copy=k).summary())

    return replace(
        result,
        per_copy=per_copy,
        note=(
            f"this structure has {len(copies)} symmetry copies sharing chain "
            f"ids, so buried_area is every {chain_a}-{chain_b} contact in the "
            "assembly at once; per_copy breaks it down, and the total exceeds "
            "their sum by the contacts between different copies. Pass copy=N, "
            f"or select '{chain_a} and sym N', for one of them"
        ),
    )


def _interface(
    array: AtomArray[Any],
    chain_a: str,
    chain_b: str,
    contact_limit: int = 200,
    include_water: bool = False,
) -> InterfaceResult:
    """One interface over whatever atoms it is given."""
    # Dropping solvent renumbers the atoms, so keep the map back to the
    # caller's numbering: the indices we return have to mean something in the
    # array they handed us, not in this private copy.
    if include_water:
        origin_index = np.arange(array.array_length())
    else:
        origin_index = np.flatnonzero(~filter_solvent(array))
        array = array[origin_index]

    # One conformer state, for the same reason and by the same mechanism.
    # Alternate conformers never coexist, so an area computed with both of
    # them present buries each behind the other and belongs to no molecule --
    # and because a residue's shared atoms carry no letter, both states land
    # in one residue entry and their areas sum. On 5FJI that inflates the
    # worst residues by nearly half.
    conformer = ""
    if has_altlocs(array):
        state = conformer_state(array)
        conformer = conformers_used(array, state)
        keep = np.flatnonzero(state)
        origin_index = origin_index[keep]
        array = array[keep]
    mask_a = _chain(array, chain_a)
    mask_b = _chain(array, chain_b)

    delta_by_index = _delta_sasa(array, mask_a, mask_b)
    buried_a = float(delta_by_index[mask_a].sum())
    buried_b = float(delta_by_index[mask_b].sum())

    # Proper geometry when hydrogens exist; distance-only when they do not.
    has_hydrogen = bool((array.element == "H").any())
    hbond_pairs: set[tuple[int, int]] = set()
    if has_hydrogen:
        criterion = "donor-H...acceptor geometry (hydrogens present)"
        triplets: Any = hbond(array, selection1=mask_a, selection2=mask_b)
        hbond_pairs = {(int(d), int(a)) for d, _, a in np.asarray(triplets)}
    else:
        criterion = (
            f"heavy-atom N/O within {POLAR_CUTOFF} A "
            "(no hydrogens in the structure, so angles cannot be checked)"
        )

    cutoff = max(POLAR_CUTOFF, SALT_BRIDGE_CUTOFF)
    cell_list: Any = CellList(array[mask_b], cell_size=cutoff)
    neighbours = cell_list.get_atoms(array[mask_a].coord, radius=cutoff)
    indices_a = np.flatnonzero(mask_a)
    indices_b = np.flatnonzero(mask_b)

    contacts: list[Contact] = []
    for local_a, row in enumerate(neighbours):
        for local_b in row[row >= 0]:
            i = int(indices_a[local_a])
            j = int(indices_b[int(local_b)])
            distance = float(np.linalg.norm(array.coord[i] - array.coord[j]))
            kind = _classify(array, i, j, distance, hbond_pairs)
            if kind is None:
                continue
            contacts.append(
                Contact(
                    chain_a=str(array.chain_id[i]),
                    seq_a=int(array.res_id[i]),
                    comp_a=str(array.res_name[i]),
                    atom_a=str(array.atom_name[i]),
                    chain_b=str(array.chain_id[j]),
                    seq_b=int(array.res_id[j]),
                    comp_b=str(array.res_name[j]),
                    atom_b=str(array.atom_name[j]),
                    distance=distance,
                    kind=kind,
                )
            )
    contacts.sort(key=lambda c: c.distance)

    residues_a, atoms_a = _residues(array, mask_a, delta_by_index)
    residues_b, atoms_b = _residues(array, mask_b, delta_by_index)

    return InterfaceResult(
        chain_a=chain_a,
        chain_b=chain_b,
        buried_area=buried_a + buried_b,
        buried_area_a=buried_a,
        buried_area_b=buried_b,
        interface_residues_a=residues_a,
        interface_residues_b=residues_b,
        indices_a=origin_index[atoms_a],
        indices_b=origin_index[atoms_b],
        contacts=contacts[:contact_limit],
        criterion=criterion,
        conformer=conformer,
        solvent=(
            "contacts include water; buried area is always solvent-free"
            if include_water
            else "excluded"
        ),
    )
