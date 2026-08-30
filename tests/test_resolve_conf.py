"""A benchmark run's settings have to come from where the log says they do.

`bench/molstar-capture/run.conf` is append-only: each block is one measurement,
kept in order, so a table in a job summary can be traced back to the settings
that produced it. The workflow used to read each key with
`grep -E "^key=" run.conf | tail -1` — the last matching line in the *file*, not
in the block — so a block that did not mention a key silently inherited it from
an earlier experiment.

Run 33252777716 is the case this file exists for. It asked for twelve rows with
the occlusion pass on, to measure a regression that lives inside the occlusion
pass, and ran every one of them with `postprocessing=no-occlusion` inherited
from a block three experiments older. The result was not an error; it was a
clean, plausible, wrong table, and the only reason it was caught is that the job
echoes what it settled on.

So the rule these tests hold is: **a block is self-contained.** Anything it does
not say takes the documented default, never a neighbour's value.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "bench" / "molstar-capture"
MODULE = BENCH / "resolve_conf.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_resolve_conf", MODULE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


resolve_conf = _load()

TWO_BLOCKS = """\
# an older experiment, with a pass switched off
versions=4.18.0,5.4.1,5.4.2,5.11.0
levels=4,1
postprocessing=no-occlusion
baseline=4.18.0

# a newer one, which says nothing about postprocessing
versions=5.5.0,5.6.0
levels=4
repeats=4
baseline=5.5.0
"""


def test_a_block_does_not_inherit_from_an_older_block():
    # The regression itself. Before the fix this returned "no-occlusion".
    values, provenance = resolve_conf.resolve(TWO_BLOCKS, {})
    assert values["postprocessing"] == "default"
    assert provenance["postprocessing"].startswith("default")


def test_the_last_block_wins_for_what_it_does_say():
    values, provenance = resolve_conf.resolve(TWO_BLOCKS, {})
    assert values["versions"] == "5.5.0,5.6.0"
    assert values["levels"] == "4"
    assert values["repeats"] == "4"
    assert values["baseline"] == "5.5.0"
    assert "run.conf line" in provenance["versions"]


def test_provenance_names_the_line_a_value_came_from():
    # The field that would have made the bug obvious at a glance. A value and no
    # provenance is what let a setting from three experiments ago look normal.
    _, provenance = resolve_conf.resolve(TWO_BLOCKS, {})
    line_no = int(provenance["versions"].rsplit(" ", 1)[1])
    assert TWO_BLOCKS.splitlines()[line_no - 1] == "versions=5.5.0,5.6.0"


def test_a_workflow_input_overrides_the_block():
    values, provenance = resolve_conf.resolve(
        TWO_BLOCKS, {"IN_VERSIONS": "5.11.0", "IN_POSTPROCESSING": "none"}
    )
    assert values["versions"] == "5.11.0"
    assert values["postprocessing"] == "none"
    assert provenance["versions"] == "workflow input"


def test_an_empty_workflow_input_does_not_override():
    # `gh workflow run` with no -f sends the declared defaults, and a dispatched
    # run with a genuinely empty input must still fall through to the conf.
    values, _ = resolve_conf.resolve(TWO_BLOCKS, {"IN_VERSIONS": "   "})
    assert values["versions"] == "5.5.0,5.6.0"


def test_an_empty_conf_gives_every_documented_default():
    values, provenance = resolve_conf.resolve("", {})
    for key, (default, _) in resolve_conf.SETTINGS.items():
        assert values[key] == default, key
        assert provenance[key].startswith("default"), key


def test_comments_are_not_settings():
    # run.conf's comments quote the settings they are explaining, at the start of
    # a line often enough that this is not theoretical.
    conf = "versions=5.5.0\n#postprocessing=no-occlusion\n# levels=1\n"
    values, _ = resolve_conf.resolve(conf, {})
    assert values["postprocessing"] == "default"
    assert values["levels"] == "4,1"


def test_a_later_line_wins_within_one_block():
    conf = "versions=5.5.0\nrepeats=4\nrepeats=8\n"
    values, _ = resolve_conf.resolve(conf, {})
    assert values["repeats"] == "8"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("versions", "5.5.0;rm -rf /"),
        ("versions", "5.5.0 5.6.0"),
        ("versions", "$(id)"),
        ("versions", "`id`"),
        ("levels", "4,x"),
        ("repeats", "-1"),
        ("size", "800*600"),
        ("postprocessing", "no-ssao"),
        ("occlusionsamples", "1.5"),
    ],
)
def test_junk_is_refused(key, value, capsys):
    # Every one of these reaches bash inside a quoted expansion, so none of them
    # would execute. Refusing them anyway is the habit, and it also stops a
    # typo from failing nineteen rows one at a time.
    pattern = resolve_conf.SETTINGS[key][1]
    assert not re.match(pattern, value), f"{key}={value} should not be accepted"


def test_every_setting_the_workflow_reads_is_produced():
    # A key added here but not to the workflow's outputs, or the reverse, is a
    # run whose parameter silently reverts to a hard-coded default.
    workflow = (REPO / ".github" / "workflows" / "molstar-capture-bench.yml").read_text()
    for key in resolve_conf.SETTINGS:
        if key == "size":
            # split into width/height for the harness
            assert "steps.cfg.outputs.width" in workflow
            assert "steps.cfg.outputs.height" in workflow
            continue
        assert f"steps.cfg.outputs.{key}" in workflow, key


def test_the_real_run_conf_resolves_to_a_block_that_asks_for_occlusion():
    # The live file, not a fixture. The experiment it currently describes is
    # about the occlusion pass, so a run that settles on anything but the
    # default postprocessing is measuring the wrong thing — which is exactly
    # what happened once.
    values, provenance = resolve_conf.resolve((BENCH / "run.conf").read_text(), {})
    assert values["postprocessing"] == "default", provenance["postprocessing"]
    assert "5.6.0" in values["versions"]


def test_junk_actually_stops_the_run(tmp_path, monkeypatch, capsys):
    # `test_junk_is_refused` above checks the patterns; this checks that main()
    # applies them. Deleting the validation left every other test in this file
    # green, which is the shape of a guard nothing can see.
    conf = tmp_path / "run.conf"
    conf.write_text("versions=5.5.0 5.6.0\n")
    monkeypatch.setattr(sys, "argv", ["resolve_conf.py", "--conf", str(conf)])
    for key in resolve_conf.SETTINGS:
        monkeypatch.delenv(f"IN_{key.upper()}", raising=False)
    assert resolve_conf.main() == 1
    assert "::error::bad versions" in capsys.readouterr().err


def test_a_good_conf_is_written_out_for_the_workflow(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "run.conf"
    conf.write_text("versions=5.5.0,5.6.0\nsize=1024x768\n")
    monkeypatch.setattr(sys, "argv", ["resolve_conf.py", "--conf", str(conf)])
    for key in resolve_conf.SETTINGS:
        monkeypatch.delenv(f"IN_{key.upper()}", raising=False)
    assert resolve_conf.main() == 0
    out = capsys.readouterr().out
    # `size` is the one setting the harness does not take as written: it reaches
    # run_bench.py as two separate arguments.
    assert "width=1024" in out
    assert "height=768" in out
    assert "size=" not in out
    assert "versions=5.5.0,5.6.0" in out
