#!/usr/bin/env python
"""Regenerate every image in `docs/images/` by driving a real viewer.

Run it from the repo root:

    npm run build --prefix viewer          # the figures are of the built app
    uv run python docs/figures/make_figures.py

    uv run python docs/figures/make_figures.py --only representations presets

Why a script rather than a folder of screenshots someone took once: a picture
in the README is a claim about what protean does *now*. Hand-collected images
go stale silently — the one failure mode this project spends most of its
effort on. These are regenerated from the same tool calls the documentation
prints beside them, so a figure that stops matching its caption is a figure
that stops being produced.

Every capture is checked for ink before it is written. A blank frame that
reports success is the thing CONTRIBUTING.md warns about on its first line,
and a figure script is an easy place for one to hide: the sheet still gets
composed, the tiles are just empty.

Needs a browser and the network (structures come from RCSB). Chrome is found
the same way the browser test suite finds it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import protean_mcp.server as server  # noqa: E402
from protean_mcp.connection import ViewerBridge  # noqa: E402

# The Chrome hunt and the teardown are accumulated scar tissue — an exact-URL
# match, keeping Chrome's own log, the pkill that catches respawned helpers.
# CONTRIBUTING.md asks that it not be hand-copied, so this imports it.
from tests.browser import STATIC, find_chrome  # noqa: E402
from tests.conftest import free_port  # noqa: E402

# The bridge logs every asset request at INFO, which buries the figure names.
logging.getLogger().setLevel(logging.WARNING)

IMAGES = REPO / "docs" / "images"
DPI = 150

# The viewer's own handle for whatever the load preset drew. Hiding it is how a
# figure takes the scene over rather than drawing on top of it — two coincident
# representations read as one muddy one.
SCENE = "auto"

# A window a little taller than wide: molecules are blobs, and a 16:9 frame
# spends most of its pixels on background either side of one.
WINDOW = (1100, 1080)

# Set by viewer(): (debugging port, viewer url), for page_screenshot().
_CDP: tuple[int, str] | None = None


# -- capture -------------------------------------------------------------------


def _mm(pixels: int) -> float:
    """The physical width that yields *pixels* at this script's DPI."""
    return pixels * 25.4 / DPI


def _ink(path: Path) -> float:
    """Fraction of the frame that is not the single most common colour.

    A crude proxy for "something was drawn", and deliberately crude: the point
    is to catch the empty frame, not to grade composition.
    """
    frame = np.asarray(Image.open(path).convert("RGB")).reshape(-1, 3)
    _, counts = np.unique(frame, axis=0, return_counts=True)
    return float(1.0 - counts.max() / len(frame))


class Blank(RuntimeError):
    """A capture came back empty, which no figure here is allowed to be."""


def _bounds(path: Path, slack: float = 0.04) -> tuple[int, int, int, int]:
    """The box around everything that is not the background, plus a margin.

    Framing happens here rather than through `snapshot(crop=True)`. That
    argument reports `cropped: true` and returns the frame untouched — checked
    on 2026-08-24 against an opaque ground, a transparent ground, a whole
    molecule and a single residue, all four byte-identical to the uncropped
    capture. Until that is fixed, a figure script that trusted it would be
    quietly producing badly framed figures and saying it had cropped them.
    """
    frame = np.asarray(Image.open(path).convert("RGB"))
    flat = frame.reshape(-1, 3)
    colours, counts = np.unique(flat, axis=0, return_counts=True)
    ground = colours[counts.argmax()]
    drawn = np.abs(frame.astype(np.int16) - ground.astype(np.int16)).sum(axis=2) > 12
    rows, columns = np.where(drawn)
    if not len(rows):
        raise Blank(f"{path.name} has nothing but background in it")
    pad = round(max(frame.shape[:2]) * slack)
    return (
        max(0, int(columns.min()) - pad),
        max(0, int(rows.min()) - pad),
        min(frame.shape[1], int(columns.max()) + pad + 1),
        min(frame.shape[0], int(rows.max()) + pad + 1),
    )


def _union(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    """One box holding all of them, so a sheet's tiles share a scale."""
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def publish(image: Image.Image, path: Path) -> Path:
    """Write a finished figure, quantised to a 256-colour palette.

    These are read on a web page at a width no browser gives them, and an
    adaptive palette with dithering is indistinguishable from full colour at
    that size while costing a third of the bytes — measured across the set:
    10 MB down to 3.4 MB. Checked by eye on the darkest gradient here, the
    lighting sheet, which is where banding would show first if it were going
    to. Full colour is one edit away if a future figure needs it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    palette = image.convert("RGB").quantize(
        colors=256, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG
    )
    palette.save(path, "PNG", optimize=True, dpi=(DPI, DPI))
    return path


def trim(path: Path) -> Path:
    """Crop a single figure to what it actually draws, in place."""
    return publish(Image.open(path).crop(_bounds(path)), path)


async def capture(
    path: Path, pixels: int = 420, floor: float = 0.01, **kwargs: Any
) -> Path:
    """Snapshot to *path*, and refuse to return a frame with nothing in it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    await server.snapshot(
        path=str(path), width_mm=_mm(pixels), dpi=DPI, overwrite=True, **kwargs
    )
    inked = _ink(path)
    if inked < floor:
        raise Blank(f"{path.name} is {inked:.4f} inked, under the {floor} floor")
    return path


# -- contact sheets ------------------------------------------------------------


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def contact_sheet(
    out: Path,
    tiles: list[tuple[str, Path]],
    columns: int = 4,
    tile_width: int = 420,
    ground: tuple[int, int, int] = (255, 255, 255),
    ink: tuple[int, int, int] = (32, 32, 36),
    share_frame: bool = True,
) -> Path:
    """Lay labelled tiles out on a grid.

    One image per family beats one image per value: a reader comparing five
    lighting rigs wants them side by side, and a page of twenty separate
    screenshots is a page nobody scrolls through.
    """
    if not tiles:
        raise ValueError(f"{out.name}: no tiles to lay out")
    label_height = max(18, tile_width // 16)
    pad = max(6, tile_width // 48)
    face = _font(max(11, tile_width // 30))

    # `share_frame` is the difference between a sheet that compares and a sheet
    # that catalogues. One window across every tile keeps their scales
    # honest — a spacefill really is fatter than a backbone trace, and cropping
    # each tile to its own bounds would hide that. But it only means anything
    # when the tiles are the same molecule seen differently. Across six
    # different structures the union is nobody's frame: it crops the close-ups
    # and strands the small ones in the middle of their tiles.
    frames = [_bounds(tile) for _, tile in tiles]
    windows = [_union(frames)] * len(tiles) if share_frame else frames
    span = max(w[2] - w[0] for w in windows)
    tallest = max(w[3] - w[1] for w in windows)
    tile_height = round(tallest * tile_width / span)

    # A shared frame means one scale for every tile — that *is* the comparison.
    # An unshared one means the opposite: each tile is its own subject and should
    # fill its cell. Scaling those by a common factor keeps the grid tidy and
    # makes the small subject unreadable, which is how a benzamidine coloured by
    # pharmacophore feature came out as an eight-pixel smudge.
    scales = (
        [tile_width / span] * len(tiles)
        if share_frame
        else [
            min(tile_width / (w[2] - w[0]), tile_height / (w[3] - w[1])) for w in windows
        ]
    )
    rows = -(-len(tiles) // columns)

    cell_w, cell_h = tile_width + pad, tile_height + label_height + pad
    sheet = Image.new("RGB", (columns * cell_w + pad, rows * cell_h + pad), ground)
    draw = ImageDraw.Draw(sheet)

    for index, (name, tile) in enumerate(tiles):
        column, row = index % columns, index // columns
        x, y = pad + column * cell_w, pad + row * cell_h
        # Centred in its cell either way, so the grid stays square rather than
        # ragged whichever scale the sheet chose.
        box, scale = windows[index], scales[index]
        art = (
            Image.open(tile)
            .convert("RGB")
            .crop(box)
            .resize(
                (
                    max(1, round((box[2] - box[0]) * scale)),
                    max(1, round((box[3] - box[1]) * scale)),
                ),
                Image.LANCZOS,
            )
        )
        cell = Image.new("RGB", (tile_width, tile_height), ground)
        cell.paste(art, ((tile_width - art.width) // 2, (tile_height - art.height) // 2))
        sheet.paste(cell, (x, y))
        draw.rectangle(
            [x, y, x + tile_width - 1, y + tile_height - 1], outline=(220, 218, 214)
        )
        draw.text((x + 2, y + tile_height + 3), name, font=face, fill=ink)

    return publish(sheet, out)


@contextmanager
def scratch() -> Iterator[Path]:
    directory = Path(tempfile.mkdtemp(prefix="protean-figures-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# -- the viewer ----------------------------------------------------------------


@asynccontextmanager
async def viewer() -> Any:
    """One throwaway browser for the whole run, wired to the production tools.

    `--headless=new` on its own keeps the real GPU, which is both faster and
    the renderer a reader's own machine will use. The software-GL flags the
    test suite sets are there to make pixel thresholds reproducible across
    machines, and nothing here compares pixels to a threshold.
    """
    chrome = find_chrome()
    if chrome is None:
        raise SystemExit("No Chrome found. Set PROTEAN_CHROME to its path.")
    if not (STATIC / "index.html").exists():
        raise SystemExit("Viewer not built. Run: npm run build --prefix viewer")

    global _CDP
    bridge = ViewerBridge(port=free_port(), static_dir=STATIC)
    await bridge.start()
    profile = tempfile.mkdtemp(prefix="protean-figures-chrome-")
    log = (Path(profile) / "chrome.log").open("wb")
    # A debugging port so one figure can photograph the browser page itself
    # rather than the canvas inside it — the viewer's own furniture is the
    # thing a first-time reader most needs to see and cannot get from a render.
    cdp_port = free_port()
    process = subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--headless=new",
            "--hide-scrollbars",
            f"--remote-debugging-port={cdp_port}",
            f"--window-size={WINDOW[0]},{WINDOW[1]}",
            bridge.viewer_url,
        ],
        stdout=log,
        stderr=log,
    )
    try:
        await bridge.wait_for_viewer(40)
        server.use_bridge(bridge)
        _CDP = (cdp_port, bridge.viewer_url)
        yield bridge
    finally:
        _CDP = None
        process.terminate()
        subprocess.run(
            ["pkill", "-f", f"user-data-dir={profile}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.close()
        await bridge.stop()
        shutil.rmtree(profile, ignore_errors=True)


async def page_screenshot(path: Path) -> Path:
    """Photograph the whole browser page over CDP, chrome and all.

    `snapshot()` captures Mol*'s canvas. This captures the tab: the icon rail,
    the sequence strip, the panels — the parts of the viewer a reader has to
    recognise before any render means anything.
    """
    import base64

    import aiohttp

    if _CDP is None:
        raise RuntimeError("page_screenshot() only works inside viewer()")
    port, url = _CDP
    want = url.split("?")[0].rstrip("/")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/json") as response:
            targets = await response.json()
        # Exact URL match: a substring match also picks up the blank second tab,
        # which screenshots as a white rectangle and reports success.
        pages = [
            t
            for t in targets
            if t.get("type") == "page"
            and t.get("url", "").split("?")[0].rstrip("/") == want
        ]
        if not pages:
            raise RuntimeError(f"no viewer page on the CDP endpoint; saw {targets}")
        async with session.ws_connect(
            pages[0]["webSocketDebuggerUrl"], max_msg_size=64 * 1024 * 1024
        ) as ws:
            await ws.send_json({"id": 1, "method": "Page.captureScreenshot"})
            async for message in ws:
                payload = json.loads(message.data)
                if payload.get("id") == 1:
                    data = payload.get("result", {}).get("data")
                    if not data:
                        raise RuntimeError(f"CDP returned no image: {str(payload)[:300]}")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(base64.b64decode(data))
                    return path
    raise RuntimeError("CDP closed before answering")


async def ground_colour() -> str:
    """The canvas colour a fresh load leaves behind, measured off a capture.

    `background()` refuses to be called with no arguments, so there is no way
    to read the current ground back without first setting it. Sampling the
    modal colour of a small capture asks the same question of the pixels.
    """
    with scratch() as tmp:
        probe = await capture(tmp / "ground.png", pixels=200)
        frame = np.asarray(Image.open(probe).convert("RGB")).reshape(-1, 3)
        colours, counts = np.unique(frame, axis=0, return_counts=True)
        red, green, blue = colours[counts.argmax()]
        return f"#{red:02x}{green:02x}{blue:02x}"


async def load(pdb_id: str, **kwargs: Any) -> None:
    """Clear whatever was there and load one structure, the production way."""
    await server.clear_viewer()
    await server.fetch_structure(pdb_id, **kwargs)


# -- the figures ---------------------------------------------------------------

FIGURES: dict[str, Callable[[], Awaitable[list[Path]]]] = {}


def figure(name: str) -> Callable[[Callable[[], Awaitable[list[Path]]]], Any]:
    def register(fn: Callable[[], Awaitable[list[Path]]]) -> Any:
        FIGURES[name] = fn
        return fn

    return register


@figure("first-structure")
async def first_structure() -> list[Path]:
    """What a novice sees after two calls and nothing else."""
    await load("1ubq")
    return [trim(await capture(IMAGES / "first-structure.png", pixels=900))]


@figure("representations")
async def representations() -> list[Path]:
    """The shapes a molecule can be drawn as."""
    shown = [
        "cartoon",
        "ball-and-stick",
        "spacefill",
        "molecular-surface",
        "gaussian-surface",
        "putty",
        "backbone",
        "line",
        "point",
        "ellipsoid",
    ]
    await load("1ubq")
    # `auto` is the viewer's handle for what the load preset drew. Hidden once,
    # rather than per tile: `show()` rebuilds the component named `fig`, so each
    # representation replaces the last instead of stacking on it.
    await server.hide(SCENE)
    with scratch() as tmp:
        tiles = []
        for name in shown:
            await server.show(selection="polymer", name="fig", representation=name)
            tiles.append((name, await capture(tmp / f"{name}.png")))
        return [contact_sheet(IMAGES / "representations.png", tiles, columns=5)]


@figure("color-themes")
async def color_themes() -> list[Path]:
    """The same fold, coloured by ten different things."""
    themes = [
        "chain-id",
        "secondary-structure",
        "element-symbol",
        "hydrophobicity",
        "residue-name",
        "molecule-type",
        "uncertainty",
        "occupancy",
        "partial-charge",
        "illustrative",
    ]
    await load("1ubq")
    await server.hide(SCENE)
    with scratch() as tmp:
        tiles = []
        for theme in themes:
            await server.show(
                selection="polymer", name="fig", representation="cartoon", color=theme
            )
            tiles.append((theme, await capture(tmp / f"{theme}.png")))
        return [contact_sheet(IMAGES / "color-themes.png", tiles, columns=5)]


@figure("presets")
async def presets() -> list[Path]:
    """Every named recipe, on one molecule."""
    live = await server.capabilities()
    await load("1ubq")
    # Measured once, before any preset has touched the canvas. Reloading the
    # structure does NOT put the ground back: it is a canvas property, not a
    # property of what is loaded. The first version of this figure assumed it
    # did, and `cinematic` left its near-black ground behind for the two tiles
    # that came after it — including the one labelled `default`, which is the
    # tile a reader trusts to show them the way back.
    ground = await ground_colour()
    # `scaffold` refuses on an experimental structure and `plddt` on anything
    # without a confidence column; both are ubiquitin. They get their own
    # figure, on a predicted model, where they mean something.
    # `active-site` needs a handle naming the site and `hide-sidechains` needs
    # sidechains already drawn; both refuse rather than draw something
    # misleading, and both are shown in their own right elsewhere in the docs.
    # `scaffold` refuses on an experimental structure and `plddt` on anything
    # without a confidence column — ubiquitin is both.
    skip = {"scaffold", "plddt", "active-site", "hide-sidechains"}
    names = [n for n in live["presets"] if n not in skip]
    with scratch() as tmp:
        tiles = []
        for name in names:
            # Reloading between tiles rather than resetting: presets set
            # lighting, ground, shading and effects, and `default` only puts
            # back what is *drawn*. Without a clean slate each tile inherits
            # the last one's rig and the sheet stops being a comparison.
            await load("1ubq")
            await server.background(color=ground)
            await server.lighting(rig="standard")
            try:
                await server.preset(name)
            except Exception as exc:  # reported, never swallowed
                print(f"   preset {name!r} refused: {exc}")
                continue
            tiles.append((name, await capture(tmp / f"{name}.png")))
        return [contact_sheet(IMAGES / "presets.png", tiles, columns=5)]


@figure("lighting")
async def lighting() -> list[Path]:
    """Six rigs on a surface, where lighting actually shows."""
    await load("1ubq")
    await server.hide(SCENE)
    await server.show(selection="polymer", name="fig", representation="molecular-surface")
    await server.background(color="#1a1a1e")
    with scratch() as tmp:
        tiles = []
        for rig in ["standard", "flat", "three-point", "rim", "ring", "studio"]:
            await server.lighting(rig=rig)
            tiles.append((rig, await capture(tmp / f"{rig}.png")))
        return [
            contact_sheet(
                IMAGES / "lighting.png",
                tiles,
                columns=3,
                ground=(26, 26, 30),
                ink=(235, 235, 235),
            )
        ]


@figure("shading")
async def shading() -> list[Path]:
    """Five shading styles, including the two see-through ones."""
    await load("1ubq")
    await server.hide(SCENE)
    await server.show(selection="polymer", name="fig", representation="molecular-surface")
    await server.background(color="#ffffff")
    with scratch() as tmp:
        tiles = []
        for style in ["normal", "cel", "flat", "xray", "xray-inverted"]:
            await server.shading(style=style, name="fig")
            tiles.append((style, await capture(tmp / f"{style}.png")))
        return [contact_sheet(IMAGES / "shading.png", tiles, columns=5)]


@figure("materials")
async def materials() -> list[Path]:
    """Five finishes, on a surface under a light that shows a highlight."""
    await load("1ubq")
    await server.hide(SCENE)
    await server.show(selection="polymer", name="fig", representation="molecular-surface")
    await server.background(color="#101014")
    await server.lighting(rig="three-point")
    with scratch() as tmp:
        tiles = []
        for finish in ["matte", "satin", "glossy", "metallic", "chrome"]:
            await server.material(finish=finish, name="fig")
            tiles.append((finish, await capture(tmp / f"{finish}.png")))
        return [
            contact_sheet(
                IMAGES / "materials.png",
                tiles,
                columns=5,
                ground=(16, 16, 20),
                ink=(235, 235, 235),
            )
        ]


@figure("print-finishes")
async def print_finishes() -> list[Path]:
    """The four print finishes, beside the render they were made from."""
    await load("1ubq")
    await server.hide(SCENE)
    await server.show(
        selection="polymer",
        name="fig",
        representation="spacefill",
        color="element-symbol",
    )
    await server.background(color="#ffffff")
    with scratch() as tmp:
        tiles = [("no finish", await capture(tmp / "plain.png", pixels=520))]
        for finish in ["cross-hatch", "hedcut", "cyanotype", "spot-ink-plates"]:
            tiles.append(
                (finish, await capture(tmp / f"{finish}.png", pixels=520, finish=finish))
            )
        return [contact_sheet(IMAGES / "print-finishes.png", tiles, columns=5)]


@figure("zinc-site")
async def zinc_site() -> list[Path]:
    """The README's opening figure: one question, answered as a picture."""
    await load("1ca2")
    await server.preset("publication-cartoon")
    await server.select("byres (polymer within 5 of resn ZN) or resn ZN", name="site")
    await server.show(
        handle="site", representation="ball-and-stick", color="element-symbol"
    )
    await server.focus("site")
    return [trim(await capture(IMAGES / "zinc-site.png", pixels=1000))]


@figure("interface")
async def interface() -> list[Path]:
    """Two chains, and the residues that actually touch."""
    await load("1hho")
    await server.preset("publication-cartoon")
    await server.color("#d8d4cc", name=SCENE)
    result = await server.interface("A", "B")
    handles = result["handles"]
    await server.show(
        handle=handles["a"], representation="ball-and-stick", color="#c0504d"
    )
    await server.show(
        handle=handles["b"], representation="ball-and-stick", color="#4f81bd"
    )
    await server.orient()
    return [trim(await capture(IMAGES / "interface.png", pixels=1000))]


@figure("views")
async def views() -> list[Path]:
    """The one-call views, each on a structure that suits it."""
    with scratch() as tmp:
        tiles = []

        await load("3ptb")
        await server.ligand_view("BEN")
        tiles.append(('ligand_view("BEN")', await capture(tmp / "ligand.png")))

        await load("3ptb")
        await server.pocket_view("BEN")
        tiles.append(('pocket_view("BEN")', await capture(tmp / "pocket.png")))

        await load("1hho")
        await server.interface_view("A", "B")
        tiles.append(('interface_view("A","B")', await capture(tmp / "iface.png")))

        await load("1ubq")
        await server.mutation_view("K48R", chain="A")
        tiles.append(('mutation_view("K48R")', await capture(tmp / "mutation.png")))

        await load("3ptb")
        await server.pharmacophore_view("BEN")
        tiles.append(('pharmacophore_view("BEN")', await capture(tmp / "pharm.png")))

        # Crambin, for its three disulfides. crosslink_view refuses outright on
        # a structure with neither a disulfide nor a metal — which ubiquitin is.
        await load("1crn")
        await server.crosslink_view(distance=2.5)
        tiles.append(("crosslink_view()", await capture(tmp / "crosslink.png")))

        return [contact_sheet(IMAGES / "views.png", tiles, columns=3, share_frame=False)]


@figure("plddt")
async def plddt() -> list[Path]:
    """A predicted model, coloured and widened by its own confidence."""
    await load("P69905")
    await server.preset("plddt")
    return [trim(await capture(IMAGES / "plddt.png", pixels=900))]


@figure("assembly")
async def assembly() -> list[Path]:
    """The deposited coordinates beside the molecule as it exists."""
    with scratch() as tmp:
        tiles = []
        for choice in ("asymmetric", "biological"):
            await load("1hho", assembly=choice)
            await server.preset("publication-cartoon")
            await server.orient()
            tiles.append((choice, await capture(tmp / f"{choice}.png", pixels=520)))
        return [contact_sheet(IMAGES / "assembly.png", tiles, columns=2)]


@figure("superpose")
async def superpose() -> list[Path]:
    """Two states of the same enzyme, on top of each other."""
    await load("1ake")
    await server.fetch_structure("4ake", name="4ake")
    await server.superpose("1ake", "4ake")
    await server.orient()
    return [trim(await capture(IMAGES / "superpose.png", pixels=1000))]


@figure("electrostatics")
async def electrostatics() -> list[Path]:
    """A surface coloured by charge: what the molecule looks like to a ligand."""
    await load("3ptb")
    await server.hide(SCENE)
    await server.show(
        selection="polymer", name="surf", representation="molecular-surface"
    )
    await server.electrostatics()
    await server.color_by_potential(handle="surf", domain=[-5, 5])
    await server.background(color="#ffffff")
    return [trim(await capture(IMAGES / "electrostatics.png", pixels=900))]


@figure("viewer-tab")
async def viewer_tab() -> list[Path]:
    """The browser tab itself, so a first-time reader knows what they are seeing."""
    await load("1ca2")
    await server.preset("publication-cartoon")
    await server.select("byres (polymer within 5 of resn ZN) or resn ZN", name="site")
    await server.show(
        handle="site", representation="ball-and-stick", color="element-symbol"
    )
    # The whole molecule, not the site: this figure exists to show the viewer's
    # furniture, and a close-up crops out the thing it is meant to point at.
    await server.reset_view()
    shot = await page_screenshot(IMAGES / "viewer-tab.png")
    if _ink(shot) < 0.02:
        raise Blank(f"viewer-tab.png is {_ink(shot):.4f} inked — the page never painted")
    return [publish(Image.open(shot), shot)]


@figure("sigma-vs-absolute")
async def sigma_vs_absolute() -> list[Path]:
    """The contour-unit trap, drawn: the same number read two ways."""
    import urllib.request

    with scratch() as tmp:
        emd = tmp / "emd_3488.map.gz"
        urllib.request.urlretrieve(
            "https://ftp.ebi.ac.uk/pub/databases/emdb/structures/"
            "EMD-3488/map/emd_3488.map.gz",
            emd,
        )
        await load("5me2")
        await server.preset("publication-cartoon")
        info = await server.load_volume(str(emd), name="hb", provenance="measured")
        print(f"   EMD-3488 sigma={info.get('sigma')} mean={info.get('mean')}")

        tiles = []
        await server.isosurface(name="hb", level=0.09, unit="absolute", opacity=0.5)
        tiles.append(
            (
                "level=0.09, unit='absolute'  ← the published level",
                await capture(tmp / "abs.png", pixels=520),
            )
        )
        # The same 0.09, read as sigma. On this map that is far below the
        # author's level, so the contour swallows the model in noise. Nothing
        # about the call looks wrong, which is the entire point.
        await server.isosurface(name="hb", level=0.09, unit="sigma", opacity=0.5)
        tiles.append(
            (
                "level=0.09, unit='sigma'  ← the same number, wrong unit",
                await capture(tmp / "sig.png", pixels=520),
            )
        )
        return [
            contact_sheet(
                IMAGES / "sigma-vs-absolute.png", tiles, columns=2, share_frame=False
            )
        ]


@figure("custom-theme")
async def custom_theme() -> list[Path]:
    """A number protean computed, turned into a colour theme of your own."""
    await load("1ubq")
    await server.hide(SCENE)
    await server.show(
        selection="polymer", name="fold", representation="molecular-surface"
    )
    burial = await server.sasa()
    field = await server.define_field(
        "burial", values=burial["residues"], key="relative", palette="blue-white-red"
    )
    print(f"   define_field reached {field.get('residues', field)} residues")
    await server.color("burial", name="fold")
    await server.background(color="#ffffff")
    return [trim(await capture(IMAGES / "custom-theme.png", pixels=900))]


@figure("selections")
async def selections() -> list[Path]:
    """Six selections on one fold, so the language has something to point at."""
    written = [
        ("ss H", "helix"),
        ("ss S", "strand"),
        ("resi 70-76", "the C-terminal tail"),
        ("sidechain and not hydro", "every side chain"),
        ("name CA", "one atom per residue"),
        ("byres (polymer within 5 of resi 76)", "what the tail touches"),
    ]
    await load("1ubq")
    await server.hide(SCENE)
    # The whole fold stays as a pale ghost under every tile, so the highlight
    # reads as "this part of that" rather than as six unrelated fragments.
    await server.show(
        selection="polymer", name="ghost", representation="cartoon", color="#d9d5cf"
    )
    await server.background(color="#ffffff")
    with scratch() as tmp:
        tiles = []
        for expression, gloss in written:
            await server.show(
                selection=expression,
                name="picked",
                representation="ball-and-stick",
                color="#c0504d",
            )
            tiles.append(
                (f"{expression}   — {gloss}", await capture(tmp / f"{gloss}.png"))
            )
        return [contact_sheet(IMAGES / "selections.png", tiles, columns=3)]


@figure("effects")
async def effects() -> list[Path]:
    """The screen-space effects, each against the same scene with it off."""
    await load("1ubq")
    await server.hide(SCENE)
    await server.show(selection="polymer", name="fig", representation="molecular-surface")
    await server.background(color="#ffffff")
    await server.lighting(rig="three-point")
    off = dict.fromkeys(
        ("outline", "occlusion", "shadow", "depth_of_field", "bloom", "sharpening"),
        False,
    )
    with scratch() as tmp:
        await server.effects(**off)
        tiles = [("everything off", await capture(tmp / "off.png"))]
        for switch in ("occlusion", "outline", "shadow", "sharpening"):
            await server.effects(**{**off, switch: True})
            tiles.append((f"{switch}=True", await capture(tmp / f"{switch}.png")))
        return [contact_sheet(IMAGES / "effects.png", tiles, columns=5)]


@figure("labels")
async def labels() -> list[Path]:
    """A measurement and its labels — the part a figure caption usually claims."""
    await load("1ca2")
    await server.preset("publication-cartoon")
    # Taking the scene over rather than drawing on top of it: the load preset
    # draws every ordered water, and a few hundred red dots over an active site
    # is the difference between a figure and a screenshot.
    await server.hide(SCENE)
    await server.show(
        selection="polymer", name="fold", representation="cartoon", color="#cfd8d2"
    )
    await server.select("resn ZN", name="zinc")
    await server.select("byres (polymer within 2.6 of resn ZN)", name="site")
    await server.show(
        handle="site", representation="ball-and-stick", color="element-symbol"
    )
    await server.show(
        handle="zinc", representation="spacefill", color="#7d7d8a", size=0.55
    )
    await server.label(name="site", level="residue")
    await server.select("chain A and resi 94 and name NE2", name="ne2")
    await server.measure(kind="distance", names=["ne2", "zinc"])
    await server.focus("site")
    return [trim(await capture(IMAGES / "labels.png", pixels=900))]


@figure("volume")
async def volume() -> list[Path]:
    """A cryo-EM map with its model in it, contoured two ways."""
    import urllib.request

    with scratch() as tmp:
        # EMD-3488: haemoglobin at 1.3 MB, small enough to fetch for a figure,
        # and deposited with 5ME2 fitted into it. Its author-recommended
        # contour is published as an ABSOLUTE 0.09, not as sigma — the exact
        # confusion `isosurface(unit=...)` exists to make impossible.
        emd = tmp / "emd_3488.map.gz"
        urllib.request.urlretrieve(
            "https://ftp.ebi.ac.uk/pub/databases/emdb/structures/"
            "EMD-3488/map/emd_3488.map.gz",
            emd,
        )
        await load("5me2")
        await server.preset("publication-cartoon")
        await server.load_volume(str(emd), name="hb", provenance="measured")

        tiles = []
        await server.isosurface(name="hb", level=0.09, unit="absolute", opacity=0.45)
        tiles.append(("level=0.09, unit='absolute'", await capture(tmp / "surface.png")))
        await server.isosurface(name="hb", level=0.09, unit="absolute", style="mesh")
        tiles.append(("the same contour as a mesh", await capture(tmp / "mesh.png")))
        return [contact_sheet(IMAGES / "volume.png", tiles, columns=2)]


@figure("boil")
async def boil() -> list[Path]:
    """Four poses of the stop-motion boil, and every pose accumulated into one."""
    await load("1ubq")
    await server.preset("textbook")
    with scratch() as tmp:
        frames = tmp / "poses"
        result = await server.boil(
            directory=str(frames), frames=8, hold=2, width=700, trails=True
        )
        poses = sorted(p for p in frames.glob("*.png") if p.name != "exposure.png")
        if len(poses) < 4:
            raise Blank(f"boil wrote {len(poses)} frames, expected at least 4")
        # Every second frame: `hold=2` means consecutive frames are the same
        # pose, and a strip of duplicates would show a boil that is not moving.
        picked = [(f"pose {n + 1}", poses[n * 2]) for n in range(4)]
        sheet = contact_sheet(IMAGES / "boil-poses.png", picked, columns=4)

        exposure = frames / "exposure.png"
        if not exposure.is_file():
            raise Blank(f"boil reported trails but wrote no exposure.png ({result})")
        return [sheet, trim(Path(shutil.copy(exposure, IMAGES / "boil-trails.png")))]


async def run(selected: list[str] | None) -> int:
    wanted = selected or list(FIGURES)
    unknown = [name for name in wanted if name not in FIGURES]
    if unknown:
        raise SystemExit(
            f"No such figure: {', '.join(unknown)}. Have: {', '.join(FIGURES)}"
        )

    failures = 0
    async with viewer():
        for name in wanted:
            print(f"-- {name}")
            try:
                for made in await FIGURES[name]():
                    print(f"   {made.relative_to(REPO)}  ({_ink(made):.3f} inked)")
            except Exception as exc:  # one bad figure must not end a twenty-figure run
                failures += 1
                print(f"   FAILED: {type(exc).__name__}: {exc}")
    if failures:
        print(f"\n{failures} figure(s) failed.")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", metavar="NAME", help="figures to rebuild")
    parser.add_argument("--list", action="store_true", help="name every figure and exit")
    args = parser.parse_args()
    if args.list:
        print("\n".join(FIGURES))
        return
    raise SystemExit(asyncio.run(run(args.only)))


if __name__ == "__main__":
    main()
