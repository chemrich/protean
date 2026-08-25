#!/usr/bin/env python
"""Generate `docs/tools.md` and the README's tool table from the source.

    uv run python docs/generate/tool_reference.py          # write both
    uv run python docs/generate/tool_reference.py --check  # fail if stale

Why generate rather than write: the hand-maintained table said "54 tools"
above a list of 55 names while the source registered 65, and the ten missing
ones were the newest and most distinctive part of the surface. Three numbers,
none of them right, and nothing could notice. `tests/test_docs_generated.py`
runs `--check`, so that particular drift now fails a build instead of
misleading a reader.

The README table is written between two HTML comment markers, so the prose
around it stays hand-written.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "src" / "protean_mcp" / "server.py"
TOOLS_MD = REPO / "docs" / "tools.md"
README = REPO / "README.md"

BEGIN = "<!-- BEGIN GENERATED TOOL TABLE -->"
END = "<!-- END GENERATED TOOL TABLE -->"

# Which area each tool belongs to, and the order the areas are presented in.
# A tool missing from here is a hard error rather than a silent "Other" bucket:
# the next tool anyone adds should be filed deliberately, and an uncategorised
# one is exactly what went unnoticed last time.
AREAS: dict[str, tuple[str, ...]] = {
    "Session": (
        "open_viewer",
        "fetch_structure",
        "clear_viewer",
        "save_session",
        "load_session",
        "capabilities",
    ),
    "Selections": (
        "select",
        "combine",
        "near",
        "invert",
        "list_selections",
        "remove",
    ),
    "Display": (
        "show",
        "hide",
        "unhide",
        "color",
        "size",
        "opacity",
        "label",
        "measure",
    ),
    "One-call views": (
        "ligand_view",
        "pocket_view",
        "interface_view",
        "mutation_view",
        "crosslink_view",
        "pharmacophore_view",
    ),
    "Custom themes": ("define_field", "define_elements"),
    "Camera": (
        "focus",
        "orient",
        "reset_view",
        "lens",
        "spin",
        "keyframe",
        "list_keyframes",
    ),
    "Analysis": (
        "interface",
        "superpose",
        "conservation",
        "electrostatics",
        "sasa",
    ),
    "Scalar colouring": (
        "color_by_potential",
        "color_by_conservation",
        "color_by_rmsf",
    ),
    "Style": (
        "preset",
        "background",
        "lighting",
        "effects",
        "shading",
        "material",
        "path_trace",
    ),
    "Capture": (
        "screenshot",
        "snapshot",
        "turntable",
        "boil",
        "record_trajectory",
        "record_timeline",
        "movie",
    ),
    "Trajectories": ("load_trajectory", "frame", "rmsf", "rmsd_series"),
    "Volumes": (
        "load_volume",
        "isosurface",
        "volume_info",
        "list_volumes",
        "remove_volume",
    ),
}


def _decorated_as_tool(node: ast.AST) -> bool:
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "_tool":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "tool":
            return True
    return False


def _signature(node: ast.AST) -> str:
    args = node.args
    rendered: list[str] = []
    padded = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(args.args, padded, strict=True):
        piece = arg.arg
        if arg.annotation is not None:
            piece += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            piece += f" = {ast.unparse(default)}"
        rendered.append(piece)
    return f"{node.name}({', '.join(rendered)})"


def collect() -> list[tuple[str, str, str]]:
    """Every registered tool as (name, signature, first docstring line)."""
    tree = ast.parse(SERVER.read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.AsyncFunctionDef, ast.FunctionDef)
        ) and _decorated_as_tool(node):
            summary = (ast.get_docstring(node) or "").strip().split("\n")[0]
            found.append((node.name, _signature(node), summary))
    return sorted(found)


def _filed(tools: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, str, str]]]:
    by_name = {name: (name, sig, summary) for name, sig, summary in tools}
    placed: dict[str, list[tuple[str, str, str]]] = {}
    seen: set[str] = set()
    for area, names in AREAS.items():
        placed[area] = []
        for name in names:
            if name not in by_name:
                raise SystemExit(
                    f"{area!r} lists {name!r}, which is not a registered tool"
                )
            placed[area].append(by_name[name])
            seen.add(name)
    missing = sorted(set(by_name) - seen)
    if missing:
        raise SystemExit(
            "These tools are registered but filed under no area in "
            f"{Path(__file__).name}: {', '.join(missing)}. Add each to AREAS."
        )
    return placed


def render_tools_md(tools: list[tuple[str, str, str]]) -> str:
    placed = _filed(tools)
    out = [
        "# Tool reference",
        "",
        f"All {len(tools)} tools protean registers, grouped by what you would be",
        "doing when you reach for one.",
        "",
        "**This page is generated** from the decorators in",
        "`src/protean_mcp/server.py` by",
        "[`docs/generate/tool_reference.py`](generate/tool_reference.py). Edit the",
        "docstrings, not this file.",
        "",
        "Each tool's full argument documentation lives in its docstring, which is",
        "what your model sees. `capabilities()` reports the live value lists for",
        "representations, colour themes, size themes, lighting rigs, shading",
        "styles, material finishes, gradients, presets and path-trace quality —",
        "read off the running Mol\\*, so it is the authority rather than any table.",
        "",
        "> **Values are checked, not guessed.** No style argument is an `enum` in",
        "> the generated JSON schema — they are plain strings. What protects you is",
        "> the other end: an unknown value is refused *by name, with the complete",
        "> list of valid ones attached*, rather than quietly drawing nothing.",
        "",
        "## Contents",
        "",
    ]
    for area in placed:
        anchor = area.lower().replace(" ", "-").replace("*", "")
        out.append(f"- [{area}](#{anchor}) — {len(placed[area])} tools")
    out.append("")
    for area, entries in placed.items():
        out += ["---", "", f"## {area}", ""]
        for name, signature, summary in entries:
            out += [f"### `{name}`", "", "```python", signature, "```", "", summary, ""]
    out += [
        "---",
        "",
        "## See also",
        "",
        "- [Selections](selections.md) — the language every `selection` argument takes",
        "- [The gallery](gallery.md) — what each style value looks like",
        "- [The cookbook](cookbook.md) — these tools in combination",
        "- [Troubleshooting](troubleshooting.md) — what a refusal means",
        "",
    ]
    return "\n".join(out)


def render_readme_table(tools: list[tuple[str, str, str]]) -> str:
    placed = _filed(tools)
    rows = ["| Area | Tools |", "|---|---|"]
    for area, entries in placed.items():
        names = ", ".join(f"`{name}`" for name, _, _ in entries)
        rows.append(f"| {area} | {names} |")
    return "\n".join(rows)


def splice(text: str, table: str) -> str:
    start, finish = text.find(BEGIN), text.find(END)
    if start == -1 or finish == -1:
        raise SystemExit(f"README.md is missing the {BEGIN} / {END} markers")
    return text[: start + len(BEGIN)] + "\n" + table + "\n" + text[finish:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if either file is stale"
    )
    args = parser.parse_args()

    tools = collect()
    wanted_tools_md = render_tools_md(tools)
    wanted_readme = splice(README.read_text(), render_readme_table(tools))

    if args.check:
        stale = []
        if not TOOLS_MD.is_file() or TOOLS_MD.read_text() != wanted_tools_md:
            stale.append(str(TOOLS_MD.relative_to(REPO)))
        if README.read_text() != wanted_readme:
            stale.append(str(README.relative_to(REPO)))
        if stale:
            print(
                f"Stale: {', '.join(stale)}\n"
                "Regenerate with: uv run python docs/generate/tool_reference.py",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"Up to date: {len(tools)} tools.")
        return

    TOOLS_MD.parent.mkdir(parents=True, exist_ok=True)
    TOOLS_MD.write_text(wanted_tools_md)
    README.write_text(wanted_readme)
    print(
        f"Wrote {TOOLS_MD.relative_to(REPO)} and the README table ({len(tools)} tools)."
    )


if __name__ == "__main__":
    main()
