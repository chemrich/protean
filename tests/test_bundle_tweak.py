"""A named change to a bundle's JavaScript must be exact, or the row is a lie.

`bench/molstar-capture/bundle_tweak.py` exists because the leading candidate for
the Mol\\* 5.6.0 capture regression is in JavaScript rather than GLSL, and
without an intervention there the strongest available statement would be "the
shader was transplanted and the step survived" — an elimination. Backlog 40 was
criticised, fairly, for exactly that shape of argument.

An elimination names a suspect; an intervention convicts one. But only if the
intervention actually happened. A pattern that matched nothing would produce a
row labelled `candidates-1` that is a byte-for-byte duplicate of the stock row
beside it, and two matching rows read as "this is not the cause".

So the failure mode being guarded here is not an exception. It is a number.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "bench" / "molstar-capture" / "bundle_tweak.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_bundle_tweak", MODULE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bundle_tweak = _load()

# Mol* 5.6.0's minified `generateBlueNoiseVectors`, verbatim from
# build/viewer/molstar.js. Kept whole rather than reduced to the matched
# fragment, so the test exercises the pattern against the shape it will meet.
MINIFIED = (
    "function Hat(e,t){if(t.length>=e)return t;t.length===0&&t.push(Gbe());"
    "let r=Math.max(10,Math.min(30,Math.floor(e/10)));"
    "for(let n=t.length;n<e;n++){let o,i=-1;for(let a=0;a<r;a++){let A=Gbe(),s=1/0;"
    "for(let c of t){let l=I.distance(A,c);s=Math.min(s,l)}s>i&&(i=s,o=A)}t.push(o)}"
    "return t}"
)


def test_the_candidate_count_is_replaced_and_nothing_else_is():
    out, record = bundle_tweak.apply_tweak(MINIFIED, "candidates-1")
    assert "let r=1;" in out
    assert "Math.max(10," not in out
    # Everything either side is byte-identical: the surrounding loop still reads
    # `r`, so a substitution that ate a character would change behaviour.
    assert out == MINIFIED.replace(record["matched"], "1", 1)
    assert record["tweak"] == "candidates-1"
    assert record["atOffset"] == MINIFIED.index("Math.max(10,")


def test_a_pattern_that_matches_nothing_stops_the_run():
    # The whole point. 5.5.0 has no blue-noise generator at all, so asking for
    # this tweak on it must fail rather than serve the stock bundle under a
    # label saying it was changed.
    with pytest.raises(bundle_tweak.TweakError, match="matched 0 times"):
        bundle_tweak.apply_tweak("var a=1;", "candidates-1")


def test_a_pattern_that_matches_twice_stops_the_run():
    with pytest.raises(bundle_tweak.TweakError, match="matched 2 times"):
        bundle_tweak.apply_tweak(MINIFIED + MINIFIED, "candidates-1")


def test_an_unknown_tweak_is_refused():
    with pytest.raises(bundle_tweak.TweakError, match="no bundle tweak named"):
        bundle_tweak.apply_tweak(MINIFIED, "not-a-tweak")


def test_the_pattern_survives_a_different_minifier_naming():
    # It matches operators and literals, never identifiers, because identifiers
    # are the thing minification renames. A Mol* rebuild that assigns different
    # short names must not silently stop the tweak from applying.
    renamed = MINIFIED.replace("(e,t)", "(qq,zz)").replace("e/10", "qq/10")
    out, _ = bundle_tweak.apply_tweak(renamed, "candidates-1")
    assert "let r=1;" in out


def test_whitespace_between_the_arguments_is_tolerated():
    spaced = MINIFIED.replace(
        "Math.max(10,Math.min(30,Math.floor(e/10)))",
        "Math.max(10, Math.min(30, Math.floor(e / 10)))",
    )
    out, _ = bundle_tweak.apply_tweak(spaced, "candidates-1")
    assert "let r=1;" in out


def test_apply_tweaks_writes_the_file_and_records_what_it_did(tmp_path):
    target = tmp_path / "molstar.js"
    target.write_text(MINIFIED)
    records = bundle_tweak.apply_tweaks(target, ["candidates-1"])
    assert "let r=1;" in target.read_text()
    assert records[0]["what"].startswith("SSAO blue-noise candidateCount")


def test_apply_tweaks_with_nothing_to_do_leaves_the_file_alone(tmp_path):
    target = tmp_path / "molstar.js"
    target.write_text(MINIFIED)
    assert bundle_tweak.apply_tweaks(target, []) == []
    assert target.read_text() == MINIFIED


def test_every_tweak_says_what_it_does():
    # The description is printed in the CI log beside the row it produced. A
    # tweak whose row cannot be explained from the log is a number nobody can
    # use six months later.
    for name, (_, _, description) in bundle_tweak.TWEAKS.items():
        assert len(description) > 40, name


def test_a_tweak_that_would_change_nothing_is_refused(monkeypatch):
    # No entry in the live registry can trip this — `1` can never equal a
    # `Math.max(...)` expression — so the guard is exercised through a synthetic
    # entry. It is worth keeping because the registry is meant to grow, and a
    # tweak whose replacement matches what it replaces produces the failure this
    # whole module exists to prevent: a duplicate row wearing a label.
    monkeypatch.setitem(
        bundle_tweak.TWEAKS,
        "identity",
        (re.compile(r"let r=[0-9]+;"), "let r=1;", "a tweak that changes nothing"),
    )
    with pytest.raises(bundle_tweak.TweakError, match="would change nothing"):
        bundle_tweak.apply_tweak("function f(){let r=1;}", "identity")
