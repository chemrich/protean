#!/usr/bin/env python3
"""
surface_render.py -- SHEET A: six kinds of surface, not one surface six ways.

Step 2 of the two-direction plan. Molecular Nodes does not compute one surface
and shade it. It builds a signed distance field on a voxel grid and carves a
surface out of it, so offset / fillet / mean-filter / scale are different
SURFACES rather than different amounts of one. That indirection is the whole
opportunity here, and it is not reachable in the viewer.

Same discipline as chonk_render.py: one subject per process, one frozen camera
solved on the largest configuration, flat achromatic material on every panel so
geometry is the only variable, and every undeclared dimension asserted constant.

    blender --background --python blender/surface_render.py -- --subject 1HSG
    blender --background --python blender/surface_render.py -- --subject 4HHB

NEVER pass --factory-startup: it disables the MolecularNodes extension.

Two measured landmines this sheet walks straight past
-----------------------------------------------------
Both were found by evaluating geometry before any of this was written, and both
render a plausible empty frame while reporting success:

  Offset <= -0.3          the surface is GONE (area 0.000)
  Mean Width 6 / Iter 10  zero geometry

So the eroded rung stops at -0.20 and the melt stops at width 3 / 5 iterations,
and every panel asserts non-zero area regardless.

A third, subtler one: three independent reviewers each called negative offset
impossible, having read the declared socket range [0, inf) out of the add-on's
asset file. The declared range is real but it governs the interface slider, not
assignment: -1.0 assigned reads back as -1.0. Erosion works. The guard that
matters is the area assertion, not a range check.
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
SUBJECT = "1HSG"
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
        os.path.dirname(os.path.abspath(__file__)), "research", "renders", "surface"
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

cam_data = bpy.data.cameras.new("SurfCam")
cam_data.lens = LENS
cam_data.sensor_width = SENSOR
cam_data.clip_start = 0.1
cam_data.clip_end = 5000.0
CAM_OB = bpy.data.objects.new("SurfCam", cam_data)
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
log(f"[subject] {SUBJECT} atoms={N_ATOMS}")

AG_PROTEIN = MOL.universe.select_atoms("protein")
assert AG_PROTEIN.n_atoms > 0, "selection 'protein' matched zero atoms"
log(f"[selection] 'protein' -> {AG_PROTEIN.n_atoms} atoms")

N_CHAINS = len(np.unique(MOL.named_attribute("chain_id")))
log(f"[preflight] {SUBJECT} chains={N_CHAINS}")


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


ST_SURF = add_style_tracked(MOL, "surface", selection="protein")
log(f"[styles] {[n.name for n in style_nodes_of(OB)]}")

BASE = {
    s.name: s.default_value
    for s in ST_SURF.inputs
    if s.type in ("VALUE", "INT", "BOOLEAN")
}
BASE_MENU = {s.name: s.default_value for s in ST_SURF.inputs if s.type == "MENU"}
log(f"[styles] surface defaults: {BASE}")
log(f"[styles] surface menus:    {BASE_MENU}")

CLAMPS = []


def setp(node, name, value):
    """Set a socket and RECORD what landed. Never asserts equality on a float."""
    sock = None
    for s in node.inputs:
        if s.name == name:
            sock = s
            break
    assert sock is not None, f"no socket {name!r}; have {[s.name for s in node.inputs]}"
    sock.default_value = value
    after = sock.default_value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        clamped = abs(float(after) - float(value)) > 1e-4
    else:
        clamped = str(after) != str(value)
    if clamped:
        CLAMPS.append({"socket": name, "requested": value, "actual": after})
        log(f"[setp] !! {name}: requested {value!r} -> stored {after!r} (CLAMPED)")
    return {"socket": name, "requested": value, "actual": after, "clamped": clamped}


def reset_surface():
    for k, v in BASE.items():
        for s in ST_SURF.inputs:
            if s.name == k:
                s.default_value = v
    for k, v in BASE_MENU.items():
        for s in ST_SURF.inputs:
            if s.name == k:
                s.default_value = v


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


def restore_camera():
    CAM_OB.matrix_world = CAM_MATRIX.copy()
    CAM_OB.data.type = "PERSP"
    CAM_OB.data.lens = LENS
    CAM_OB.data.ortho_scale = ORTHO_SCALE
    CAM_OB.data.shift_x = 0.0
    CAM_OB.data.shift_y = 0.0
    bpy.context.view_layer.update()


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
        co = co @ m[:3, :3].T + m[:3, 3]
        out.append(co)
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
    mx = float(np.abs(q[:, 0] / (d * TAN_X)).max())
    my = float(np.abs(q[:, 1] / (d * TAN_Y)).max())
    return round(max(mx, my), 4)


# Solve the frame on the LARGEST configuration on the sheet -- dilation by
# offset 2.0 on top of scale 2.5 -- then freeze it for every panel.
for _k, _v in (("Offset", 2.0), ("Scale", 2.5)):
    setp(ST_SURF, _k, _v)
bpy.context.view_layer.update()
FAT_PTS = eval_world_verts()
assert len(FAT_PTS) > 0, "largest configuration evaluated to no geometry"
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
    f"[camera] reframed: frame_width={2 * D0 * TAN_X * 10:.1f} A "
    f"largest_ndc={ndc_extent(FAT_PTS)}"
)

Z_GEOM = float(FAT_PTS[:, 2].min())
Z_GROUND = Z_GEOM - 0.02
log(f"[studio] largest z_min={Z_GEOM:.4f} ground={Z_GROUND:.4f}")
reset_surface()
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
    reset_surface()


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

        stats = ev_stats()
        rec["stats"] = stats
        rec["ndc_extent"] = ndc_extent()
        log(f"[stats] {stats} ndc_extent={rec['ndc_extent']}")
        # Measured: offset <= -0.3 and mean w6/i10 both return NOTHING.
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


def s_control():
    return [{"note": "shipped defaults: quality 3, scale 1.25, offset 0.15, relax 5"}]


def s_set(**kw):
    return [
        setp(ST_SURF, k.replace("_", " ").title().replace("Id", "ID"), v)
        for k, v in kw.items()
    ]


PANELS = [
    (
        "A01",
        "control",
        G,
        s_control,
        "the surface the add-on ships: scale 1.25, offset 0.15, relax 5, quality 3",
    ),
    # -- dilate: push the level set outward -------------------------------
    (
        "A02",
        "dilate-0.5",
        G,
        lambda: [setp(ST_SURF, "Offset", 0.5)],
        "offset 0.5 -- clefts begin to fill",
    ),
    (
        "A03",
        "dilate-1.0",
        G,
        lambda: [setp(ST_SURF, "Offset", 1.0)],
        "offset 1.0 -- measured 67k verts against the control's 39k",
    ),
    (
        "A04",
        "dilate-2.0",
        G,
        lambda: [setp(ST_SURF, "Offset", 2.0)],
        "offset 2.0 -- a single smooth body; evaluation cost rises steeply here",
    ),
    # -- erode: pull it inward, to the edge of collapse --------------------
    (
        "A05",
        "erode-0.10",
        G,
        lambda: [setp(ST_SURF, "Offset", -0.10)],
        "offset -0.10 -- measured MORE area than offset 0, the surface breaking "
        "toward separate lobes",
    ),
    (
        "A06",
        "erode-0.20",
        G,
        lambda: [setp(ST_SURF, "Offset", -0.20)],
        "offset -0.20 -- the last rung that survives; -0.3 returns nothing",
    ),
    # -- melt: blur the distance field before carving ----------------------
    (
        "A07",
        "melt-w3i1",
        G,
        lambda: s_set(mean_width=3, mean_iterations=1),
        "mean filter width 3, one pass",
    ),
    (
        "A08",
        "melt-w3i5",
        G,
        lambda: s_set(mean_width=3, mean_iterations=5),
        "mean filter width 3, five passes -- measured volume 119 -> 86, detail gone",
    ),
    # -- fillet: round only the concave creases ----------------------------
    (
        "A09",
        "fillet-8",
        G,
        lambda: [setp(ST_SURF, "Fillet", 8)],
        "fillet 8 at the default relax",
    ),
    (
        "A10",
        "fillet-20",
        G,
        lambda: [setp(ST_SURF, "Fillet", 20)],
        "fillet 20 -- 18% fewer vertices while volume holds; it fills grooves "
        "without inflating",
    ),
    (
        "A11",
        "fillet-20-relax0",
        G,
        lambda: s_set(fillet=20, relax=0),
        "fillet 20 with relax OFF -- relax is a curvature-weighted smoothing that "
        "eats the same concave detail fillet exists to fill, so the two confound",
    ),
    # -- inflate: grow the atoms instead of moving the surface --------------
    (
        "A12",
        "inflate-1.8",
        G,
        lambda: [setp(ST_SURF, "Scale", 1.8)],
        "vdW radii x1.8 -- a different mechanism from offset",
    ),
    (
        "A13",
        "inflate-2.5",
        G,
        lambda: [setp(ST_SURF, "Scale", 2.5)],
        "vdW radii x2.5 -- measured volume 109 -> 151",
    ),
    (
        "A14",
        "shrink-0.8",
        G,
        lambda: [setp(ST_SURF, "Scale", 0.8)],
        "vdW radii x0.8 -- measured MORE vertices than the control, atoms "
        "separating into distinguishable spheres",
    ),
    # -- resolution --------------------------------------------------------
    (
        "A15",
        "quality-1",
        G,
        lambda: [setp(ST_SURF, "Quality", 1)],
        "quality 1 -- 3,084 verts, the voxel grid visible as faceting",
    ),
    (
        "A16",
        "quality-5",
        G,
        lambda: [setp(ST_SURF, "Quality", 5)],
        "quality 5 -- 116,514 verts, 30x the evaluation cost of quality 1",
    ),
    # -- relax -------------------------------------------------------------
    (
        "A17",
        "relax-0",
        G,
        lambda: [setp(ST_SURF, "Relax", 0)],
        "relax 0 -- the raw marching-cubes surface, unsmoothed",
    ),
    (
        "A18",
        "relax-60",
        G,
        lambda: [setp(ST_SURF, "Relax", 60)],
        "relax 60 -- measured volume 120 -> 107; relax SHRINKS as well as smooths",
    ),
]

# One fused envelope against the default, which is ALREADY per-chain.
#
# This started as a pair, A19 "per-chain" and A20 "fused". A19 set Separate By
# to "chain_id" -- its own shipped default -- so it was a no-op and rendered
# bit-identically to A01: same 39,046 verts, same area 103.1. It reported
# success and told us nothing. The default IS the per-chain surface, so the
# only panel worth rendering here is the fused one, and its control is A01.
# The duplicate-geometry guard in render() now catches this class outright.
if N_CHAINS > 1:
    PANELS += [
        (
            "A19",
            "fused-envelope",
            G,
            lambda: s_set(separate_by="Group ID"),
            f"one fused envelope over all {N_CHAINS} chains, against A01's "
            f"per-chain default -- separate surfaces can take separate "
            f"materials or be pulled apart, a fused one cannot",
        ),
    ]
else:
    log(
        f"[plan] {SUBJECT} has {N_CHAINS} chain -- skipping the per-chain pair, "
        f"which would render two identical pictures"
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
    f"{len(viol)} with violations, {len(CLAMPS)} clamped"
)
if ok:
    log(
        "[areas] "
        + "  ".join(f"{r['id']}:{r['stats']['area']:.1f}" for r in ok if "stats" in r)
    )
    log(
        "[verts] "
        + "  ".join(f"{r['id']}:{r['stats']['verts']}" for r in ok if "stats" in r)
    )
for r in bad:
    log(f"[failed] {r['id']}: {r.get('error')}")
for r in viol:
    log(f"[violation] {r['id']}: {r['violations']}")

man = {
    "subject": SUBJECT,
    "samples": SAMPLES,
    "resolution": list(RES),
    "chains": N_CHAINS,
    "surface_defaults": BASE,
    "surface_menus": BASE_MENU,
    "clamped": CLAMPS,
    "panels": RESULTS,
}
with open(os.path.join(OUT, "manifest.json"), "w") as f:
    json.dump(man, f, indent=2, default=str)
with open(os.path.join(OUT, "log.txt"), "w") as f:
    f.write("\n".join(LOG))
log(f"[done] manifest -> {os.path.join(OUT, 'manifest.json')}")
