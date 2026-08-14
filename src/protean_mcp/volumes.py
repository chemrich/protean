"""Getting a density map from a file into the viewer.

protean could parse exactly one volume format before this — OpenDX, because
that is what APBS writes — while Mol\\* registers providers for ``ccp4``,
``dsn6``, ``dx``, ``cube``, ``dscif`` and ``segcif``. Anyone showing a cryo-EM
reconstruction or a 2Fo-Fc map hit that gap.

**Two things this module does not do.** It does not read the MRC header, and it
does not decide a contour level. Mol\\* parses the volume and reports its own
statistics, so the reply describes what the viewer actually holds rather than
what we thought we sent — the same rule the rest of this codebase follows, and
it matters more here because a volume has several ways to arrive as nothing
while every call returns cleanly.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path

#: Mol\* format id by file extension. The names are Mol\*'s own, because they
#: are what ``plugin.dataFormats.get()`` is keyed on.
_BY_SUFFIX = {
    ".ccp4": "ccp4",
    ".map": "ccp4",
    ".mrc": "ccp4",
    ".dsn6": "dsn6",
    ".omap": "dsn6",
    ".dx": "dx",
    ".cube": "cube",
    ".cub": "cube",
    ".bcif": "dscif",
}

#: MRC/CCP4 stamps its format into bytes 208-212. Checked before the extension
#: because ``.map`` is also used for other things and a mislabelled file should
#: be recognised rather than handed to the wrong parser.
_MRC_MAGIC = b"MAP "
_MRC_MAGIC_OFFSET = 208

FORMATS = sorted(set(_BY_SUFFIX.values()))


class VolumeError(ValueError):
    """The file is not a volume this viewer can parse."""


@dataclass(frozen=True)
class Volume:
    """A volume ready to hand to the viewer."""

    data: bytes
    format: str
    #: Whether the source was gzipped. Worth reporting: EMDB ships ``.map.gz``
    #: and a user who did not notice will otherwise wonder why the size on
    #: disk and the size loaded disagree.
    was_compressed: bool
    source: Path


def _sniff_format(head: bytes, path: Path) -> str | None:
    """Identify the format from the bytes, then from the extension."""
    if (
        len(head) >= _MRC_MAGIC_OFFSET + 4
        and head[_MRC_MAGIC_OFFSET : _MRC_MAGIC_OFFSET + 4] == _MRC_MAGIC
    ):
        return "ccp4"
    # OpenDX and Gaussian cube are text, and both announce themselves.
    text = head[:512].lstrip()
    if text.startswith(b"#") and b"object" in head[:2048]:
        return "dx"

    suffixes = [s.lower() for s in path.suffixes]
    for suffix in reversed(suffixes):
        if suffix in _BY_SUFFIX:
            return _BY_SUFFIX[suffix]
    return None


def read_volume(path: str | Path, format: str = "auto") -> Volume:
    """Read a volume file, decompressing it if it is gzipped.

    EMDB distributes ``emd_XXXXX.map.gz`` and that is the common case, not an
    edge one — so gzip is handled here rather than left to the caller to
    notice. The decompressed bytes are what the viewer is given, because Mol\\*
    parses volumes from a buffer and does not decompress.

    Args:
        path: The file to read.
        format: A Mol\\* format id, or ``"auto"`` to detect. Detection reads the
            MRC magic first and falls back to the extension.

    Raises:
        VolumeError: the file is missing, or its format cannot be identified.
    """
    path = Path(path)
    if not path.is_file():
        raise VolumeError(f"{path} is not a file")

    raw = path.read_bytes()
    was_compressed = raw[:2] == b"\x1f\x8b"
    if was_compressed:
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise VolumeError(
                f"{path} looks gzipped but will not decompress: {exc}"
            ) from exc

    # Before detection, not after: an empty file — or a valid gzip of nothing —
    # would otherwise be reported as "cannot tell what format this is", which
    # sends the caller looking for the wrong problem entirely.
    if not raw:
        raise VolumeError(f"{path} is empty")

    if format != "auto":
        if format not in FORMATS:
            raise VolumeError(f"unknown format {format!r}. Known: {', '.join(FORMATS)}")
        detected = format
    else:
        # Sniff the *decompressed* bytes: emd_30913.map.gz has suffixes
        # ['.map', '.gz'], and the magic is only there once unwrapped.
        found = _sniff_format(raw, Path(path.name.removesuffix(".gz")))
        if found is None:
            raise VolumeError(
                f"cannot tell what format {path.name} is. Its extension is not one "
                f"of {', '.join(sorted(_BY_SUFFIX))} and it carries no MRC magic. "
                f"Pass format= explicitly if you know what it is."
            )
        detected = found

    return Volume(data=raw, format=detected, was_compressed=was_compressed, source=path)


__all__ = ["FORMATS", "Volume", "VolumeError", "read_volume"]
