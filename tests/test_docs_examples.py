"""Every tool call printed in the documentation must be a call you could make.

Documentation drifts in a way prose review does not catch: a renamed argument,
a tool that never existed, a plausible file extension. This branch shipped
`save_session("scene.molx")` in two places before this test was written — the
extension is `.protean`, and nothing would have noticed.

So: parse the python blocks out of every documentation page, find the calls
whose names are protean tools, and check each against the real signature.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.docs_pages import documentation_pages

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "src" / "protean_mcp" / "server.py"

#: Derived from `docs/README.md`, not listed here — see `tests/docs_pages.py`.
PAGES = documentation_pages()

BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)

# Names that appear in documentation blocks and are deliberately not tool calls.
#
# This is an allowlist, and the default is now failure rather than silence: a
# call to a name that is neither a registered tool nor listed here fails the
# page. It used to be skipped, which meant a documented call to a tool that does
# not exist — the exact thing this file was written to catch — passed without a
# word. Nothing in the documentation needed the escape hatch when it was flipped;
# these builtins are kept so an illustrative snippet can use them.
NOT_A_TOOL = {"print", "len", "range", "sorted", "cmd", "dict", "list"}


def _signatures() -> dict[str, ast.arguments]:
    """Every registered tool's argument spec, straight from the decorators."""
    found = {}
    for node in ast.walk(ast.parse(SERVER.read_text())):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            named = getattr(target, "id", None) or getattr(target, "attr", None)
            if named in {"_tool", "tool"}:
                found[node.name] = node.args
    return found


TOOLS = _signatures()


def _calls(source: str):
    """Top-level-ish calls in a documentation snippet, by name."""
    # Snippets are illustrative, not runnable files: a stray `...` or an
    # unbalanced fragment should skip rather than fail the whole page.
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            yield node


@pytest.mark.parametrize("page", PAGES)
def test_documented_calls_use_real_tools_and_real_arguments(page: str):
    text = (REPO / page).read_text()
    problems: list[str] = []

    for block in BLOCK.findall(text):
        for call in _calls(block):
            name = call.func.id
            if name in NOT_A_TOOL:
                continue
            if name not in TOOLS:
                problems.append(
                    f"{name}() is not a registered tool. If it is a helper in an "
                    f"illustrative snippet, add it to NOT_A_TOOL deliberately."
                )
                continue
            spec = TOOLS[name]
            valid = {a.arg for a in spec.args} | {a.arg for a in spec.kwonlyargs}
            for keyword in call.keywords:
                if keyword.arg is not None and keyword.arg not in valid:
                    problems.append(
                        f"{name}({keyword.arg}=...) — no such argument. "
                        f"Takes: {', '.join(sorted(valid))}"
                    )
            positional = len(call.args)
            if positional > len(spec.args):
                problems.append(
                    f"{name}() given {positional} positional arguments, "
                    f"but takes at most {len(spec.args)}"
                )

    assert not problems, f"{page}:\n" + "\n".join(
        f"  - {p}" for p in sorted(set(problems))
    )


def test_the_test_can_see_tools_at_all():
    """A guard on the guard: an empty TOOLS map would pass every page silently."""
    assert len(TOOLS) > 50, f"only found {len(TOOLS)} tools; the parser is broken"
    assert "snapshot" in TOOLS and "select" in TOOLS
