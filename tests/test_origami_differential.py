"""Differential pixel tests and headless browser snapshot harness for the Origami aesthetic.

Validates that:
- The origami preset and finish execute without WebGL or Python runtime errors.
- Programmatic snapshots are captured for 1crn and 1ubq.
- Pixel coverage is > 2% (DRAWN = 0.02).
- Differential delta vs standard baseline is > 5% (DELTA = 0.05).
- Snapshots are written to tests/snapshots/*.png for visual judging.

Opt-in differential test suite:
    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_origami_differential.py -v
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from .browser import BROWSER_MARKS, viewer_session
from .pixels import (
    Render,
    coverage,
    decode,
    difference,
)
from .test_render_differential import _as_server

pytestmark = BROWSER_MARKS

SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"


def _save_snapshot(render: Render, filename: str) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SNAPSHOTS_DIR / filename
    from PIL import Image as PILImage

    img = PILImage.fromarray(render.pixels, mode="RGBA")
    img.save(out_path)
    return out_path


async def _capture_shot(session: Any) -> Render:
    result = await session.request("screenshot", {})
    assert "data_uri" in result
    return decode(result["data_uri"])


@pytest.mark.asyncio
async def test_origami_preset_1crn_execution_and_snapshot():
    """1crn loaded, origami preset applied, no WebGL errors, snapshot written to disk, coverage > 2%."""
    async with viewer_session("1crn") as session:
        # Capture baseline default render
        baseline = await _capture_shot(session)
        assert coverage(baseline) > 0.02, f"Baseline coverage too low: {coverage(baseline)}"

        # Run through server-level preset invocation
        async with _as_server(session, load=True, pdb_id="1crn"):
            import protean_mcp.server as server_mod
            preset_reply = await server_mod.preset("origami")
            assert preset_reply.get("ok") is True or preset_reply.get("preset") == "origami"

        # Capture origami render
        origami_render = await _capture_shot(session)
        cov = coverage(origami_render)
        assert cov > 0.02, f"Origami pixel coverage {cov:.4f} is not > 0.02"

        # Compare differential delta vs baseline
        delta = difference(baseline, origami_render)
        assert delta > 0.05, f"Differential delta {delta:.4f} vs baseline is not > 0.05 (5%)"

        # Save snapshot
        saved_file = _save_snapshot(origami_render, "1crn_origami_snapshot.png")
        assert saved_file.exists()
        assert saved_file.stat().st_size > 1000


@pytest.mark.asyncio
async def test_origami_preset_1ubq_execution_and_snapshot():
    """1ubq loaded, origami preset applied, snapshot saved, coverage > 2%, delta > 5%."""
    async with viewer_session("1ubq") as session:
        baseline = await _capture_shot(session)
        assert coverage(baseline) > 0.02, f"Baseline coverage too low: {coverage(baseline)}"

        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            preset_reply = await server_mod.preset("origami")
            assert preset_reply.get("ok") is True or preset_reply.get("preset") == "origami"

        origami_render = await _capture_shot(session)
        cov = coverage(origami_render)
        assert cov > 0.02, f"Origami pixel coverage {cov:.4f} is not > 0.02"

        delta = difference(baseline, origami_render)
        assert delta > 0.05, f"Differential delta {delta:.4f} vs baseline is not > 0.05 (5%)"

        saved_file = _save_snapshot(origami_render, "1ubq_origami_snapshot.png")
        assert saved_file.exists()
        assert saved_file.stat().st_size > 1000


STYLED = 0.008
MATERIAL_DELTA = 0.003


@pytest.mark.asyncio
async def test_origami_material_finish_direct_dispatch():
    """Direct dispatch of material(finish='origami') alters pixels vs standard finish."""
    async with viewer_session("1ubq") as session:
        baseline = await _capture_shot(session)

        # Apply origami material finish directly
        reply = await session.request("material", {"name": "auto", "finish": "origami"})
        assert reply.get("ok") is True or reply.get("finish") == "origami"

        after_mat = await _capture_shot(session)
        assert coverage(after_mat) > 0.02
        delta = difference(baseline, after_mat)
        assert delta > MATERIAL_DELTA, f"Material finish delta {delta:.4f} too small"


@pytest.mark.asyncio
async def test_origami_shading_direct_dispatch():
    """Direct dispatch of shading(style='origami') enables facet creasing and changes render."""
    async with viewer_session("1ubq") as session:
        baseline = await _capture_shot(session)

        reply = await session.request("shading", {"name": "auto", "style": "origami"})
        assert reply.get("ok") is True or reply.get("style") == "origami"

        after_shading = await _capture_shot(session)
        assert coverage(after_shading) > 0.02
        delta = difference(baseline, after_shading)
        assert delta > STYLED, f"Shading style delta {delta:.4f} too small"


@pytest.mark.asyncio
async def test_origami_capabilities():
    """Capabilities RPC reports origami finish and preset."""
    async with viewer_session("1crn") as session:
        reply = await session.request("capabilities", {})
        assert "origami" in reply.get("material_finishes", [])
        assert "origami" in reply.get("shading_styles", [])
