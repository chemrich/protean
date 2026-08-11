"""Reading coordinate trajectories, on top of biotite.

PLAN's Phase 5 line said MDAnalysis. biotite already reads XTC, TRR, DCD and
NetCDF and is already a dependency, so MDAnalysis would be a large addition for
formats this does not need — see decision 14. What biotite does not bring is
topology parsing (Amber PRMTOP, GROMACS TPR) or the LAMMPS formats; a
trajectory here is coordinates laid onto a structure protean already has.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import numpy as np
from biotite.structure import stack as atom_stack


class TrajectoryError(Exception):
    """A trajectory could not be read, or does not match the structure."""


# A coordinate stack is (frames, atoms, xyz), and fluctuation needs more than
# one frame to be about anything.
_STACK_DIMENSIONS = 3
_MIN_RMSF_FRAMES = 2


# Extension to biotite reader. Each of these carries coordinates only, which is
# why every one of them needs a template to say what the atoms are.
_READERS: dict[str, str] = {
    ".xtc": "biotite.structure.io.xtc:XTCFile",
    ".trr": "biotite.structure.io.trr:TRRFile",
    ".dcd": "biotite.structure.io.dcd:DCDFile",
    ".nc": "biotite.structure.io.netcdf:NetCDFFile",
    ".netcdf": "biotite.structure.io.netcdf:NetCDFFile",
}


def supported_formats() -> list[str]:
    return sorted(_READERS)


def _reader(suffix: str) -> Any:
    try:
        module_name, class_name = _READERS[suffix].split(":")
    except KeyError:
        raise TrajectoryError(
            f"Cannot read {suffix!r} trajectories. Supported: "
            f"{', '.join(supported_formats())}"
        ) from None
    return getattr(importlib.import_module(module_name), class_name)


def read(path: str, template: Any, stride: int = 1, limit: int | None = None) -> Any:
    """Read *path* onto *template*, returning an AtomArrayStack.

    The atom counts have to agree exactly. A trajectory carries no atom names,
    so pairing it with the wrong structure produces coordinates that parse
    cleanly, animate smoothly, and describe nothing — the failure this check
    exists to make impossible rather than unlikely.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise TrajectoryError(f"No trajectory at {path!r}")
    if stride < 1:
        raise TrajectoryError(f"Stride must be 1 or more, got {stride}")

    reader = _reader(source.suffix.lower())
    try:
        opened = reader.read(str(source))
    except Exception as exc:
        raise TrajectoryError(f"{source.name} could not be read: {exc}") from exc

    coordinates = opened.get_coord()
    if coordinates is None or len(coordinates) == 0:
        raise TrajectoryError(f"{source.name} holds no frames")

    atoms = template.array_length()
    if coordinates.shape[1] != atoms:
        raise TrajectoryError(
            f"{source.name} has {coordinates.shape[1]} atoms per frame but the loaded "
            f"structure has {atoms}. A trajectory carries no atom names, so the two "
            "cannot be checked against each other beyond this — load the structure "
            "these coordinates belong to."
        )

    kept = coordinates[::stride]
    if limit is not None and len(kept) > limit:
        kept = kept[:limit]

    return _as_stack(template, np.asarray(kept, dtype=np.float32))


def _as_stack(template: Any, coordinates: Any) -> Any:
    frames = [template.copy() for _ in range(len(coordinates))]
    for frame, coord in zip(frames, coordinates, strict=True):
        frame.coord = coord
    return atom_stack(frames)


def rmsf(stack: Any) -> Any:
    """Per-atom root-mean-square fluctuation about the mean position.

    Computed here rather than pulled from a library because it is three lines
    of numpy over a coordinate stack, and the alternative was a dependency.
    Assumes the frames are already superposed; drift in the whole molecule
    otherwise swamps the per-atom motion it is meant to show.
    """
    coordinates = np.asarray(stack.coord, dtype=np.float64)
    if coordinates.ndim != _STACK_DIMENSIONS or len(coordinates) < _MIN_RMSF_FRAMES:
        raise TrajectoryError("RMSF needs at least two frames")
    mean = coordinates.mean(axis=0)
    squared = ((coordinates - mean) ** 2).sum(axis=2)
    return np.sqrt(squared.mean(axis=0))
