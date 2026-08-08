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

from dataclasses import dataclass
from typing import Any

import numpy as np
from biotite.structure import AtomArray, CellList, filter_solvent, hbond, sasa

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

    def as_dict(self) -> dict[str, Any]:
        # indices_a/indices_b are deliberately absent: thousands of integers
        # are not something a caller should read. They become handles instead.
        kinds: dict[str, int] = {}
        for contact in self.contacts:
            kinds[contact.kind] = kinds.get(contact.kind, 0) + 1
        return {
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
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for i in np.flatnonzero(mask):
        if delta[i] <= 0:
            continue
        key = (str(array.chain_id[i]), int(array.res_id[i]))
        entry = out.setdefault(
            key,
            {
                "chain": key[0],
                "seq": key[1],
                "comp": str(array.res_name[i]),
                "buried": 0.0,
            },
        )
        entry["buried"] += float(delta[i])
    kept_keys = {k for k, r in out.items() if r["buried"] > INTERFACE_DELTA_SASA}
    keep = [r for k, r in out.items() if k in kept_keys]
    for r in keep:
        r["buried"] = round(r["buried"], 1)

    side = np.flatnonzero(mask)
    labels = np.char.add(
        np.char.add(array.chain_id[side].astype(str), "|"),
        array.res_id[side].astype(str),
    )
    wanted = [f"{chain}|{seq}" for chain, seq in kept_keys]
    indices = side[np.isin(labels, wanted)]

    return sorted(keep, key=lambda r: (-r["buried"], r["seq"])), indices


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


def interface(
    array: AtomArray[Any],
    chain_a: str,
    chain_b: str,
    contact_limit: int = 200,
    include_water: bool = False,
) -> InterfaceResult:
    """Describe the interface between two chains of one structure.

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
    # Dropping solvent renumbers the atoms, so keep the map back to the
    # caller's numbering: the indices we return have to mean something in the
    # array they handed us, not in this private copy.
    if include_water:
        origin_index = np.arange(array.array_length())
    else:
        origin_index = np.flatnonzero(~filter_solvent(array))
        array = array[origin_index]
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
        solvent=(
            "contacts include water; buried area is always solvent-free"
            if include_water
            else "excluded"
        ),
    )
