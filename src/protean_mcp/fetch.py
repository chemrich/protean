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
#: Where the database says a prediction lives. Asked rather than assumed.
#:
#: The URL used to be built here as
#: `.../files/AF-{accession}-F1-model_v4.cif`, and that was wrong twice over.
#: v4 was retired — every AlphaFold fetch had been failing with "Not found
#: upstream", which reads as "no such protein" rather than "protean is asking
#: for a file that no longer exists". Bumping the number to v6 would have fixed
#: most accessions until the next release.
#:
#: It would not have fixed all of them, and that is the reason this asks
#: instead. **The template is wrong in shape, not only in version.** P0DTC2 —
#: the SARS-CoV-2 spike — is served as
#: `AF-0000000365840314-model_v1.cif`: an internal numeric id, no `-F1`
#: fragment, and version 1 while its neighbours are on 6. No version of the
#: old pattern can produce that, so the backlog's reading that some accessions
#: are "genuinely absent" was itself a consequence of the bug.
ALPHAFOLD_API = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"

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
        cache_path = cache / f"AF-{accession}.cif"
        if cache_path.is_file():
            return StructureData(
                f"AF-{accession}", "mmcif", cache_path.read_text(), "cache"
            )
        # The extra request only happens on a cache miss, and it is what keeps
        # this from going stale on the database's release schedule.
        url = await _alphafold_url(accession, transport)
        data, cached = await _download_cached(url, cache_path, transport)
        return StructureData(
            f"AF-{accession}", "mmcif", data, "cache" if cached else "alphafold"
        )

    raise FetchError(
        f"Could not resolve '{identifier}': not an existing file, 4-character "
        f"PDB ID, or UniProt accession. Pass source='file'|'pdb'|'alphafold' "
        f"to disambiguate."
    )


async def _alphafold_url(
    accession: str, transport: httpx.AsyncBaseTransport | None
) -> str:
    """Ask the database where this prediction's mmCIF is.

    Returns the `cifUrl` the API reports, which carries the current version and
    whatever id scheme that entry happens to use. See ALPHAFOLD_API for why
    this is a request rather than a format string.
    """
    async with httpx.AsyncClient(
        transport=transport, follow_redirects=True, timeout=60
    ) as client:
        resp = await client.get(ALPHAFOLD_API.format(accession=accession))
        if resp.status_code == HTTP_NOT_FOUND:
            raise FetchError(
                f"AlphaFold DB has no prediction for {accession}. The accession "
                f"may be obsolete, or the protein may not be in the database."
            )
        resp.raise_for_status()
        try:
            entries = resp.json()
        except ValueError as exc:
            raise FetchError(
                f"AlphaFold DB returned something that is not JSON for {accession}."
            ) from exc

    # A list, and empty is a real answer the API gives rather than a 404.
    if not isinstance(entries, list) or not entries:
        raise FetchError(f"AlphaFold DB has no prediction for {accession}.")
    first = entries[0]
    url = first.get("cifUrl") if isinstance(first, dict) else None
    if not url:
        reported = sorted(first) if isinstance(first, dict) else type(first).__name__
        raise FetchError(
            f"AlphaFold DB listed {accession} but gave no mmCIF URL. "
            f"It reported: {reported}"
        )
    return str(url)


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
