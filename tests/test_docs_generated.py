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

from tests.docs_pages import documentation_pages, engineering_records

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


@pytest.mark.parametrize("page", documentation_pages())
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


def test_every_documentation_page_is_filed_in_the_index():
    """A page in `docs/` that neither table mentions is a page nothing checks.

    The two lists in `docs/README.md` are what `tests/docs_pages.py` derives
    from, so a file missing from both is invisible to every test in this module
    — including the link check directly above. That is not hypothetical: this
    test found `molstar-capabilities.md`, added the same day, filed nowhere.
    """
    filed = {Path(page).name for page in documentation_pages() + engineering_records()}
    present = {page.name for page in (REPO / "docs").glob("*.md")}
    assert present - filed == set(), (
        f"In docs/ but in neither table of docs/README.md: {sorted(present - filed)}. "
        f"Add each to Documentation (how to use protean) or to Engineering "
        f"records (how a piece of work was built)."
    )


def test_the_derived_page_list_can_see_the_documentation():
    """A guard on the guard: an empty list would pass every page check silently.

    The same shape as `test_the_test_can_see_tools_at_all` in
    `test_docs_examples.py`. If `docs/README.md`'s headings are renamed, the
    derivation returns the two index pages and nothing else, and every
    parametrised test above would vanish rather than fail.
    """
    pages = documentation_pages()
    assert len(pages) > 5, f"only derived {len(pages)} pages: {pages}"
    for expected in ("README.md", "docs/gallery.md", "docs/cookbook.md"):
        assert expected in pages, f"{expected} missing from {pages}"
    for page in pages + engineering_records():
        assert (REPO / page).exists(), f"index points at missing file: {page}"
