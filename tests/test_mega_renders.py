"""Test suite for Protean Mega Renders generation and verification."""

from __future__ import annotations

from pathlib import Path
import pytest
from PIL import Image
import numpy as np

OUTPUT_DIR = Path("/Users/charlie/code/scratch/mega_renders")
STRUCTURES = ["1fha", "5jq3", "1f88", "1gfl"]
AESTHETICS = ["glass", "seaglass", "origami"]

ALL_EXPECTED_FILES = [
    f"{pdb}_{aesthetic}.png"
    for pdb in STRUCTURES
    for aesthetic in AESTHETICS
]


def _calc_ink(img: Image.Image) -> float:
    """Calculate non-background ink ratio."""
    rgb = np.asarray(img.convert("RGB")).reshape(-1, 3)
    _, counts = np.unique(rgb, axis=0, return_counts=True)
    return float(1.0 - counts.max() / len(rgb))


def test_mega_renders_file_inventory():
    """Verify all 12 expected render files exist in output directory."""
    assert OUTPUT_DIR.exists(), f"Output directory {OUTPUT_DIR} does not exist"
    missing = [f for f in ALL_EXPECTED_FILES if not (OUTPUT_DIR / f).exists()]
    assert not missing, f"Missing {len(missing)} render files: {missing}"


@pytest.mark.parametrize("filename", ALL_EXPECTED_FILES)
def test_mega_render_properties(filename: str):
    """Verify individual render file specifications and image fidelity."""
    file_path = OUTPUT_DIR / filename
    assert file_path.exists(), f"Render file missing: {file_path}"

    # 1. Check file size (> 50 KB)
    file_size = file_path.stat().st_size
    assert file_size > 50_000, f"{filename} file size too small: {file_size} bytes (< 50 KB)"

    # 2. Check dimensions and DPI
    with Image.open(file_path) as img:
        assert img.format == "PNG", f"{filename} format is {img.format}, expected PNG"
        width, height = img.size
        assert width == 2161, f"{filename} width {width} != expected 2161 px (double column 300 DPI)"
        assert height > 1000, f"{filename} height {height} unexpectedly small"

        # Check DPI metadata if present
        dpi = img.info.get("dpi")
        if dpi:
            assert round(dpi[0]) == 300, f"{filename} horizontal DPI {dpi[0]} != 300"
            assert round(dpi[1]) == 300, f"{filename} vertical DPI {dpi[1]} != 300"

        # 3. Check ink coverage (non-blank render)
        ink = _calc_ink(img)
        assert ink > 0.02, f"{filename} ink coverage {ink:.4f} <= 0.02 (blank frame)"
