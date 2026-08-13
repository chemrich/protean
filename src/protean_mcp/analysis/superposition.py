"""Structural superposition with structured, reproducible results.

Runs entirely in Python on the fetched coordinates. That is deliberate: the
numbers a caller reasons over must be checkable without a browser, and Mol*'s
own analysis properties would tie every result to a rendering session. The
viewer's job is to display the transform this module returns, not to derive it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from biotite.structure import (
    AtomArray,
    filter_amino_acids,
    superimpose_homologs,
    superimpose_structural_homologs,
)
from biotite.structure.io.pdb import PDBFile
from biotite.structure.io.pdbx import CIFFile, get_structure

from ..selections_numpy import EXTRA_FIELDS, _normalise_altloc

# A transform for a single model is 4x4; biotite returns a stack for multi-model
# input, which we index into.
_SINGLE_MATRIX_DIMS = 2

MODES = ("sequence", "structural")


class SuperpositionError(ValueError):
    """Raised when two structures cannot be meaningfully superposed."""


@dataclass
class ResidueDeviation:
    """How far one aligned residue sits from its counterpart after fitting."""

    chain: str
    seq: int
    comp: str
    deviation: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "seq": self.seq,
            "comp": self.comp,
            "deviation": round(self.deviation, 3),
        }


@dataclass
class SuperpositionResult:
    rmsd: float
    aligned_residues: int
    sequence_identity: float
    transform: list[list[float]]
    mobile_chains: list[str]
    target_chains: list[str]
    outliers: list[ResidueDeviation]
    mode: str = "sequence"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "rmsd": round(self.rmsd, 3),
            "aligned_residues": self.aligned_residues,
            "sequence_identity": round(self.sequence_identity, 3),
            "transform": self.transform,
            "mobile_chains": self.mobile_chains,
            "target_chains": self.target_chains,
            "outliers": [d.as_dict() for d in self.outliers],
        }


def parse_structure(text: str, fmt: str) -> AtomArray[Any]:
    """Read one model out of mmCIF or PDB text."""
    handle = io.StringIO(text)
    if fmt not in ("pdb", "mmcif"):
        raise SuperpositionError(
            f"Unsupported format {fmt!r} (expected 'pdb' or 'mmcif')"
        )
    try:
        # `EXTRA_FIELDS` carries occupancy, which is what conformer resolution
        # ranks a site's alternates by. Without it `conformer_state` falls back
        # to `np.zeros`, every alternate ties, and the winner is decided by
        # sort order — the letter. Same bytes, same atom count, different
        # state: 5FJI resolves to `A+B` through the loader and `A` here.
        #
        # `altloc="all"` for the same reason it is in the loader: every
        # conformer is loaded and a state is resolved afterwards.
        #
        # **This is not parity with the loader, and an earlier version of this
        # comment claimed it was.** `get_structure` reads the asymmetric unit
        # while `load_structure` defaults to the biological assembly, so the
        # two still hold different molecules: 1HHO is 2396 atoms here against
        # 4792 loaded (2 copies), and 1AKE is 3816 here against 1966 loaded —
        # the assembly is *smaller* than the deposited coordinates. So
        # `interface("1hho", "A", "B")` standalone and `interface("A", "B")`
        # after `fetch_structure("1hho")` still describe different things, and
        # `sym_id` is absent here so `copy=N` refuses. Recorded rather than
        # fixed: making this call `load_structure` changes what `superpose`
        # superposes, which is a decision, not a repair.
        if fmt == "pdb":
            array = PDBFile.read(handle).get_structure(
                model=1, extra_fields=EXTRA_FIELDS, altloc="all"
            )
        else:
            array = get_structure(
                CIFFile.read(handle), model=1, extra_fields=EXTRA_FIELDS, altloc="all"
            )
        array = _normalise_altloc(array)
    except Exception as exc:
        # Surface malformed coordinates as our own error rather than letting a
        # biotite exception type escape into the tool layer.
        #
        # A PDB whose occupancy and B-factor columns are blank — some modelling
        # tools write them that way — fails here on "'' cannot be parsed into a
        # number", which names neither the column nor the file. `load_structure`
        # has always refused the same file with the same message, so this is a
        # limitation of the loader rather than one this parser adds; naming it
        # is what makes the two answerable at all.
        if "cannot be parsed into a number" in str(exc):
            raise SuperpositionError(
                f"Could not parse {fmt} coordinates: {exc}. If this file leaves "
                f"the occupancy or B-factor columns blank, fill them — occupancy "
                f"is what decides between alternate conformers, and guessing it "
                f"would pick a conformer without saying so."
            ) from exc
        raise SuperpositionError(f"Could not parse {fmt} coordinates: {exc}") from exc
    return array


def protein_atoms(array: AtomArray[Any], chain: str | None, label: str) -> AtomArray[Any]:
    """Amino-acid atoms, optionally narrowed to one chain."""
    selected = array[filter_amino_acids(array)]
    if chain is not None:
        selected = selected[selected.chain_id == chain]
        if selected.array_length() == 0:
            available = sorted(set(array.chain_id.tolist()))
            raise SuperpositionError(
                f"{label} has no protein in chain {chain!r}; chains present: "
                f"{', '.join(available)}"
            )
    if selected.array_length() == 0:
        raise SuperpositionError(f"{label} contains no amino acids to superpose")
    return selected


def superpose(
    mobile_text: str,
    mobile_format: str,
    target_text: str,
    target_format: str,
    mobile_chain: str | None = None,
    target_chain: str | None = None,
    outlier_limit: int = 20,
    mode: str = "sequence",
) -> SuperpositionResult:
    """Superpose *mobile* onto *target*, reporting the fit rather than just doing it.

    ``mode`` chooses how the two structures are put into correspondence, which
    is the whole question — the fitting itself is settled maths.

    "sequence" aligns the residue sequences and superposes the residues that
    align, discarding outliers. The structures need not share numbering, which
    is the usual case when comparing a mutant, a different species, or two
    crystal forms. It is the right choice whenever the two are the same protein.

    "structural" ignores the sequence and matches residues by the shape of
    their local backbone, so it finds a common substructure between proteins
    whose sequences have diverged past the point where aligning them means
    anything. It is the slower and more permissive of the two: it maximises how
    much it can superpose, so it will report more residues at a worse RMSD.
    """
    if mode not in MODES:
        raise SuperpositionError(f"Unknown mode {mode!r} ({', '.join(MODES)})")
    mobile = protein_atoms(
        parse_structure(mobile_text, mobile_format), mobile_chain, "mobile"
    )
    target = protein_atoms(
        parse_structure(target_text, target_format), target_chain, "target"
    )

    # biotite's generic signatures do not narrow here, so name the parts.
    fitted: Any
    transform: Any
    target_anchors: Any
    mobile_anchors: Any
    align = (
        superimpose_homologs if mode == "sequence" else superimpose_structural_homologs
    )
    try:
        fitted, transform, target_anchors, mobile_anchors = align(target, mobile)
    except Exception as exc:  # biotite raises several types for unalignable input
        raise SuperpositionError(f"Could not superpose ({mode} mode): {exc}") from exc

    if len(target_anchors) == 0:
        raise SuperpositionError(
            "No corresponding residues found; the two chains may be unrelated"
        )

    target_fit = target[target_anchors]
    mobile_fit = fitted[mobile_anchors]
    offsets = np.linalg.norm(target_fit.coord - mobile_fit.coord, axis=-1)
    rmsd = float(np.sqrt(np.mean(offsets**2)))

    identical = int(np.sum(target_fit.res_name == mobile_fit.res_name))
    identity = identical / len(target_anchors)

    # Report the worst-fitting residues: an RMSD alone hides whether the
    # disagreement is spread out or concentrated in one loop.
    order = np.argsort(offsets)[::-1][:outlier_limit]
    outliers = [
        ResidueDeviation(
            chain=str(mobile_fit.chain_id[i]),
            seq=int(mobile_fit.res_id[i]),
            comp=str(mobile_fit.res_name[i]),
            deviation=float(offsets[i]),
        )
        for i in order
    ]

    matrix = np.asarray(transform.as_matrix())
    if matrix.ndim > _SINGLE_MATRIX_DIMS:
        matrix = matrix[0]

    return SuperpositionResult(
        rmsd=rmsd,
        aligned_residues=len(target_anchors),
        sequence_identity=identity,
        transform=[[float(v) for v in row] for row in matrix],
        mobile_chains=sorted({str(c) for c in mobile.chain_id}),
        target_chains=sorted({str(c) for c in target.chain_id}),
        outliers=outliers,
        mode=mode,
    )
