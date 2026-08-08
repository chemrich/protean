"""protean MCP server — Phase 1 tools: open_viewer, fetch_structure, screenshot."""

from __future__ import annotations

import base64
import datetime
import logging
import webbrowser
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from .connection import ViewerBridge, ViewerError
from .fetch import FetchError, fetch_structure_data
from .selections import SelectionError, to_molscript

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "protean",
    instructions=(
        "Agent-native molecular visualization built on Mol*. "
        "Call open_viewer first to launch the browser viewer, then "
        "fetch_structure to load molecules and screenshot to see the result."
    ),
)

_bridge: ViewerBridge | None = None


def _static_dir() -> Path | None:
    packaged = Path(__file__).parent / "static"
    if (packaged / "index.html").exists():
        return packaged
    repo_build = Path(__file__).resolve().parents[2] / "viewer" / "dist"
    if (repo_build / "index.html").exists():
        return repo_build
    return None


def get_bridge() -> ViewerBridge:
    global _bridge
    if _bridge is None:
        _bridge = ViewerBridge(static_dir=_static_dir())
    return _bridge


def _require_viewer() -> ViewerBridge:
    bridge = get_bridge()
    if not bridge.viewer_connected:
        raise ViewerError("No viewer connected — call open_viewer first.")
    return bridge


def _visibility_note(bridge: ViewerBridge) -> str:
    """Flag a backgrounded tab: loads still work there, but only via the pump."""
    visibility = bridge.viewer_visibility
    if visibility is None or visibility == "visible":
        return ""
    return f" (tab is {visibility} — rendering runs on the background-tab pump)"


@mcp.tool()
async def open_viewer(timeout: float = 20) -> str:
    """Launch the protean viewer in a browser tab and wait for it to connect.

    Idempotent: if a viewer is already connected, reports its address instead
    of opening a new tab.
    """
    bridge = get_bridge()
    port = await bridge.start()
    url = f"http://127.0.0.1:{port}/"
    if bridge.viewer_connected:
        return f"Viewer already connected at {url}{_visibility_note(bridge)}"
    if _static_dir() is None:
        return (
            f"Bridge is listening at {url}, but the viewer app is not built. "
            "Run `npm install && npm run build` in the viewer/ directory, "
            "then call open_viewer again."
        )
    webbrowser.open(url)
    await bridge.wait_for_viewer(timeout)
    return f"Viewer connected at {url}{_visibility_note(bridge)}"


@mcp.tool()
async def fetch_structure(
    identifier: str, source: str = "auto", name: str | None = None
) -> str:
    """Fetch a structure and load it into the viewer.

    identifier: a local file path (.pdb/.cif), a 4-character PDB ID (e.g.
    "1ubq"), or a UniProt accession for an AlphaFold model (e.g. "P69905").
    source: "auto" (default), "file", "pdb", or "alphafold".
    name: optional label for the loaded structure.
    """
    bridge = _require_viewer()
    try:
        structure = await fetch_structure_data(identifier, source)
    except FetchError as exc:
        raise ViewerError(str(exc)) from exc
    label = name or structure.name
    result = await bridge.request(
        "load_structure",
        {"name": label, "format": structure.format, "data": structure.data},
    )
    origin = {
        "file": "local file",
        "pdb": "RCSB PDB",
        "alphafold": "AlphaFold DB",
        "cache": "cache",
    }[structure.source]
    return f"Loaded {label} ({structure.format}, from {origin}): {result}"


def _compile(selection: str) -> str:
    """PyMOL syntax → MolScript, surfacing compile errors as viewer errors."""
    try:
        return to_molscript(selection)
    except SelectionError as exc:
        raise ViewerError(f"Bad selection {selection!r}: {exc}") from exc


@mcp.tool()
async def select(selection: str, name: str = "sele", limit: int = 200) -> dict:
    """Resolve a PyMOL-syntax selection and report exactly what it matched.

    selection: PyMOL algebra, e.g. "chain A and resi 50-60", "byres (polymer
      within 4 of resn HEM)", "glycan", "metals". Unsupported constructs raise
      rather than silently matching nothing.
    name: handle for this selection, reusable by show() and color().
    limit: cap on residues listed back; counts are always exact.

    Returns atom/residue counts, the chains touched, and the residue list —
    so the contents are known rather than inferred from a picture.
    """
    bridge = _require_viewer()
    return await bridge.request(
        "select", {"name": name, "expression": _compile(selection), "limit": limit}
    )


@mcp.tool()
async def show(
    selection: str,
    representation: str = "cartoon",
    color: str | None = None,
    name: str = "sele",
    limit: int = 200,
) -> dict:
    """Display a selection with a representation, and report what it matched.

    representation: cartoon, ball-and-stick, spacefill, molecular-surface,
      gaussian-surface, putty, line, point, ellipsoid, backbone, carbohydrate.
    color: a Mol* colour theme (chain-id, element-symbol, secondary-structure,
      b-factor, hydrophobicity, uniform) or a literal hex value like "#ff0000".
    """
    bridge = _require_viewer()
    args = {
        "name": name,
        "expression": _compile(selection),
        "representation": representation,
        "limit": limit,
    }
    if color:
        args["color"] = color
    return await bridge.request("show", args)


@mcp.tool()
async def color(color: str, name: str = "sele") -> dict:
    """Recolour an existing named selection.

    color: a Mol* colour theme or a literal hex value like "#3366cc".
    name: the handle passed to a previous select() or show().
    """
    bridge = _require_viewer()
    return await bridge.request("color", {"name": name, "color": color})


@mcp.tool()
async def hide(name: str = "sele") -> dict:
    """Hide a named selection without discarding it; unhide() brings it back."""
    bridge = _require_viewer()
    return await bridge.request("hide", {"name": name})


@mcp.tool()
async def unhide(name: str = "sele") -> dict:
    """Show a selection previously hidden with hide()."""
    bridge = _require_viewer()
    return await bridge.request("unhide", {"name": name})


@mcp.tool()
async def remove(name: str = "sele") -> dict:
    """Delete a named selection and its representations from the scene."""
    bridge = _require_viewer()
    return await bridge.request("remove", {"name": name})


@mcp.tool()
async def list_selections() -> dict:
    """List the named selections in the scene, with atom counts and visibility.

    Lets the scene be inspected directly rather than inferred from a picture.
    """
    bridge = _require_viewer()
    return await bridge.request("list_selections", {})


@mcp.tool()
async def focus(name: str = "sele") -> dict:
    """Zoom the camera to a named selection, returning the resulting camera target."""
    bridge = _require_viewer()
    return await bridge.request("focus", {"name": name})


@mcp.tool()
async def reset_view() -> dict:
    """Reset the camera to frame the whole scene."""
    bridge = _require_viewer()
    return await bridge.request("reset_view", {})


@mcp.tool()
async def orient() -> dict:
    """Align the camera to the structure's principal axes."""
    bridge = _require_viewer()
    return await bridge.request("orient", {})


@mcp.tool()
async def measure(kind: str, names: list[str]) -> dict:
    """Add a distance, angle, or dihedral between named selections.

    kind: "distance" (2 selections), "angle" (3), or "dihedral" (4).
    Each selection is measured at its centroid, so point-like selections read
    most clearly — e.g. select("chain A and resi 58 and name NE2", name="ne2").
    """
    bridge = _require_viewer()
    return await bridge.request("measure", {"kind": kind, "names": names})


@mcp.tool()
async def clear_viewer() -> str:
    """Remove all loaded structures from the viewer."""
    bridge = _require_viewer()
    await bridge.request("clear")
    return "Viewer cleared."


@mcp.tool()
async def screenshot(path: str | None = None) -> list:
    """Capture the current viewport as a PNG.

    path: optional output file path; defaults to a timestamped file in
    ~/.cache/protean/screenshots/. Returns the image and the saved path.
    """
    bridge = _require_viewer()
    result = await bridge.request("screenshot", {})
    data_uri: str = result["data_uri"]
    header, _, payload = data_uri.partition(",")
    if "base64" not in header:
        raise ViewerError(f"Unexpected screenshot encoding: {header}")
    png = base64.b64decode(payload)

    if path:
        out = Path(path).expanduser()
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path.home() / ".cache" / "protean" / "screenshots" / f"protean-{stamp}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return [Image(data=png, format="png"), f"Saved to {out}"]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
