#!/usr/bin/env python3
"""Turn a directory of run_bench.py results into one markdown table.

Written to be read in a GitHub step summary, where it is the only output anyone
will look at, and to make the two things backlog 40 needs to distinguish visible
side by side:

  * a *per-sample* regression shows as the same ratio at every sample level;
  * a *fixed per-capture* regression shows as a constant difference in
    milliseconds, so the ratio shrinks as the level rises.

Results are ordered by their file name, which the workflow prefixes with the
run index — so a version measured twice appears twice, in the order it ran, and
drift across a long job is visible rather than averaged away.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(directory: Path) -> list[dict]:
    out = []
    for path in sorted(directory.glob("*.json")):
        try:
            out.append({"file": path.name, **json.loads(path.read_text())})
        except Exception as exc:  # a broken file is a row, not a crash
            out.append({"file": path.name, "ok": False, "error": f"unreadable: {exc}"})
    return out


def fmt(value: float | None, digits: int = 0) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path)
    ap.add_argument("--baseline", default="", help="label to express ratios against")
    args = ap.parse_args()

    runs = load(args.results)
    if not runs:
        print("No results.")
        return 1

    good = [r for r in runs if r.get("ok")]
    levels: list[int] = []
    for run in good:
        for entry in run.get("levels", []):
            if entry["sampleLevel"] not in levels:
                levels.append(entry["sampleLevel"])
    levels.sort(reverse=True)

    # The baseline is the first run carrying the requested label, not an average
    # over every run of it: an average would fold a late, drifted repeat into the
    # number every other row is divided by.
    baseline = None
    for run in good:
        if not args.baseline or run.get("label") == args.baseline:
            baseline = run
            break

    def median_at(run: dict, level: int) -> float | None:
        for entry in run.get("levels", []):
            if entry["sampleLevel"] == level:
                return entry["stats"]["median"]
        return None

    print("## Mol\\* capture cost by release\n")
    if baseline:
        print(
            f"Baseline for the ratio columns: **{baseline.get('label')}** "
            f"(`{baseline['file']}`), the first run of it in this job.\n"
        )

    head = ["#", "version"]
    for level in levels:
        head += [f"L{level} median ms", f"L{level} p25-p75", f"L{level} vs base"]
    head += [
        "full path ms",
        "coverage",
        "cam dist",
        "draws",
        "instances",
        "transparency",
        "helper level",
    ]
    print("| " + " | ".join(head) + " |")
    print("|" + "|".join(["---"] * len(head)) + "|")

    for index, run in enumerate(runs, start=1):
        if not run.get("ok"):
            error = str(run.get("error", "failed")).replace("|", "\\|")[:160]
            print(
                f"| {index} | {run.get('label', run['file'])} | "
                + " | ".join(["-"] * (len(head) - 3))
                + f" | **FAILED**: {error} |"
            )
            continue
        row = [str(index), str(run.get("label", "?"))]
        for level in levels:
            value = median_at(run, level)
            entry = next((e for e in run["levels"] if e["sampleLevel"] == level), None)
            spread = (
                f"{entry['stats']['p25']:,.0f}-{entry['stats']['p75']:,.0f}"
                if entry
                else "-"
            )
            base = median_at(baseline, level) if baseline else None
            ratio = f"{value / base:.2f}x" if (value and base) else "-"
            row += [fmt(value), spread, ratio]
        full = run.get("fullPath", {}).get("stats")
        work = run.get("work", {})
        stats = work.get("stats") or {}
        picture = run.get("picture") or {}
        camera = run.get("camera") or {}
        coverage = picture.get("coverage")
        distance = camera.get("distanceToTarget")
        row += [
            fmt(full["median"]) if full else "-",
            "-" if coverage is None else f"{coverage * 100:.2f}%",
            fmt(distance, 1),
            fmt(stats.get("drawCount")),
            fmt(stats.get("instanceCount")),
            str(work.get("transparencyMode") or "-"),
            str((run.get("helperChosenMultiSample") or {}).get("sampleLevel", "-")),
        ]
        print("| " + " | ".join(row) + " |")

    failures = [r for r in runs if not r.get("ok")]
    if failures:
        print(f"\n**{len(failures)} of {len(runs)} runs failed.**\n")
        for run in failures:
            print(f"- `{run['file']}`: {str(run.get('error'))[:400]}")

    if len(levels) > 1 and baseline:
        print(
            "\n### Reading this\n\n"
            "A regression in the cost of each *sample* keeps the same ratio at every "
            "sample level. A regression in fixed per-capture work — a pass built or a "
            "program linked once per capture — shows as a roughly constant difference "
            "in milliseconds, so its ratio falls as the level rises. Compare the "
            f"`vs base` columns for L{levels[0]} and L{levels[-1]} before concluding "
            "which one this is.\n"
        )

    # Coverage and camera distance are not decoration. Under a software
    # rasteriser a capture costs very nearly what it covers, so a release that
    # merely changed the camera fit would produce the same table as a real
    # per-sample regression. If those two columns hold steady while the
    # milliseconds move, the framing is not the explanation.
    print(
        "\n`coverage` is the fraction of the frame the molecule occupies and "
        "`cam dist` the fitted camera distance. They are here to be checked, not "
        "read past: a release that only changed the camera fit would move the "
        "milliseconds too, and these are the two columns that tell it apart from "
        "a renderer that got slower. Every result also carries a thumbnail of "
        "its own capture, in the artifact.\n"
    )
    print(
        "\nEvery row is a fresh browser on the same runner, in the order shown. "
        "Ratios between rows are the only numbers here that mean anything across "
        "jobs; the absolute milliseconds are a property of this runner.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
