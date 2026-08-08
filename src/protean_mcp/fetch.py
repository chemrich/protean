"""Structure fetching: local files, RCSB PDB, AlphaFold DB — with an on-disk cache."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

HTTP_NOT_FOUND = 404

PDB_ID_RE = re.compile(r"^[0-9][a-zA-Z0-9]{3}$")
# Standard UniProt accession pattern (6- and 10-char forms).
UNIPROT_RE = re.compile(
    r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$"
)

RCSB_URL = "https://files.rcsb.org/download/{pdb_id}.cif"
ALPHAFOLD_URL = "https://alphafold.ebi.ac.uk/files/AF-{accession}-F1-model_v4.cif"

_SUFFIX_FORMATS = {
    ".pdb": "pdb",
    ".ent": "pdb",
    ".cif": "mmcif",
    ".mmcif": "mmcif",
}


def default_cache_dir() -> Path:
    root = os.environ.get("PROTEAN_CACHE")
    base = Path(root) if root else Path.home() / ".cache" / "protean"
    return base / "structures"


@dataclass
class StructureData:
    name: str
    format: str  # "pdb" | "mmcif"
    data: str
    source: str  # "file" | "pdb" | "alphafold" | "cache"


class FetchError(RuntimeError):
    pass


async def fetch_structure_data(
    identifier: str,
    source: str = "auto",
    *,
    cache_dir: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> StructureData:
    """Resolve *identifier* to structure file content.

    Resolution order for ``source="auto"``: existing local path, 4-char PDB ID
    (RCSB), UniProt accession (AlphaFold DB).
    """
    identifier = identifier.strip()
    cache = cache_dir or default_cache_dir()

    if source not in ("auto", "file", "pdb", "alphafold"):
        raise FetchError(f"Unknown source '{source}'")

    if source in ("auto", "file"):
        path = Path(identifier).expanduser()
        if path.is_file():
            fmt = _SUFFIX_FORMATS.get(path.suffix.lower())
            if fmt is None:
                raise FetchError(
                    f"Unsupported file extension '{path.suffix}' "
                    f"(supported: {', '.join(_SUFFIX_FORMATS)})"
                )
            return StructureData(path.stem, fmt, path.read_text(), "file")
        if source == "file":
            raise FetchError(f"File not found: {identifier}")

    if source in ("auto", "pdb") and PDB_ID_RE.match(identifier):
        pdb_id = identifier.lower()
        data, cached = await _download_cached(
            RCSB_URL.format(pdb_id=pdb_id), cache / f"{pdb_id}.cif", transport
        )
        return StructureData(pdb_id, "mmcif", data, "cache" if cached else "pdb")

    if source in ("auto", "alphafold") and UNIPROT_RE.match(identifier.upper()):
        accession = identifier.upper()
        data, cached = await _download_cached(
            ALPHAFOLD_URL.format(accession=accession),
            cache / f"AF-{accession}.cif",
            transport,
        )
        return StructureData(
            f"AF-{accession}", "mmcif", data, "cache" if cached else "alphafold"
        )

    raise FetchError(
        f"Could not resolve '{identifier}': not an existing file, 4-character "
        f"PDB ID, or UniProt accession. Pass source='file'|'pdb'|'alphafold' "
        f"to disambiguate."
    )


async def _download_cached(
    url: str, cache_path: Path, transport: httpx.AsyncBaseTransport | None
) -> tuple[str, bool]:
    if cache_path.is_file():
        return cache_path.read_text(), True
    async with httpx.AsyncClient(
        transport=transport, follow_redirects=True, timeout=60
    ) as client:
        resp = await client.get(url)
        if resp.status_code == HTTP_NOT_FOUND:
            raise FetchError(f"Not found upstream: {url}")
        resp.raise_for_status()
        text = resp.text
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text)
    return text, False
