"""protean MCP server — Phase 1 tools: open_viewer, fetch_structure, screenshot."""

from __future__ import annotations

import base64
import datetime
import gzip
import json
import logging
import webbrowser
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .analysis.contacts import ContactError
from .analysis.contacts import interface as _interface
from .analysis.superposition import SuperpositionError, parse_structure
from .analysis.superposition import superpose as _superpose
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
    global _bridge  # noqa: PLW0603 - deliberate module-level singleton
    if _bridge is None:
        _bridge = ViewerBridge(static_dir=_static_dir())
    return _bridge


def _require_viewer() -> ViewerBridge:
    bridge = get_bridge()
    if not bridge.viewer_connected:
        raise ViewerError("No viewer connected — call open_viewer first.")
    return bridge


async def _call(action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send an action to the viewer and insist the reply is a JSON object.

    The bridge hands back whatever the viewer serialised, so the shape is
    unverified until something checks it. Tools declare a dict return type;
    this makes that declaration true rather than assumed.
    """
    bridge = _require_viewer()
    result = await bridge.request(action, args or {})
    if not isinstance(result, dict):
        raise ViewerError(
            f"Viewer returned {type(result).__name__} for {action!r}, expected an object"
        )
    return result


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
async def select(selection: str, name: str = "sele", limit: int = 200) -> dict[str, Any]:
    """Resolve a PyMOL-syntax selection and report exactly what it matched.

    selection: PyMOL algebra, e.g. "chain A and resi 50-60", "byres (polymer
      within 4 of resn HEM)", "glycan", "metals". Unsupported constructs raise
      rather than silently matching nothing.
    name: handle for this selection, reusable by show() and color().
    limit: cap on residues listed back; counts are always exact.

    Returns atom/residue counts, the chains touched, and the residue list —
    so the contents are known rather than inferred from a picture.
    """
    return await _call(
        "select", {"name": name, "expression": _compile(selection), "limit": limit}
    )


@mcp.tool()
async def show(
    selection: str,
    representation: str = "cartoon",
    color: str | None = None,
    size: float | None = None,
    name: str = "sele",
    limit: int = 200,
) -> dict[str, Any]:
    """Display a selection with a representation, and report what it matched.

    representation: cartoon, ball-and-stick, spacefill, molecular-surface,
      gaussian-surface, putty, line, point, ellipsoid, backbone, carbohydrate.
      An unknown name is rejected with the full list; see capabilities().
    color: a Mol* colour theme (chain-id, element-symbol, secondary-structure,
      b-factor, hydrophobicity, uniform) or a literal hex value like "#ff0000".
    size: scales the representation. For spacefill this scales the van der
      Waals radius, so an ion drawn at full radius (which hides what it
      coordinates) can be shrunk with size=0.3.
    """
    args: dict[str, Any] = {
        "name": name,
        "expression": _compile(selection),
        "representation": representation,
        "limit": limit,
    }
    if color:
        args["color"] = color
    if size is not None:
        args["size"] = size
    return await _call("show", args)


@mcp.tool()
async def color(color: str, name: str = "sele") -> dict[str, Any]:
    """Recolour an existing named selection.

    color: a Mol* colour theme or a literal hex value like "#3366cc".
    name: the handle passed to a previous select() or show().
    """
    return await _call("color", {"name": name, "color": color})


@mcp.tool()
async def label(name: str = "sele", level: str = "residue") -> dict[str, Any]:
    """Draw text labels on a named selection.

    level: "residue" (e.g. HIS 94), "chain", or "element" for per-atom names.
    """
    return await _call("label", {"name": name, "level": level})


@mcp.tool()
async def hide(name: str = "sele") -> dict[str, Any]:
    """Hide a named selection without discarding it; unhide() brings it back."""
    return await _call("hide", {"name": name})


@mcp.tool()
async def unhide(name: str = "sele") -> dict[str, Any]:
    """Show a selection previously hidden with hide()."""
    return await _call("unhide", {"name": name})


@mcp.tool()
async def remove(name: str = "sele") -> dict[str, Any]:
    """Delete a named selection and its representations from the scene."""
    return await _call("remove", {"name": name})


@mcp.tool()
async def list_selections() -> dict[str, Any]:
    """List the named selections in the scene, with atom counts and visibility.

    Lets the scene be inspected directly rather than inferred from a picture.
    """
    return await _call("list_selections", {})


@mcp.tool()
async def focus(name: str = "sele") -> dict[str, Any]:
    """Zoom the camera to a named selection, returning the resulting camera target."""
    return await _call("focus", {"name": name})


@mcp.tool()
async def reset_view() -> dict[str, Any]:
    """Reset the camera to frame the whole scene."""
    return await _call("reset_view", {})


@mcp.tool()
async def orient() -> dict[str, Any]:
    """Align the camera to the structure's principal axes."""
    return await _call("orient", {})


@mcp.tool()
async def measure(kind: str, names: list[str]) -> dict[str, Any]:
    """Add a distance, angle, or dihedral between named selections.

    kind: "distance" (2 selections), "angle" (3), or "dihedral" (4).
    Each selection is measured at its centroid, so point-like selections read
    most clearly — e.g. select("chain A and resi 58 and name NE2", name="ne2").
    """
    return await _call("measure", {"kind": kind, "names": names})


@mcp.tool()
async def capabilities() -> dict[str, Any]:
    """List the representation and colour-theme names this viewer accepts.

    Read from Mol*'s live registries, so the list matches the bundled version
    rather than a hardcoded guess.
    """
    return await _call("capabilities", {})


SESSION_FORMAT = "protean-session"
SESSION_VERSION = 1


def _session_path(path: str) -> Path:
    out = Path(path).expanduser()
    return out if out.suffix else out.with_suffix(".protean")


@mcp.tool()
async def save_session(path: str) -> dict[str, Any]:
    """Save the whole scene to a .protean file.

    The file embeds the structure data along with representations, colours,
    camera and the named selection handles, so reopening it reproduces the
    scene exactly without refetching anything.
    """
    payload = await _call("save_session")
    out = _session_path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "created": datetime.datetime.now(tz=datetime.UTC).isoformat(),
        "handles": payload["handles"],
        "molstar": payload["snapshot"],
    }
    # Sessions are mostly embedded mmCIF text, which compresses roughly 5x.
    raw = json.dumps(document).encode()
    out.write_bytes(gzip.compress(raw))
    return {
        "path": str(out),
        "bytes": out.stat().st_size,
        "uncompressed_bytes": len(raw),
        "handles": sorted(payload["handles"]),
    }


@mcp.tool()
async def load_session(path: str) -> dict[str, Any]:
    """Restore a scene previously written by save_session().

    Reports which named handles came back, and which were dropped because the
    restored state no longer contains them.
    """
    src = _session_path(path)
    if not src.is_file():
        raise ViewerError(f"No session file at {src}")
    try:
        document = json.loads(gzip.decompress(src.read_bytes()))
    except (OSError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
        raise ViewerError(f"{src} is not a readable protean session: {exc}") from exc
    if document.get("format") != SESSION_FORMAT:
        raise ViewerError(
            f"{src} is not a protean session (format={document.get('format')!r})"
        )
    if document.get("version") != SESSION_VERSION:
        raise ViewerError(
            f"{src} is session version {document.get('version')!r}; "
            f"this build reads version {SESSION_VERSION}"
        )
    result = await _call(
        "load_session",
        {"snapshot": document["molstar"], "handles": document.get("handles", {})},
    )
    return {"path": str(src), "created": document.get("created"), **result}


@mcp.tool()
async def superpose(
    mobile: str,
    target: str,
    mobile_chain: str | None = None,
    target_chain: str | None = None,
) -> dict[str, Any]:
    """Superpose one structure onto another and report how well it fits.

    mobile, target: PDB IDs, UniProt accessions or local files, as fetch_structure takes.
    mobile_chain, target_chain: restrict to one chain each; otherwise all
      protein chains are used, which requires them to correspond in order.

    Correspondence comes from a sequence alignment, so the two structures need
    not share residue numbering. Returns the RMSD, how many residues were
    aligned, the sequence identity over those residues, the 4x4 transform, and
    the worst-fitting residues — an RMSD alone hides whether the disagreement is
    spread out or concentrated in one loop.

    This is pure analysis and does not touch the viewer.
    """
    try:
        first = await fetch_structure_data(mobile)
        second = await fetch_structure_data(target)
    except FetchError as exc:
        raise ViewerError(str(exc)) from exc
    try:
        result = _superpose(
            first.data,
            first.format,
            second.data,
            second.format,
            mobile_chain=mobile_chain,
            target_chain=target_chain,
        )
    except SuperpositionError as exc:
        raise ViewerError(str(exc)) from exc
    return {"mobile": mobile, "target": target, **result.as_dict()}


@mcp.tool()
async def interface(
    identifier: str,
    chain_a: str,
    chain_b: str,
    contact_limit: int = 200,
) -> dict[str, Any]:
    """Describe the interface between two chains: buried area and contacts.

    Returns the buried surface area (total and per side), the interface
    residues with how much each buries, and the contacts classified as salt
    bridges, hydrogen bonds or polar contacts.

    Solvent is excluded. The `criterion` field states how contacts were
    judged: real donor-H...acceptor geometry when the structure has hydrogens,
    a heavy-atom distance cutoff when it does not.

    This is pure analysis and does not touch the viewer.
    """
    try:
        structure = await fetch_structure_data(identifier)
    except FetchError as exc:
        raise ViewerError(str(exc)) from exc
    try:
        array = parse_structure(structure.data, structure.format)
        result = _interface(array, chain_a, chain_b, contact_limit=contact_limit)
    except (ContactError, SuperpositionError) as exc:
        raise ViewerError(str(exc)) from exc
    return {"identifier": identifier, **result.as_dict()}


@mcp.tool()
async def clear_viewer() -> str:
    """Remove all loaded structures from the viewer."""
    bridge = _require_viewer()
    await bridge.request("clear")
    return "Viewer cleared."


@mcp.tool()
async def screenshot(path: str | None = None) -> list[Any]:
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
        stamp = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d-%H%M%S")
        out = Path.home() / ".cache" / "protean" / "screenshots" / f"protean-{stamp}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return [Image(data=png, format="png"), f"Saved to {out}"]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
