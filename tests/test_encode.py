"""Encoding frames into a movie.

The parts worth testing are not "does ffmpeg work" but the choices around it:
that a missing encoder is reported once and clearly, that each container gets
the arguments it needs to produce a file that actually plays, and that a
directory with nothing in it does not quietly produce a zero-frame movie.

Tests that run ffmpeg are skipped when it is absent, which is what any machine
without it should experience.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from protean_mcp.analysis.encode import EncodeError, encode, ffmpeg_binary

needs_ffmpeg = pytest.mark.skipif(
    ffmpeg_binary() is None, reason="ffmpeg is not installed"
)


def _frames(directory: Path, count: int = 6, size=(64, 48)) -> Path:
    """A sequence that visibly changes, so an encoder cannot collapse it."""
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        shade = 20 + index * 30
        Image.new("RGB", size, (shade, 40, 200 - shade)).save(
            directory / f"frame_{index:04d}.png"
        )
    return directory


def test_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(EncodeError, match="No directory at"):
        encode(str(tmp_path / "absent"), str(tmp_path / "out.mp4"))


def test_a_directory_with_no_frames_says_what_it_wanted(tmp_path):
    """Otherwise this is a zero-frame movie, which ffmpeg reports obscurely."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(EncodeError, match=r"no frames named frame_0000\.png"):
        encode(str(empty), str(tmp_path / "out.mp4"))


def test_an_unknown_container_is_refused_before_anything_runs(tmp_path):
    frames = _frames(tmp_path / "frames")
    with pytest.raises(EncodeError, match=r"Supported: \.gif, \.mp4, \.webm"):
        encode(str(frames), str(tmp_path / "out.avi"))


def test_a_zero_frame_rate_is_refused(tmp_path):
    frames = _frames(tmp_path / "frames")
    with pytest.raises(EncodeError, match="fps must be at least 1"):
        encode(str(frames), str(tmp_path / "out.mp4"), fps=0)


@needs_ffmpeg
@pytest.mark.parametrize("suffix", [".mp4", ".gif", ".webm"])
def test_every_container_produces_a_file_with_frames_in_it(tmp_path, suffix):
    frames = _frames(tmp_path / "frames", count=8)
    out = encode(str(frames), str(tmp_path / f"movie{suffix}"), fps=8)

    written = Path(out["path"])
    assert written.is_file()
    assert written.stat().st_size > 0
    assert out["frames"] == 8
    # The duration is what a caller is usually actually choosing.
    assert out["seconds"] == pytest.approx(1.0)


@needs_ffmpeg
def test_odd_dimensions_still_encode_to_mp4(tmp_path):
    """H.264 refuses odd dimensions, and a figure-width frame is odd half the time.

    Without the crop filter this fails with an ffmpeg error nobody can act on,
    so it is the one encoder detail worth pinning.
    """
    frames = _frames(tmp_path / "odd", count=4, size=(65, 49))
    out = encode(str(frames), str(tmp_path / "odd.mp4"), fps=4)
    assert Path(out["path"]).stat().st_size > 0


@needs_ffmpeg
def test_the_reported_duration_follows_the_frame_rate(tmp_path):
    frames = _frames(tmp_path / "frames", count=12)
    slow = encode(str(frames), str(tmp_path / "slow.mp4"), fps=6)
    fast = encode(str(frames), str(tmp_path / "fast.mp4"), fps=12)

    assert slow["seconds"] == pytest.approx(2.0)
    assert fast["seconds"] == pytest.approx(1.0)


@needs_ffmpeg
def test_the_movie_is_not_a_still(tmp_path):
    """Guards the size check above: a one-colour movie also has bytes in it.

    Eight frames of changing colour compress to more than eight identical ones,
    so the difference is evidence that the frames reached the encoder rather
    than the first one being repeated.
    """
    changing = _frames(tmp_path / "changing", count=8)
    still = tmp_path / "still"
    still.mkdir()
    for index in range(8):
        Image.new("RGB", (64, 48), (20, 40, 200)).save(still / f"frame_{index:04d}.png")

    moving = encode(str(changing), str(tmp_path / "moving.mp4"), fps=8)
    frozen = encode(str(still), str(tmp_path / "frozen.mp4"), fps=8)
    assert moving["bytes"] > frozen["bytes"]


@needs_ffmpeg
def test_a_gif_keeps_the_colours_it_was_given(tmp_path):
    """The palette pass, which is invisible in the file size.

    Without palettegen/paletteuse ffmpeg quantises to a fixed web palette, and
    the result is a valid GIF of the right length with visibly wrong colours —
    so only reading a pixel back separates the two.
    """
    source = (23, 41, 197)
    directory = tmp_path / "flat"
    directory.mkdir()
    for index in range(4):
        Image.new("RGB", (64, 48), source).save(directory / f"frame_{index:04d}.png")

    out = encode(str(directory), str(tmp_path / "flat.gif"), fps=4)
    with Image.open(out["path"]) as gif:
        pixel = gif.convert("RGB").getpixel((32, 24))
    assert isinstance(pixel, tuple)
    landed = tuple(int(channel) for channel in pixel)

    assert all(abs(a - b) <= 12 for a, b in zip(landed, source, strict=True))
