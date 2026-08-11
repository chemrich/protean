"""What actually ends up in an installed protean.

The viewer is an npm build artifact and therefore gitignored, and hatchling
honours VCS ignores when choosing files for a wheel. That combination shipped a
package whose viewer never loaded: `pip install protean-mcp` succeeded,
open_viewer reported the app was not built, and nothing pointed at the cause.

These tests build a real wheel, because the packaging config is the only place
that mistake can be seen and the config looks fine either way.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "protean_mcp" / "static"

needs_viewer = pytest.mark.skipif(
    not (STATIC / "index.html").exists(),
    reason="viewer not built (npm run build in viewer/)",
)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    built = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        # `build` is not a dependency; uv is what this project uses.
        built = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(out)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    if built.returncode != 0:
        pytest.skip(f"could not build a wheel here: {built.stderr[-300:]}")
    wheels = sorted(out.glob("*.whl"))
    assert wheels, "the build produced no wheel"
    newest: Path = wheels[-1]
    return newest


@needs_viewer
def test_the_wheel_carries_the_viewer(wheel):
    """The bug this file exists for.

    Without an explicit artifacts rule the wheel has the server and none of the
    viewer, which is a working install of something that cannot draw.
    """
    names = zipfile.ZipFile(wheel).namelist()
    static = [name for name in names if "/static/" in name]

    assert static, "the wheel contains no viewer files at all"
    assert any(name.endswith("static/index.html") for name in static)
    # The Mol* bundle is the largest part and the one an install is useless
    # without; its absence would leave an index.html that loads nothing.
    assert any(name.endswith("static/molstar.js") for name in static)
    assert any("/static/assets/" in name for name in static)


def test_the_wheel_exposes_the_command(wheel):
    """`protean-mcp` is how an MCP client starts the server."""
    with zipfile.ZipFile(wheel) as archive:
        entry = next(n for n in archive.namelist() if n.endswith("entry_points.txt"))
        text = archive.read(entry).decode()
    assert "protean-mcp" in text
    assert "protean_mcp.server:main" in text


def test_the_wheel_carries_the_server_itself(wheel):
    names = zipfile.ZipFile(wheel).namelist()
    assert any(name.endswith("protean_mcp/server.py") for name in names)
    assert any(name.endswith("protean_mcp/analysis/trajectory.py") for name in names)
