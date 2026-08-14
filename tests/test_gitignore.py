"""The ignore rules have to cover a symlink, not only a directory.

A `.gitignore` entry with a trailing slash matches a **directory** and not a
symlink to one. So `viewer/node_modules/` ignores the real thing while
`git add -A` happily stages a symlink of the same name as a mode-120000 blob
whose content is an absolute path from whoever's machine made it.

That is not a hypothesis. The `cryoem-volumes` branch carries exactly that
commit, and it went unnoticed until the volume work was ported off it. Reviewers
of `docs/going-public.md` flagged the same gap as the reason its "no absolute
paths committed" row cannot be relied on across a merge, since the flip to public
is irreversible.

Symlinking a checkout's `node_modules` or built `static/` in from elsewhere is a
normal thing to do when working across worktrees, so the rule has to hold for
both shapes. These tests run `git check-ignore` against a throwaway repository
seeded with the real `.gitignore`, which keeps them honest about git's actual
behaviour rather than about our reading of the manual.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GITIGNORE = REPO / ".gitignore"

# Every path here is one a contributor might reasonably symlink in rather than
# rebuild: node_modules across worktrees, and the built viewer that
# `npm run build` writes into the package.
SYMLINKABLE = [
    "viewer/node_modules",
    "viewer/dist",
    "src/protean_mcp/static",
]


@pytest.fixture(scope="module")
def scratch_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real git repo carrying this repo's actual ignore rules."""
    root = tmp_path_factory.mktemp("ignore-rules")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text(GITIGNORE.read_text())
    return root


def _is_ignored(repo: Path, relative: str) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", "--", relative],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


@pytest.mark.parametrize("relative", SYMLINKABLE)
def test_a_symlink_is_ignored_not_only_a_directory(scratch_repo, relative):
    """The regression this file exists for.

    With a trailing slash on the rule, this is the case that slips through: the
    directory form is ignored and the symlink form is staged.
    """
    target = scratch_repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to("/somewhere/else/entirely")

    assert _is_ignored(scratch_repo, relative), (
        f"{relative} is not ignored when it is a symlink, so `git add -A` would "
        f"commit it as a mode-120000 blob holding an absolute path"
    )


@pytest.mark.parametrize("relative", SYMLINKABLE)
def test_the_directory_form_is_still_ignored(scratch_repo, relative):
    """Dropping the trailing slash must not lose the case it already handled."""
    target = scratch_repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    target.mkdir(parents=True, exist_ok=True)
    (target / "a-file").write_text("x")

    assert _is_ignored(scratch_repo, f"{relative}/a-file"), (
        f"{relative}/ is no longer ignored as a directory"
    )


def test_no_viewer_or_build_rule_carries_a_trailing_slash():
    """Catch the next one at the source rather than one path at a time.

    The parametrised tests above only cover paths someone thought to list. This
    asserts the property itself for every rule naming a directory we generate,
    so a new `viewer/whatever/` entry fails here instead of years later.
    """
    offenders = [
        line
        for line in GITIGNORE.read_text().splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and line.rstrip().endswith("/")
        and any(line.startswith(prefix) for prefix in ("viewer/", "src/"))
    ]
    assert offenders == [], (
        f"these rules end in a slash, so they ignore a directory but not a "
        f"symlink of the same name: {offenders}"
    )
