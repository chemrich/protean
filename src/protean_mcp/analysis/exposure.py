"""How much of each residue the solvent can reach, and how deep the rest sits.

Two numbers from one Shrake-Rupley pass, because they answer the two halves of
the same question and computing them separately would run it twice.

**Area** is what the rolling probe reaches, in square angstroms. It is the
honest primary number and it is almost never the one you want to draw: a
tryptophan with 60 A^2 exposed is *buried*, and a glycine with 60 A^2 is
wide open, because the two have very different amounts of surface to begin
with.

**Relative exposure** divides that by the most the residue could possibly
have, so it lands in 0..1 and means the same thing for every residue type.
That is the number to colour by.

**Depth** is how far a residue sits from the surface, in angstroms. It is a
*proxy* and the docstring for `residue_exposure` says which one: distance to
the nearest solvent-reachable atom, not distance to a solvent-excluded
surface. They agree closely near the surface and diverge in a large interior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from biotite.structure import AtomArray, filter_solvent, get_residue_starts, sasa

__all__ = [
    "BURIED_BELOW",
    "EXPOSED_ABOVE",
    "MAX_ASA",
    "Exposure",
    "ExposureError",
    "residue_exposure",
]

#: Most surface a residue of each type can have, in A^2 — Tien et al. 2013,
#: the theoretical set, measured on Gly-X-Gly tripeptides.
#:
#: The theoretical values rather than the empirical ones, deliberately: the
#: empirical maxima come from a survey of real structures and are therefore
#: already slightly buried, which biases every relative number upward.
#:
#: **Relative exposure can still exceed 1**, and a caller drawing it needs to
#: know that rather than discover it. A terminal residue has surface a
#: Gly-X-Gly tripeptide does not — measured on 1UBQ, the C-terminal GLY76
#: comes out at 1.42. Clamping would be worse: it would report the single most
#: exposed residue in the structure as merely typical.
MAX_ASA: dict[str, float] = {
    "ALA": 129.0,
    "ARG": 274.0,
    "ASN": 195.0,
    "ASP": 193.0,
    "CYS": 167.0,
    "GLU": 223.0,
    "GLN": 225.0,
    "GLY": 104.0,
    "HIS": 224.0,
    "ILE": 197.0,
    "LEU": 201.0,
    "LYS": 236.0,
    "MET": 224.0,
    "PHE": 240.0,
    "PRO": 159.0,
    "SER": 155.0,
    "THR": 172.0,
    "TRP": 285.0,
    "TYR": 263.0,
    "VAL": 174.0,
}

#: An atom the probe reached at all. Not zero: Shrake-Rupley samples a point
#: mesh, so a fully buried atom can come back with a hundredth of an angstrom
#: of area from a single grazing point, and calling that "on the surface"
#: puts the nearest-surface distance at zero for half the core.
_REACHED = 0.1

#: The conventional relative-exposure cutoffs for calling a residue buried or
#: exposed. There is no natural boundary — burial is continuous — so these are
#: reporting conveniences and the raw numbers are always returned beside them.
#: 0.25 is the usual single threshold in the literature; splitting it into two
#: leaves the middle band unclaimed rather than forcing every residue to a side.
BURIED_BELOW = 0.1
EXPOSED_ABOVE = 0.4


def _clean(
    raw: np.ndarray[Any, Any], radii: str
) -> tuple[np.ndarray[Any, Any], str, np.ndarray[Any, Any]]:
    """Zeroed areas, the radius set, and which atoms had a radius at all.

    The third is the part that has to survive: `nan_to_num` is right for
    summing an area, and it destroys the only record of which atoms biotite
    could not measure — which is exactly what the depth pass needs to exclude.
    """
    return np.nan_to_num(raw), radii, ~np.isnan(raw)


def _areas(
    kept: AtomArray[Any], probe_radius: float
) -> tuple[np.ndarray[Any, Any], str, np.ndarray[Any, Any]]:
    """Per-atom reachable area, and which radius set produced it.

    biotite's default ProtOr radii are per (residue, atom) pairs looked up in
    the chemical component dictionary, and they **raise** rather than return
    nothing for a pair they do not hold: a ligand the dictionary has never
    seen, or a residue whose atoms have been renamed, takes the whole
    calculation down with `KeyError: Residue 'LIG' does not contain an atom
    named 'N'`. Found by building a fixture with an invented ligand, which is
    a thing real files contain.

    The fallback is element-based radii, which exist for everything. It is
    applied to the **whole structure** rather than to the offending residues:
    mixing two radius sets inside one calculation would leave residues that
    cannot be compared with each other, and comparability is the entire point
    of a relative number.
    """
    try:
        return _clean(sasa(kept, probe_radius=probe_radius), "protor")
    except (KeyError, ValueError) as exc:
        # KeyError for a residue/atom pair the dictionary does not hold.
        # ValueError for an atom whose *name* begins with H, which ProtOr
        # refuses before it looks anything up — and biotite filters hydrogens
        # by *element*, so an atom named HB1 with a blank element column, or an
        # organomercury ligand whose atom is named HG, walks straight into it.
        if "occlusion filter" in str(exc):
            raise ExposureError(
                "Nothing here has a van der Waals radius to roll a probe "
                "against — a selection of only ions or only hydrogens has no "
                "surface to measure."
            ) from exc
        return _clean(sasa(kept, probe_radius=probe_radius, vdw_radii="Single"), "single")


def _nearest_distance(
    points: np.ndarray[Any, Any], targets: np.ndarray[Any, Any], chunk: int = 512
) -> np.ndarray[Any, Any]:
    """Distance from each point to its nearest target, in chunks.

    A KD-tree would be the obvious tool and scipy is **not a declared
    dependency** — it arrives transitively today, and a tool that leans on
    that breaks on somebody else's release schedule with no line of protean
    changing. This project has been bitten by exactly that once already.

    Chunked on **both** axes. Chunking rows alone bounds the row count and not
    the memory — the temporary is chunk x len(targets) x 3, which measured 172
    MB at 12k targets and would be well over a gigabyte on a 200k-atom
    assembly. Squared distances throughout, with one square root at the end,
    because sqrt of the whole matrix is the other half of that allocation.

    Time still grows with the product of the two lengths, which is the honest
    cost of not having a tree: seconds at 20k points, minutes at 100k.
    """
    best = np.full(len(points), np.inf, dtype=np.float64)
    for start in range(0, len(points), chunk):
        block = points[start : start + chunk]
        for other in range(0, len(targets), chunk):
            wedge = targets[other : other + chunk]
            gaps = block[:, None, :] - wedge[None, :, :]
            np.minimum(
                best[start : start + chunk],
                (gaps * gaps).sum(axis=2).min(axis=1),
                out=best[start : start + chunk],
            )
    return np.sqrt(best)


@dataclass(frozen=True)
class Exposure:
    """Per-residue rows, and which radius set produced them.

    The radius set is not a detail: one ligand the dictionary has never seen
    switches the whole structure to element radii, and that moves every number
    — measured on 1L2Y, adding a two-atom unknown 30 A away moved residue 1
    from 0.8101 to 0.8472. A caller comparing two structures needs to know
    they were measured the same way.
    """

    residues: list[dict[str, Any]]
    radii: str


class ExposureError(ValueError):
    """Raised when there is no surface to measure at all."""


def residue_exposure(array: AtomArray[Any], probe_radius: float = 1.4) -> Exposure:
    """Per-residue area, relative exposure and depth, in residue order.

    Solvent is removed first. Waters sit *on* the surface, so leaving them in
    reports a protein with almost none — the crystallographer's waters would
    be counted as the thing doing the burying.

    `relative` is None for anything with no reference maximum: ligands, ions,
    nucleotides and non-standard residues have no Gly-X-Gly value, and
    inventing one would put a number on the picture that means nothing. The
    area and the depth are still reported for those.

    `depth_a` is the distance from the residue to the nearest atom the probe
    reached, averaged over its atoms — **not** the distance to a
    solvent-excluded surface, which needs a surface pass this does not do. A
    residue with any reachable atom of its own is at 0.
    """
    kept = array[~filter_solvent(array)]
    if kept.array_length() == 0:
        return Exposure(residues=[], radii="none")

    # `carries_radius` is the atoms biotite could measure at all. Zeroed areas
    # are right for summing — no radius, no reachable area — and wrong for the
    # depth pass, which must not treat an unmeasurable atom as buried.
    areas, radii, carries_radius = _areas(kept, probe_radius)
    reached = areas > _REACHED
    coords = kept.coord

    depths = np.full(len(coords), np.nan)
    if reached.any():
        # Measured only over atoms that have a radius. A hydrogen is never
        # `reached`, so with hydrogens in the query set every residue in a
        # protonated file carried its own H-to-heavy-atom distance into the
        # mean: on 1L2Y, ASN1 is 81% exposed and read 0.52 A deep with
        # hydrogens present against 0.0 with them stripped. That contradicts
        # the promise one paragraph up, on exactly the NMR and MD structures
        # the trajectory tools load.
        measurable = np.flatnonzero(carries_radius)
        if measurable.size:
            depths[measurable] = _nearest_distance(coords[measurable], coords[reached])

    out: list[dict[str, Any]] = []
    starts: np.ndarray[Any, Any] = get_residue_starts(kept)
    for start, stop in zip(starts, np.r_[starts[1:], kept.array_length()], strict=True):
        resn = str(kept.res_name[start])
        area = float(areas[start:stop].sum())
        reference = MAX_ASA.get(resn)
        span = depths[start:stop]
        depth = float(np.nanmean(span)) if not np.all(np.isnan(span)) else float("nan")
        ins = str(kept.ins_code[start]).strip()
        out.append(
            {
                "chain": str(kept.chain_id[start]),
                "seq": int(kept.res_id[start]),
                "resn": resn,
                "area_a2": round(area, 2),
                "relative": None if reference is None else round(area / reference, 4),
                "depth_a": None if np.isnan(depth) else round(depth, 2),
                # Carried only when there is one, which is what every other
                # producer in this repo does. Emitting `None` instead put the
                # literal string "None" into the residue key `define_field`
                # builds — `A|76|None` against the viewer's `A|76|` — so the
                # exact call this tool's docstring recommends matched zero
                # residues on every structure without insertion codes.
                **({"ins_code": ins} if ins else {}),
            }
        )
    return Exposure(residues=out, radii=radii)
