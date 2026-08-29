#!/usr/bin/env python3
"""Render one plate per hedcut candidate, at plate size, for Charlie to pick from.

Not a figure in `make_figures.py`'s sense — nothing here goes in the docs. It
exists to answer one open half of one observation: *"Hedcut is also way too
coarse. They read like bad modern art."* The size half was answered in #143
(17 px to 5 px); the mechanism half was not, and this is the round that asks
about it.

Two rules from the project's own record shape this file.

**One plate per row, at plate size.** A hatch is a mark a few pixels wide whose
interval scales with the plate. `print-finishes.png` already had to move from
520 px to 900 for that reason — below it the finishes resolve to the same grey
and the figure tells the reader they are indistinguishable when they are not.
These go out at 1890, which is the size the marks were chosen at.

**The control is always plate one.** A preference between candidates cannot be
read against nothing; `hedcut` exactly as it ships anchors the set.

    uv run python docs/figures/hedcut_bracket.py [--subject 1ubq] [--pixels 1890]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "docs" / "figures"))

from make_figures import SCENE, capture, load, trim  # noqa: E402

from protean_mcp import server  # noqa: E402
from protean_mcp.analysis import hedcut_candidates  # noqa: E402,F401  registers them


async def plate(out: Path, label: str, finish: str, subject: str, pixels: int) -> Path:
    """One candidate, on one subject, at plate size."""
    await load(subject)
    await server.hide(SCENE)
    await server.show(
        selection="polymer",
        name="fig",
        representation="spacefill",
        color="element-symbol",
    )
    # Fill the plate. The default scene fit leaves the molecule at about a
    # third of a 2:1 capture, which means a 5 px mark is 5 px against a 600 px
    # subject — three times coarser, relative to what is being looked at, than
    # the interval was chosen to be. Focusing makes the subject the plate.
    await server.focus("fig")
    await server.background(color="#ffffff")
    path = out / f"{label}.png"
    # `capture` refuses a frame with nothing in it, which is the guard that
    # matters here: a candidate whose mechanism fails silently would otherwise
    # come back as a clean white plate and read as a design choice.
    await capture(path, pixels=pixels, finish=finish)
    # Trim to what is drawn. The capture is 2:1 and the molecule is fitted to
    # its height, so a third of the plate is paper and the marks are judged at
    # a third of the size they were chosen at. Trimming happens AFTER the
    # finish is applied, so the stroke interval is still `longest / 378` of the
    # full plate — 5 px — and only the margin goes.
    trim(path)
    print(f"  {label:28} {finish:22} {path}", flush=True)
    return path


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subject", default="1ubq")
    ap.add_argument("--pixels", type=int, default=1890)
    ap.add_argument("--out", type=Path, default=Path("/tmp/hedcut-bracket"))
    ap.add_argument(
        "--finishes",
        default="hedcut",
        help="comma-separated FINISHES keys, in plate order; the control first",
    )
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    await server.open_viewer()
    made = []
    for finish in [f.strip() for f in args.finishes.split(",") if f.strip()]:
        made.append(await plate(args.out, finish, finish, args.subject, args.pixels))
    print(f"\n{len(made)} plates in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
