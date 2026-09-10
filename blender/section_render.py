#!/usr/bin/env python3
"""
section_render.py -- SHEETS C and D: cross-sections, and geometry that carries
a measurement.

Steps 3 and 4 of the two-direction plan. Two modes:

    --mode sections    the backbone swept with profiles no viewer offers
    --mode databound   ribbon thickness driven by a per-residue measurement

    blender --background --python blender/section_render.py -- --mode sections --subject 4HHB
    blender --background --python blender/section_render.py -- --mode databound --subject P04637 --plddt

--plddt is not cosmetic. b_factor holds crystallographic B on an experimental
structure and pLDDT confidence on a predicted one, and NOTHING IN THE FILE
DISTINGUISHES THEM -- 4HHB's B runs 4.91..80.12, which sails through any range
test for pLDDT and was duly captioned as prediction confidence once. So the
meaning is declared on the command line rather than guessed, and every Sheet D
caption takes its wording from it.

NEVER pass --factory-startup: it disables the MolecularNodes extension.

The mechanism, measured before this was written
------------------------------------------------
Atoms to CA Curves -> Curve to Mesh(Profile Curve) sweeps any closed curve
along the backbone: square bar, hexagonal rod, star, tape, anything closed.

The profile is in the CURVE's space, where the whole 4HHB tetramer spans about
6.5 units -- roughly ten angstroms per unit. Style Cartoon's own sockets are
NOT in that space (its helix_width of 2.2 is angstrom-scale). Assuming they
matched put every profile about 20x too large, so residues 0.38 units apart
overlapped violently and every swept panel rendered as a ball of shards -- with
entirely plausible vertex counts and surface areas throughout. A ribbon a few
angstroms across is r ~ 0.10-0.20 here.

Atoms to CA Curves also returns a POLYLINE, one straight segment per residue.
Swept directly it is an angular wireframe, which is faithful to the data and
quite unlike a ribbon diagram; Set Spline Type + Resample Curve give the smooth
rod that "ribbon" implies. Both are on the sheet.

A section that VARIES along the chain goes through Curve to Mesh > Scale.
It does NOT go through Set Curve Radius, which is a no-op in this chain: a
hard-coded radius of 2.0 left the swept mesh bit-identical to the untouched
one, down to the last digit of its surface area. (That probe ran at the old
oversized profile scale, so its absolute numbers do not correspond to any panel
here; the no-op is what transfers.) Exactly the kind of thing that renders a
uniform tube while looking deliberate.

Controls, which are the point of Sheet D
----------------------------------------
A driven panel on its own proves nothing: it differs from the default, but so
would any other number. Every driven panel here ships with TWO controls:

  FLAT   the same field's mean, applied uniformly. Separates "varies along the
         chain" from "differs from the default".
  SHUFFLE the same values, randomly permuted across residues. Identical
         distribution, wrong assignment. If driven and shuffled read the same,
         the binding carries no information and the panel is decoration.

The shuffle is seeded so the sheet is reproducible.

A guard the previous sheets did not need
-----------------------------------------
sig_geometry() in chonk_render.py hashes socket default_values. A data-bound
panel changes a LINK, not a value, so that signature is blind to it -- it would
report a driven panel as identical to its control and pass. This script hashes
the modifier tree's links as well.
"""

import hashlib
import json
import math
import os
import sys
import time
import traceback

import bpy
import numpy as np
from mathutils import Euler, Vector

# ---------------------------------------------------------------- args

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
MODE = "sections"
SUBJECT = None
SAMPLES = 64
ONLY = None
OUT = None
for i, a in enumerate(argv):
    if a == "--mode":
        MODE = argv[i + 1]
    if a == "--subject":
        SUBJECT = argv[i + 1]
    if a == "--samples":
        SAMPLES = int(argv[i + 1])
    if a == "--only":
        ONLY = set(argv[i + 1].split(","))
    if a == "--out":
        OUT = argv[i + 1]

assert MODE in ("sections", "databound"), f"unknown mode {MODE!r}"
if SUBJECT is None:
    SUBJECT = "4HHB" if MODE == "sections" else "P04637"

if OUT is None:
    OUT = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "research", "renders", MODE
    )
OUT = os.path.join(OUT, SUBJECT)
os.makedirs(OUT, exist_ok=True)

CACHE = os.path.expanduser("~/MolecularNodesCache")
RES = (1000, 750)
LENS = 50.0
SENSOR = 36.0
MARGIN = 0.15
SEED = 20260909

LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(str(msg))


bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.molecularnodes")
import bl_ext.blender_org.molecularnodes as mn  # noqa: E402
from bl_ext.blender_org.molecularnodes.nodes import nodes as MNNODES  # noqa: E402

mn.material.Default(name="_seed_groups")

# ================================================================ scene


def purge_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)


purge_scene()
scene = bpy.context.scene

cam_data = bpy.data.cameras.new("SecCam")
cam_data.lens = LENS
cam_data.sensor_width = SENSOR
cam_data.clip_start = 0.1
cam_data.clip_end = 5000.0
CAM_OB = bpy.data.objects.new("SecCam", cam_data)
scene.collection.objects.link(CAM_OB)
scene.camera = CAM_OB

canvas = mn.Canvas(engine="CYCLES", resolution=RES, template=None)
canvas.samples = SAMPLES
scene = bpy.context.scene
assert canvas.scene == scene and scene.camera == CAM_OB

scene.render.engine = "CYCLES"
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 8
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

# ================================================================ subject

MOL = mn.Molecule.load(os.path.join(CACHE, f"{SUBJECT}.bcif"), name="subject")
OB = MOL.object
OB_M0 = OB.matrix_world.copy()
N_ATOMS = MOL.universe.atoms.n_atoms
NT = OB.modifiers[0].node_group
log(f"[subject] {SUBJECT} atoms={N_ATOMS}")

AG = MOL.universe.select_atoms("protein")
assert AG.n_atoms > 0, "selection 'protein' matched zero atoms"
assert len(MOL.position) == N_ATOMS, "vertex/atom mapping is not 1:1"
N_CA = int(MOL.named_attribute("is_alpha_carbon").sum())
assert N_CA > 0, (
    f"{SUBJECT} has no alpha carbons -- Atoms to CA Curves would return an "
    f"empty curve and every swept panel would be blank"
)
log(f"[selection] protein -> {AG.n_atoms} atoms, {N_CA} alpha carbons")

# ---- the data channel, for --mode databound -------------------------------
# b_factor needs NO JOIN: it is already on the mesh. On an AlphaFold model it
# holds the model's own per-residue confidence (pLDDT, 0-100).
BF = MOL.named_attribute("b_factor").astype(np.float32)
BF_LO, BF_HI = float(BF.min()), float(BF.max())
log(f"[data] b_factor range {BF_LO:.2f}..{BF_HI:.2f} mean {float(BF.mean()):.2f}")

# What this field MEANS cannot be inferred from its range. A crystallographic
# B-factor also lands inside [0, 100] -- 4HHB's is 4.91..80.12, which sailed
# through a range test and got captioned as prediction confidence. It is only
# pLDDT when the structure is a predicted model, so that is declared, not
# guessed.
IS_PLDDT = "--plddt" in argv
CHANNEL = "pLDDT confidence" if IS_PLDDT else "crystallographic B-factor"
log(f"[data] channel declared as: {CHANNEL}")


def norm_to(lo, hi, invert=False):
    """b_factor mapped into [lo, hi]."""
    span = max(BF_HI - BF_LO, 1e-9)
    t = (BF - BF_LO) / span
    if invert:
        t = 1.0 - t
    return (lo + t * (hi - lo)).astype(np.float32)


# ================================================================ styles


def style_nodes_of(ob):
    return [n for n in ob.modifiers[0].node_group.nodes if n.name.startswith("Style")]


def add_style_tracked(mol, *args, **kwargs):
    ng = mol.object.modifiers[0].node_group
    before = set(ng.nodes.keys())
    mol.add_style(*args, **kwargs)
    new = [n for k, n in ng.nodes.items() if k not in before and k.startswith("Style")]
    assert len(new) == 1, f"expected 1 new style node, got {[n.name for n in new]}"
    return new[0]


GRP_IN = next(n for n in NT.nodes if n.type == "GROUP_INPUT")
GRP_OUT = next(n for n in NT.nodes if n.type == "GROUP_OUTPUT")

ST_CARTOON = add_style_tracked(MOL, "cartoon", selection="protein")
ST_CARTOON.inputs["Quality"].default_value = 3
CARTOON_LINK = next(lk for lk in NT.links if lk.to_node == GRP_OUT)
BASE_T = ST_CARTOON.inputs["Helix Thickness"].default_value
BASE_W = ST_CARTOON.inputs["Helix Width"].default_value
BASE_ST = ST_CARTOON.inputs["Sheet Thickness"].default_value
BASE_SW = ST_CARTOON.inputs["Sheet Width"].default_value

# ---- the curve-sweep chain, built once and rewired per panel ---------------
CA_CURVES = NT.nodes.new("GeometryNodeGroup")
CA_CURVES.node_tree = MNNODES.append(name="Atoms to CA Curves")
CA_CURVES.location = (-400, -600)
NT.links.new(GRP_IN.outputs[0], CA_CURVES.inputs[0])

# Atoms to CA Curves returns a POLYLINE -- one straight segment per residue,
# with a sharp corner at every alpha carbon. Swept directly it reads as an
# angular wireframe rather than a rod, which is faithful to the data and quite
# unlike a ribbon diagram. Converting the spline type and resampling gives the
# smooth curve a ribbon implies. Both are kept: SMOOTH is the default and RAW
# is rendered once, as itself, rather than being treated as a defect.
SPLINE = NT.nodes.new("GeometryNodeCurveSplineType")
SPLINE.spline_type = "NURBS"
SPLINE.location = (-200, -600)
NT.links.new(CA_CURVES.outputs[0], SPLINE.inputs["Curve"])

RESAMPLE = NT.nodes.new("GeometryNodeResampleCurve")
# In Blender 5 the resample mode is a MENU SOCKET, not a node property --
# `node.mode = "LENGTH"` raises AttributeError.
RESAMPLE.inputs["Mode"].default_value = "Length"
RESAMPLE.inputs["Length"].default_value = 0.06
RESAMPLE.location = (0, -600)
NT.links.new(SPLINE.outputs["Curve"], RESAMPLE.inputs["Curve"])

C2M = NT.nodes.new("GeometryNodeCurveToMesh")
C2M.location = (200, -600)
NT.links.new(RESAMPLE.outputs["Curve"], C2M.inputs["Curve"])


def curve_source(smooth=True):
    """Feed Curve to Mesh from either the smoothed curve or the raw CA trace."""
    for lk in list(C2M.inputs["Curve"].links):
        NT.links.remove(lk)
    NT.links.new((RESAMPLE if smooth else CA_CURVES).outputs[0], C2M.inputs["Curve"])


assert any(s.name == "Scale" for s in C2M.inputs), (
    "Curve to Mesh has no Scale input on this Blender; a varying section would "
    "have to come from somewhere else"
)

PROFILE_NODES = {}


def profile_circle(radius=1.0, resolution=12):
    key = ("circle", radius, resolution)
    if key not in PROFILE_NODES:
        n = NT.nodes.new("GeometryNodeCurvePrimitiveCircle")
        n.inputs["Radius"].default_value = radius
        n.inputs["Resolution"].default_value = resolution
        PROFILE_NODES[key] = n
    return PROFILE_NODES[key]


def profile_quad(w, h):
    key = ("quad", w, h)
    if key not in PROFILE_NODES:
        n = NT.nodes.new("GeometryNodeCurvePrimitiveQuadrilateral")
        n.inputs["Width"].default_value = w
        n.inputs["Height"].default_value = h
        PROFILE_NODES[key] = n
    return PROFILE_NODES[key]


def profile_star(points, inner, outer):
    key = ("star", points, inner, outer)
    if key not in PROFILE_NODES:
        n = NT.nodes.new("GeometryNodeCurveStar")
        n.inputs["Points"].default_value = points
        n.inputs["Inner Radius"].default_value = inner
        n.inputs["Outer Radius"].default_value = outer
        PROFILE_NODES[key] = n
    return PROFILE_NODES[key]


ATTR_NODES = {}


def attr_node(name):
    if name not in ATTR_NODES:
        n = NT.nodes.new("GeometryNodeInputNamedAttribute")
        n.data_type = "FLOAT"
        n.inputs["Name"].default_value = name
        ATTR_NODES[name] = n
    return ATTR_NODES[name]


def clear_output():
    for lk in list(NT.links):
        if lk.to_node == GRP_OUT:
            NT.links.remove(lk)


def use_cartoon():
    clear_output()
    NT.links.new(ST_CARTOON.outputs[0], GRP_OUT.inputs[0])


def use_sweep(profile, scale_attr=None, scale_value=1.0, smooth=True):
    clear_output()
    curve_source(smooth)
    for lk in list(C2M.inputs["Profile Curve"].links):
        NT.links.remove(lk)
    for lk in list(C2M.inputs["Scale"].links):
        NT.links.remove(lk)
    NT.links.new(profile.outputs["Curve"], C2M.inputs["Profile Curve"])
    if scale_attr:
        NT.links.new(attr_node(scale_attr).outputs["Attribute"], C2M.inputs["Scale"])
    else:
        C2M.inputs["Scale"].default_value = scale_value
    NT.links.new(C2M.outputs["Mesh"], GRP_OUT.inputs[0])


def drive_socket(node, sock_name, attr):
    """Link a stored per-atom attribute into a style socket."""
    for s in node.inputs:
        if s.name == sock_name:
            for lk in list(s.links):
                NT.links.remove(lk)
            if attr is not None:
                NT.links.new(attr_node(attr).outputs["Attribute"], s)
            return s
    raise RuntimeError(f"no socket {sock_name!r}")


def undrive(node, sock_name, value):
    for s in node.inputs:
        if s.name == sock_name:
            for lk in list(s.links):
                NT.links.remove(lk)
            s.default_value = value
            return s
    raise RuntimeError(f"no socket {sock_name!r}")


def reset_tree():
    for n in (ST_CARTOON,):
        for s in n.inputs:
            for lk in list(s.links):
                if lk.from_node in ATTR_NODES.values():
                    NT.links.remove(lk)
    ST_CARTOON.inputs["Helix Thickness"].default_value = BASE_T
    ST_CARTOON.inputs["Helix Width"].default_value = BASE_W
    ST_CARTOON.inputs["Sheet Thickness"].default_value = BASE_ST
    ST_CARTOON.inputs["Sheet Width"].default_value = BASE_SW
    ST_CARTOON.inputs["Quality"].default_value = 3
    for lk in list(C2M.inputs["Scale"].links):
        NT.links.remove(lk)
    C2M.inputs["Scale"].default_value = 1.0
    use_cartoon()


# ================================================================ materials


def new_mat(name):
    if name in bpy.data.materials:
        bpy.data.materials.remove(bpy.data.materials[name])
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    return m, nt, out


def mat_matte(name, rgb=(0.62, 0.62, 0.63), roughness=0.42):
    m, nt, out = new_mat(name)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.location = (250, 0)
    b.inputs["Base Color"].default_value = (*rgb, 1.0)
    b.inputs["Roughness"].default_value = roughness
    b.inputs["Specular IOR Level"].default_value = 0.35
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


M_MATTE = mat_matte("M_matte")
# The sweep chain has no style node, so its material is assigned on the object
# rather than through a Style socket.
if not OB.data.materials:
    OB.data.materials.append(M_MATTE)
else:
    OB.data.materials[0] = M_MATTE
for n in style_nodes_of(OB):
    mn.material.set_socket_material(n.inputs["Material"], M_MATTE)

# ================================================================ studio


def make_ground_mat():
    m, nt, out = new_mat("M_ground")
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.location = (250, 0)
    b.inputs["Roughness"].default_value = 0.62
    b.inputs["Specular IOR Level"].default_value = 0.30
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


GROUND_MAT = make_ground_mat()


def set_ground(rgb):
    GROUND_MAT.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        *rgb,
        1.0,
    )


def set_world_flat(value, rgb=(1.0, 1.0, 1.0)):
    w = scene.world or bpy.data.worlds.new("W")
    scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (*rgb, 1.0)
    bg.inputs["Strength"].default_value = value
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


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


# ---------------------------------------------------------------- camera

FRAME_PTS = MOL.position[AG.indices]
CENTROID = Vector(FRAME_PTS.mean(axis=0).tolist())
CAM_OB.rotation_euler = Euler((math.radians(78.0), 0.0, math.radians(-20.0)), "XYZ")
CAM_OB.location = CENTROID + Vector((0, -40, 8))
bpy.context.view_layer.update()
canvas.camera.frame_points(FRAME_PTS, margin=MARGIN)
bpy.context.view_layer.update()
TAN_X = (SENSOR / 2.0) / LENS
TAN_Y = TAN_X * RES[1] / RES[0]
CAM_MATRIX = CAM_OB.matrix_world.copy()


def eval_world_verts():
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    out = []
    for inst in dg.object_instances:
        o = inst.object
        if o is None or o.original is not OB:
            continue
        mw = inst.matrix_world.copy()
        try:
            me = o.to_mesh()
        except Exception:
            continue
        if me is None or len(me.vertices) == 0:
            continue
        co = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        m = np.array(mw).reshape(4, 4)
        out.append(co @ m[:3, :3].T + m[:3, 3])
        o.to_mesh_clear()
    return np.concatenate(out, axis=0) if out else np.zeros((0, 3), dtype=np.float32)


def ndc_extent(pts=None):
    if pts is None:
        pts = eval_world_verts()
    if len(pts) == 0:
        return None
    inv = np.array(CAM_MATRIX.inverted()).reshape(4, 4)
    q = pts @ inv[:3, :3].T + inv[:3, 3]
    d = -q[:, 2]
    if float(d.min()) <= 1e-6:
        return 99.0
    return round(
        max(
            float(np.abs(q[:, 0] / (d * TAN_X)).max()),
            float(np.abs(q[:, 1] / (d * TAN_Y)).max()),
        ),
        4,
    )


# Solve the frame on the largest configuration this sheet uses.


def _collect(build):
    build()
    bpy.context.view_layer.update()
    return eval_world_verts()


# The frame must cover EVERY panel on the sheet, not just the largest one of
# whichever family happens to be first. A first pass here solved on the
# cartoon family alone and the three swept panels duly clipped -- caught by the
# ndc guard, but only after they had rendered.
_probes = []
if MODE == "sections":
    _probes.append(_collect(lambda: use_sweep(profile_quad(0.30, 0.30), scale_value=1.4)))
    _probes.append(
        _collect(lambda: use_sweep(profile_star(12, 0.13, 0.19), scale_value=1.4))
    )
else:
    MOL.store_named_attribute(norm_to(BASE_T, 4.5), "fat_probe", "FLOAT", "POINT")
    MOL.store_named_attribute(
        np.full(N_ATOMS, 2.4, dtype=np.float32), "sw_probe", "FLOAT", "POINT"
    )

    def _cart():
        use_cartoon()
        drive_socket(ST_CARTOON, "Helix Thickness", "fat_probe")
        drive_socket(ST_CARTOON, "Sheet Thickness", "fat_probe")

    _probes.append(_collect(_cart))
    _probes.append(
        _collect(lambda: use_sweep(profile_star(6, 0.07, 0.20), scale_attr="sw_probe"))
    )
    _probes.append(
        _collect(lambda: use_sweep(profile_circle(0.12, 12), scale_attr="sw_probe"))
    )

FAT_PTS = np.concatenate([p for p in _probes if len(p)], axis=0)
assert len(FAT_PTS) > 0, "largest configuration evaluated to no geometry"
log(f"[frame] union of {len(_probes)} probe configurations, {len(FAT_PTS):,} vertices")
canvas.camera.frame_points(FAT_PTS, margin=MARGIN)
bpy.context.view_layer.update()
CAM_MATRIX = CAM_OB.matrix_world.copy()
CAM_POS = CAM_MATRIX.translation.copy()
CAM_FWD = (CAM_MATRIX.to_3x3() @ Vector((0, 0, -1))).normalized()
D0 = (CENTROID - CAM_POS).dot(CAM_FWD)
ORTHO_SCALE = 2.0 * D0 * TAN_X
Z_GROUND = float(FAT_PTS[:, 2].min()) - 0.02
log(
    f"[camera] reframed on largest config, ndc={ndc_extent(FAT_PTS)} "
    f"ground={Z_GROUND:.3f}"
)
reset_tree()
bpy.context.view_layer.update()

CYC = build_cyclorama(Z_GROUND, CENTROID.y + 62.0)
CYC.data.materials.append(GROUND_MAT)
KEY_LOC = CENTROID + Vector((-26.0, -22.0, 26.0))


def restore_camera():
    CAM_OB.matrix_world = CAM_MATRIX.copy()
    CAM_OB.data.type = "PERSP"
    CAM_OB.data.lens = LENS
    CAM_OB.data.ortho_scale = ORTHO_SCALE
    CAM_OB.data.shift_x = 0.0
    CAM_OB.data.shift_y = 0.0
    bpy.context.view_layer.update()


def rig_studio_key():
    clear_lights()
    add_area("Key", KEY_LOC, CENTROID, size=34.0, energy=48000.0, rgb=(1.0, 0.98, 0.95))


# ================================================================ instrument


def ev_stats():
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    verts = polys = insts = 0
    area = 0.0
    for inst in dg.object_instances:
        o = inst.object
        if o is None or o.original is not OB:
            continue
        if inst.is_instance:
            insts += 1
        try:
            me = o.to_mesh()
        except Exception:
            continue
        if me is None:
            continue
        verts += len(me.vertices)
        polys += len(me.polygons)
        area += float(sum(p.area for p in me.polygons))
        o.to_mesh_clear()
    return {"verts": verts, "polys": polys, "instances": insts, "area": round(area, 4)}


# ================================================================ harness


def _mrx(m):
    return tuple(round(v, 6) for row in m for v in row)


def sig_camera():
    return (_mrx(CAM_OB.matrix_world), round(CAM_OB.data.lens, 4), CAM_OB.data.type)


def sig_dof():
    d = CAM_OB.data.dof
    return (
        d.use_dof,
        round(d.aperture_fstop, 4),
        d.focus_object.name if d.focus_object else None,
    )


def sig_lights():
    return tuple(
        (
            o.name,
            o.data.type,
            round(o.data.energy, 4),
            tuple(round(c, 4) for c in o.data.color),
            _mrx(o.matrix_world),
        )
        for o in sorted(
            [o for o in bpy.data.objects if o.type == "LIGHT"], key=lambda x: x.name
        )
    )


def _tree_hash(nt):
    if nt is None:
        return None
    parts = []
    for n in sorted(nt.nodes, key=lambda x: x.name):
        vals = []
        for s in n.inputs:
            try:
                dv = s.default_value
                # str before __len__: see the note in sig_geometry.
                dv = (
                    dv
                    if isinstance(dv, str)
                    else tuple(round(float(x), 5) for x in dv)
                    if hasattr(dv, "__len__")
                    else round(float(dv), 5)
                    if isinstance(dv, (int, float))
                    else str(dv)
                )
            except Exception:
                dv = None
            vals.append((s.name, repr(dv)))
        parts.append((n.name, n.bl_idname, tuple(vals)))
    links = tuple(
        sorted(
            (lk.from_node.name, lk.from_socket.name, lk.to_node.name, lk.to_socket.name)
            for lk in nt.links
        )
    )
    return hashlib.sha256(repr((tuple(parts), links)).encode()).hexdigest()[:16]


def sig_world():
    w = scene.world
    return (w.name if w else None, _tree_hash(w.node_tree if w else None))


def sig_ground():
    return (_mrx(CYC.matrix_world), _tree_hash(GROUND_MAT.node_tree))


def sig_material():
    mats = [m.name if m else None for m in OB.data.materials]
    return (tuple(mats), _tree_hash(M_MATTE.node_tree))


def sig_geometry():
    """Hashes the WHOLE modifier tree -- nodes AND links.

    A data-bound panel changes a link, not a socket value. A signature that
    reads only default_values would call a driven panel identical to its
    control and pass.
    """
    return (_mrx(OB.matrix_world), _tree_hash(NT))


DIMS = {
    "camera": sig_camera,
    "dof": sig_dof,
    "lights": sig_lights,
    "world": sig_world,
    "ground": sig_ground,
    "material": sig_material,
    "geometry": sig_geometry,
}


def snapshot_state():
    return {k: f() for k, f in DIMS.items()}


# ================================================================ rendering

RESULTS = []
CARRIER = None
# geometry signature -> the panel id that first produced it
SEEN_GEOM = {}


def carrier_studio():
    restore_camera()
    OB.matrix_world = OB_M0.copy()
    d = CAM_OB.data.dof
    d.use_dof = False
    d.focus_object = None
    rig_studio_key()
    set_world_flat(0.06)
    set_ground((0.30, 0.30, 0.31))
    reset_tree()


def render(pid, name, build, note):
    global CARRIER
    if ONLY and pid not in ONLY:
        return
    fname = f"{pid}_{name}.png"
    path = os.path.join(OUT, fname)
    log(f"\n=== {pid} {name} ===")
    t0 = time.time()
    rec = {
        "id": pid,
        "name": name,
        "file": fname,
        "note": note,
        "varies": ["geometry"],
        "status": "ok",
        "violations": [],
    }
    try:
        carrier_studio()
        build()
        bpy.context.view_layer.update()
        st = snapshot_state()
        # A panel whose geometry is bit-identical to an earlier panel is a
        # panel that shows nothing new. Without this, a socket set to its own
        # default renders a duplicate and reports success.
        gkey = hashlib.sha256(repr(st["geometry"]).encode()).hexdigest()[:16]
        rec["geometry_sig"] = gkey
        if gkey in SEEN_GEOM:
            rec["violations"].append(f"IDENTICAL GEOMETRY to {SEEN_GEOM[gkey]}")
            log(f"[discipline] !! identical geometry to panel {SEEN_GEOM[gkey]}")
        else:
            SEEN_GEOM[gkey] = pid

        if CARRIER is None:
            CARRIER = st
            log("[discipline] carrier established")
        else:
            for dim, val in st.items():
                if dim == "geometry":
                    continue
                if val != CARRIER[dim]:
                    rec["violations"].append(f"UNDECLARED DRIFT in {dim!r}")
                    log(f"[discipline] !! UNDECLARED DRIFT in {dim!r}")
        stats = ev_stats()
        rec["stats"] = stats
        rec["ndc_extent"] = ndc_extent()
        log(f"[stats] {stats} ndc={rec['ndc_extent']}")
        assert stats["area"] > 0.0, f"{pid} produced ZERO surface area"
        if rec["ndc_extent"] and rec["ndc_extent"] > 1.0:
            rec["violations"].append(f"CLIPS FRAME ({rec['ndc_extent']:.3f})")
            log(f"[discipline] !! clips frame at {rec['ndc_extent']:.3f}")
        canvas.snapshot(path)
        rec["seconds"] = round(time.time() - t0, 1)
        rec["bytes"] = os.path.getsize(path)
        log(f"[render] {fname} {rec['seconds']}s")
    except Exception as e:
        rec["status"] = "FAILED"
        rec["error"] = f"{type(e).__name__}: {e}"
        log(f"[render] {pid} FAILED: {rec['error']}")
        log(traceback.format_exc())
    RESULTS.append(rec)


# ================================================================ panels

if MODE == "sections":
    # PROFILE UNITS. The CA curve lives in the same space as the evaluated
    # mesh, where the whole tetramer spans about 6.5 units -- so ONE UNIT IS
    # ROUGHLY TEN ANGSTROMS. Style Cartoon's own sockets do NOT use this space
    # (its helix_width of 2.2 is Angstrom-scale), and assuming they matched is
    # what shredded the first version of this sheet: a radius-1.0 profile is a
    # 20 A tube on a 65 A molecule, so consecutive residues 0.38 units apart
    # overlapped violently and every panel came out a ball of shards. The
    # numbers looked entirely plausible while the pictures were nonsense.
    #
    # A ribbon a few Angstroms across is therefore r ~ 0.10-0.20.
    PANELS = [
        (
            "C01",
            "cartoon-control",
            use_cartoon,
            "the shipped cartoon, for reference -- everything below replaces it "
            "with a swept curve",
        ),
        (
            "C02",
            "tube-thin",
            lambda: use_sweep(profile_circle(0.08, 12)),
            "circle profile r=0.08 (~1.6 A across) -- a plain thin tube",
        ),
        (
            "C02b",
            "ca-trace-raw",
            lambda: use_sweep(profile_circle(0.08, 12), smooth=False),
            "the SAME tube on the unsmoothed curve -- Atoms to CA Curves returns a "
            "polyline, so this is one straight segment per residue with a corner at "
            "every alpha carbon",
        ),
        (
            "C03",
            "tube-fat",
            lambda: use_sweep(profile_circle(0.20, 12)),
            "circle profile r=0.20 (~4 A across) -- the same drawing, thicker",
        ),
        (
            "C04",
            "hex-rod",
            lambda: use_sweep(profile_circle(0.15, 6)),
            "circle at resolution 6 -- a hexagonal rod, flat facets running the "
            "length of the chain",
        ),
        (
            "C05",
            "tri-rod",
            lambda: use_sweep(profile_circle(0.18, 3)),
            "circle at resolution 3 -- a triangular rod",
        ),
        (
            "C06",
            "flat-tape",
            lambda: use_sweep(profile_quad(0.30, 0.05)),
            "quadrilateral 0.30 x 0.05 -- a flat tape. Blender's minimum-twist "
            "frame decides its roll, so watch whether it lies flat or tangles",
        ),
        (
            "C07",
            "square-bar",
            lambda: use_sweep(profile_quad(0.16, 0.16)),
            "quadrilateral 0.16 x 0.16 -- a square bar",
        ),
        (
            "C08",
            "deep-bar",
            lambda: use_sweep(profile_quad(0.10, 0.28)),
            "quadrilateral 0.10 x 0.28 -- taller than wide, a standing blade",
        ),
        (
            "C09",
            "big-square",
            lambda: use_sweep(profile_quad(0.30, 0.30)),
            "quadrilateral 0.30 x 0.30 -- a heavy square rod",
        ),
        (
            "C10",
            "star-6",
            lambda: use_sweep(profile_star(6, 0.07, 0.20)),
            "six-point star -- a fluted rod",
        ),
        (
            "C11",
            "star-4",
            lambda: use_sweep(profile_star(4, 0.05, 0.22)),
            "four-point star -- sharper flutes, deeper valleys",
        ),
        (
            "C12",
            "star-12",
            lambda: use_sweep(profile_star(12, 0.13, 0.19)),
            "twelve-point star -- shallow flutes, close to a ribbed tube",
        ),
    ]
else:
    # --- the data channel, and its two controls ---------------------------
    #
    # All three fields are built by the SAME per-residue aggregation and differ
    # ONLY in assignment. That matters: an earlier version drove the panel with
    # the raw per-atom field but shuffled a per-residue MAXIMUM, so the driven
    # and shuffled panels differed in aggregation as well as in assignment and
    # the control was not a control. Averaging within a residue first makes the
    # comparison honest.
    LO, HI = 0.5, 4.0
    rng = np.random.default_rng(SEED)
    res_key = MOL.named_attribute("chain_id").astype(
        np.int64
    ) * 100000 + MOL.named_attribute("res_id").astype(np.int64)
    uniq, inv = np.unique(res_key, return_inverse=True)

    _sum = np.zeros(len(uniq), dtype=np.float64)
    np.add.at(_sum, inv, norm_to(LO, HI).astype(np.float64))
    per_res = (_sum / np.bincount(inv, minlength=len(uniq))).astype(np.float32)

    driven = per_res[inv].astype(np.float32)
    flat = np.full(len(driven), float(driven.mean()), dtype=np.float32)
    # norm_to(lo, hi, invert=True) is exactly lo + hi - norm_to(lo, hi), so the
    # inverted field stays on the same per-residue aggregation as the rest.
    inverted = (LO + HI - per_res)[inv].astype(np.float32)

    # The shuffle preserves the per-RESIDUE distribution exactly -- it is a
    # permutation of per_res -- but NOT the per-atom one, because residues hold
    # different numbers of atoms and each residue's value is broadcast to all
    # of them. Glycine carries its value on ~7 atoms and tryptophan on ~24, so
    # permuting values across residues reweights the per-atom histogram.
    #
    # An earlier version of this assert compared per-atom medians and duly
    # fired: it was asserting something false. The claim being made is about
    # residues, so it is checked on the residue-level array.
    _perm = rng.permutation(len(uniq))
    shuffled = per_res[_perm][inv].astype(np.float32)
    assert np.array_equal(np.sort(per_res), np.sort(per_res[_perm])), (
        "shuffled must be a permutation of the driven per-residue values -- "
        "same multiset, different assignment"
    )
    log(
        f"[data] shuffle preserves the per-residue distribution exactly; the "
        f"per-atom mean shifts "
        f"{abs(float(driven.mean()) - float(shuffled.mean())):.4f} because "
        f"residues differ in atom count"
    )
    log(
        f"[data] {len(uniq)} residues; driven {driven.min():.2f}..{driven.max():.2f} "
        f"mean {driven.mean():.3f}; shuffled mean {shuffled.mean():.3f} "
        f"(same values, permuted across residues)"
    )

    MOL.store_named_attribute(driven, "bf_driven", "FLOAT", "POINT")
    MOL.store_named_attribute(flat, "bf_flat", "FLOAT", "POINT")
    MOL.store_named_attribute(shuffled, "bf_shuffled", "FLOAT", "POINT")
    MOL.store_named_attribute(inverted, "bf_inverted", "FLOAT", "POINT")
    # Sweep scale wants a smaller range than ribbon thickness.
    for nm, arr in (
        ("sw_driven", norm_to(0.4, 2.4)),
        ("sw_flat", np.full(N_ATOMS, float(norm_to(0.4, 2.4).mean()), dtype=np.float32)),
    ):
        MOL.store_named_attribute(arr.astype(np.float32), nm, "FLOAT", "POINT")

    chan = CHANNEL

    def cartoon_driven(attr):
        def f():
            use_cartoon()
            drive_socket(ST_CARTOON, "Helix Thickness", attr)
            drive_socket(ST_CARTOON, "Sheet Thickness", attr)

        return f

    def cartoon_flat(v):
        def f():
            use_cartoon()
            undrive(ST_CARTOON, "Helix Thickness", v)
            undrive(ST_CARTOON, "Sheet Thickness", v)

        return f

    PANELS = [
        (
            "D01",
            "control-default",
            use_cartoon,
            f"the shipped cartoon at default thickness 0.6 -- {chan} not used",
        ),
        (
            "D02",
            "driven",
            cartoon_driven("bf_driven"),
            f"ribbon thickness driven by {chan}: 0.5 where lowest, 4.0 where "
            f"highest, per residue",
        ),
        (
            "D03",
            "control-flat",
            cartoon_flat(float(driven.mean())),
            f"CONTROL: the same field's MEAN ({driven.mean():.2f}) applied "
            f"uniformly. Separates 'varies along the chain' from 'differs from "
            f"the default'",
        ),
        (
            "D04",
            "control-shuffled",
            cartoon_driven("bf_shuffled"),
            "CONTROL: the same values, randomly permuted across residues. Same "
            "distribution, wrong assignment -- if this reads like D02 the binding "
            "carries no information",
        ),
        (
            "D05",
            "driven-inverted",
            cartoon_driven("bf_inverted"),
            f"thickness driven by INVERTED {chan} -- thick where the measurement "
            f"is lowest",
        ),
        (
            "D06",
            "sweep-driven",
            lambda: use_sweep(profile_circle(0.12, 12), scale_attr="sw_driven"),
            f"swept circular section, scale driven by {chan} -- the cross-section "
            f"itself changes along the chain",
        ),
        (
            "D07",
            "sweep-flat",
            lambda: use_sweep(profile_circle(0.12, 12), scale_attr="sw_flat"),
            "CONTROL for D06: the same field's mean, applied uniformly",
        ),
        (
            "D08",
            "sweep-star-driven",
            lambda: use_sweep(profile_star(6, 0.07, 0.20), scale_attr="sw_driven"),
            f"six-point star section, scale driven by {chan} -- section SHAPE and "
            f"section SIZE both carrying, one by choice and one by measurement",
        ),
    ]

log(f"[plan] mode={MODE} {len(PANELS)} panels -> {OUT}")
if ONLY:
    _ids = {p[0] for p in PANELS}
    _unknown = ONLY - _ids
    assert not _unknown, (
        f"--only names panels that do not exist: {sorted(_unknown)}; "
        f"this sheet has {sorted(_ids)}"
    )

for pid, name, build, note in PANELS:
    render(pid, name, build, note)

# ================================================================ manifest

ok = [r for r in RESULTS if r["status"] == "ok"]
bad = [r for r in RESULTS if r["status"] != "ok"]
viol = [r for r in RESULTS if r["violations"]]
log("\n" + "=" * 62)
log(
    f"[done] {len(ok)}/{len(RESULTS)} rendered, {len(bad)} failed, "
    f"{len(viol)} with violations"
)
if ok:
    log(
        "[areas] "
        + "  ".join(f"{r['id']}:{r['stats']['area']:.1f}" for r in ok if "stats" in r)
    )
for r in bad:
    log(f"[failed] {r['id']}: {r.get('error')}")
for r in viol:
    log(f"[violation] {r['id']}: {r['violations']}")

man = {
    "subject": SUBJECT,
    "mode": MODE,
    "samples": SAMPLES,
    "resolution": list(RES),
    "seed": SEED,
    "b_factor_range": [BF_LO, BF_HI],
    "plddt_declared": IS_PLDDT,
    "panels": RESULTS,
}
with open(os.path.join(OUT, "manifest.json"), "w") as f:
    json.dump(man, f, indent=2, default=str)
with open(os.path.join(OUT, "log.txt"), "w") as f:
    f.write("\n".join(LOG))
log(f"[done] manifest -> {os.path.join(OUT, 'manifest.json')}")
