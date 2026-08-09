"""protean MCP server — Phase 1 tools: open_viewer, fetch_structure, screenshot."""

from __future__ import annotations

import base64
import contextlib
import datetime
import gzip
import json
import logging
import webbrowser
from pathlib import Path
from typing import Any

import numpy as np
from biotite.structure import filter_amino_acids
from mcp.server.fastmcp import FastMCP, Image

from .analysis.conservation import ConservationError
from .analysis.conservation import chain_sequence as _chain_sequence
from .analysis.conservation import fetch_msa as _fetch_msa
from .analysis.conservation import score as _score_conservation
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
# Conservation scores from the last conservation() call, per chain, so they can
# be coloured without paying for the alignment again.
_conservation_scores: dict[str, Any] = {}

_DOMAIN_BOUNDS = 2
_MIN_BINS = 2
# Ramps for banded colouring. Conservation runs variable -> conserved, which is
# the direction the eye reads as "important is saturated".
_PALETTES: dict[str, list[str]] = {
    "conservation": ["#2c7bb6", "#ffffbf", "#d7191c"],
    "red-white-blue": ["#d7191c", "#ffffff", "#2c7bb6"],
    "viridis": ["#440154", "#21918c", "#fde725"],
    "white-red": ["#ffffff", "#d7191c"],
}


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
    identifier: str,
    source: str = "auto",
    name: str | None = None,
    assembly: str = "biological",
) -> str:
    """Fetch a structure and load it into the viewer.

    identifier: a local file path (.pdb/.cif), a 4-character PDB ID (e.g.
    "1ubq"), or a UniProt accession for an AlphaFold model (e.g. "P69905").
    source: "auto" (default), "file", "pdb", or "alphafold".
    name: optional label for the loaded structure.
    assembly: "biological" (default) builds the molecule as it exists —
      haemoglobin is a tetramer, not the deposited dimer — and "asymmetric"
      uses the deposited coordinates. The viewer and the analysis always make
      the same choice, and the reply states the atom count each of them ended
      up with so a disagreement is visible rather than latent.

    Note that the biological assembly is not always larger: an asymmetric unit
    holding two copies of a complex has an assembly *half* its size.
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
    _conservation_scores.clear()
    loaded = None
    try:
        loaded = _load_structure(structure.data, structure.format, assembly)
        _structure, _structure_error = loaded.array, None
    except SelectionError as exc:
        _structure, _structure_error = None, str(exc)
    _structure_identifier = identifier
    label = name or structure.name

    result = await bridge.request(
        "load_structure",
        {
            "name": label,
            "format": structure.format,
            "data": structure.data,
            "assembly": loaded.assembly if loaded else assembly,
        },
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
    if loaded is not None:
        note += _assembly_note(loaded, result)
    return f"Loaded {label} ({structure.format}, from {origin}): {result}{note}"


def _assembly_note(loaded: Any, viewer_result: Any) -> str:
    """State whether the viewer and the analysis are holding the same molecule.

    They are built by two independent implementations from the same file, and
    for years the only symptom of them disagreeing was an atom count nobody
    compared. Comparing it is cheap; discovering the mismatch later, through a
    potential map computed for a different molecule, is not.
    """
    parts = [f" [{loaded.assembly} assembly"]
    if loaded.copies > 1:
        parts.append(f", {loaded.copies} symmetry copies")
    ours = int(loaded.array.array_length())
    theirs = viewer_result.get("atom_count") if isinstance(viewer_result, dict) else None
    if theirs is None:
        parts.append(f", {ours} atoms here; the viewer reported no count")
    elif int(theirs) == ours:
        parts.append(f", {ours} atoms in both viewer and analysis")
    else:
        parts.append(
            f", MISMATCH: {ours} atoms here but {theirs} in the viewer. "
            "Analysis and the picture are different molecules; treat counts, "
            "buried areas and potentials as unreliable"
        )
    if loaded.note:
        parts.append(f" — {loaded.note}")
    return "".join(parts) + "]"


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


def _residue_indices(array: Any, keys: set[tuple[str, int, str]]) -> Any:
    """Every atom of the given (chain, seq, ins_code) residues."""
    labels = np.char.add(
        np.char.add(array.chain_id.astype(str), "|"),
        np.char.add(array.res_id.astype(str), array.ins_code.astype(str)),
    )
    wanted = [f"{chain}|{seq}{ins}" for chain, seq, ins in keys]
    return np.flatnonzero(np.isin(labels, wanted))


@mcp.tool()
async def conservation(
    chain: str | None = None,
    conserved_percentile: float = 25.0,
    variable_percentile: float = 75.0,
    name_conserved: str = "conserved",
    name_variable: str = "variable",
    use_env: bool = True,
    force_refresh: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Score a chain by evolutionary conservation and register the extremes.

    chain: defaults to the first protein chain in the loaded structure.
    conserved_percentile, variable_percentile: cutoffs *within this chain*, so
      the split adapts to how variable the protein actually is. The defaults
      take the most-conserved and least-conserved quartiles.
    name_conserved, name_variable: handles for those two sets. They compose —
      combine("intersect", ["iface_a", "conserved"], "hot") is the conserved
      part of an interface.

    Submits the sequence to an MMseqs2 server (the ColabFold public API by
    default, overridable with PROTEAN_MSA_URL) and scores each position by
    Shannon entropy over the resulting alignment. The first call for a
    sequence takes tens of seconds to minutes; the alignment is then cached on
    disk, so later calls are immediate.

    Returns per-residue entropy and conservation, plus `msa_depth`. Read that
    number before trusting the scores: a protein with few known homologs looks
    conserved everywhere because nothing was found to disagree with it.
    """
    array = _require_structure()
    if chain is None:
        protein: Any = np.asarray(filter_amino_acids(array))
        if not protein.any():
            raise ViewerError("No protein in the loaded structure to score")
        chain = str(array.chain_id[protein][0])

    try:
        sequence, _ = _chain_sequence(array, chain)
        a3m, source = await _fetch_msa(
            sequence,
            default_cache_dir(),
            use_env=use_env,
            force_refresh=force_refresh,
        )
        result = _score_conservation(array, chain, a3m, source=source)
    except ConservationError as exc:
        raise ViewerError(str(exc)) from exc

    # Keep the scores so color_by_conservation does not have to pay for the
    # alignment again just to change how it is drawn.
    _conservation_scores[chain] = result.scores

    entropies = np.array([s.entropy for s in result.scores])
    low = float(np.percentile(entropies, conserved_percentile))
    high = float(np.percentile(entropies, variable_percentile))

    payload: dict[str, Any] = {
        **result.as_dict(limit=limit),
        "conserved_below_entropy": round(low, 3),
        "variable_above_entropy": round(high, 3),
    }
    for name, keys, origin in (
        (
            name_conserved,
            {(s.chain, s.seq, s.ins_code) for s in result.scores if s.entropy <= low},
            f"conservation({chain}) below the {conserved_percentile}th percentile",
        ),
        (
            name_variable,
            {(s.chain, s.seq, s.ins_code) for s in result.scores if s.entropy >= high},
            f"conservation({chain}) above the {variable_percentile}th percentile",
        ),
    ):
        indices = _residue_indices(array, keys)
        _register(name, indices, origin)
        await _display(name, indices)
    payload["handles"] = {"conserved": name_conserved, "variable": name_variable}
    return payload


@mcp.tool()
async def color_by_potential(
    handle: str = "sele",
    path: str | None = None,
    domain: list[float] | None = None,
    palette: str = "red-white-blue",
) -> dict[str, Any]:
    """Colour a displayed selection by an electrostatic potential grid.

    handle: a selection that is already shown — a molecular surface is the
      usual target, since surface potential is what this is for.
    path: an OpenDX grid; defaults to the one electrostatics() last wrote.
    domain: [min, max] in kT/e. Defaults to a symmetric range about zero, which
      keeps neutral white and makes the two signs comparable. +/-5 is a common
      choice for figures.
    palette: red-white-blue by convention — acidic red, basic blue.

    Run electrostatics() first; this only displays what that computed, so the
    method and its caveats belong to that call, not this one.
    """
    grid_path = Path(path).expanduser() if path else default_cache_dir() / "potential.dx"
    if not grid_path.is_file():
        raise ViewerError(
            f"No potential grid at {grid_path}. Run electrostatics() first, or "
            "pass path= to an OpenDX file."
        )
    args: dict[str, Any] = {
        "name": handle,
        "volume": grid_path.read_text(),
        "palette": palette,
    }
    if domain is not None:
        if len(domain) != _DOMAIN_BOUNDS:
            raise ViewerError("domain must be [min, max]")
        args["domain"] = [float(domain[0]), float(domain[1])]
    result = await _call("color_by_volume", args)
    return {"handle": handle, "dx_path": str(grid_path), **result}


@mcp.tool()
async def color_by_conservation(
    chain: str | None = None,
    bins: int = 7,
    representation: str = "molecular-surface",
    palette: str = "conservation",
    prefix: str = "cons",
    hide_others: bool = True,
) -> dict[str, Any]:
    """Colour the structure by the conservation scores from conservation().

    bins: how many bands to split the entropy range into, low entropy (most
      conserved) first. Each band becomes its own handle named
      prefix_0 .. prefix_n, so a band can be reused, hidden or measured.

    representation: each band is drawn in this style. Bands are drawn, not
      recoloured: a per-residue scalar is not something Mol* can theme, so the
      colour has to live in geometry. Avoid "cartoon" here — a ribbon needs
      consecutive backbone and conservation bands are scattered single
      residues, so most of the structure simply does not draw. Surface and
      spacefill are per-atom and tile correctly.
    hide_others: hide the automatic representations first, so the bands are
      not sitting underneath a uniformly coloured copy of the same atoms.

    This is a banded ramp rather than a continuous one. Mol* colours by fields
    it can read off the structure — b-factor, occupancy — or by a volume, and
    per-residue conservation is neither, so bands over handles is what the
    existing machinery expresses honestly. It reads as a gradient at 7 bands
    and it composes, which a continuous theme would not.
    """
    array = _require_structure()
    if not _conservation_scores:
        raise ViewerError("No conservation scores yet — call conservation() first.")
    key = chain or next(iter(_conservation_scores))
    if key not in _conservation_scores:
        known = ", ".join(sorted(_conservation_scores))
        raise ViewerError(f"No conservation for chain {key!r}. Scored: {known}")
    scores = _conservation_scores[key]
    if bins < _MIN_BINS:
        raise ViewerError(f"bins must be at least {_MIN_BINS}")

    entropies = np.array([s.entropy for s in scores])
    edges = np.linspace(entropies.min(), entropies.max(), bins + 1)
    ramp = _ramp(palette, bins)

    if hide_others:
        # The preset's own cartoon covers the same atoms; leaving it visible
        # buries the bands under a uniform colour.
        for existing in (
            "auto",
            *(n for n in _handles.names() if not n.startswith(prefix)),
        ):
            # A handle with no drawn representation cannot be hidden; that is
            # not a failure worth interrupting the colouring for.
            with contextlib.suppress(ViewerError):
                await _call("hide", {"name": existing})

    bands: list[dict[str, Any]] = []
    for i in range(bins):
        low, high = edges[i], edges[i + 1]
        # The last band has to include its upper edge or the most variable
        # residue silently lands in no band at all.
        in_band = (entropies >= low) & (
            entropies <= high if i == bins - 1 else entropies < high
        )
        keys = {
            (s.chain, s.seq, s.ins_code)
            for s, hit in zip(scores, in_band, strict=True)
            if hit
        }
        if not keys:
            continue
        indices = _residue_indices(array, keys)
        name = f"{prefix}_{i}"
        _register(name, indices, f"conservation band {i} ({low:.2f}-{high:.2f} entropy)")
        # Each band gets its own *representation*, not just a selection. A
        # selection component carries no geometry, so recolouring one changes
        # nothing on screen while every call still reports success.
        await _call(
            "show",
            {
                "name": name,
                "expression": _indices_to_molscript(array, indices),
                "representation": representation,
                "color": ramp[i],
                "limit": 0,
            },
        )
        bands.append(
            {
                "handle": name,
                "entropy_range": [round(float(low), 3), round(float(high), 3)],
                "residues": len(keys),
                "color": ramp[i],
            }
        )
    return {
        "chain": key,
        "bins": len(bands),
        "palette": palette,
        "most_conserved_first": True,
        "bands": bands,
    }


def _ramp(palette: str, steps: int) -> list[str]:
    """Interpolate a colour ramp to *steps* hex strings, low value first."""
    stops = _PALETTES.get(palette)
    if stops is None:
        known = ", ".join(sorted(_PALETTES))
        raise ViewerError(f"Unknown palette {palette!r}. Available: {known}")
    if steps == 1:
        return [stops[0]]
    out: list[str] = []
    for i in range(steps):
        position = i * (len(stops) - 1) / (steps - 1)
        low = int(np.floor(position))
        high = min(low + 1, len(stops) - 1)
        blend = position - low
        channels = [
            round(
                int(stops[low][1:][c * 2 : c * 2 + 2], 16) * (1 - blend)
                + int(stops[high][1:][c * 2 : c * 2 + 2], 16) * blend
            )
            for c in range(3)
        ]
        out.append("#" + "".join(f"{v:02x}" for v in channels))
    return out


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
