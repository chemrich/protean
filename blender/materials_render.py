#!/usr/bin/env python3
"""
materials_render.py -- a MATERIAL SHEET for protean.

Same subject (4HHB), same camera, same framing, same lighting rig, same ONE
mid-value warm grey ground for every panel. Only the MATERIAL changes (plus a
scatter layer where a material genuinely needs real geometry -- declared).

Built on stylespace_render.py's discipline harness: every panel snapshots nine
scene dimensions and asserts the ones it did not declare are bit-identical to
the carrier.

Run:
    blender --background --python materials_render.py -- [--only M04,M06] [--samples 64]

NEVER pass --factory-startup: it disables the MolecularNodes extension.
"""

import hashlib
import json
import math
import os
import random
import sys
import time
import traceback

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

# ---------------------------------------------------------------- args

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
ONLY = None
SAMPLES = 64
TAG = ""
for i, a in enumerate(argv):
    if a == "--only":
        ONLY = set(argv[i + 1].split(","))
    if a == "--samples":
        SAMPLES = int(argv[i + 1])
    if a == "--tag":
        TAG = argv[i + 1]

BASE = (
    "/private/tmp/claude-501/-Users-charlie-code-protean/"
    "b549de8d-56f4-456e-9de7-823d312c525f/scratchpad"
)
OUT = os.path.join(BASE, "materials")
PREV = os.path.join(BASE, "stylespace")
os.makedirs(OUT, exist_ok=True)

CACHE = os.path.expanduser("~/MolecularNodesCache")
RES = (1000, 750)
LENS = 50.0
SENSOR = 36.0
MARGIN = 0.15

# ONE ground for the whole sheet: mid-value warm grey. Chosen so a pale
# material (bone ~0.86 albedo) and a dark one (graphite ~0.035) both separate
# from it. The previous run's D1 failed at 0.92 ground vs 0.86 paper: a
# subject/ground step of 0.0158 in display luminance, i.e. a blank frame.
GROUND_RGB = (0.325, 0.300, 0.272)
GROUND_ROUGH = 0.62
WORLD_STRENGTH = 0.06

# ---------------------------------------------------------------- addon

bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.molecularnodes")
import bl_ext.blender_org.molecularnodes as mn  # noqa: E402

mn.material.Default(name="_seed_groups")
NG_MN_COLOR = bpy.data.node_groups["MN Color"]

LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(str(msg))


# ================================================================ scene setup

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
for me in list(bpy.data.meshes):
    bpy.data.meshes.remove(me)

scene = bpy.context.scene

cam_data = bpy.data.cameras.new("StyleCam")
cam_data.lens = LENS
cam_data.sensor_width = SENSOR
cam_data.clip_start = 0.1
cam_data.clip_end = 5000.0
CAM_OB = bpy.data.objects.new("StyleCam", cam_data)
scene.collection.objects.link(CAM_OB)
scene.camera = CAM_OB

canvas = mn.Canvas(engine="CYCLES", resolution=RES, template=None)
canvas.samples = SAMPLES
scene = bpy.context.scene
assert canvas.scene == scene, "Canvas is driving a different scene"
assert scene.camera == CAM_OB, "Canvas replaced the camera"

scene.render.engine = "CYCLES"
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
# Raised from the style-space run: clear glass through a tangle of ribbons needs
# far more than 4 transmission bounces or it renders as a black knot. Set once,
# held constant for every panel, so the carrier and the glass see the same
# integrator.
scene.cycles.max_bounces = 32
scene.cycles.diffuse_bounces = 4
scene.cycles.glossy_bounces = 8
scene.cycles.transmission_bounces = 24
scene.cycles.transparent_max_bounces = 16
scene.cycles.use_adaptive_sampling = True

try:
    cprefs = bpy.context.preferences.addons["cycles"].preferences
    try:
        cprefs.compute_device_type = "METAL"
    except Exception:
        pass
    try:
        cprefs.refresh_devices()
    except Exception:
        pass
    ndev = 0
    for d in getattr(cprefs, "devices", []):
        if d.type in ("METAL", "GPU"):
            d.use = True
            ndev += 1
    scene.cycles.device = "GPU" if ndev else "CPU"
    log(f"[setup] cycles device={scene.cycles.device} metal_devices={ndev}")
except Exception as e:
    log(f"[setup] GPU config skipped: {e}")

scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "None"
scene.view_settings.exposure = 0.0
VIEW_TRANSFORM = scene.view_settings.view_transform
assert VIEW_TRANSFORM == "AgX", VIEW_TRANSFORM
log(f"[setup] view_transform={VIEW_TRANSFORM} samples={SAMPLES}")

# ================================================================ subject

HERO = mn.Molecule.load(os.path.join(CACHE, "4HHB.bcif"), name="hero")
HERO_OB = HERO.object
HERO_M0 = HERO_OB.matrix_world.copy()
N_ATOMS = HERO.universe.atoms.n_atoms
assert len(HERO.position) == N_ATOMS, "vertex/atom mapping is not 1:1"
log(f"[subject] 4HHB atoms={N_ATOMS}")

SEL = {}
for key, sel in [
    ("A", "chainID A"),
    ("B", "chainID B"),
    ("C", "chainID C"),
    ("D", "chainID D"),
    ("HEM", "resname HEM"),
    ("FE", "name FE"),
    ("protein", "protein"),
    ("frame", "protein or resname HEM"),
]:
    ag = HERO.universe.select_atoms(sel)
    assert ag.n_atoms > 0, f"selection {sel!r} matched zero atoms"
    SEL[key] = ag
    log(f"[selection] {sel!r} -> {ag.n_atoms} atoms")
IDX = {k: v.indices for k, v in SEL.items()}


def style_nodes_of(ob):
    ng = ob.modifiers[0].node_group
    return [n for n in ng.nodes if n.name.startswith("Style")]


def add_style_tracked(mol, *args, **kwargs):
    ng = mol.object.modifiers[0].node_group
    before = set(ng.nodes.keys())
    mol.add_style(*args, **kwargs)
    new = [n for k, n in ng.nodes.items() if k not in before and k.startswith("Style")]
    assert len(new) == 1, f"expected 1 new style node, got {[n.name for n in new]}"
    return new[0]


ST_CARTOON = add_style_tracked(HERO, "cartoon", selection="protein")
ST_HEM = add_style_tracked(HERO, "ball_and_stick", selection="resname HEM")
for n in style_nodes_of(HERO_OB):
    if "Quality" in n.inputs:
        n.inputs["Quality"].default_value = 3
log(f"[styles] {[n.name for n in style_nodes_of(HERO_OB)]}")

# =============================================================== colours

PAL_CARRIER = {
    "A": (0.42, 0.50, 0.60),
    "C": (0.36, 0.45, 0.55),
    "B": (0.55, 0.47, 0.42),
    "D": (0.50, 0.43, 0.39),
    "HEM": (0.62, 0.34, 0.22),
    "FE": (0.78, 0.42, 0.20),
}


def write_colours(mapping, default=(0.5, 0.5, 0.5)):
    col = np.zeros((N_ATOMS, 4), dtype=np.float32)
    col[:, :3] = default
    col[:, 3] = 1.0
    for key, rgb in mapping.items():
        col[IDX[key], :3] = rgb
    HERO.store_named_attribute(col, "Color", "FLOAT_COLOR")
    return col


write_colours(PAL_CARRIER)

# ======================================================= material primitives


def new_mat(name):
    if name in bpy.data.materials:
        bpy.data.materials.remove(bpy.data.materials[name])
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    return m, nt, out


def setin(node, name, value):
    """Set a socket by name, ASSERTING it exists. A silent no-op here is the
    exact failure mode this sheet is meant to avoid: a beautiful render that
    demonstrates nothing."""
    assert name in node.inputs, (
        f"{node.bl_idname} has no input {name!r}; has {[s.name for s in node.inputs]}"
    )
    s = node.inputs[name]
    if hasattr(s.default_value, "__len__") and not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} wants a vector, got {value!r}")
    if isinstance(value, (list, tuple)) and len(s.default_value) == 4 and len(value) == 3:
        value = (*value, 1.0)
    s.default_value = value
    return s


def principled(nt, x=520, y=0, **kw):
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.location = (x, y)
    for k, v in kw.items():
        setin(b, k, v)
    return b


def mn_colour(nt, x=-1400, y=200):
    n = nt.nodes.new("ShaderNodeGroup")
    n.node_tree = NG_MN_COLOR
    n.location = (x, y)
    return n


def obj_coord(nt, x=-1500, y=-300):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (x, y)
    return tc.outputs["Object"]


def noise(nt, coord, scale, detail=6.0, roughness=0.55, distortion=0.0, x=-1200, y=-300):
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.location = (x, y)
    n.noise_dimensions = "3D"
    setin(n, "Scale", scale)
    setin(n, "Detail", detail)
    setin(n, "Roughness", roughness)
    setin(n, "Distortion", distortion)
    nt.links.new(coord, n.inputs["Vector"])
    return n


def voronoi(nt, coord, scale, feature="F1", randomness=1.0, x=-1200, y=-600):
    v = nt.nodes.new("ShaderNodeTexVoronoi")
    v.location = (x, y)
    v.feature = feature
    v.voronoi_dimensions = "3D"
    setin(v, "Scale", scale)
    setin(v, "Randomness", randomness)
    nt.links.new(coord, v.inputs["Vector"])
    return v


def maprange(nt, value_socket, fmin, fmax, tmin, tmax, x=-900, y=-300, clamp=True):
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.location = (x, y)
    mr.clamp = clamp
    setin(mr, "From Min", fmin)
    setin(mr, "From Max", fmax)
    setin(mr, "To Min", tmin)
    setin(mr, "To Max", tmax)
    nt.links.new(value_socket, mr.inputs["Value"])
    return mr.outputs["Result"]


def mathnode(nt, op, a, b=None, x=-700, y=-300, clamp=False):
    n = nt.nodes.new("ShaderNodeMath")
    n.location = (x, y)
    n.operation = op
    n.use_clamp = clamp
    if hasattr(a, "is_linked") or hasattr(a, "node"):
        nt.links.new(a, n.inputs[0])
    else:
        n.inputs[0].default_value = a
    if b is not None:
        if hasattr(b, "is_linked") or hasattr(b, "node"):
            nt.links.new(b, n.inputs[1])
        else:
            n.inputs[1].default_value = b
    return n.outputs["Value"]


def mixrgb(nt, fac, a, b, x=-500, y=200):
    """Mix two colours. fac/a/b may be sockets or literal RGB triples."""
    m = nt.nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    m.blend_type = "MIX"
    m.location = (x, y)
    if hasattr(fac, "node"):
        nt.links.new(fac, m.inputs["Factor"])
    else:
        m.inputs["Factor"].default_value = fac
    for slot, val in ((6, a), (7, b)):
        if hasattr(val, "node"):
            nt.links.new(val, m.inputs[slot])
        else:
            m.inputs[slot].default_value = (*val, 1.0)
    return m.outputs[2]


def ramp(nt, fac, stops, x=-700, y=200, interp="LINEAR"):
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.location = (x, y)
    r.color_ramp.interpolation = interp
    els = r.color_ramp.elements
    els[0].position = stops[0][0]
    els[0].color = (*stops[0][1], 1.0)
    els[1].position = stops[-1][0]
    els[1].color = (*stops[-1][1], 1.0)
    for pos, col in stops[1:-1]:
        e = els.new(pos)
        e.color = (*col, 1.0)
    nt.links.new(fac, r.inputs["Fac"])
    return r.outputs["Color"]


def ao_node(nt, distance=0.55, samples=16, x=-1400, y=-900):
    a = nt.nodes.new("ShaderNodeAmbientOcclusion")
    a.location = (x, y)
    a.samples = samples
    a.inside = False
    a.only_local = True  # the ground must not paint the molecule
    setin(a, "Distance", distance)
    return a


def bump(nt, height_socket, strength, distance=0.05, x=250, y=-500):
    b = nt.nodes.new("ShaderNodeBump")
    b.location = (x, y)
    setin(b, "Strength", strength)
    setin(b, "Distance", distance)
    nt.links.new(height_socket, b.inputs["Height"])
    return b.outputs["Normal"]


# ===================================================== the material library
#
# Scale note: 1 Blender unit = 10 A. 4HHB spans ~10 BU and ~700 px, so
#   1 BU ~ 70 px, 1 px ~ 0.014 BU.
# A grain that should read at ~3 px must be ~0.045 BU across, i.e. a Voronoi
# Scale near 22. Every texture scale below was chosen from that arithmetic,
# not from taste.

PX = 1.0 / 70.0  # one pixel, in Blender units, at this framing


def mat_carrier(name="M_carrier"):
    """The reference surface: restrained matte dielectric that DOES read the
    molecule's Color attribute. Identical in construction to the style-space
    run's A1."""
    m, nt, out = new_mat(name)
    c = mn_colour(nt)
    b = principled(
        nt,
        Roughness=0.40,
        Metallic=0.0,
        **{
            "Transmission Weight": 0.0,
            "Coat Weight": 0.03,
            "Coat Roughness": 0.10,
            "Specular IOR Level": 0.45,
        },
    )
    nt.links.new(c.outputs["Color"], b.inputs["Base Color"])
    nt.links.new(c.outputs["Alpha"], b.inputs["Alpha"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_glass(name, tint, roughness=0.02, ior=1.48):
    """Real transmissive glass. `tint` is the only thing that differs between
    the achromatic and the amber panel, so the pair answers 'did glass fail for
    being glass, or for being saturated?' directly."""
    m, nt, out = new_mat(name)
    b = principled(
        nt,
        Roughness=roughness,
        Metallic=0.0,
        IOR=ior,
        **{
            "Base Color": (*tint, 1.0),
            "Transmission Weight": 1.0,
            "Coat Weight": 0.0,
            "Specular IOR Level": 0.5,
        },
    )
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_sea_glass(name):
    """Sea glass: tumbled and frosted. Rough transmission (so light still
    passes and the form stays translucent) plus a chipped-facet bump and a
    fine frost bump, plus a translucent term so the frosting scatters rather
    than mirrors. No sharp specular: coat is off and roughness is high enough
    that caustics smear out.

    First attempt used bump distance 0.012 at strength 0.42 and rendered as
    flat mint plastic -- the frosting was invisible, so the panel would have
    claimed 'frosted' while showing 'smooth'. Deepened here."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    # tumbled chips: cell EDGES at ~6 px, so the facet boundaries read
    chip = voronoi(nt, co, 1.0 / (6.0 * PX), feature="DISTANCE_TO_EDGE", y=-600)
    coarse = bump(
        nt, chip.outputs["Distance"], strength=0.55, distance=0.055, x=180, y=-820
    )
    fine = noise(nt, co, 1.0 / (1.5 * PX), detail=4.0, y=-380)
    nrm = bump(nt, fine.outputs["Fac"], strength=1.0, distance=0.014, x=340, y=-560)
    nt.links.new(coarse, nrm.node.inputs["Normal"])

    b = principled(
        nt,
        x=520,
        y=120,
        Roughness=0.34,
        Metallic=0.0,
        IOR=1.49,
        **{
            "Base Color": (0.66, 0.80, 0.75, 1.0),
            "Transmission Weight": 0.90,
            "Coat Weight": 0.0,
            "Specular IOR Level": 0.5,
        },
    )
    nt.links.new(nrm, b.inputs["Normal"])
    tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
    tr.location = (520, -220)
    setin(tr, "Color", (0.55, 0.76, 0.70, 1.0))
    nt.links.new(nrm, tr.inputs["Normal"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (740, 0)
    mix.inputs["Fac"].default_value = 0.28
    nt.links.new(b.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


def mat_clay(name):
    """Matte, faintly waxy, low-frequency thumbprint dents. The porcelain
    neighbour, so this is the low-risk anchor of the set."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    thumb = noise(nt, co, 3.2, detail=2.0, roughness=0.4)  # ~0.31 BU dents
    grit = noise(nt, co, 1.0 / (2.0 * PX), detail=2.0, y=-520)  # tooth
    h = mathnode(
        nt,
        "ADD",
        mathnode(nt, "MULTIPLY", thumb.outputs["Fac"], 1.0, x=-950, y=-300),
        mathnode(nt, "MULTIPLY", grit.outputs["Fac"], 0.12, x=-950, y=-520),
        x=-780,
        y=-400,
    )
    nrm = bump(nt, h, strength=0.55, distance=0.10)
    col = mixrgb(
        nt,
        mathnode(nt, "MULTIPLY", thumb.outputs["Fac"], 1.0, x=-700, y=400),
        (0.485, 0.400, 0.335),
        (0.560, 0.470, 0.395),
    )
    b = principled(
        nt,
        Roughness=0.72,
        Metallic=0.0,
        **{
            "Coat Weight": 0.10,
            "Coat Roughness": 0.35,
            "Specular IOR Level": 0.30,
            "Sheen Weight": 0.12,
            "Sheen Roughness": 0.55,
        },
    )
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_sand(name):
    """Cast in sand.

    First attempt bumped the Voronoi DISTANCE-TO-EDGE at 0.030 BU on a 3 px
    cell -- a crater two thirds as deep as the cell is wide -- and rendered as
    coral, not sand. Corrected: rounded grains (1 - F1 distance, so the cell
    CENTRE is the high point), bump depth 0.008 BU which is about half a pixel,
    and a separate coarse erosion bump chained underneath so the form is worn
    as well as granular. The albedo speckle is widened so individual grains
    carry a value difference, which is what actually makes sand read as sand
    rather than as noise."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    grain = voronoi(nt, co, 1.0 / (1.9 * PX), feature="F1", randomness=1.0)
    erode = noise(nt, co, 5.0, detail=4.0, roughness=0.6, y=-1050)
    coarse = bump(nt, erode.outputs["Fac"], strength=0.6, distance=0.045, x=100, y=-1050)
    dome = mathnode(nt, "SUBTRACT", 1.0, grain.outputs["Distance"], x=-780, y=-820)
    nrm = bump(nt, dome, strength=0.9, distance=0.008, x=300, y=-820)
    nt.links.new(coarse, nrm.node.inputs["Normal"])
    # per-grain colour: Voronoi Color is constant within a cell
    speck = nt.nodes.new("ShaderNodeSeparateColor")
    speck.location = (-950, 500)
    nt.links.new(grain.outputs["Color"], speck.inputs["Color"])
    col = ramp(
        nt,
        speck.outputs["Red"],
        [
            (0.00, (0.245, 0.190, 0.115)),
            (0.34, (0.520, 0.425, 0.275)),
            (0.68, (0.700, 0.600, 0.415)),
            (1.00, (0.840, 0.760, 0.590)),
        ],
        x=-700,
        y=500,
    )
    b = principled(
        nt,
        Roughness=0.95,
        Metallic=0.0,
        **{"Specular IOR Level": 0.25, "Coat Weight": 0.0, "Sheen Weight": 0.05},
    )
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_sss(
    name,
    base,
    radius,
    sss_scale,
    weight,
    roughness,
    coat=0.0,
    mottle=None,
    veins=None,
    ao_stain=None,
):
    """Shared body for wax / alabaster / bone / jade.

    mottle: (scale, amount) faint albedo variation
    veins : (scale, distortion, amount, colour) warped low-frequency streaks
    ao_stain: (distance, amount, colour) darkening in recesses -- the
              specimen/aged register
    """
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    col = None
    if veins is not None:
        vscale, vdist, vamt, vcol = veins
        n = noise(nt, co, vscale, detail=4.0, roughness=0.6, distortion=vdist, y=200)
        f = maprange(nt, n.outputs["Fac"], 0.35, 0.62, 0.0, vamt, x=-950, y=200)
        col = mixrgb(nt, f, base, vcol, x=-700, y=200)
    if mottle is not None:
        mscale, mamt = mottle
        n2 = noise(nt, co, mscale, detail=3.0, roughness=0.5, y=-100)
        f2 = maprange(nt, n2.outputs["Fac"], 0.3, 0.7, 0.0, mamt, x=-950, y=-100)
        dark = tuple(c * 0.80 for c in base)
        col = mixrgb(nt, f2, col if col is not None else base, dark, x=-500, y=-100)
    if ao_stain is not None:
        adist, aamt, acol = ao_stain
        a = ao_node(nt, distance=adist)
        f3 = maprange(nt, a.outputs["AO"], 0.25, 0.90, aamt, 0.0, x=-1150, y=-900)
        col = mixrgb(nt, f3, col if col is not None else base, acol, x=-300, y=-400)
    b = principled(
        nt,
        Roughness=roughness,
        Metallic=0.0,
        **{
            "Base Color": (*base, 1.0),
            "Subsurface Weight": weight,
            "Subsurface Radius": radius,
            "Subsurface Scale": sss_scale,
            "Coat Weight": coat,
            "Coat Roughness": 0.12,
            "Specular IOR Level": 0.45,
        },
    )
    if col is not None:
        nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_terracotta(name):
    """Unglazed, porous, warm. Matte with a fine open tooth and a faint
    coarse blotching from the firing."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    pore = voronoi(nt, co, 1.0 / (2.2 * PX), feature="F1")
    blotch = noise(nt, co, 2.4, detail=3.0, y=-560)
    h = mathnode(
        nt,
        "ADD",
        mathnode(nt, "MULTIPLY", pore.outputs["Distance"], 1.0, x=-950, y=-600),
        mathnode(nt, "MULTIPLY", blotch.outputs["Fac"], 0.25, x=-950, y=-460),
        x=-780,
        y=-540,
    )
    nrm = bump(nt, h, strength=0.45, distance=0.020)
    col = mixrgb(
        nt,
        maprange(nt, blotch.outputs["Fac"], 0.3, 0.7, 0.0, 1.0, x=-950, y=300),
        (0.470, 0.205, 0.120),
        (0.610, 0.310, 0.185),
        x=-700,
        y=300,
    )
    b = principled(
        nt,
        Roughness=0.88,
        Metallic=0.0,
        **{
            "Specular IOR Level": 0.20,
            "Sheen Weight": 0.10,
            "Sheen Roughness": 0.7,
            "Coat Weight": 0.0,
        },
    )
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_graphite(name):
    """Very dark, semi-metallic, with a faint flake sheen. The test of whether
    a near-black subject reads at all against this ground."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    flake = noise(nt, co, 1.0 / (5.0 * PX), detail=2.0, roughness=0.4)
    rough = maprange(nt, flake.outputs["Fac"], 0.35, 0.68, 0.55, 0.24, x=-950, y=-300)
    nrm = bump(nt, flake.outputs["Fac"], strength=0.20, distance=0.006)
    col = mixrgb(
        nt,
        maprange(nt, flake.outputs["Fac"], 0.35, 0.68, 0.0, 1.0, x=-950, y=300),
        (0.026, 0.026, 0.029),
        (0.062, 0.063, 0.070),
        x=-700,
        y=300,
    )
    b = principled(nt, Metallic=0.62, **{"Specular IOR Level": 0.55, "Coat Weight": 0.0})
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(rough, b.inputs["Roughness"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_wet_stone(name):
    """Dark, smooth, wet. A full clear coat over a dark rough stone: the sheen
    lives in the coat, the body stays matte, so the silhouette stays strong and
    the highlights ride the form."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    grit = noise(nt, co, 1.0 / (2.5 * PX), detail=4.0)
    coarse = noise(nt, co, 6.0, detail=3.0, y=-520)
    h = mathnode(
        nt,
        "ADD",
        mathnode(nt, "MULTIPLY", grit.outputs["Fac"], 0.5, x=-950, y=-300),
        mathnode(nt, "MULTIPLY", coarse.outputs["Fac"], 0.5, x=-950, y=-520),
        x=-780,
        y=-400,
    )
    nrm = bump(nt, h, strength=0.30, distance=0.020)
    col = mixrgb(
        nt,
        maprange(nt, coarse.outputs["Fac"], 0.3, 0.7, 0.0, 1.0, x=-950, y=300),
        (0.038, 0.037, 0.036),
        (0.082, 0.080, 0.074),
        x=-700,
        y=300,
    )
    b = principled(
        nt,
        Roughness=0.55,
        Metallic=0.0,
        **{
            "Coat Weight": 1.0,
            "Coat Roughness": 0.045,
            "Coat IOR": 1.44,
            "Specular IOR Level": 0.4,
        },
    )
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_porcelain_crazed(name):
    """A white glaze with a fine crack network, the cracks DEEPENED where the
    ambient occlusion says the surface is in a recess. One shader; the crazing
    costs nothing extra because the AO is already being computed."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    # crack cells ~9 px across; Distance to Edge is small ON a crack
    cell = voronoi(nt, co, 1.0 / (9.0 * PX), feature="DISTANCE_TO_EDGE")
    crack = maprange(nt, cell.outputs["Distance"], 0.0, 0.035, 1.0, 0.0, x=-950, y=-600)
    a = ao_node(nt, distance=0.5)
    # 1.0 deep in a recess, 0.25 on an exposed face
    deepen = maprange(nt, a.outputs["AO"], 0.30, 0.92, 1.0, 0.25, x=-1150, y=-900)
    strength = mathnode(nt, "MULTIPLY", crack, deepen, x=-700, y=-750)
    col = mixrgb(
        nt, strength, (0.880, 0.872, 0.845), (0.190, 0.170, 0.140), x=-500, y=300
    )
    rough = maprange(nt, strength, 0.0, 1.0, 0.14, 0.72, x=-500, y=-750)
    nrm = bump(
        nt,
        mathnode(nt, "SUBTRACT", 1.0, strength, x=-300, y=-750),
        strength=0.60,
        distance=0.010,
    )
    b = principled(
        nt,
        Metallic=0.0,
        **{
            "Coat Weight": 0.85,
            "Coat Roughness": 0.06,
            "Specular IOR Level": 0.5,
            "Subsurface Weight": 0.12,
            "Subsurface Radius": (1.0, 0.9, 0.85),
            "Subsurface Scale": 0.020,
        },
    )
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(rough, b.inputs["Roughness"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_frost(name):
    """Ice in the bulk, frost accumulating in the crevices. The frost mask is
    the ambient occlusion, so the rime collects exactly where a real one would:
    in the sheltered angles."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    a = ao_node(nt, distance=0.55)
    breakup = noise(nt, co, 26.0, detail=5.0, roughness=0.6, y=-500)
    raw = maprange(nt, a.outputs["AO"], 0.34, 0.86, 1.0, 0.0, x=-1150, y=-900)
    # multiply by a noise so the rime line is crystalline, not a smooth ramp
    fmask = mathnode(
        nt,
        "MULTIPLY",
        raw,
        maprange(nt, breakup.outputs["Fac"], 0.30, 0.70, 0.35, 1.3, x=-950, y=-500),
        x=-780,
        y=-700,
        clamp=True,
    )
    cryst = voronoi(nt, co, 1.0 / (2.2 * PX), feature="F1", y=-1200)
    h = mathnode(nt, "MULTIPLY", cryst.outputs["Distance"], fmask, x=-560, y=-1000)
    nrm = bump(nt, h, strength=0.75, distance=0.016)
    col = mixrgb(nt, fmask, (0.640, 0.760, 0.830), (0.955, 0.975, 1.000), x=-500, y=300)
    rough = maprange(nt, fmask, 0.0, 1.0, 0.075, 0.88, x=-500, y=-300)
    sss = maprange(nt, fmask, 0.0, 1.0, 0.55, 0.10, x=-500, y=-150)
    b = principled(
        nt,
        Metallic=0.0,
        IOR=1.31,
        **{
            "Subsurface Radius": (0.75, 0.90, 1.0),
            "Subsurface Scale": 0.065,
            "Coat Weight": 0.30,
            "Coat Roughness": 0.10,
            "Specular IOR Level": 0.5,
        },
    )
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(rough, b.inputs["Roughness"])
    nt.links.new(sss, b.inputs["Subsurface Weight"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_rust(name):
    """Pitted corrosion. The PITTING DENSITY is driven by the ambient
    occlusion: sheltered angles hold moisture, so that is where the metal has
    gone. Bare steel survives on the exposed, wiped faces."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    a = ao_node(nt, distance=0.5)
    shelter = maprange(nt, a.outputs["AO"], 0.32, 0.90, 1.0, 0.05, x=-1150, y=-900)
    blotch = noise(nt, co, 9.0, detail=6.0, roughness=0.65, y=-500)
    # threshold the blotch AGAINST the shelter field: a low threshold in a
    # recess (rust everywhere) and a high one on an exposed face (rust rare)
    thresh = maprange(nt, shelter, 0.0, 1.0, 0.72, 0.18, x=-950, y=-700)
    rustm = maprange(nt, blotch.outputs["Fac"], 0.0, 1.0, 0.0, 1.0, x=-950, y=-500)
    diff = mathnode(nt, "SUBTRACT", rustm, thresh, x=-780, y=-600)
    mask = maprange(nt, diff, -0.06, 0.10, 0.0, 1.0, x=-620, y=-600)
    pit = voronoi(nt, co, 1.0 / (2.6 * PX), feature="F1", y=-1200)
    h = mathnode(
        nt,
        "MULTIPLY",
        mathnode(nt, "SUBTRACT", 1.0, pit.outputs["Distance"], x=-620, y=-1200),
        mask,
        x=-450,
        y=-1000,
    )
    nrm = bump(nt, h, strength=0.80, distance=0.026)
    rust_col = ramp(
        nt,
        blotch.outputs["Fac"],
        [
            (0.00, (0.230, 0.075, 0.030)),
            (0.45, (0.430, 0.155, 0.055)),
            (0.75, (0.560, 0.255, 0.090)),
            (1.00, (0.330, 0.180, 0.100)),
        ],
        x=-620,
        y=400,
    )
    col = mixrgb(nt, mask, (0.115, 0.118, 0.125), rust_col, x=-380, y=300)
    rough = maprange(nt, mask, 0.0, 1.0, 0.34, 0.93, x=-380, y=-300)
    metal = maprange(nt, mask, 0.0, 1.0, 1.0, 0.0, x=-380, y=-150)
    b = principled(nt, **{"Specular IOR Level": 0.45, "Coat Weight": 0.0})
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(rough, b.inputs["Roughness"])
    nt.links.new(metal, b.inputs["Metallic"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_damp_stone(name):
    """The substrate under the moss: damp dark stone, darker in the crevices."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    a = ao_node(nt, distance=0.5)
    f = maprange(nt, a.outputs["AO"], 0.30, 0.92, 1.0, 0.0, x=-1150, y=-900)
    grit = noise(nt, co, 1.0 / (2.5 * PX), detail=4.0)
    # First attempt used 0.115 albedo and read as a light grey under this key,
    # which flattened the contrast with the moss. Darkened to real wet stone.
    col = mixrgb(nt, f, (0.062, 0.058, 0.046), (0.016, 0.018, 0.013), x=-500, y=300)
    nrm = bump(nt, grit.outputs["Fac"], strength=0.35, distance=0.014)
    b = principled(
        nt,
        Roughness=0.52,
        Metallic=0.0,
        **{"Coat Weight": 0.35, "Coat Roughness": 0.20, "Specular IOR Level": 0.4},
    )
    nt.links.new(col, b.inputs["Base Color"])
    nt.links.new(nrm, b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_moss_blade(name):
    """The instanced blade's own material. Dark at the base, yellow-green at
    the tip (Generated Z runs the blade's own length), per-instance hue jitter
    from Object Info Random, and a translucent term so backlit blades glow."""
    m, nt, out = new_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (-1400, 0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-1200, 0)
    nt.links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    oi = nt.nodes.new("ShaderNodeObjectInfo")
    oi.location = (-1200, -300)
    tipcol = ramp(
        nt,
        sep.outputs["Z"],
        [
            (0.00, (0.028, 0.035, 0.012)),
            (0.35, (0.075, 0.150, 0.030)),
            (0.75, (0.180, 0.330, 0.055)),
            (1.00, (0.330, 0.430, 0.090)),
        ],
        x=-980,
        y=0,
    )
    warm = mixrgb(
        nt,
        maprange(nt, oi.outputs["Random"], 0.0, 1.0, 0.0, 1.0, x=-980, y=-300),
        (0.055, 0.150, 0.045),
        (0.290, 0.330, 0.055),
        x=-750,
        y=-300,
    )
    col = mixrgb(nt, 0.45, tipcol, warm, x=-520, y=0)
    b = principled(
        nt,
        Roughness=0.72,
        Metallic=0.0,
        **{"Specular IOR Level": 0.3, "Sheen Weight": 0.15},
    )
    nt.links.new(col, b.inputs["Base Color"])
    tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
    tr.location = (520, -260)
    nt.links.new(col, tr.inputs["Color"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (740, 0)
    mix.inputs["Fac"].default_value = 0.30
    nt.links.new(b.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


def mat_felt_fibre(name):
    m, nt, out = new_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (-1400, 0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-1200, 0)
    nt.links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    oi = nt.nodes.new("ShaderNodeObjectInfo")
    oi.location = (-1200, -300)
    col = mixrgb(
        nt,
        maprange(nt, oi.outputs["Random"], 0.0, 1.0, 0.0, 1.0, x=-980, y=-300),
        (0.330, 0.300, 0.268),
        (0.640, 0.600, 0.545),
        x=-750,
        y=-300,
    )
    shade = mixrgb(
        nt,
        maprange(nt, sep.outputs["Z"], 0.0, 1.0, 0.65, 0.0, x=-980, y=0),
        col,
        (0.085, 0.078, 0.070),
        x=-520,
        y=0,
    )
    b = principled(
        nt,
        Roughness=0.90,
        Metallic=0.0,
        **{"Specular IOR Level": 0.18, "Sheen Weight": 0.45, "Sheen Roughness": 0.35},
    )
    nt.links.new(shade, b.inputs["Base Color"])
    tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
    tr.location = (520, -260)
    nt.links.new(shade, tr.inputs["Color"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (740, 0)
    mix.inputs["Fac"].default_value = 0.22
    nt.links.new(b.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


def mat_felt_base(name):
    m, nt, out = new_mat(name)
    b = principled(
        nt,
        Roughness=0.95,
        Metallic=0.0,
        **{
            "Base Color": (0.135, 0.125, 0.115, 1.0),
            "Specular IOR Level": 0.12,
            "Sheen Weight": 0.5,
            "Sheen Roughness": 0.4,
        },
    )
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


# ================================================================ studio


def build_cyclorama(
    z_ground, y_curve, radius=25.0, height=70.0, half_width=260.0, y_front=-260.0, segs=48
):
    prof = [(y_front, 0.0), (y_curve, 0.0)]
    for i in range(1, segs + 1):
        t = (math.pi / 2) * i / segs
        prof.append((y_curve + radius * math.sin(t), radius * (1 - math.cos(t))))
    prof.append((y_curve + radius, radius + height))
    verts, faces = [], []
    for y, z in prof:
        verts.append((-half_width, y, z_ground + z))
        verts.append((half_width, y, z_ground + z))
    for i in range(len(prof) - 1):
        faces.append((2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2))
    me = bpy.data.meshes.new("cyc")
    me.from_pydata(verts, [], faces)
    me.update()
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new("Cyclorama", me)
    scene.collection.objects.link(ob)
    return ob


def make_ground_mat():
    m, nt, out = new_mat("M_ground")
    b = principled(nt, x=250, Roughness=GROUND_ROUGH, **{"Specular IOR Level": 0.30})
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


GROUND_MAT = make_ground_mat()


def set_ground(rgb, roughness=GROUND_ROUGH):
    b = GROUND_MAT.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1.0)
    b.inputs["Roughness"].default_value = roughness


def set_world_flat(value, rgb=(1.0, 1.0, 1.0)):
    w = scene.world or bpy.data.worlds.new("W")
    scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (300, 0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (0, 0)
    bg.inputs["Color"].default_value = (*rgb, 1.0)
    bg.inputs["Strength"].default_value = value
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def set_world_studio_env(strength=1.0):
    """A sky-and-ground environment: something for clear glass to actually
    refract. Used by exactly ONE bonus panel, declared."""
    w = scene.world or bpy.data.worlds.new("W")
    scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (700, 0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (500, 0)
    bg.inputs["Strength"].default_value = strength
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (-500, 0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-300, 0)
    nt.links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.location = (-100, 0)
    mr.inputs["From Min"].default_value = -0.7
    mr.inputs["From Max"].default_value = 0.7
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.location = (150, 0)
    els = r.color_ramp.elements
    els[0].position = 0.0
    els[0].color = (0.18, 0.155, 0.13, 1.0)  # warm floor bounce
    els[1].position = 1.0
    els[1].color = (2.60, 2.75, 3.00, 1.0)  # bright cool sky
    e = els.new(0.48)
    e.color = (0.60, 0.62, 0.68, 1.0)
    nt.links.new(mr.outputs["Result"], r.inputs["Fac"])
    nt.links.new(r.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


def clear_lights():
    for ob in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(ob, do_unlink=True)


def add_area(name, loc, aim, size, energy, rgb=(1, 1, 1)):
    ld = bpy.data.lights.new(name, "AREA")
    ld.size = size
    ld.energy = energy
    ld.color = rgb
    ob = bpy.data.objects.new(name, ld)
    scene.collection.objects.link(ob)
    ob.location = Vector(loc)
    ob.rotation_euler = (Vector(aim) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return ob


# ================================================================ camera

FRAME_PTS = HERO.position[IDX["frame"]]
CENTROID = Vector(FRAME_PTS.mean(axis=0).tolist())

CAM_OB.rotation_euler = Euler((math.radians(78.0), 0.0, math.radians(-20.0)), "XYZ")
CAM_OB.location = CENTROID + Vector((0, -40, 8))
bpy.context.view_layer.update()
canvas.camera.frame_points(FRAME_PTS, margin=MARGIN)
bpy.context.view_layer.update()

CAM_MATRIX = CAM_OB.matrix_world.copy()
CAM_POS = CAM_MATRIX.translation.copy()
CAM_FWD = (CAM_MATRIX.to_3x3() @ Vector((0, 0, -1))).normalized()
D0 = (CENTROID - CAM_POS).dot(CAM_FWD)
TAN_X = (SENSOR / 2.0) / LENS
ORTHO_SCALE = 2.0 * D0 * TAN_X

# The instrument mask from the style-space run was rendered at THAT camera.
# Reuse it only if this camera reproduces it to within a rounding error --
# otherwise every masked measurement below would be reading the wrong pixels.
try:
    prev = json.load(open(os.path.join(PREV, "manifest.json")))
    pm = prev["camera_matrix"]
    here = [v for row in CAM_MATRIX for v in row]
    dmax = max(abs(a - b) for a, b in zip(pm, here, strict=False))
    log(f"[camera] max |delta| vs style-space camera matrix: {dmax:.2e}")
    assert dmax < 1e-4, (
        "camera does not reproduce the style-space camera; "
        "the shared instrument mask would be mis-registered"
    )
    MASK_OK = True
except Exception as e:
    log(f"[camera] could not verify against style-space camera: {e}")
    MASK_OK = False

log(f"[camera] d0={D0:.4f} ortho_scale={ORTHO_SCALE:.4f}")


def restore_camera():
    CAM_OB.matrix_world = CAM_MATRIX.copy()
    CAM_OB.data.type = "PERSP"
    CAM_OB.data.lens = LENS
    CAM_OB.data.shift_x = 0.0
    CAM_OB.data.shift_y = 0.0
    bpy.context.view_layer.update()


dg = bpy.context.evaluated_depsgraph_get()
ev = HERO_OB.evaluated_get(dg)
try:
    bb = [HERO_OB.matrix_world @ Vector(c) for c in ev.bound_box]
    Z_GEOM = min(p.z for p in bb)
    assert math.isfinite(Z_GEOM) and abs(Z_GEOM) < 1e4
except Exception:
    Z_GEOM = float(HERO.position[:, 2].min())
Z_GROUND = Z_GEOM - 0.02
log(f"[studio] evaluated z_min={Z_GEOM:.4f} ground={Z_GROUND:.4f}")

Y_CURVE = CENTROID.y + 62.0
CYC = build_cyclorama(Z_GROUND, Y_CURVE)
CYC.data.materials.append(GROUND_MAT)

FOCUS = bpy.data.objects.new("FocusTarget", None)
scene.collection.objects.link(FOCUS)
FOCUS.location = CENTROID
bpy.context.view_layer.update()

KEY_LOC = CENTROID + Vector((-26.0, -22.0, 26.0))


def rig_studio_key():
    clear_lights()
    add_area("Key", KEY_LOC, CENTROID, size=34.0, energy=48000.0, rgb=(1.0, 0.98, 0.95))


def rig_glass_env():
    """Key plus a broad soft backlight, so a transmissive surface has a bright
    field BEHIND it to carry. Used by one declared bonus panel."""
    clear_lights()
    add_area("Key", KEY_LOC, CENTROID, size=34.0, energy=48000.0, rgb=(1.0, 0.98, 0.95))
    add_area(
        "Back",
        CENTROID + Vector((14.0, 30.0, 16.0)),
        CENTROID,
        size=48.0,
        energy=90000.0,
        rgb=(0.94, 0.97, 1.0),
    )


# ====================================================== SASA (biotite)

import biotite.structure as struc  # noqa: E402

_u = HERO.universe
_arr = struc.AtomArray(N_ATOMS)
_arr.coord = np.ascontiguousarray(HERO.position * 10.0, dtype=np.float32)
_arr.element = np.array([e.upper() for e in _u.atoms.elements], dtype="U2")
_arr.atom_name = np.array(_u.atoms.names, dtype="U6")
_arr.res_name = np.array(_u.atoms.resnames, dtype="U5")
_arr.res_id = np.asarray(_u.atoms.resids, dtype=int)
_arr.chain_id = np.array(_u.atoms.chainIDs, dtype="U4")
_sasa_atom = struc.sasa(_arr, vdw_radii="Single", point_number=200)
_nan = int(np.isnan(_sasa_atom).sum())
_sasa_atom = np.nan_to_num(_sasa_atom, nan=0.0)

# Per-RESIDUE exposure, not per-atom. The moss grows on the CARTOON RIBBON,
# which follows the backbone; a backbone atom is buried even in a fully solvent
# exposed residue, so a per-atom field would be a field of zeros along exactly
# the ribbon the moss sits on. Summing over the residue is the standard measure
# of how exposed that piece of chain is, and it is the field the ribbon can see.
_keys = np.char.add(np.char.add(_arr.chain_id.astype(str), ":"), _arr.res_id.astype(str))
_uk, _inv = np.unique(_keys, return_inverse=True)
_res_sasa = np.zeros(len(_uk))
np.add.at(_res_sasa, _inv, _sasa_atom)
SASA_RES = _res_sasa[_inv]  # per atom, but residue-valued
_p95 = float(np.percentile(_res_sasa, 95))
SASA_F = np.clip(SASA_RES / max(_p95, 1e-6), 0.0, 1.0) ** 0.85
SASA_INFO = {
    "n_atoms": int(N_ATOMS),
    "nan_atoms": _nan,
    "total_sasa_A2": round(float(_sasa_atom.sum()), 1),
    "n_residues": len(_uk),
    "res_sasa_min": round(float(_res_sasa.min()), 2),
    "res_sasa_p50": round(float(np.percentile(_res_sasa, 50)), 2),
    "res_sasa_p95": round(_p95, 2),
    "res_sasa_max": round(float(_res_sasa.max()), 2),
    "f_mean": round(float(SASA_F.mean()), 4),
    "f_frac_below_0.2": round(float((SASA_F < 0.2).mean()), 4),
    "f_frac_above_0.8": round(float((SASA_F > 0.8).mean()), 4),
}
log(f"[sasa] {json.dumps(SASA_INFO)}")
assert SASA_INFO["f_frac_below_0.2"] > 0.05 and SASA_INFO["f_frac_above_0.8"] > 0.05, (
    "the exposure field has no dynamic range -- the binding could not show anything"
)

# A point cloud carrying the exposure field, for Sample Nearest to read.
_am = bpy.data.meshes.new("sasa_atoms")
_am.from_pydata([tuple(map(float, p)) for p in HERO.position], [], [])
_am.update()
_attr = _am.attributes.new("sasa_f", "FLOAT", "POINT")
_attr.data.foreach_set("value", SASA_F.astype(np.float32).tolist())
SASA_OB = bpy.data.objects.new("SasaAtoms", _am)
scene.collection.objects.link(SASA_OB)
SASA_OB.hide_render = True
SASA_OB.location = (0, 0, -900.0)  # parked; Object Info reads ORIGINAL space
log(f"[sasa] point cloud: {len(_am.vertices)} verts, attribute 'sasa_f'")

# ====================================================== instanced growth


def make_clump_mesh(name, groups, seed):
    """groups: list of (n_blades, radius, height, spread, tilt_deg, sides).
    A clump may mix short splayed blades (the velvet cushion) with a couple of
    tall thin ones (the sporophytes that give real moss its silhouette)."""
    rng = random.Random(seed)
    verts, faces = [], []
    for n_blades, radius, height, spread, tilt_deg, sides in groups:
        for _ in range(n_blades):
            h = height * rng.uniform(0.55, 1.35)
            r = radius * rng.uniform(0.7, 1.25)
            az = rng.uniform(0, 2 * math.pi)
            rr = spread * math.sqrt(rng.random())
            base = Vector((rr * math.cos(az), rr * math.sin(az), 0.0))
            tilt = math.radians(rng.uniform(0.0, tilt_deg))
            tdir = rng.uniform(0, 2 * math.pi)
            R = (
                Matrix.Rotation(tdir, 3, "Z")
                @ Matrix.Rotation(tilt, 3, "Y")
                @ Matrix.Rotation(-tdir, 3, "Z")
            )
            o = len(verts)
            for i in range(sides):
                a = 2 * math.pi * i / sides + az
                p = R @ Vector((r * math.cos(a), r * math.sin(a), 0.0))
                verts.append(tuple(base + p))
            verts.append(tuple(base + (R @ Vector((0, 0, h)))))
            for i in range(sides):
                faces.append((o + i, o + (i + 1) % sides, o + sides))
            faces.append(tuple(o + i for i in range(sides)))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    for p in me.polygons:
        p.use_smooth = False
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    ob.hide_render = True
    ob.location = (0, 0, -800.0)
    return ob


# The spiky blade used by the Tier-4 pair: one shape, so M17 and M18 differ in
# the DENSITY FIELD and nothing else.
MOSS_CLUMP = make_clump_mesh("moss_clump", [(4, 0.010, 0.085, 0.016, 32, 4)], seed=11)
# A velvet cushion with two sporophytes, for the Tier-1 "can we do moss" panel.
MOSS_CUSHION = make_clump_mesh(
    "moss_cushion",
    [(7, 0.0085, 0.030, 0.020, 68, 4), (2, 0.0035, 0.090, 0.012, 16, 3)],
    seed=31,
)
FELT_CLUMP = make_clump_mesh("felt_clump", [(3, 0.0045, 0.048, 0.010, 45, 3)], seed=23)
M_MOSS_BLADE = mat_moss_blade("M_moss_blade")
M_FELT_FIBRE = mat_felt_fibre("M_felt_fibre")
MOSS_CLUMP.data.materials.append(M_MOSS_BLADE)
MOSS_CUSHION.data.materials.append(M_MOSS_BLADE)
FELT_CLUMP.data.materials.append(M_FELT_FIBRE)
log(
    f"[growth] moss clump polys={len(MOSS_CLUMP.data.polygons)} "
    f"cushion polys={len(MOSS_CUSHION.data.polygons)} "
    f"felt clump polys={len(FELT_CLUMP.data.polygons)}"
)

SCATTER_MOD = "Scatter"


def clear_scatter():
    for md in list(HERO_OB.modifiers)[1:]:
        ng = md.node_group
        HERO_OB.modifiers.remove(md)
        if ng is not None and ng.users == 0:
            bpy.data.node_groups.remove(ng)
    for ng in list(bpy.data.node_groups):
        if ng.name.startswith("GN_scatter") and ng.users == 0:
            bpy.data.node_groups.remove(ng)


def _enabled(node, kind="INPUT"):
    return [s for s in (node.inputs if kind == "INPUT" else node.outputs) if s.enabled]


def build_scatter(
    clump_ob, density, seed, scale_lo, scale_hi, use_sasa=False, dens_lo=0.03, gamma=1.0
):
    """Second geometry-nodes modifier: realize the molecular geometry, scatter
    points on its faces, instance a growth clump on each, spin and scale it
    randomly, and join it back on top of the untouched original.

    use_sasa: link the per-residue solvent-exposure field to the Density input
    (sampled from the nearest atom), instead of a constant."""
    clear_scatter()
    ng = bpy.data.node_groups.new("GN_scatter", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1400, 0)
    go = ng.nodes.new("NodeGroupOutput")
    go.location = (900, 0)

    real = ng.nodes.new("GeometryNodeRealizeInstances")
    real.location = (-1200, 0)
    ng.links.new(gi.outputs[0], real.inputs[0])

    dist = ng.nodes.new("GeometryNodeDistributePointsOnFaces")
    dist.location = (-700, 0)
    dist.distribute_method = "RANDOM"
    assert "Density" in dist.inputs and "Seed" in dist.inputs
    dist.inputs["Density"].default_value = density
    dist.inputs["Seed"].default_value = seed
    ng.links.new(real.outputs[0], dist.inputs[0])

    if use_sasa:
        oi_s = ng.nodes.new("GeometryNodeObjectInfo")
        oi_s.location = (-1400, -700)
        oi_s.inputs["Object"].default_value = SASA_OB
        oi_s.transform_space = "ORIGINAL"
        pos = ng.nodes.new("GeometryNodeInputPosition")
        pos.location = (-1400, -900)
        near = ng.nodes.new("GeometryNodeSampleNearest")
        near.location = (-1180, -800)
        near.domain = "POINT"
        ng.links.new(oi_s.outputs["Geometry"], near.inputs[0])
        ng.links.new(pos.outputs["Position"], near.inputs["Sample Position"])
        named = ng.nodes.new("GeometryNodeInputNamedAttribute")
        named.location = (-1180, -1050)
        named.data_type = "FLOAT"
        named.inputs["Name"].default_value = "sasa_f"
        si = ng.nodes.new("GeometryNodeSampleIndex")
        si.location = (-980, -900)
        si.data_type = "FLOAT"
        si.domain = "POINT"
        si.clamp = True
        ng.links.new(oi_s.outputs["Geometry"], si.inputs[0])
        ng.links.new(named.outputs["Attribute"], si.inputs["Value"])
        ng.links.new(near.outputs["Index"], si.inputs["Index"])
        pw = ng.nodes.new("ShaderNodeMath")
        pw.location = (-880, -700)
        pw.operation = "POWER"
        pw.inputs[1].default_value = gamma
        ng.links.new(si.outputs[0], pw.inputs[0])
        mr = ng.nodes.new("ShaderNodeMapRange")
        mr.location = (-700, -700)
        mr.clamp = True
        mr.inputs["From Min"].default_value = 0.0
        mr.inputs["From Max"].default_value = 1.0
        mr.inputs["To Min"].default_value = density * dens_lo
        mr.inputs["To Max"].default_value = density
        ng.links.new(pw.outputs["Value"], mr.inputs["Value"])
        ng.links.new(mr.outputs["Result"], dist.inputs["Density"])

    oi = ng.nodes.new("GeometryNodeObjectInfo")
    oi.location = (-700, -380)
    oi.inputs["Object"].default_value = clump_ob
    oi.transform_space = "ORIGINAL"

    alg = ng.nodes.new("FunctionNodeAlignRotationToVector")
    alg.location = (-480, -180)
    alg.axis = "Z"
    ng.links.new(dist.outputs["Normal"], alg.inputs["Vector"])

    iop = ng.nodes.new("GeometryNodeInstanceOnPoints")
    iop.location = (-220, 0)
    ng.links.new(dist.outputs["Points"], iop.inputs["Points"])
    ng.links.new(oi.outputs["Geometry"], iop.inputs["Instance"])
    ng.links.new(alg.outputs["Rotation"], iop.inputs["Rotation"])

    rv = ng.nodes.new("FunctionNodeRandomValue")
    rv.location = (-480, 260)
    rv.data_type = "FLOAT"
    ins = _enabled(rv)
    names = [s.name for s in ins]
    assert "Min" in names and "Max" in names and "Seed" in names, names
    rv.inputs[ins[names.index("Min")].name]
    for s in ins:
        if s.name == "Min":
            s.default_value = scale_lo
        elif s.name == "Max":
            s.default_value = scale_hi
        elif s.name == "Seed":
            s.default_value = seed + 1
    outs = _enabled(rv, "OUTPUT")
    ng.links.new(outs[0], iop.inputs["Scale"])

    rot = ng.nodes.new("GeometryNodeRotateInstances")
    rot.location = (60, 0)
    ng.links.new(iop.outputs[0], rot.inputs["Instances"])
    rot.inputs["Local Space"].default_value = True
    rv2 = ng.nodes.new("FunctionNodeRandomValue")
    rv2.location = (-220, -600)
    rv2.data_type = "FLOAT_VECTOR"
    for s in _enabled(rv2):
        if s.name == "Min":
            s.default_value = (0.0, 0.0, -math.pi)
        elif s.name == "Max":
            s.default_value = (0.0, 0.0, math.pi)
        elif s.name == "Seed":
            s.default_value = seed + 2
    o2 = _enabled(rv2, "OUTPUT")[0]
    conv = ng.nodes.new("FunctionNodeEulerToRotation")
    conv.location = (-60, -600)
    ng.links.new(o2, conv.inputs[0])
    ng.links.new(conv.outputs[0], rot.inputs["Rotation"])

    join = ng.nodes.new("GeometryNodeJoinGeometry")
    join.location = (500, 0)
    ng.links.new(gi.outputs[0], join.inputs[0])
    ng.links.new(rot.outputs[0], join.inputs[0])
    ng.links.new(join.outputs[0], go.inputs[0])

    md = HERO_OB.modifiers.new(SCATTER_MOD, "NODES")
    md.node_group = ng
    bpy.context.view_layer.update()
    return md


def count_instances():
    bpy.context.view_layer.update()
    d = bpy.context.evaluated_depsgraph_get()
    n = 0
    for inst in d.object_instances:
        if inst.is_instance and inst.parent and inst.parent.original == HERO_OB:
            n += 1
    return n


BASE_INSTANCES = count_instances()
log(f"[growth] base instances with no scatter: {BASE_INSTANCES}")

# ================================================================ signatures


def _tree_hash(nt):
    h = hashlib.sha256()
    if nt is None:
        return "none"
    for n in sorted(nt.nodes, key=lambda x: x.name):
        h.update(n.name.encode())
        h.update(n.bl_idname.encode())
        for attr in (
            "operation",
            "blend_type",
            "data_type",
            "interpolation",
            "attribute_name",
            "only_local",
            "inside",
            "samples",
            "clamp",
            "feature",
            "distance",
            "noise_dimensions",
            "voronoi_dimensions",
            "distribute_method",
            "transform_space",
            "domain",
            "axis",
        ):
            if hasattr(n, attr):
                h.update(repr(getattr(n, attr)).encode())
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree:
            h.update(n.node_tree.name.encode())
        if n.bl_idname == "ShaderNodeValToRGB":
            for e in n.color_ramp.elements:
                h.update(
                    f"{e.position:.5f}{tuple(round(c, 5) for c in e.color)}".encode()
                )
        for s in n.inputs:
            h.update(s.name.encode())
            h.update(str(s.is_linked).encode())
            try:
                dv = s.default_value
                dv = (
                    tuple(round(float(x), 5) for x in dv)
                    if hasattr(dv, "__len__")
                    else (
                        round(float(dv), 5) if isinstance(dv, (int, float)) else str(dv)
                    )
                )
                h.update(repr(dv).encode())
            except Exception:
                pass
    return h.hexdigest()[:16]


def _mrx(m):
    return tuple(round(v, 5) for row in m for v in row)


def sig_camera():
    d = CAM_OB.data
    return (
        _mrx(CAM_OB.matrix_world),
        d.type,
        round(d.lens, 4),
        round(d.shift_x, 4),
        round(d.shift_y, 4),
        round(d.sensor_width, 4),
    )


def sig_dof():
    d = CAM_OB.data.dof
    return (
        d.use_dof,
        round(d.aperture_fstop, 4),
        d.focus_object.name if d.focus_object else None,
    )


def sig_lights():
    out = []
    for ob in bpy.data.objects:
        if ob.type != "LIGHT":
            continue
        L = ob.data
        out.append(
            (
                ob.name,
                L.type,
                round(L.energy, 3),
                tuple(round(c, 3) for c in L.color),
                round(getattr(L, "size", 0.0), 3),
                round(getattr(L, "angle", 0.0), 5),
                tuple(round(v, 3) for v in ob.location),
                tuple(round(v, 4) for v in ob.rotation_euler),
            )
        )
    return tuple(sorted(out))


def sig_world():
    return _tree_hash(scene.world.node_tree if scene.world else None)


def sig_ground():
    b = GROUND_MAT.node_tree.nodes["Principled BSDF"]
    return (
        tuple(round(c, 4) for c in b.inputs["Base Color"].default_value),
        round(b.inputs["Roughness"].default_value, 4),
        len(CYC.data.vertices),
        CYC.hide_render,
    )


def _mn_objects():
    return sorted(
        [
            o
            for o in bpy.data.objects
            if o.modifiers
            and o.modifiers[0].type == "NODES"
            and o.modifiers[0].node_group
        ],
        key=lambda o: o.name,
    )


def sig_material():
    out = []
    for ob in _mn_objects():
        if ob.hide_render:
            continue
        for n in style_nodes_of(ob):
            mat = n.inputs["Material"].default_value
            out.append(
                (
                    ob.name,
                    n.name,
                    mat.name if mat else None,
                    _tree_hash(mat.node_tree) if mat else None,
                )
            )
    return tuple(out)


def sig_color():
    arr = HERO.named_attribute("Color")
    return hashlib.sha256(
        np.ascontiguousarray(np.round(np.asarray(arr, dtype=np.float64), 5)).tobytes()
    ).hexdigest()[:16]


def sig_geometry():
    out = []
    for ob in _mn_objects():
        if ob.hide_render:
            continue
        styles = []
        for n in style_nodes_of(ob):
            vals = []
            for s in n.inputs:
                if s.name in ("Atoms", "Material"):
                    continue
                try:
                    dv = s.default_value
                    dv = (
                        tuple(round(float(x), 4) for x in dv)
                        if hasattr(dv, "__len__")
                        else (
                            round(float(dv), 4)
                            if isinstance(dv, (int, float))
                            else str(dv)
                        )
                    )
                except Exception:
                    dv = None
                vals.append((s.name, repr(dv)))
            styles.append((n.name, tuple(vals)))
        out.append((ob.name, _mrx(ob.matrix_world), tuple(styles)))
    return tuple(out)


def sig_scatter():
    """The dimension the style-space harness did not have: the growth layer.
    Without it, adding real instanced geometry on top of the molecule would be
    invisible to the discipline check."""
    out = []
    for ob in _mn_objects():
        for md in list(ob.modifiers)[1:]:
            out.append((ob.name, md.name, md.show_render, _tree_hash(md.node_group)))
    for cl in (MOSS_CLUMP, MOSS_CUSHION, FELT_CLUMP):
        mats = tuple(m.name for m in cl.data.materials)
        out.append(
            (
                cl.name,
                len(cl.data.polygons),
                mats,
                tuple(_tree_hash(bpy.data.materials[m].node_tree) for m in mats),
            )
        )
    return tuple(out)


DIMS = {
    "camera": sig_camera,
    "dof": sig_dof,
    "lights": sig_lights,
    "world": sig_world,
    "ground": sig_ground,
    "material": sig_material,
    "color": sig_color,
    "geometry": sig_geometry,
    "scatter": sig_scatter,
}


def snapshot_state():
    return {k: f() for k, f in DIMS.items()}


# ================================================================ carrier

M_CARRIER = mat_carrier()


def use_material(mat, nodes=None):
    for n in nodes if nodes is not None else style_nodes_of(HERO_OB):
        mn.material.set_socket_material(n.inputs["Material"], mat)


def carrier_studio():
    """Restored before EVERY panel. One ground, one rig, one world, for the
    whole sheet."""
    restore_camera()
    HERO_OB.matrix_world = HERO_M0.copy()
    CAM_OB.data.dof.use_dof = False
    CAM_OB.data.dof.focus_object = FOCUS
    CAM_OB.data.dof.aperture_fstop = 0.25
    rig_studio_key()
    set_world_flat(WORLD_STRENGTH)
    set_ground(GROUND_RGB)
    write_colours(PAL_CARRIER)
    clear_scatter()
    use_material(M_CARRIER)
    HERO_OB.hide_render = False
    bpy.context.view_layer.update()


carrier_studio()
bpy.context.view_layer.update()
CARRIER = snapshot_state()
log(
    "[discipline] carrier state established BEFORE any render, so a partial "
    "run (--only) is checked against the same baseline as a full one"
)

# ================================================================ render

RESULTS = []
STATES = {}
EXTRA = {}


def render(pid, name, varies, build, note):
    fname = f"{pid}_{name}.png"
    path = os.path.join(OUT, fname)
    if ONLY and pid not in ONLY:
        return
    log(f"\n=== {pid} {name} ===")
    t0 = time.time()
    rec = {
        "id": pid,
        "name": name,
        "file": path,
        "note": note,
        "varies": sorted(varies),
        "status": "ok",
        "violations": [],
    }
    try:
        carrier_studio()
        info = build()
        if isinstance(info, dict):
            rec.update(info)
            EXTRA[pid] = info
        bpy.context.view_layer.update()
        st = snapshot_state()
        STATES[pid] = st
        for dim, val in st.items():
            if dim in varies:
                continue
            if val != CARRIER[dim]:
                msg = f"UNDECLARED DRIFT in {dim!r}"
                rec["violations"].append(msg)
                log(f"[discipline] !! {msg}")
                log(f"    carrier: {str(CARRIER[dim])[:240]}")
                log(f"    panel  : {str(val)[:240]}")
        for dim in sorted(varies):
            if st[dim] == CARRIER[dim]:
                log(f"[discipline] ?? declared '{dim}' but it did NOT change")
            else:
                log(f"[discipline] declared variable: {dim}")
        rec["instances"] = count_instances()
        canvas.snapshot(path)
        rec["seconds"] = round(time.time() - t0, 1)
        rec["bytes"] = os.path.getsize(path)
        log(f"[render] {fname} {rec['seconds']}s inst={rec['instances']}")
    except Exception as e:
        rec["status"] = "FAILED"
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["seconds"] = round(time.time() - t0, 1)
        log(f"[render] {pid} FAILED: {rec['error']}")
        log(traceback.format_exc())
    RESULTS.append(rec)


# --------------------------------------------------------------- the sheet

render(
    "M00",
    "carrier_reference",
    set(),
    lambda: None,
    "The reference surface: matte dielectric reading the molecule's own "
    "colours, on the one warm grey ground used by every panel. Every other "
    "panel differs from this in the MATERIAL alone unless it says otherwise.",
)

# ---- Tier 1: the materials Charlie named

M_GLASS_CLEAR = mat_glass("M_glass_clear", (0.960, 0.965, 0.970), roughness=0.02)
M_GLASS_AMBER = mat_glass("M_glass_amber", (0.780, 0.330, 0.045), roughness=0.02)
M_GLASS_SEA = mat_sea_glass("M_glass_sea")


def M01():
    use_material(M_GLASS_CLEAR)


render(
    "M01",
    "glass_clear",
    {"material"},
    M01,
    "Real transmissive glass, ACHROMATIC (base colour 0.96/0.965/0.97, "
    "IOR 1.48, roughness 0.02). The deliberate re-test: an earlier amber "
    "glass was rejected as cheap and gaudy, and this asks whether that was "
    "the glass or the saturation. All colour here comes from the room.",
)


def M02():
    use_material(M_GLASS_AMBER)


render(
    "M02",
    "glass_amber_control",
    {"material"},
    M02,
    "THE CONTROL FOR M01: the identical shader with one thing changed, the "
    "base colour, driven to saturated amber. M01 vs M02 differ in a single "
    "RGB triple, so whatever separates them is the saturation and nothing "
    "else.",
)


def M03():
    use_material(M_GLASS_SEA)


render(
    "M03",
    "glass_sea",
    {"material"},
    M03,
    "Sea glass: rough transmission (roughness 0.46) plus a translucent "
    "term, a tumbled pit bump at ~4 px and a fine frost at ~1.6 px, no "
    "coat, no sharp specular. The forgiving variant.",
)


M_DAMP = mat_damp_stone("M_damp_stone")


def M04():
    use_material(M_DAMP)
    build_scatter(MOSS_CUSHION, density=760.0, seed=5, scale_lo=0.6, scale_hi=1.5)
    return {"scatter_density": 760.0, "scatter_kind": "uniform", "clump": "cushion"}


render(
    "M04",
    "moss",
    {"material", "scatter"},
    M04,
    "REAL INSTANCED GEOMETRY, not a shader: the molecular geometry is "
    "realized, points are scattered on its faces, and a growth clump is "
    "instanced on each, aligned to the surface normal, randomly spun and "
    "scaled. The clump is seven short splayed blades (the velvet cushion) "
    "plus two tall thin sporophytes, which is what gives real moss its "
    "fuzzy silhouette. The molecule underneath becomes damp dark stone.",
)

M_CLAY = mat_clay("M_clay")
M_SAND = mat_sand("M_sand")


def M05():
    use_material(M_CLAY)


render(
    "M05",
    "clay",
    {"material"},
    M05,
    "Matte, faintly waxy (coat 0.10 at roughness 0.35), with low-frequency "
    "thumbprint dents at ~0.31 BU and a fine tooth at ~2 px. The nearest "
    "neighbour to the porcelain that was already approved.",
)


def M06():
    use_material(M_SAND)


render(
    "M06",
    "sand",
    {"material"},
    M06,
    "Cast in sand. Grain cells at ~3 px so the grain resolves instead of "
    "dissolving into noise, per-grain albedo from the Voronoi cell colour "
    "(some grains near-black, some pale), and a coarse erosion at ~0.2 BU.",
)

# ---- Tier 2: shader-only

M_WAX = mat_sss(
    "M_wax",
    base=(0.910, 0.700, 0.430),
    radius=(1.0, 0.55, 0.28),
    sss_scale=0.13,
    weight=0.92,
    roughness=0.36,
    coat=0.18,
    mottle=(9.0, 0.25),
)
M_ALABASTER = mat_sss(
    "M_alabaster",
    base=(0.880, 0.855, 0.810),
    radius=(1.0, 0.92, 0.82),
    sss_scale=0.095,
    weight=0.80,
    roughness=0.30,
    coat=0.25,
    veins=(4.5, 2.2, 0.55, (0.560, 0.520, 0.455)),
)
M_BONE = mat_sss(
    "M_bone",
    base=(0.855, 0.815, 0.700),
    radius=(1.0, 0.70, 0.48),
    sss_scale=0.042,
    weight=0.40,
    roughness=0.52,
    coat=0.06,
    mottle=(16.0, 0.30),
    ao_stain=(0.50, 0.62, (0.330, 0.265, 0.170)),
)
M_TERRACOTTA = mat_terracotta("M_terracotta")
M_GRAPHITE = mat_graphite("M_graphite")
M_WET_STONE = mat_wet_stone("M_wet_stone")


def M07():
    use_material(M_WAX)


render(
    "M07",
    "wax",
    {"material"},
    M07,
    "Real subsurface scattering: weight 0.92, radius (1.0,0.55,0.28) at "
    "scale 0.13 BU = 1.3 A of light bleed, so thin ribbon edges glow warm. "
    "Cycles-only; no WebGL viewer approximates this well.",
)


def M08():
    use_material(M_ALABASTER)


render(
    "M08",
    "alabaster",
    {"material"},
    M08,
    "SSS at scale 0.095 plus faint veining from a heavily distorted noise "
    "(scale 4.5, distortion 2.2). The carved-stone register.",
)


def M09():
    use_material(M_TERRACOTTA)


render(
    "M09",
    "terracotta",
    {"material"},
    M09,
    "Unglazed and porous: roughness 0.88, no coat, an open pore bump at "
    "~2.2 px and a coarse firing blotch that shifts the albedo between two "
    "warm reds.",
)


def M10():
    use_material(M_GRAPHITE)


render(
    "M10",
    "graphite",
    {"material"},
    M10,
    "Base albedo 0.026-0.062, metallic 0.62, roughness varying 0.24-0.55 "
    "with a flake noise. The test of whether a near-black subject reads at "
    "all against this ground.",
)


def M11():
    use_material(M_BONE)


render(
    "M11",
    "bone",
    {"material"},
    M11,
    "Subtle SSS (weight 0.40 at 0.042 BU), warm off-white, fine mottling, "
    "and an AO-driven stain in the recesses. The specimen register -- and "
    "the pale material used for the ground demonstration below.",
)


def M12():
    use_material(M_WET_STONE)


render(
    "M12",
    "wet_stone",
    {"material"},
    M12,
    "Dark stone (albedo 0.038-0.082) under a FULL clear coat at roughness "
    "0.045. The sheen lives in the coat so the body stays matte, the "
    "silhouette stays hard and the highlights ride the form.",
)

# ---- Tier 3: driven by something already computed

M_PORCELAIN = mat_porcelain_crazed("M_porcelain_crazed")
M_FROST = mat_frost("M_frost")
M_RUST = mat_rust("M_rust")
M_FELT_BASE = mat_felt_base("M_felt_base")


def M13():
    use_material(M_PORCELAIN)


render(
    "M13",
    "porcelain_crazed",
    {"material"},
    M13,
    "White glaze with a crazing network: Voronoi distance-to-edge at ~9 px "
    "cells gives the crack lines, and the crack STRENGTH is multiplied by "
    "an AO term so cracks run 1.0 deep in a recess and 0.25 on an exposed "
    "face. The crazing is free -- the AO was already being computed.",
)


def M14():
    use_material(M_FROST)


render(
    "M14",
    "frost",
    {"material"},
    M14,
    "Ice in the bulk (SSS 0.55 falling to 0.10, IOR 1.31) with frost "
    "accumulating where the AO says the surface is sheltered, broken up by "
    "a noise so the rime line is crystalline rather than a smooth ramp.",
)


def M15():
    use_material(M_RUST)


render(
    "M15",
    "rust",
    {"material"},
    M15,
    "Pitted corrosion whose PITTING DENSITY is AO-driven: the blotch noise "
    "is thresholded against the occlusion, so the threshold is 0.18 in a "
    "recess (rust everywhere) and 0.72 on an exposed face (rust rare). "
    "Bare steel survives on the wiped faces.",
)


def M16():
    use_material(M_FELT_BASE)
    build_scatter(FELT_CLUMP, density=2600.0, seed=9, scale_lo=0.6, scale_hi=1.5)
    return {"scatter_density": 2600.0, "scatter_kind": "uniform"}


render(
    "M16",
    "felt_fibres",
    {"material", "scatter"},
    M16,
    "REAL SHORT FIBRES, instanced. protean already ships a felt look built "
    "as a screen-space effect in the viewer; this is the version that needs "
    "a real renderer -- the fibres break the silhouette and self-shadow, "
    "which a screen-space pass cannot do.",
)

# ---- Tier 4: the material carries data

MOSS_T4_DENSITY = 900.0
MOSS_T4_SEED = 17
UNIFORM_DENSITY = [MOSS_T4_DENSITY * 0.5]  # calibrated below


def _calibrate_uniform():
    """Equalise BLADE COUNT between the two Tier-4 moss panels.

    If the exposure-driven panel grew 30k clumps and the control grew 90k, the
    two panels would differ in DENSITY, not in DISTRIBUTION, and the control
    would prove nothing. So: build the bound version, count its instances, then
    solve the control's constant density to land on the same count."""
    carrier_studio()
    use_material(M_DAMP)
    build_scatter(
        MOSS_CLUMP,
        density=MOSS_T4_DENSITY,
        seed=MOSS_T4_SEED,
        scale_lo=0.55,
        scale_hi=1.55,
        use_sasa=True,
        dens_lo=0.03,
        gamma=1.0,
    )
    n_sasa = count_instances() - BASE_INSTANCES
    d = MOSS_T4_DENSITY * 0.5
    n_uni = None
    for _ in range(4):
        build_scatter(
            MOSS_CLUMP, density=d, seed=MOSS_T4_SEED, scale_lo=0.55, scale_hi=1.55
        )
        n_uni = count_instances() - BASE_INSTANCES
        log(f"[calibrate] uniform density {d:.1f} -> {n_uni} clumps (target {n_sasa})")
        if n_uni == 0:
            break
        if abs(n_uni - n_sasa) / max(n_sasa, 1) < 0.02:
            break
        d *= n_sasa / n_uni
    UNIFORM_DENSITY[0] = d
    clear_scatter()
    return n_sasa, n_uni, d


if not ONLY or ({"M17", "M18"} & ONLY):
    N_SASA, N_UNI, D_UNI = _calibrate_uniform()
    log(
        f"[calibrate] sasa clumps={N_SASA} uniform clumps={N_UNI} "
        f"uniform density={D_UNI:.2f} (max density both = {MOSS_T4_DENSITY})"
    )
else:
    N_SASA = N_UNI = 0
    D_UNI = UNIFORM_DENSITY[0]


def M17():
    use_material(M_DAMP)
    build_scatter(
        MOSS_CLUMP,
        density=MOSS_T4_DENSITY,
        seed=MOSS_T4_SEED,
        scale_lo=0.55,
        scale_hi=1.55,
        use_sasa=True,
        dens_lo=0.03,
        gamma=1.0,
    )
    n = count_instances() - BASE_INSTANCES
    return {
        "clumps": n,
        "scatter_kind": "sasa-bound",
        "density_max": MOSS_T4_DENSITY,
        "density_min_frac": 0.03,
    }


render(
    "M17",
    "moss_sasa",
    {"material", "scatter"},
    M17,
    "The moss density is bound to PER-RESIDUE SOLVENT ACCESSIBLE SURFACE "
    "AREA, computed with biotite: how much of each piece of chain a water "
    "molecule can actually touch. Growth runs from 3% of maximum density on "
    "the most buried residues to 100% on the most exposed. Same seed, same "
    "clump, same maximum density as M18.",
)


def M18():
    use_material(M_DAMP)
    build_scatter(
        MOSS_CLUMP,
        density=UNIFORM_DENSITY[0],
        seed=MOSS_T4_SEED,
        scale_lo=0.55,
        scale_hi=1.55,
    )
    n = count_instances() - BASE_INSTANCES
    return {
        "clumps": n,
        "scatter_kind": "uniform-control",
        "density": round(UNIFORM_DENSITY[0], 2),
    }


render(
    "M18",
    "moss_uniform",
    {"material", "scatter"},
    M18,
    "THE CONTROL for M17: identical clump, identical seed, identical "
    "material, uniform density calibrated so the TOTAL CLUMP COUNT matches "
    "M17's to within 2%. If M17 and M18 are indistinguishable, the data "
    "binding is decoration and this sheet has to say so.",
)


M_GLASS_CAT = mat_sea_glass("M_glass_category")


def M19():
    use_material(M_CARRIER)
    use_material(M_GLASS_CAT, nodes=[ST_HEM])


render(
    "M19",
    "glass_category",
    {"material"},
    M19,
    "Transmission as a CATEGORY CHANNEL rather than as a look: the protein "
    "keeps the carrier surface exactly, and only the four haem groups -- "
    "the thing that makes haemoglobin haemoglobin -- are frosted glass. The "
    "material itself says 'this class is different'. Differs from M00 in "
    "one style node's material.",
)

# ---- the ground demonstration


def G1():
    use_material(M_BONE)
    set_ground((0.780, 0.762, 0.735))


render(
    "G1",
    "bone_on_light_ground",
    {"material", "ground"},
    G1,
    "The pale material on a LIGHT ground. This is the white-on-white trap "
    "the previous run fell into (its paper panel measured a subject/ground "
    "step of 0.0158 in display luminance -- a blank frame).",
)


def G2():
    use_material(M_BONE)
    set_ground((0.030, 0.029, 0.027))


render(
    "G2",
    "bone_on_dark_ground",
    {"material", "ground"},
    G2,
    "The SAME pale material on a DARK ground. G1 vs M11 vs G2 is a "
    "three-point sweep of ground albedo with the material held fixed, so "
    "the ground's contribution can be measured rather than asserted.",
)

# ---- bonus: does clear glass need a room?


def X1():
    use_material(M_GLASS_CLEAR)
    set_world_studio_env(1.0)
    rig_glass_env()


render(
    "X1",
    "glass_clear_env",
    {"material", "world", "lights"},
    X1,
    "BONUS, three variables declared. M01 asks 'is the material the "
    "problem'. This asks the competing question: is CLEAR GLASS a material "
    "choice at all, or a lighting choice? Same glass, plus a sky-and-floor "
    "environment and a broad backlight, so there is something to refract.",
)

# ---- instrument: a direct readout of the field the Tier-3 shaders claim to use


def mat_ao_map(name):
    """NOT a material to look at. This is the ambient-occlusion term that the
    crazing, frost and rust shaders drive themselves with, rendered as pure
    EMISSION so it can be read back off the image.

    Emission ignores the lights, so what lands on the film is the shelter field
    itself and not a picture of the shelter field lit from one side. Without
    this, 'the cracks are AO-driven' is an assertion about a node graph; with
    it, it is a correlation that can come out zero."""
    m, nt, out = new_mat(name)
    a = ao_node(nt, distance=0.5, samples=32)
    e = nt.nodes.new("ShaderNodeEmission")
    e.location = (300, 0)
    setin(e, "Strength", 1.0)
    nt.links.new(a.outputs["AO"], e.inputs["Color"])
    nt.links.new(e.outputs["Emission"], out.inputs["Surface"])
    return m


M_AO_MAP = mat_ao_map("M_ao_map")


def I1():
    use_material(M_AO_MAP)


render(
    "I1",
    "instrument_ao_field",
    {"material"},
    I1,
    "INSTRUMENT, not a panel. The AO field at distance 0.5 that M13, M14 and "
    "M15 bind their effects to, emitted directly. Bright = exposed, dark = "
    "sheltered. Every 'AO-driven' claim on this sheet is tested as a rank "
    "correlation against this image, so the claim can fail.",
)

# ---- two materials nobody asked for, motivated by what the sheet showed


def mat_obsidian(name):
    """Dark glass with real VOLUME ABSORPTION. M01 showed clear glass shatters
    the form into prisms; M10 showed a near-black subject reads best of all.
    This is the synthesis: absorption makes path length visible, so a thin
    ribbon edge glows and a deep tangle goes black -- transmission that
    delivers depth instead of confetti."""
    m, nt, out = new_mat(name)
    b = principled(
        nt,
        Roughness=0.06,
        Metallic=0.0,
        IOR=1.49,
        **{
            "Base Color": (1.0, 1.0, 1.0),
            "Transmission Weight": 1.0,
            "Coat Weight": 0.0,
            "Specular IOR Level": 0.5,
        },
    )
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    va = nt.nodes.new("ShaderNodeVolumeAbsorption")
    va.location = (520, -320)
    setin(va, "Color", (0.34, 0.40, 0.52))
    setin(va, "Density", 12.0)
    nt.links.new(va.outputs["Volume"], out.inputs["Volume"])
    return m


def mat_velvet(name):
    """Sheen. A deep, almost valueless body with a bright fibre sheen that
    fires only at grazing angles, so the SILHOUETTE lights up and the facing
    surfaces stay dark. Every other material on this sheet puts its highlight
    where the light is; this one puts it exactly on the contour, which is where
    a molecular picture needs its information."""
    m, nt, out = new_mat(name)
    co = obj_coord(nt)
    nap = noise(nt, co, 1.0 / (3.0 * PX), detail=4.0, roughness=0.5)
    rough = maprange(nt, nap.outputs["Fac"], 0.0, 1.0, 0.18, 0.34, x=-800, y=-300)
    b = principled(
        nt,
        Roughness=0.92,
        Metallic=0.0,
        **{
            "Base Color": (0.052, 0.048, 0.082, 1.0),
            "Sheen Weight": 1.0,
            "Sheen Tint": (0.960, 0.905, 0.820, 1.0),
            "Coat Weight": 0.0,
            "Specular IOR Level": 0.25,
        },
    )
    nt.links.new(rough, b.inputs["Sheen Roughness"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


M_OBSIDIAN = mat_obsidian("M_obsidian")
M_VELVET = mat_velvet("M_velvet")


def X2():
    use_material(M_OBSIDIAN)


render(
    "X2",
    "obsidian",
    {"material"},
    X2,
    "UNASKED-FOR #1. Transmissive like M01, dark like M10: a Volume "
    "Absorption node at density 12 inside the glass, so how far light "
    "travelled through the molecule sets how much of it survives.",
)


def X3():
    use_material(M_VELVET)


render(
    "X3",
    "velvet",
    {"material"},
    X3,
    "UNASKED-FOR #2. Sheen weight 1.0 over a near-black body: the highlight "
    "lives on the grazing contour instead of facing the key light, so the "
    "silhouette and every interior edge self-illuminate.",
)

# ---- the sparse re-test of the data binding
#
# M17 vs M18 came back a NULL: green coverage 0.5703 vs 0.5716 inside the
# silhouette, a 0.2% difference, even though the density field spans 27x. The
# diagnosis is saturation -- at 60,850 clumps the moss covers everything, and a
# density field cannot show through a surface that is already fully covered.
# This pair tests that diagnosis at a tenth of the density. If the two still
# match, the binding is decoration and the saturation story is wrong too.

SPARSE_DENSITY = 90.0
SPARSE_UNIFORM = [SPARSE_DENSITY * 0.5]


def _calibrate_sparse():
    carrier_studio()
    use_material(M_DAMP)
    build_scatter(
        MOSS_CLUMP,
        density=SPARSE_DENSITY,
        seed=MOSS_T4_SEED,
        scale_lo=0.55,
        scale_hi=1.55,
        use_sasa=True,
        dens_lo=0.03,
        gamma=1.0,
    )
    n_sasa = count_instances() - BASE_INSTANCES
    d = SPARSE_DENSITY * 0.5
    n_uni = None
    for _ in range(5):
        build_scatter(
            MOSS_CLUMP, density=d, seed=MOSS_T4_SEED, scale_lo=0.55, scale_hi=1.55
        )
        n_uni = count_instances() - BASE_INSTANCES
        log(f"[calibrate-sparse] density {d:.2f} -> {n_uni} clumps (target {n_sasa})")
        if n_uni == 0 or abs(n_uni - n_sasa) / max(n_sasa, 1) < 0.02:
            break
        d *= n_sasa / n_uni
    SPARSE_UNIFORM[0] = d
    clear_scatter()
    return n_sasa, n_uni, d


if ONLY and ({"S17", "S18"} & ONLY):
    NS_SASA, NS_UNI, DS_UNI = _calibrate_sparse()
    log(f"[calibrate-sparse] sasa={NS_SASA} uniform={NS_UNI} density={DS_UNI:.2f}")
else:
    NS_SASA = NS_UNI = 0
    DS_UNI = SPARSE_UNIFORM[0]


def S17():
    use_material(M_DAMP)
    build_scatter(
        MOSS_CLUMP,
        density=SPARSE_DENSITY,
        seed=MOSS_T4_SEED,
        scale_lo=0.55,
        scale_hi=1.55,
        use_sasa=True,
        dens_lo=0.03,
        gamma=1.0,
    )
    return {
        "clumps": count_instances() - BASE_INSTANCES,
        "scatter_kind": "sasa-bound-sparse",
        "density_max": SPARSE_DENSITY,
    }


render(
    "S17",
    "moss_sasa_sparse",
    {"material", "scatter"},
    S17,
    "The exposure-bound moss at a TENTH of M17's density, so the growth no "
    "longer covers everything and the density field has room to show.",
)


def S18():
    use_material(M_DAMP)
    build_scatter(
        MOSS_CLUMP,
        density=SPARSE_UNIFORM[0],
        seed=MOSS_T4_SEED,
        scale_lo=0.55,
        scale_hi=1.55,
    )
    return {
        "clumps": count_instances() - BASE_INSTANCES,
        "scatter_kind": "uniform-control-sparse",
        "density": round(SPARSE_UNIFORM[0], 3),
    }


render(
    "S18",
    "moss_uniform_sparse",
    {"material", "scatter"},
    S18,
    "THE CONTROL for S17: same clump, same seed, uniform density calibrated "
    "to the same total clump count.",
)

# ================================================================ manifest

manifest = {
    "resolution": RES,
    "samples": SAMPLES,
    "engine": "CYCLES",
    "device": scene.cycles.device,
    "lens_mm": LENS,
    "sensor_mm": SENSOR,
    "view_transform": VIEW_TRANSFORM,
    "look": scene.view_settings.look,
    "margin": MARGIN,
    "subject": "4HHB",
    "d0": round(D0, 4),
    "ground_rgb": GROUND_RGB,
    "world_strength": WORLD_STRENGTH,
    "camera_matrix": [round(v, 5) for row in CAM_MATRIX for v in row],
    "mask_registered_with_stylespace": MASK_OK,
    "bounces": {
        "max": scene.cycles.max_bounces,
        "diffuse": scene.cycles.diffuse_bounces,
        "glossy": scene.cycles.glossy_bounces,
        "transmission": scene.cycles.transmission_bounces,
    },
    "sasa": SASA_INFO,
    "moss_calibration": {
        "sasa_clumps": N_SASA,
        "uniform_clumps": N_UNI,
        "uniform_density": round(D_UNI, 3),
        "max_density": MOSS_T4_DENSITY,
        "base_instances": BASE_INSTANCES,
    },
    "moss_calibration_sparse": {
        "sasa_clumps": NS_SASA,
        "uniform_clumps": NS_UNI,
        "uniform_density": round(DS_UNI, 3),
        "max_density": SPARSE_DENSITY,
    },
    "px_per_bu": 70.0,
    "panels": RESULTS,
}

pairs = []


def check_pair(p, q, allowed, claim):
    if p not in STATES or q not in STATES:
        return
    diff = sorted([d for d in DIMS if STATES[p][d] != STATES[q][d]])
    ok = set(diff) <= set(allowed)
    pairs.append(
        {
            "pair": f"{p} vs {q}",
            "differs_in": diff,
            "allowed": sorted(allowed),
            "ok": ok,
            "claim": claim,
        }
    )
    log(
        f"[pair] {p} vs {q}: differs in {diff} (allowed {sorted(allowed)}) "
        f"-> {'OK' if ok else 'VIOLATION'}"
    )


check_pair(
    "M01", "M02", {"material"}, "clear vs amber glass: one RGB triple, nothing else"
)
check_pair(
    "M00", "M19", {"material"}, "category glass: only the haem style's material changed"
)
check_pair(
    "M17", "M18", {"scatter"}, "exposure-bound vs uniform moss: the density field ALONE"
)
check_pair(
    "S17",
    "S18",
    {"scatter"},
    "the same pair at a tenth the density, where coverage is unsaturated",
)
check_pair("M11", "G1", {"ground"}, "pale material, ground albedo alone")
check_pair("M11", "G2", {"ground"}, "pale material, ground albedo alone")
check_pair("G1", "G2", {"ground"}, "light vs dark ground, same material")
check_pair(
    "M01",
    "X1",
    {"world", "lights"},
    "same clear glass, with and without a room to refract",
)
manifest["pairs"] = pairs

suffix = f"_{TAG}" if TAG else ""
with open(os.path.join(OUT, f"manifest{suffix}.json"), "w") as f:
    json.dump(manifest, f, indent=2)
with open(os.path.join(OUT, f"build_log{suffix}.txt"), "w") as f:
    f.write("\n".join(LOG))

log("\n===== SUMMARY =====")
for r in RESULTS:
    log(
        f"{r['id']:4} {r['status']:6} {r.get('seconds', '?'):>6}s  {r['name']}"
        + (f"  inst={r.get('instances')}" if r.get("instances") else "")
        + (f"  VIOLATIONS={r['violations']}" if r["violations"] else "")
        + (f"  ERR={r.get('error')}" if r["status"] != "ok" else "")
    )
log(f"total render seconds: {sum(r.get('seconds', 0) for r in RESULTS):.1f}")
