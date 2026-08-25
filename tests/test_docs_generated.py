"""The generated documentation must match the source it is generated from.

`docs/tools.md` and the README's tool table are produced by
`docs/generate/tool_reference.py`. Before that generator existed, the README
said "54 tools" above a hand-maintained list of 55 names while the source
registered 65 — three numbers, none of them right, and nothing in the repo
could notice.

These tests are the noticing.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
GENERATOR = REPO / "docs" / "generate" / "tool_reference.py"


def _generator() -> ModuleType:
    """Import the generator by path — `docs/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("tool_reference", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generated_docs_are_current():
    """`--check` is the whole test: it re-renders and compares."""
    done = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert done.returncode == 0, (
        f"{done.stdout}{done.stderr}\nRun: uv run python docs/generate/tool_reference.py"
    )


def test_every_registered_tool_is_filed_under_an_area():
    """A new tool must be given an area, rather than vanishing from the docs.

    The ten tools missing from the old hand-written table were the newest ones.
    Filing is deliberate here: an unknown name raises rather than landing in an
    "Other" bucket nobody reads.
    """
    module = _generator()
    tools = module.collect()
    filed = {name for names in module.AREAS.values() for name in names}
    registered = {name for name, _, _ in tools}

    assert registered - filed == set(), (
        f"Registered but filed under no area: {sorted(registered - filed)}. "
        f"Add each to AREAS in {GENERATOR.relative_to(REPO)}."
    )
    assert filed - registered == set(), (
        f"Filed under an area but not registered: {sorted(filed - registered)}."
    )


def test_every_tool_has_a_summary_line():
    """A tool with no docstring would render as a blank entry in the reference."""
    undocumented = [name for name, _, summary in _generator().collect() if not summary]
    assert not undocumented, f"No docstring: {undocumented}"


@pytest.mark.parametrize(
    "page",
    [
        "README.md",
        "docs/README.md",
        "docs/getting-started.md",
        "docs/cookbook.md",
        "docs/gallery.md",
        "docs/selections.md",
        "docs/tools.md",
        "docs/troubleshooting.md",
        "docs/for-pymol-users.md",
    ],
)
def test_documentation_links_and_images_resolve(page: str):
    """Every relative link and image in the documentation points at something.

    Cheap, and it catches the failure that makes documentation worse than none:
    a page confidently pointing at a file that was renamed or never written.
    """
    source = REPO / page
    broken = []
    for link in re.findall(r"]\(([^)]+)\)", source.read_text()):
        # Strip a `"title"` suffix and any #anchor: neither is a path.
        target = link.split(" ")[0].split("#")[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if not (source.parent / target).exists():
            broken.append(target)
    assert not broken, f"{page} points at missing files: {sorted(set(broken))}"
