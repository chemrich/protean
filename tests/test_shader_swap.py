"""The benchmark's shader transplant has to fail loudly or it is worthless.

`bench/molstar-capture/shader_swap.py` splices one Mol\\* release's GLSL into
another release's prebuilt bundle, so that "which of the four files that changed
in 5.6.0 cost the 15%" can be measured rather than read off a diff. The whole
value of that measurement is that a row labelled "5.6.0 carrying 5.5.0's
ssao.frag" really is carrying it.

So every test here is an attempt to make the swap do the wrong thing quietly.
This repo has shipped that exact defect before: a shader patch written against
LF, against a Mol\\* that ships CRLF, which matched nothing inside a build that
stayed green and produced a viewer that looked right and captured three times
slower. A silent no-op here would be worse, because it would produce a *number*
— two identical rows that read as "this shader is not the cause".

The bundles themselves are 4.8 MB downloads and are not fetched here; the
synthetic fixtures below have the one property that matters, which is that the
GLSL lives in a backtick template literal in minified JavaScript.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "bench" / "molstar-capture" / "shader_swap.py"


def _load() -> ModuleType:
    # The benchmark is deliberately outside the package — it has to run against
    # Mol* 4.18.0 with no protean import at all — so it is loaded by path rather
    # than imported, and this test does not become a reason to package it.
    spec = importlib.util.spec_from_file_location("bench_shader_swap", MODULE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load the benchmark's shader swap from {MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shader_swap = _load()

SSAO_A = (
    "\nprecision highp float;\nuniform sampler2D tDepth;\n"
    "#define dNSamples 128\n"
    "// StarCraft II Ambient Occlusion by [Filion and McNaughton 2008]\n"
    "void main(void) { gl_FragColor = vec4(1.0); }\n"
)
SSAO_B = SSAO_A.replace("vec4(1.0)", "vec4(0.5)")


def bundle(*shaders: str) -> str:
    """A minified bundle's shape: shaders as backtick literals between code."""
    parts = ['"use strict";var m=(()=>{']
    for i, glsl in enumerate(shaders):
        parts.append(f"var s{i}=`{glsl}`;")
    parts.append("return m})();")
    return "".join(parts)


def test_a_swap_replaces_only_the_named_shader():
    other = SSAO_A.replace("StarCraft II Ambient Occlusion", "Some Other Pass")
    before = bundle(other, SSAO_A)
    after, record = shader_swap.swap_shader(before, "ssao.frag", SSAO_B)
    assert shader_swap.read_shader(after, "ssao.frag") == SSAO_B
    # The neighbouring literal, and the JavaScript around both, are untouched.
    assert after.replace(SSAO_B, SSAO_A, 1) == before
    assert record["fromLength"] == len(SSAO_A)
    assert record["toLength"] == len(SSAO_B)
    assert record["fromDigest"] != record["toDigest"]


def test_a_swap_that_would_change_nothing_is_refused():
    # The failure this whole module exists to prevent. A no-op swap does not
    # produce an error, it produces a *measurement* — two matching rows that
    # read as "this shader is not the cause of the step".
    with pytest.raises(shader_swap.SwapError, match="byte-identical"):
        shader_swap.swap_shader(bundle(SSAO_A), "ssao.frag", SSAO_A)


def test_an_anchor_in_two_literals_is_refused():
    # Mol* could reasonably grow a second shader quoting the same paper. Picking
    # whichever one came first would splice into the wrong pass and still report
    # success.
    with pytest.raises(shader_swap.SwapError, match="different literals"):
        shader_swap.swap_shader(bundle(SSAO_A, SSAO_A + "\n"), "ssao.frag", SSAO_B)


def test_a_literal_that_is_not_a_shader_is_refused():
    # If the bundle ever stops holding its GLSL in plain template literals, the
    # backtick walk finds *something* — a chunk of minified JavaScript — and
    # would happily overwrite it. That has to stop the run, not corrupt 4.8 MB.
    not_glsl = "StarCraft II Ambient Occlusion is a paper, not a shader"
    with pytest.raises(shader_swap.SwapError, match="does not look like a shader"):
        shader_swap.swap_shader(bundle(not_glsl), "ssao.frag", SSAO_B)


def test_an_anchor_outside_any_template_literal_is_refused():
    # A bundle that ships its GLSL some other way — a quoted string, an external
    # file — still contains the anchor. Without this check the backtick walk
    # returns (-1, -1), which reads as "the literal is the whole file up to the
    # last byte", passes the marker check because the markers really are in
    # there, and splices the replacement over 4.8 MB of JavaScript.
    with pytest.raises(shader_swap.SwapError, match="not inside a template literal"):
        shader_swap.swap_shader(SSAO_A.replace("\n", " "), "ssao.frag", SSAO_B)


def test_a_missing_anchor_is_refused():
    with pytest.raises(shader_swap.SwapError, match="anchor not found"):
        shader_swap.swap_shader(
            bundle(SSAO_B.replace("StarCraft", "x")), "ssao.frag", SSAO_A
        )


def test_a_backtick_in_the_replacement_is_refused():
    # The same trap that cost this project about six build cycles in the
    # painterly work: a backtick inside GLSL terminates the template literal it
    # is spliced into, and the result is a syntax error 3 MB from the cause.
    with pytest.raises(shader_swap.SwapError, match="backtick"):
        shader_swap.swap_shader(bundle(SSAO_A), "ssao.frag", SSAO_B + "// `tick`")


def test_an_unknown_shader_name_is_refused():
    with pytest.raises(shader_swap.SwapError, match="no anchor known"):
        shader_swap.swap_shader(bundle(SSAO_A), "not-a-shader.frag", SSAO_B)


def test_reading_an_unknown_shader_name_is_refused_too():
    # Both entry points check, and both are exercised: deleting the check in
    # `read_shader` alone left every other test in this file green, because a
    # transplant checks the name again on the way in. That is the shape of a
    # guard nothing can see.
    with pytest.raises(shader_swap.SwapError, match="no anchor known"):
        shader_swap.read_shader(bundle(SSAO_A), "not-a-shader.frag")


def test_the_interface_change_is_reported_rather_than_judged():
    # A transplant is only meaningful while the JavaScript still sets everything
    # the shader reads. That is not decidable here, so it is reported — but it
    # has to be reported *accurately*, because it is the one field that says a
    # row is meaningless.
    same = shader_swap.swap_shader(bundle(SSAO_A), "ssao.frag", SSAO_B)[1]
    assert same["interfaceUnchanged"] is True
    widened = SSAO_B.replace(
        "#define dNSamples 128", "#define dNSamples 128\nuniform float uNew;"
    )
    changed = shader_swap.swap_shader(bundle(SSAO_A), "ssao.frag", widened)[1]
    assert changed["interfaceUnchanged"] is False


def test_every_anchor_names_a_shader_file_that_molstar_ships():
    # The registry is a list of file names; a typo in one would surface as
    # "anchor not found" much later, in a job that costs half an hour.
    for name in shader_swap.SHADER_ANCHORS:
        assert name.endswith(".frag"), name


def test_resolve_source_refuses_a_bundle_that_was_never_fetched(tmp_path):
    with pytest.raises(shader_swap.SwapError, match="no bundle for"):
        shader_swap.resolve_source("9.9.9", tmp_path)


def test_resolve_source_refuses_a_candidate_file_that_is_not_there(tmp_path):
    # A mistyped path to a proposed repair has to stop the run. Without the
    # check it surfaces as a FileNotFoundError from inside the harness, which
    # reads like the benchmark broke rather than like the argument was wrong.
    with pytest.raises(shader_swap.SwapError, match="shader file not found"):
        shader_swap.resolve_source(f"@{tmp_path / 'nope.frag'}", tmp_path)


def test_resolve_source_reads_a_candidate_shader_from_a_file(tmp_path):
    # The form a proposed *repair* is measured through, before it is proposed.
    candidate = tmp_path / "ssao-candidate.frag"
    candidate.write_text(SSAO_B)
    text, provenance = shader_swap.resolve_source(f"@{candidate}", tmp_path)
    assert text == SSAO_B
    assert provenance.startswith("file:")


def test_apply_swaps_writes_the_patched_bundle_and_records_it(tmp_path):
    source = tmp_path / "5.5.0" / "build" / "viewer"
    source.mkdir(parents=True)
    (source / "molstar.js").write_text(bundle(SSAO_A))
    target = tmp_path / "molstar.js"
    target.write_text(bundle(SSAO_B))

    records = shader_swap.apply_swaps(target, ["ssao.frag=5.5.0"], tmp_path)

    assert shader_swap.read_shader(target.read_text(), "ssao.frag") == SSAO_A
    assert records[0]["source"] == "bundle:5.5.0"


def test_apply_swaps_with_nothing_to_do_leaves_the_bundle_alone(tmp_path):
    target = tmp_path / "molstar.js"
    target.write_text(bundle(SSAO_A))
    assert shader_swap.apply_swaps(target, [], tmp_path) == []
    assert target.read_text() == bundle(SSAO_A)


def test_a_malformed_swap_spec_is_refused(tmp_path):
    target = tmp_path / "molstar.js"
    target.write_text(bundle(SSAO_A))
    with pytest.raises(shader_swap.SwapError) as raised:
        shader_swap.apply_swaps(target, ["ssao.frag"], tmp_path)
    # Asserted against the message rather than through `match=`, and it matters.
    # `match=` searches the whole `str(exception)`, a spec with no `=` in it
    # falls through to "no bundle for '' at <tmp_path>/...", and pytest names
    # `tmp_path` after the test — so `match="malformed"` passed by matching the
    # *directory*, and went on passing with the guard deleted. Found by removing
    # the guard and watching the test not notice.
    assert str(raised.value).startswith("malformed --shader-swap")


# --- the candidate shaders the 5.6.0 experiment measures --------------------
#
# `make_candidates.py` writes these from the real bundles, which a unit test
# cannot fetch. What it can do is assert the committed files are what they claim
# to be — because they are what CI splices into a 4.8 MB bundle, and a stale or
# hand-edited one would produce a row that is confidently mislabelled rather
# than an error.

CANDIDATES = REPO / "bench" / "molstar-capture" / "candidates"

FIXED_PREDICATE_24BIT = "return depth >= 0.99999994;"
FIXED_PREDICATE_16BIT = "return depth >= 0.999;"
BROKEN_PREDICATE = "return depth == 1.0;"


def test_the_candidate_shaders_are_all_present():
    names = sorted(p.name for p in CANDIDATES.glob("*.frag"))
    assert names == [
        "ssao-5.5.0-bgfix.frag",
        "ssao-5.6.0-bgfix.frag",
        "ssao-5.6.0-no-bg-guard.frag",
        "ssao-5.6.0-no-bounds-skip.frag",
        "ssao-blur-bgfix.frag",
    ]


def test_each_single_commit_revert_reverts_exactly_its_own_commit():
    # The two variants exist to price one upstream commit each. If either one
    # also carried the other's change, its row would be labelled for a commit it
    # does not isolate, and the split would be arithmetic over the wrong things.
    skip = (CANDIDATES / "ssao-5.6.0-no-bounds-skip.frag").read_text()
    guard = (CANDIDATES / "ssao-5.6.0-no-bg-guard.frag").read_text()

    # PR #1740 + #1741: the skip is gone from the one, present in the other.
    assert "isOutsideBounds(offset.xy)" not in skip
    assert "nSamples -= 1.0" not in skip
    assert "occlusion /= float(dNSamples);" in skip
    assert "isOutsideBounds(offset.xy)" in guard
    assert "nSamples -= 1.0" in guard

    # PR #1737's shader half: the opaque guard is gone from the one, kept in the
    # other. Only the opaque one — the transparent guard predates both releases
    # and must survive in both files.
    assert "if (!isBackground(sampleDepth))" not in guard
    assert "if (!isBackground(sampleDepth))" in skip
    assert "isBackground(sampleDepthWithAlpha.x)" in skip
    assert "isBackground(sampleDepthWithAlpha.x)" in guard


@pytest.mark.parametrize(
    ("name", "predicate"),
    [
        ("ssao-5.5.0-bgfix.frag", FIXED_PREDICATE_24BIT),
        ("ssao-5.6.0-bgfix.frag", FIXED_PREDICATE_24BIT),
        # The blur reads packUnitIntervalToRG's 16-bit encoding, where a
        # background texel round-trips to 0.99998468 and the 24-bit constant
        # cannot fire. Giving it the wrong one is not a compile error — it is a
        # silent no-op, and it shipped once.
        ("ssao-blur-bgfix.frag", FIXED_PREDICATE_16BIT),
    ],
)
def test_each_candidate_carries_the_repair_and_not_the_bug(name, predicate):
    text = (CANDIDATES / name).read_text()
    assert predicate in text, name
    assert BROKEN_PREDICATE not in text, name


def test_the_candidate_shaders_have_no_backtick():
    # They are spliced into a JavaScript template literal. One backtick anywhere
    # in one of them is a syntax error 3 MB from its cause, in a job that costs
    # half an hour.
    for path in CANDIDATES.glob("*.frag"):
        assert "`" not in path.read_text(), path.name


def test_the_two_ssao_candidates_differ_only_below_the_predicate():
    # The whole point of the pair is that one variable changes. If they differed
    # anywhere else, the ratio between their two rows would not be the cost of
    # 5.6.0's loop rework — and nothing downstream would notice.
    a = (CANDIDATES / "ssao-5.5.0-bgfix.frag").read_text()
    b = (CANDIDATES / "ssao-5.6.0-bgfix.frag").read_text()
    assert a != b
    cut = "// StarCraft II Ambient Occlusion"
    assert a[: a.index(cut)] != b[: b.index(cut)], (
        "5.6.0 added isOutsideBounds() and dropped the clamps from the depth "
        "getters, both of which are above main()"
    )
    # Same interface, or the transplant is not comparing like with like.
    assert shader_swap.uniform_lines(a) == shader_swap.uniform_lines(b)


def test_run_conf_only_names_candidate_files_that_exist():
    # The version list is a string in a config file; a typo in a path there
    # fails eight rows into a job, after twenty minutes of runner time.
    conf = (REPO / "bench" / "molstar-capture" / "run.conf").read_text()
    referenced = set()
    for line in conf.splitlines():
        if not line.startswith("versions="):
            continue
        for entry in line.split("=", 1)[1].split(","):
            for part in entry.split(":"):
                _, _, source = part.partition("=")
                if source.startswith("@"):
                    referenced.add(source[1:])
    assert referenced, "no candidate file is referenced; this test would pass blind"
    for path in sorted(referenced):
        assert (REPO / path).is_file(), path
