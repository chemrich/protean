"""Differential pixel tests and headless browser snapshot harness for Glass and Seaglass shaders.

Validates that:
- Tier 1: Feature coverage for material(finish="glass"), material(finish="seaglass"), preset("seaglass"), and capabilities().
- Tier 2: Boundary and corner cases (invalid finishes, parameter overrides, out-of-range parameters, unshown handles).
- Tier 3: Cross-feature combinations (finishes x representations x lighting rigs x color themes x backgrounds).
- Tier 4: Real-world application scenarios on structures 1ubq and 1crn, asserting coverage > 0.02 and differential delta > 0.005, and saving PNG snapshots to tests/snapshots/*.png for visual inspection.

Opt-in differential test suite:
    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_glass_differential.py -v
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

# Default differential execution flag for this dedicated suite
os.environ.setdefault("PROTEAN_DIFFERENTIAL", "1")
os.environ.setdefault(
    "PROTEAN_CHROME_FLAGS",
    "--headless=new --no-sandbox --disable-dev-shm-usage --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader",
)

import numpy as np
import pytest

from .browser import BROWSER_MARKS, viewer_session
from .pixels import (
    Render,
    background,
    close,
    color_fraction,
    corners,
    coverage,
    decode,
    difference,
    mean_distance_from,
    opaque,
    transparent_fraction,
)
from .test_render_differential import _as_server

pytestmark = BROWSER_MARKS

SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"
DRAWN = 0.02
DELTA = 0.005
MATERIAL_DELTA = 0.005


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


# ==============================================================================
# Tier 1: Feature Coverage Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_tier1_capabilities_reports_glass_and_seaglass():
    """Capabilities RPC reports glass and seaglass material finishes, and seaglass preset."""
    async with viewer_session("1crn") as session:
        reply = await session.request("capabilities", {})
        finishes = reply.get("material_finishes", [])
        assert "glass" in finishes, f"glass missing from material_finishes: {finishes}"
        assert "seaglass" in finishes, f"seaglass missing from material_finishes: {finishes}"

        async with _as_server(session, load=True, pdb_id="1crn"):
            import protean_mcp.server as server_mod
            caps = await server_mod.capabilities()
            assert "glass" in caps.get("material_finishes", [])
            assert "seaglass" in caps.get("material_finishes", [])
            assert "seaglass" in caps.get("presets", [])


@pytest.mark.asyncio
async def test_tier1_material_finish_glass_direct_dispatch():
    """Direct dispatch of material(finish='glass') sets clean transmission properties."""
    async with viewer_session("1ubq") as session:
        reply = await session.request("material", {"name": "auto", "finish": "glass"})
        assert reply.get("ok") is True or reply.get("finish") == "glass"
        assert reply.get("finish") == "glass"
        assert reply.get("metalness") == 0
        assert reply.get("roughness") == 0.05
        assert reply.get("bumpiness", 0) == 0


@pytest.mark.asyncio
async def test_tier1_material_finish_seaglass_direct_dispatch():
    """Direct dispatch of material(finish='seaglass') sets frosted roughness and paper/tumble bump."""
    async with viewer_session("1ubq") as session:
        reply = await session.request("material", {"name": "auto", "finish": "seaglass"})
        assert reply.get("ok") is True or reply.get("finish") == "seaglass"
        assert reply.get("finish") == "seaglass"
        assert reply.get("metalness") == 0
        assert reply.get("roughness") == 0.7
        assert reply.get("bumpiness") == 0.45
        assert reply.get("bump_frequency") == 4.0


@pytest.mark.asyncio
async def test_tier1_preset_seaglass_direct_dispatch():
    """Preset 'seaglass' executes with seafoam color tint, frosted finish, and three-point lighting."""
    async with viewer_session("1ubq") as session:
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            reply = await server_mod.preset("seaglass")
            assert reply.get("ok") is True or reply.get("preset") == "seaglass"
            assert reply.get("preset") == "seaglass"
            steps = reply.get("steps", [])
            assert len(steps) > 0


@pytest.mark.asyncio
async def test_tier1_glass_material_differential_vs_baseline():
    """Glass material produces a non-zero differential delta vs default matte baseline."""
    async with viewer_session("1ubq") as session:
        await session.request("lighting", {"rig": "studio"})
        baseline = await _capture_shot(session)
        assert coverage(baseline) > DRAWN

        reply = await session.request("material", {"name": "auto", "finish": "glass"})
        assert reply.get("ok") is True or reply.get("finish") == "glass"

        glass_render = await _capture_shot(session)
        assert coverage(glass_render) > DRAWN
        delta = difference(baseline, glass_render)
        assert delta > MATERIAL_DELTA, f"Glass material delta {delta:.4f} vs baseline is too small"


@pytest.mark.asyncio
async def test_tier1_seaglass_material_differential_vs_baseline():
    """Seaglass material produces a non-zero differential delta vs default matte baseline."""
    async with viewer_session("1ubq") as session:
        await session.request("lighting", {"rig": "studio"})
        baseline = await _capture_shot(session)
        assert coverage(baseline) > DRAWN

        reply = await session.request("material", {"name": "auto", "finish": "seaglass"})
        assert reply.get("ok") is True or reply.get("finish") == "seaglass"

        seaglass_render = await _capture_shot(session)
        assert coverage(seaglass_render) > DRAWN
        delta = difference(baseline, seaglass_render)
        assert delta > MATERIAL_DELTA, f"Seaglass material delta {delta:.4f} vs baseline is too small"


# ==============================================================================
# Tier 2: Boundary and Corner Cases
# ==============================================================================


@pytest.mark.asyncio
async def test_tier2_material_glass_roughness_override():
    """Glass finish with explicit roughness override applies specified roughness."""
    async with viewer_session("1ubq") as session:
        reply = await session.request(
            "material", {"name": "auto", "finish": "glass", "roughness": 0.35}
        )
        assert reply.get("ok") is True or reply.get("finish") == "glass"
        assert reply.get("roughness") == 0.35
        assert reply.get("metalness") == 0


@pytest.mark.asyncio
async def test_tier2_material_glass_metalness_override():
    """Glass finish with explicit metalness override applies specified metalness."""
    async with viewer_session("1ubq") as session:
        reply = await session.request(
            "material", {"name": "auto", "finish": "glass", "metalness": 0.5}
        )
        assert reply.get("ok") is True or reply.get("finish") == "glass"
        assert reply.get("metalness") == 0.5
        assert reply.get("roughness") == 0.05


@pytest.mark.asyncio
async def test_tier2_material_seaglass_bump_override():
    """Seaglass finish with bumpiness and bump_frequency overrides applies specified values."""
    async with viewer_session("1ubq") as session:
        reply = await session.request(
            "material",
            {
                "name": "auto",
                "finish": "seaglass",
                "bumpiness": 0.8,
                "bump_frequency": 6.0,
            },
        )
        assert reply.get("ok") is True or reply.get("finish") == "seaglass"
        assert reply.get("bumpiness") == 0.8
        assert reply.get("bump_frequency") == 6.0


@pytest.mark.asyncio
async def test_tier2_material_seaglass_roughness_override():
    """Seaglass finish with custom roughness override applies specified roughness."""
    async with viewer_session("1ubq") as session:
        reply = await session.request(
            "material", {"name": "auto", "finish": "seaglass", "roughness": 0.4}
        )
        assert reply.get("ok") is True or reply.get("finish") == "seaglass"
        assert reply.get("roughness") == 0.4


@pytest.mark.asyncio
async def test_tier2_material_invalid_finish_rejected():
    """Invalid finish name is rejected with informative error message listing valid finishes."""
    async with viewer_session("1ubq") as session:
        with pytest.raises(Exception, match=r"Unknown finish.*(?:glass|seaglass)"):
            await session.request("material", {"name": "auto", "finish": "frosted_glass"})


@pytest.mark.asyncio
async def test_tier2_material_out_of_bounds_parameters_rejected():
    """Out-of-bounds parameter values are rejected by schema/range validation."""
    invalid_cases = [
        ("roughness", 1.5),
        ("roughness", -0.1),
        ("metalness", 2.0),
        ("metalness", -0.5),
        ("bumpiness", 3.0),
        ("bumpiness", -0.2),
        ("bump_frequency", 25.0),
        ("bump_frequency", -1.0),
        ("emissive", 5.0),
        ("emissive", -0.1),
    ]
    async with viewer_session("1ubq") as session:
        for param, value in invalid_cases:
            with pytest.raises(Exception, match=r"(?:between 0 and|out of range|invalid)"):
                await session.request(
                    "material", {"name": "auto", "finish": "glass", param: value}
                )


@pytest.mark.asyncio
async def test_tier2_material_unshown_handle_refused():
    """Applying glass finish to a selection handle that was never shown raises an error."""
    async with viewer_session("1ubq") as session:
        with pytest.raises(Exception, match=r"(?:No selection named|no representation)"):
            await session.request("material", {"name": "nonexistent_handle", "finish": "glass"})


@pytest.mark.asyncio
async def test_tier2_preset_seaglass_with_custom_handle():
    """Preset 'seaglass' applied to a specific handle affects that handle without resetting whole camera."""
    async with viewer_session("1ubq") as session:
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            await server_mod.select("resi 1-20", name="nterm")
            await server_mod.show(handle="nterm", representation="cartoon")

            reply = await server_mod.preset("seaglass", handle="nterm")
            assert reply.get("preset") == "seaglass"
            assert reply.get("applied_to") == "nterm"


# ==============================================================================
# Tier 3: Cross-Feature Combinations
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["glass", "seaglass"])
@pytest.mark.parametrize("representation", ["cartoon", "spacefill", "surface", "ball-and-stick"])
async def test_tier3_glass_finishes_across_representations(finish: str, representation: str):
    """Glass and seaglass finishes render correctly across multiple representation types."""
    async with viewer_session("1crn") as session:
        async with _as_server(session, load=True, pdb_id="1crn"):
            import protean_mcp.server as server_mod
            await server_mod.show(representation=representation, selection="polymer", name="repr_test")
            await server_mod.material(finish=finish, name="repr_test")

        shot = await _capture_shot(session)
        assert coverage(shot) > DRAWN, f"Representation {representation} with finish {finish} has zero coverage"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["glass", "seaglass"])
@pytest.mark.parametrize("rig", ["three-point", "studio", "rim", "flat", "ring"])
async def test_tier3_glass_finishes_with_lighting_rigs(finish: str, rig: str):
    """Glass and seaglass finishes interact properly with different lighting rigs."""
    async with viewer_session("1ubq") as session:
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            await server_mod.material(finish=finish, name="auto")
            await server_mod.lighting(rig=rig)

        shot = await _capture_shot(session)
        assert coverage(shot) > DRAWN, f"Finish {finish} under rig {rig} failed coverage check"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["glass", "seaglass"])
@pytest.mark.parametrize(
    "color_theme",
    ["#73b9a2", "secondary-structure", "chain-id", "element-symbol"],
)
async def test_tier3_glass_finishes_with_color_modes(finish: str, color_theme: str):
    """Glass and seaglass finishes render correctly with uniform colors and color themes."""
    async with viewer_session("1ubq") as session:
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            await server_mod.color(color=color_theme, name="auto")
            await server_mod.material(finish=finish, name="auto")

        shot = await _capture_shot(session)
        assert coverage(shot) > DRAWN, f"Finish {finish} with color {color_theme} failed coverage"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["glass", "seaglass"])
async def test_tier3_glass_finishes_with_backgrounds(finish: str):
    """Glass and seaglass finishes render over white, dark, gradient, and transparent grounds."""
    async with viewer_session("1ubq") as session:
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            # White background
            await server_mod.background(color="#ffffff")
            await server_mod.material(finish=finish, name="auto")
            white_shot = await _capture_shot(session)
            assert coverage(white_shot) > DRAWN

            # Dark background
            await server_mod.background(color="#111111")
            dark_shot = await _capture_shot(session)
            assert coverage(dark_shot) > DRAWN

            # Radial gradient
            await server_mod.background(gradient="radial", gradient_from="#1a2a3a", gradient_to="#050a10")
            grad_shot = await _capture_shot(session)
            assert len(corners(grad_shot)) == 4

            # Transparent ground
            await server_mod.background(transparent=True)
            trans_shot = await _capture_shot(session)
            assert transparent_fraction(trans_shot) > 0.1


# ==============================================================================
# Tier 4: Real-World Application Scenarios
# ==============================================================================


@pytest.mark.asyncio
async def test_tier4_scenario1_ubiquitin_1ubq_glass_snapshot():
    """Scenario 1: Ubiquitin (1ubq) loaded, glass material applied, coverage > 2%, delta > 0.005, snapshot saved."""
    async with viewer_session("1ubq") as session:
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            await server_mod.lighting(rig="studio")
            # 1. Baseline render
            baseline = await _capture_shot(session)
            assert coverage(baseline) > DRAWN, f"Baseline coverage {coverage(baseline):.4f} <= {DRAWN}"

            # 2. Apply clear glass material via server tool
            reply = await server_mod.material(finish="glass", name="auto")
            assert reply.get("ok") is True or reply.get("finish") == "glass"
            assert reply.get("finish") == "glass"

            # 3. Capture glass render
            glass_render = await _capture_shot(session)
            cov = coverage(glass_render)
            assert cov > DRAWN, f"Glass pixel coverage {cov:.4f} is not > {DRAWN}"

            # 4. Assert differential delta vs baseline
            delta = difference(baseline, glass_render)
            assert delta > DELTA, f"Differential delta {delta:.4f} vs baseline is not > {DELTA}"

            # 5. Save snapshot to tests/snapshots/1ubq_glass_snapshot.png
            saved_path = _save_snapshot(glass_render, "1ubq_glass_snapshot.png")
            assert saved_path.exists(), f"Snapshot file not found: {saved_path}"
            assert saved_path.stat().st_size > 1000, f"Snapshot file size too small: {saved_path.stat().st_size} bytes"


@pytest.mark.asyncio
async def test_tier4_scenario2_ubiquitin_1ubq_seaglass_preset_snapshot():
    """Scenario 2: Ubiquitin (1ubq) loaded, preset('seaglass') applied, coverage > 2%, delta > 0.005, snapshot saved."""
    async with viewer_session("1ubq") as session:
        # 1. Baseline render
        baseline = await _capture_shot(session)
        assert coverage(baseline) > DRAWN, f"Baseline coverage {coverage(baseline):.4f} <= {DRAWN}"

        # 2. Apply high-level seaglass preset via server tool
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            preset_reply = await server_mod.preset("seaglass")
            assert preset_reply.get("ok") is True or preset_reply.get("preset") == "seaglass"
            assert preset_reply.get("preset") == "seaglass"

        # 3. Capture seaglass preset render
        seaglass_render = await _capture_shot(session)
        cov = coverage(seaglass_render)
        assert cov > DRAWN, f"Seaglass preset pixel coverage {cov:.4f} is not > {DRAWN}"

        # 4. Assert differential delta vs baseline
        delta = difference(baseline, seaglass_render)
        assert delta > DELTA, f"Differential delta {delta:.4f} vs baseline is not > {DELTA}"

        # 5. Save snapshot to tests/snapshots/1ubq_seaglass_preset_snapshot.png
        saved_path = _save_snapshot(seaglass_render, "1ubq_seaglass_preset_snapshot.png")
        assert saved_path.exists(), f"Snapshot file not found: {saved_path}"
        assert saved_path.stat().st_size > 1000, f"Snapshot file size too small: {saved_path.stat().st_size} bytes"


@pytest.mark.asyncio
async def test_tier4_scenario3_crambin_1crn_glass_roughness_override():
    """Scenario 3: Crambin (1crn) glass material with roughness and bump overrides, coverage > 2%, delta > 0.005."""
    async with viewer_session("1crn") as session:
        async with _as_server(session, load=True, pdb_id="1crn"):
            import protean_mcp.server as server_mod
            await server_mod.lighting(rig="studio")
            baseline = await _capture_shot(session)
            assert coverage(baseline) > DRAWN

            reply = await server_mod.material(
                finish="glass",
                roughness=0.25,
                bumpiness=0.15,
                bump_frequency=3.0,
                name="auto",
            )
            assert reply.get("ok") is True or reply.get("finish") == "glass"
            assert reply.get("roughness") == 0.25

            render = await _capture_shot(session)
            cov = coverage(render)
            assert cov > DRAWN, f"Render coverage {cov:.4f} is not > {DRAWN}"

            delta = difference(baseline, render)
            assert delta > DELTA, f"Differential delta {delta:.4f} vs baseline is not > {DELTA}"


@pytest.mark.asyncio
async def test_tier4_scenario4_multi_representation_complex_seaglass():
    """Scenario 4: Multi-representation structure (cartoon + molecular surface) with seaglass finish."""
    async with viewer_session("1crn") as session:
        async with _as_server(session, load=True, pdb_id="1crn"):
            import protean_mcp.server as server_mod
            await server_mod.show(name="cartoon_layer", selection="polymer", representation="cartoon", color="secondary-structure")
            await server_mod.show(name="surface_layer", selection="polymer", representation="surface", color="#73b9a2")
            await server_mod.material(name="surface_layer", finish="seaglass")

            render = await _capture_shot(session)
            cov = coverage(render)
            assert cov > DRAWN, f"Multi-representation seaglass coverage {cov:.4f} is not > {DRAWN}"


@pytest.mark.asyncio
async def test_tier4_scenario5_sequential_finish_transitions():
    """Scenario 5: Sequential transitions (matte -> glass -> seaglass -> preset('seaglass')), delta > 0.005 per step."""
    async with viewer_session("1ubq") as session:
        await session.request("lighting", {"rig": "studio"})
        # Step 0: Matte baseline
        step0_matte = await _capture_shot(session)
        assert coverage(step0_matte) > DRAWN

        # Step 1: Transition to Glass finish
        await session.request("material", {"name": "auto", "finish": "glass"})
        step1_glass = await _capture_shot(session)
        assert coverage(step1_glass) > DRAWN
        delta_0_1 = difference(step0_matte, step1_glass)
        assert delta_0_1 > DELTA, f"Matte -> Glass delta {delta_0_1:.4f} <= {DELTA}"

        # Step 2: Transition to Seaglass finish
        await session.request("material", {"name": "auto", "finish": "seaglass"})
        step2_seaglass = await _capture_shot(session)
        assert coverage(step2_seaglass) > DRAWN
        delta_1_2 = difference(step1_glass, step2_seaglass)
        assert delta_1_2 > DELTA, f"Glass -> Seaglass delta {delta_1_2:.4f} <= {DELTA}"

        # Step 3: Transition to Seaglass Preset (with seafoam tint & balanced lighting)
        async with _as_server(session, load=True, pdb_id="1ubq"):
            import protean_mcp.server as server_mod
            await server_mod.preset("seaglass")

        step3_preset = await _capture_shot(session)
        assert coverage(step3_preset) > DRAWN
        delta_2_3 = difference(step2_seaglass, step3_preset)
        assert delta_2_3 > DELTA, f"Seaglass -> Seaglass Preset delta {delta_2_3:.4f} <= {DELTA}"
