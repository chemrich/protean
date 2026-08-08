"""Evolutionary conservation: an MSA, per-position Shannon entropy, and a score.

The pipeline is the one from MCPymol's ``conservation_view``, with the display
half removed. Submit the chain's sequence to an MMseqs2 server (the ColabFold
public API by default), parse the A3M alignment, and score each position by
Shannon entropy over the amino acids observed there.

What changed in the port, and why:

  - The MSA is cached on disk rather than the entropies in memory. The search
    is the slow part — tens of seconds to minutes — and it is what deserves to
    survive a restart. Entropy over a parsed A3M is microseconds.
  - Scores come back keyed by *residue*, not as a bare list. A list is only
    meaningful if the caller reconstructs the same residue ordering we used,
    and structures have gaps, insertion codes and modified termini. Handing
    back the identity of the residue removes that class of mistake.
  - Nothing here touches the viewer. Conservation produces sets; the server
    turns them into handles, which are already colourable and composable.

A note on what entropy does and does not mean. It measures variability in a
particular alignment, so it depends on which homologs the search found: a
protein with few relatives scores as "conserved" everywhere because there is
nothing to disagree with it. ``msa_depth`` is reported for exactly that
reason, and a shallow alignment should be read as weak evidence rather than
strong conservation.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from biotite.structure import AtomArray, filter_amino_acids
from biotite.structure.info import one_letter_code

# ColabFold's public MMseqs2 API. Overridable so a local or self-hosted server
# can be used instead; the protocol is the same.
DEFAULT_MSA_URL = "https://api.colabfold.com"

_AA_ALPHABET = frozenset("ACDEFGHIKLMNPQRSTVWY")
_MAX_ENTROPY = math.log2(20)

# Below this a search is not worth submitting; the server rejects very short
# queries and an alignment of a fragment says nothing anyway.
MIN_SEQUENCE_LENGTH = 10
# An alignment this shallow cannot distinguish conserved from unstudied.
SHALLOW_MSA = 10
# One sequence is the query aligned to itself, which has no information in it.
MIN_MSA_DEPTH = 2

_SUBMIT_RETRIES = 3
_POLL_INTERVAL = 5.0
_POLL_ATTEMPTS = 120


class ConservationError(ValueError):
    """Raised when conservation cannot be computed as asked."""


def msa_url() -> str:
    return os.environ.get("PROTEAN_MSA_URL", DEFAULT_MSA_URL)


@dataclass
class ResidueScore:
    """One residue's conservation, identified well enough to act on."""

    chain: str
    seq: int
    ins_code: str
    comp: str
    entropy: float
    conservation: float

    def as_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "chain": self.chain,
            "seq": self.seq,
            "comp": self.comp,
            "entropy": round(self.entropy, 3),
            "conservation": round(self.conservation, 3),
        }
        if self.ins_code.strip():
            entry["ins_code"] = self.ins_code.strip()
        return entry


@dataclass
class ConservationResult:
    chain: str
    sequence: str
    msa_depth: int
    scores: list[ResidueScore]
    source: str

    def as_dict(self, limit: int = 0) -> dict[str, Any]:
        entropies = [s.entropy for s in self.scores]
        listed = self.scores if limit <= 0 else self.scores[:limit]
        out: dict[str, Any] = {
            "chain": self.chain,
            "residues_scored": len(self.scores),
            "sequence_length": len(self.sequence),
            "msa_depth": self.msa_depth,
            "entropy_min": round(min(entropies), 3) if entropies else None,
            "entropy_max": round(max(entropies), 3) if entropies else None,
            "source": self.source,
            "residues": [s.as_dict() for s in listed],
            "truncated": len(listed) < len(self.scores),
        }
        if self.msa_depth < SHALLOW_MSA:
            out["warning"] = (
                f"Only {self.msa_depth} sequences aligned. Conservation from a "
                "shallow alignment reflects how few homologs were found, not "
                "how constrained the protein is."
            )
        return out


def chain_sequence(
    array: AtomArray[Any], chain: str
) -> tuple[str, list[tuple[str, int, str, str]]]:
    """The one-letter sequence of a chain's amino acids, and which residues.

    The residue list is returned alongside the sequence rather than
    reconstructed later: alignment position *i* means residue *i* of this list,
    and that correspondence is the only thing tying scores to atoms.
    """
    mask = np.asarray(filter_amino_acids(array)) & (array.chain_id == chain)
    if not mask.any():
        chains = ", ".join(sorted({str(c) for c in array.chain_id}))
        raise ConservationError(
            f"No amino acids in chain {chain!r}; chains present: {chains}"
        )
    subset = array[mask]
    letters: list[str] = []
    residues: list[tuple[str, int, str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for i in range(subset.array_length()):
        key = (
            str(subset.chain_id[i]),
            int(subset.res_id[i]),
            str(subset.ins_code[i]),
        )
        if key in seen:
            continue
        seen.add(key)
        comp = str(subset.res_name[i])
        code = one_letter_code(comp)
        # A modified or unknown residue still occupies an alignment column;
        # dropping it would shift every score after it onto the wrong residue.
        letters.append((code or "X").upper())
        residues.append((*key, comp))
    return "".join(letters), residues


def parse_a3m(a3m_text: str) -> list[list[str]]:
    """Parse an A3M alignment into columns aligned with the query.

    A3M marks insertions relative to the query with lower case. Stripping them
    is what makes column *i* the same position in every sequence.
    """
    sequences: list[list[str]] = []
    current: list[str] = []
    for line in a3m_text.splitlines():
        if line.startswith(">"):
            if current:
                sequences.append([c for c in "".join(current) if not c.islower()])
            current = []
        elif line.strip():
            current.append(line.strip())
    if current:
        sequences.append([c for c in "".join(current) if not c.islower()])
    return sequences


def shannon_entropy(msa: list[list[str]]) -> list[float]:
    """Per-position Shannon entropy, normalised to [0, 1].

    0 is perfectly conserved, 1 is maximum variability. Gaps and non-standard
    characters are excluded from the count rather than treated as a 21st
    residue: a column half of whose sequences are simply absent is not thereby
    variable.
    """
    if not msa:
        return []
    entropies: list[float] = []
    for col in range(len(msa[0])):
        counts: dict[str, int] = {}
        total = 0
        for seq in msa:
            if col >= len(seq):
                continue
            residue = seq[col].upper()
            if residue in _AA_ALPHABET:
                counts[residue] = counts.get(residue, 0) + 1
                total += 1
        if total == 0:
            entropies.append(1.0)
            continue
        entropy = -sum(
            (n / total) * math.log2(n / total) for n in counts.values() if n > 0
        )
        # An invariant column sums to exactly zero and negates to -0.0. That is
        # numerically fine and reads as a mistake in a report, so normalise it.
        entropies.append(entropy / _MAX_ENTROPY + 0.0)
    return entropies


def _cache_path(sequence: str, mode: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(f"{mode}\n{sequence}".encode()).hexdigest()[:32]
    return cache_dir / "msa" / f"{digest}.a3m"


async def fetch_msa(  # noqa: PLR0913 - each argument is a distinct axis
    sequence: str,
    cache_dir: Path,
    *,
    use_env: bool = True,
    force_refresh: bool = False,
    server_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, str]:
    """Get an A3M alignment for *sequence*, from cache or the MMseqs2 server.

    Returns the alignment and where it came from. Submit, poll, download —
    the ColabFold API hands back a tar.gz of results, of which the .a3m
    members are what we want.
    """
    if len(sequence) < MIN_SEQUENCE_LENGTH:
        raise ConservationError(
            f"Sequence is {len(sequence)} residues; at least "
            f"{MIN_SEQUENCE_LENGTH} are needed for a meaningful alignment"
        )
    mode = "env" if use_env else "all"
    path = _cache_path(sequence, mode, cache_dir)
    if path.is_file() and not force_refresh:
        return path.read_text(), "cache"

    host = (server_url or msa_url()).rstrip("/")
    async with httpx.AsyncClient(transport=transport, timeout=60) as client:
        ticket = await _submit(client, host, sequence, mode)
        await _await_completion(client, host, ticket)
        a3m = await _download(client, host, ticket)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(a3m)
    return a3m, "search"


async def _submit(client: httpx.AsyncClient, host: str, sequence: str, mode: str) -> str:
    payload = {"q": f">query\n{sequence}\n", "mode": mode}
    last: Exception | None = None
    for attempt in range(_SUBMIT_RETRIES):
        try:
            response = await client.post(f"{host}/ticket/msa", data=payload)
            response.raise_for_status()
            ticket = response.json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            await asyncio.sleep(2**attempt)
    else:
        raise ConservationError(
            f"Could not submit the MSA search to {host} after "
            f"{_SUBMIT_RETRIES} attempts: {last}"
        )
    identifier = ticket.get("id")
    if not identifier:
        raise ConservationError(f"{host} returned no ticket id: {ticket}")
    return str(identifier)


async def _await_completion(client: httpx.AsyncClient, host: str, ticket: str) -> None:
    for _ in range(_POLL_ATTEMPTS):
        try:
            response = await client.get(f"{host}/ticket/{ticket}")
            status = response.json().get("status")
        except (httpx.HTTPError, ValueError):
            await asyncio.sleep(_POLL_INTERVAL)
            continue
        if status == "COMPLETE":
            return
        if status == "ERROR":
            raise ConservationError(f"The MSA search failed on {host}: {ticket}")
        await asyncio.sleep(_POLL_INTERVAL)
    raise ConservationError(
        f"The MSA search did not finish within "
        f"{int(_POLL_ATTEMPTS * _POLL_INTERVAL / 60)} minutes"
    )


async def _download(client: httpx.AsyncClient, host: str, ticket: str) -> str:
    response = await client.get(f"{host}/result/download/{ticket}")
    response.raise_for_status()
    chunks: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.name.endswith(".a3m"):
                continue
            handle = tar.extractfile(member)
            if handle is not None:
                chunks.append(handle.read().decode("utf-8"))
    if not chunks:
        raise ConservationError("The MSA search returned no alignment")
    return "".join(chunks)


def score(
    array: AtomArray[Any], chain: str, a3m_text: str, source: str = "search"
) -> ConservationResult:
    """Turn an alignment into per-residue conservation for one chain."""
    sequence, residues = chain_sequence(array, chain)
    msa = parse_a3m(a3m_text)
    if len(msa) < MIN_MSA_DEPTH:
        raise ConservationError(
            f"The alignment contains {len(msa)} sequence(s); at least two are "
            "needed to say anything about conservation"
        )
    entropies = shannon_entropy(msa)
    if len(entropies) < len(residues):
        raise ConservationError(
            f"The alignment is {len(entropies)} columns but chain {chain!r} has "
            f"{len(residues)} residues; scores would be assigned to the wrong "
            "residues, so none are returned"
        )

    scores = [
        ResidueScore(
            chain=key[0],
            seq=key[1],
            ins_code=key[2],
            comp=key[3],
            entropy=entropies[i],
            conservation=1.0 - entropies[i],
        )
        for i, key in enumerate(residues)
    ]
    return ConservationResult(
        chain=chain,
        sequence=sequence,
        msa_depth=len(msa),
        scores=scores,
        source=source,
    )
