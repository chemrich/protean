"""Which pages are documentation, derived rather than listed.

Both documentation tests used to carry a hardcoded page list, and they were not
even the same list: eight pages in `test_docs_examples.py`, nine in
`test_docs_generated.py`, with `benchmark.md` in neither despite
`docs/README.md` advertising it. Sixteen more files in `docs/` sat outside both.

A named list means a new page goes unchecked until someone remembers to edit
two literals — the same failure this repo already hit in CI, where a browser
suite named its test files and twenty new tests sat unexecuted behind three
green ticks.

So the list comes from `docs/README.md`, which already sorts every file in the
directory into one of two tables: **Documentation** tells you how to use protean
today, **Engineering records** describe how a piece of work was built. Adding a
page means adding the row you would have added anyway, and it is covered from
that moment.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "docs" / "README.md"

#: Link targets in a markdown table row: `| [gallery.md](gallery.md) | … |`
_LINK = re.compile(r"\]\(([A-Za-z0-9._-]+\.md)\)")


def _section(name: str) -> str:
    """The body of one `## heading` in the documentation index."""
    text = INDEX.read_text()
    marker = f"\n## {name}\n"
    if marker not in text:
        raise AssertionError(
            f"docs/README.md has no '## {name}' section. "
            f"tests/docs_pages.py derives the documented page list from it."
        )
    body = text.split(marker, 1)[1]
    # Stop at the next `## ` heading; `### ` subsections belong to this one.
    return re.split(r"\n## ", body, maxsplit=1)[0]


def documentation_pages() -> list[str]:
    """Repo-relative paths of every page that documents how to use protean.

    The two index pages are documentation about the documentation, so they are
    included without needing to link to themselves.
    """
    pages = ["README.md", "docs/README.md"]
    pages += [f"docs/{name}" for name in _LINK.findall(_section("Documentation"))]
    # dict.fromkeys rather than set(): the index's order is a reading order.
    return list(dict.fromkeys(pages))


def engineering_records() -> list[str]:
    """Repo-relative paths of the plan documents kept as a record.

    Not documentation. They describe decisions and dead ends rather than the
    current tool surface, and `docs/README.md` says so at length. They are
    derived here only so that a file in neither table can be noticed.
    """
    return [f"docs/{name}" for name in _LINK.findall(_section("Engineering records"))]
