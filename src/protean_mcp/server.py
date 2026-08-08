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

import numpy as np
from mcp.server.fastmcp import FastMCP, Image

from .analysis.contacts import ContactError
from .analysis.contacts import interface as _interface
from .analysis.electrostatics import ElectrostaticsError
from .analysis.electrostatics import apbs_binary as _apbs_binary
from .analysis.electrostatics import coulombic as _coulombic
from .analysis.electrostatics import prepare as _prepare_charges
from .analysis.electrostatics import run_apbs as _run_apbs
from .analysis.electrostatics import sample as _sample_grid
from .analysis.electrostatics import write_dx as _write_dx
from .analysis.superposition import SuperpositionError, parse_structure
from .analysis.superposition import superpose as _superpose
from .connection import ViewerBridge, ViewerError
from .fetch import FetchError, default_cache_dir, fetch_structure_data
from .handles import HandleError, HandleRegistry
from .handles import combine as _combine_indices
from .handles import summarise as _summarise
from .handles import to_molscript as _indices_to_molscript
from .selections import SelectionError
from .selections import parse as _parse_selection
from .selections_numpy import _residue_keys
from .selections_numpy import _widen as _widen_mask
from .selections_numpy import _within as _within_mask
from .selections_numpy import evaluate as _evaluate
from .selections_numpy import load_structure as _load_structure

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

# The loaded structure, held in Python so selections and analysis do not need a
# browser. The viewer displays what we resolve; it is not the source of truth.
_structure: Any = None
_structure_error: str | None = None
_structure_identifier: str | None = None
_handles = HandleRegistry()


def _same_structure(identifier: str) -> bool:
    """Is this identifier the structure we already have loaded?

    Handles index into the loaded structure, so an analysis of anything else
    cannot produce one. Getting this wrong would register handles whose atom
    indices point into a different molecule — right-looking numbers, wrong
    atoms — so the comparison is conservative: unsure means no.
    """
    if _structure is None or _structure_identifier is None:
        return False
    if identifier.casefold() == _structure_identifier.casefold():
        return True
    try:
        return Path(identifier).expanduser().resolve() == (
            Path(_structure_identifier).expanduser().resolve()
        )
    except OSError:
        return False


def _require_structure() -> Any:
    if _structure is None:
        if _structure_error is not None:
            raise ViewerError(
                "The loaded structure could not be parsed for analysis, so "
                f"selections are unavailable: {_structure_error}"
            )
        raise ViewerError("No structure loaded — call fetch_structure first.")
    return _structure


async def _display(name: str, indices: Any) -> None:
    """Mirror a handle into the viewer, if one is connected.

    Selections are usable without a viewer; display is a side effect, not a
    precondition.
    """
    bridge = get_bridge()
    if not bridge.viewer_connected:
        return
    expression = _indices_to_molscript(_require_structure(), indices)
    await bridge.request("select", {"name": name, "expression": expression, "limit": 0})


def _register(name: str, indices: Any, origin: str) -> dict[str, Any]:
    _handles.set(name, indices, origin)
    return {"name": name, "origin": origin, **_summarise(_require_structure(), indices)}


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
    # Mol* is more tolerant than our analysis parser, so a file it can render
    # should still display. Only analysis degrades, and it says so rather than
    # silently matching nothing.
    global _structure, _structure_error, _structure_identifier  # noqa: PLW0603 - session state
    _handles.clear()
    try:
        _structure = _load_structure(structure.data, structure.format)
        _structure_error = None
    except SelectionError as exc:
        _structure, _structure_error = None, str(exc)
    _structure_identifier = identifier
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
    note = (
        "" if _structure_error is None else f" [analysis unavailable: {_structure_error}]"
    )
    return f"Loaded {label} ({structure.format}, from {origin}): {result}{note}"


@mcp.tool()
async def select(selection: str, name: str = "sele", limit: int = 200) -> dict[str, Any]:
    """Resolve a PyMOL-syntax selection into a named handle.

    selection: PyMOL algebra for leaf predicates, e.g. "chain A and resi 50-60",
      "byres (polymer within 4 of resn HEM)", "glycan", "metals". Combining
      selections is done with combine()/near()/invert() rather than in this
      string, so there is no operator precedence to get wrong.
    name: the handle. Pass it to show(), color(), measure(), combine() and so on.

    Resolved in Python, so it works with no viewer open. Returns atom and
    residue counts, the chains touched, and the residue list.
    """
    array = _require_structure()
    try:
        mask = _evaluate(_parse_selection(selection), array)
    except SelectionError as exc:
        raise ViewerError(f"Bad selection {selection!r}: {exc}") from exc
    indices = np.flatnonzero(mask)
    summary = _register(name, indices, f"select({selection!r})")
    await _display(name, indices)
    summary["residues"] = summary["residues"][:limit]
    return summary


@mcp.tool()
async def combine(operation: str, of: list[str], name: str) -> dict[str, Any]:
    """Build a handle from existing ones: union, intersect or subtract.

    of: handle names, applied left to right. subtract removes every later
      selection from the first.

    This is where composition lives, instead of inside the selection string.
    """
    try:
        indices = _combine_indices(_handles, operation, of)
    except HandleError as exc:
        raise ViewerError(str(exc)) from exc
    summary = _register(name, indices, f"{operation}({', '.join(of)})")
    await _display(name, indices)
    return summary


@mcp.tool()
async def near(
    of: str,
    radius: float = 5.0,
    whole_residues: bool = True,
    exclude_self: bool = True,
    name: str = "near",
) -> dict[str, Any]:
    """Atoms within a distance of an existing handle.

    whole_residues: widen to complete residues, which is usually what a figure
      or a contact list wants.
    exclude_self: leave out the atoms of `of` itself.
    """
    array = _require_structure()
    try:
        source = _handles.get(of)
    except HandleError as exc:
        raise ViewerError(str(exc)) from exc
    mask = np.zeros(array.array_length(), dtype=bool)
    mask[source.indices] = True
    found = _within_mask(array, mask, radius)
    if whole_residues:
        found = _widen_mask(found, _residue_keys(array))
    if exclude_self:
        found = found & ~mask
    indices = np.flatnonzero(found)
    origin = f"near({of}, radius={radius}, whole_residues={whole_residues})"
    summary = _register(name, indices, origin)
    await _display(name, indices)
    return summary


@mcp.tool()
async def invert(of: str, name: str) -> dict[str, Any]:
    """Everything the given handle does not contain."""
    array = _require_structure()
    try:
        source = _handles.get(of)
    except HandleError as exc:
        raise ViewerError(str(exc)) from exc
    mask = np.ones(array.array_length(), dtype=bool)
    mask[source.indices] = False
    indices = np.flatnonzero(mask)
    summary = _register(name, indices, f"invert({of})")
    await _display(name, indices)
    return summary


@mcp.tool()
async def show(
    representation: str = "cartoon",
    selection: str | None = None,
    handle: str | None = None,
    color: str | None = None,
    size: float | None = None,
    name: str = "sele",
) -> dict[str, Any]:
    """Display a selection, given either a handle or a selection string.

    handle: an existing handle from select(), combine(), near() or an analysis
      tool — the usual way to display something already computed.
    selection: PyMOL syntax, resolved and registered under `name` as a
      shorthand for select() followed by show().

    representation: cartoon, ball-and-stick, spacefill, molecular-surface,
      gaussian-surface, putty, line, point, ellipsoid, backbone, carbohydrate.
      An unknown name is rejected with the full list; see capabilities().
    color: a Mol* colour theme or a literal hex value like "#ff0000".
    size: scales the representation; for spacefill this scales the van der
      Waals radius, so an ion that would hide what it coordinates can be shrunk.
    """
    if (selection is None) == (handle is None):
        raise ViewerError("Pass exactly one of selection or handle")
    array = _require_structure()
    if handle is not None:
        try:
            target = _handles.get(handle)
        except HandleError as exc:
            raise ViewerError(str(exc)) from exc
        indices, label = target.indices, handle
    else:
        assert selection is not None
        try:
            mask = _evaluate(_parse_selection(selection), array)
        except SelectionError as exc:
            raise ViewerError(f"Bad selection {selection!r}: {exc}") from exc
        indices = np.flatnonzero(mask)
        _register(name, indices, f"select({selection!r})")
        label = name

    args: dict[str, Any] = {
        "name": label,
        "expression": _indices_to_molscript(array, indices),
        "representation": representation,
        "limit": 0,
    }
    if color:
        args["color"] = color
    if size is not None:
        args["size"] = size
    await _call("show", args)
    return {"name": label, "representation": representation, **_summarise(array, indices)}


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
    """The named handles in this session, with sizes and where each came from.

    Read from Python, so it answers with or without a viewer.
    """
    array = _require_structure()
    return {
        "selections": [
            {
                "name": name,
                "atom_count": len(_handles.get(name)),
                "residue_count": _summarise(array, _handles.get(name).indices, limit=0)[
                    "residue_count"
                ],
                "origin": _handles.get(name).origin,
            }
            for name in _handles.names()
        ]
    }


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
    chain_a: str,
    chain_b: str,
    identifier: str | None = None,
    contact_limit: int = 200,
    name_a: str = "iface_a",
    name_b: str = "iface_b",
) -> dict[str, Any]:
    """Describe the interface between two chains: buried area and contacts.

    identifier: defaults to the loaded structure, which is the usual case.
      Naming a different structure runs the analysis standalone, with no
      viewer and no handles.
    name_a, name_b: handles registered for the interface residues of each side,
      so the result can be shown or coloured without re-encoding it as a
      selection string. Pass them to show(), color(), combine() or near().

    Returns the buried surface area (total and per side), the interface
    residues with how much each buries, and the contacts classified as salt
    bridges, hydrogen bonds or polar contacts.

    Solvent is excluded. The `criterion` field states how contacts were
    judged: real donor-H...acceptor geometry when the structure has hydrogens,
    a heavy-atom distance cutoff when it does not.
    """
    # Handles are atom indices into the loaded structure, so they can only be
    # registered when that is what we analysed. Reuse the loaded array rather
    # than re-parsing: identical indices by construction, not by assumption.
    label: str | None
    if identifier is not None and not _same_structure(identifier):
        on_loaded = False
        label = identifier
        try:
            structure = await fetch_structure_data(identifier)
            array = parse_structure(structure.data, structure.format)
        except FetchError as exc:
            raise ViewerError(str(exc)) from exc
        except SuperpositionError as exc:
            raise ViewerError(str(exc)) from exc
    else:
        on_loaded = True
        array = _require_structure()
        label = identifier or _structure_identifier

    try:
        result = _interface(array, chain_a, chain_b, contact_limit=contact_limit)
    except (ContactError, SuperpositionError) as exc:
        raise ViewerError(str(exc)) from exc

    payload: dict[str, Any] = {"identifier": label, **result.as_dict()}
    if on_loaded:
        call = f"interface({chain_a}, {chain_b})"
        for name, indices, side in (
            (name_a, result.indices_a, chain_a),
            (name_b, result.indices_b, chain_b),
        ):
            # Name the side in the origin: list_selections shows it, and two
            # handles reading "interface(A, B)" would be indistinguishable.
            _register(name, indices, f"{call} side {side}")
            await _display(name, indices)
        payload["handles"] = {"a": name_a, "b": name_b}
    else:
        # Say why rather than omitting them: a missing key reads as "there was
        # no interface", which is a different and much worse claim.
        payload["handles"] = None
        payload["handles_note"] = (
            f"No handles registered: {identifier!r} is not the loaded structure. "
            "Load it with fetch_structure to get handles for its interface."
        )
    return payload


def _sample_handle(grid: Any, handle: str, limit: int) -> dict[str, Any]:
    """Mean potential per residue over the atoms of a handle."""
    array = _require_structure()
    try:
        target = _handles.get(handle)
    except HandleError as exc:
        raise ViewerError(str(exc)) from exc

    indices = target.indices
    values = _sample_grid(grid, array.coord[indices])
    chains = array.chain_id[indices].astype(str)
    seqs = array.res_id[indices]
    comps = array.res_name[indices].astype(str)
    keys = np.char.add(
        np.char.add(chains, "|"),
        np.char.add(seqs.astype(str), array.ins_code[indices].astype(str)),
    )

    residues: list[dict[str, Any]] = []
    for key in dict.fromkeys(keys.tolist()):
        mask = keys == key
        sampled = values[mask]
        usable = sampled[~np.isnan(sampled)]
        if not len(usable):
            continue
        first = int(np.flatnonzero(mask)[0])
        residues.append(
            {
                "chain": str(chains[first]),
                "seq": int(seqs[first]),
                "comp": str(comps[first]),
                "potential": round(float(usable.mean()), 3),
            }
        )
    residues.sort(key=lambda r: r["potential"])
    return {
        "handle": handle,
        "residues_sampled": len(residues),
        # Points off the grid come back NaN rather than clamped, and are counted
        # here: a silently dropped residue would read as one that scored neutral.
        "atoms_outside_grid": int(np.isnan(values).sum()),
        "most_negative": residues[:limit],
        "most_positive": residues[-limit:][::-1],
    }


@mcp.tool()
async def electrostatics(
    method: str = "auto",
    ph: float = 7.0,
    ionic_strength: float = 0.15,
    spacing: float = 1.0,
    padding: float = 10.0,
    handle: str | None = None,
    path: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Electrostatic potential around the loaded structure, in kT/e.

    method: "auto" uses APBS when a runnable binary is present and falls back
      to a screened Coulomb field otherwise. "coulombic" and "apbs" force one.
      The result always states which actually ran — the two are not equivalent,
      and a potential whose provenance is unstated is worth nothing.
    ph: protonation states are assigned at this pH by pdb2pqr/propka.
    ionic_strength: mol/L; sets the Debye screening length.
    handle: sample the potential over this set and report it per residue, which
      answers "is this interface acidic?" without rendering anything.
    path: where to write the OpenDX grid; defaults to the protean cache.

    On the Coulombic field: it assumes one uniform dielectric, so it has no
    protein interior and no reaction field at the solvent boundary. Measured
    against APBS on ubiquitin it tracks surface potential closely in shape
    (r = 0.96, 94% sign agreement) while running about 1.6x low in magnitude.
    Read it for where the charge is, never for an energy.
    """
    array = _require_structure()
    try:
        prepared = _prepare_charges(array, ph=ph)
    except ElectrostaticsError as exc:
        raise ViewerError(str(exc)) from exc

    binary = _apbs_binary()
    if method == "auto":
        chosen = "apbs" if binary else "coulombic"
    elif method in ("apbs", "coulombic"):
        chosen = method
    else:
        raise ViewerError(f"Unknown method {method!r} (auto, coulombic, apbs)")

    try:
        if chosen == "apbs":
            grid = _run_apbs(
                prepared,
                spacing=spacing,
                padding=padding,
                ionic_strength=ionic_strength,
                binary=binary,
            )
        else:
            grid = _coulombic(
                prepared,
                spacing=spacing,
                padding=padding,
                ionic_strength=ionic_strength,
            )
    except ElectrostaticsError as exc:
        raise ViewerError(str(exc)) from exc

    out = Path(path).expanduser() if path else default_cache_dir() / "potential.dx"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_write_dx(grid))

    payload: dict[str, Any] = {
        **grid.as_dict(),
        "charges": prepared.as_dict(),
        "dx_path": str(out),
        "apbs_available": binary is not None,
    }
    if chosen == "coulombic":
        payload["caveat"] = (
            "Uniform dielectric: no protein interior, no reaction field. The "
            "shape of the surface potential is reliable, the magnitude is not, "
            "and no free energy follows from it."
        )
    if handle is not None:
        payload["sampled"] = _sample_handle(grid, handle, limit)
    return payload


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
