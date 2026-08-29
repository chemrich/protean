"""The pair reading has to cancel drift, and has to show what it could not.

`bench/molstar-capture/read_pairs.py` is how the backlog-43 runs are read: each
condition twice, in mirrored positions, so a runner that drifts over the job
does not put its drift on whichever condition ran late.

The occlusion-off control is why this is not a nicety. Read row by row it showed
5.5.0 -> 5.6.0 at 0.890x and 5.6.0 -> 5.5.0 at 0.894x — two adjacent steps of
the same size pointing at opposite versions. Either one read alone is a version
effect that is not there. As pair means the two versions are 0.99x apart.

The other half is the spread column. That same control's two 5.5.0 rows are 22%
apart, which is larger than the 16% effect being chased, so nothing in it can
carry that claim. A table that printed the ratio and not the spread would have
looked like an answer.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

BENCH = Path(__file__).resolve().parent.parent / "bench" / "molstar-capture"
MODULE = BENCH / "read_pairs.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_read_pairs", MODULE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


read_pairs = _load()


def write(directory: Path, rows: list[tuple[str, float]]) -> None:
    for i, (label, ms) in enumerate(rows, start=1):
        payload = {
            "label": label,
            "ok": True,
            "levels": [{"sampleLevel": 4, "stats": {"median": ms}}],
        }
        (directory / f"{i:02d}-{label.replace(':', '_')}.json").write_text(
            json.dumps(payload)
        )


def test_a_mirrored_pair_cancels_a_linear_drift(tmp_path, capsys):
    # The occlusion-off control's actual shape: every adjacent step the same
    # size, alternating which version it flatters. A and B are genuinely equal;
    # row by row it looks like a 10% effect twice, pointing both ways.
    write(tmp_path, [("A", 1000), ("B", 900), ("B", 900), ("A", 810)])
    sys.argv = ["read_pairs.py", str(tmp_path), "--baseline", "A"]
    assert read_pairs.main() == 0
    out = capsys.readouterr().out
    # Asserted on what the table PRINTS, not on a mean recomputed here.
    # Computing it in the test left the table free to print the first row of
    # each pair instead — which is the one thing this tool must never do, and
    # deleting the mean left every test in this file green.
    a_row = next(row for row in out.splitlines() if row.startswith("A "))
    b_row = next(row for row in out.splitlines() if row.startswith("B "))
    assert "905" in a_row, a_row
    assert "900" in b_row, b_row
    assert "0.994x" in b_row, b_row


def test_the_positions_are_reported_so_the_mirroring_can_be_checked(tmp_path, capsys):
    write(tmp_path, [("A", 100), ("B", 110), ("B", 110), ("A", 100)])
    sys.argv = ["read_pairs.py", str(tmp_path), "--baseline", "A"]
    assert read_pairs.main() == 0
    out = capsys.readouterr().out
    assert "1,4" in out and "2,3" in out


def test_the_noise_floor_is_printed_and_is_the_widest_pair(tmp_path, capsys):
    # A run whose own repeats disagree by more than the effect cannot carry it,
    # and the table has to say so rather than leave it to be worked out.
    write(tmp_path, [("A", 1000), ("B", 1160), ("B", 1160), ("A", 1240)])
    sys.argv = ["read_pairs.py", str(tmp_path), "--baseline", "A"]
    assert read_pairs.main() == 0
    out = capsys.readouterr().out
    assert "noise floor" in out
    assert "21.4" in out  # (1240-1000)/1120
    # And per row, not only in the summary line. The summary is computed
    # separately, so asserting only on it let the spread column be deleted
    # while this test stayed green — and the spread column is what says the
    # 16% being chased does not clear this run's own repeats.
    a_row = next(row for row in out.splitlines() if row.startswith("A "))
    b_row = next(row for row in out.splitlines() if row.startswith("B "))
    assert "21.4%" in a_row, a_row
    assert "0.0%" in b_row, b_row


def test_a_condition_measured_once_is_called_out(tmp_path, capsys):
    write(tmp_path, [("A", 100), ("B", 110), ("A", 100)])
    sys.argv = ["read_pairs.py", str(tmp_path), "--baseline", "A"]
    assert read_pairs.main() == 0
    assert "measured once" in capsys.readouterr().out


def test_a_failed_row_is_dropped_rather_than_averaged_in(tmp_path):
    write(tmp_path, [("A", 100), ("A", 100)])
    (tmp_path / "03-B.json").write_text(json.dumps({"label": "B", "ok": False}))
    runs = read_pairs.load(tmp_path)
    assert "B" not in runs


def test_an_unknown_baseline_is_refused_rather_than_defaulted(tmp_path, capsys):
    write(tmp_path, [("A", 100), ("A", 100)])
    sys.argv = ["read_pairs.py", str(tmp_path), "--baseline", "nope"]
    assert read_pairs.main() == 1
