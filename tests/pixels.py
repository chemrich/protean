"""Assertions about what a render actually put on screen.

Phase 4's deliverables are invisible to a return value, and — unusually for
this project — mostly invisible to screenshot byte size too. Size cannot
separate a transparent background from a black one, barely moves when an
outline is switched on, and moves the *wrong way* when opacity is reduced. So
decision 12 pays for a harness that reads pixels, and this is it.

Everything here is a pure function over a decoded image, which is what lets the
harness be tested without a browser: `test_pixels.py` runs it against synthetic
images in the fast CI job, including the negative cases that prove each
detector actually fires. `test_render_differential.py` then runs the same
functions against real Mol* output.

**Opacity does not show up in the alpha channel.** On an opaque canvas a
half-transparent cartoon is composited during rendering, so every output pixel
still comes back with alpha 255 — the effect is that drawn pixels move *toward*
the background colour. Measure it with `mean_distance_from(background)`, or
render on a transparent background and measure alpha. Reaching for
`transparent_fraction` on an opaque canvas will report 0.0 for every opacity
setting and look like a passing test.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

RGBA = tuple[int, int, int, int]

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")

# 8-bit channel noise below this is not a colour difference. Renders are not
# bit-exact across GL backends: SwiftShader and a real GPU disagree in the low
# bits, and antialiasing puts intermediate values along every edge.
TOLERANCE = 8


@dataclass(frozen=True)
class Render:
    """A decoded image: an (height, width, 4) uint8 array of RGBA pixels."""

    pixels: np.ndarray
    """DPI as recorded *in the file*, not inferred — None when absent.

    A PNG carries physical resolution in its optional `pHYs` chunk. Mol* does
    not write one, so this is None for anything the viewer produces today; it
    becomes the check that `snapshot()` really stamped the DPI it claims.
    """
    dpi: tuple[float, float] | None = None

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)


def decode(source: str | bytes) -> Render:
    """Decode a PNG from raw bytes, a base64 string, or a data URI.

    The viewer returns a data URI and the `screenshot` tool hands back bytes,
    so tests should not have to care which one they are holding.
    """
    if isinstance(source, str):
        if source.startswith("data:"):
            header, _, payload = source.partition(",")
            if "base64" not in header:
                raise ValueError(f"Unexpected image encoding: {header}")
        else:
            payload = source
        raw = base64.b64decode(payload)
    else:
        raw = source

    with Image.open(io.BytesIO(raw)) as image:
        dpi = image.info.get("dpi")
        # Convert *before* asarray: a palette or RGB PNG has no alpha channel
        # and would otherwise arrive with the wrong last axis, so every alpha
        # assertion would read colour data and quietly succeed.
        pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)

    return Render(
        pixels=pixels,
        dpi=(float(dpi[0]), float(dpi[1])) if dpi else None,
    )


def corners(render: Render) -> dict[str, RGBA]:
    """The four corner pixels, which is where a background shows itself.

    All four are returned rather than one, because a gradient or skybox
    background differs corner to corner and a test asserting on a single corner
    would pass on a background it never actually checked.
    """
    p = render.pixels
    picks = {
        "top-left": p[0, 0],
        "top-right": p[0, -1],
        "bottom-left": p[-1, 0],
        "bottom-right": p[-1, -1],
    }
    return {name: _rgba(value) for name, value in picks.items()}


def background(render: Render, tolerance: int = TOLERANCE) -> RGBA:
    """The single background colour, refusing to answer if there isn't one.

    A uniform background is the premise of `coverage` and of every
    background-colour assertion. When the corners disagree — a gradient, a
    skybox, or a molecule running off the edge of the frame — returning one of
    them would silently pick a winner, so this raises instead and the caller
    has to use `corners()` and say what it means.
    """
    found = corners(render)
    values = np.array(list(found.values()), dtype=np.int16)
    if int(np.abs(values - values[0]).max()) > tolerance:
        raise ValueError(f"Corners disagree, so there is no single background: {found}")
    # The median, not the first corner: within-tolerance backend noise is real
    # and returning whichever corner happened to be sampled first would hand
    # every downstream comparison a value a few bits off the true background.
    return _rgba(np.median(values, axis=0).round())


def opaque(render: Render) -> bool:
    """True when every pixel is fully opaque."""
    return bool((render.pixels[:, :, 3] == 255).all())


def transparent_fraction(render: Render) -> float:
    """Fraction of pixels that are fully transparent.

    This is the transparent-background check. It is *not* an opacity check —
    see the module docstring.
    """
    alpha = render.pixels[:, :, 3]
    return float((alpha == 0).mean())


def coverage(render: Render, of: RGBA | None = None, tolerance: int = TOLERANCE) -> float:
    """Fraction of the frame that is not background — i.e. how much was drawn.

    The guard against protean's oldest failure: a render that succeeds and
    draws nothing. On a transparent background "drawn" means any non-zero
    alpha; otherwise it means any pixel differing from the background colour.
    """
    if of is None:
        of = background(render)
    if of[3] == 0:
        return float((render.pixels[:, :, 3] > 0).mean())
    difference = np.abs(render.pixels.astype(np.int16) - np.array(of, dtype=np.int16))
    return float((difference.max(axis=2) > tolerance).mean())


def color_fraction(render: Render, color: RGBA, tolerance: int = TOLERANCE) -> float:
    """Fraction of pixels matching *color*.

    Outline is the intended use: it draws in a colour you choose, so setting it
    to something absent from the palette and counting the result measures the
    effect directly rather than inferring it from a silhouette.
    """
    difference = np.abs(render.pixels.astype(np.int16) - np.array(color, dtype=np.int16))
    return float((difference.max(axis=2) <= tolerance).mean())


def close(a: RGBA, b: RGBA, tolerance: int = TOLERANCE) -> bool:
    """Are two colours the same, allowing for the backend that drew them?

    Exists because comparing two RGBA tuples with `==` is the one place this
    harness invites a bit-exact assertion, and every other function here takes
    a tolerance for a reason. Two corners of a gradient came back as
    (255, 0, 1) and (255, 1, 1) under CI's SwiftShader while being identical on
    a real GPU — a green channel one bit apart, and a test that passed locally
    and failed in CI.
    """
    return all(abs(int(x) - int(y)) <= tolerance for x, y in zip(a, b, strict=True))


def difference(a: Render, b: Render, tolerance: int = TOLERANCE) -> float:
    """Fraction of pixels that differ between two renders of the same scene.

    The measure for anything that changes how a molecule is *lit* or shaded
    rather than what is drawn. A lighting rig moves colour across the whole
    surface while leaving the silhouette exactly where it was, so `coverage`
    barely moves and only a direct comparison shows the change.

    Pairs with `coverage`: different pixels but the same coverage is the
    signature of a shading change, and distinguishes it from having drawn
    something else entirely.
    """
    if a.size != b.size:
        raise ValueError(f"Renders are different sizes: {a.size} vs {b.size}")
    gap = np.abs(a.pixels.astype(np.int16) - b.pixels.astype(np.int16))
    return float((gap.max(axis=2) > tolerance).mean())


def mean_distance_from(render: Render, color: RGBA, tolerance: int = TOLERANCE) -> float:
    """Mean RGB distance from *color*, over the pixels that differ from it.

    This is the opacity measure. Lowering a representation's alpha over an
    opaque canvas blends it toward the background, so the drawn pixels stay at
    alpha 255 and simply get closer to the background colour: a lower number
    here means a more transparent representation.

    Averaging only over differing pixels keeps the answer independent of how
    much of the frame the molecule happens to fill, so a camera move does not
    read as an opacity change.
    """
    pixels = render.pixels.astype(np.int16)
    difference = np.abs(pixels[:, :, :3] - np.array(color[:3], dtype=np.int16))
    drawn = difference.max(axis=2) > tolerance
    if not drawn.any():
        return 0.0
    return float(difference[drawn].mean())


def _rgba(value: object) -> RGBA:
    r, g, b, a = (int(v) for v in np.asarray(value).tolist())
    return (r, g, b, a)
