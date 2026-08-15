"""protean MCP server — Phase 1 tools: open_viewer, fetch_structure, screenshot."""

from __future__ import annotations

import base64
import contextlib
import datetime
import gzip
import io
import json
import logging
import math
import re
import webbrowser
from pathlib import Path
from typing import Any

import numpy as np
from biotite.structure import filter_amino_acids
from biotite.structure.io.pdbx import CIFFile, set_structure
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

# Reused rather than redefined. wiggles-em is already a dependency, its
# vocabulary is right for any density map and not only a cryo-EM one, and a
# second enum meaning the same thing would let the two drift — the backend
# lowers wiggles-em scenes onto this viewer, so they have to agree.
from wiggles_em.provenance import Provenance

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
from .analysis.encode import CONTAINERS as MOVIE_CONTAINERS
from .analysis.encode import EncodeError
from .analysis.encode import encode as _encode_movie
from .analysis.encode import ffmpeg_binary as _ffmpeg_binary
from .analysis.superposition import SuperpositionError, parse_structure
from .analysis.superposition import superpose as _superpose
from .analysis.timeline import EASINGS as _EASINGS
from .analysis.timeline import TimelineError
from .analysis.timeline import path as _timeline_path
from .analysis.trajectory import TrajectoryError
from .analysis.trajectory import read as _read_trajectory
from .analysis.trajectory import rmsd_series as _rmsd_series
from .analysis.trajectory import rmsf as _rmsf
from .analysis.trajectory import superpose_frames as _superpose_frames
from .analysis.trajectory import supported_formats as _trajectory_formats
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
from .selections_numpy import conformer_state as _conformer_state
from .selections_numpy import conformers_used as _conformers_used
from .selections_numpy import evaluate as _evaluate
from .selections_numpy import labelled_atom_count as _labelled_atom_count
from .selections_numpy import load_structure as _load_structure
from .selections_numpy import resolve_conformers as _resolve_conformers
from .volumes import VolumeError, read_volume

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
# Named camera positions, in the order they were set: a timeline runs through
# them as written rather than in some sorted order.
_keyframes: dict[str, dict[str, Any]] = {}

# The coordinate stack from the last load_trajectory(), kept because the
# analysis copy is only ever one frame of it.
_trajectory: Any = None

# Conservation scores from the last conservation() call, per chain, so they can
# be coloured without paying for the alignment again.
_conservation_scores: dict[str, Any] = {}

# Whether the viewer is path tracing, mirrored from the last path_trace() reply
# so screenshot() knows which timeout to use.
_path_tracing = False

# An ordinary capture is well under a second; the tracer takes seconds to
# minutes, and aborting it would report a stall for work that was succeeding.
_SCREENSHOT_TIMEOUT = 60.0
_TRACED_SCREENSHOT_TIMEOUT = 600.0
# A figure-resolution capture is a real render, not a viewport grab: 4323x3242
# takes about 2.5s and 12000x9000 about 20s on a real GPU.
_SNAPSHOT_TIMEOUT = 300.0

# Nature's column widths, which most journals sit close to. Anything else goes
# through width_mm rather than being invented here.
_COLUMN_WIDTHS_MM: dict[str, float] = {"single": 89.0, "double": 183.0}
_SNAPSHOT_FORMATS: dict[str, str] = {"png": ".png", "tiff": ".tiff", "jpeg": ".jpg"}
# Mol* renders correctly well past this — 12000x9000 was verified — but a
# capture that large costs 20s and half a gigabyte in this process, and a
# figure needs nowhere near it: 183 mm at 1200 dpi is 56 megapixels.
_MAX_SNAPSHOT_PIXELS = 120_000_000

# The viewer's reserved handle for whatever the load preset drew, which is what
# "the whole scene" means to the display tools.
_WHOLE_SCENE = "auto"

# Background imagery travels as data URIs through the bridge, which accepts 64 MB
# messages. Six skybox faces share that budget, so each one is capped well below
# it — a face this large is already far more than a figure needs.
_MAX_BACKGROUND_IMAGE_BYTES = 8 * 1024 * 1024
# A turntable writes one file per frame and re-renders the scene each time, so
# a runaway frame count is a long wait and a full disk rather than an error.
_MAX_TURNTABLE_FRAMES = 720
# Two frames is the least that is a sequence rather than a picture.
_MIN_TURNTABLE_FRAMES = 2
# Fewer than two keyframes is a still, not a move.
_MIN_KEYFRAMES = 2
_SKYBOX_FACES = ("nx", "ny", "nz", "px", "py", "pz")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

_DOMAIN_BOUNDS = 2
# The uncertainty theme ramps over a fixed [0, 100] domain.
_B_FACTOR_FULL = 100.0
# A homogeneous transform is 4x4.
_TRANSFORM_SIZE = 4
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


def _writable(out: Path, writes: tuple[str, ...], *, overwrite: bool) -> Path:
    """Refuse to change what an existing file *is*, unless asked outright.

    Every tool here writes wherever it is pointed, creating parent directories
    on the way. The caller is a model, so the realistic route to a destructive
    write is a tool call it was talked into, and the demonstrated ones were not
    subtle: `save_session` replacing a 21-byte JSON file with 32 kB of gzip,
    and `electrostatics(path=…)` — an *output* path that reads like an input —
    writing an OpenDX grid over a file named `secret.key`.

    The rule is narrower than "never overwrite", because overwriting is half of
    how these tools are used: capture a figure, adjust the scene, capture it
    again over the same name. What is never intended is a write that changes a
    file from one kind of thing into another, and that is exactly the shape
    both demonstrations had. So an existing file is replaced only when it
    already holds what this tool writes.

    `overwrite=True` says do it anyway. That is a smaller barrier than it looks
    against a hostile tool call — anything that can set the path can set the
    flag — and it is still worth having: it moves destruction from something
    that happens invisibly to something a caller has to ask for by name, in a
    call a reader can see.
    """
    if out.is_dir():
        raise ViewerError(f"{out} is a directory, so there is no file to write there")
    if not out.exists() or overwrite or out.suffix.lower() in writes:
        return out
    raise ViewerError(
        f"{out} already exists and is not {'/'.join(writes)}, so writing here "
        f"would replace a {out.suffix or 'extensionless'} file with something "
        "else entirely. Pass overwrite=True to do that on purpose, or choose "
        "another path."
    )


def _visibility_note(bridge: ViewerBridge) -> str:
    """Flag a backgrounded tab: loads still work there, but only via the pump."""
    visibility = bridge.viewer_visibility
    if visibility is None or visibility == "visible":
        return ""
    return f" (tab is {visibility} — rendering runs on the background-tab pump)"


@mcp.tool()
async def open_viewer(timeout: float = 20, reveal_url: bool = False) -> str:
    """Launch the protean viewer in a browser tab and wait for it to connect.

    Idempotent: if a viewer is already connected, reports its address instead
    of opening a new tab.

    reveal_url: include the handshake token in the address reported back. The
      token is what authenticates a socket, and an *absent* Origin is allowed
      so non-browser clients can connect at all — so whatever holds this URL
      can drive the viewer and answer for it. This reply is read by a model,
      kept in a transcript and often written to a log, so by default the
      address comes back without it and the real one goes straight to the
      browser. Set this only to open the viewer somewhere the launch could not
      reach: a second browser, or another machine forwarding the port.
    """
    bridge = get_bridge()
    await bridge.start()
    # Carries the handshake token; the bridge builds it so no caller can open a
    # viewer that is then refused by its own socket.
    launch_url = bridge.viewer_url
    shown = launch_url if reveal_url else bridge.display_url
    if bridge.viewer_connected:
        return f"Viewer already connected at {shown}{_visibility_note(bridge)}"
    if _static_dir() is None:
        return (
            f"Bridge is listening at {shown}, but the viewer app is not built. "
            "Run `npm install && npm run build` in the viewer/ directory, "
            "then call open_viewer again."
        )
    webbrowser.open(launch_url)
    await bridge.wait_for_viewer(timeout)
    return f"Viewer connected at {shown}{_visibility_note(bridge)}"


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
    discarded = _discard_session_state()
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
    note += discarded
    return f"Loaded {label} ({structure.format}, from {origin}): {result}{note}"


def _discard_session_state() -> str:
    """Drop everything that belonged to the structure being replaced.

    A trajectory and a set of keyframes are each bound to the structure they
    were made for: the frames are that molecule's atoms in that order, and a
    saved camera was framed on it. Carried across a load they keep answering,
    about a molecule that is no longer here — and ``rmsf()`` reads the
    trajectory's own first frame rather than the loaded structure, so nothing
    would mismatch and nothing would complain. ``load_trajectory`` refuses a
    mismatched atom count at load time; this is the call that would otherwise
    invalidate that check afterwards.

    Returns what was thrown away, for the reply. Doing it silently would be a
    smaller version of the same bug: a caller who had a trajectory loaded
    should be told it is gone rather than discover it from a later refusal.
    """
    global _trajectory  # noqa: PLW0603 - session state
    discarded: list[str] = []
    if _trajectory is not None:
        discarded.append("the trajectory")
    _trajectory = None
    if _keyframes:
        count = len(_keyframes)
        discarded.append(f"{count} keyframe{'s' if count > 1 else ''}")
    _keyframes.clear()
    # Handles and conservation scores were already dropped on every load. They
    # are here so one function is the whole answer to "what does loading a new
    # structure end?". Neither is announced, because neither ever was.
    _handles.clear()
    _conservation_scores.clear()
    if not discarded:
        return ""
    return (
        f" [discarded with the previous structure: {', '.join(discarded)} — "
        "a trajectory and a saved camera belong to the molecule they were "
        "made for]"
    )


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
    surplus = loaded.altloc_surplus
    if theirs is None:
        parts.append(f", {ours} atoms here; the viewer reported no count")
    elif int(theirs) == ours:
        parts.append(f", {ours} atoms in both viewer and analysis")
    else:
        # The branch that used to explain a conformer difference is gone: both
        # halves load every conformer now, so a difference is a difference.
        explained = (
            f" ({surplus} rows of the file are alternate conformers)" if surplus else ""
        )
        parts.append(
            f", MISMATCH: {ours} atoms here but {theirs} in the viewer{explained}. "
            "Analysis and the picture are different molecules; treat counts, "
            "buried areas and potentials as unreliable"
        )
    # Alternate conformers are drawn and selectable, but analysis reads one
    # state at a time -- states never coexist, so geometry over both describes
    # no molecule. Which one is used has to be in the reply: a number computed
    # over conformer A while the picture shows both is exactly the kind of
    # quiet mismatch the rest of this note exists to prevent.
    #
    # Counted from the labels rather than from `altloc_surplus`, which counts
    # rows *beyond the first at a site*: a single partially occupied ion
    # labelled `B` is one row at one site, so the surplus is 0 while the atom
    # is very much an alternate. That combination printed "0 of these are
    # alternate conformers, and analysis reads conformer B".
    labelled = _labelled_atom_count(loaded.array)
    if labelled:
        used = _conformers_used(loaded.array)
        parts.append(
            f"; {labelled} atoms carry an alternate-conformer label, and "
            f"analysis reads conformer {used} — each site resolved to its "
            f"highest occupancy. `alt ''+{used.split('+')[0]}` selects a whole "
            "conformer, `alt A` only the atoms that differ"
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

    `ss H`, `ss S` and `ss L` select helix, strand and loop, as in PyMOL. The
    three helix types are separately addressable: `ss alpha` for the common
    3.6-residue-per-turn helix, `ss 3-10` for the tighter one, `ss pi` for the
    rare wide one, and `ss H` for all three together. `ss extended`,
    `ss bridge`, `ss turn` and `ss bend` reach the remaining DSSP classes.
    Note that `ss S` is strand, following PyMOL, where DSSP's own letter S
    means bend — ask for `ss bend` if that is what you want.

    Secondary structure is computed with DSSP rather than read from the file,
    so it answers the same way for a predicted model as for a deposited one.

    `sym N` names one copy of the asymmetric unit in a biological assembly,
    numbered from 0. Copies share chain ids and residue numbers — haemoglobin
    loaded as its assembly has two chains called A — so `chain A` alone means
    every copy of that chain, and `chain A and sym 0` is the single subunit.
    A selection with no `sym` term keeps meaning every copy. Refused on a
    structure loaded as the asymmetric unit, which has only one.

    `alt A` names an alternate conformer — an atom resolved in more than one
    position — and means exactly the atoms carrying that label, as PyMOL does.
    Only the atoms that actually differ carry one, so `alt A` is usually a
    side chain with no backbone. **The whole conformer is `alt ''+A`**: the
    shared atoms plus that letter. `alt ''` and `alt .` both mean "no
    alternate". The labels are disjoint, so `alt A and alt B` is empty.

    Analysis tools do not use `alt`. They resolve one conformer state on their
    own — the one with the most occupancy — because the states never coexist
    and geometry over both describes no molecule; each says which it used.

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

    radius: in angstroms, greater than zero.
    whole_residues: widen to complete residues, which is usually what a figure
      or a contact list wants.
    exclude_self: leave out the atoms of `of` itself.
    """
    array = _require_structure()
    if not math.isfinite(radius) or radius <= 0:
        # An empty set is the one answer that looks like a real result, so the
        # request that can only produce one is refused rather than served.
        raise ViewerError(
            f"radius must be greater than 0, got {radius:g}. A non-positive "
            "radius matches nothing, which would look like an answer"
        )
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
    opacity: float | None = None,
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
    opacity: 0 is invisible, 1 is solid. Use it to draw a surface you can see
      through to whatever is inside; opacity() changes it afterwards.
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
    if opacity is not None:
        args["opacity"] = opacity
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
async def opacity(opacity: float, name: str = "sele") -> dict[str, Any]:
    """Make an already-displayed selection transparent.

    opacity: 0 is invisible, 1 is solid. 0.3 or so is the usual "ghost" surface
      that lets a cartoon or a ligand show through from inside it.
    name: the handle passed to a previous show(). A handle that was only
      select()ed carries no geometry and is refused, because setting opacity on
      it would change nothing on screen.
    """
    return await _call("opacity", {"name": name, "opacity": opacity})


@mcp.tool()
async def effects(
    outline: bool | None = None,
    outline_color: str | None = None,
    outline_scale: float | None = None,
    occlusion: bool | None = None,
    shadow: bool | None = None,
    depth_of_field: bool | None = None,
    bloom: bool | None = None,
    sharpening: bool | None = None,
) -> dict[str, Any]:
    """Switch screen-space effects on or off.

    Anything omitted is left exactly as it is, so these compose across calls.
    The reply is read back off the canvas rather than echoed.

    outline: draw a line around the silhouette and interior edges. The
      illustrative, textbook look; pairs with shading(style="cel").
    outline_color / outline_scale: only meaningful with the outline on, and
      refused when it is off rather than quietly ignored.
    occlusion: ambient occlusion — darkens crevices, so pockets and clefts
      read as recessed. On by default in Mol*, and the single biggest gain in
      legibility for a surface.
    shadow: cast shadows. Cheap and blunt; occlusion usually reads better.
    depth_of_field: blur what is not at the camera's focus, to push background
      chains back.
    bloom: glow around bright things. On by default, and only visible on
      emissive material.
    sharpening: contrast-adaptive sharpening, worth a little at high DPI.
    """
    args: dict[str, Any] = {}
    for key, value in (
        ("outline", outline),
        ("outline_color", outline_color),
        ("outline_scale", outline_scale),
        ("occlusion", occlusion),
        ("shadow", shadow),
        ("depth_of_field", depth_of_field),
        ("bloom", bloom),
        ("sharpening", sharpening),
    ):
        if value is not None:
            args[key] = value
    if not args:
        raise ViewerError("Pass at least one effect to change")
    return await _call("effects", args)


def _open_snapshot(png: bytes) -> Any:
    """Decode a capture we produced ourselves.

    Pillow refuses images beyond MAX_IMAGE_PIXELS (~89 MP) as possible
    decompression bombs. That guard is for untrusted input; this PNG came from
    our own viewer moments ago, and a legitimate 12000x9000 figure trips it. The
    limit is raised for this decode only, and the real ceiling is enforced
    separately against the requested size.
    """
    previous = PILImage.MAX_IMAGE_PIXELS
    PILImage.MAX_IMAGE_PIXELS = _MAX_SNAPSHOT_PIXELS
    try:
        image = PILImage.open(io.BytesIO(png))
        image.load()
        return image
    finally:
        PILImage.MAX_IMAGE_PIXELS = previous


def _background_image_uri(source: str) -> str:
    """Turn a local image into a data URI, or pass a real URL through.

    Opened with Pillow rather than trusted by extension. Mol* takes a URL and
    draws nothing when it fails to load — no error, no reply field, just a
    background that stays as it was — so a file that is not an image has to be
    refused here or it becomes a silent no-op.
    """
    if source.startswith(("http://", "https://", "data:")):
        return source

    path = Path(source).expanduser()
    if not path.is_file():
        raise ViewerError(f"No image at {source!r}")
    raw = path.read_bytes()
    if len(raw) > _MAX_BACKGROUND_IMAGE_BYTES:
        raise ViewerError(
            f"{path.name} is {len(raw) // 1024} kB, beyond the "
            f"{_MAX_BACKGROUND_IMAGE_BYTES // 1024 // 1024} MB a background image "
            "can travel as. Downscale it."
        )
    try:
        with PILImage.open(io.BytesIO(raw)) as probe:
            probe.verify()
            fmt = (probe.format or "").lower()
    except Exception as exc:
        raise ViewerError(f"{path.name} is not an image Pillow can read: {exc}") from exc

    mime = "jpeg" if fmt in {"jpeg", "jpg"} else fmt
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"


def _skybox_faces(directory: str) -> dict[str, str]:
    """Find the six cube faces in *directory*, named nx/ny/nz/px/py/pz.

    A cube map is six files that have to agree, so naming them by convention
    beats six arguments a caller has to keep in the right order.
    """
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise ViewerError(f"No directory at {directory!r} to read skybox faces from")

    found: dict[str, str] = {}
    missing: list[str] = []
    for face in _SKYBOX_FACES:
        for suffix in _IMAGE_SUFFIXES:
            candidate = folder / f"{face}{suffix}"
            if candidate.is_file():
                found[face] = _background_image_uri(str(candidate))
                break
        else:
            missing.append(face)
    if missing:
        raise ViewerError(
            f"{folder} is missing skybox faces: {', '.join(missing)}. "
            f"All six are needed, named {'/'.join(_SKYBOX_FACES)} "
            f"with one of {', '.join(_IMAGE_SUFFIXES)}."
        )
    return found


def _incomplete_capture(image: Any) -> bool:
    """Did part of this capture never get rendered?

    A large capture can come back with the right dimensions and most of the
    frame simply unwritten — fully transparent rather than background-coloured.
    Measured under software rendering: at 4323 px wide, three of the four
    corners were empty and 79% of the frame was blank, and nothing in the reply
    said so. On an opaque canvas a transparent pixel cannot be legitimate, so
    it is the signal.

    Reads the alpha channel's range rather than the pixels, which stays cheap
    on a 14-megapixel image.
    """
    if image.mode != "RGBA":
        return False
    lowest, _ = image.getchannel("A").getextrema()
    return bool(lowest == 0)


def _snapshot_pixels(
    column: str | None, width_mm: float | None, dpi: int
) -> tuple[int, float]:
    """Turn a physical width into pixels, refusing anything unprintable.

    The arithmetic is here rather than at the call site because a model asked
    for "600 dpi" and left to multiply would produce a 900-pixel figure that
    claims 600 dpi — a wrong answer that looks entirely right, and that no
    return value would catch.
    """
    if (column is None) == (width_mm is None):
        raise ViewerError(
            "Pass exactly one of column "
            f"({', '.join(sorted(_COLUMN_WIDTHS_MM))}) or width_mm"
        )
    if column is not None:
        if column not in _COLUMN_WIDTHS_MM:
            raise ViewerError(
                f"Unknown column {column!r}. Available: "
                f"{', '.join(sorted(_COLUMN_WIDTHS_MM))}, or pass width_mm"
            )
        millimetres = _COLUMN_WIDTHS_MM[column]
    else:
        assert width_mm is not None
        millimetres = width_mm
    if millimetres <= 0:
        raise ViewerError(f"Figure width must be positive, got {millimetres} mm")
    if dpi <= 0:
        raise ViewerError(f"DPI must be positive, got {dpi}")

    pixels = round(millimetres / 25.4 * dpi)
    if pixels < 1:
        raise ViewerError(f"{millimetres} mm at {dpi} dpi rounds to no pixels at all")
    # Checked against width squared rather than the exact area, because the
    # height follows the viewport's aspect and is not known until the viewer
    # answers. Refusing up front beats discovering it after a long render.
    if pixels * pixels > _MAX_SNAPSHOT_PIXELS:
        raise ViewerError(
            f"{millimetres} mm at {dpi} dpi is {pixels} pixels wide, which is beyond "
            f"what can be captured ({_MAX_SNAPSHOT_PIXELS // 1_000_000} megapixels). "
            f"Lower the dpi or the width."
        )
    return pixels, millimetres


@mcp.tool()
async def snapshot(
    path: str,
    column: str | None = None,
    width_mm: float | None = None,
    dpi: int = 300,
    format: str = "png",
    transparent: bool | None = None,
    crop: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save a publication-resolution figure at a real physical size.

    Unlike screenshot(), which captures the viewport as it is, this renders at
    whatever pixel count the requested size and DPI imply, and writes the
    resolution into the file so it survives outside this reply.

    column: "single" (89 mm) or "double" (183 mm) — Nature's column widths, and
      close enough to most journals to be a sane default. Pass width_mm instead
      for anything else. Exactly one of the two.
    dpi: 300 is the usual journal minimum, 600 is common for line art. The
      pixel count follows from this and the width, so you never compute it.
    format: png, tiff or jpeg. PNG and TIFF keep transparency and are lossless;
      JPEG has no alpha channel at all and is refused with transparency on.
    transparent: overrides the canvas setting for this one capture.
    crop: trim to the molecule's bounds. This changes the output dimensions, so
      the reply reports the physical width the result actually corresponds to.
    overwrite: replace the file at `path` even when it holds something other
      than what this tool writes. Off by default so a call cannot quietly turn
      one kind of file into another — the shape of every destructive write
      found in the security pass.

    Returns the path, the pixel dimensions, the DPI written into the file, and
    the size on disk. Height follows the viewport's aspect ratio.
    """
    chosen = format.lower()
    if chosen not in _SNAPSHOT_FORMATS:
        raise ViewerError(
            f"Unknown format {format!r}. "
            f"Available: {', '.join(sorted(_SNAPSHOT_FORMATS))}"
        )
    width, millimetres = _snapshot_pixels(column, width_mm, dpi)

    # Refused here rather than in the viewer: the capture is fine, it is the
    # file we would be asked to write that cannot hold an alpha channel.
    if chosen == "jpeg" and transparent:
        raise ViewerError(
            "JPEG has no alpha channel, so it cannot hold a transparent "
            "background. Use png or tiff, or pass transparent=False."
        )

    args: dict[str, Any] = {"width": width, "crop": crop}
    if transparent is not None:
        args["transparent"] = transparent

    bridge = _require_viewer()
    timeout = _TRACED_SCREENSHOT_TIMEOUT if _path_tracing else _SNAPSHOT_TIMEOUT
    result = await bridge.request("snapshot", args, timeout=timeout)

    data_uri: str = result["data_uri"]
    header, _, payload = data_uri.partition(",")
    if "base64" not in header:
        raise ViewerError(f"Unexpected snapshot encoding: {header}")
    png = base64.b64decode(payload)

    out = Path(path).expanduser()
    if not out.suffix:
        out = out.with_suffix(_SNAPSHOT_FORMATS[chosen])
    out = _writable(out, tuple(_SNAPSHOT_FORMATS.values()), overwrite=overwrite)
    out.parent.mkdir(parents=True, exist_ok=True)

    image = _open_snapshot(png)
    if not result.get("transparent") and _incomplete_capture(image):
        raise ViewerError(
            "The capture came back incomplete: parts of the "
            f"{image.width}x{image.height} image were never rendered. "
            "Renderers run out of room at large sizes, and software rendering "
            "does so well before a real GPU does. Lower the dpi or the width, "
            "or capture on a machine with a GPU."
        )
    saved_dpi = float(dpi)
    save: dict[str, Any] = {"dpi": (dpi, dpi)}
    if chosen == "jpeg":
        # Already refused when transparency was asked for; this handles a
        # canvas that happens to be transparent anyway.
        image = image.convert("RGB")
        save["quality"] = 95
    image.save(out, format=chosen.upper(), **save)

    return {
        "path": str(out),
        "format": chosen,
        "pixels": [image.width, image.height],
        "dpi": saved_dpi,
        # What the result is actually the width of. Cropping trims the frame,
        # so the requested millimetres stop being true and saying so beats
        # repeating the request back.
        "width_mm": round(image.width / dpi * 25.4, 2),
        "requested_width_mm": millimetres,
        "cropped": bool(result.get("cropped")),
        "bytes": out.stat().st_size,
        **({"traced_ms": result["traced_ms"]} if "traced_ms" in result else {}),
    }


@mcp.tool()
async def load_trajectory(
    path: str, stride: int = 1, max_frames: int = 100
) -> dict[str, Any]:
    """Lay a coordinate trajectory onto the structure already loaded.

    A trajectory file carries coordinates and nothing else — no atom names, no
    chains — so it has to be read onto a structure that says what the atoms
    are. Load that first with fetch_structure, then this. The atom counts must
    match exactly, and a mismatch is refused rather than animated: the wrong
    pairing parses cleanly and describes nothing.

    path: an .xtc, .trr, .dcd or .nc file.
    stride: take every nth frame. The payload is atoms times frames, so this is
      how a long run is made viewable rather than by hoping.
    max_frames: stop after this many kept frames, as a floor under the same
      problem. Both are reported back so a truncated view is never silent.

    Afterwards, frame(index) steps through it and turntable()/snapshot()
    capture it.
    """
    global _structure, _structure_error, _structure_identifier, _trajectory  # noqa: PLW0603 - session state
    # A trajectory carries one position per atom and no alternates, so the
    # template has to be one conformer state. Matching against every conformer
    # would refuse a trajectory that belongs to this structure, and tell the
    # caller to load a different one.
    template, conformer = _resolve_conformers(_require_structure())
    if max_frames < 1:
        raise ViewerError(f"max_frames must be at least 1, got {max_frames}")

    try:
        stack = _read_trajectory(path, template, stride=stride, limit=max_frames)
    except TrajectoryError as exc:
        raise ViewerError(str(exc)) from exc

    available = stack.stack_depth()
    # The same renumbering every path that re-sends a structure has to do, or
    # handles built afterwards name different atoms than they resolve to.
    _renumber_for_viewer(stack)
    reply = await _send_structure(stack, Path(path).stem)

    # The analysis copy becomes the first frame, so selections keep working and
    # describe the same molecule the viewer is showing.
    _structure, _structure_error = stack[0], None
    _structure_identifier = str(Path(path).expanduser())
    _trajectory = stack
    _handles.clear()

    return {
        "trajectory": str(Path(path).expanduser()),
        "frames": available,
        "stride": stride,
        "atoms": int(stack.array_length()),
        **({"conformer": conformer} if conformer else {}),
        "viewer_atoms": reply.get("atom_count"),
        "formats": _trajectory_formats(),
    }


def _require_trajectory() -> Any:
    if _trajectory is None:
        raise ViewerError("No trajectory loaded — call load_trajectory first.")
    return _trajectory


@mcp.tool()
async def rmsf(per: str = "residue", limit: int = 50) -> dict[str, Any]:
    """Per-atom fluctuation across the trajectory, as numbers.

    Frames are superposed onto the first before measuring, so what comes back
    is internal motion rather than the molecule drifting across the box — that
    drift would otherwise read as enormous fluctuation everywhere, which is a
    confident wrong answer rather than an obviously broken one.

    per: "residue" averages over each residue's atoms, which is what a figure
      or a table usually wants; "atom" gives every value.
    limit: how many of the most mobile entries to list. The summary counts and
      the extremes are always exact; only the listing is capped.

    color_by_rmsf() draws the same numbers on the structure.
    """
    if per not in ("residue", "atom"):
        raise ViewerError(f"Unknown grouping {per!r} (residue, atom)")
    stack = _require_trajectory()
    try:
        values = _rmsf(_superpose_frames(stack))
    except TrajectoryError as exc:
        raise ViewerError(str(exc)) from exc

    array = stack[0]
    entries: list[dict[str, Any]]
    if per == "atom":
        entries = [
            {
                "chain": str(array.chain_id[i]),
                "seq": int(array.res_id[i]),
                "atom": str(array.atom_name[i]),
                "rmsf": round(float(values[i]), 3),
            }
            for i in range(array.array_length())
        ]
    else:
        grouped: dict[tuple[str, int, str], list[float]] = {}
        names: dict[tuple[str, int, str], str] = {}
        for i in range(array.array_length()):
            key = (str(array.chain_id[i]), int(array.res_id[i]), str(array.ins_code[i]))
            grouped.setdefault(key, []).append(float(values[i]))
            names[key] = str(array.res_name[i])
        entries = [
            {
                "chain": chain,
                "seq": seq,
                "comp": names[(chain, seq, ins)],
                "rmsf": round(float(np.mean(group)), 3),
            }
            for (chain, seq, ins), group in grouped.items()
        ]

    ranked = sorted(entries, key=lambda e: float(e["rmsf"]), reverse=True)
    magnitudes = [float(e["rmsf"]) for e in entries]
    return {
        "per": per,
        "frames": int(stack.stack_depth()),
        "count": len(entries),
        "mean": round(float(np.mean(magnitudes)), 3),
        "max": round(float(np.max(magnitudes)), 3),
        "min": round(float(np.min(magnitudes)), 3),
        "most_mobile": ranked[:limit],
        "truncated": len(ranked) > limit,
    }


@mcp.tool()
async def color_by_rmsf(
    representation: str = "cartoon",
    scale: str = "relative",
    hide_others: bool = True,
) -> dict[str, Any]:
    """Colour the structure by how much each atom moves across the trajectory.

    The same numbers rmsf() returns, drawn on the molecule: rigid core one
    colour, mobile loops and termini the other. Carried into the viewer in the
    B-factor column and drawn with Mol*'s uncertainty theme, which is the one
    per-atom numeric field it will ramp over — so this re-sends the structure,
    and the reply says so.

    Only the viewer's copy has its B-factors overwritten. The analysis copy
    keeps its crystallographic values, because a B-factor that quietly means
    something else is exactly what gets read as temperature later.

    scale: "relative" stretches the ramp over this trajectory's own range,
      which is what makes a rigid core stand out; "absolute" pins 0 to the low
      end so two runs can be compared.
    """
    if scale not in ("relative", "absolute"):
        raise ViewerError(f"Unknown scale {scale!r} (relative, absolute)")
    stack = _require_trajectory()
    try:
        values = _rmsf(_superpose_frames(stack))
    except TrajectoryError as exc:
        raise ViewerError(str(exc)) from exc

    lowest, highest = float(values.min()), float(values.max())
    span = highest - lowest
    # The uncertainty theme's domain is [0, 100], so values arrive already
    # stretched into it; the theme takes no domain from us.
    if scale == "relative" and span > 0:
        scaled = (values - lowest) / span * _B_FACTOR_FULL
    else:
        ceiling = highest if highest > 0 else 1.0
        scaled = np.clip(values / ceiling, 0.0, 1.0) * _B_FACTOR_FULL

    array = stack[0].copy()
    _renumber_for_viewer(array)
    display = array.copy()
    display.b_factor = np.asarray(scaled, dtype=float)

    await _send_structure(display, "rmsf")
    if hide_others:
        with contextlib.suppress(ViewerError):
            await _call("hide", {"name": "auto"})

    name = "rmsf"
    await _call(
        "show",
        {
            "name": name,
            "expression": _indices_to_molscript(array, np.arange(array.array_length())),
            "representation": representation,
            "color": "uncertainty",
            "limit": 0,
        },
    )

    # Handles are Python-side atom indices and the atom order is untouched, so
    # they survive the reload; their components do not, so redraw them.
    restored = 0
    for handle in _handles.names():
        await _display(handle, _handles.get(handle).indices)
        restored += 1

    return {
        "name": name,
        "representation": representation,
        "scale": scale,
        "frames": int(stack.stack_depth()),
        "rmsf_min": round(lowest, 3),
        "rmsf_max": round(highest, 3),
        "reloaded": True,
        "b_factors_overwritten": "viewer copy only",
        "handles_redrawn": restored,
    }


@mcp.tool()
async def rmsd_series(reference: int = 0) -> dict[str, Any]:
    """RMSD of every frame against one of them, after superposing onto it.

    The usual read on whether a simulation has settled: a series that climbs
    and then flattens has equilibrated, one still climbing at the end has not.

    reference: which frame to measure against, usually the first.
    """
    stack = _require_trajectory()
    try:
        series = _rmsd_series(stack, reference)
    except TrajectoryError as exc:
        raise ViewerError(str(exc)) from exc

    values = [round(float(v), 3) for v in series]
    return {
        "reference": reference,
        "frames": len(values),
        "rmsd": values,
        "mean": round(float(np.mean(values)), 3),
        "max": round(float(np.max(values)), 3),
        "final": values[-1],
    }


@mcp.tool()
async def frame(index: int) -> dict[str, Any]:
    """Show one frame of the loaded trajectory.

    index: 0 to frames-1, as reported by load_trajectory. Out of range is
      refused rather than clamped, so a loop that runs off the end says so.
    """
    return await _call("frame", {"index": index})


async def _capture_sequence(
    directory: str,
    width: int,
    transparent: bool | None,
    place: list[Any],
) -> dict[str, Any]:
    """Capture one frame per entry in *place*, writing frame_0000.png upward.

    Shared by turntable(), record_trajectory() and record_timeline(), which
    differ only in how they position the scene for each frame. Each entry is a
    coroutine factory called just before its capture; the loop itself — the
    naming, the decode, the incomplete-capture guard — is the same every time,
    and was three near-copies before it was one.
    """
    if width < 1:
        raise ViewerError(f"Frame width must be at least 1 pixel, got {width}")

    out = Path(directory).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    bridge = _require_viewer()
    timeout = _TRACED_SCREENSHOT_TIMEOUT if _path_tracing else _SNAPSHOT_TIMEOUT
    args: dict[str, Any] = {"width": width, "crop": False}
    if transparent is not None:
        args["transparent"] = transparent

    written: list[str] = []
    for index, position in enumerate(place):
        await position()
        result = await bridge.request("snapshot", args, timeout=timeout)
        data_uri: str = result["data_uri"]
        header, _, payload = data_uri.partition(",")
        if "base64" not in header:
            raise ViewerError(f"Unexpected frame encoding: {header}")
        png = base64.b64decode(payload)
        image = _open_snapshot(png)
        # Per frame, because one bad frame in thirty-six is a flicker nobody
        # notices until the movie is assembled.
        if not result.get("transparent") and _incomplete_capture(image):
            raise ViewerError(
                f"Frame {index} came back incomplete: parts of the "
                f"{image.width}x{image.height} image were never rendered. "
                "Lower the width, or capture on a machine with a GPU."
            )
        frame_path = out / f"frame_{index:04d}.png"
        frame_path.write_bytes(png)
        written.append(str(frame_path))

    return {
        "directory": str(out),
        "frames": len(written),
        "width": width,
        "bytes": sum(Path(f).stat().st_size for f in written),
    }


@mcp.tool()
async def record_trajectory(
    directory: str, width: int = 1200, stride: int = 1, transparent: bool | None = None
) -> dict[str, Any]:
    """Capture one image per trajectory frame, ready to encode.

    The trajectory equivalent of turntable(): where that orbits a still
    structure, this steps through the motion with the camera held still. Both
    write frame_0000.png upward, which is what movie() reads.

    directory: where the frames go. Created if missing.
    width: frame width in pixels; the height follows the viewport's aspect.
    stride: capture every nth frame, for a long run or a shorter movie.
    transparent: capture onto nothing, for compositing.

    The viewer is left showing the frame it started on.
    """
    if stride < 1:
        raise ViewerError(f"Stride must be 1 or more, got {stride}")
    stack = _require_trajectory()
    total = int(stack.stack_depth())
    bridge = _require_viewer()

    indices = list(range(0, total, stride))

    def step(index: int) -> Any:
        async def place() -> None:
            await bridge.request("frame", {"index": index}, timeout=_SNAPSHOT_TIMEOUT)

        return place

    result = await _capture_sequence(
        directory, width, transparent, [step(i) for i in indices]
    )
    # Back to where the run started, so the viewer is not left mid-trajectory.
    await bridge.request("frame", {"index": 0}, timeout=_SNAPSHOT_TIMEOUT)
    return {**result, "of": total, "stride": stride}


@mcp.tool()
async def keyframe(name: str, remove: bool = False) -> dict[str, Any]:
    """Remember where the camera is now, under a name.

    A timeline runs through keyframes in the order they were set, so orient the
    view, save it, move, save again. focus() and orient() are the usual way to
    get somewhere worth saving.

    name: what to call this position. Setting the same name twice replaces it
      without changing where it sits in the order.
    remove: forget this one instead of saving it.
    """
    if remove:
        if name not in _keyframes:
            raise ViewerError(
                f"No keyframe named {name!r}. Known: {', '.join(_keyframes) or '(none)'}"
            )
        del _keyframes[name]
        return {"removed": name, "keyframes": list(_keyframes)}

    state = await _call("camera_state", {})
    _keyframes[name] = {
        "position": list(state["position"]),
        "target": list(state["target"]),
        "up": list(state["up"]),
    }
    return {"keyframe": name, "keyframes": list(_keyframes), **_keyframes[name]}


@mcp.tool()
async def list_keyframes() -> dict[str, Any]:
    """The camera positions saved so far, in the order a timeline will use."""
    return {
        "keyframes": [{"name": name, **state} for name, state in _keyframes.items()],
        "count": len(_keyframes),
    }


@mcp.tool()
async def record_timeline(
    directory: str,
    frames: int = 60,
    width: int = 1200,
    easing: str = "ease-in-out",
    transparent: bool | None = None,
) -> dict[str, Any]:
    """Capture a camera move through the saved keyframes.

    The camera swings around its target rather than sliding between positions,
    so the subject stays the same size throughout — a straight line between two
    viewpoints passes closer to the molecule on the way, and for a half-turn
    goes through it.

    frames: how many captures make up the move. At 30 fps, 60 frames is two
      seconds.
    easing: how the move starts and stops — ease-in-out by default, because
      linear motion reads as mechanical exactly at the cuts. Also linear,
      ease-in, ease-out.
    width: frame width in pixels; the height follows the viewport's aspect.

    Frames are written as frame_0000.png upward, for movie() to encode. The
    camera is left on the last keyframe.
    """
    if easing not in _EASINGS:
        raise ViewerError(f"Unknown easing {easing!r}. Available: {', '.join(_EASINGS)}")
    if len(_keyframes) < _MIN_KEYFRAMES:
        raise ViewerError(
            f"A timeline needs at least two keyframes; {len(_keyframes)} saved. "
            "Point the camera somewhere and call keyframe()."
        )

    try:
        states = _timeline_path(list(_keyframes.values()), frames, easing)
    except TimelineError as exc:
        raise ViewerError(str(exc)) from exc

    bridge = _require_viewer()

    def move(state: dict[str, Any]) -> Any:
        async def place() -> None:
            await bridge.request("set_camera", state, timeout=_SNAPSHOT_TIMEOUT)

        return place

    result = await _capture_sequence(
        directory, width, transparent, [move(state) for state in states]
    )
    return {
        **result,
        "keyframes": list(_keyframes),
        "easing": easing,
        "seconds_at_30fps": round(frames / 30, 2),
    }


@mcp.tool()
async def movie(
    directory: str, path: str, fps: int = 30, overwrite: bool = False
) -> dict[str, Any]:
    """Encode a directory of captured frames into a movie.

    Reads frame_0000.png upward — what turntable() and record_trajectory()
    write — and hands them to ffmpeg.

    path: the output file. The extension chooses the container: .mp4 plays
      everywhere, .gif drops into a slide or an issue, .webm keeps
      transparency. MP4 cannot hold an alpha channel at all.
    fps: frames per second. The reply reports the duration this works out to,
      which is usually what you actually wanted to control.

    ffmpeg has to be installed; it is checked before anything is written, and
    the frames are ordinary PNGs if you would rather encode them elsewhere.
    """
    # Encoding over a previous encode is the ordinary case, so the containers
    # encode.py accepts are what may be replaced. Shared with it rather than
    # listed again: a second copy of a table is the thing this repo keeps
    # getting caught by.
    _writable(Path(path).expanduser(), tuple(MOVIE_CONTAINERS), overwrite=overwrite)
    try:
        return dict(_encode_movie(directory, path, fps))
    except EncodeError as exc:
        raise ViewerError(str(exc)) from exc


@mcp.tool()
async def spin(
    mode: str = "spin", speed: float | None = None, angle: float | None = None
) -> dict[str, Any]:
    """Set the viewer turning on its own, for looking rather than capturing.

    This is a live animation in the browser: it makes a structure easier to
    read on screen, and it does not produce frames. turntable() is the one that
    writes a sequence.

    mode: "spin" turns continuously, "rock" swings back and forth, "off" stops.
    speed: radians per second. Mol*'s defaults are 1 spinning, 0.3 rocking.
    angle: rock only — how far it swings either side, in degrees.
    """
    args: dict[str, Any] = {"mode": mode}
    if speed is not None:
        args["speed"] = speed
    if angle is not None:
        args["angle"] = angle
    return await _call("spin", args)


@mcp.tool()
async def turntable(
    directory: str,
    frames: int = 36,
    width: int = 1200,
    degrees: float = 360.0,
    transparent: bool | None = None,
) -> dict[str, Any]:
    """Capture a numbered frame sequence orbiting the structure.

    The camera is moved a fixed step and captured, frame by frame, rather than
    a live animation being sampled — so the sequence is reproducible and the
    step is exact. A full 360 turn ends where it started, which is what makes
    the sequence loop cleanly.

    Sizes are in pixels here, not millimetres: a movie has a frame size, not a
    physical width. snapshot() is the one that thinks in millimetres and DPI.

    directory: where the frames are written, as frame_0000.png upward. Created
      if it does not exist; existing frames with the same names are replaced.
    frames: how many captures make up the turn. 36 gives a 10-degree step.
    degrees: total rotation across the sequence. 360 loops; 180 gives a
      half-turn that reverses cleanly for a ping-pong.
    transparent: capture onto nothing, for compositing.

    Encoding to MP4 or GIF is Phase 5 — this writes the frames that step needs.
    """
    if frames < _MIN_TURNTABLE_FRAMES:
        raise ViewerError(f"A turntable needs at least 2 frames, got {frames}")
    if frames > _MAX_TURNTABLE_FRAMES:
        raise ViewerError(
            f"{frames} frames is beyond the {_MAX_TURNTABLE_FRAMES} this writes in one "
            "call. Capture a shorter turn, or run it twice."
        )
    bridge = _require_viewer()
    step = degrees / frames

    def turn(index: int) -> Any:
        async def place() -> None:
            # The first frame is captured where the camera already is; every
            # later one is a step further round.
            if index:
                await bridge.request(
                    "orbit", {"degrees": step}, timeout=_SNAPSHOT_TIMEOUT
                )

        return place

    result = await _capture_sequence(
        directory, width, transparent, [turn(i) for i in range(frames)]
    )
    # Close the loop, so a turntable is not a one-way trip that leaves every
    # later capture facing somewhere else.
    await bridge.request(
        "orbit", {"degrees": degrees - step * (frames - 1)}, timeout=_SNAPSHOT_TIMEOUT
    )
    return {
        **result,
        "degrees": degrees,
        "step_degrees": round(step, 4),
        "first": str(Path(result["directory"]) / "frame_0000.png"),
        "last": str(Path(result["directory"]) / f"frame_{frames - 1:04d}.png"),
    }


@mcp.tool()
async def path_trace(
    enabled: bool = True,
    quality: str = "standard",
    bounces: int | None = None,
    shadows: bool | None = None,
    denoise: bool | None = None,
) -> dict[str, Any]:
    """Switch the renderer to Mol*'s progressive path tracer.

    This is the highest-quality mode: real global illumination, soft shadows
    and light that bounces between surfaces, rather than the screen-space
    approximations effects() offers. It is also far slower, and it changes
    every subsequent screenshot rather than being a one-off.

    **Cost.** Measured on a small protein at 800x600 on a real GPU: draft 1.2s,
    standard 4.1s, high 15.8s per capture — roughly 4x per step, and scaling
    with pixel count, so figure-resolution captures run into minutes. Under
    software rendering it does not finish at all, and the call is refused up
    front if the browser lacks the WebGL extensions the tracer needs.

    quality: draft, standard, high or ultra — 8, 32, 128 or 512 samples. On a
      simple scene the denoiser converges early and draft already looks like
      high; the ladder buys most on surfaces and heavy transparency.
    bounces: how far light carries between surfaces, 1 to 16. Mol*'s default
      is 4.
    shadows / denoise: on by default; turning denoise off shows the raw
      sampling, which is mostly useful for judging whether to spend more.
    enabled: pass False to go back to ordinary rendering.

    Screenshots report how long the trace took.
    """
    global _path_tracing  # noqa: PLW0603 - mirrors viewer state for timeouts
    args: dict[str, Any] = {"enabled": enabled, "quality": quality}
    for key, value in (("bounces", bounces), ("shadows", shadows), ("denoise", denoise)):
        if value is not None:
            args[key] = value
    result = await _call("path_trace", args)
    # Track what the *canvas* reported, not what was asked for, so a refused
    # enable does not leave screenshots waiting minutes for a raster render.
    _path_tracing = bool(result.get("enabled"))
    return result


@mcp.tool()
async def material(
    finish: str = "matte",
    name: str = "sele",
    metalness: float | None = None,
    roughness: float | None = None,
    emissive: float | None = None,
) -> dict[str, Any]:
    """Give a displayed selection a surface finish.

    finish: one of — capabilities() reports the live list. They run from dull to
      sharp, so a shinier name really is shinier.

      matte      Fully diffuse. Mol*'s default, and the way back.
      satin      A soft, broad sheen.
      glossy     A tight highlight — wet or lacquered.
      metallic   Brushed metal: the highlight takes the surface colour.
      chrome     Polished metal, close to a mirror.

    metalness / roughness: 0 to 1, overriding the finish where given.
      Roughness runs 0 (mirror) to 1 (fully diffuse), and only bites when
      there is some metalness: a true dielectric has a 4% specular term that
      roughness barely moves.
    emissive: 0 to 1, self-illumination. Note that effects(bloom=True) glows
      only where this is above zero — bloom's default mode is emissive, so on
      an ordinary material it correctly draws nothing at all. The reply says
      whether bloom will actually show.

    name: the handle passed to a previous show(). A select()-only handle
      carries no geometry and is refused.

    Materials need a light to play off: pair a shiny finish with
    lighting(rig="studio") or "ring" rather than "flat", which has no
    directional light to reflect.
    """
    args: dict[str, Any] = {"name": name, "finish": finish}
    for key, value in (
        ("metalness", metalness),
        ("roughness", roughness),
        ("emissive", emissive),
    ):
        if value is not None:
            args[key] = value
    return await _call("material", args)


@mcp.tool()
async def shading(
    style: str, name: str = "sele", cel_steps: int | None = None
) -> dict[str, Any]:
    """Change how a displayed selection is shaded.

    style: one of — capabilities() reports the live list.

      normal         Mol*'s own shading. Also the way back.
      cel            Banded, cartoon-like. Pair with effects(outline=True) for
                     the illustrative look.
      xray           The ghost look: see-through with the edges picked out.
                     Cheaper and cleaner than opacity for showing what is
                     inside a surface.
      xray-inverted  Inverts which parts fade — the facing surface goes and the
                     rim stays.
      flat           Unlit flat colour, for a diagram rather than a picture of
                     an object.

    name: the handle passed to a previous show(). A select()-only handle
      carries no geometry and is refused.
    cel_steps: number of bands, 2 to 16. Global to the renderer, so it affects
      everything cel shaded, not just this selection.
    """
    args: dict[str, Any] = {"name": name, "style": style}
    if cel_steps is not None:
        args["cel_steps"] = cel_steps
    return await _call("shading", args)


@mcp.tool()
async def lighting(
    rig: str = "standard",
    intensity: float | None = None,
    ambient: float | None = None,
    exposure: float | None = None,
) -> dict[str, Any]:
    """Light the scene with a named rig.

    rig: one of — capabilities() reports the live list, and an unknown name is
      refused with it rather than quietly leaving the lighting unchanged.

      standard     Mol*'s own single key light. Also the way back.
      flat         No directional light; purely ambient. Even and shadowless,
                   which is what a schematic figure wants.
      three-point  Key, fill and back light. Form from the key, shadows opened
                   by the fill, separation from the back.
      rim          Weak key, strong back light. Silhouette first — good for
                   showing a shape, poor for reading surface detail.
      ring         Six lights on a circle. Soft and nearly shadowless; suits a
                   surface whose curvature would vanish into one hard highlight.
      studio       Warm key against a cool fill, low contrast. Photographic.

    intensity: scales every light in the rig. 1 leaves it as designed.
    ambient: overrides the rig's ambient level, 0 to 2.
    exposure: overall exposure, 0 to 3. Left untouched when omitted.
    """
    args: dict[str, Any] = {"rig": rig}
    if intensity is not None:
        args["intensity"] = intensity
    if ambient is not None:
        args["ambient"] = ambient
    if exposure is not None:
        args["exposure"] = exposure
    return await _call("lighting", args)


@mcp.tool()
async def background(
    color: str | None = None,
    transparent: bool | None = None,
    gradient: str | None = None,
    gradient_from: str | None = None,
    gradient_to: str | None = None,
    image: str | None = None,
    skybox: str | None = None,
    blur: float | None = None,
) -> dict[str, Any]:
    """Set the canvas background: a flat colour, a gradient, or nothing at all.

    color: a literal hex value like "#ffffff". Unlike show(), a colour theme is
      meaningless here — a canvas has no atoms to take a theme from.
    transparent: render onto nothing, so a saved figure drops into a document
      or a slide without a coloured card behind it. This also switches the
      screenshot pipeline to transparent capture, which is a separate flag in
      Mol* and would otherwise keep returning opaque PNGs.
    gradient: "off", "horizontal" (top to bottom) or "radial" (centre to edge).
      A gradient sits in front of the flat colour rather than replacing it.
    gradient_from / gradient_to: the two stops — top and bottom for horizontal,
      centre and edge for radial. Both default to Mol*'s pale grey pair.
    image: a flat picture behind the scene — a local file path, or an http(s)
      URL. A local file is read and sent inline, so nothing has to still be
      reachable when the figure is captured.
    skybox: a directory holding the six cube faces, named nx, ny, nz, px, py
      and pz (.png, .jpg, .jpeg or .webp). Unlike a flat image this surrounds
      the scene, so it also shows in reflections off a metallic material.
    blur: softens the image or skybox, 0 to 1 — useful for pushing a
      photographic background behind the molecule.

    gradient, image and skybox are the same slot in Mol*, so at most one of
    them applies at a time. Returns the values read back off the canvas, not
    the ones passed in.
    """
    chosen = [
        name
        for name, value in (("gradient", gradient), ("image", image), ("skybox", skybox))
        if value is not None
    ]
    if len(chosen) > 1:
        raise ViewerError(
            f"Pass at most one of gradient, image or skybox — got {', '.join(chosen)}. "
            "They are the same background slot in Mol*."
        )
    if color is None and transparent is None and not chosen:
        if gradient_from is not None or gradient_to is not None:
            raise ViewerError(
                "Pass gradient= as well, to say which gradient the colours are for"
            )
        raise ViewerError(
            "Pass at least one of color, transparent, gradient, image or skybox"
        )
    args: dict[str, Any] = {}
    for key, value in (
        ("color", color),
        ("transparent", transparent),
        ("gradient", gradient),
        ("gradient_from", gradient_from),
        ("gradient_to", gradient_to),
        ("blur", blur),
    ):
        if value is not None:
            args[key] = value
    # Read and encoded here rather than in the viewer, which has no filesystem.
    if image is not None:
        args["image"] = _background_image_uri(image)
    if skybox is not None:
        args["skybox"] = _skybox_faces(skybox)
    return await _call("background", args)


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
    reported = await _call("capabilities", {})
    # Presets are composed here rather than in the viewer, so the viewer cannot
    # report them. They belong in the same answer as everything else a caller
    # can choose from.
    reported["presets"] = sorted(_PRESETS)
    # Whether a movie can actually be written, rather than finding out at
    # the end of a long capture.
    reported["ffmpeg"] = _ffmpeg_binary() is not None
    return reported


# -- presets -------------------------------------------------------------------


async def _preset_publication_cartoon(target: str) -> list[str]:
    """A clean figure: white ground, soft directional light, crevices readable."""
    await background(color="#ffffff", gradient="off")
    await lighting(rig="three-point")
    await effects(occlusion=True, outline=False, bloom=False, depth_of_field=False)
    await shading(style="normal", name=target)
    await material(finish="matte", name=target)
    return [
        'background(color="#ffffff")',
        'lighting(rig="three-point")',
        "effects(occlusion=True, outline=False, bloom=False)",
        f'shading(style="normal", name="{target}")',
        f'material(finish="matte", name="{target}")',
    ]


async def _preset_illustrative(target: str) -> list[str]:
    """The textbook look: flat banded colour with a drawn edge."""
    await background(color="#ffffff", gradient="off")
    await lighting(rig="flat")
    await effects(outline=True, outline_color="#000000", occlusion=False, bloom=False)
    await shading(style="cel", name=target, cel_steps=4)
    return [
        'background(color="#ffffff")',
        'lighting(rig="flat")',
        'effects(outline=True, outline_color="#000000", occlusion=False)',
        f'shading(style="cel", cel_steps=4, name="{target}")',
    ]


async def _preset_ghost_surface(target: str) -> list[str]:
    """A see-through surface that leaves what is inside it visible.

    The scoping is the point. A surface shown under the *same* handle would
    replace whatever that handle was already drawing — the component is rebuilt,
    not layered — so the cartoon you wanted to see inside the ghost would
    silently disappear. The surface gets its own handle over the same atoms.
    """
    array = _require_structure()
    if target == _WHOLE_SCENE:
        indices = np.arange(array.array_length())
        origin = "preset(ghost-surface) over everything"
    else:
        try:
            indices = _handles.get(target).indices
        except HandleError as exc:
            raise ViewerError(str(exc)) from exc
        origin = f"preset(ghost-surface) over {target}"

    ghost = f"{target}_ghost"
    _register(ghost, indices, origin)
    await show(representation="molecular-surface", handle=ghost, opacity=0.25)
    await shading(style="xray", name=ghost)
    await material(finish="glossy", name=ghost)
    return [
        f'show(representation="molecular-surface", handle="{ghost}", opacity=0.25)',
        f'shading(style="xray", name="{ghost}")',
        f'material(finish="glossy", name="{ghost}")',
    ]


async def _preset_active_site(target: str) -> list[str]:
    """Sticks and labels on the site, the rest faded back out of the way."""
    if target == _WHOLE_SCENE:
        raise ViewerError(
            "active-site needs a handle saying which site — from select(), "
            "interface(), or near()"
        )
    await opacity(0.2, name=_WHOLE_SCENE)
    await show(representation="ball-and-stick", handle=target, color="element-symbol")
    await label(name=target, level="residue")
    await focus(name=target)
    await lighting(rig="studio")
    await effects(occlusion=True, outline=False)
    return [
        f'opacity(0.2, name="{_WHOLE_SCENE}")',
        f'show(representation="ball-and-stick", handle="{target}")',
        f'label(name="{target}")',
        f'focus(name="{target}")',
        'lighting(rig="studio")',
    ]


_PRESETS = {
    "publication-cartoon": _preset_publication_cartoon,
    "illustrative": _preset_illustrative,
    "ghost-surface": _preset_ghost_surface,
    "active-site": _preset_active_site,
}


@mcp.tool()
async def preset(name: str, handle: str | None = None) -> dict[str, Any]:
    """Apply a named recipe: lighting, effects, shading and materials at once.

    A preset is a composition of the other display tools, so nothing here is
    reachable only through it — the reply lists the calls it made, and any of
    them can be adjusted afterwards or run by hand instead.

    name: one of — capabilities() reports the live list.

      publication-cartoon  White ground, three-point light, ambient occlusion
                           on. The default figure.
      illustrative         Flat cel shading with a black outline. The textbook
                           look; pairs well with a simple cartoon.
      ghost-surface        A see-through surface over the selection, leaving
                           whatever is inside it visible. Drawn under its own
                           handle so it layers over the existing representation
                           rather than replacing it.
      active-site          Ball-and-stick and residue labels on the given site,
                           the rest of the structure faded back. Needs a handle.

    handle: which selection the per-selection parts apply to. Omitted means the
      whole scene, which active-site refuses since a site has to be named.
    """
    recipe = _PRESETS.get(name)
    if recipe is None:
        raise ViewerError(
            f"Unknown preset {name!r}. Available: {', '.join(sorted(_PRESETS))}"
        )
    _require_viewer()
    steps = await recipe(handle or _WHOLE_SCENE)
    return {"preset": name, "applied_to": handle or _WHOLE_SCENE, "steps": steps}


SESSION_FORMAT = "protean-session"
SESSION_VERSION = 1

# A session is mostly embedded mmCIF, which compresses about 5x, so a real one
# is a few MB. The bound is on the *decompressed* size because that is what a
# crafted file controls: 9 kB of gzip expands to 2 GB at a ratio a text
# document reaches easily, and load_session decompresses before it can check
# anything about the contents.
_MAX_SESSION_BYTES = 512 * 1024 * 1024

# The only URL protean itself puts in a session: a relative path to this
# bridge's own volume route, which is how a volume reaches the viewer.
# `/volumes/{handle}` is a dict lookup in connection.py, not a filesystem path,
# so allowing this prefix cannot be turned into a read.
#
# `\Z` rather than `$`, and no whitespace in the handle: `$` matches before a
# trailing newline and `[^/]` admits one, so "/volumes/x\n" would have passed
# as this bridge's own route. The emitter percent-encodes handles, so a real
# one never carries whitespace.
_SESSION_LOCAL_URL = re.compile(r"^/volumes/[^/\s]+\Z")

# Anything naming a location to fetch: a scheme, a protocol-relative //host, or
# a leading /, which the browser resolves against the viewer's own origin — so
# `/volumes/../../x` is a request to this bridge that is not the volumes route.
# Measured: the only /-leading strings in real sessions are the volume URLs.
_SESSION_URL_LIKE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?/")

# Mol* serialises its *own* defaults for the custom-property providers the
# prebuilt bundle registers, so a legitimate session carries these three third-
# party URLs without protean ever asking for anything. They are allowed by
# exact value only: the same key holding a different host is a session telling
# the viewer to fetch from someone else, and those providers do fetch.
# Measured from real sessions — re-measure when the molstar pin moves, and
# expect the failure to be a loud refusal naming the URL.
_SESSION_DEFAULT_URLS = frozenset(
    {
        "https://www.ebi.ac.uk/pdbe/api/validation/residuewise_outlier_summary/entry/",
        "https://files.rcsb.org/pub/pdb/validation_reports",
        "https://data.rcsb.org/graphql",
    }
)

# What protean's own save_session writes, measured by building a scene with
# every state-adding tool (structures, representations, labels, measurements,
# presets, superposition, volumes, isosurfaces) and taking the union.
#
# This is an allowlist rather than a hunt for fetching params, because the
# fetching ones cannot be enumerated reliably: `create-volume-streaming-info`
# fetches from `serverUrl`, which is a PD.Text and so invisible to a PD.Url
# grep, and it would fetch from Mol*'s public default even with no URL in the
# file at all. A name protean never writes has no business in a protean
# session.
_SESSION_TRANSFORMERS = frozenset(
    {
        "build-in.root",
        "ms-plugin.create-group",
        "ms-plugin.custom-model-properties",
        "ms-plugin.custom-structure-properties",
        "ms-plugin.download",
        "ms-plugin.model-from-trajectory",
        "ms-plugin.model-unitcell-3d",
        "ms-plugin.raw-data",
        "ms-plugin.structure-component",
        "ms-plugin.structure-from-model",
        "ms-plugin.structure-multi-selection-from-bundle",
        "ms-plugin.structure-representation-3d",
        "ms-plugin.structure-selections-angle-3d",
        "ms-plugin.structure-selections-dihedral-3d",
        "ms-plugin.structure-selections-distance-3d",
        "ms-plugin.volume-representation-3d",
        # trajectory-from-mmcif is covered by the decoder pattern below, along
        # with trajectory-from-pdb; naming one of the pair here would read as
        # if it were the only one allowed.
    }
)

# The decoder families, allowed by pattern because which one appears depends on
# the format the caller loaded rather than on anything protean chooses: a .pdb
# file gives `trajectory-from-pdb` where an mmCIF gives `trajectory-from-mmcif`,
# and each volume format has its own pair. Naming them one by one is how this
# check came to refuse a session protean had just written — the census behind
# the list above used structures fetched from RCSB, which arrive as mmCIF, so
# no PDB file was ever in it.
#
# Safe to admit as families, on a claim narrower than it is tempting to write:
# **no `parse-*`, `volume-from-*` or `trajectory-from-*` transform fetches** —
# each consumes the object its parent produced. That is not the same as saying
# their files do not fetch, and the difference matters when re-measuring:
# transforms/model.js *does* fetch, in `custom-model-properties` and
# `custom-structure-properties` (it hands `assetManager` to the property
# providers at model.js:1150 and :1206), which is exactly what reaches the
# three URLs pinned by value above. Those two are allowlisted by name, not by
# this pattern. In transforms/data.js the fetching transforms are Download —
# named above, and its URL checked — and DownloadBlob, which is in neither
# list and so is refused outright.
#
# `\Z`, not `$`: `$` also matches before a trailing newline, so
# "ms-plugin.parse-cif\n" would be admitted as a known decoder and reach the
# viewer to fail there as a raw Mol* error, rather than being refused here by
# name. `_SESSION_LOCAL_URL` above is anchored the same way and for the same
# reason.
_SESSION_DECODERS = re.compile(
    r"^ms-plugin\.(?:parse|volume-from|trajectory-from)-[a-z0-9-]+\Z"
)


def _session_path(path: str) -> Path:
    out = Path(path).expanduser()
    return out if out.suffix else out.with_suffix(".protean")


def _decompress_session(src: Path) -> bytes:
    """Gunzip *src*, refusing to expand past _MAX_SESSION_BYTES.

    gzip.decompress() will happily allocate whatever the file decodes to, and
    the file is chosen by whoever wrote the session rather than by the user
    opening it.
    """
    with gzip.open(src, "rb") as handle:
        raw = handle.read(_MAX_SESSION_BYTES + 1)
    if len(raw) > _MAX_SESSION_BYTES:
        raise ViewerError(
            f"{src} decompresses to more than "
            f"{_MAX_SESSION_BYTES // 1024 // 1024} MB, which no scene this "
            "viewer can hold produces. Refusing to read it."
        )
    return raw


def _remote_references(node: Any, path: str = "snapshot") -> list[str]:
    """Every string in *node* that names somewhere the viewer would fetch from.

    A protean session embeds its own data — that is the point of the format —
    so the only URL protean writes is the relative /volumes path above, plus
    the third-party defaults Mol* serialises for its own custom-property
    providers. Anything else means the file is telling the viewer to fetch from
    a host of its author's choosing, and Mol* will do it: the state tree is
    applied as given, so whoever wrote the file decides what ends up on screen
    while load_session still reports success.

    **Every string, not every string under a `url` key.** The key names cannot
    be enumerated: `serverUrl` on `create-volume-streaming-info` is a PD.Text,
    so a grep for PD.Url does not find it, and a URL sitting inside a list has
    no key at all. Both were live bypasses of the first version of this check.

    Strings under a `data` key are skipped: that is embedded file content —
    an mmCIF cites `http://mmcif.pdb.org/...` in its own header — and it is
    parsed, never fetched.

    Returns the offending locations rather than raising, so the refusal can
    name all of them at once.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "data" and isinstance(value, str):
                continue
            found.extend(_remote_references(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_remote_references(value, f"{path}[{index}]"))
    elif (
        isinstance(node, str)
        and _SESSION_URL_LIKE.search(node)
        and not _SESSION_LOCAL_URL.match(node)
        and node not in _SESSION_DEFAULT_URLS
    ):
        found.append(f"{path} = {node[:120]}")
    return found


def _session_transforms(snapshot: Any) -> list[Any]:
    """The state tree's transform list, or [] if the shape is not what we expect."""
    tree = snapshot.get("data", {}) if isinstance(snapshot, dict) else {}
    transforms = (
        tree.get("tree", {}).get("transforms", []) if isinstance(tree, dict) else []
    )
    return transforms if isinstance(transforms, list) else []


def _embedded_structure(snapshot: Any) -> tuple[str, str] | None:
    """The structure text a session carries, and the format it is in.

    A session embeds its structure rather than referencing it — that is the
    whole design of the format — so the analysis half can be rebuilt from the
    file without refetching anything, and without the network being involved in
    a restore.

    There is exactly one such node even after a superpose, which sends the
    combined structure as a single blob, so there is nothing to guess about
    which molecule the analysis subject is. The format follows the trajectory
    transform Mol* used: `dispatch.ts` collapses everything that is not 'pdb'
    to 'mmcif', and the tree records which one it picked.
    """
    transforms = _session_transforms(snapshot)
    text: str | None = None
    fmt = "mmcif"
    for transform in transforms:
        if not isinstance(transform, dict):
            continue
        if transform.get("transformer") == "ms-plugin.trajectory-from-pdb":
            fmt = "pdb"
        if transform.get("transformer") != "ms-plugin.raw-data":
            continue
        params = transform.get("params")
        data = params.get("data") if isinstance(params, dict) else None
        # A volume travels by URL, so a raw-data node holding bytes rather than
        # text is not the structure.
        if isinstance(data, str) and text is None:
            text = data
    return None if text is None else (text, fmt)


def _unknown_transformers(snapshot: Any) -> list[str]:
    """Transformer names in *snapshot* that save_session never writes.

    The second half of the check, and the half that does not depend on
    spotting a URL: `create-volume-streaming-info` fetches from Mol*'s own
    public default when the file names no URL at all, so a session carrying it
    reaches the network with nothing for _remote_references() to find.
    """
    unknown = []
    for transform in _session_transforms(snapshot):
        if not isinstance(transform, dict):
            continue
        name = transform.get("transformer")
        if not isinstance(name, str):
            continue
        if name in _SESSION_TRANSFORMERS or _SESSION_DECODERS.match(name):
            continue
        unknown.append(name)
    return sorted(set(unknown))


@mcp.tool()
async def save_session(path: str, overwrite: bool = False) -> dict[str, Any]:
    """Save the whole scene to a .protean file.

    The file embeds the structure data along with representations, colours,
    camera and the named selection handles, so reopening it reproduces the
    scene exactly without refetching anything.
    """
    payload = await _call("save_session")
    out = _writable(_session_path(path), (".protean",), overwrite=overwrite)
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

    A session file is untrusted input — it is a file, and files get shared —
    so one that reaches outside itself is refused rather than restored. See
    _remote_references().
    """
    src = _session_path(path)
    if not src.is_file():
        raise ViewerError(f"No session file at {src}")
    try:
        document = json.loads(_decompress_session(src))
    except (OSError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
        raise ViewerError(f"{src} is not a readable protean session: {exc}") from exc
    except RecursionError as exc:
        # 155 bytes of nested brackets reach this, and an unhandled
        # RecursionError is a stack trace where a refusal belongs.
        raise ViewerError(f"{src} is nested too deeply to be a session") from exc
    if not isinstance(document, dict):
        # Valid JSON is not a session: `[1, 2, 3]` reached .get() and raised a
        # bare AttributeError, which is a stack trace where a refusal belongs.
        raise ViewerError(
            f"{src} is not a protean session (it holds a "
            f"{type(document).__name__}, not an object)"
        )
    if document.get("format") != SESSION_FORMAT:
        raise ViewerError(
            f"{src} is not a protean session (format={document.get('format')!r})"
        )
    if document.get("version") != SESSION_VERSION:
        raise ViewerError(
            f"{src} is session version {document.get('version')!r}; "
            f"this build reads version {SESSION_VERSION}"
        )
    snapshot = document.get("molstar")
    if snapshot is None:
        # Truncated or hand-built: the format and version said session, and the
        # scene is simply absent.
        raise ViewerError(f"{src} carries no scene to restore")
    try:
        remote = _remote_references(snapshot)
    except RecursionError as exc:
        # A document that parsed can still out-nest this walk, and a guard that
        # cannot finish must refuse rather than fall through to the viewer.
        raise ViewerError(f"{src} is nested too deeply to check") from exc
    if remote:
        raise ViewerError(
            f"{src} tells the viewer to fetch from somewhere else, so it was "
            "not written by save_session() and is refused: " + "; ".join(remote[:5])
        )
    unknown = _unknown_transformers(snapshot)
    if unknown:
        raise ViewerError(
            f"{src} holds state save_session() never writes, so it was not "
            "written by protean and is refused: " + ", ".join(unknown[:5])
        )
    global _structure, _structure_error, _structure_identifier  # noqa: PLW0603 - session state
    result = await _call(
        "load_session",
        {"snapshot": snapshot, "handles": document.get("handles", {})},
    )
    # Both halves, or neither. The viewer now holds the session's molecule; if
    # the analysis kept the one loaded before, every measurement afterwards
    # would describe a different structure from the picture and say nothing
    # about it — measured at viewer 100 atoms against 660 here, with the
    # identifier still naming the old molecule.
    discarded = _discard_session_state()
    _structure, _structure_error, _structure_identifier = None, None, None
    analysis, agreement = _restore_analysis(snapshot, result.get("atom_count"))
    _structure_identifier = str(src) if _structure is not None else None
    return {
        "path": str(src),
        "created": document.get("created"),
        **result,
        "analysis": analysis + discarded,
        **agreement,
    }


def _restore_analysis(snapshot: Any, viewer_atoms: Any) -> tuple[str, dict[str, Any]]:
    """Rebuild the analysis structure from the session's own embedded copy.

    Sets `_structure` and returns what to tell the caller. The session carries
    the structure text, so this needs no network and cannot disagree with the
    viewer about *which file* — only about how it was built.

    **The viewer's own atom count decides how to build it**, rather than a
    default chosen here. A session saved from `assembly="biological"` and one
    saved from `assembly="asymmetric"` embed the same deposited text and differ
    only in what Mol\\* assembled from it, and nothing in the file records which
    was chosen. On 1HHO the two readings are 4792 and 2396, so guessing would
    be wrong half the time and silently: the count that matches the viewer is
    the one that describes the picture.

    If neither matches, the analysis is left empty. A structure that disagrees
    with the viewer is the failure this whole change exists to remove, and
    keeping it with a caveat attached would be that failure with a note on it.
    """
    global _structure, _structure_error  # noqa: PLW0603 - session state
    embedded = _embedded_structure(snapshot)
    if embedded is None:
        return (
            "unavailable — this session carries no structure to analyse, so "
            "selections and measurements need a fetch_structure first",
            {},
        )
    text, fmt = embedded
    readings: dict[str, int] = {}
    failures: dict[str, str] = {}
    for assembly in ("biological", "asymmetric"):
        try:
            loaded = _load_structure(text, fmt, assembly)
        except SelectionError as exc:
            # Only one reading needs to work: a structure carrying no assembly
            # records cannot be built as one, and its asymmetric reading is the
            # whole molecule anyway. A failure here is not the answer until
            # both have failed.
            failures[assembly] = str(exc)
            continue
        atoms = int(loaded.array.array_length())
        readings[assembly] = atoms
        if not isinstance(viewer_atoms, int) or atoms == viewer_atoms:
            _structure, _structure_error = loaded.array, None
            return (
                f"restored from the session's own copy ({atoms} atoms, "
                f"{assembly} assembly)",
                {"analysis_atoms": atoms, "agrees_with_viewer": atoms == viewer_atoms},
            )
    if not readings:
        _structure_error = "; ".join(f"{a}: {m}" for a, m in failures.items())
        return (
            "unavailable — the session's structure could not be parsed for "
            f"analysis ({_structure_error}); the viewer is unaffected",
            {},
        )
    return (
        "unavailable — the session's structure reads as "
        + " and ".join(f"{n} atoms {a}" for a, n in readings.items())
        + f", neither of which is the {viewer_atoms} the viewer is showing. "
        "Rather than analyse a molecule that is not the one on screen, "
        "analysis is left empty",
        {"analysis_atoms": None, "agrees_with_viewer": False},
    )


@mcp.tool()
async def superpose(
    mobile: str,
    target: str,
    mobile_chain: str | None = None,
    target_chain: str | None = None,
    mode: str = "sequence",
    show: bool = True,
    mobile_suffix: str = "_2",
) -> dict[str, Any]:
    """Superpose one structure onto another and report how well it fits.

    mobile, target: PDB IDs, UniProt accessions or local files, as fetch_structure takes.
    mobile_chain, target_chain: restrict to one chain each; otherwise all
      protein chains are used, which requires them to correspond in order.
      Naming a chain matters more than it looks: superposing two multi-chain
      structures asks a single rigid transform to satisfy every chain at once,
      which none can do once the chains have moved relative to each other, and
      the honest answer is then a large RMSD.
    mode: how residues are put into correspondence, which is the whole question
      — the fitting itself is settled maths.
      "sequence" (default) aligns the two sequences and superposes the residues
      that align, discarding outliers. Right whenever the two are the same
      protein.
      "structural" ignores the sequence and matches residues by the shape of
      their local backbone, so it finds a common substructure between proteins
      too diverged for a sequence alignment to mean anything. Slower and more
      permissive: it maximises how much it superposes, so expect more residues
      at a worse RMSD.

    Returns the RMSD, how many residues were aligned, the sequence identity
    over those residues, the 4x4 transform, and the worst-fitting residues — an
    RMSD alone hides whether the disagreement is spread out or concentrated in
    one loop.

    show: load the superposed pair into the viewer as one structure, with the
      mobile coordinates already moved into the target's frame. This replaces
      whatever was loaded, and selections afterwards address both halves.
    mobile_suffix: appended to a mobile chain id that the target already uses,
      since the pair share one chain namespace once combined.
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
            mode=mode,
        )
    except SuperpositionError as exc:
        raise ViewerError(str(exc)) from exc

    payload = {"mobile": mobile, "target": target, **result.as_dict()}
    if not show:
        return payload
    return {
        **payload,
        **await _display_superposition(
            first, second, mobile, target, result, mobile_suffix
        ),
    }


async def _display_superposition(
    mobile_data: Any,
    target_data: Any,
    mobile: str,
    target: str,
    result: Any,
    suffix: str,
) -> dict[str, Any]:
    """Load the pair, with the mobile structure moved into the target's frame.

    The transform is applied to the coordinates here rather than sent to the
    viewer as a matrix, and the two are loaded as a single structure. That is
    what lets everything else keep working: handles, selections and every
    analysis tool address one structure, and a superposition that arrived as
    two would have needed all of them to grow a structure argument.

    The cost is that the pair share one namespace, so a mobile chain whose id
    the target also uses is renamed. Which chains moved is reported, because a
    selection written against the original name would otherwise pick up the
    wrong molecule.
    """
    # One state each, and now by choice rather than by necessity. This used to
    # say the writer blanks `label_alt_id` so every conformer would arrive
    # indistinguishable; `_structure_as_mmcif` writes that column now. What
    # remains is the argument that outlasts the workaround: a superposition is
    # a statement about one conformation of each side, and the transform was
    # fitted over exactly these atoms.
    moved, _ = _resolve_conformers(
        _load_structure(mobile_data.data, mobile_data.format, "asymmetric").array
    )
    fixed, _ = _resolve_conformers(
        _load_structure(target_data.data, target_data.format, "asymmetric").array
    )

    matrix = np.asarray(result.transform, dtype=float)
    if matrix.shape != (_TRANSFORM_SIZE, _TRANSFORM_SIZE):
        raise ViewerError(f"Expected a 4x4 transform, got {matrix.shape}")
    # x' = xR^T + t, the convention biotite's superimpose_homologs returns.
    moved.coord = moved.coord @ matrix[:3, :3].T + matrix[:3, 3]

    taken = {str(c) for c in fixed.chain_id}
    renamed: dict[str, str] = {}
    relabelled = []
    for chain in moved.chain_id:
        name = str(chain)
        if name in taken:
            renamed.setdefault(name, f"{name}{suffix}")
            relabelled.append(renamed[name])
        else:
            relabelled.append(name)
    moved.set_annotation("chain_id", np.asarray(relabelled))

    combined = fixed + moved
    _renumber_for_viewer(combined)

    global _structure, _structure_error, _structure_identifier  # noqa: PLW0603 - session state
    # Superposing replaces the loaded structure with the pair, so it ends the
    # previous session exactly as a fetch does -- the trajectory's frames are
    # not this molecule, and a saved camera was framed on something else.
    discarded = _discard_session_state()
    _structure, _structure_error = combined, None
    _structure_identifier = f"{target}+{mobile}"

    viewer = await _send_structure(combined, f"{target}+{mobile}")
    ours = int(combined.array_length())
    theirs = viewer.get("atom_count")
    return {
        "displayed": True,
        "structure": _structure_identifier,
        "target_chains_shown": sorted(taken),
        "mobile_chains_shown": sorted({str(c) for c in moved.chain_id}),
        "renamed_chains": renamed,
        "atoms": ours,
        "viewer_atom_count": theirs,
        "agree": theirs is None or int(theirs) == ours,
        "note": (
            "The loaded structure is now the superposed pair, so selections and "
            "analysis address both. The mobile copy is in the target's frame; "
            "its coordinates were transformed, not just displayed shifted."
            + (f" Renamed to avoid collisions: {renamed}." if renamed else "")
            + discarded
        ),
    }


@mcp.tool()
async def interface(
    chain_a: str,
    chain_b: str,
    identifier: str | None = None,
    contact_limit: int = 200,
    name_a: str = "iface_a",
    name_b: str = "iface_b",
    copy: int | None = None,
) -> dict[str, Any]:
    """Describe the interface between two chains: buried area and contacts.

    identifier: defaults to the loaded structure, which is the usual case.
      Naming a different structure runs the analysis standalone, with no
      viewer and no handles.
    copy: which symmetry copy to describe, numbered from 0 as `sym N` numbers
      them. Only meaningful on a biological assembly.
    name_a, name_b: handles registered for the interface residues of each side,
      so the result can be shown or coloured without re-encoding it as a
      selection string. Pass them to show(), color(), combine() or near().

    Returns the buried surface area (total and per side), the interface
    residues with how much each buries, and the contacts classified as salt
    bridges, hydrogen bonds or polar contacts.

    On a biological assembly the copies share chain ids, so "chain A" names
    one chain in every copy and an A-B interface is several interfaces at
    once — haemoglobin reports 2765.9 A^2 where one alpha-beta pair buries
    873.9. With no `copy` the answer describes the whole structure and carries
    a `per_copy` breakdown; the total is more than their sum, because chain A
    of one copy also touches chain B of another.

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
        result = _interface(
            array, chain_a, chain_b, contact_limit=contact_limit, copy=copy
        )
    except (ContactError, SuperpositionError) as exc:
        raise ViewerError(str(exc)) from exc

    payload: dict[str, Any] = {"identifier": label, **result.as_dict()}
    if on_loaded:
        call = f"interface({chain_a}, {chain_b})" + (
            f" copy {copy}" if copy is not None else ""
        )
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
    overwrite: bool = False,
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
    path: where to write the OpenDX grid; defaults to the protean cache. This
      is an *output*, which is easy to misread from the name — pointed at an
      existing file it used to overwrite it without a word, and did, over a
      file named secret.key during the security pass.
    overwrite: replace the file at `path` even when it is not an OpenDX grid.

    On the Coulombic field: it assumes one uniform dielectric, so it has no
    protein interior and no reaction field at the solvent boundary. Measured
    against APBS on ubiquitin it tracks surface potential closely in shape
    (r = 0.96, 94% sign agreement) while running about 1.6x low in magnitude.
    Read it for where the charge is, never for an energy.
    """
    # One conformer state before any geometry. biotite's PDB writer blanks the
    # altLoc column, so pdb2pqr would receive two rows for one atom with no way
    # to tell them apart and would silently keep whichever came first -- a
    # potential map computed over atoms sitting on top of each other, with
    # nothing in the reply to say so.
    array, conformer = _resolve_conformers(_require_structure())
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
    out = _writable(out, (".dx",), overwrite=overwrite)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_write_dx(grid))

    payload: dict[str, Any] = {
        **grid.as_dict(),
        "charges": prepared.as_dict(),
        "dx_path": str(out),
        "apbs_available": binary is not None,
    }
    if conformer:
        # Which atoms this map describes. Alternate conformers never coexist,
        # so a potential computed over both would be of no molecule -- and the
        # picture shows all of them.
        payload["conformer"] = conformer
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
    # Scoring reads one conformer state; the handles it registers are indices
    # into the *full* array, because that is what `_register` and `_display`
    # resolve them against. Keeping the origin of every atom kept is the only
    # thing that connects the two — the same `origin_index` `contacts.py`
    # carries, for the same reason. Both come off one mask: computing the state
    # twice would be two enumerations assumed to agree.
    full = _require_structure()
    state = _conformer_state(full)
    array = full[state]
    origin_index = np.flatnonzero(state)
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

    # The alignment fetch above is a network call the docstring describes as
    # "tens of seconds to minutes", and nothing in this server holds a lock.
    # `fetch_structure`, `superpose` and `load_trajectory` all rebind
    # `_structure`, so a call landing during that wait would leave
    # `origin_index` mapping into the array this started with while `_register`
    # summarises against the new one — a handle naming arbitrary atoms, with a
    # plausible count. Refuse instead: the scores describe a molecule that is
    # no longer loaded.
    if _require_structure() is not full:
        raise ViewerError(
            "The loaded structure changed while the alignment was being "
            "fetched, so these scores describe a molecule that is no longer "
            "loaded. Nothing was registered — call conservation() again."
        )

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
        indices = origin_index[_residue_indices(array, keys)]
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
    mode: str = "gradient",
    representation: str | None = None,
    scale: str = "relative",
    bins: int = 7,
    palette: str = "conservation",
    prefix: str = "cons",
    hide_others: bool = True,
) -> dict[str, Any]:
    """Colour the structure by the conservation scores from conservation().

    mode:
      "gradient" (default) gives a continuous ramp on a cartoon, which is the
        usual conservation figure. Entropy is carried into the viewer in the
        B-factor column and drawn with Mol*'s uncertainty theme — the one
        per-atom numeric field it will colour by. That means re-sending the
        structure, so it is a heavier call than it looks; `reloaded` says so.
      "bands" splits the entropy range into `bins` discrete handles, each drawn
        separately. No reload, and every band stays addressable — combine it
        with an interface, hide it, measure it.

    representation: defaults to "cartoon" for gradient and "molecular-surface"
      for bands. Do not draw *bands* as a cartoon: a ribbon needs consecutive
      backbone and bands are scattered single residues, so most of the
      structure will not draw at all. The gradient has no such problem, because
      one representation covers the whole chain.
    scale: "relative" stretches the ramp across this protein's own entropy
      range, which is what makes a conserved core visible; "absolute" uses the
      full 0-1 entropy range so two proteins can be compared.
    hide_others: hide existing representations first, so the result is not
      sitting underneath a uniformly coloured copy of the same atoms.

    Blue is conserved and red is variable in both modes.
    """
    if mode not in ("gradient", "bands"):
        raise ViewerError(f"Unknown mode {mode!r} (gradient, bands)")
    array = _require_structure()
    if not _conservation_scores:
        raise ViewerError("No conservation scores yet — call conservation() first.")
    key = chain or next(iter(_conservation_scores))
    if key not in _conservation_scores:
        known = ", ".join(sorted(_conservation_scores))
        raise ViewerError(f"No conservation for chain {key!r}. Scored: {known}")
    scores = _conservation_scores[key]
    if mode == "gradient":
        return await _conservation_gradient(
            array, key, scores, representation or "cartoon", scale, hide_others
        )

    if bins < _MIN_BINS:
        raise ViewerError(f"bins must be at least {_MIN_BINS}")
    representation = representation or "molecular-surface"

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


async def _conservation_gradient(
    array: Any,
    chain: str,
    scores: list[Any],
    representation: str,
    scale: str,
    hide_others: bool,
) -> dict[str, Any]:
    r"""Carry entropy into the viewer's B-factor column and colour by it.

    Mol\*'s uncertainty theme is the only per-atom numeric field it will ramp
    over, and a model's B-factors cannot be edited in place — so the structure
    is re-sent with entropy written into that column. The analysis copy keeps
    its crystallographic B values; only the viewer's copy is overwritten, and
    the reply says so, because a B-factor that silently means something else is
    exactly the kind of thing that gets read as temperature later.

    The theme's own scale is reversed, so low values come out blue. Entropy is
    written rather than conservation for that reason: conserved is low entropy,
    and therefore blue, matching the banded palette.
    """
    if scale not in ("relative", "absolute"):
        raise ViewerError(f"Unknown scale {scale!r} (relative, absolute)")

    entropies = np.array([s.entropy for s in scores])
    lowest, highest = float(entropies.min()), float(entropies.max())
    span = highest - lowest

    # The uncertainty theme's domain is [0, 100], so the values have to arrive
    # already stretched into it; the theme takes no domain from us here.
    if scale == "relative" and span > 0:
        scaled = (entropies - lowest) / span * _B_FACTOR_FULL
    else:
        scaled = entropies * _B_FACTOR_FULL

    by_residue = {
        (s.chain, s.seq, s.ins_code): float(v)
        for s, v in zip(scores, scaled, strict=True)
    }
    # Renumber before the copy so the analysis array and the file the viewer
    # parses agree on which atom is which.
    _renumber_for_viewer(array)
    display = array.copy()
    # Keyed without the symmetry copy, so a score computed once for a chain
    # lands on every copy of it — conservation is a property of the sequence,
    # not of which copy you happen to be looking at.
    labels = np.char.add(
        np.char.add(array.chain_id.astype(str), "|"),
        np.char.add(array.res_id.astype(str), array.ins_code.astype(str)),
    ).tolist()
    lookup = {f"{c}|{s}{i}": v for (c, s, i), v in by_residue.items()}
    # Residues with no score — other chains, ligands, waters — are pinned to the
    # variable end rather than left at their crystallographic B, which would
    # otherwise be read as a conservation value they never had.
    values = np.array(
        [lookup.get(label, _B_FACTOR_FULL) for label in labels], dtype=float
    )
    display.b_factor = values

    scored = sum(1 for label in labels if label in lookup)
    result = await _send_structure(display, f"conservation:{chain}")
    if hide_others:
        with contextlib.suppress(ViewerError):
            await _call("hide", {"name": "auto"})

    # Handles survive the reload because they are Python-side atom indices and
    # the atom order is untouched; their components do not, so redraw them.
    restored = 0
    for name in _handles.names():
        await _display(name, _handles.get(name).indices)
        restored += 1

    name = f"{chain}_conservation"
    _register(name, np.arange(array.array_length()), f"conservation gradient ({chain})")
    await _call(
        "show",
        {
            "name": name,
            "expression": _indices_to_molscript(array, np.arange(array.array_length())),
            "representation": representation,
            "color": "uncertainty",
            "limit": 0,
        },
    )
    return {
        "chain": chain,
        "mode": "gradient",
        "handle": name,
        "representation": representation,
        "scale": scale,
        "entropy_range": [round(lowest, 3), round(highest, 3)],
        "residues_scored": scored,
        "reloaded": True,
        "viewer_atom_count": result.get("atom_count"),
        "handles_redisplayed": restored,
        "note": (
            "The viewer's B-factor column now carries conservation entropy "
            "scaled to 0-100, not crystallographic B. Blue is conserved. The "
            "analysis copy is unchanged, so selections on `b` still mean "
            "temperature factor."
        ),
    }


def _renumber_for_viewer(array: Any) -> None:
    """Give the array the atom ids the viewer will end up parsing.

    biotite's mmCIF writer numbers atom_site.id sequentially from 1 and
    discards whatever the array carried. Handles reach the viewer as atom.id
    ranges, so leaving the analysis copy on its original ids means every handle
    names different atoms than intended — silently, because the counts still
    agree. A biological assembly makes it certain rather than likely: its ids
    are duplicated across symmetry copies, and the written file's are not.
    """
    array.atom_id = np.arange(1, array.array_length() + 1)


async def _send_structure(array: Any, label: str) -> dict[str, Any]:
    """Load an array we built ourselves into the viewer.

    Always as the asymmetric unit: these coordinates already are whatever
    molecule was chosen, and biotite writes no assembly for the viewer to
    expand even if it were asked to.
    """
    return await _call(
        "load_structure",
        {
            "name": label,
            "format": "mmcif",
            "data": _structure_as_mmcif(array),
            "assembly": "asymmetric",
        },
    )


def _structure_as_mmcif(array: Any) -> str:
    """Serialise an AtomArray as mmCIF for the viewer.

    mmCIF rather than PDB because PDB cannot carry more than 99,999 atoms or a
    multi-character chain id. What biotite writes declares no assembly, so the
    viewer has nothing to expand even if it were asked to.

    **The altloc column is written back in.** biotite's writer emits
    `_atom_site.label_alt_id` as "." for every row regardless of what the array
    carries, so a structure sent this way arrived as overlapping atoms Mol\\*
    could not tell apart: template bonds inferred across conformer states,
    doubled spheres, and no `alt` selection possible in the picture — while
    `viewer_atom_count` still agreed, so nothing flagged it. The annotation
    already spells "no alternate" as ".", which is mmCIF's own spelling, so it
    is written through unchanged.
    """
    handle = CIFFile()
    set_structure(handle, array)
    if "altloc_id" in array.get_annotation_categories():
        site = handle.block["atom_site"]
        letters = np.asarray(array.get_annotation("altloc_id"))
        # A trajectory arrives as a stack, and biotite writes one row per atom
        # *per model* while the annotation is per atom. Tiling rather than
        # assuming one model: the first version of this assigned the per-atom
        # column straight onto a stack's rows and every trajectory and rmsf
        # render died in `write` with "Failed to serialize block", which the
        # fast suite could not see because both paths are gated.
        rows = len(site["label_alt_id"].as_array())
        models, remainder = divmod(rows, len(letters))
        if remainder:
            raise ViewerError(
                f"Cannot label conformers: the written file has {rows} atom rows, "
                f"which is not a whole number of copies of the array's "
                f"{len(letters)} atoms."
            )
        site["label_alt_id"] = np.tile(letters, models)
    buffer = io.StringIO()
    handle.write(buffer)
    return buffer.getvalue()


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


def _provenance(declared: str) -> Provenance:
    """Parse a declared provenance, refusing anything not in the vocabulary.

    Refused rather than coerced to UNKNOWN: a typo silently becoming "unknown"
    would turn a caller who *did* declare their map into one who appears not to
    have, which is the failure this parameter exists to prevent.
    """
    try:
        return Provenance(declared.strip().lower())
    except ValueError:
        known = ", ".join(p.value for p in Provenance)
        raise ViewerError(
            f"unknown provenance {declared!r}. Known: {known}. This is never "
            f"inferred from the filename, so an undeclared map is 'unknown'."
        ) from None


def _with_caveat(volume: dict[str, Any]) -> dict[str, Any]:
    """Attach the one-line warning that belongs beside a picture of this map.

    The viewer stores the provenance string and this derives the prose, so there
    is one source of truth rather than two copies drifting apart.
    """
    declared = volume.get("provenance")
    if isinstance(declared, str):
        # Suppressed rather than raised: the viewer only ever echoes a value
        # this module validated on the way in, so an unparseable one means the
        # reply shape changed. Losing the caveat line is the right failure
        # there; refusing to report a volume that loaded is not.
        with contextlib.suppress(ValueError):
            volume["caveat"] = Provenance(declared).caveat
    return volume


@mcp.tool()
async def load_volume(
    path: str,
    name: str | None = None,
    format: str = "auto",
    provenance: str = "unknown",
) -> dict[str, Any]:
    """Load a density map into the viewer and report what it actually holds.

    path: an MRC/CCP4 (.map/.mrc/.ccp4, optionally .gz), DSN6, OpenDX, Gaussian
      cube or BinaryCIF volume. EMDB ships .map.gz and that is handled.
    name: handle for `isosurface`, `volume_info` and `remove_volume`. Defaults
      to the file stem. Note `color_by_potential` still takes OpenDX text
      inline rather than a handle, so colouring a surface by a second map is
      not reachable from here yet.
    format: "auto" detects from the MRC magic and then the extension. Pass one
      of ccp4, dsn6, dx, cube, dscif to override.
    provenance: how this map came to exist — one of `measured`, `sharpened`,
      `nn_enhanced`, `generated`, `unknown`. **Never inferred.** A filename
      saying `deepemhancer` is not evidence, and guessing from one would make
      the label less trustworthy than no label at all, so an undeclared map
      stays `unknown`. The reply carries a `caveat` line to show alongside any
      picture of it.

    The reply's statistics — min, max, mean, sigma and voxels — are computed by
    walking the voxels the viewer parsed, not echoed from the request and not
    taken from the file header. They are the only way to convert a published
    absolute contour level into sigma, so they have to describe the data.

    `stated` carries the header's own four numbers alongside. For MRC/CCP4 those
    are stored fields, and they are not always true: a cropped or rescaled map
    keeps whatever header nobody updated. A large disagreement between the two
    is information — it says the file has been through something — which is why
    both are reported rather than one silently winning.

    Volumes travel over HTTP rather than inline in this call: a 400-cubed map
    is a quarter of a gigabyte and does not belong in a JSON message.
    """
    bridge = _require_viewer()
    declared = _provenance(provenance)
    try:
        volume = read_volume(path, format)
    except VolumeError as exc:
        raise ViewerError(str(exc)) from exc

    handle = name or Path(path).name.removesuffix(".gz").rsplit(".", 1)[0]
    url = bridge.publish_volume(handle, volume.data)
    try:
        result = await _call(
            "load_volume",
            {
                "name": handle,
                "url": url,
                "format": volume.format,
                "provenance": declared.value,
            },
        )
    except ViewerError:
        # Scoped to the handle this call created: never a sweep of the session.
        bridge.forget_volume(handle)
        raise
    result["source"] = str(volume.source)
    result["gzipped"] = volume.was_compressed
    return _with_caveat(result)


@mcp.tool()
async def volume_info(name: str) -> dict[str, Any]:
    """Report a loaded volume's dimensions and value statistics.

    Computed from the voxels in the viewer, not read from the file header, so it
    describes what is actually being drawn. The header's own claims come back
    under `stated`, for comparison rather than for use.

    The sigma here is what converts a published contour level into the units a
    viewer contours in. EMDB publishes author-recommended levels as ABSOLUTE
    map values while most viewers contour in sigma — EMD-30913 publishes 0.05,
    which is 3.16 sigma for that map, and used as sigma it contours noise.
    Taking that sigma from a stale header would put the contour in the wrong
    place while every call still returned cleanly.
    """
    _require_viewer()
    return _with_caveat(await _call("volume_info", {"name": name}))


@mcp.tool()
async def list_volumes() -> dict[str, Any]:
    """List the volumes currently loaded, with their statistics and provenance."""
    _require_viewer()
    result = await _call("list_volumes")
    for volume in result.get("volumes", []):
        _with_caveat(volume)
    return result


@mcp.tool()
async def isosurface(
    name: str,
    level: float,
    unit: str = "sigma",
    style: str = "surface",
    opacity: float | None = None,
) -> dict[str, Any]:
    """Contour a loaded volume at `level` and draw it.

    name: a handle from `load_volume`.
    level: the contour value.
    unit: **`sigma` or `absolute`, and it is not a detail.** EMDB publishes
      author-recommended levels as ABSOLUTE map values while most viewers
      contour in sigma. EMD-30913 publishes 0.05, which is 3.16 sigma for that
      map; typed in as sigma it contours noise and looks like an ordinary bad
      map rather than a unit error. Naming the unit is the only way that cannot
      happen, so there is no bare-number form of this call.
    style: `surface` (solid) or `mesh` (wireframe).
    opacity: 0-1. Omit it to keep whatever the surface already has, so raising
      a contour level on a surface you made translucent does not silently make
      it solid again.

    A sigma level is converted here, against the sigma and mean measured off
    the voxels, and Mol\\* is handed an absolute value. It would otherwise
    convert using the file header's own statistics, which for CCP4/MRC are
    stored fields and routinely stale — Mol\\*'s default isosurface is 2 sigma
    against exactly those. The reply reports `sigma` and `mean` used, and
    `stated_absolute`: what the header's numbers would have given for the same
    request. A large gap between that and `absolute` says the file disagrees
    with itself.

    The reply also carries the volume's `provenance` and `caveat`, because a
    contour makes a generated map look exactly as authoritative as a measured
    one.
    """
    _require_viewer()
    if unit not in ("sigma", "absolute"):
        raise ViewerError(f"unit must be 'sigma' or 'absolute', not {unit!r}")
    if style not in ("surface", "mesh"):
        raise ViewerError(f"style must be 'surface' or 'mesh', not {style!r}")
    if opacity is not None and not 0.0 <= opacity <= 1.0:
        raise ViewerError(f"opacity must be between 0 and 1, not {opacity}")
    args: dict[str, Any] = {
        "name": name,
        "level": level,
        "unit": unit,
        "style": style,
    }
    # Omitted rather than defaulted, so the viewer's "keep the current alpha"
    # branch is reachable. Sending 1.0 every time would make every level change
    # silently reset a translucent surface to solid.
    if opacity is not None:
        args["opacity"] = opacity
    return _with_caveat(await _call("isosurface", args))


@mcp.tool()
async def remove_volume(name: str) -> str:
    """Remove one loaded volume from the viewer."""
    bridge = _require_viewer()
    result = await _call("remove_volume", {"name": name})
    bridge.forget_volume(name)
    return f"Removed volume {result['removed']}."


@mcp.tool()
async def clear_viewer() -> str:
    """Remove all loaded structures and volumes from the viewer."""
    bridge = _require_viewer()
    await bridge.request("clear")
    # The viewer has dropped its volume handles, so nothing can fetch these any
    # more; holding the bytes past that point is retention with no reader.
    bridge.forget_all_volumes()
    return "Viewer cleared."


# structured_output=False, and the reason is not cosmetic. FastMCP derives an
# output schema from the return annotation, and `list[Any]` gets one — so the
# reply is serialised as *structured* content, which an Image cannot be:
#
#     Unable to serialize unknown type:
#     <class 'mcp.server.fastmcp.utilities.types.Image'>
#
# The tool then fails for every caller, having worked when the annotation was
# bare `list` and the library minted no schema. Turning the schema off puts the
# image back in unstructured content, where it belongs: an image is what this
# returns, not a JSON object describing one.
@mcp.tool(structured_output=False)
async def screenshot(path: str | None = None, overwrite: bool = False) -> list[Any]:
    """Capture the current viewport as a PNG.

    path: optional output file path; defaults to a timestamped file in
    ~/.cache/protean/screenshots/. Returns the image and the saved path.
    """
    bridge = _require_viewer()
    # A path-traced capture runs the tracer inside this request, and takes
    # seconds to minutes rather than the fraction of a second an ordinary one
    # does. Leaving the default timeout in place would abort work that was
    # going to succeed, and report it as a viewer stall.
    timeout = _TRACED_SCREENSHOT_TIMEOUT if _path_tracing else _SCREENSHOT_TIMEOUT
    result = await bridge.request("screenshot", {}, timeout=timeout)
    data_uri: str = result["data_uri"]
    header, _, payload = data_uri.partition(",")
    if "base64" not in header:
        raise ViewerError(f"Unexpected screenshot encoding: {header}")
    png = base64.b64decode(payload)

    if path:
        out = _writable(Path(path).expanduser(), (".png",), overwrite=overwrite)
    else:
        stamp = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d-%H%M%S")
        out = Path.home() / ".cache" / "protean" / "screenshots" / f"protean-{stamp}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    note = f"Saved to {out}"
    if "traced_ms" in result:
        note += f" (path traced in {result['traced_ms'] / 1000:.1f}s)"
    return [Image(data=png, format="png"), note]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
