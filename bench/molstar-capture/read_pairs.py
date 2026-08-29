#!/usr/bin/env python3
"""Read a mirrored benchmark run as pair means, and show what cancels.

`summarise.py` prints every row in the order it ran, which is right for a sweep
where the order *is* the finding. It is the wrong reading for the runs that
answer backlog 43, where each condition appears twice in mirrored positions
specifically so that drift cancels.

That mirroring is not decoration. The occlusion-off control (run 33252837938)
read every adjacent step at ~0.89x regardless of which version followed which —
5.5.0 -> 5.6.0 was 0.890x and 5.6.0 -> 5.5.0 was 0.894x. Read row by row, either
of those looks like a version effect and they point opposite ways; read as pair
means, the two versions are 0.99x apart and the 0.89x is the runner settling.

So: one line per condition, the mean of its mirrored pair, the ratio against a
baseline, and — always — how far apart that condition's own two rows were. That
last column is the run's noise floor, measured on the same runner in the same
job as the signal rather than assumed, and no ratio in the table should be
believed unless it clears it.

    python3 read_pairs.py results/ --baseline 5.5.0
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def load(directory: Path) -> dict[str, list[tuple[int, dict[int, float]]]]:
    runs: dict[str, list[tuple[int, dict[int, float]]]] = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text())
        if not data.get("ok"):
            continue
        order = int(path.name.split("-", 1)[0])
        medians = {e["sampleLevel"]: e["stats"]["median"] for e in data["levels"]}
        runs.setdefault(data["label"], []).append((order, medians))
    return runs


def shorten(label: str) -> str:
    return label.replace("bench/molstar-capture/candidates/", "").replace(
        ":ssao-blur.frag=@ssao-blur-bgfix.frag", "+blurfix"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path)
    ap.add_argument("--baseline", default="5.5.0")
    args = ap.parse_args()

    runs = load(args.results)
    if not runs:
        print("No results.")
        return 1
    if args.baseline not in runs:
        print(f"baseline {args.baseline!r} is not one of: {', '.join(runs)}")
        return 1

    levels = sorted({lv for rs in runs.values() for _, m in rs for lv in m}, reverse=True)
    base = {lv: statistics.mean(m[lv] for _, m in runs[args.baseline]) for lv in levels}

    width = max(len(shorten(k)) for k in runs)
    head = f"{'condition':{width}}"
    for lv in levels:
        head += f" {'L' + str(lv) + ' mean':>10} {'vs base':>8}"
    print(head + f" {'spread':>7}  positions")
    for label, rs in sorted(runs.items(), key=lambda kv: min(o for o, _ in kv[1])):
        line = f"{shorten(label):{width}}"
        for lv in levels:
            values = [m[lv] for _, m in rs]
            mean = statistics.mean(values)
            line += f" {mean:10,.0f} {mean / base[lv]:7.3f}x"
        top = [m[levels[0]] for _, m in rs]
        spread = 0.0
        if len(top) > 1:
            spread = (max(top) - min(top)) / statistics.mean(top) * 100
        positions = ",".join(str(o) for o, _ in sorted(rs))
        print(line + f" {spread:6.1f}%  {positions}")

    # The floor, stated rather than left to be inferred. A condition measured
    # once has no floor of its own and is marked, because a single row cannot
    # tell a version effect from where it happened to sit in the job.
    singles = [k for k, v in runs.items() if len(v) < 2]
    if singles:
        print(f"\nmeasured once, so carrying no spread of its own: {', '.join(singles)}")
    paired = [
        (max(m[levels[0]] for _, m in v) - min(m[levels[0]] for _, m in v))
        / statistics.mean(m[levels[0]] for _, m in v)
        * 100
        for v in runs.values()
        if len(v) > 1
    ]
    if paired:
        print(
            f"\nnoise floor from same-condition pairs: {min(paired):.2f}% to "
            f"{max(paired):.2f}%. No ratio above should be read as real unless it "
            "clears the top of that range."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
