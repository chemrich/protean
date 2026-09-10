#!/usr/bin/env python3
"""
chonk_render.py -- SHEET B: the ribbon, thickened and reshaped.

Step 1 of the two-direction plan. Every panel varies ONE geometry setting on
Style Cartoon and nothing else. Material is a flat achromatic dielectric on
every panel, deliberately: material and geometry confound each other badly
(a lighting rig alone once moved subject luminance 112 -> 162), so the sheet
that asks "what shape is this" must not also change how it is lit.

Run ONE SUBJECT PER PROCESS. The harness holds a single frozen camera and a
single carrier snapshot; a second subject means a second frame_points solve,
after which every non-geometry dimension stops guarding anything.

    blender --background --python blender/chonk_render.py -- --subject 4HHB
    blender --background --python blender/chonk_render.py -- --subject 1EMA

NEVER pass --factory-startup: it disables the MolecularNodes extension.

Rig provenance
--------------
The studio (camera solve, cyclorama, ground, key light, world) is copied from
stylespace_render.py rather than imported -- that script builds its whole panel
set at import time and cannot be imported.

The copies are CLOSE, NOT VERBATIM, and the difference matters to anyone editing
both: the studio and the signature functions were carried over, while the
material, the subject handle and the panel loop differ per sheet, and this file
fixed a menu-socket bug in the signature that the original had. Expect to make
the same edit twice. The refactor is still deferred because each script is
verified by rendering, and a shared module would put every sheet back in the
queue to re-verify at once.

Guards, and why each one is here
--------------------------------
- setp() RECORDS a clamp and continues. It never asserts equality on a float:
  a float32 socket returns 1.600000023841858 for 1.6, so an equality assert
  fires on nearly every set and kills the sheet at panel 1.
- ev_stats() counts the whole evaluated geometry set, not just to_mesh().
  Surfaces build inside per-chain instances and spheres are instances by
  default, so a to_mesh()-only stat is blind to exactly what it guards.
- AREA is the instrument, not vertex count or bounding box. Measured: vertex
  count is bit-identical (21,852) across the entire thickness range and the
  bbox moves 5% across a 7.5x change. Both would report a null while the
  picture changed underneath them.
- Secondary structure is COUNTED before panel 1. 4HHB has zero sheet residues,
  so a sheet knob tested there renders an identical picture and reports
  success. Two of four menu sockets were caught this way.
- Frame headroom is projected per panel. The fat rungs can leave the frame,
  and the margin is a fraction, so it scales with the subject.
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
SUBJECT = "4HHB"
SAMPLES = 64
ONLY = None
OUT = None
for i, a in enumerate(argv):
    if a == "--subject":
        SUBJECT = argv[i + 1]
    if a == "--samples":
        SAMPLES = int(argv[i + 1])
    if a == "--only":
        ONLY = set(argv[i + 1].split(","))
    if a == "--out":
        OUT = argv[i + 1]

if OUT is None:
    OUT = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "research", "renders", "chonk"
    )
OUT = os.path.join(OUT, SUBJECT)
os.makedirs(OUT, exist_ok=True)

CACHE = os.path.expanduser("~/MolecularNodesCache")
RES = (1000, 750)
LENS = 50.0
SENSOR = 36.0
MARGIN = 0.15

LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(str(msg))


# ---------------------------------------------------------------- addon

bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.molecularnodes")
import bl_ext.blender_org.molecularnodes as mn  # noqa: E402

mn.material.Default(name="_seed_groups")

# ================================================================ scene


def purge_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)


purge_scene()
scene = bpy.context.scene

cam_data = bpy.data.cameras.new("ChonkCam")
cam_data.lens = LENS
cam_data.sensor_width = SENSOR
cam_data.clip_start = 0.1
cam_data.clip_end = 5000.0
CAM_OB = bpy.data.objects.new("ChonkCam", cam_data)
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
scene.cycles.max_bounces = 8
scene.cycles.diffuse_bounces = 3
scene.cycles.glossy_bounces = 4
scene.cycles.transmission_bounces = 4
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
assert len(MOL.position) == N_ATOMS, "vertex/atom mapping is not 1:1"
log(f"[subject] {SUBJECT} atoms={N_ATOMS}")

# MDAnalysis syntax, not PyMOL. A wrong token is swallowed as a UserWarning
# and renders a perfect picture of nothing highlighted.
AG_PROTEIN = MOL.universe.select_atoms("protein")
assert AG_PROTEIN.n_atoms > 0, "selection 'protein' matched zero atoms"
log(f"[selection] 'protein' -> {AG_PROTEIN.n_atoms} atoms")

# ---- PREFLIGHT: count secondary structure before rendering anything -------
# MN encodes sec_struct as an int: 0 unassigned, 1 helix, 2 sheet, 3 loop.
SS_NAME = {0: "unassigned", 1: "helix", 2: "sheet", 3: "loop"}
_ss = MOL.named_attribute("sec_struct")
_ca = MOL.named_attribute("is_alpha_carbon").astype(bool)
SS_COUNTS = {
    SS_NAME.get(int(k), str(k)): int(v)
    for k, v in zip(*np.unique(_ss[_ca], return_counts=True), strict=True)
}
N_HELIX = SS_COUNTS.get("helix", 0)
N_SHEET = SS_COUNTS.get("sheet", 0)
log(f"[preflight] {SUBJECT} secondary structure by CA: {SS_COUNTS}")
assert N_HELIX + N_SHEET > 0, (
    f"{SUBJECT} has no assigned helix or sheet -- Style Cartoon would route "
    f"every atom to the loop branch and every panel would be the same picture"
)

# ================================================================ styles


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


ST_CARTOON = add_style_tracked(MOL, "cartoon", selection="protein")
ST_CARTOON.inputs["Quality"].default_value = 3
log(f"[styles] {[n.name for n in style_nodes_of(OB)]}")

# Socket defaults captured once, so every ladder is expressed relative to the
# shipped value rather than to whatever the previous panel left behind.
BASE = {
    s.name: (s.default_value if not hasattr(s.default_value, "__len__") else None)
    for s in ST_CARTOON.inputs
    if s.type in ("VALUE", "INT", "BOOLEAN")
}
BASE_MENU = {s.name: s.default_value for s in ST_CARTOON.inputs if s.type == "MENU"}
log(
    f"[styles] cartoon defaults: { {k: round(v, 4) for k, v in BASE.items() if v is not None} }"
)
log(f"[styles] cartoon menus:    {BASE_MENU}")

CLAMPS = []


def setp(node, name, value):
    """Set a socket and RECORD what actually landed. Never asserts equality."""
    sock = None
    for s in node.inputs:
        if s.name == name:
            sock = s
            break
    assert sock is not None, (
        f"no socket {name!r} on {node.name}; have {[s.name for s in node.inputs]}"
    )
    before = sock.default_value
    sock.default_value = value
    after = sock.default_value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        clamped = abs(float(after) - float(value)) > 1e-4
    else:
        clamped = str(after) != str(value)
    if clamped:
        CLAMPS.append({"socket": name, "requested": value, "actual": after})
        log(f"[setp] !! {name}: requested {value!r} -> stored {after!r} (CLAMPED)")
    return {
        "socket": name,
        "requested": value,
        "actual": after,
        "before": before,
        "clamped": clamped,
    }


def reset_cartoon():
    for k, v in BASE.items():
        if v is None:
            continue
        for s in ST_CARTOON.inputs:
            if s.name == k:
                s.default_value = v
    for k, v in BASE_MENU.items():
        for s in ST_CARTOON.inputs:
            if s.name == k:
                s.default_value = v
    ST_CARTOON.inputs["Quality"].default_value = 3


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
    """Flat achromatic dielectric. Colour is NOT a variable on this sheet, so
    MN Color is deliberately not connected -- a per-atom palette would move
    with the geometry and there would be no telling which one a panel read."""
    m, nt, out = new_mat(name)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.location = (250, 0)
    b.inputs["Base Color"].default_value = (*rgb, 1.0)
    b.inputs["Roughness"].default_value = roughness
    b.inputs["Specular IOR Level"].default_value = 0.35
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return m


M_MATTE = mat_matte("M_matte")


def use_material(mat):
    for n in style_nodes_of(OB):
        mn.material.set_socket_material(n.inputs["Material"], mat)


use_material(M_MATTE)

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


def set_ground(rgb, roughness=0.62):
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
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 3, 2 * i + 2
        faces.append((a, b, c, d))
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

FRAME_PTS = MOL.position[AG_PROTEIN.indices]
CENTROID = Vector(FRAME_PTS.mean(axis=0).tolist())

CAM_OB.rotation_euler = Euler((math.radians(78.0), 0.0, math.radians(-20.0)), "XYZ")
CAM_OB.location = CENTROID + Vector((0, -40, 8))
# matrix_world is NOT refreshed after writing rotation_euler; without this the
# subject frames silently out of shot.
bpy.context.view_layer.update()
canvas.camera.frame_points(FRAME_PTS, margin=MARGIN)
bpy.context.view_layer.update()

CAM_MATRIX = CAM_OB.matrix_world.copy()
CAM_POS = CAM_MATRIX.translation.copy()
CAM_FWD = (CAM_MATRIX.to_3x3() @ Vector((0, 0, -1))).normalized()
TAN_X = (SENSOR / 2.0) / LENS
TAN_Y = TAN_X * RES[1] / RES[0]
D0 = (CENTROID - CAM_POS).dot(CAM_FWD)
ORTHO_SCALE = 2.0 * D0 * TAN_X
log(f"[camera] d0={D0:.3f} frame_width={2 * D0 * TAN_X * 10:.1f} A")


def restore_camera():
    CAM_OB.matrix_world = CAM_MATRIX.copy()
    CAM_OB.data.type = "PERSP"
    CAM_OB.data.lens = LENS
    CAM_OB.data.ortho_scale = ORTHO_SCALE
    CAM_OB.data.shift_x = 0.0
    CAM_OB.data.shift_y = 0.0
    bpy.context.view_layer.update()


def eval_world_verts():
    """World-space vertices of the whole evaluated geometry set.

    The bounding BOX is the wrong instrument here: its corners sit outside the
    geometry on every diagonal, so an AABB projection reports a clip on a
    subject that is comfortably inside the frame. Project the real vertices.
    """
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
        co = co @ m[:3, :3].T + m[:3, 3]
        out.append(co)
        o.to_mesh_clear()
    return np.concatenate(out, axis=0) if out else np.zeros((0, 3), dtype=np.float32)


def ndc_extent(pts=None):
    """Largest |x|,|y| of the evaluated geometry in camera NDC. >1 clips."""
    if pts is None:
        pts = eval_world_verts()
    if len(pts) == 0:
        return None
    inv = np.array(CAM_MATRIX.inverted()).reshape(4, 4)
    q = pts @ inv[:3, :3].T + inv[:3, 3]
    d = -q[:, 2]
    if float(d.min()) <= 1e-6:
        return 99.0
    mx = float(np.abs(q[:, 0] / (d * TAN_X)).max())
    my = float(np.abs(q[:, 1] / (d * TAN_Y)).max())
    return round(max(mx, my), 4)


# ---- solve the frame on the FATTEST configuration, once -------------------
# frame_points() above framed the ATOM CENTRES. The drawn ribbon extends past
# them, and the fat rungs extend a great deal further, so a camera solved on
# the default geometry sends the top of the ladder out of shot. Solve instead
# on a configuration that is a superset of every panel -- each socket at its
# maximum across the whole sheet -- then freeze that camera for all panels, so
# the camera dimension stays constant and keeps guarding.
#
# The ground is taken from the same pass, or the fat rungs sink through it.

FATTEST = (
    {"Sheet Thickness": 4.5, "Sheet Width": 10.0}
    if N_SHEET >= 40
    else {
        "Helix Thickness": 4.5,
        "Helix Width": BASE["Helix Width"] * 6.0,
        "Sheet Thickness": BASE["Sheet Thickness"] * 6.0,
        "Sheet Width": BASE["Sheet Width"] * 6.0,
        "Loop Radius": 4.0,
    }
)
for _k, _v in FATTEST.items():
    for _s in ST_CARTOON.inputs:
        if _s.name == _k:
            _s.default_value = _v
bpy.context.view_layer.update()

FAT_PTS = eval_world_verts()
assert len(FAT_PTS) > 0, "fattest configuration evaluated to no geometry"
log(f"[frame] solving on fattest config: {FATTEST}")
log(
    f"[frame] {len(FAT_PTS):,} evaluated vertices, "
    f"extent {np.ptp(FAT_PTS, axis=0).round(2).tolist()} BU"
)

canvas.camera.frame_points(FAT_PTS, margin=MARGIN)
bpy.context.view_layer.update()

CAM_MATRIX = CAM_OB.matrix_world.copy()
CAM_POS = CAM_MATRIX.translation.copy()
CAM_FWD = (CAM_MATRIX.to_3x3() @ Vector((0, 0, -1))).normalized()
D0 = (CENTROID - CAM_POS).dot(CAM_FWD)
ORTHO_SCALE = 2.0 * D0 * TAN_X
log(
    f"[camera] reframed: d0={D0:.3f} frame_width={2 * D0 * TAN_X * 10:.1f} A "
    f"fattest_ndc={ndc_extent(FAT_PTS)}"
)

Z_GEOM = float(FAT_PTS[:, 2].min())
assert math.isfinite(Z_GEOM) and abs(Z_GEOM) < 1e4
Z_GROUND = Z_GEOM - 0.02
log(f"[studio] fattest z_min={Z_GEOM:.4f} ground={Z_GROUND:.4f}")

reset_cartoon()
bpy.context.view_layer.update()

Y_CURVE = CENTROID.y + 62.0
CYC = build_cyclorama(Z_GROUND, Y_CURVE)
CYC.data.materials.append(GROUND_MAT)

KEY_LOC = CENTROID + Vector((-26.0, -22.0, 26.0))


def rig_studio_key():
    clear_lights()
    add_area("Key", KEY_LOC, CENTROID, size=34.0, energy=48000.0, rgb=(1.0, 0.98, 0.95))


# ================================================================ instrument


def ev_stats():
    """Whole evaluated geometry set. AREA is the instrument -- vertex count is
    bit-identical across the entire thickness range, so a vertex-count guard
    would pass while reporting nothing."""
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
    return (
        _mrx(CAM_OB.matrix_world),
        round(CAM_OB.data.lens, 4),
        CAM_OB.data.type,
        round(CAM_OB.data.shift_x, 6),
        round(CAM_OB.data.shift_y, 6),
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
    for o in sorted(
        [o for o in bpy.data.objects if o.type == "LIGHT"], key=lambda x: x.name
    ):
        ld = o.data
        out.append(
            (
                o.name,
                ld.type,
                round(ld.energy, 4),
                tuple(round(c, 4) for c in ld.color),
                round(getattr(ld, "size", 0.0), 4),
                _mrx(o.matrix_world),
            )
        )
    return tuple(out)


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
    return (
        round(CYC.location.z, 5),
        _mrx(CYC.matrix_world),
        _tree_hash(GROUND_MAT.node_tree),
    )


def sig_material():
    out = []
    for n in style_nodes_of(OB):
        s = n.inputs.get("Material")
        mat = getattr(s, "default_value", None) if s else None
        out.append(
            (
                n.name,
                mat.name if mat else None,
                _tree_hash(mat.node_tree) if mat else None,
            )
        )
    return tuple(out)


def sig_geometry():
    out = []
    for n in style_nodes_of(OB):
        vals = []
        for s in n.inputs:
            if s.name in ("Atoms", "Material"):
                continue
            try:
                dv = s.default_value
                # A MENU socket's default_value is a str, and str has
                # __len__ — so the vector branch used to run first, float("C")
                # raised, the bare except swallowed it, and EVERY menu socket
                # hashed to the same None. Test str before __len__.
                dv = (
                    dv
                    if isinstance(dv, str)
                    else tuple(round(float(x), 4) for x in dv)
                    if hasattr(dv, "__len__")
                    else round(float(dv), 4)
                    if isinstance(dv, (int, float))
                    else str(dv)
                )
            except Exception:
                dv = None
            vals.append((s.name, repr(dv)))
        out.append((n.name, tuple(vals)))
    return (_mrx(OB.matrix_world), tuple(out))


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
    use_material(M_MATTE)
    reset_cartoon()


def render(pid, name, varies, build, note):
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
        "varies": sorted(varies),
        "status": "ok",
        "violations": [],
        "settings": [],
    }
    try:
        carrier_studio()
        rec["settings"] = build() or []
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
                if dim in varies:
                    continue
                if val != CARRIER[dim]:
                    msg = f"UNDECLARED DRIFT in {dim!r}"
                    rec["violations"].append(msg)
                    log(f"[discipline] !! {msg}")
                    log(f"    carrier: {str(CARRIER[dim])[:200]}")
                    log(f"    panel  : {str(val)[:200]}")

        stats = ev_stats()
        rec["stats"] = stats
        rec["ndc_extent"] = ndc_extent()
        log(f"[stats] {stats} ndc_extent={rec['ndc_extent']}")
        # A panel with no surface renders an empty frame and reports success.
        assert stats["area"] > 0.0, f"{pid} produced ZERO surface area"
        if rec["ndc_extent"] and rec["ndc_extent"] > 1.0:
            rec["violations"].append(f"CLIPS FRAME (ndc {rec['ndc_extent']:.3f} > 1.0)")
            log(f"[discipline] !! clips frame at ndc {rec['ndc_extent']:.3f}")

        canvas.snapshot(path)
        rec["seconds"] = round(time.time() - t0, 1)
        rec["bytes"] = os.path.getsize(path)
        log(f"[render] {fname} {rec['seconds']}s {rec['bytes']}B")
    except Exception as e:
        rec["status"] = "FAILED"
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["seconds"] = round(time.time() - t0, 1)
        log(f"[render] {pid} FAILED: {rec['error']}")
        log(traceback.format_exc())
    RESULTS.append(rec)


G = {"geometry"}

# ================================================================ panels

T0 = BASE["Helix Thickness"]  # 0.6
W0 = BASE["Helix Width"]  # 2.2
L0 = BASE["Loop Radius"]  # 0.4
ST0 = BASE["Sheet Thickness"]  # 0.5
SW0 = BASE["Sheet Width"]  # 2.5


def p_control():
    return [{"note": "shipped defaults, quality 3"}]


def p_uniform(k):
    """Thickness AND width scaled together. Aspect ratio is INVARIANT at every
    rung -- 0.6:2.2 is 1:3.67 and so is 2.4:8.8. This ladder changes size, not
    section shape. It is here as the size axis, and as the control the aspect
    ladder is read against."""
    return [
        setp(ST_CARTOON, "Helix Thickness", T0 * k),
        setp(ST_CARTOON, "Helix Width", W0 * k),
        setp(ST_CARTOON, "Sheet Thickness", ST0 * k),
        setp(ST_CARTOON, "Sheet Width", SW0 * k),
        setp(ST_CARTOON, "Loop Radius", L0 * k),
    ]


def p_aspect(t):
    """Thickness rises, width PINNED at its default. This is the ladder that
    turns a ribbon into a bar: at thickness 2.2 the helix section is square,
    and past that it is taller than it is wide."""
    return [
        setp(ST_CARTOON, "Helix Thickness", t),
        setp(ST_CARTOON, "Helix Width", W0),
        setp(ST_CARTOON, "Loop Radius", L0),
    ]


def p_loop(r):
    return [setp(ST_CARTOON, "Loop Radius", r)]


def p_menu(sock, val):
    return [setp(ST_CARTOON, sock, val)]


def p_sheet_thick(t):
    return [setp(ST_CARTOON, "Sheet Thickness", t)]


def p_sheet_width(w):
    return [setp(ST_CARTOON, "Sheet Width", w)]


PANELS_HELIX = [
    (
        "B01",
        "control",
        G,
        p_control,
        "shipped defaults: helix thickness 0.6, width 2.2, loop radius 0.4",
    ),
    ("B02", "uniform-k1.5", G, lambda: p_uniform(1.5), "every dimension x1.5"),
    ("B03", "uniform-k2.5", G, lambda: p_uniform(2.5), "every dimension x2.5"),
    ("B04", "uniform-k4", G, lambda: p_uniform(4.0), "every dimension x4"),
    (
        "B05",
        "uniform-k6",
        G,
        lambda: p_uniform(6.0),
        "every dimension x6; section aspect still 1:3.67",
    ),
    (
        "B06",
        "aspect-t1.4",
        G,
        lambda: p_aspect(1.4),
        "thickness 1.4, width pinned 2.2 -- section 1:1.57",
    ),
    (
        "B07",
        "aspect-t2.2",
        G,
        lambda: p_aspect(2.2),
        "thickness 2.2, width pinned 2.2 -- section SQUARE",
    ),
    (
        "B08",
        "aspect-t3.2",
        G,
        lambda: p_aspect(3.2),
        "thickness 3.2, width pinned 2.2 -- section inverted, 1.45:1",
    ),
    (
        "B09",
        "aspect-t4.5",
        G,
        lambda: p_aspect(4.5),
        "thickness 4.5, width pinned 2.2 -- section 2.05:1, a standing blade",
    ),
    ("B10", "loop-1.2", G, lambda: p_loop(1.2), "loop radius 1.2, helix untouched"),
    ("B11", "loop-2.4", G, lambda: p_loop(2.4), "loop radius 2.4"),
    (
        "B12",
        "loop-4.0",
        G,
        lambda: p_loop(4.0),
        "loop radius 4.0 -- past the declared maximum of 3.0; the stored value is "
        "recorded in the manifest",
    ),
    (
        "B13",
        "helix-cylinder",
        G,
        lambda: p_menu("Helix Shape", "Cylinder"),
        "helices drawn as cylinders instead of spiral ribbons",
    ),
    (
        "B14",
        "peptide-round",
        G,
        lambda: p_menu("Peptide Shape", "Round"),
        "rounded section instead of sharp-edged",
    ),
    (
        "B15",
        "round-and-chonky",
        G,
        lambda: p_menu("Peptide Shape", "Round") + p_aspect(2.2),
        "square section AND rounded edges -- the two mechanisms together",
    ),
]

PANELS_SHEET = [
    ("B20", "control", G, p_control, "shipped defaults: sheet thickness 0.5, width 2.5"),
    ("B21", "sheet-t1.2", G, lambda: p_sheet_thick(1.2), "sheet thickness 1.2"),
    ("B22", "sheet-t2.0", G, lambda: p_sheet_thick(2.0), "sheet thickness 2.0"),
    ("B23", "sheet-t3.0", G, lambda: p_sheet_thick(3.0), "sheet thickness 3.0"),
    (
        "B24",
        "sheet-t4.5",
        G,
        lambda: p_sheet_thick(4.5),
        "sheet thickness 4.5 -- 9x the default, section 4.5:2.5",
    ),
    ("B25", "sheet-w4.0", G, lambda: p_sheet_width(4.0), "sheet width 4.0"),
    ("B26", "sheet-w6.5", G, lambda: p_sheet_width(6.5), "sheet width 6.5"),
    (
        "B27",
        "sheet-w10",
        G,
        lambda: p_sheet_width(10.0),
        "sheet width 10.0 -- strands wide enough to meet their neighbours",
    ),
    (
        "B28",
        "arrows-flat",
        G,
        lambda: p_menu("Sheet Arrows", "Flat"),
        "strand ends squared off instead of arrowed",
    ),
    (
        "B29",
        "peptide-round",
        G,
        lambda: p_menu("Peptide Shape", "Round"),
        "rounded section instead of sharp-edged",
    ),
    (
        "B30",
        "sheet-square",
        G,
        lambda: p_sheet_thick(2.5),
        "sheet thickness 2.5 against width 2.5 -- section SQUARE",
    ),
]

# 4HHB has zero sheet residues: the sheet knobs cannot show there and the
# panels would be identical pictures reporting success. Pick by measurement.
if N_SHEET >= 40:
    PANELS = PANELS_SHEET
    log(f"[plan] {SUBJECT} has {N_SHEET} sheet CAs -> rendering the SHEET set")
else:
    PANELS = PANELS_HELIX
    log(
        f"[plan] {SUBJECT} has {N_SHEET} sheet CAs, {N_HELIX} helix "
        f"-> rendering the HELIX set"
    )

log(f"[plan] {len(PANELS)} panels -> {OUT}")

if ONLY:
    _ids = {p[0] for p in PANELS}
    _unknown = ONLY - _ids
    assert not _unknown, (
        f"--only names panels that do not exist: {sorted(_unknown)}; "
        f"this sheet has {sorted(_ids)}"
    )

for pid, name, varies, build, note in PANELS:
    render(pid, name, varies, build, note)

# ================================================================ manifest

ok = [r for r in RESULTS if r["status"] == "ok"]
bad = [r for r in RESULTS if r["status"] != "ok"]
viol = [r for r in RESULTS if r["violations"]]

log("\n" + "=" * 62)
log(
    f"[done] {len(ok)}/{len(RESULTS)} rendered, {len(bad)} failed, "
    f"{len(viol)} with violations, {len(CLAMPS)} clamped settings"
)
if ok:
    areas = [(r["id"], r["stats"]["area"]) for r in ok if "stats" in r]
    log("[areas] " + "  ".join(f"{i}:{a:.1f}" for i, a in areas))
for r in bad:
    log(f"[failed] {r['id']}: {r.get('error')}")
for r in viol:
    log(f"[violation] {r['id']}: {r['violations']}")
for c in CLAMPS:
    log(f"[clamped] {c}")

man = {
    "subject": SUBJECT,
    "samples": SAMPLES,
    "resolution": list(RES),
    "sec_struct_by_ca": SS_COUNTS,
    "cartoon_defaults": {k: v for k, v in BASE.items() if v is not None},
    "cartoon_menus": BASE_MENU,
    "clamped": CLAMPS,
    "panels": RESULTS,
}
with open(os.path.join(OUT, "manifest.json"), "w") as f:
    json.dump(man, f, indent=2, default=str)
with open(os.path.join(OUT, "log.txt"), "w") as f:
    f.write("\n".join(LOG))
log(f"[done] manifest -> {os.path.join(OUT, 'manifest.json')}")
