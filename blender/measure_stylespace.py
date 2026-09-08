#!/usr/bin/env python3
"""
measure.py -- read the rendered panels back and measure what the research
predicted, instead of asserting it. Also builds a contact sheet.
"""

import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = (
    "/private/tmp/claude-501/-Users-charlie-code-protean/"
    "b549de8d-56f4-456e-9de7-823d312c525f/scratchpad/stylespace"
)
man = json.load(open(os.path.join(OUT, "manifest.json")))
panels = [p for p in man["panels"] if p["status"] == "ok"]


def load(p):
    im = Image.open(p["file"]).convert("RGB")
    return np.asarray(im, dtype=np.float64) / 255.0


def lum(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def sat(a):
    mx, mn_ = a.max(axis=-1), a.min(axis=-1)
    return np.where(mx > 1e-6, (mx - mn_) / np.maximum(mx, 1e-6), 0.0)


# ---------------------------------------------------------------- mask
mask_path = os.path.join(OUT, "_instrument_mask_hero.png")
MASK = None
if os.path.exists(mask_path):
    m = np.asarray(Image.open(mask_path).convert("L"), dtype=np.float64) / 255.0
    MASK = m > 0.5
    print(f"mask coverage: {MASK.mean() * 100:.2f}% of frame")


def dilate(b, k):
    im = Image.fromarray((b * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.MaxFilter(2 * k + 1))) > 127


def erode(b, k):
    im = Image.fromarray((b * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.MinFilter(2 * k + 1))) > 127


report = {}
rows = []
for p in panels:
    a = load(p)
    L, S = lum(a), sat(a)
    r = {
        "id": p["id"],
        "name": p["name"],
        "lum_mean": round(float(L.mean()), 4),
        "lum_p05": round(float(np.percentile(L, 5)), 4),
        "lum_p95": round(float(np.percentile(L, 95)), 4),
        "sat_mean": round(float(S.mean()), 4),
        "sat_p99": round(float(np.percentile(S, 99)), 4),
        "seconds": p.get("seconds"),
    }
    if MASK is not None and MASK.shape == L.shape:
        inside = erode(MASK, 3)
        band = dilate(MASK, 2) & ~erode(MASK, 2)  # the silhouette line
        outside = dilate(MASK, 9) & ~dilate(MASK, 3)  # ground just beyond
        r["lum_subject"] = round(float(L[inside].mean()), 4)
        r["lum_edge"] = round(float(L[band].mean()), 4)
        r["lum_ground_near"] = round(float(L[outside].mean()), 4)
        # Two DIFFERENT claims, which A5 shows are not the same claim:
        #   outline_contrast  -- did the ink line itself vanish?
        #   subject_contrast  -- did the molecule lose its edge?
        # An ink line can disappear while the subject/ground tonal step gets
        # STRONGER, in which case the edge is not lost at all.
        r["outline_contrast"] = round(abs(r["lum_edge"] - r["lum_ground_near"]), 4)
        r["subject_contrast"] = round(abs(r["lum_subject"] - r["lum_ground_near"]), 4)
        r["sat_subject"] = round(float(S[inside].mean()), 4)
        r["sat_ground_near"] = round(float(S[outside].mean()), 4)
    report[p["id"]] = r
    rows.append(r)

hdr = (
    "id",
    "lum_mean",
    "lum_subject",
    "lum_edge",
    "lum_ground_near",
    "outline_contrast",
    "subject_contrast",
    "sat_subject",
    "sat_ground_near",
    "seconds",
)
print("\n" + "  ".join(f"{h:>15}" for h in hdr))
for r in rows:
    print("  ".join(f"{r.get(h, '-')!s:>15}" for h in hdr))

# --------------------------------------------------- the specific predictions
print("\n=== predictions under test ===")


def cmp(a, b, key, claim):
    if a in report and b in report and key in report[a] and key in report[b]:
        va, vb = report[a][key], report[b][key]
        print(
            f"{claim}\n    {a}.{key}={va}   {b}.{key}={vb}   "
            f"ratio={vb / va if va else float('nan'):.3f}"
        )


cmp(
    "A3",
    "A5",
    "outline_contrast",
    "A5 claim 1: the ink outline should lose contrast on a dark ground",
)
cmp(
    "A3",
    "A5",
    "subject_contrast",
    "A5 claim 2: and the molecule should therefore LOSE ITS EDGE",
)
cmp("C3", "C4", "sat_subject", "C4: saturation moved onto the largest object")
# B3 vs B5 differ ONLY in the receding copies, and the hero mask cannot see a
# single one of them -- measuring lum_subject there compares two identical
# heroes and reports "no difference" for a panel pair built to differ. Derive
# the copy region instead: B2 is the same scene, same DOF, WITHOUT copies, so
# the pixels where B3 departs from B2 are exactly the copies.
if all(k in report for k in ("B2", "B3", "B5")):
    b2 = lum(load(next(p for p in panels if p["id"] == "B2")))
    b3 = lum(load(next(p for p in panels if p["id"] == "B3")))
    b5 = lum(load(next(p for p in panels if p["id"] == "B5")))
    copies = np.abs(b3 - b2) > 0.02
    if MASK is not None and MASK.shape == b2.shape:
        copies &= ~dilate(MASK, 4)
    n = int(copies.sum())
    print(f"\ncopy region: {n} px ({100 * n / copies.size:.2f}% of frame)")
    if n > 500:
        g3, g5 = float(b3[copies].mean()), float(b5[copies].mean())
        bg = float(b2[copies].mean())  # what the background is, there
        print(f"  background behind the copies : {bg:.4f}")
        print(
            f"  B3 copies (fade to world hue): {g3:.4f}  |delta to bg| {abs(g3 - bg):.4f}"
        )
        print(
            f"  B5 copies (fade to black)    : {g5:.4f}  |delta to bg| {abs(g5 - bg):.4f}"
        )
        verdict = "CONFIRMED" if abs(g5 - bg) > abs(g3 - bg) * 1.3 else "NOT SHOWN"
        print(
            f"  B5 'wrong move' prediction (copies separate from the "
            f"background instead of merging into it): {verdict}"
        )
        report["_copies"] = {
            "px": n,
            "bg": round(bg, 4),
            "B3": round(g3, 4),
            "B5": round(g5, 4),
            "verdict": verdict,
        }

if "A3" in report and "A5" in report:
    o3, o5 = report["A3"]["outline_contrast"], report["A5"]["outline_contrast"]
    s3, s5 = report["A3"]["subject_contrast"], report["A5"]["subject_contrast"]
    v1 = "CONFIRMED" if o5 < o3 * 0.6 else ("PARTIAL" if o5 < o3 else "CONTRADICTED")
    v2 = "CONFIRMED" if s5 < s3 * 0.8 else ("PARTIAL" if s5 < s3 else "CONTRADICTED")
    print(f"\nA5 claim 1 (outline vanishes): {v1}   {o3} -> {o5}")
    print(f"A5 claim 2 (molecule loses its edge): {v2}   {s3} -> {s5}")
    if v1 in ("CONFIRMED", "PARTIAL") and v2 == "CONTRADICTED":
        print(
            "  => The outline really does vanish, but the subject/ground "
            "tonal step REPLACES it. Losing the outline is not the same as "
            "losing the edge."
        )

json.dump(report, open(os.path.join(OUT, "measurements.json"), "w"), indent=2)

# ---------------------------------------------------------------- contact sheet
COLS, TW = 4, 470
order = list(panels)
th = int(TW * man["resolution"][1] / man["resolution"][0])
pad, lab = 14, 30
rowsn = (len(order) + COLS - 1) // COLS
sheet = Image.new(
    "RGB", (COLS * (TW + pad) + pad, rowsn * (th + lab + pad) + pad), (250, 250, 250)
)
d = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 19)
except Exception:
    font = ImageFont.load_default()
for i, p in enumerate(order):
    c, rw = i % COLS, i // COLS
    x = pad + c * (TW + pad)
    y = pad + rw * (th + lab + pad)
    im = Image.open(p["file"]).convert("RGB").resize((TW, th), Image.LANCZOS)
    sheet.paste(im, (x, y))
    d.rectangle([x, y, x + TW - 1, y + th - 1], outline=(210, 210, 210))
    d.text((x + 2, y + th + 5), f"{p['id']}  {p['name']}", fill=(20, 20, 20), font=font)
sheet.save(os.path.join(OUT, "_contact_sheet.png"))
print(f"\ncontact sheet: {os.path.join(OUT, '_contact_sheet.png')} {sheet.size}")
