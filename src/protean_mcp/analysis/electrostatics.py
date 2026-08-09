"""Electrostatic potential: charge assignment, a screened-Coulomb field, APBS.

Two backends produce the same artifact — a potential grid in kT/e, writable as
OpenDX — so the viewer does not care which one ran and the two can be compared
directly on identical inputs.

``coulombic`` is the default and needs nothing installed. It sums screened
Coulomb contributions from every partial charge, with Debye-Hückel screening
for the salt. That is enough to see which face of a protein is acidic and
which is basic, which is what surface potential is overwhelmingly used for.

``apbs`` solves the Poisson-Boltzmann equation properly and is used when the
binary is present. The difference is not a detail: the Coulombic field assumes
one uniform dielectric everywhere, so it ignores the low-dielectric protein
interior and the reaction field at the solvent boundary — the very things PB
exists to model. Patterns and signs on the surface usually agree; magnitudes
do not, and no free energy should ever be derived from the Coulombic field.

Every result therefore carries the ``method`` that produced it. A number whose
provenance is unstated is the failure this module is written to avoid.
"""

from __future__ import annotations

import io
import logging
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from biotite.structure import AtomArray
from biotite.structure.io.pdb import PDBFile

logger = logging.getLogger(__name__)

# Coulomb's constant in kcal/mol . A . e^-2.
COULOMB_KCAL = 332.0637
# Boltzmann constant in kcal/(mol.K), for converting kcal/mol/e to kT/e.
BOLTZMANN_KCAL = 0.0019872041
# Bulk water at 298 K. The protein interior is nearer 2-4, which is precisely
# what the Coulombic approximation cannot represent.
SOLVENT_DIELECTRIC = 78.54
DEFAULT_TEMPERATURE = 298.15
# Physiological-ish salt, in mol/L.
DEFAULT_IONIC_STRENGTH = 0.15

# Grids get expensive fast: memory is 4 bytes x nx x ny x nz, and the cost of
# the sum is that times the atom count.
MAX_GRID_POINTS = 12_000_000
# A grid definition needs counts, an origin and three delta rows.
_DELTA_ROWS = 3
# APBS's largest supported mg-auto dimension.
_MAX_DIME = 545
# Chunk x atoms floats per block; 20k keeps a real protein near 100 MB.
_CHUNK = 20_000


class ElectrostaticsError(ValueError):
    """Raised when a potential cannot be computed as asked."""


@dataclass
class PreparedStructure:
    """Coordinates with charges and radii, as a PQR provides them."""

    coords: np.ndarray[Any, Any]
    charges: np.ndarray[Any, Any]
    radii: np.ndarray[Any, Any]
    pqr_text: str
    forcefield: str
    ph: float

    @property
    def net_charge(self) -> float:
        return float(self.charges.sum())

    def as_dict(self) -> dict[str, Any]:
        return {
            "atoms": len(self.charges),
            "net_charge": round(self.net_charge, 3),
            "forcefield": self.forcefield,
            "ph": self.ph,
        }


@dataclass
class PotentialGrid:
    """A scalar field on a regular grid, in kT/e."""

    origin: tuple[float, float, float]
    spacing: tuple[float, float, float]
    values: np.ndarray[Any, Any]
    method: str

    @property
    def shape(self) -> tuple[int, int, int]:
        nx, ny, nz = self.values.shape
        return int(nx), int(ny), int(nz)

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "units": "kT/e",
            "grid_shape": list(self.shape),
            "spacing": [round(s, 3) for s in self.spacing],
            "origin": [round(o, 3) for o in self.origin],
            "potential_min": round(float(self.values.min()), 3),
            "potential_max": round(float(self.values.max()), 3),
        }


def debye_length(
    ionic_strength: float,
    dielectric: float = SOLVENT_DIELECTRIC,
    temperature: float = DEFAULT_TEMPERATURE,
) -> float:
    """Debye screening length in angstrom; infinite at zero salt."""
    if ionic_strength <= 0:
        return float("inf")
    # 1/kappa = sqrt(eps.eps0.kB.T / (2.NA.e^2.I)); the constant folds all of
    # that at the given dielectric and temperature, with I in mol/L.
    reference = 3.04 * math.sqrt(dielectric / SOLVENT_DIELECTRIC)
    reference *= math.sqrt(temperature / DEFAULT_TEMPERATURE)
    return float(reference / math.sqrt(ionic_strength))


def _to_pdb_text(array: AtomArray[Any]) -> str:
    """Render the array as PDB, the only input pdb2pqr reads reliably.

    PDB cannot express more than 99,999 atoms or multi-character chain ids, so
    a large complex fails here rather than quietly losing atoms on the way to a
    potential that would still look plausible.
    """
    try:
        handle = PDBFile()
        handle.set_structure(array)
        buffer = io.StringIO()
        handle.write(buffer)
    except Exception as exc:
        raise ElectrostaticsError(
            f"Could not write this structure as PDB for charge assignment "
            f"({array.array_length()} atoms): {exc}"
        ) from exc
    return buffer.getvalue()


def parse_pqr(text: str) -> tuple[Any, Any, Any]:
    """Read coordinates, charges and radii out of a PQR.

    PQR is whitespace-delimited rather than column-fixed, and the two trailing
    fields are the charge and the radius. Reading them positionally from the
    end survives the atom-name and residue-name columns varying in width,
    which they do.
    """
    coords: list[list[float]] = []
    charges: list[float] = []
    radii: list[float] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        try:
            radius = float(fields[-1])
            charge = float(fields[-2])
            xyz = [float(v) for v in fields[-5:-2]]
        except (ValueError, IndexError):
            continue
        coords.append(xyz)
        charges.append(charge)
        radii.append(radius)
    if not coords:
        raise ElectrostaticsError(
            "The PQR contains no atoms. This is what a silently failed "
            "preparation looks like, so it is an error rather than an empty field."
        )
    return (
        np.asarray(coords, dtype=np.float64),
        np.asarray(charges, dtype=np.float64),
        np.asarray(radii, dtype=np.float64),
    )


def prepare(
    array: AtomArray[Any],
    ph: float = 7.0,
    forcefield: str = "AMBER",
    drop_water: bool = True,
) -> PreparedStructure:
    """Assign protonation, charges and radii with pdb2pqr.

    pdb2pqr is handed PDB text written from the array we already hold, not the
    original file: it misreads mmCIF badly enough to report a protein as
    nucleic acid and then write an empty PQR, and an empty PQR is a
    zero-everywhere potential that looks like a real answer.
    """
    # Imported lazily: pdb2pqr pulls in propka and a force-field table set,
    # which is a second of import time nothing else in protean pays for.
    from pdb2pqr.main import build_main_parser, main_driver  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="protean-pqr-") as tmp:
        work = Path(tmp)
        source = work / "input.pdb"
        source.write_text(_to_pdb_text(array))
        target = work / "output.pqr"

        options = [
            str(source),
            str(target),
            "--ff",
            forcefield,
            "--keep-chain",
            "--titration-state-method",
            "propka",
            "--with-ph",
            str(ph),
        ]
        if drop_water:
            options.append("--drop-water")
        try:
            # pdb2pqr logs a wall of per-atom warnings at WARNING level for any
            # real structure; they are not ours to relay.
            level = logging.getLogger("pdb2pqr").level
            logging.getLogger("pdb2pqr").setLevel(logging.ERROR)
            main_driver(build_main_parser().parse_args(options))
        except Exception as exc:
            raise ElectrostaticsError(
                f"pdb2pqr could not prepare the structure: {exc}"
            ) from exc
        finally:
            logging.getLogger("pdb2pqr").setLevel(level)

        if not target.is_file():
            raise ElectrostaticsError("pdb2pqr produced no PQR file")
        pqr_text = target.read_text()

    coords, charges, radii = parse_pqr(pqr_text)
    return PreparedStructure(
        coords=coords,
        charges=charges,
        radii=radii,
        pqr_text=pqr_text,
        forcefield=forcefield,
        ph=ph,
    )


def grid_axes(
    coords: Any, spacing: float, padding: float
) -> tuple[tuple[float, float, float], tuple[int, int, int]]:
    """Origin and point counts for a box enclosing *coords* plus *padding*."""
    lower = coords.min(axis=0) - padding
    upper = coords.max(axis=0) + padding
    counts = np.maximum(np.ceil((upper - lower) / spacing).astype(int) + 1, 2)
    total = int(np.prod(counts))
    if total > MAX_GRID_POINTS:
        raise ElectrostaticsError(
            f"That grid would be {counts.tolist()} = {total:,} points. "
            f"Increase spacing (currently {spacing} A) or reduce padding; "
            f"the limit is {MAX_GRID_POINTS:,}."
        )
    return (float(lower[0]), float(lower[1]), float(lower[2])), (
        int(counts[0]),
        int(counts[1]),
        int(counts[2]),
    )


def coulombic(  # noqa: PLR0913, PLR0917 - each argument is a physical parameter
    prepared: PreparedStructure,
    spacing: float = 1.0,
    padding: float = 10.0,
    dielectric: float = SOLVENT_DIELECTRIC,
    ionic_strength: float = DEFAULT_IONIC_STRENGTH,
    temperature: float = DEFAULT_TEMPERATURE,
) -> PotentialGrid:
    """Screened Coulomb potential on a grid, in kT/e.

    One uniform dielectric everywhere. See the module docstring for what that
    costs; the short version is that this shows you where the charge is, not
    what the solvation energy of it is.
    """
    origin, counts = grid_axes(prepared.coords, spacing, padding)
    axes = [
        origin[i] + spacing * np.arange(counts[i], dtype=np.float64) for i in range(3)
    ]
    points = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)

    kappa = (
        0.0
        if ionic_strength <= 0
        else 1.0 / debye_length(ionic_strength, dielectric, temperature)
    )
    # kcal/mol/e -> kT/e, so the scale matches what APBS writes.
    to_kt = COULOMB_KCAL / (dielectric * BOLTZMANN_KCAL * temperature)

    charged = prepared.charges != 0.0
    coords = prepared.coords[charged]
    charges = prepared.charges[charged]

    # |p - c|^2 expanded as |p|^2 + |c|^2 - 2 p.c, so the distance matrix comes
    # out of one BLAS matmul. Subtracting coordinates elementwise instead
    # materialises a (points, atoms, 3) array, which for a real protein is
    # gigabytes of memory traffic and an order of magnitude slower.
    points32 = points.astype(np.float32)
    coords32 = coords.astype(np.float32)
    charges32 = charges.astype(np.float32)
    atom_sq = (coords32**2).sum(axis=1)

    potential = np.empty(len(points), dtype=np.float32)
    for start in range(0, len(points), _CHUNK):
        block = points32[start : start + _CHUNK]
        squared = (block**2).sum(axis=1)[:, None] + atom_sq[None, :]
        squared -= 2.0 * (block @ coords32.T)
        # Rounding can drive a squared distance slightly negative at a point
        # sitting on an atom centre, and sqrt of that is a silent NaN.
        # Inside an atom the point-charge form diverges; flooring at 1 A keeps
        # the field finite without pretending it means anything there. The
        # interior is not where surface potential is read.
        np.maximum(squared, 1.0, out=squared)
        distance = np.sqrt(squared, out=squared)
        weights = np.exp(-kappa * distance)
        weights /= distance
        weights *= charges32
        potential[start : start + _CHUNK] = weights.sum(axis=1)

    return PotentialGrid(
        origin=origin,
        spacing=(spacing, spacing, spacing),
        values=(potential * to_kt).reshape(counts),
        method=(
            f"screened Coulomb (uniform dielectric {dielectric:g}, "
            f"ionic strength {ionic_strength:g} M) — not a Poisson-Boltzmann "
            "solution; magnitudes are indicative only"
        ),
    )


def write_dx(grid: PotentialGrid) -> str:
    """Serialise as OpenDX, which is what APBS writes and Mol* reads."""
    nx, ny, nz = grid.shape
    hx, hy, hz = grid.spacing
    ox, oy, oz = grid.origin
    header = [
        f"# Generated by protean ({grid.method})",
        f"object 1 class gridpositions counts {nx} {ny} {nz}",
        f"origin {ox:.6e} {oy:.6e} {oz:.6e}",
        f"delta {hx:.6e} 0.000000e+00 0.000000e+00",
        f"delta 0.000000e+00 {hy:.6e} 0.000000e+00",
        f"delta 0.000000e+00 0.000000e+00 {hz:.6e}",
        f"object 2 class gridconnections counts {nx} {ny} {nz}",
        f"object 3 class array type double rank 0 items {nx * ny * nz} data follows",
    ]
    # OpenDX runs the last axis fastest, which is the C order the array is
    # already in; writing it any other way transposes the field silently.
    flat = grid.values.ravel(order="C")
    body = [" ".join(f"{v:.6e}" for v in flat[i : i + 3]) for i in range(0, len(flat), 3)]
    footer = [
        'attribute "dep" string "positions"',
        'object "regular positions regular connections" class field',
        'component "positions" value 1',
        'component "connections" value 2',
        'component "data" value 3',
        "",
    ]
    return "\n".join([*header, *body, *footer])


def read_dx(text: str) -> PotentialGrid:
    """Read an OpenDX grid, so an APBS result comes back the same shape."""
    counts: tuple[int, int, int] | None = None
    origin: tuple[float, float, float] | None = None
    deltas: list[float] = []
    values: list[float] = []
    reading = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("object 1"):
            parts = stripped.split()
            counts = (int(parts[-3]), int(parts[-2]), int(parts[-1]))
        elif stripped.startswith("origin"):
            parts = stripped.split()
            origin = (float(parts[1]), float(parts[2]), float(parts[3]))
        elif stripped.startswith("delta"):
            deltas.append(max(abs(float(v)) for v in stripped.split()[1:]))
        elif stripped.startswith("object 3"):
            reading = True
        elif stripped.startswith(("attribute", "component", "object")):
            reading = False
        elif reading:
            values.extend(float(v) for v in stripped.split())

    if counts is None or origin is None or len(deltas) < _DELTA_ROWS:
        raise ElectrostaticsError("Malformed OpenDX: missing grid definition")
    expected = counts[0] * counts[1] * counts[2]
    if len(values) != expected:
        raise ElectrostaticsError(
            f"OpenDX declares {expected} points but carries {len(values)}; "
            "reshaping that would silently scramble the field"
        )
    return PotentialGrid(
        origin=origin,
        spacing=(deltas[0], deltas[1], deltas[2]),
        values=np.asarray(values, dtype=np.float64).reshape(counts),
        method="read from OpenDX",
    )


def apbs_binary() -> str | None:
    """The APBS executable, if one is both present and runnable.

    Presence is not enough: a package manager removing a shared library leaves
    a binary that exists and cannot start, which is how APBS was found on the
    machine this was written on.
    """
    found = shutil.which("apbs")
    if found is None:
        return None
    try:
        # Even `--version` writes an io.mc log into the working directory, so
        # the probe runs somewhere disposable. Asking whether a tool exists
        # must not leave a file in the user's repository.
        with tempfile.TemporaryDirectory(prefix="protean-apbs-probe-") as scratch:
            probe = subprocess.run(
                [found, "--version"],
                capture_output=True,
                timeout=20,
                check=False,
                cwd=scratch,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode != 0 and b"APBS" not in probe.stdout + probe.stderr:
        return None
    return found


APBS_TEMPLATE = """read
    mol pqr {pqr}
end
elec name solvated
    mg-auto
    dime {dime}
    cglen {cglen}
    fglen {fglen}
    cgcent mol 1
    fgcent mol 1
    mol 1
    lpbe
    bcfl sdh
    ion charge 1 conc {conc} radius 2.0
    ion charge -1 conc {conc} radius 2.0
    pdie 2.0
    sdie {sdie}
    srfm smol
    chgm spl2
    sdens 10.0
    srad 1.4
    swin 0.3
    temp {temp}
    calcenergy no
    calcforce no
    write pot dx {out}
end
quit
"""


def run_apbs(  # noqa: PLR0913, PLR0917 - each argument is a physical parameter
    prepared: PreparedStructure,
    spacing: float = 1.0,
    padding: float = 10.0,
    ionic_strength: float = DEFAULT_IONIC_STRENGTH,
    temperature: float = DEFAULT_TEMPERATURE,
    binary: str | None = None,
    timeout: float = 600,
) -> PotentialGrid:
    """Solve the linearised Poisson-Boltzmann equation with APBS."""
    executable = binary or apbs_binary()
    if executable is None:
        raise ElectrostaticsError(
            "No runnable APBS binary found. Install it (conda-forge has builds "
            "for linux-64, osx-64, osx-arm64 and win-64) or use method='coulombic'."
        )
    _, counts = grid_axes(prepared.coords, spacing, padding)
    extent = prepared.coords.max(axis=0) - prepared.coords.min(axis=0)
    lengths = extent + 2 * padding
    # mg-auto wants dime = c.2^(l+1)+1; 97 is the conventional workhorse value.
    dime = [_apbs_dime(int(n)) for n in counts]

    with tempfile.TemporaryDirectory(prefix="protean-apbs-") as tmp:
        work = Path(tmp)
        pqr = work / "structure.pqr"
        pqr.write_text(prepared.pqr_text)
        out = work / "potential"
        script = work / "apbs.in"
        script.write_text(
            APBS_TEMPLATE.format(
                pqr=pqr.name,
                dime=" ".join(str(d) for d in dime),
                cglen=" ".join(f"{v * 1.5:.3f}" for v in lengths),
                fglen=" ".join(f"{v:.3f}" for v in lengths),
                conc=ionic_strength,
                sdie=SOLVENT_DIELECTRIC,
                temp=temperature,
                out=out.name,
            )
        )
        try:
            completed = subprocess.run(
                [executable, script.name],
                cwd=work,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ElectrostaticsError(f"APBS timed out after {timeout}s") from exc

        produced = out.with_suffix(".dx")
        if completed.returncode != 0 or not produced.is_file():
            tail = completed.stderr.decode(errors="replace")[-600:]
            raise ElectrostaticsError(
                f"APBS exited {completed.returncode} without a potential map: {tail}"
            )
        grid = read_dx(produced.read_text())

    grid.method = (
        f"APBS linearised Poisson-Boltzmann (pdie 2.0, sdie {SOLVENT_DIELECTRIC:g}, "
        f"ionic strength {ionic_strength:g} M)"
    )
    return grid


def _apbs_dime(points: int) -> int:
    """Round up to APBS's required c.2^(l+1)+1 grid dimension."""
    candidate = 33
    while candidate < points and candidate < _MAX_DIME:
        candidate = (candidate - 1) * 2 + 1
    return candidate


def sample(grid: PotentialGrid, points: Any) -> Any:
    """Trilinearly interpolate the grid at arbitrary points, in kT/e.

    Points outside the box come back as NaN rather than clamped to the edge: a
    clamped value is a real number in the wrong place, which is the kind of
    answer that gets believed.
    """
    coords = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    origin = np.asarray(grid.origin)
    spacing = np.asarray(grid.spacing)
    shape = np.asarray(grid.shape)

    index = (coords - origin) / spacing
    lower = np.floor(index).astype(int)
    inside = np.all((lower >= 0) & (lower < shape - 1), axis=1)

    out = np.full(len(coords), np.nan)
    if not inside.any():
        return out

    base = lower[inside]
    frac = index[inside] - base
    total = np.zeros(len(base))
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                weight = (
                    (frac[:, 0] if dx else 1.0 - frac[:, 0])
                    * (frac[:, 1] if dy else 1.0 - frac[:, 1])
                    * (frac[:, 2] if dz else 1.0 - frac[:, 2])
                )
                total += (
                    weight
                    * grid.values[base[:, 0] + dx, base[:, 1] + dy, base[:, 2] + dz]
                )
    out[inside] = total
    return out
