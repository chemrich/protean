"""Five candidate treatments for `hedcut`, for one round of plates.

**Not shipped, and not a bracket.** Importing this registers five extra keys in
`FINISHES`; nothing else in the product reads them and nothing should be merged
from here without a decision.

## Why this is an approach round

The shipped `hedcut` answers half of one observation. *"Hedcut is also way too
coarse. They read like bad modern art"* has a size half — answered in #143, 17 px
to 5 px — and a mechanism half, which was declined on the grounds that

    "A hedcut rules one direction and thickens it, and that is the style
     rather than a defect"                              hatching.py:920-923

That claim does not survive checking. Sprouls, who invented the form in 1979,
Randy Glass, who draws them now, and the WSJ's own tooling team all describe
**stipple plus minimal line**, with the line reserved for hair, cloth and hard
shadow. At magnification the dots on skin sit in rows that *curve with the
structure* — arcs around the orbital rim, rows descending the nose, a single
file along the jaw — and tone is carried by dot size and row density rather than
by the width of a fixed-angle rule. Where a hedcut does rule (a shoulder strap),
the angle is the object's cross-direction and it bends with the object.

So the fixed-angle swelling rule protean calls a hedcut is a **woodcut hatch
wearing the name**. The question is which kind of mark it should be, not how
fine.

## Why the obvious bracket would have told us nothing

Measured on the suite's own `_spheres()` fixture at plate size, rim lift being
`inked[rim].mean() - inked[subject].mean()`:

    shipped hedcut                     +0.067
    bowed, relief 2.5                  +0.067
    bowed, relief 6.0                  +0.068

**The warp does not move the rim at all on this mechanism.** A relief sweep —
the obvious three plates — would have been the brush-volume round again: three
pictures of the same rejection. What does move it is the *burr*, already in
`_Lozenge` and documented there as "the entire rim-landing mechanism", and
changing the mark family:

    debanded + burr, no warp           +0.154
    burred area stipple                +0.147
    dot-and-burin                      +0.232

The suite's own bar for a form-following finish is +0.12, with hedcut's +0.086
named as the control it is not supposed to reach.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .hatching import (
    _TONE_CURVE,
    FINISHES,
    _blur,
    _Engraving,
    _Frame,
    _hash01,
    _Lozenge,
    _shade,
)

# `hedcut`'s shipped interval, so every candidate is judged at one mark size and
# the only thing varying between plates is the mechanism.
_HED = 1 / 378


@dataclass(frozen=True, kw_only=True)
class _Debanded(_Engraving):
    """B — the swell, with the six terraces taken out and nothing else changed.

    `_Engraving` quantises tone into `bands` and fills each with a stroke of
    `width = step * (level / bands)`. That is two mechanisms at once: a swelling
    line, and a posterisation into six flat steps. This isolates the second by
    testing a continuous duty against the carrier instead, so the stroke width
    follows tone smoothly.

    It is the ablation control. If the terracing is what reads as "bad modern
    art", this plate says so on its own, and the rest of the set is beside the
    point.
    """

    def marks(self, frame: _Frame) -> np.ndarray:
        darkness = np.power(np.clip(1.0 - frame.luma, 0.0, 1.0), _TONE_CURVE)
        step = self._step(frame)
        height, width = frame.shape
        y, x = np.ogrid[0:height, 0:width]
        radians = np.deg2rad(self.angles[0])
        phase = np.asarray((x * np.cos(radians) + y * np.sin(radians)) / step)
        # Constant duty against the carrier, the craft rule `_Lozenge` states:
        # a fraction `darkness` of every interval takes ink, whatever the
        # interval is in pixels.
        residual = np.abs(phase - np.rint(phase))
        return np.asarray((residual < 0.5 * darkness) & ~frame.is_paper)


@dataclass(frozen=True, kw_only=True)
class _Bowed(_Debanded):
    """C — the incumbent mechanism, surviving, with the form added under it.

    `_Debanded` plus `_Lozenge`'s warped carrier: the ruled plane is pushed
    aside by the recovered light, so one direction still thickens with tone but
    the rules bow around each atom rather than running straight through it.

    This is the answer that keeps the most of what ships. Its rim lift says it
    does not answer the rim half — included because "keep the mechanism, add the
    bow" is the reasonable first idea and it should be looked at, not argued
    about.
    """

    relief: float = 2.5
    smooth: float = 1 / 380
    achromatic: float = 0.12

    def marks(self, frame: _Frame) -> np.ndarray:
        shade = _blur(_shade(frame.rgb, self.achromatic), frame.diagonal * self.smooth)
        darkness = np.power(np.clip(1.0 - frame.luma, 0.0, 1.0), _TONE_CURVE)
        step = self._step(frame)
        height, width = frame.shape
        y, x = np.ogrid[0:height, 0:width]
        radians = np.deg2rad(self.angles[0])
        phase = np.asarray(
            (x * np.cos(radians) + y * np.sin(radians)) / step + self.relief * shade
        )
        residual = np.abs(phase - np.rint(phase))
        return np.asarray((residual < 0.5 * darkness) & ~frame.is_paper)


@dataclass(frozen=True, kw_only=True)
class _DotAndBurin(_Lozenge):
    """D — dots in rows that bend with the form, line where the form turns.

    The real idiom, as closely as this engine reaches it. The body of every
    atom is stipple; the dots sit on the *same warped carrier* the hatches use,
    so their rows curve with the recovered light exactly as the rows on a
    hedcut's cheek curve with the cheek. Line is kept for the two places a
    hedcut keeps it: where the form turns hard (the rim of an atom, the seam
    where two meet) and in deep shadow.

    `split_lo`/`split_hi` are the slope ramp that hands tone from dots to line;
    `shadow` is the tone past which the burin takes over whatever the form is
    doing, which is the real idiom's other division.
    """

    dot_pitch: float = 1 / 470
    dot: float = 0.34
    jitter: float = 0.30
    lattice_angle: float = 33.0
    split_lo: float = 8.0
    split_hi: float = 24.0
    shadow: float = 0.45

    def _dots(self, frame: _Frame, rest: np.ndarray) -> np.ndarray:
        height, width = frame.shape
        pitch = max(2.0, frame.diagonal * self.dot_pitch)
        radians = np.deg2rad(self.lattice_angle)
        y, x = np.ogrid[0:height, 0:width]
        u = (x * np.cos(radians) + y * np.sin(radians)) / pitch
        v = (-x * np.sin(radians) + y * np.cos(radians)) / pitch
        cell_u = np.floor(u).astype(np.int64)
        cell_v = np.floor(v).astype(np.int64)
        dots = np.zeros((height, width), dtype=bool)
        # Three by three so a jittered dot from a neighbouring cell still lands.
        for du in (-1, 0, 1):
            for dv in (-1, 0, 1):
                au, av = cell_u + du, cell_v + dv
                ju = au + 0.5 + (_hash01(au, av, 1) - 0.5) * 2 * self.jitter
                jv = av + 0.5 + (_hash01(au, av, 2) - 0.5) * 2 * self.jitter
                # Dot *area* grows with the share it carries, so the field can
                # close to solid. A density-only stipple cannot: measured, it
                # caps at 0.283 coverage at black and fails the suite's bar.
                radius = self.dot * np.clip(0.6 + 1.1 * rest, 0.0, 1.6)
                dots |= (np.hypot(u - ju, v - jv) < radius) & (
                    _hash01(au, av, 3) < np.sqrt(rest)
                )
        return dots

    def _split(self, frame: _Frame) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
        shade, step, coverage = self._fields(frame)
        gradient_y, gradient_x = np.gradient(shade)
        slope = np.hypot(gradient_x, gradient_y) * frame.diagonal
        turn = np.clip(
            (slope - self.split_lo) / (self.split_hi - self.split_lo), 0.0, 1.0
        )
        deep = np.clip((coverage - self.shadow) / max(1e-6, 1.0 - self.shadow), 0.0, 1.0)
        return shade, step, coverage, np.maximum(turn, deep)

    def marks(self, frame: _Frame) -> np.ndarray:
        shade, step, coverage, share = self._split(frame)
        line_coverage = np.clip(coverage * share, 0.0, 1.0)
        phase = self._phase(frame, shade, step, self.angle)
        ink = np.abs(phase - np.rint(phase)) < 0.5 * line_coverage
        # The dots take whatever the line did not, through the incommensurate
        # union the hatches already use, so the two families sum to the
        # coverage asked for rather than double-inking.
        rest = np.clip(
            (coverage - line_coverage) / np.maximum(1.0 - line_coverage, 1e-6),
            0.0,
            1.0,
        )
        return np.asarray((ink | self._dots(frame, rest)) & ~frame.is_paper)


@dataclass(frozen=True, kw_only=True)
class _AllDots(_DotAndBurin):
    """E — the overshoot: every mark is a dot, including on the rims.

    `_DotAndBurin` with the burin branch removed. A real hedcut would still
    rule the hard shadow and the turn, so this is **deliberately past where I
    would stop** — it is here so the answer can be "less than that", which is
    information, rather than "no", which is not.

    The axis is the mark family, ruled bar to pure dot field. It is not relief,
    not size, and not the number of bands, because none of those move the thing
    the complaint was about.
    """

    def marks(self, frame: _Frame) -> np.ndarray:
        _, _, coverage, _ = self._split(frame)
        return np.asarray(self._dots(frame, coverage) & ~frame.is_paper)


#: The plate order. `hedcut` itself is plate one and is not redefined here.
CANDIDATES = {
    "hedcut-debanded": _Debanded(angles=(75.0,), cumulative=False, bands=6, spacing=_HED),
    "hedcut-bowed": _Bowed(angles=(75.0,), cumulative=False, bands=6, spacing=_HED),
    "hedcut-burin": _DotAndBurin(angle=75.0, cross=None, hold=0.55, spacing=_HED),
    "hedcut-dots": _AllDots(angle=75.0, cross=None, hold=0.55, spacing=_HED),
}

FINISHES.update(CANDIDATES)
