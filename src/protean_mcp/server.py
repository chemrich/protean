"""protean MCP server — Phase 1 tools: open_viewer, fetch_structure, screenshot."""

from __future__ import annotations

import base64
import contextlib
import contextvars
import datetime
import functools
import gzip
import io
import json
import logging
import math
import re
import webbrowser
from dataclasses import dataclass
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

from . import __version__, vintage
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
from .analysis.hatching import apply_finish, ink_fraction
from .analysis.pharmacophore import (
    CLASS_COLOURS,
    UNCLASSIFIED,
    NoConnectivity,
    classify,
)
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
from .selections_numpy import _residue_keys, bond_pairs
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

# What the person at the viewer did since the model last heard anything.
#
# Without this the model answers about a scene it did not produce and has no way
# to know changed — the desync this project exists to prevent, arriving through
# a door we opened ourselves. MCP can push notifications, but client support is
# uneven, so this rides out on the next tool reply instead: no client support
# needed, and a model cannot act on a stale picture without being told.
_user_actions: list[str] = []


#: True while a tool call is already on its way to becoming a reply.
#:
#: A ContextVar rather than a counter because tool calls can overlap: under a
#: plain global, one call's nested `hide()` would silence a *different* call
#: that was about to report correctly. Copied into tasks, so a tool that spawns
#: one still counts as nested.
_replying_to_model: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_replying_to_model", default=False
)


def _take_user_actions() -> str:
    """Drain what the user did, as a phrase, or "" if they did nothing.

    Drained rather than read: the point is to say it once, at the first moment
    the model can act on it. Left in place it would ride out on every later
    reply too, and a model told twice about one click will reasonably conclude
    there were two.
    """
    if not _user_actions:
        return ""
    done = ", ".join(_user_actions)
    _user_actions.clear()
    return f"since your last call the user {done} in the viewer"


def _carrying_user_actions(result: Any) -> Any:
    """Attach the news to a reply, in whatever shape that reply has."""
    if not _user_actions:
        return result
    if isinstance(result, str):
        return f"{result} [{_take_user_actions()}]"
    if isinstance(result, dict):
        return {**result, "user_actions": _take_user_actions()}
    # A screenshot replies with a list of image content and has nowhere to put
    # a sentence. Left undrained on purpose: the next reply that can carry it
    # will, which is later than ideal and better than dropping it.
    return result


_mcp_tool = mcp.tool


def _tool(*args: Any, **kwargs: Any) -> Any:
    """`mcp.tool()`, and every reply carries what the user did in the viewer.

    Wrapped once here rather than added to fifty-three replies by hand, which
    is the version that goes stale: the next tool anyone writes would be the
    one that forgets, and nothing would fail. `functools.wraps` carries the
    name, docstring and signature across, so the schema FastMCP generates is
    byte-identical to the unwrapped one — checked, because the schema is the
    only thing a model ever sees of these functions.
    """

    def decorate(fn: Any) -> Any:
        @functools.wraps(fn)
        async def carrying(*call_args: Any, **call_kwargs: Any) -> Any:
            # Only the outermost call is a reply to the model, and only a reply
            # to the model may drain. Several tools reach other tools through
            # helpers — `_take_the_scene` calls `hide`, `_set_effects` calls
            # `effects` — and the inner reply is discarded by the caller. Left
            # unguarded the news went into that discarded dict and the model
            # heard nothing, which is worse than never having recorded it: the
            # tools that nest are the presets, and a preset is exactly what a
            # click applies.
            if _replying_to_model.get():
                return await fn(*call_args, **call_kwargs)
            token = _replying_to_model.set(True)
            try:
                result = await fn(*call_args, **call_kwargs)
            finally:
                _replying_to_model.reset(token)
            return _carrying_user_actions(result)

        return _mcp_tool(*args, **kwargs)(carrying)

    return decorate


# An ordinary capture is well under a second; the tracer takes seconds to
# minutes, and aborting it would report a stall for work that was succeeding.
_SCREENSHOT_TIMEOUT = 60.0
_TRACED_SCREENSHOT_TIMEOUT = 600.0

# Positioning the scene — a trajectory frame, a camera move, an orbit step — is
# not a capture. These borrowed the capture's budget back when there was only
# one, and they keep its old value under their own name so that changing what a
# render is allowed to cost does not quietly change what a camera move is.
_VIEWER_ACTION_TIMEOUT = 300.0

# What a capture is allowed to cost, derived from the pixels asked for rather
# than fixed, because the range a fixed number would have to span is enormous.
# 12000x9000 (108 MP) takes about 20 s on a real GPU — 0.19 s/MP. Measured
# under SwiftShader on the development machine, one capture per width:
#
#     1200 px   0.6 MP     6.5 s   10.4 s/MP
#     2000 px   1.7 MP    17.7 s   10.3
#     3000 px   3.9 MP    42.9 s   11.1
#     4323 px   8.1 MP   105.2 s   13.1     <- 183 mm at 600 dpi
#     6000 px  15.5 MP   209.7 s   13.5
#     8000 px  27.6 MP   467.3 s   16.9
#
# Nearly linear in the pixel count, drifting up as it grows. A CI runner is
# about three times slower again.
#
# The fixed 300 s this replaces sat right on that curve: ample for a 1200 px
# capture, marginal for the journal figure in CI — where the same commit failed
# three tests on one run and passed on the next — and unreachable above about
# 5000 px on any renderer this slow, including locally. A timeout a correct
# program clears only sometimes reports the runner's speed rather than the
# program's health, and re-running it is how a red build stops meaning
# anything.
#
# There is no progress signal to use instead. Mol*'s ordinary image pass
# renders in a single synchronous call — `MultiSamplePass.render`, with no
# `runtime.update` between samples — so the page's main thread is blocked for
# the whole capture and could not send a heartbeat if we asked for one. Silence
# is exactly what a healthy large capture looks like, and the pixel count is
# the only thing that separates it from a stall.
#
# What this budget costs, stated rather than glossed: a page that goes away for
# good — tab closed, or its reconnect attempts exhausted — is not detected here.
# A plain disconnect deliberately fails nothing, because the page may be
# mid-render with the socket dead under it and about to deliver. So a closed tab
# blocks for this budget where it used to block for 300 s. Accepted rather than
# solved: a bounded grace period after a disconnect would have to outlast the
# remaining render, which is precisely the quantity nobody knows. What does end
# the wait quickly is a page reconnecting without claiming the work — see
# ViewerBridge._fail_pending.
#
# The area is estimated as width squared, the same convention
# _MAX_SNAPSHOT_PIXELS is checked against and for the same reason: height
# follows the viewport's aspect and is not known on this side until the viewer
# answers. It overestimates — the viewer's real aspect measures 0.43 with the
# panels collapsed — and overestimating is the safe direction for a timeout.
#
# 60 s/MP against that estimate gives the journal figure 1121 s: about 10x what
# this machine needs for it and about 3x what a CI runner needs.
_CAPTURE_SECONDS_PER_MEGAPIXEL = 60.0
# Below a megapixel or so the fixed costs — encoding, the data URI, the round
# trip — dominate the render, so small captures keep a flat budget.
_CAPTURE_TIMEOUT_FLOOR = 300.0

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
    if _bridge is None:
        return use_bridge(ViewerBridge(static_dir=_static_dir()))
    return _bridge


def use_bridge(bridge: ViewerBridge) -> ViewerBridge:
    """Adopt a bridge as this process's viewer, and say what a click on it may do.

    One function because there are two ways to arrive here — the bridge this
    module builds for itself, and one handed in by a test or a restore — and
    the interesting half is not the assignment but `on_invoke`. Wiring that at
    the point of construction would leave every other path with a socket a page
    can talk to and no rule about what it may ask for, which is the kind of gap
    that is invisible until someone finds it. A test asserts the handler is
    there rather than trusting this comment.
    """
    global _bridge  # noqa: PLW0603 - deliberate module-level singleton
    _bridge = bridge
    bridge.on_invoke(_invoke_from_page, _page_view_catalogue())
    return bridge


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


#: Enough to recognise what moved without pasting a directory listing.
_STALE_FILES_NAMED = 3


def _vintage_note() -> str:
    """What build answered, and whether it is the one on disk.

    Appended to `open_viewer` because that is the first call of every session
    and the one that fails when a long-lived server meets a newer page. The
    version alone would not have identified the incident that motivated this —
    every build so far reads `0.1.0.dev0` — so the staleness is the part that
    carries the information, and the version rides along for the case where
    someone is comparing two machines rather than two moments.
    """
    note = f" [protean {__version__}, running since {vintage.running_since()}"
    stale = vintage.changed_since_load()
    if stale:
        shown = stale[:_STALE_FILES_NAMED]
        named = ", ".join(shown)
        if len(stale) > len(shown):
            named += " and others"
        note += (
            f"; **this process is running older code than the files on disk** "
            f"({named} changed since it started) — restart the MCP server, "
            f"because a rebuilt viewer can meet a server that predates it"
        )
    return note + "]"


@_tool()
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
        return (
            f"Viewer already connected at {shown}"
            f"{_visibility_note(bridge)}{_vintage_note()}"
        )
    if _static_dir() is None:
        return (
            f"Bridge is listening at {shown}, but the viewer app is not built. "
            "Run `npm install && npm run build` in the viewer/ directory, "
            "then call open_viewer again." + _vintage_note()
        )
    webbrowser.open(launch_url)
    await bridge.wait_for_viewer(timeout)
    return f"Viewer connected at {shown}{_visibility_note(bridge)}{_vintage_note()}"


@_tool()
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
    if isinstance(result, dict) and result.get("camera_settled") is False:
        # The viewer frames a new molecule with a tweened camera move and waits
        # for it to land. When that wait runs out of budget the camera is still
        # travelling, and a capture taken now is framed for somewhere it was
        # passing through. Said out loud because the alternative is a figure
        # that looks like a measurement and is not.
        note += (
            " [the camera was still moving when the viewer stopped waiting, so a "
            "capture taken immediately may be framed mid-move — reset_view() "
            "settles it]"
        )
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
async def show(
    representation: str = "cartoon",
    selection: str | None = None,
    handle: str | None = None,
    color: str | None = None,
    size: float | None = None,
    opacity: float | None = None,
    pickable: bool | None = None,
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
    pickable: False makes this scenery — it cannot be clicked, and it does not
      light up when something underneath it is. A see-through surface exists to
      be looked *through*, and left pickable it takes every click meant for what
      is inside it, putting a selection on a jagged patch of mesh rather than on
      the residue that was aimed at.
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
    if pickable is not None:
        args["pickable"] = pickable
    await _call("show", args)
    return {"name": label, "representation": representation, **_summarise(array, indices)}


@_tool()
async def color(color: str, name: str = "sele") -> dict[str, Any]:
    """Recolour an existing named selection.

    color: a Mol* colour theme or a literal hex value like "#3366cc".
    name: the handle passed to a previous select() or show().
    """
    return await _call("color", {"name": name, "color": color})


@_tool()
async def define_field(
    name: str,
    values: list[dict[str, Any]],
    key: str | None = None,
    domain: list[float] | None = None,
    palette: str = "blue-white-red",
    sizes: list[float] | None = None,
) -> dict[str, Any]:
    """Register a per-residue number as something colour() and size() can use.

    This is how any scalar you have computed becomes a picture. Register it
    once under a name, then `color(name)` paints it and `size(name)` gives it
    width — it is an ordinary theme from that point on, and appears in
    capabilities() beside Mol*'s own.

    values: a list of entries carrying "chain", "seq", and the number — the
      shape the analysis tools return. `rmsf()`'s residues go in unchanged,
      because each carries exactly one number and it is found whatever it is
      called. `conservation()`'s carry two, entropy and conservation, so say
      which with `key`.
    key: which field of each entry holds the number. Omitted, an entry with
      one number uses it and an entry with several refuses rather than
      guessing.

      Mind where the entries come from: `rmsf()` lists them under
      "most_mobile" and `conservation()` under "residues", and **both are
      truncated to `limit`** — 50 by default for rmsf. A field built from a
      truncated list covers part of the molecule and looks deliberate, so the
      reply says how many residues on screen it did not reach. Raise `limit`
      to cover the whole structure.
    domain: [low, high] for the ends of the ramp. Omitted, it fits the data,
      which is what makes a rigid core stand out; give it explicitly when two
      structures have to be comparable.
    palette: capabilities() lists them. The default runs blue through white to
      red, the convention for a signed quantity.
    sizes: [thin, thick] in angstroms, for what size() will do with this.

    Refused when the field matches no residue in the loaded structure. Chain
    and sequence number have to be the ones the viewer holds — a field keyed
    on something else registers cleanly and paints the whole molecule the
    "no data" grey, which looks like a rendering fault rather than a mistake
    in the numbers.

    Keyed by residue rather than by atom index on purpose: a biological
    assembly holds symmetry copies the analysis array does not, so index
    alignment would be silently wrong on exactly the structures where it
    matters, while a residue key gives every copy the value that residue
    earned.
    """
    if not values:
        raise ViewerError(f"Field {name!r} was given no values")
    for label, pair, ends in (
        ("domain", domain, "[low, high]"),
        ("sizes", sizes, "[thin, thick]"),
    ):
        if pair is not None and len(pair) != _DOMAIN_BOUNDS:
            raise ViewerError(f"{label} takes {ends}, got {len(pair)} numbers")

    keyed: dict[str, float] = {}
    for entry in values:
        if "chain" not in entry or "seq" not in entry:
            raise ViewerError(
                f"Every entry needs 'chain' and 'seq'; got {sorted(entry)}. "
                "The residues an analysis tool returns are already this shape."
            )
        number = _field_value(entry, key)
        residue = f"{entry['chain']}|{int(entry['seq'])}|{entry.get('ins_code', '')}"
        # Two entries for one residue is not a merge, it is a loss: the second
        # silently replaces the first. It happens for real — `rmsf(per="atom")`
        # gives one entry per atom, and residues 100 and 100A collapse together
        # unless the insertion code travels with them.
        if residue in keyed:
            raise ViewerError(
                f"Two entries name residue {residue.replace('|', ' ')!r}, so one "
                "would silently replace the other. Pass one number per residue — "
                'rmsf(per="residue") rather than per="atom" — and carry '
                "'ins_code' on any residue that has one."
            )
        keyed[residue] = number

    payload: dict[str, Any] = {"name": name, "values": keyed, "palette": palette}
    if domain is not None:
        payload["domain"] = [float(domain[0]), float(domain[1])]
    if sizes is not None:
        payload["sizes"] = [float(sizes[0]), float(sizes[1])]
    return await _call("define_field", payload)


def _field_value(entry: dict[str, Any], key: str | None = None) -> float:
    """The number in an analysis entry, whatever its author called it.

    `rmsf()` names it "rmsf", and requiring a rename before the values could be
    drawn would make the common case — hand the output of one tool to the next
    — the awkward one. `conservation()` carries two numbers, entropy and
    conservation, which is why `key` exists: an entry with more than one number
    is ambiguous and says so rather than picking.
    """
    if key is not None:
        if key not in entry:
            raise ViewerError(
                f"No {key!r} in entry {sorted(entry)}. Name the field holding "
                "the number to draw."
            )
        return float(entry[key])
    if "value" in entry:
        return float(entry["value"])
    numeric = {
        key: value
        for key, value in entry.items()
        if key not in ("seq", "chain", "ins_code")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }
    if len(numeric) == 1:
        return float(next(iter(numeric.values())))
    if not numeric:
        raise ViewerError(
            f"No number to draw in entry {sorted(entry)}. Name it 'value', or "
            "pass an entry that carries exactly one number."
        )
    raise ViewerError(
        f"Entry carries more than one number ({sorted(numeric)}), so which to "
        f'draw is ambiguous. Pass key="{sorted(numeric)[0]}" to say which.'
    )


# The palette an all-atom view uses unless told otherwise.
#
# Not CPK. Mol*'s CPK reds and blues fight a secondary-structure cartoon, and
# the carbons come out chain-coloured, so a sidechain drawn over a ribbon looks
# like a different molecule. These are quieter and chosen to sit on one:
# Charlie's brief was a light grey carbon and "a tacky 90s pastel palette",
# which is a better description of what reads well next to a cartoon than
# anything more principled would be.
_ELEMENT_PALETTE: dict[str, str] = {
    "C": "#d3d3d3",  # light grey — the backbone of every organic molecule
    "N": "#4ec9c9",  # teal
    "O": "#c9a0dc",  # mauve
    "S": "#e97451",  # burnt sienna
    "P": "#f2c14e",  # butterscotch, for nucleic acids and phosphates
    "H": "#ececec",
    "X": "#b0a8b9",  # anything unlisted, so a metal is never invisible
}

_ELEMENT_THEME = "protean-elements"

# The same palette with a warmer carbon, for whatever the picture is *about*.
# A ligand drawn in the pocket's own grey disappears into the sidechains around
# it; drawn in Mol*'s default it comes out chain-coloured brown, which is the
# thing the palette exists to fix and only looks deliberate by accident.
_SUBJECT_THEME = "protean-subject"
_SUBJECT_CARBON = "#e8b04b"

# Every symbol a palette may name, plus "X" for the fallback. Taken from the
# periodic table rather than from what protean has happened to meet, so an
# unusual metal is a colour someone can set rather than a refusal.
_KNOWN_ELEMENTS = frozenset(
    {"X"}
    | {
        symbol.upper()
        for symbol in [
            "H",
            "He",
            "Li",
            "Be",
            "B",
            "C",
            "N",
            "O",
            "F",
            "Ne",
            "Na",
            "Mg",
            "Al",
            "Si",
            "P",
            "S",
            "Cl",
            "Ar",
            "K",
            "Ca",
            "Sc",
            "Ti",
            "V",
            "Cr",
            "Mn",
            "Fe",
            "Co",
            "Ni",
            "Cu",
            "Zn",
            "Ga",
            "Ge",
            "As",
            "Se",
            "Br",
            "Kr",
            "Rb",
            "Sr",
            "Y",
            "Zr",
            "Nb",
            "Mo",
            "Tc",
            "Ru",
            "Rh",
            "Pd",
            "Ag",
            "Cd",
            "In",
            "Sn",
            "Sb",
            "Te",
            "I",
            "Xe",
            "Cs",
            "Ba",
            "La",
            "Ce",
            "Pr",
            "Nd",
            "Pm",
            "Sm",
            "Eu",
            "Gd",
            "Tb",
            "Dy",
            "Ho",
            "Er",
            "Tm",
            "Yb",
            "Lu",
            "Hf",
            "Ta",
            "W",
            "Re",
            "Os",
            "Ir",
            "Pt",
            "Au",
            "Hg",
            "Tl",
            "Pb",
            "Bi",
            "Po",
            "At",
            "Rn",
            "Fr",
            "Ra",
            "Ac",
            "Th",
            "Pa",
            "U",
            "Np",
            "Pu",
            "Am",
            "Cm",
            "Bk",
            "Cf",
            "Es",
            "Fm",
            "Md",
            "No",
            "Lr",
        ]
    }
)

# The field `superpose` registers, so a caller can paint divergence directly.
_DEVIATION_FIELD = "deviation"

# The target half of a superposed pair, which is the copy the field describes.
_SUPERPOSED_TARGET = "superposed_target"

# Below this, in angstroms, two structures have not moved relative to each
# other and a ramp fitted to the range would be painting rounding error.
_DEVIATION_FLOOR = 0.05


@_tool()
async def ligand_view(resn: str, around: float = 5.0) -> dict[str, Any]:
    """Draw a bound ligand and the residues that line its pocket.

    `preset("active-site")` already does the drawing; what it wants is a
    handle, and the thing a caller has is a name — "HEM", "ATP", "GLC". This
    turns one into the other, and says what it found.

    resn: the residue name as the file spells it. Refused when the structure
      does not contain it, and the refusal names the ones it does — a view of
      a ligand that is not there would otherwise draw an empty selection and
      report success.
    around: how far to reach for the lining residues, in angstroms. Whole
      residues, so a sidechain reaching into the pocket brings its backbone.

    Reports which ligand, how many copies, and how many residues line it. A
    structure with four copies of a heme is a different picture from one with
    a single site, and a caller cannot see the screen.
    """
    if around <= 0:
        raise ViewerError(
            f"around={around} matches nothing, so this would draw the ligand "
            "with no pocket around it and report success."
        )
    array = _require_structure()
    wanted = resn.strip().upper()
    try:
        found = _evaluate(_parse_selection(f"resn {wanted}"), array)
    except SelectionError as exc:
        raise ViewerError(str(exc)) from exc
    if not found.any():
        raise ViewerError(_no_such_residue(array, wanted))

    copies = len(
        {
            (str(array.chain_id[i]), int(array.res_id[i]), str(array.ins_code[i]))
            for i in np.flatnonzero(found)
        }
    )
    site = f"ligand_{wanted.lower()}"
    _register(site, np.flatnonzero(found), f"ligand_view({wanted})")

    lining = _evaluate(
        _parse_selection(f"byres (polymer within {around} of resn {wanted})"), array
    )
    if not lining.any():
        raise ViewerError(
            f"{wanted} is here but nothing lines it within {around} A. Drawing "
            "an empty pocket handle would report success for a picture with "
            "nothing in it — raise `around`, or the ligand is on the surface."
        )
    pocket = f"{site}_pocket"
    _register(pocket, np.flatnonzero(lining), f"ligand_view({wanted})")

    steps = await _preset_active_site(site)
    steps += await _element_coloured(pocket, 0.25)
    # The ligand last and in the warmer carbon, so it reads as the subject
    # rather than as one more residue in the lining.
    steps += await _element_coloured(site, 0.4, subject=True)
    return {
        "view": "ligand",
        "ligand": wanted,
        "copies": copies,
        "atoms": int(found.sum()),
        "lining_residues": _residue_count(array, lining),
        "handles": [site, pocket],
        "steps": steps,
    }


@_tool()
async def interface_view(chain_a: str, chain_b: str) -> dict[str, Any]:
    """Draw two chains apart and pick out where they touch.

    `interface()` already computes the contact residues and registers handles
    for them; this is the picture. The two chains go down in flat colours so
    the eye can tell them apart, and the contact residues come up as sticks on
    top, which is the whole point of looking at an interface.

    Refuses when the two chains do not touch, rather than drawing an empty
    highlight over an ordinary two-colour cartoon — which looks like an
    interface with nothing interesting in it rather than like no interface.

    Reports how many residues line each side, because a caller cannot see the
    screen and "they touch" is not the same claim as "they touch here".
    """
    described = await interface(chain_a, chain_b)
    touching = "iface_a" in _handles.names() and len(_handles.get("iface_a")) > 0
    if not touching:
        raise ViewerError(
            f"Chains {chain_a} and {chain_b} do not touch, so there is no "
            "interface to draw. interface() reports the numbers either way."
        )

    array = _require_structure()
    # The load's own scene goes first. Without it these cartoons are drawn over
    # a chain-coloured copy of the same backbone — two coincident
    # representations reading as one muddy one, which is what
    # `_take_the_scene` exists to prevent — and any third chain stays in its
    # load colours with nothing saying it was not part of the interface.
    steps: list[str] = []
    with contextlib.suppress(ViewerError):
        steps.append(await _run(hide, name=_WHOLE_SCENE))
    for chain, colour in ((chain_a, "#7ba7d7"), (chain_b, "#e6a86c")):
        handle = f"chain_{chain}"
        mask = _evaluate(_parse_selection(f"chain {chain} and polymer"), array)
        _register(handle, np.flatnonzero(mask), f"interface_view({chain_a},{chain_b})")
        steps.append(await _run(show, representation="cartoon", handle=handle))
        steps.append(await _run(color, color=colour, name=handle))

    for side in ("iface_a", "iface_b"):
        if side in _handles.names():
            steps += await _element_coloured(side, 0.3)

    steps.append(await _run(focus, name="iface_a"))
    return {
        "view": "interface",
        "chains": [chain_a, chain_b],
        # Counted from masks. `Handle.indices` is an index array, and
        # `_residue_count` calls `np.flatnonzero` — so passing indices dropped
        # atom 0 as falsy and treated the rest as positions, returning a
        # plausible small number about the first N atoms of the structure
        # rather than about the interface.
        "contact_residues": {
            side: _residue_count(array, _mask_of(array, _handles.get(side).indices))
            for side in ("iface_a", "iface_b")
        },
        "buried_area": described.get("buried_area"),
        "handles": [f"chain_{chain_a}", f"chain_{chain_b}", "iface_a", "iface_b"],
        "steps": steps,
    }


# One-letter to three-letter, for checking a mutation's notation against the
# file. Only the twenty; anything else in a mutation string is a mistake worth
# refusing rather than guessing at.
_ONE_LETTER = {
    "A": "ALA",
    "R": "ARG",
    "N": "ASN",
    "D": "ASP",
    "C": "CYS",
    "E": "GLU",
    "Q": "GLN",
    "G": "GLY",
    "H": "HIS",
    "I": "ILE",
    "L": "LEU",
    "K": "LYS",
    "M": "MET",
    "F": "PHE",
    "P": "PRO",
    "S": "SER",
    "T": "THR",
    "W": "TRP",
    "Y": "TYR",
    "V": "VAL",
}


@_tool()
async def mutation_view(mutations: str, chain: str | None = None) -> dict[str, Any]:
    """Draw the residues named in a mutation string, checking they are those.

    mutations: the usual notation, comma-separated — "A123G", "V45L,T67S".
      The first letter is the residue that should be there now, the number is
      its position, the last letter is what it becomes.
    chain: which chain, when the structure has more than one and the notation
      does not say. A mutation string rarely carries it.

    **Verifies the stated residue is what the file says it is, and refuses when
    it is not.** MCPymol does not, and that is the one thing worth doing better
    here: a mutation view that highlights the wrong residue because the
    numbering is offset by a construct tag looks exactly like one that worked.
    The picture is confident either way, and the person reading it has no way
    to tell. An offset of one is the most common thing that goes wrong with
    residue numbering and the least visible.

    The *new* residue is not checked, because it is not there — this draws the
    structure you have, at the positions a mutation would change.
    """
    array = _require_structure()
    wanted: list[tuple[str, int, str]] = []
    for piece in mutations.split(","):
        text = piece.strip().upper()
        match = re.fullmatch(r"([A-Z])(\d+)([A-Z])", text)
        if not match:
            raise ViewerError(
                f"{piece.strip()!r} is not a mutation. The notation is one "
                "letter, a number, one letter — 'A123G' — comma-separated."
            )
        was, position, becomes = match.group(1), int(match.group(2)), match.group(3)
        for letter in (was, becomes):
            if letter not in _ONE_LETTER:
                raise ViewerError(
                    f"{letter!r} in {text!r} is not one of the twenty amino acid letters."
                )
        wanted.append((was, position, becomes))

    where = f" and chain {chain}" if chain else ""
    checked: list[dict[str, Any]] = []
    mismatched: list[str] = []
    indices: list[int] = []
    for was, position, becomes in wanted:
        mask = _evaluate(_parse_selection(f"resi {position}{where}"), array)
        if not mask.any():
            mismatched.append(f"{was}{position}{becomes}: no residue {position} here")
            continue
        names = {str(array.res_name[i]) for i in np.flatnonzero(mask)}
        expected = _ONE_LETTER[was]
        if names != {expected}:
            mismatched.append(
                f"{was}{position}{becomes}: position {position} holds "
                f"{'/'.join(sorted(names))}, not {expected}"
            )
            continue
        indices.extend(np.flatnonzero(mask).tolist())
        checked.append(
            {
                "mutation": f"{was}{position}{becomes}",
                "residue": expected,
                "seq": position,
            }
        )

    if mismatched:
        raise ViewerError(
            "The structure does not match the notation, so this would have "
            "highlighted the wrong residues: "
            + "; ".join(mismatched)
            + ". Numbering offset by a construct tag is the usual cause; pass "
            "chain= if the structure has more than one."
        )

    handle = "mutations"
    _register(handle, np.asarray(indices), f"mutation_view({mutations})")
    steps = await _preset_active_site(handle)
    return {
        "view": "mutation",
        "verified": checked,
        "handle": handle,
        "steps": steps,
    }


@_tool()
async def pocket_view(resn: str, around: float = 5.0) -> dict[str, Any]:
    """Show the cavity a ligand sits in, as a surface.

    The same lining residues `ligand_view` draws as sticks, drawn as a surface
    instead — which is what a pocket looks like, and what you want when the
    question is about shape rather than about which residues touch what.

    **This is not cavity detection.** It shows the pocket around a ligand you
    name; it cannot find pockets in an apo structure, which is a different and
    much harder problem needing an algorithm and probably a dependency. The
    plan for this document called that the hard part and then found that the
    view everyone actually wants is this one.

    resn: the bound residue whose pocket to show. Refused when absent, naming
      what is bound.
    around: how far the lining reaches, in angstroms. Whole residues.
    """
    if around <= 0:
        raise ViewerError(
            f"around={around} matches nothing, so this would draw the ligand "
            "with no pocket around it and report success."
        )
    array = _require_structure()
    wanted = resn.strip().upper()
    try:
        found = _evaluate(_parse_selection(f"resn {wanted}"), array)
    except SelectionError as exc:
        raise ViewerError(str(exc)) from exc
    if not found.any():
        raise ViewerError(_no_such_residue(array, wanted))

    lining = _evaluate(
        _parse_selection(f"byres (polymer within {around} of resn {wanted})"), array
    )
    if not lining.any():
        raise ViewerError(
            f"{wanted} is here but nothing lines it within {around} A — it is "
            "not in a pocket. Raise `around`, or look at it with ligand_view."
        )

    pocket = f"pocket_{wanted.lower()}"
    site = f"pocket_{wanted.lower()}_ligand"
    _register(pocket, np.flatnonzero(lining), f"pocket_view({wanted})")
    _register(site, np.flatnonzero(found), f"pocket_view({wanted})")

    steps = [
        await _run(hide, name=_WHOLE_SCENE),
        await _run(
            show,
            representation="molecular-surface",
            handle=pocket,
            color="hydrophobicity",
        ),
        # Half-transparent, because a pocket you cannot see into is a lump.
        await _run(opacity, opacity=0.55, name=pocket),
    ]
    steps += await _element_coloured(site, 0.4, subject=True)
    steps.append(await _run(focus, name=site))
    return {
        "view": "pocket",
        "ligand": wanted,
        "lining_residues": _residue_count(array, lining),
        "handles": [pocket, site],
        "steps": steps,
    }


@_tool()
async def crosslink_view(distance: float = 2.5) -> dict[str, Any]:
    """Pick out what holds a fold together: disulfides and metal sites.

    Two things, both distance filters over what protean already has rather than
    new machinery. Cysteine sulfurs within bonding distance of each other are
    disulfides; metals and whatever they touch are coordination sites.

    Refuses when a structure has neither, rather than drawing a bare cartoon
    and calling it a crosslink view — the picture would be indistinguishable
    from one where the search failed.

    distance: how close two sulfurs must be to count as bonded. 2.5 A is
      generous for a disulfide, whose bond is about 2.05.
    """
    # One conformer, resolved first. A cysteine modelled in two positions has
    # two SG atoms a fraction of an angstrom apart, well inside any bonding
    # cutoff, so the raw array reports each such residue as a disulfide with
    # itself — and a real bridge between two such cysteines as four. Every tool
    # that reads coordinates resolves a state; this one did not.
    full = _require_structure()
    array = full[_conformer_state(full)]
    sulfurs = _evaluate(_parse_selection("resn CYS and name SG"), array)
    indices = np.flatnonzero(sulfurs)
    coords = np.asarray(array.coord)

    bridges: list[dict[str, Any]] = []
    paired: list[int] = []
    for a_pos, i in enumerate(indices):
        for j in indices[a_pos + 1 :]:
            if float(np.linalg.norm(coords[i] - coords[j])) <= distance:
                paired.extend((int(i), int(j)))
                bridges.append(
                    {
                        "a": f"{array.chain_id[i]} {int(array.res_id[i])}",
                        "b": f"{array.chain_id[j]} {int(array.res_id[j])}",
                        "angstroms": round(
                            float(np.linalg.norm(coords[i] - coords[j])), 2
                        ),
                    }
                )

    metals = _evaluate(_parse_selection("metals"), array)
    coordinating = (
        _evaluate(
            # Parenthesised, and the parentheses are the whole thing. `not`
            # binds looser than `within`, so `not metals within X of metals`
            # asks for everything that is *not* near a metal — 1260 atoms of
            # 1260 on myoglobin, which drew the entire structure as
            # ball-and-stick and called every residue coordinating. Neither
            # structure the other tests use has a metal, so nothing caught it.
            _parse_selection(f"byres ((not metals) within {2 * distance} of metals)"),
            array,
        )
        if metals.any()
        else np.zeros(len(metals), dtype=bool)
    )

    if not bridges and not metals.any():
        raise ViewerError(
            "No disulfides and no metals here, so there is nothing holding "
            "this together to draw. A cartoon with nothing picked out would "
            "look the same as a search that failed."
        )

    steps = [await _run(preset, name="publication-cartoon")]
    handles: list[str] = []
    if bridges:
        # Whole cysteines, not the sulfurs alone: a bridge drawn as two dots
        # says where it is and not what it joins.
        bridge_mask = _evaluate(
            _parse_selection("byres (resn CYS and name SG)"), array
        ) & _in_residues_of(array, paired)
        _register("disulfides", np.flatnonzero(bridge_mask), "crosslink_view()")
        handles.append("disulfides")
        steps += await _element_coloured("disulfides", 0.35, subject=True)
    if metals.any():
        _register(
            "metal_sites", np.flatnonzero(metals | coordinating), "crosslink_view()"
        )
        handles.append("metal_sites")
        steps += await _element_coloured("metal_sites", 0.3)

    steps.append(await _run(opacity, opacity=0.25, name=_styleable(_WHOLE_SCENE)))
    return {
        "view": "crosslink",
        "disulfides": bridges,
        "metal_atoms": int(metals.sum()),
        "coordinating_residues": _residue_count(array, coordinating),
        "handles": handles,
        "steps": steps,
    }


@_tool()
async def pharmacophore_view(resn: str) -> dict[str, Any]:
    r"""Colour a ligand's atoms by what each can do: donate, accept, or be greasy.

    A pharmacophore is a claim about what a site *wants*. It is not a list of
    contacts, which is what this document's plan twice assumed Mol\*'s
    `interactions` extension could provide — it computes interactions between
    atoms and cannot type one ligand's atoms at all.

    **The typing is inferred, not measured, and the reply says which rules
    fired.** Most crystal structures carry no hydrogens, so donor and acceptor
    cannot be read off the file; they follow from element and heavy-atom
    connectivity, the same rules a chemist applies by eye and wrong in the same
    places. An oxygen with one heavy neighbour is a hydroxyl and does both; with
    two it is an ether and only accepts. A nitrogen with three heavy neighbours
    has no hydrogen left to give.

    Argue with the counts in the reply rather than trusting the picture: it
    looks equally confident whichever rule fired.

    Grey means "no feature here", not "unknown" — a carbon next to an oxygen
    is part of a polar group rather than a greasy patch, so a sugar comes out
    almost entirely oxygens with grey carbons between them. That is the right
    answer for a sugar and would be the wrong-looking one for a drug, where
    the aromatic ring is the point and shows as hydrophobe.
    """
    array = _require_structure()
    wanted = resn.strip().upper()
    try:
        found = _evaluate(_parse_selection(f"resn {wanted}"), array)
    except SelectionError as exc:
        raise ViewerError(str(exc)) from exc
    if not found.any():
        raise ViewerError(_no_such_residue(array, wanted))

    indices = np.flatnonzero(found)
    try:
        bonds = bond_pairs(array)
    except SelectionError as exc:
        raise ViewerError(
            f"Cannot type {wanted} without knowing what is bonded to what: {exc}"
        ) from exc
    try:
        assigned, counts = classify(array, indices, bonds)
    except NoConnectivity as exc:
        raise ViewerError(
            f"Cannot type {wanted}: {exc}. The file carries no bonds for it "
            "and the residue-template dictionary does not know the name — "
            "which is the case for every UNL and LIG, so a docking pose needs "
            "its bonds in the file. Typed from element alone this would call "
            "every oxygen a hydroxyl and say so as confidently as a real "
            "answer."
        ) from exc
    if not assigned:
        raise ViewerError(
            f"Nothing in {wanted} typed as a pharmacophore feature — it holds "
            "no polar or greasy atoms this can recognise."
        )

    keyed = {
        f"{array.chain_id[i]}|{int(array.res_id[i])}|"
        f"{str(array.ins_code[i]).strip()}|{array.atom_name[i]}": feature
        for i, feature in assigned.items()
    }
    site = f"pharmacophore_{wanted.lower()}"
    _register(site, indices, f"pharmacophore_view({wanted})")
    typed = await _call(
        "define_atom_classes",
        {
            "name": site,
            "classes": keyed,
            "colors": {
                feature: int(colour[1:], 16) for feature, colour in CLASS_COLOURS.items()
            },
        },
    )
    # How many of the typed atoms the viewer could actually find. The theme
    # registers when *any* key matches, so a partial match paints most of the
    # ligand the no-feature grey while the counts below still claim every atom
    # was typed.
    reached = int(typed.get("matched", 0))

    _, steps = await _take_the_scene(_WHOLE_SCENE, f"resn {wanted}")
    steps += [
        await _run(show, representation="ball-and-stick", handle=site, size=0.45),
        await _run(color, color=site, name=site),
        await _run(focus, name=site),
        await _run(lighting, rig="studio"),
    ]
    return {
        "view": "pharmacophore",
        "ligand": wanted,
        "features": counts,
        "atoms_the_viewer_matched": reached,
        **(
            {
                "partial": (
                    f"Only {reached} of {len(assigned)} typed atoms were found "
                    "on screen, so the rest are painted the no-feature grey."
                )
            }
            if reached < len(assigned)
            else {}
        ),
        "colours": CLASS_COLOURS,
        "grey_means": f"no feature here, not unknown ({CLASS_COLOURS[UNCLASSIFIED]})",
        "inferred": (
            "Donor and acceptor come from element and heavy-atom connectivity, "
            "not from hydrogens, which this structure most likely does not "
            "carry. Rules of thumb, reported so they can be argued with."
        ),
        "handle": site,
        "steps": steps,
    }


def _mask_of(array: Any, indices: Any) -> Any:
    """An index array as a boolean mask over the structure."""
    mask = np.zeros(array.array_length(), dtype=bool)
    mask[np.asarray(indices, dtype=int)] = True
    return mask


def _in_residues_of(array: Any, atoms: list[int]) -> Any:
    """A mask over every atom sharing a residue with one of *atoms*.

    Through the selection engine's own residue keys rather than a Python loop
    over three numpy arrays: it is the same question `byres` answers, and a
    second definition of what makes a residue identity is a second set of
    answers to it.
    """
    keys = _residue_keys(array)
    return np.isin(keys, keys[np.asarray(atoms, dtype=int)])


def _no_such_residue(array: Any, wanted: str) -> str:
    """Refuse by naming what is actually here.

    "No HEM in this structure" leaves a caller guessing whether they misspelled
    it or loaded the wrong file. The list settles it in one reply.
    """
    present = sorted(
        {
            str(name)
            for name, hetero in zip(array.res_name, array.hetero, strict=False)
            if hetero
        }
        - {"HOH", "DOD", "WAT"}
    )
    if not present:
        return (
            f"No residue named {wanted!r} here, and nothing is bound to this "
            "structure at all — it holds only polymer and solvent."
        )
    return f"No residue named {wanted!r} here. What is bound: {', '.join(present)}."


def _residue_count(array: Any, mask: Any) -> int:
    """Residues, not atoms — which is what a caller means by "how many".

    Takes a **mask**. Handed an index array it silently counted something else
    entirely, because `np.flatnonzero` on indices drops atom 0 and reads the
    rest as positions.
    """
    return int(np.unique(_residue_keys(array)[np.asarray(mask, dtype=bool)]).size)


@_tool()
async def define_elements(
    name: str = _ELEMENT_THEME, colors: dict[str, str] | None = None
) -> dict[str, Any]:
    """Register a colour theme that paints each element a colour you choose.

    Mol* cannot do this. Its `element-symbol` theme takes exactly one
    parameter, `carbonColor`, and every other element comes from a fixed CPK
    table with no way in — so carbon can be changed and oxygen, nitrogen and
    sulfur cannot. An all-atom view that has to sit on a cartoon needs all of
    them.

    Registered like any field: `color(name)` applies it afterwards, and it
    appears in capabilities() beside Mol*'s own.

    colors: element symbol to hex, e.g. {"C": "#d3d3d3", "O": "#c9a0dc"}.
      Omitted, protean's own palette is used — light grey carbon with teal
      nitrogen, mauve oxygen and burnt sienna sulfur, chosen to sit quietly
      next to a secondary-structure cartoon rather than to match CPK.
      "X" names the colour for anything not listed, so an unusual metal is
      never invisible.
    """
    # `colors or ...` treated an explicitly empty dict as "use the default",
    # so a caller who filtered one down to nothing got seven elements they had
    # not asked for and a success reply naming them.
    chosen = _ELEMENT_PALETTE if colors is None else colors
    if not chosen:
        raise ViewerError(f"Palette {name!r} was given no colours")
    packed: dict[str, int] = {}
    for element, value in chosen.items():
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ViewerError(
                f"{element}: {value!r} is not a colour. Hex like '#c9a0dc', "
                "six digits, because a near-miss paints black without saying so."
            )
        # A name rather than a symbol registers cleanly and paints the whole
        # molecule the fallback colour, which is the "succeeds and shows one
        # flat thing" failure the rest of this file refuses.
        if element.upper() not in _KNOWN_ELEMENTS:
            raise ViewerError(
                f"{element!r} is not an element symbol. Use 'C' rather than "
                "'Carbon'; 'X' names the colour for everything unlisted."
            )
        packed[element] = int(value[1:], 16)
    return await _call("define_elements", {"name": name, "colors": packed})


@_tool()
async def size(size: str, name: str = "sele") -> dict[str, Any]:
    """Set what decides the *width* of an already-displayed selection.

    Colour is one channel and width is another, and the second was not exposed
    until now: `putty` has always varied its tube with B-factor because that is
    Mol*'s default for it, and nothing could say otherwise or ask for the same
    treatment anywhere else.

    size: a Mol* size theme — capabilities() reports the live list. The useful
      ones are `uncertainty` (B-factor, or whatever has been written into that
      column), `physical` (van der Waals radius, the usual choice for
      spacefill), and `uniform` (one width everywhere, which is how you flatten
      a putty back into a plain tube).
    name: the handle passed to a previous show().

    Every representation has a width, including the ones where that is not
    obvious: a cartoon's ribbon thickens under `physical` as much as a putty's
    tube does. The reply says what each was sizing by before, so that asking
    for the theme already in force reads as the no-op it is rather than as a
    change that failed to appear.
    """
    return await _call("size", {"name": name, "size": size})


@_tool()
async def opacity(opacity: float, name: str = "sele") -> dict[str, Any]:
    """Make an already-displayed selection transparent.

    opacity: 0 is invisible, 1 is solid. 0.3 or so is the usual "ghost" surface
      that lets a cartoon or a ligand show through from inside it.
    name: the handle passed to a previous show(). A handle that was only
      select()ed carries no geometry and is refused, because setting opacity on
      it would change nothing on screen.
    """
    return await _call("opacity", {"name": name, "opacity": opacity})


@_tool()
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


def _capture_timeout(width: int, traced: bool = False) -> float:
    """How long a capture *width* pixels across is allowed to take.

    Proportional to the work rather than fixed, so the same number does not
    have to be both generous enough for a 4323 px figure under software
    rendering and meaningful for a 1200 px one. See
    _CAPTURE_SECONDS_PER_MEGAPIXEL for the measurements behind the rate.

    Path tracing takes the larger of its flat budget and the size-derived one.
    Taking the flat 600 s alone inverted the ordering it exists to guarantee:
    above 3163 px the size-derived budget overtakes it, so a journal figure got
    1121 s with the tracer off and 600 s with it on — less time for strictly
    more expensive work, at exactly the sizes this budget was rebuilt for.
    """
    megapixels = width * width / 1_000_000
    budget = max(_CAPTURE_TIMEOUT_FLOOR, _CAPTURE_SECONDS_PER_MEGAPIXEL * megapixels)
    return max(_TRACED_SCREENSHOT_TIMEOUT, budget) if traced else budget


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


@_tool()
async def snapshot(
    path: str,
    column: str | None = None,
    width_mm: float | None = None,
    dpi: int = 300,
    format: str = "png",
    transparent: bool | None = None,
    crop: bool = False,
    finish: str | None = None,
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
    finish: redraw the capture in ink — "cross-hatch" or "hedcut". Tone becomes
      line: the image is banded by brightness and each band filled with
      strokes, more of them where it is darker, the way an engraving carries
      shading without any greys.

      **Applied after the capture, in Python, and the viewer will not show
      it.** Mol* has no hatching of any kind, so there is no live preview and
      no menu entry — a caller sees this only in the file, and the reply says
      so rather than leaving it to be noticed.

      It reads what is on screen as tone, so it wants a light ground and shape
      carried by shading: `publication-cartoon` or `painting` engrave well, a
      near-black `cinematic` ground comes out almost solid ink.
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
    timeout = _capture_timeout(width, traced=_path_tracing)
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
    inked: float | None = None
    if finish is not None:
        try:
            image = apply_finish(image, finish)
        except KeyError as exc:
            raise ViewerError(str(exc).strip("\"'")) from exc
        inked = ink_fraction(image)

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
        # Said outright, not implied by the picture: this file is not what the
        # viewer drew. protean's claim is that the picture and the analysis
        # describe the same thing, and a second renderer having touched it
        # afterwards is exactly the sort of thing that claim depends on
        # knowing.
        **(
            {
                "finish": finish,
                "finish_applied": "after the capture, in Python — the viewer "
                "does not show this",
                # The caller usually cannot look at the result. Near 1 means
                # the tone had nowhere to go — a dark ground engraves to a
                # filled rectangle with the molecule showing through as a few
                # light strokes.
                "ink": inked,
            }
            if finish is not None
            else {}
        ),
        **({"traced_ms": result["traced_ms"]} if "traced_ms" in result else {}),
    }


@_tool()
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


@_tool()
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
        # The insertion code travels with the entry when there is one. It was
        # grouped by and then dropped, so 100 and 100A came back as two
        # entries that named the same residue — which anything keying on
        # chain and number, `define_field` among them, cannot tell apart.
        entries = [
            {
                "chain": chain,
                "seq": seq,
                "comp": names[(chain, seq, ins)],
                "rmsf": round(float(np.mean(group)), 3),
                **({"ins_code": ins.strip()} if ins.strip() else {}),
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


@_tool()
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


@_tool()
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


@_tool()
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
    # The same ceiling snapshot() enforces through _snapshot_pixels, which this
    # path never went through. It mattered little against a flat 300 s; against
    # a size-derived budget a mistyped width buys hours per frame rather than
    # failing in five minutes — turntable(width=20000) would allow 6.7 h each.
    if width * width > _MAX_SNAPSHOT_PIXELS:
        raise ViewerError(
            f"A frame {width} pixels wide is beyond what can be captured "
            f"({_MAX_SNAPSHOT_PIXELS // 1_000_000} megapixels). Lower the width."
        )

    out = Path(directory).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    bridge = _require_viewer()
    timeout = _capture_timeout(width, traced=_path_tracing)
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


@_tool()
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
            await bridge.request(
                "frame", {"index": index}, timeout=_VIEWER_ACTION_TIMEOUT
            )

        return place

    result = await _capture_sequence(
        directory, width, transparent, [step(i) for i in indices]
    )
    # Back to where the run started, so the viewer is not left mid-trajectory.
    await bridge.request("frame", {"index": 0}, timeout=_VIEWER_ACTION_TIMEOUT)
    return {**result, "of": total, "stride": stride}


@_tool()
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


@_tool()
async def list_keyframes() -> dict[str, Any]:
    """The camera positions saved so far, in the order a timeline will use."""
    return {
        "keyframes": [{"name": name, **state} for name, state in _keyframes.items()],
        "count": len(_keyframes),
    }


@_tool()
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
            await bridge.request("set_camera", state, timeout=_VIEWER_ACTION_TIMEOUT)

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


@_tool()
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


@_tool()
async def spin(
    mode: str = "spin", speed: float | None = None, angle: float | None = None
) -> dict[str, Any]:
    """Set the viewer turning on its own, for looking rather than capturing.

    This is a live animation in the browser: it makes a structure easier to
    read on screen, and it does not produce frames. turntable() is the one that
    writes a sequence.

    mode: "spin" turns continuously, "rock" swings back and forth, "off" stops.
    speed: full turns per second — 1 is one revolution a second, which is
      faster than it sounds. Mol*'s defaults are 0.1 spinning (a turn every
      ten seconds) and 0.3 rocking, and those are what you get by omitting
      this. Mol* offers it in the range -2 to 2 for spinning, negative for
      the other direction, though nothing stops a larger number.

      This was radians per second until Mol* 5, where the same name came to
      mean 2*pi times as much. The unit here follows Mol*'s rather than
      converting, so that what protean reports and what the viewer holds stay
      one value.
    angle: rock only — how far it swings either side, in degrees.
    """
    args: dict[str, Any] = {"mode": mode}
    if speed is not None:
        args["speed"] = speed
    if angle is not None:
        args["angle"] = angle
    return await _call("spin", args)


@_tool()
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
                    "orbit", {"degrees": step}, timeout=_VIEWER_ACTION_TIMEOUT
                )

        return place

    result = await _capture_sequence(
        directory, width, transparent, [turn(i) for i in range(frames)]
    )
    # Close the loop, so a turntable is not a one-way trip that leaves every
    # later capture facing somewhere else.
    await bridge.request(
        "orbit",
        {"degrees": degrees - step * (frames - 1)},
        timeout=_VIEWER_ACTION_TIMEOUT,
    )
    return {
        **result,
        "degrees": degrees,
        "step_degrees": round(step, 4),
        "first": str(Path(result["directory"]) / "frame_0000.png"),
        "last": str(Path(result["directory"]) / f"frame_{frames - 1:04d}.png"),
    }


@_tool()
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


@_tool()
async def material(
    finish: str = "matte",
    name: str = "sele",
    metalness: float | None = None,
    roughness: float | None = None,
    emissive: float | None = None,
    bumpiness: float | None = None,
    bump_frequency: float | None = None,
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
    bumpiness: 0 to 1. Perturbs the surface normal, which is what makes a
      surface read as fibrous, powdery or eroded rather than moulded.

      **It needs a non-zero `bump_frequency` on the same representation to show
      at all**, and the two live in different places in Mol\\*. Most
      representations already carry one: of the eleven that declare the
      parameter, seven default non-zero — spacefill, molecular-surface,
      gaussian-surface, orientation and polyhedron at 1, cartoon and putty at
      2 — and four default to zero: **ball-and-stick, backbone, carbohydrate
      and ellipsoid**. On those four, bumpiness alone changes nothing. The
      reply says so rather than leaving it to be discovered: `bump_will_show`
      is false and `bump_shows_on` counts the representations where both halves
      are in place.

      Defaulted to 0 by every call that does not mention it, including a bare
      `material(finish=...)`, because a finish is a claim about gloss and not
      about texture.
    bump_frequency: 0 to 10, how fine the perturbation is, and **raising it
      makes a surface read smoother, not rougher** — a higher frequency puts
      the perturbation below the size of a pixel. Measured on a 1UBQ spacefill:
      0.036 of the frame moves at 1, 0.018 at 3, 0.004 at 6. Low is eroded
      stone, middling is felt or wool.

      Five representations declare no frequency at all — label, line, point,
      plane and gaussian-volume — because they have no surface to perturb.
      Asking for one there is reported through `bump_frequency_applied_to`
      rather than ignored.
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
        ("bumpiness", bumpiness),
        ("bump_frequency", bump_frequency),
    ):
        if value is not None:
            args[key] = value
    return await _call("material", args)


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
async def label(name: str = "sele", level: str = "residue") -> dict[str, Any]:
    """Draw text labels on a named selection.

    level: "residue" (e.g. HIS 94), "chain", or "element" for per-atom names.
    """
    return await _call("label", {"name": name, "level": level})


@_tool()
async def hide(name: str = "sele") -> dict[str, Any]:
    """Hide a named selection without discarding it; unhide() brings it back."""
    return await _call("hide", {"name": name})


@_tool()
async def unhide(name: str = "sele") -> dict[str, Any]:
    """Show a selection previously hidden with hide()."""
    return await _call("unhide", {"name": name})


@_tool()
async def remove(name: str = "sele") -> dict[str, Any]:
    """Delete a named selection and its representations from the scene.

    The handle goes too. It used to survive on this side while its component
    was deleted in the viewer, so the two disagreed about what existed: a
    later show() or shading() on that name resolved here and then failed
    there, and code asking "is this drawn?" by looking in the handle table got
    yes for something that had been removed.
    """
    result = await _call("remove", {"name": name})
    # `auto` is the viewer's own handle and has no entry here, so a missing one
    # is ordinary rather than an error.
    with contextlib.suppress(HandleError):
        _handles.drop(name)
    return result


@_tool()
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


@_tool()
async def focus(name: str = "sele") -> dict[str, Any]:
    """Zoom the camera to a named selection, returning the resulting camera target."""
    return await _call("focus", {"name": name})


@_tool()
async def reset_view() -> dict[str, Any]:
    """Reset the camera to frame the whole scene."""
    return await _call("reset_view", {})


@_tool()
async def orient() -> dict[str, Any]:
    """Align the camera to the structure's principal axes."""
    return await _call("orient", {})


@_tool()
async def measure(kind: str, names: list[str]) -> dict[str, Any]:
    """Add a distance, angle, or dihedral between named selections.

    kind: "distance" (2 selections), "angle" (3), or "dihedral" (4).
    Each selection is measured at its centroid, so point-like selections read
    most clearly — e.g. select("chain A and resi 58 and name NE2", name="ne2").
    """
    return await _call("measure", {"kind": kind, "names": names})


@_tool()
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

# The handle a preset draws the whole scene through. Shared by every one of
# them on purpose: applying a second view *replaces* the first rather than
# stacking another representation on top of it, because show() rebuilds a
# component under an existing name. That is what a switcher needs, and what the
# eye needs — two coincident representations read as one muddy one.
_SCENE_HANDLE = f"{_WHOLE_SCENE}_view"

# Every screen-space effect a preset has an opinion about. A preset states all
# of them rather than only the ones it changes, because `effects()` leaves
# anything omitted exactly as it was — right for a tool composing calls, wrong
# for a recipe declaring a whole look. `cinematic` is the only preset that turns
# depth of field on, and before this every view that did not mention it kept the
# blur: a `textbook` diagram applied after it came out with a shallow focus, and
# said it had succeeded.
_PRESET_EFFECTS = (
    "outline",
    "occlusion",
    "shadow",
    "depth_of_field",
    "bloom",
    "sharpening",
)


def _step(call: str, **kwargs: Any) -> str:
    """Render a tool call as the line of code that would reproduce it."""
    rendered = ", ".join(
        f'{key}="{value}"' if isinstance(value, str) else f"{key}={value}"
        for key, value in kwargs.items()
    )
    return f"{call}({rendered})"


async def _run(tool: Any, **kwargs: Any) -> str:
    """Make a call and return the line that reproduces it, from one dict.

    The steps a preset reports are documented as the calls it made, and as
    something a caller can run by hand instead. Written out separately they
    drifted: three of them omitted an argument that had been sent, so replaying
    the reported steps gave a different picture than the preset did — including,
    after `cinematic`, a different depth of field. Deriving the call and its
    description from the same kwargs makes that impossible rather than unlikely.
    """
    await tool(**kwargs)
    return _step(tool.__name__, **kwargs)


async def _set_effects(**wanted: Any) -> str:
    """Set every effect in `_PRESET_EFFECTS`, not only the ones being changed."""
    settings: dict[str, Any] = dict.fromkeys(_PRESET_EFFECTS, False)
    settings.update(wanted)
    await effects(**settings)
    return _step("effects", **settings)


def _styleable(target: str) -> str:
    """The handle shading, material and opacity should actually point at.

    Once a drawing preset has taken the scene over, `auto` is hidden, and
    restyling a hidden component succeeds while changing nothing on screen —
    the silent success this project exists to catch, arriving through a door we
    would have built ourselves. Style what is drawn.
    """
    if target == _WHOLE_SCENE and _SCENE_HANDLE in _handles.names():
        return _SCENE_HANDLE
    return target


async def _take_the_scene(target: str, selection: str) -> tuple[str, list[str]]:
    """Give a drawing preset a handle whose picture it can replace.

    A named handle already is one: show() rebuilds that component rather than
    layering over it, so drawing on it replaces what it was drawing. The whole
    scene is not — `auto` is the viewer's own handle for what the load preset
    built, and carries no atom indices on this side — so it is hidden and the
    preset draws its own selection instead.

    Returns the handle and the steps taken, so the reply says what happened to
    the scene rather than leaving a caller to notice `auto` went away.

    **Nothing is changed until it is known there is something to draw.** The
    first version hid `auto` and rebuilt the scene handle before checking that
    the selection had matched anything, so refusing left a blank viewer, an
    empty handle registered under `auto_view`, and a caller holding an error
    that said nothing about either. A refusal has to leave the scene alone.
    """
    if target != _WHOLE_SCENE:
        try:
            atoms = len(_handles.get(target))
        except HandleError as exc:
            raise ViewerError(str(exc)) from exc
        if not atoms:
            raise ViewerError(
                f"Handle {target!r} is empty, so this view would draw nothing "
                "and report success."
            )
        return target, []

    array = _require_structure()
    try:
        mask = _evaluate(_parse_selection(selection), array)
    except SelectionError as exc:
        raise ViewerError(f"Bad selection {selection!r}: {exc}") from exc
    indices = np.flatnonzero(mask)
    if not len(indices):
        raise ViewerError(
            f"Nothing matched {selection!r}, so this view would draw nothing "
            "and report success. The scene is untouched; pass a handle naming "
            "what to draw instead."
        )

    # Only now that the view is going to happen. A scene the load preset never
    # built has no `auto` to hide, which is not a reason to refuse to draw —
    # reported either way rather than suppressed, because a step saying nothing
    # happened is the difference between "taken over" and "there was nothing
    # there", and a caller cannot see the scene.
    steps = []
    try:
        await hide(_WHOLE_SCENE)
        steps.append(_step("hide", name=_WHOLE_SCENE))
    except ViewerError as exc:
        steps.append(f"{_step('hide', name=_WHOLE_SCENE)} — skipped: {exc}")
    _register(_SCENE_HANDLE, indices, f"preset over {selection!r}")
    steps.append(_step("select", selection=selection, name=_SCENE_HANDLE))
    return _SCENE_HANDLE, steps


async def _frame_the_scene(target: str) -> list[str]:
    """Point the camera at what the view just drew, and do it explicitly.

    Measured: drawing the same handle twice through show() lands on two
    different cameras. The first draw keeps the framing the load preset chose;
    the second refits to what is actually on screen and then holds — 0.144 of
    the frame between them on 1UBQ, with no preset involved, so this is show()'s
    behaviour and not the presets'. Left alone, applying a view twice gives two
    pictures, and the first figure anyone captures is framed for a scene that is
    no longer there.

    Asking for the frame outright makes a view idempotent, which is what a
    switcher needs. The cost is that a whole-scene view discards a camera the
    caller had moved, so it is listed in the steps rather than done quietly, and
    a view given a handle does not touch the camera at all.
    """
    if target != _WHOLE_SCENE:
        return []
    await reset_view()
    return ["reset_view()"]


async def _preset_publication_cartoon(target: str) -> list[str]:
    """A clean figure: white ground, soft directional light, crevices readable."""
    target = _styleable(target)
    return [
        await _run(background, color="#ffffff", gradient="off"),
        await _run(lighting, rig="three-point"),
        await _set_effects(occlusion=True),
        await _run(shading, style="normal", name=target),
        await _run(material, finish="matte", name=target),
    ]


async def _preset_illustrative(target: str) -> list[str]:
    """The textbook look: flat banded colour with a drawn edge."""
    target = _styleable(target)
    return [
        await _run(background, color="#ffffff", gradient="off"),
        await _run(lighting, rig="flat"),
        await _set_effects(outline=True, outline_color="#000000"),
        await _run(shading, style="cel", name=target, cel_steps=4),
    ]


async def _preset_cinematic(target: str) -> list[str]:
    """A lit render: dark ground, raking back light, deep crevices, shallow focus.

    Styling only, like `publication-cartoon` — it lights whatever is on screen
    rather than choosing it. Pair it with a view that draws.
    """
    target = _styleable(target)
    return [
        await _run(background, color="#05070c", gradient="off"),
        await _run(lighting, rig="rim"),
        await _set_effects(occlusion=True, depth_of_field=True),
        await _run(shading, style="normal", name=target),
        await _run(material, finish="glossy", name=target),
    ]


async def _preset_ghost_heart(target: str) -> list[str]:
    """A see-through surface that leaves what is inside it visible.

    The scoping is the point. A surface shown under the *same* handle would
    replace whatever that handle was already drawing — the component is rebuilt,
    not layered — so the cartoon you wanted to see inside the ghost would
    silently disappear. The surface gets its own handle over the same atoms.

    **Over the whole scene it means everything except the solvent.** A molecular
    surface is computed per atom, so an isolated water gets its own closed
    blob: 1UBQ drew fifty-eight of them, detached spheres floating around the
    fold, and they were 14% of everything on screen. Ligands and ions stay in —
    they are part of the molecule's shape, and the envelope should bulge around
    a bound ligand rather than ignore it. Only the solvent is scenery.
    """
    array = _require_structure()
    if target == _WHOLE_SCENE:
        try:
            mask = _evaluate(_parse_selection("not solvent"), array)
        except SelectionError as exc:  # pragma: no cover - the grammar is fixed
            raise ViewerError(str(exc)) from exc
        indices = np.flatnonzero(mask)
        if not len(indices):
            raise ViewerError(
                "This structure is nothing but solvent, so a ghost heart over "
                "it would wrap water. Pass a handle naming what to wrap."
            )
        origin = "preset(ghost-heart) over everything but the solvent"
    else:
        try:
            indices = _handles.get(target).indices
        except HandleError as exc:
            raise ViewerError(str(exc)) from exc
        origin = f"preset(ghost-heart) over {target}"

    ghost = f"{target}_ghost"
    _register(ghost, indices, origin)
    return [
        await _run(
            show,
            representation="molecular-surface",
            handle=ghost,
            opacity=0.25,
            pickable=False,
        ),
        await _run(shading, style="xray", name=ghost),
        await _run(material, finish="glossy", name=ghost),
    ]


async def _preset_light_ground(target: str) -> list[str]:
    """A white ground, and nothing else touched.

    Paired with `dark-ground` rather than made a toggle, because nothing here
    records which one is in force: the server does not track what was last
    applied, and a control that reports state it cannot know is worse than one
    that needs two entries. Stated in docs/views.md §5.8, which is where the
    stateful version would start.
    """
    del target  # the ground is the scene's, not any selection's
    return [await _run(background, color="#ffffff", gradient="off")]


async def _preset_dark_ground(target: str) -> list[str]:
    """A near-black ground, for a lit render or a dark room."""
    del target
    return [await _run(background, color="#05070c", gradient="off")]


#: The handle `sidechains` draws under, so `hide-sidechains` can find it again.
_SIDECHAIN_HANDLE = "sidechains"


async def _element_coloured(
    handle: str, size: float, *, subject: bool = False
) -> list[str]:
    """Ball-and-stick in protean's element palette, registering it first.

    Four views want this now, and the fourth is where it was noticed: the
    palette is registered by the page rather than shipped by Mol*, so a view
    that asks for it by name without registering it first is refused with
    "Unknown colour theme". Registering is idempotent — the viewer skips an
    identical re-register rather than churning the theme under a live
    representation — so calling it every time costs nothing.
    """
    theme = _SUBJECT_THEME if subject else _ELEMENT_THEME
    palette = {**_ELEMENT_PALETTE, "C": _SUBJECT_CARBON} if subject else _ELEMENT_PALETTE
    return [
        await _run(define_elements, name=theme, colors=palette),
        await _run(
            show,
            representation="ball-and-stick",
            handle=handle,
            color=theme,
            size=size,
        ),
    ]


async def _preset_default(target: str) -> list[str]:
    """Put back the picture the load produced, and take the views' own away.

    Watched go wrong: eight views clicked in a row leave no way back, because
    every drawing view hides `auto` and replaces the one handle they share, so
    the scene the load built is still there and still hidden with nothing
    naming it.

    This restores what is *drawn* and does not touch lighting, ground or
    effects. Those are the styling presets' business — `light-ground` and
    `dark-ground` exist for exactly that — and a "default" that silently reset
    someone's carefully built lighting because they wanted the cartoon back
    would be a worse surprise than the one it fixes. The reply says so rather
    than leaving it to be discovered.
    """
    del target
    steps: list[str] = []
    # Removed rather than hidden: a hidden component still answers to its
    # handle, so a later `unhide` or a styling call would bring back a picture
    # the caller thought they had put away.
    #
    # Every handle a view made, not a list of three. The first version named
    # the scene, the ligand and the sidechains — which was already false when
    # it was written, because `ghost-heart` is one click away and leaves its
    # translucent surface wrapped around whatever comes next, and every view
    # added since registers handles of its own. A view's handles are the ones
    # whose origin says a view made them, which is a fact the registry already
    # keeps rather than a list to maintain.
    for handle in _handles.names():
        origin = _handles.get(handle).origin
        if handle == _SCENE_HANDLE or "view" in origin or "preset" in origin:
            with contextlib.suppress(ViewerError):
                steps.append(await _run(remove, name=handle))
    steps.append(await _run(unhide, name=_WHOLE_SCENE))
    return [*steps, await _run(reset_view)]


async def _preset_sidechains(target: str) -> list[str]:
    """Sidechain sticks over whatever is already drawn.

    Its own handle, for the reason the ghost heart has one: drawing under an
    existing handle rebuilds that component rather than layering on it, so the
    cartoon these are meant to sit on would disappear.

    Solvent and the backbone are both out. `sidechain` is already the variable
    part of a residue — the thing worth looking at when someone asks to see
    them — and adding waters would bury it.
    """
    del target
    array = _require_structure()
    try:
        # The alpha carbon comes along, and it is not a detail. `sidechain` is
        # "polymer and not backbone", and CA is backbone — so drawing the
        # selection alone gives sticks that begin at CB with no bond back to
        # anything, floating beside the ribbon they belong to. Including CA
        # gives each one the bond that attaches it.
        # Asked twice, and the two questions are different. Whether there is
        # anything to draw is about sidechain atoms; what gets drawn includes
        # the alpha carbons that anchor them. Conflating the two broke the
        # refusal the moment CA joined the drawing — every polymer has one, so
        # glycine, whose sidechain is a hydrogen, started reporting success
        # for a view of nothing but anchors.
        present = _evaluate(_parse_selection("sidechain and not solvent"), array)
        # `byres sidechain` is what makes this right for both polymers and
        # for glycine. Anchoring on "every polymer CA" drew a lone unbonded
        # ball for every glycine — whose sidechain is a hydrogen, so it
        # contributes an anchor and nothing to anchor — and left nucleic acids
        # exactly as they were, bases floating off a sugar that is backbone.
        # Restricted to residues that actually contribute a sidechain atom,
        # and taking each polymer's own anchor: CA for protein, C1' for
        # nucleic.
        mask = _evaluate(
            _parse_selection(
                "(sidechain or (byres sidechain and (name CA or name C1'))) "
                "and not solvent"
            ),
            array,
        )
    except SelectionError as exc:  # pragma: no cover - the grammar is fixed
        raise ViewerError(str(exc)) from exc
    if not present.any():
        raise ViewerError(
            "Nothing here has a sidechain to draw — a bare backbone, or "
            "nothing but glycine, whose sidechain is a hydrogen."
        )
    indices = np.flatnonzero(mask)
    _register(_SIDECHAIN_HANDLE, indices, "preset(sidechains)")
    # 0.22 against Mol*'s own default of 0.15, which drew hairlines that read
    # as noise against a cartoon. Picked by looking at 0.15, 0.22 and 0.3 side
    # by side; 0.4 was tried first and buried the ribbon completely.
    return await _element_coloured(_SIDECHAIN_HANDLE, 0.22)


async def _preset_hide_sidechains(target: str) -> list[str]:
    """Put the sidechain sticks away again, leaving everything else alone.

    Refuses rather than passing quietly when there are none drawn: a control
    that reports success for doing nothing is the failure this project spends
    most of its time on.
    """
    del target
    if _SIDECHAIN_HANDLE not in _handles.names():
        raise ViewerError(
            "No sidechains are drawn, so there are none to hide. Apply the "
            "sidechains view first."
        )
    # Not `_run`, which discards the reply. The handle survives being hidden,
    # so the registry answers the same either way and this preset would report
    # success for a second call that moved nothing — the exact failure the
    # refusal above exists to prevent, one call further along. `changed` counts
    # the components that actually flipped, and zero of them means the sticks
    # were already away.
    result = await hide(name=_SIDECHAIN_HANDLE)
    if not result.get("changed"):
        raise ViewerError(
            "The sidechains are registered but already hidden, so this would "
            "have changed nothing. Apply the sidechains view to bring them back."
        )
    return [_step("hide", name=_SIDECHAIN_HANDLE)]


async def _preset_active_site(target: str) -> list[str]:
    """Sticks and labels on the site, the rest faded back out of the way."""
    if target == _WHOLE_SCENE:
        raise ViewerError(
            "active-site needs a handle saying which site — from select(), "
            "interface(), or near()"
        )
    rest = _styleable(_WHOLE_SCENE)
    return [
        await _run(opacity, opacity=0.2, name=rest),
        await _run(
            show, representation="ball-and-stick", handle=target, color="element-symbol"
        ),
        await _run(label, name=target, level="residue"),
        await _run(focus, name=target),
        await _run(lighting, rig="studio"),
        await _set_effects(occlusion=True),
    ]


# -- the views that decide what is drawn ---------------------------------------
#
# Five recipes with one shape: take the scene, draw one representation through
# it, style it, frame it. They were five copies of that shape, and the copies
# are what let their effect sets and their hand-written step strings drift —
# `cinematic`'s depth of field survived into every view that did not mention it,
# and three views described a call they had not quite made. One helper and a
# table of the differences makes both impossible rather than unlikely.


async def _hydrophobic_style(_target: str, handle: str) -> list[str]:
    """Ring lighting, because a surface's curvature is what is being read.

    One hard key light flattens it into a highlight and a shadow.
    """
    return [
        await _run(background, color="#ffffff", gradient="off"),
        await _run(lighting, rig="ring"),
        await _set_effects(occlusion=True),
        await _run(material, finish="matte", name=handle),
    ]


# Gouache rather than CPK. Geis mixed his own colours and the plastic-sphere
# palette did not exist yet; more to the point, hard green carbon against hard
# red oxygen is the single loudest thing in an all-atom picture, and it is what
# makes a spacefill read as a rendering. Warm stone carbon, slate nitrogen and a
# brick oxygen let the light do the work instead. The iron is named so the heme
# is findable, though on myoglobin the sphere model buries it — which is the
# honest behaviour of a spacefill and not something the palette can fix.
_PAINTING_PALETTE = {
    "C": "#cdbfa6",
    "N": "#8397ad",
    "O": "#b25a4a",
    "S": "#c8a13c",
    "P": "#a9825c",
    "H": "#e8e0d2",
    "FE": "#8f3222",
    "X": "#94897b",
}
_PAINTING_THEME = "protean-painting"


async def _painting_style(_target: str, handle: str) -> list[str]:
    """Depth carried by light rather than by line — after Irving Geis.

    An homage to how structures were drawn before they were rendered, and not a
    facsimile: Geis worked in gouache from coordinates on paper, and what can be
    borrowed here is the set of decisions, not the hand. No outline at all,
    which is what separates this from every other styled view in the catalogue;
    form comes from a warm key against a cool fill, occlusion in the crevices
    and a cast shadow, over a ground that is paper rather than white.

    Named for the technique and not for the man. A view named `geis` would
    promise more than a recipe can deliver, and his name has no currency as a
    style term the way `richardson` does.
    """
    return [
        # Not white. A painting has a ground, and pure white makes the matte
        # atoms read as cut out and pasted on; this is about a quarter of the
        # way to buff, which is where the cast shadow has somewhere to fall.
        await _run(background, color="#efe9dc", gradient="off"),
        await _run(lighting, rig="studio"),
        await _set_effects(occlusion=True, shadow=True),
        await _run(shading, style="normal", name=handle),
        await _run(material, finish="matte", name=handle),
        # Recoloured after the draw rather than at it, because the palette has
        # to be registered with the viewer before anything can name it, and
        # registering is itself a viewer call. The steps say so.
        await _run(define_elements, name=_PAINTING_THEME, colors=_PAINTING_PALETTE),
        await _run(color, color=_PAINTING_THEME, name=handle),
    ]


async def _richardson_style(_target: str, handle: str) -> list[str]:
    """The ribbon diagram's restraint: two tones, a quiet line, white paper.

    Mol\\*'s cartoon already draws arrowed strands and coiled helices, which is
    Jane Richardson's invention, so almost all of this view is styling held
    back rather than anything added.

    **The outline cannot be made thinner, and this is where that was found.**
    §5.9 asked for one; Mol\\*'s outline scale is `min: 1, step: 1`
    (`mol-canvas3d/passes/outline.js`) and `illustrative` already sits at 1, so
    the floor is the default and a smaller number would have been clamped and
    reported as applied. A grey line at scale 1 is the available way to make an
    edge recede, and it is closer to a drawn line than black is anyway.
    """
    return [
        await _run(background, color="#ffffff", gradient="off"),
        # Two bands rather than four, which is the whole difference in the
        # shading: a lit side and a shaded side, the way a wash drawing has.
        await _run(shading, style="cel", name=handle, cel_steps=2),
        await _run(lighting, rig="standard"),
        await _set_effects(outline=True, outline_color="#4a4a4a"),
    ]


# Madder, indigo, weld yellow, walnut and undyed cream — the dyes a wool
# workshop actually had before aniline. Chosen for the same reason `painting`
# has its own palette: CPK's hard green carbon against hard red oxygen is the
# loudest thing in an all-atom picture, and no amount of surface texture reads
# through it.
_WOOL_PALETTE = {
    "C": "#d9cbb3",  # undyed cream, the ground the others sit on
    "N": "#3f5d7d",  # indigo
    "O": "#a33b32",  # madder
    "S": "#c9a227",  # weld yellow
    "P": "#8a6a4a",  # walnut
    "H": "#e8e0d2",
    "X": "#9a9384",
}
_WOOL_THEME = "protean-wool"

#: The halo handle. A second, barely-there layer at a larger radius is how a
#: fibrous silhouette is faked without a shader or a hair system.
_FELT_HALO = "auto_felt_halo"


async def _felt_style(target: str, handle: str) -> list[str]:
    """Felted wool: no speculars, a fibrous surface, a soft edge.

    **A style, and it carries no data.** Every other all-atom view in this
    catalogue either shows what is there or colours by something; this one is
    a look, in the way `painting` is a look, and says so rather than implying a
    measurement. It was built and rejected as a data-carrying treatment first:
    binding a per-atom number to the radius jitter is invisible against a
    surface already textured at the same spatial frequency, which is recorded
    in docs/bakeoff.md.

    The soft edge is the one part with a claim behind it. A hard shell asserts
    that the van der Waals surface is a boundary, and it is not — it is where a
    probability has fallen off to an arbitrary threshold. A fuzzy edge is the
    more honest picture, which is the same argument `putty` makes about
    B-factor.
    """
    steps = [
        # Wool over a warm off-white, so the cream carbon has something to sit
        # against. Pure white makes undyed wool look grey.
        await _run(background, color="#f2ede4", gradient="off"),
        await _run(lighting, rig="three-point"),
        # No outline: a drawn edge is the opposite of a fibrous one. Occlusion
        # rather than a cast shadow, because shadow on a fuzzy surface reads as
        # dirt.
        await _set_effects(occlusion=True),
        await _run(shading, style="normal", name=handle),
        await _run(define_elements, name=_WOOL_THEME, colors=_WOOL_PALETTE),
        await _run(color, color=_WOOL_THEME, name=handle),
        await _run(size, size="jitter", name=handle),
        # Frequency 3, measured rather than picked: it is fineness, so raising
        # it makes the surface read *smoother*, and 6 nearly vanishes at
        # ordinary viewport sizes. See the material tests.
        await _run(
            material, finish="matte", name=handle, bumpiness=0.9, bump_frequency=3
        ),
    ]
    return steps + await _felt_halo(target, handle)


async def _felt_halo(_target: str, handle: str) -> list[str]:
    """The second layer, at 1.12x and barely opaque.

    Its own handle, for the reason `ghost-heart` uses one: `show()` rebuilds a
    component under an existing name, so drawing the halo through the same
    handle would replace the wool underneath it rather than layer over it.

    Drawn from the *handle* rather than from a selection string, because the
    handle is the set that was actually drawn — re-describing it would risk the
    two layers covering different atoms, which is exactly the disagreement a
    halo would make invisible.
    """
    # Registered here before it is drawn, the way `ghost-heart` registers its
    # surface: `show(handle=...)` draws a handle this side already knows, and
    # the halo is a new one over the same atoms.
    entry = _handles.get(handle)
    _register(_FELT_HALO, entry.indices, f"preset(felt) halo over {handle}")
    return [
        await _run(
            show,
            representation="spacefill",
            handle=_FELT_HALO,
            size=1.12,
            opacity=0.2,
            pickable=False,
        ),
        await _run(color, color=_WOOL_THEME, name=_FELT_HALO),
        await _run(size, size="jitter", name=_FELT_HALO),
        await _run(
            material, finish="matte", name=_FELT_HALO, bumpiness=1.0, bump_frequency=3
        ),
    ]


@dataclass(frozen=True)
class _View:
    """What separates one drawing view from another, and nothing else."""

    selection: str
    representation: str
    color: str
    style: Any

    @property
    def draws_ligands(self) -> bool:
        """Does this view need what is bound drawn separately?

        Derived rather than declared. It was a hand-kept boolean for one
        review's length, and in that time it was already wrong:
        `hydrophobic-surface` selects `polymer` like the other two and nobody
        set its flag, so a surface of maltose-binding protein still had no
        maltose in it. A field that restates the field two lines above it will
        drift again.

        The all-atom views take `not solvent` and have the ligand already;
        drawing it again would put a second, differently-styled copy inside
        the first.
        """
        return self.selection == "polymer"


_VIEWS: dict[str, _View] = {
    # `illustrative` is the styling half of textbook and stays a preset in its
    # own right, because restyling what is already drawn is a different request
    # from deciding what to draw. Called here rather than repeated.
    "textbook": _View(
        selection="polymer",
        representation="cartoon",
        color="secondary-structure",
        style=lambda target, handle: _preset_illustrative(target),
    ),
    "putty": _View(
        selection="polymer",
        representation="putty",
        color="uncertainty",
        style=lambda target, handle: _preset_publication_cartoon(target),
    ),
    "hydrophobic-surface": _View(
        selection="polymer",
        representation="molecular-surface",
        color="hydrophobicity",
        style=_hydrophobic_style,
    ),
    # Solvent is left out of both of these: waters are most of the atoms in a
    # crystal structure and none of the shape, so drawing them puts a haze of
    # spheres or sticks around the molecule. Ligands and ions are kept, because
    # in an all-atom view they are usually the point.
    "spacefill": _View(
        selection="not solvent",
        representation="spacefill",
        color="element-symbol",
        style=lambda target, handle: _preset_publication_cartoon(target),
    ),
    # Ball-and-stick rather than `line`: a line model has no thickness to
    # shade, so the ambient occlusion that makes this look like an object
    # instead of a diagram would have nothing to work on.
    "skeleton": _View(
        selection="not solvent",
        representation="ball-and-stick",
        color="element-symbol",
        style=lambda target, handle: _preset_publication_cartoon(target),
    ),
    # The two illustration styles of docs/views.md §5.9. Both are drawing views
    # rather than styling ones, because each is a look *and* the subject that
    # look was invented for: Geis painted all the atoms, Richardson drew the
    # fold, and either recipe applied to the other's subject is not the thing.
    "painting": _View(
        selection="not solvent",
        # Spheres, and this was settled by looking at both. Ball-and-stick is
        # the nearer relative of what Geis actually drew, and on myoglobin it
        # came out as a thicket of green wire with no depth at all: occlusion
        # and a cast shadow need something to fall across, and a stick model
        # gives them almost nothing. The sphere model is the only one of the
        # two that this lighting can model. What it costs is the interior,
        # which a spacefill always costs.
        representation="spacefill",
        # Mol*'s own, and overwritten a moment later by the style, which
        # registers the palette it wants and applies it. It has to be a theme
        # that already exists: `show()` refuses a name the viewer has never
        # heard of, and a protean palette exists only once something has
        # registered it.
        color="element-symbol",
        style=_painting_style,
    ),
    # A style rather than a treatment: it carries no data, and the docstring
    # says so. See docs/bakeoff.md for the version that tried to carry some.
    "felt": _View(
        selection="not solvent",
        representation="spacefill",
        # Replaced a moment later by the wool palette, which has to be
        # registered before anything can name it. `show()` refuses a theme the
        # viewer has never heard of, so the draw needs one that already exists.
        color="element-symbol",
        style=_felt_style,
    ),
    "richardson": _View(
        selection="polymer",
        representation="cartoon",
        # Flat and pale, and one tone for the whole fold. Secondary structure is
        # already drawn here — arrowed strands, coiled helices — so colouring by
        # it would say the same thing twice, and saying it in colour is what
        # `textbook` does. The shape carries it, which is the entire argument of
        # a ribbon diagram.
        color="#d8d3c8",
        style=_richardson_style,
    ),
}


_LIGAND_HANDLE = "auto_ligand"


async def _draw_the_ligands(target: str) -> list[str]:
    """Draw whatever is bound, for the views whose selection would drop it.

    `textbook` and `putty` select `polymer`, and a ligand is not polymer — so
    maltose-binding protein came up with no maltose in it, which is most of the
    reason anyone loads that structure. Reported by looking at a picture and
    asking where the sugar was.

    Solvent stays out: a crystal structure's waters are most of its non-polymer
    atoms and none of its point. Under its own handle so it survives the next
    view taking the scene, and drawn in the same element palette the sidechains
    use so the two agree.
    """
    if target != _WHOLE_SCENE:
        return []  # a handle names what to draw; nothing is implied alongside it
    array = _require_structure()
    try:
        mask = _evaluate(_parse_selection("not polymer and not solvent"), array)
    except SelectionError as exc:  # pragma: no cover - the grammar is fixed
        raise ViewerError(str(exc)) from exc
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    _register(_LIGAND_HANDLE, indices, "view(ligand)")
    # Thicker and warmer than the sidechains: a ligand is the subject when it
    # is there, and should not read as one more sidechain.
    return await _element_coloured(_LIGAND_HANDLE, 0.35, subject=True)


async def _draw_view(name: str, target: str) -> list[str]:
    """Take the scene, draw the view through it, style it, then frame it."""
    view = _VIEWS[name]
    handle, steps = await _take_the_scene(target, view.selection)
    steps.append(
        await _run(
            show, representation=view.representation, handle=handle, color=view.color
        )
    )
    if view.draws_ligands:
        steps += await _draw_the_ligands(target)
    elif _LIGAND_HANDLE in _handles.names():
        # The scene handle is hidden when a view takes over, and the ligand
        # has its own — so switching from `textbook` to `spacefill` left the
        # ball-and-stick maltose inside the new spheres, and to
        # `hydrophobic-surface` left sticks poking through a surface the
        # ligand is meant to be inside. Exactly the double-draw the ligand
        # handle exists to avoid, one view later.
        with contextlib.suppress(ViewerError):
            steps.append(await _run(hide, name=_LIGAND_HANDLE))
    # The same leak, one layer out. `felt` draws a halo under a handle of its
    # own, and nothing here knew about it: switching from `felt` to any other
    # drawing view left a 1.12x, alpha-0.2 shell of every non-solvent atom
    # hanging around the new picture. The views are supposed to be exclusive —
    # they replace their predecessor rather than stack — and a second handle is
    # exactly how that invariant gets broken quietly.
    if name != "felt" and _FELT_HALO in _handles.names():
        with contextlib.suppress(ViewerError):
            steps.append(await _run(hide, name=_FELT_HALO))
    steps += await view.style(target, handle)
    return steps + await _frame_the_scene(target)


_PRESETS: dict[str, Any] = {
    "publication-cartoon": _preset_publication_cartoon,
    "illustrative": _preset_illustrative,
    "cinematic": _preset_cinematic,
    "ghost-heart": _preset_ghost_heart,
    "active-site": _preset_active_site,
    "light-ground": _preset_light_ground,
    "dark-ground": _preset_dark_ground,
    "default": _preset_default,
    "sidechains": _preset_sidechains,
    "hide-sidechains": _preset_hide_sidechains,
    **{name: functools.partial(_draw_view, name) for name in _VIEWS},
}


@_tool()
async def preset(name: str, handle: str | None = None) -> dict[str, Any]:
    """Apply a named recipe: lighting, effects, shading and materials at once.

    A preset is a composition of the other display tools, so nothing here is
    reachable only through it — the reply lists the calls it made, and any of
    them can be adjusted afterwards or run by hand instead.

    Presets come in two kinds, and the difference matters when you combine
    them. Some only restyle — ground, lighting, shading, material — and leave
    what is drawn alone. The rest also decide what is drawn, replacing the
    picture rather than adding to it, so applying a second one of those
    switches views instead of piling them up.

    name: one of — capabilities() reports the live list.

      Restyle what is already there:

      publication-cartoon  White ground, three-point light, ambient occlusion
                           on. The default figure.
      illustrative         Flat cel shading with a black outline. The textbook
                           look; pairs well with a simple cartoon.
      cinematic            Near-black ground, back light, ambient occlusion and
                           a shallow depth of field. A render, not a diagram.
      light-ground         A white ground, and nothing else touched.
      dark-ground          A near-black ground, and nothing else touched.

      Decide what is drawn:

      default              The picture the load produced. Every view below
                           replaces the last, so this is the way back; it
                           restores what is drawn and leaves lighting and
                           ground alone.
      textbook             Cartoon by secondary structure, flat and outlined —
                           illustrative's styling with the drawing done too.
      putty                A tube whose width *and* colour follow B-factor, so
                           a disordered loop reads as a fat warm bulge.
      hydrophobic-surface  A molecular surface coloured by hydrophobicity, ring
                           lit so the curvature survives.
      spacefill            Every non-solvent atom as a CPK sphere, lit so the
                           packing reads as volume rather than as a blob.
      skeleton             Ball-and-stick over everything but the solvent.
      painting             All-atom, no outline at all, warm ground, studio
                           light and a cast shadow. Depth from light rather
                           than from line — an homage to Irving Geis.
      richardson           The ribbon diagram: cartoon in one pale tone, cel
                           shaded at two steps, a grey line, white paper.
      felt                 All-atom spheres as felted wool: dyed-wool palette,
                           no speculars, a fibrous surface and a soft halo. A
                           look, not a measurement — it carries no data, and
                           the soft edge is the honest picture of a van der
                           Waals surface, which is a probability falling off
                           rather than a boundary.

      Add to what is there:

      ghost-heart          A see-through surface over the selection, leaving
                           whatever is inside it visible. Drawn under its own
                           handle so it layers over the existing representation
                           rather than replacing it, and it takes no clicks.
      sidechains           Sidechain sticks over whatever is already drawn,
                           under their own handle.
      hide-sidechains      Puts those away again. Refuses when none are drawn.
      active-site          Ball-and-stick and residue labels on the given site,
                           the rest of the structure faded back. Needs a handle.

    handle: which selection the preset applies to. Omitted means the whole
      scene — the drawing presets then hide what the viewer loaded, draw under
      the handle "auto_view", and reframe the camera on it, all of which the
      reply lists. What lands in "auto_view" is the view's own selection:
      `polymer` for textbook and putty, and everything that is not solvent for
      hydrophobic-surface, spacefill and skeleton. **A whole-scene view
      therefore discards a camera you had moved**, so apply the view first and
      orient afterwards.
      Given a handle they leave the camera alone. active-site refuses an
      omitted handle, since a site has to be named.

      To put the viewer's own scene back: remove("auto_view") and then
      unhide("auto"). Both are needed — unhide alone leaves the view drawn on
      top of the restored scene, which is the pair of coincident
      representations the shared handle exists to avoid.
    """
    recipe = _PRESETS.get(name)
    if recipe is None:
        raise ViewerError(
            f"Unknown preset {name!r}. Available: {', '.join(sorted(_PRESETS))}"
        )
    _require_viewer()
    steps = await recipe(handle or _WHOLE_SCENE)
    return {"preset": name, "applied_to": handle or _WHOLE_SCENE, "steps": steps}


# -- what a click can ask for --------------------------------------------------
#
# The page names a view from this list and nothing else. It is not a route to
# the tool surface, and the difference is not stylistic: the socket is
# token-authenticated, but the going-public pass established what a page holding
# that token can already do. Reaching every tool would hand a hostile page
# `snapshot(path=)`, `save_session(path=)`, `movie(path=)` and
# `electrostatics(path=)` — every one of which writes to a caller-chosen path.
# The write-protection in backlog 21 refuses to *change what a file is*, which
# is not the same as refusing to write.
#
# So: view names from a fixed list, resolved here, run through the same tool a
# model would call. A test enumerates the live tool registry and asserts nothing
# reachable from the page takes a path — read from the registry rather than from
# a list in a file, because the going-public pass found a hand-written list of
# nine where fourteen tools existed.

#: The tools a page-initiated request may reach, by name in the MCP registry.
_PAGE_TOOLS = frozenset({"preset"})

#: What a view does to the scene, which decides how a menu may present it.
#:
#: Not a detail of presentation: the three behave differently enough that one
#: flat list would misdescribe two of them.
#:
#: - `draws` replaces what is on screen. `_take_the_scene` hides `auto` and
#:   draws its own selection, so these replace each other *and* whatever was
#:   there. Each also applies a styling preset of its own, so a `draws` chosen
#:   after a `styles` silently discards it.
#: - `styles` changes how the scene looks and never what is drawn. These
#:   replace each other — all of them set background and lighting — and compose
#:   with any `draws`.
#: - `layers` draws over what is already there, under its own handle, and
#:   composes with everything.
_VIEW_DRAWS = "draws"
_VIEW_STYLES = "styles"
_VIEW_LAYERS = "layers"

#: View names a click may ask for, the preset each means, and what it does.
#:
#: `active-site` is deliberately absent: it needs a handle to point at, and a
#: button has none to give.
_PAGE_VIEWS: dict[str, tuple[str, str]] = {
    # First, and named for what it does rather than for what it undoes: after
    # a run of views there is otherwise no way back to the picture the load
    # produced, because each one hides `auto` and replaces the handle they all
    # share.
    "default": ("default", _VIEW_DRAWS),
    "textbook": ("textbook", _VIEW_DRAWS),
    "putty": ("putty", _VIEW_DRAWS),
    "hydrophobic-surface": ("hydrophobic-surface", _VIEW_DRAWS),
    "spacefill": ("spacefill", _VIEW_DRAWS),
    "skeleton": ("skeleton", _VIEW_DRAWS),
    "painting": ("painting", _VIEW_DRAWS),
    "felt": ("felt", _VIEW_DRAWS),
    "richardson": ("richardson", _VIEW_DRAWS),
    "publication-cartoon": ("publication-cartoon", _VIEW_STYLES),
    "illustrative": ("illustrative", _VIEW_STYLES),
    "cinematic": ("cinematic", _VIEW_STYLES),
    "light-ground": ("light-ground", _VIEW_STYLES),
    "dark-ground": ("dark-ground", _VIEW_STYLES),
    "ghost-heart": ("ghost-heart", _VIEW_LAYERS),
    "sidechains": ("sidechains", _VIEW_LAYERS),
    "hide-sidechains": ("hide-sidechains", _VIEW_LAYERS),
}


def _page_view_catalogue() -> list[dict[str, str]]:
    """What the page may ask for, in the order a menu should show it.

    Sent to the page rather than written there. A copy in `index.html` is the
    list-that-drifts this project keeps meeting — a hand-written nine where
    fourteen tools existed, a named-file CI list that stopped running new
    suites — and two lists cannot disagree if only one of them exists.
    """
    return [{"name": name, "kind": kind} for name, (_, kind) in _PAGE_VIEWS.items()]


async def _invoke_from_page(view: str) -> str:
    """Run a view the person at the viewer asked for.

    Deliberately `preset()` itself rather than the recipe behind it: one code
    path, two entry points, so a handle made by a click is an ordinary handle
    and the picture a click produces is the picture the model would have made.
    Any other arrangement lets the two render the same view differently, and
    eventually they will.
    """
    entry = _PAGE_VIEWS.get(view)
    if entry is None:
        raise ViewerError(
            f"Unknown view {view!r}. Available: {', '.join(sorted(_PAGE_VIEWS))}"
        )
    preset_name = entry[0]
    # A click is not a reply to the model, so nothing it runs may drain the
    # queue: without this the `preset` below would take an earlier click's news
    # into a reply that goes back to the page, where the model never sees it.
    token = _replying_to_model.set(True)
    try:
        await preset(preset_name)
    finally:
        _replying_to_model.reset(token)
    _user_actions.append(f"applied the {view} view")
    return view


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


@_tool()
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


@_tool()
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
    # A restored snapshot names its colour themes, and protean's own are
    # registered by the page rather than shipped by Mol* — so a session saved
    # with sidechains on, reopened in a fresh page, asked for a theme the
    # registry did not hold. The element palette is put back here. A field
    # registered by hand before saving is not: its numbers are the caller's
    # and this file never had them, which the reply says rather than leaving
    # the grey to be discovered.
    palette_restored = False
    with contextlib.suppress(ViewerError):
        await define_elements()
        palette_restored = True
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
        "element_palette_restored": palette_restored,
        "fields_not_restored": (
            "A session records theme names, not the numbers behind them, so a "
            "field registered with define_field() before saving is gone. "
            "Register it again to colour by it."
        ),
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


@_tool()
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

    With show=True it also registers a **deviation field** and a handle for
    the target copy, and the reply says how to use them. That is the readable
    way to look at a pair: two structures in two colours interleave where they
    agree and look no different where they do not, whereas one copy painted by
    how far the other moved shows the motion itself. The field covers every
    residue the two share rather than the ones the fit kept — on a hinge
    motion those are the residues that did *not* move.

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

    # A superposed pair drawn in two colours is the picture everyone reaches
    # for and it is close to unreadable: where the two agree the backbones
    # interleave at the same depth and read as a mottle of both colours, and
    # where they disagree looks the same. What you actually want to see is
    # *how far* each residue moved, painted onto one copy — so it is registered
    # here, ready for `color("deviation")`, rather than left as an exercise.
    deviation_field = None
    field_error = None
    registered: dict[str, Any] | None = None
    # By the time this runs the session has been discarded, the globals
    # replaced and the pair sent to the viewer. A colouring aid that raises
    # would therefore fail a superposition that already happened — losing the
    # rmsd, the transform and the outliers, while the viewer shows the result
    # of the work being reported as an error. It is an aid; it reports its own
    # failure and leaves the analysis alone.
    furthest = max((entry.deviation for entry in result.deviations), default=0.0)
    if result.deviations and furthest > _DEVIATION_FLOOR:
        try:
            registered = await define_field(
                _DEVIATION_FIELD,
                [entry.as_dict() for entry in result.deviations],
                key="deviation",
                palette="white-red",
                domain=[0.0, furthest],
            )
        except ViewerError as exc:
            registered = None
            field_error = str(exc)
    if result.deviations and furthest <= _DEVIATION_FLOOR:
        # Stretching a ramp over floating-point noise paints a random red
        # speckle that reads as a hinge. Two structures that do not differ
        # should say so.
        field_error = (
            f"No deviation field: the two differ by at most {furthest:.3g} A, "
            "which is nothing to paint."
        )
    if registered is not None:
        kept = np.flatnonzero(np.isin(combined.chain_id, list(taken)))
        _register(_SUPERPOSED_TARGET, kept, "superpose(target)")
        deviation_field = {
            "name": _DEVIATION_FIELD,
            "residues": registered.get("matched"),
            "target_handle": _SUPERPOSED_TARGET,
            "how": (
                f'hide("auto"), then show(handle="{_SUPERPOSED_TARGET}", '
                f'representation="cartoon") and color("{_DEVIATION_FIELD}", '
                f'name="{_SUPERPOSED_TARGET}"). That paints one copy by how far '
                "the other moved — white where they agree, red at the hinge — "
                "which is legible where two interleaved colours are not. "
                "Measured over every residue the two share, not only the ones "
                "the fit kept: on a hinge motion the fit discards exactly the "
                "residues that moved."
            ),
        }

    return {
        "displayed": True,
        "structure": _structure_identifier,
        "target_chains_shown": sorted(taken),
        "mobile_chains_shown": sorted({str(c) for c in moved.chain_id}),
        "renamed_chains": renamed,
        "atoms": ours,
        "viewer_atom_count": theirs,
        "agree": theirs is None or int(theirs) == ours,
        **({"deviation_field": deviation_field} if deviation_field else {}),
        **({"deviation_field_unavailable": field_error} if field_error else {}),
        "note": (
            "The loaded structure is now the superposed pair, so selections and "
            "analysis address both. The mobile copy is in the target's frame; "
            "its coordinates were transformed, not just displayed shifted."
            + (f" Renamed to avoid collisions: {renamed}." if renamed else "")
            + discarded
        ),
    }


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
async def list_volumes() -> dict[str, Any]:
    """List the volumes currently loaded, with their statistics and provenance."""
    _require_viewer()
    result = await _call("list_volumes")
    for volume in result.get("volumes", []):
        _with_caveat(volume)
    return result


@_tool()
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


@_tool()
async def remove_volume(name: str) -> str:
    """Remove one loaded volume from the viewer."""
    bridge = _require_viewer()
    result = await _call("remove_volume", {"name": name})
    bridge.forget_volume(name)
    return f"Removed volume {result['removed']}."


@_tool()
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
@_tool(structured_output=False)
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
