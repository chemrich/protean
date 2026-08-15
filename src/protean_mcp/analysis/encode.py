"""Turning a directory of frames into a movie, with ffmpeg.

Mol* has no encoder, so this is where a frame sequence becomes something that
plays. ffmpeg is probed rather than assumed, in the same spirit as APBS: a tool
that is missing should say so once, clearly, rather than fail somewhere deeper.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

FRAME_PATTERN = "frame_%04d.png"

# H.264 in a .mp4 is what plays everywhere without a codec argument; GIF is what
# drops into a slide or an issue. WebM is neither, but it keeps transparency.
CONTAINERS = {".mp4", ".gif", ".webm"}
_CONTAINERS = CONTAINERS  # the private name this module already used


class EncodeError(Exception):
    """A movie could not be written."""


def ffmpeg_binary() -> str | None:
    """The ffmpeg executable, if one is present and actually runs.

    Presence alone is not enough — a broken install is a binary that exists and
    cannot start, which is exactly how APBS was found broken on this machine.
    """
    found = shutil.which("ffmpeg")
    if found is None:
        return None
    try:
        probe = subprocess.run(
            [found, "-version"], capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found if probe.returncode == 0 else None


def _require_ffmpeg() -> str:
    binary = ffmpeg_binary()
    if binary is None:
        raise EncodeError(
            "ffmpeg is needed to write a movie and was not found on PATH. "
            "Install it (brew install ffmpeg, apt install ffmpeg), or keep the "
            "frames and encode them elsewhere — they are ordinary PNGs."
        )
    return binary


def _arguments(output: Path, fps: int) -> list[str]:
    """The filters each container needs to produce something that plays.

    Both of these are the difference between a file that works everywhere and
    one that fails in a way nobody can diagnose from the error.
    """
    if output.suffix == ".gif":
        # A GIF built without a palette pass dithers against the default 216
        # colours and looks far worse than the frames it came from.
        return [
            "-vf",
            f"fps={fps},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse",
            "-loop",
            "0",
        ]
    if output.suffix == ".webm":
        return ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30"]
    return [
        # H.264 will not encode odd dimensions, and a figure-width frame is odd
        # about half the time. Cropping by a pixel beats refusing the render.
        "-vf",
        "crop=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        # QuickTime and PowerPoint will not play anything else, whatever the
        # encoder happily produces.
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
    ]


def encode(directory: str, output: str, fps: int = 30) -> dict[str, Any]:
    """Encode frame_%04d.png in *directory* into *output*."""
    if fps < 1:
        raise EncodeError(f"fps must be at least 1, got {fps}")

    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise EncodeError(f"No directory at {directory!r}")
    frames = sorted(folder.glob("frame_*.png"))
    if not frames:
        raise EncodeError(
            f"{folder} holds no frames named frame_0000.png upward. "
            "turntable() and record_trajectory() write that pattern."
        )

    target = Path(output).expanduser()
    if target.suffix.lower() not in _CONTAINERS:
        raise EncodeError(
            f"Cannot write {target.suffix or 'a file with no extension'!r}. "
            f"Supported: {', '.join(sorted(_CONTAINERS))}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)

    binary = _require_ffmpeg()
    command = [
        binary,
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(folder / FRAME_PATTERN),
        *_arguments(target, fps),
        str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EncodeError(f"ffmpeg could not be run: {exc}") from exc

    if result.returncode != 0 or not target.is_file():
        # ffmpeg says why on stderr and nowhere else; the tail is the part that
        # names the actual problem.
        tail = result.stderr.decode(errors="replace").strip().splitlines()[-6:]
        raise EncodeError("ffmpeg failed:\n" + "\n".join(tail))

    return {
        "path": str(target),
        "frames": len(frames),
        "fps": fps,
        "seconds": round(len(frames) / fps, 2),
        "bytes": target.stat().st_size,
    }
