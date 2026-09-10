#!/usr/bin/env python3
"""
measure_materials.py -- read the material sheet back off the film.

Every number below is printed next to WHAT IT WOULD READ IF THE EFFECT WERE
ABSENT. The previous run's post-mortem found a metric (outline_contrast) that
collapsed on a black ground regardless of what the ink did, and the agent
reported the collapse as a confirmation. A metric with no stated null is not a
measurement, it is a decoration.
"""

import json
import os

# Where materials_render.py writes by default. Override with the first
# positional argument. This was an absolute path into one machine's agent
# scratchpad, carrying a username and a long-dead session id.
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = (
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "research", "renders", "materials"
    )
)

# ------------------------------------------------------------------ manifests
man = {"panels": []}
seen = set()
for mf in ("manifest.json", "manifest_rest.json", "manifest_extra.json"):
    p = os.path.join(OUT, mf)
    if not os.path.exists(p):
        continue
    d = json.load(open(p))
    for k, v in d.items():
        if k in ("panels", "pairs"):
            continue
        # A partial run writes zeros for the calibrations it skipped. Letting
        # those overwrite a real measurement is how a report ends up quoting
        # "sasa clumps = 0" for a panel that grew sixty thousand.
        if (
            isinstance(v, dict)
            and isinstance(man.get(k), dict)
            and not any(v.values())
            and any(man[k].values())
        ):
            continue
        man[k] = v
    for pan in d["panels"]:
        if pan["id"] in seen:
            man["panels"] = [x for x in man["panels"] if x["id"] != pan["id"]]
        seen.add(pan["id"])
        man["panels"].append(pan)
    man.setdefault("pairs", [])
    man["pairs"] += d.get("pairs", [])
man["panels"].sort(key=lambda p: p["id"])
PAN = {p["id"]: p for p in man["panels"] if p["status"] == "ok"}
print(f"panels in merged manifest: {sorted(PAN)}")


def load(pid):
    return (
        np.asarray(Image.open(PAN[pid]["file"]).convert("RGB"), dtype=np.float64) / 255.0
    )


def lum(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def sat(a):
    mx, mn_ = a.max(axis=-1), a.min(axis=-1)
    return np.where(mx > 1e-6, (mx - mn_) / np.maximum(mx, 1e-6), 0.0)


def blur(x, r):
    return (
        np.asarray(
            Image.fromarray((np.clip(x, 0, 1) * 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(r)
            ),
            dtype=np.float64,
        )
        / 255.0
    )


def dilate(b, k):
    return (
        np.asarray(
            Image.fromarray((b * 255).astype(np.uint8)).filter(
                ImageFilter.MaxFilter(2 * k + 1)
            )
        )
        > 127
    )


def erode(b, k):
    return (
        np.asarray(
            Image.fromarray((b * 255).astype(np.uint8)).filter(
                ImageFilter.MinFilter(2 * k + 1)
            )
        )
        > 127
    )


# ---------------------------------------------------------------------- mask
MASK = (
    np.asarray(
        Image.open(os.path.join(OUT, "_instrument_mask_hero.png")).convert("L"),
        dtype=np.float64,
    )
    / 255.0
    > 0.5
)
INSIDE = erode(MASK, 3)
NEAR = dilate(MASK, 9) & ~dilate(MASK, 3)
print(
    f"mask: {MASK.mean() * 100:.2f}% of frame, interior {INSIDE.sum()} px, "
    f"ground ring {NEAR.sum()} px"
)

# The mask was rendered at the style-space camera. Prove it still lands on the
# subject HERE, rather than assuming it: the carrier's silhouette is a strong
# luminance step, so the mask edge should sit on a gradient ridge.
_c = lum(load("M00"))
_g = np.hypot(*np.gradient(_c))
_band = dilate(MASK, 2) & ~erode(MASK, 2)
print(
    f"mask registration: mean |grad| on the mask edge = {_g[_band].mean():.4f} "
    f"vs {_g[INSIDE].mean():.4f} inside and {_g[NEAR].mean():.4f} on the "
    f"ground. If the mask were mis-registered these three would be equal."
)

# ============================================================ per-panel table
R = {}
for pid in sorted(PAN):
    a = load(pid)
    L, S = lum(a), sat(a)
    hp = np.abs(L - blur(L, 1.6))  # local high-frequency energy
    gx, gy = np.gradient(L)
    gm = np.hypot(gx, gy)
    R[pid] = {
        "id": pid,
        "name": PAN[pid]["name"],
        "lum_subject": float(L[INSIDE].mean()),
        "lum_ground": float(L[NEAR].mean()),
        "subject_contrast": float(abs(L[INSIDE].mean() - L[NEAR].mean())),
        "sat_subject": float(S[INSIDE].mean()),
        "hf_energy": float(hp[INSIDE].mean()),
        # TONAL separation cannot see a subject that matches the ground in mean
        # value but is covered in fuzz. This one can: how much more local
        # texture the subject carries than the bare ground beside it.
        "hf_contrast": float(hp[INSIDE].mean() / max(hp[NEAR].mean(), 1e-9)),
        "edge_density": float((gm[INSIDE] > 0.035).mean()),
        "lum_p05": float(np.percentile(L[INSIDE], 5)),
        "lum_p95": float(np.percentile(L[INSIDE], 95)),
        "seconds": PAN[pid].get("seconds"),
        "instances": PAN[pid].get("instances"),
    }

# The mask is the CARRIER's silhouette. Panels that grow real geometry spill
# past it, so their "ground ring" is partly subject and their tonal separation
# is understated. Measure that contamination instead of hand-waving it.
_c00 = lum(load("M00"))
for pid in R:
    # Only meaningful for panels that kept the carrier's ground and world. A
    # panel that DECLARED a ground change has a legitimately different ring,
    # and scoring it as contamination would be an instrument reading its own
    # experimental variable back as an error.
    if set(PAN[pid].get("varies", [])) & {"ground", "world", "lights"}:
        R[pid]["ring_contamination"] = None
        continue
    dl = np.abs(lum(load(pid)) - _c00)
    R[pid]["ring_contamination"] = float((dl[NEAR] > 0.03).mean())

hdr = [
    ("id", 5),
    ("name", 22),
    ("lum_subject", 12),
    ("lum_ground", 11),
    ("subject_contrast", 17),
    ("sat_subject", 12),
    ("hf_energy", 10),
    ("hf_contrast", 12),
    ("edge_density", 13),
    ("ring_contamination", 19),
]
print("\n" + "".join(f"{h:>{w}}" for h, w in hdr))
for pid in sorted(R):
    r = R[pid]
    print(
        "".join(
            f"{('n/a' if r[h] is None else r[h] if isinstance(r[h], str) else round(r[h], 4)):>{w}}"
            for h, w in hdr
        )
    )

# ==================================================== 1. does the ground work?
print("\n" + "=" * 78)
print("1. THE GROUND. subject_contrast = |mean luminance inside the silhouette")
print("   - mean luminance of the ground ring just outside it|.")
print("   NULL: the previous run's paper-on-white panel measured 0.0158, which")
print("   is a blank frame. Anything near that value here is the same failure.")
pale = [p for p in ("M05", "M08", "M11", "M13", "M14") if p in R]
dark = [p for p in ("M10", "M12", "X2", "X3") if p in R]
print(
    "   palest materials : "
    + ", ".join(f"{p}={R[p]['subject_contrast']:.4f}" for p in pale)
)
print(
    "   darkest materials: "
    + ", ".join(f"{p}={R[p]['subject_contrast']:.4f}" for p in dark)
)
worst = min((R[p]["subject_contrast"], p) for p in pale + dark)
print(
    f"   worst separation among those: {worst[1]} at {worst[0]:.4f} "
    f"({worst[0] / 0.0158:.1f}x the failed panel)"
)
allworst = sorted((R[p]["subject_contrast"], p) for p in R if p != "I1")[:4]
print("   WORST FOUR ON THE WHOLE SHEET (tonal separation only):")
for v, p in allworst:
    rc = R[p]["ring_contamination"]
    rcs = "n/a (declared a ground change)" if rc is None else f"{rc * 100:.0f}%"
    print(
        f"     {p} {R[p]['name']:<22} tonal {v:.4f}  "
        f"but texture contrast {R[p]['hf_contrast']:.1f}x the bare ground, "
        f"ring contamination {rcs}"
    )
print("   WHAT THE TONAL METRIC CANNOT SEE: a subject whose MEAN value matches")
print("   the ground but which is covered in texture, or which spills real")
print("   geometry over the mask edge so that its own 'ground ring' is subject.")
print("   hf_contrast is the second opinion. NULL for hf_contrast: 1.0 means")
print("   the subject carries no more local texture than the empty ground.")

if all(k in R for k in ("G1", "M11", "G2")):
    print("\n   THE GROUND SWEEP (same bone material, ground albedo alone):")
    for pid, g in (("G1", "0.78 light"), ("M11", "0.32 sheet"), ("G2", "0.03 dark")):
        r = R[pid]
        print(
            f"     {pid} ground {g:>11}: subject {r['lum_subject']:.4f}  "
            f"ground {r['lum_ground']:.4f}  separation {r['subject_contrast']:.4f}"
        )
    print("   NULL: if the ground were doing nothing, these three separations")
    print("   would be equal. The spread between them IS the ground's contribution.")

# ============================================ 2. glass: glass-ness or chroma?
print("\n" + "=" * 78)
print("2. GLASS. M01 (achromatic) vs M02 (amber): one RGB triple apart.")
if all(k in R for k in ("M00", "M01", "M02", "M03")):
    for p in ("M00", "M01", "M02", "M03"):
        r = R[p]
        print(
            f"   {p} {r['name']:<20} sat {r['sat_subject']:.4f}   "
            f"edge_density {r['edge_density']:.4f}   hf {r['hf_energy']:.4f}"
        )
    print("   NULL for sat_subject: if the amber rejection had been about")
    print("   glass-ness rather than chroma, M01 and M02 would sit at similar")
    print("   saturation. NULL for edge_density: if glass did not fragment the")
    print("   form, a glass panel would match the carrier M00.")
    ed0 = R["M00"]["edge_density"]
    for p in ("M01", "M02", "M03"):
        print(f"     {p} edge_density / carrier = {R[p]['edge_density'] / ed0:.2f}x")

# ==================================== 3. the AO-driven claims, against the field
print("\n" + "=" * 78)
print("3. THE AO-DRIVEN MATERIALS, tested against the emitted AO field (I1).")
if "I1" in R:
    AO = lum(load("I1"))
    aoi = AO[INSIDE]
    q = np.quantile(aoi, np.linspace(0, 1, 6))
    q[0] -= 1e-9
    binid = np.digitize(aoi, q[1:-1])
    print(
        f"   AO field inside the silhouette: min {aoi.min():.3f} "
        f"p50 {np.median(aoi):.3f} max {aoi.max():.3f}"
    )
    print("   Quintile 0 = most sheltered, quintile 4 = most exposed. The bins")
    print("   are QUANTILES, so they hold equal pixel counts and the ordering")
    print("   is by rank; but note the display values bunch near the top (AgX")
    print("   compresses the highlights), so quintiles 2-4 are all 'fairly")
    print("   exposed' and the real discrimination lives in quintile 0 vs 4.")

    def by_ao(vals, label, absent):
        m = [float(vals[binid == b].mean()) for b in range(5)]
        print(f"   {label}")
        print("     sheltered -> exposed: " + "  ".join(f"{v:.4f}" for v in m))
        print(f"     ABSENT WOULD READ: {absent}")
        return m

    carrier = load("M00")
    Lc, Sc = lum(carrier)[INSIDE], sat(carrier)[INSIDE]

    # ---------------------------------------------------------------- FLAT
    # First attempt at the crazing test measured "how much darker than its
    # neighbourhood" over the whole subject, and subtracted the carrier as a
    # control. It came back NEGATIVE in every bin -- the statistic was
    # dominated by ribbon boundaries and self-shadow lines, which are far
    # stronger than a hairline crack and which the coloured carrier has MORE
    # of than a flat white glaze. That is a metric that cannot see its
    # subject; it is discarded, not reported.
    #
    # The fix: only look at pixels the carrier says are locally FLAT -- smooth
    # ribbon interior, no shading edge, no silhouette. Whatever fine dark
    # structure survives there is the crazing, because nothing else in the
    # scene puts marks on a flat lit face.
    gcx, gcy = np.gradient(lum(carrier))
    gc = np.hypot(gcx, gcy)
    FLAT = INSIDE & (gc < np.percentile(gc[INSIDE], 35))
    aof = AO[FLAT]
    # The AO field has heavy ties (AgX bunches the exposed end), so equal-count
    # quantile edges can coincide and leave an EMPTY bin, which averages to nan
    # and would print as a hole in the middle of the table. Drop duplicate
    # edges and report how many bins actually survived.
    qf = np.unique(np.quantile(aof, np.linspace(0, 1, 6)))
    qf[0] -= 1e-9
    binf = np.digitize(aof, qf[1:-1])
    NB = int(binf.max()) + 1
    counts = [int((binf == b).sum()) for b in range(NB)]
    print(
        f"\n   FLAT region (carrier locally smooth): {FLAT.sum()} px, "
        f"{FLAT.sum() / INSIDE.sum() * 100:.1f}% of the subject."
    )
    print(f"   AO bins that survived de-duplication: {NB}, pixel counts {counts}")

    def by_ao_flat(img2d, label, absent):
        v = img2d[FLAT]
        m = [float(v[binf == b].mean()) for b in range(NB)]
        print(f"   {label}")
        print("     sheltered -> exposed: " + "  ".join(f"{x:.5f}" for x in m))
        print(f"     ratio sheltered/exposed = {m[0] / max(m[-1], 1e-12):.2f}x")
        print(f"     ABSENT WOULD READ: {absent}")
        return m

    def excess(m, mc, panel, control):
        """The part of the signal the control does not already explain."""
        e = [m[i] - mc[i] for i in range(len(m))]
        print(
            f"     EXCESS over {control} (this is the effect itself): "
            + "  ".join(f"{x:.5f}" for x in e)
        )
        r = e[0] / max(e[-1], 1e-12)
        print(
            f"     excess sheltered/exposed = {r:.2f}x   "
            f"NULL 1.00x = the effect is spread evenly, not collecting"
        )
        R[panel]["ao_excess_ratio"] = r
        return r

    if "M13" in R:
        L13 = lum(load("M13"))
        crack13 = np.maximum(blur(L13, 2.0) - L13, 0.0)
        print("\n   M13 porcelain_crazed -- crack signal = how much darker a")
        print("   pixel is than its 2 px neighbourhood, ON FLAT FACES ONLY.")
        m13 = by_ao_flat(
            crack13,
            "crack signal by AO quintile:",
            "flat across quintiles -- crazing not AO-coupled",
        )
        # POSITIVE CONTROL for the instrument itself: M11 bone is a smooth
        # pale material with no crack network at all. If the metric reported a
        # comparable signal there, it would be measuring something else.
        if "M11" in R:
            L11 = lum(load("M11"))
            c11 = np.maximum(blur(L11, 2.0) - L11, 0.0)
            m11 = by_ao_flat(
                c11,
                "  INSTRUMENT CHECK, same statistic on M11 "
                "bone (a smooth material with NO cracks):",
                "if this matched M13, the metric is not seeing cracks",
            )
            excess(m13, m11, "M13", "M11 bone")
        R["M13"]["ao_ratio"] = m13[0] / max(m13[-1], 1e-12)

    if "M15" in R:
        a15 = load("M15")
        S15 = sat(a15)[INSIDE]
        print("\n   M15 rust -- rust is saturated orange, bare steel is grey, so")
        print("   SATURATION is a direct readout of how much metal has gone.")
        m = by_ao(
            S15,
            "saturation by AO quintile:",
            "flat across quintiles -- pitting not bound to shelter",
        )
        mb = by_ao(
            Sc,
            "  same statistic on the CARRIER (lighting control):",
            "the confound from shading alone",
        )
        print(
            f"     sheltered/exposed ratio: {m[0] / max(m[4], 1e-9):.2f}x   "
            f"carrier's own ratio: {mb[0] / max(mb[4], 1e-9):.2f}x"
        )
        R["M15"]["ao_ratio"] = m[0] / max(m[4], 1e-9)

    if "M14" in R:
        L14 = lum(load("M14"))
        hp14 = np.abs(L14 - blur(L14, 1.6))
        print("\n   M14 frost -- the rime is a crystalline BUMP, so its")
        print("   signature is fine texture on an otherwise smooth ice face.")
        m14 = by_ao_flat(
            hp14,
            "fine texture by AO quintile, ON FLAT FACES:",
            "flat -- frost spread evenly instead of collecting",
        )
        if "M08" in R:
            L08 = lum(load("M08"))
            h08 = np.abs(L08 - blur(L08, 1.6))
            m08 = by_ao_flat(
                h08,
                "  INSTRUMENT CHECK, same statistic on M08 alabaster (smooth, no rime):",
                "if this matched M14, the metric is not seeing frost",
            )
            excess(m14, m08, "M14", "M08 alabaster")
        R["M14"]["ao_ratio"] = m14[0] / max(m14[-1], 1e-12)

    # ------------------------------------------- 4. the data binding, Tier 4
    if all(k in R for k in ("M17", "M18")):
        print("\n" + "=" * 78)
        print("4. THE DATA BINDING. M17 moss density bound to per-residue solvent")
        print("   accessibility; M18 the same moss at uniform density, clump count")
        print("   matched to within 2%.")
        cal = man.get("moss_calibration", {})
        print(
            f"   clump counts: sasa={cal.get('sasa_clumps')} "
            f"uniform={cal.get('uniform_clumps')}  (calibrated, not assumed)"
        )

        def greenfrac(pid):
            a = load(pid)
            r, g, b = a[..., 0], a[..., 1], a[..., 2]
            return (g > r + 0.02) & (g > b + 0.02) & (sat(a) > 0.12)

        g17, g18 = greenfrac("M17"), greenfrac("M18")
        print(
            f"   green coverage inside the silhouette: "
            f"M17 {g17[INSIDE].mean():.4f}   M18 {g18[INSIDE].mean():.4f}"
        )
        print("   NULL: if the binding did no visible work these would be equal,")
        print("   because the two panels grew the same NUMBER of clumps.")
        for pid, gm_ in (("M17", g17), ("M18", g18)):
            prof = [float(gm_[INSIDE][binid == b].mean()) for b in range(5)]
            print(
                f"     {pid} green coverage by AO quintile "
                f"(sheltered->exposed): " + "  ".join(f"{v:.3f}" for v in prof)
            )
        print("   AO is not SASA, but both measure how open a piece of surface is,")
        print("   so a real exposure binding should tilt M17's profile toward the")
        print("   exposed end relative to M18's. NULL: identical profiles.")
        p17 = np.array([float(g17[INSIDE][binid == b].mean()) for b in range(5)])
        p18 = np.array([float(g18[INSIDE][binid == b].mean()) for b in range(5)])
        print(
            f"     M17/M18 ratio, most sheltered quintile: "
            f"{p17[0] / max(p18[0], 1e-9):.2f}x"
        )
        print(
            f"     M17/M18 ratio, most exposed quintile  : "
            f"{p17[4] / max(p18[4], 1e-9):.2f}x"
        )

    if all(k in R for k in ("S17", "S18")):
        print("\n   THE SAME PAIR AT A TENTH THE DENSITY (S17 vs S18). If the")
        print("   dense pair came back null because the moss had covered")
        print("   everything, the sparse pair must separate. If it does not,")
        print("   the saturation diagnosis is wrong and the binding is inert.")
        cal = man.get("moss_calibration_sparse", {})
        print(
            f"   clump counts: sasa={cal.get('sasa_clumps')} "
            f"uniform={cal.get('uniform_clumps')}"
        )

        def greenfrac2(pid):
            a = load(pid)
            r, g, b = a[..., 0], a[..., 1], a[..., 2]
            return (g > r + 0.02) & (g > b + 0.02) & (sat(a) > 0.12)

        s17, s18 = greenfrac2("S17"), greenfrac2("S18")
        print(
            f"   green coverage inside the silhouette: "
            f"S17 {s17[INSIDE].mean():.4f}   S18 {s18[INSIDE].mean():.4f}   "
            f"ratio {s17[INSIDE].mean() / max(s18[INSIDE].mean(), 1e-9):.2f}x"
        )
        q17 = np.array([float(s17[INSIDE][binid == b].mean()) for b in range(5)])
        q18 = np.array([float(s18[INSIDE][binid == b].mean()) for b in range(5)])
        print(
            "     S17 coverage by AO quintile (sheltered->exposed): "
            + "  ".join(f"{v:.3f}" for v in q17)
        )
        print(
            "     S18 coverage by AO quintile (sheltered->exposed): "
            + "  ".join(f"{v:.3f}" for v in q18)
        )
        r_sh = q17[0] / max(q18[0], 1e-9)
        r_ex = q17[4] / max(q18[4], 1e-9)
        print(
            f"     S17/S18 sheltered {r_sh:.2f}x   exposed {r_ex:.2f}x   "
            f"tilt {r_ex / max(r_sh, 1e-9):.2f}x"
        )
        print("     NULL: tilt 1.00x means the exposure field moved no moss.")

# ======================================== 5. the category channel, and grain
print("\n" + "=" * 78)
print("5. GLASS AS A CATEGORY CHANNEL (M19 vs the carrier M00).")
if all(k in R for k in ("M00", "M19")):
    d = np.abs(load("M19") - load("M00")).max(axis=-1)
    changed = d > 0.02
    print(
        f"   pixels that changed at all: {changed.mean() * 100:.2f}% of frame, "
        f"{changed[INSIDE].mean() * 100:.2f}% of the subject"
    )
    print("   NULL: the haem groups are 172 of 4779 atoms (3.6%) and are mostly")
    print("   buried, so a correct category render changes a SMALL, LOCALISED")
    print("   fraction. Changing most of the subject would mean the protein's")
    print("   material moved too; changing ~0% would mean nothing was applied.")

print("\n6. GRAIN AND TEXTURE VISIBILITY at 1000 px (hf_energy inside the mask).")
print("   NULL: the carrier M00 is a smooth material, so its hf_energy is what")
print("   'no visible surface texture' reads as. A texture that dissolved at")
print("   this resolution would land on the carrier's number.")
if "M00" in R:
    h0 = R["M00"]["hf_energy"]
    for pid in sorted(R):
        if pid == "I1":
            continue
        print(
            f"     {pid:<4} {R[pid]['name']:<24} hf {R[pid]['hf_energy']:.4f}  "
            f"= {R[pid]['hf_energy'] / h0:.2f}x carrier"
        )

json.dump(
    {"panels": R, "manifest": {k: v for k, v in man.items() if k != "panels"}},
    open(os.path.join(OUT, "measurements.json"), "w"),
    indent=2,
)

# ------------------------------------------------------------- contact sheet
sheet_ids = [p for p in sorted(PAN) if p != "I1"]
COLS, TW = 4, 470
th = int(TW * man["resolution"][1] / man["resolution"][0])
pad, lab = 14, 34
rowsn = (len(sheet_ids) + COLS - 1) // COLS
sheet = Image.new(
    "RGB", (COLS * (TW + pad) + pad, rowsn * (th + lab + pad) + pad), (248, 248, 248)
)
d = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 20)
except Exception:
    font = ImageFont.load_default()
for i, pid in enumerate(sheet_ids):
    c, rw = i % COLS, i // COLS
    x, y = pad + c * (TW + pad), pad + rw * (th + lab + pad)
    sheet.paste(
        Image.open(PAN[pid]["file"]).convert("RGB").resize((TW, th), Image.LANCZOS),
        (x, y),
    )
    d.rectangle([x, y, x + TW - 1, y + th - 1], outline=(205, 205, 205))
    d.text(
        (x + 2, y + th + 6), f"{pid}  {PAN[pid]['name']}", fill=(20, 20, 20), font=font
    )
sheet.save(os.path.join(OUT, "_contact_sheet.png"))
print(f"\ncontact sheet: {sheet.size} -> _contact_sheet.png")

# ------------------------------------------------- the ground demonstration
if all(k in PAN for k in ("G1", "M11", "G2")):
    TW2 = 640
    th2 = int(TW2 * 0.75)
    trip = Image.new("RGB", (3 * TW2 + 4 * pad, th2 + lab + 2 * pad), (248, 248, 248))
    dd = ImageDraw.Draw(trip)
    for i, (pid, cap) in enumerate(
        [
            ("G1", "G1  ground albedo 0.78"),
            ("M11", "M11  ground albedo 0.32 (the sheet)"),
            ("G2", "G2  ground albedo 0.03"),
        ]
    ):
        x = pad + i * (TW2 + pad)
        trip.paste(
            Image.open(PAN[pid]["file"]).convert("RGB").resize((TW2, th2), Image.LANCZOS),
            (x, pad),
        )
        dd.text(
            (x + 2, pad + th2 + 6),
            f"{cap}   sep {R[pid]['subject_contrast']:.4f}",
            fill=(20, 20, 20),
            font=font,
        )
    trip.save(os.path.join(OUT, "_ground_demonstration.png"))
    print(f"ground demonstration: {trip.size} -> _ground_demonstration.png")
