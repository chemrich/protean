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
from biotite.structure import AtomArray, filter_amino_acids, superimpose_homologs
from biotite.structure.io.pdb import PDBFile
from biotite.structure.io.pdbx import CIFFile, get_structure

# A transform for a single model is 4x4; biotite returns a stack for multi-model
# input, which we index into.
_SINGLE_MATRIX_DIMS = 2


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

    def as_dict(self) -> dict[str, Any]:
        return {
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
        if fmt == "pdb":
            array = PDBFile.read(handle).get_structure(model=1)
        else:
            array = get_structure(CIFFile.read(handle), model=1)
    except Exception as exc:
        # Surface malformed coordinates as our own error rather than letting a
        # biotite exception type escape into the tool layer.
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
) -> SuperpositionResult:
    """Superpose *mobile* onto *target*, reporting the fit rather than just doing it.

    Correspondence comes from a sequence alignment of the two chains, so the
    structures need not have matching numbering — which is the usual case when
    comparing a mutant, a different species, or two crystal forms.
    """
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
    try:
        fitted, transform, target_anchors, mobile_anchors = superimpose_homologs(
            target, mobile
        )
    except Exception as exc:  # biotite raises several types for unalignable input
        raise SuperpositionError(f"Could not superpose: {exc}") from exc

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
    )
