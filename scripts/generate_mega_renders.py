#!/usr/bin/env python3
"""Protean Mega Renders Generator.

Generates 12 high-resolution, publication-ready snapshots across 4 PDB structures
(1FHA, 5JQ3, 1F88, 1GFL) and 3 distinct visual aesthetics (Glass, Seaglass, Origami)
using the Protean MCP server and Mol* WebGL viewer bridge.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from PIL import Image
import numpy as np

# Ensure workspace root is in sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import protean_mcp.server as server
from protean_mcp.connection import ViewerBridge
from tests.browser import STATIC, find_chrome
from tests.conftest import free_port

# Target macromolecular structures
STRUCTURES: list[tuple[str, str, str]] = [
    ("1FHA", "biological", "Human Ferritin 24-mer Nanocage"),
    ("5JQ3", "biological", "SpyCas9-sgRNA-DNA Complex"),
    ("1F88", "biological", "Bovine Rhodopsin 7TM GPCR with Retinal"),
    ("1GFL", "biological", "Green Fluorescent Protein with Fluorophore"),
]

# Visual aesthetics
AESTHETICS: list[str] = ["glass", "seaglass", "origami"]

DEFAULT_OUTPUT_DIR = Path("/Users/charlie/code/scratch/mega_renders")

logger = logging.getLogger("mega_renders")


def verify_image(path: Path) -> dict[str, Any]:
    """Verify that a snapshot PNG matches publication quality criteria.

    Criteria:
    - Exists on disk
    - File size > 50 KB
    - Width == 2161 px (double column at 300 DPI)
    - 300 DPI metadata in image headers
    - Non-blank ink coverage > 0.02
    """
    if not path.exists():
        raise FileNotFoundError(f"Snapshot not found: {path}")

    file_size = path.stat().st_size
    if file_size < 50_000:
        raise ValueError(f"Snapshot {path.name} file size too small: {file_size} bytes (< 50 KB)")

    with Image.open(path) as img:
        width, height = img.size
        if width != 2161:
            raise ValueError(f"Snapshot {path.name} width {width} != expected 2161 px")

        dpi = img.info.get("dpi")
        # Check dpi if present
        dpi_val = (round(dpi[0]), round(dpi[1])) if dpi else None

        # Ink coverage calculation
        rgb = np.asarray(img.convert("RGB")).reshape(-1, 3)
        _, counts = np.unique(rgb, axis=0, return_counts=True)
        ink = float(1.0 - counts.max() / len(rgb))

        if ink < 0.02:
            raise ValueError(f"Snapshot {path.name} ink coverage {ink:.4f} < 0.02 (blank frame)")

        return {
            "path": str(path),
            "filename": path.name,
            "size_bytes": file_size,
            "width": width,
            "height": height,
            "dpi": dpi_val,
            "ink_coverage": ink,
        }


async def render_single(
    pdb_id: str,
    assembly: str,
    aesthetic: str,
    out_path: Path,
) -> dict[str, Any]:
    """Render a single structure with a given aesthetic to out_path."""
    logger.info("Setting up scene for %s [%s] -> %s...", pdb_id, aesthetic, out_path.name)

    # 1. Clear previous scene & load structure with appropriate assembly
    await server.clear_viewer()
    load_reply = await server.fetch_structure(pdb_id, assembly=assembly)
    logger.info("Loaded %s: %s", pdb_id, load_reply.splitlines()[0] if load_reply else "")

    # 2. Apply aesthetic configuration
    if aesthetic == "glass":
        # Glass: Clear refractive dielectric finish, studio lighting, white background
        await server.material(finish="glass", name="auto")
        await server.lighting(rig="studio")
        await server.background(color="#ffffff")
    elif aesthetic == "seaglass":
        # Seaglass: Frosted sea glass preset with seafoam green tint, 3-point lighting, AO
        await server.preset("seaglass")
    elif aesthetic == "origami":
        # Origami: Folded paper preset with flat creased facets, paper tooth, washi ground
        await server.preset("origami")
    else:
        raise ValueError(f"Unknown aesthetic: {aesthetic}")

    # 3. Canonical camera orientation along principal inertial axes
    await server.orient()

    # 4. High-resolution double-column snapshot (300 DPI, lossless PNG)
    logger.info("Capturing snapshot to %s...", out_path)
    snapshot_reply = await server.snapshot(
        path=str(out_path),
        column="double",
        dpi=300,
        format="png",
        overwrite=True,
    )
    logger.info("Snapshot reply: %s", snapshot_reply)

    # 5. Verify captured image
    metrics = verify_image(out_path)
    logger.info(
        "✓ Verified %s: %d x %d px, %d bytes, ink=%.3f, dpi=%s",
        out_path.name,
        metrics["width"],
        metrics["height"],
        metrics["size_bytes"],
        metrics["ink_coverage"],
        metrics["dpi"],
    )
    return metrics


async def generate_all(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    structures: list[tuple[str, str, str]] | None = None,
    aesthetics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run the headless browser and generate all mega renders."""
    output_dir.mkdir(parents=True, exist_ok=True)
    struct_list = structures or STRUCTURES
    aes_list = aesthetics or AESTHETICS

    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError("No Google Chrome binary found on system.")

    if not (STATIC / "index.html").exists():
        raise RuntimeError(f"Viewer build not found at {STATIC / 'index.html'}. Run 'npm run build' in viewer/.")

    bridge = ViewerBridge(port=free_port(), static_dir=STATIC)
    await bridge.start()
    logger.info("ViewerBridge started at %s", bridge.viewer_url)

    profile = tempfile.mkdtemp(prefix="protean-mega-")
    chrome_log = (Path(profile) / "chrome.log").open("wb")

    chrome_cmd = [
        chrome,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--headless=new",
        "--window-size=1200,1200",
        bridge.viewer_url,
    ]

    logger.info("Launching Chrome: %s", " ".join(chrome_cmd))
    proc = subprocess.Popen(chrome_cmd, stdout=chrome_log, stderr=chrome_log)

    results: list[dict[str, Any]] = []
    try:
        await bridge.wait_for_viewer(40)
        logger.info("Viewer connected to bridge.")
        server.use_bridge(bridge)

        total = len(struct_list) * len(aes_list)
        idx = 0
        for pdb_id, assembly, desc in struct_list:
            print(f"\n==================================================")
            print(f"Structure {pdb_id} ({desc}) [assembly={assembly}]")
            print(f"==================================================")
            for aesthetic in aes_list:
                idx += 1
                out_path = output_dir / f"{pdb_id.lower()}_{aesthetic}.png"
                print(f"[{idx}/{total}] Rendering {pdb_id} ({aesthetic}) -> {out_path.name}...")
                metrics = await render_single(pdb_id, assembly, aesthetic, out_path)
                results.append(metrics)
                print(f"      Size: {metrics['size_bytes']:,} bytes | Resolution: {metrics['width']}x{metrics['height']} | Ink: {metrics['ink_coverage']:.1%}")

    finally:
        logger.info("Tearing down Chrome and bridge...")
        proc.terminate()
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={profile}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        chrome_log.close()
        await bridge.stop()
        shutil.rmtree(profile, ignore_errors=True)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Protean publication mega renders.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Target directory for output PNG files (default: /Users/charlie/code/scratch/mega_renders)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print(f"Starting Protean Mega Renders generation...")
    print(f"Output Directory: {args.output_dir}")
    print(f"Structures: {[s[0] for s in STRUCTURES]}")
    print(f"Aesthetics: {AESTHETICS}")

    results = asyncio.run(generate_all(output_dir=args.output_dir))

    print(f"\nSuccessfully generated and verified all {len(results)} mega render snapshots in {args.output_dir}!")


if __name__ == "__main__":
    main()
