#!/usr/bin/env python3
"""
stylespace_render.py -- a STYLE-SPACE MAP for protean.

Diagrammatic panels, each isolating ONE principle. Not hero renders.

Run:
    blender --background --python stylespace_render.py -- [--only A1,B3] [--samples 64]

NEVER pass --factory-startup: it disables the MolecularNodes extension.

Discipline
----------
One structure, one camera, one framing, one render setting across the whole
set. Each panel DECLARES which dimensions it varies; after the panel is built
the script snapshots every dimension and asserts that the undeclared ones are
bit-identical to the carrier. That is "change one thing per green" applied to
pictures, enforced by the machine rather than by intention.
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
from mathutils import Euler, Matrix, Vector

# ---------------------------------------------------------------- args

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
ONLY = None
SAMPLES = 64
for i, a in enumerate(argv):
    if a == "--only":
        ONLY = set(argv[i + 1].split(","))
    if a == "--samples":
        SAMPLES = int(argv[i + 1])

OUT = "/private/tmp/claude-501/-Users-charlie-code-protean/b549de8d-56f4-456e-9de7-823d312c525f/scratchpad/stylespace"
os.makedirs(OUT, exist_ok=True)

CACHE = os.path.expanduser("~/MolecularNodesCache")
RES = (1000, 750)
LENS = 50.0  # mm, constant for every panel
SENSOR = 36.0  # mm
MARGIN = 0.15  # canonical framing margin
# At this world scale (1 BU = 10 A) a physically ordinary f-number gives almost
# no blur: at f/1.4 the far copies defocus by under 2 px. Measured, not guessed.
FSTOP = 0.14

# ---------------------------------------------------------------- addon

bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.molecularnodes")
import bl_ext.blender_org.molecularnodes as mn  # noqa: E402

# Seed the MN shader node groups ("MN Color", "Color AO") so custom materials
# can reuse them. MN Color is required, not optional: it resolves colour for
# INSTANCED geometry (ball_and_stick), which a bare Attribute node cannot.
mn.material.Default(name="_seed_groups")
NG_MN_COLOR = bpy.data.node_groups["MN Color"]
NG_COLOR_AO = bpy.data.node_groups["Color AO"]

LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


# ================================================================ scene setup


def purge_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)


purge_scene()
scene = bpy.context.scene

# Camera must exist before mn.Canvas() or it raises.
cam_data = bpy.data.cameras.new("StyleCam")
cam_data.lens = LENS
cam_data.sensor_width = SENSOR
cam_data.clip_start = 0.1
cam_data.clip_end = 5000.0
CAM_OB = bpy.data.objects.new("StyleCam", cam_data)
scene.collection.objects.link(CAM_OB)
scene.camera = CAM_OB

# template=None: do NOT load the MN template. load_preset()/the default template
# swaps the scene out from under us (stale StructRNA) and injects a camera plus
# three sun lamps, which would silently contaminate every lighting comparison.
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

# Metal GPU if the build exposes it; fall back silently to CPU.
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

VIEW_TRANSFORM = scene.view_settings.view_transform
log(
    f"[setup] view_transform={VIEW_TRANSFORM} look={scene.view_settings.look} "
    f"exposure={scene.view_settings.exposure}"
)

# ================================================================ subject

HERO = mn.Molecule.load(os.path.join(CACHE, "4HHB.bcif"), name="hero")
HERO_OB = HERO.object
HERO_M0 = HERO_OB.matrix_world.copy()
N_ATOMS = HERO.universe.atoms.n_atoms
assert len(HERO.position) == N_ATOMS, "vertex/atom mapping is not 1:1"
log(f"[subject] 4HHB atoms={N_ATOMS}")

# MDAnalysis selection syntax -- verify every selection resolves to real atoms
# before it is used. A wrong token is swallowed as a UserWarning and you render
# a beautiful picture of nothing highlighted.
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

# --------------------------------------------------------------- styles


def style_nodes_of(ob):
    ng = ob.modifiers[0].node_group
    return [n for n in ng.nodes if n.name.startswith("Style")]


def add_style_tracked(mol, *args, **kwargs):
    """add_style, returning the Style node it created."""
    ng = mol.object.modifiers[0].node_group
    before = set(ng.nodes.keys())
    mol.add_style(*args, **kwargs)
    new = [n for k, n in ng.nodes.items() if k not in before and k.startswith("Style")]
    assert len(new) == 1, f"expected 1 new style node, got {[n.name for n in new]}"
    return new[0]


# Base geometry, constant for every 4HHB panel: cartoon protein + ball-and-stick
# haems. The haems exist in every panel so the accent target of group C is
# physically present even in the panels that do not accent it.
ST_CARTOON = add_style_tracked(HERO, "cartoon", selection="protein")
ST_HEM = add_style_tracked(HERO, "ball_and_stick", selection="resname HEM")
log(f"[styles] {[n.name for n in style_nodes_of(HERO_OB)]}")

for n in style_nodes_of(HERO_OB):
    if "Quality" in n.inputs:
        n.inputs["Quality"].default_value = 3

# =============================================================== colours


def write_colours(mapping, default=(0.5, 0.5, 0.5)):
    """mapping: {selection-key: (r,g,b)} written into the Color attribute."""
    col = np.zeros((N_ATOMS, 4), dtype=np.float32)
    col[:, :3] = default
    col[:, 3] = 1.0
    for key, rgb in mapping.items():
        col[IDX[key], :3] = rgb
    HERO.store_named_attribute(col, "Color", "FLOAT_COLOR")
    return col


# Carrier palette: restrained, used by every group A and B panel.
PAL_CARRIER = {
    "A": (0.42, 0.50, 0.60),
    "C": (0.36, 0.45, 0.55),
    "B": (0.55, 0.47, 0.42),
    "D": (0.50, 0.43, 0.39),
    "HEM": (0.62, 0.34, 0.22),
    "FE": (0.78, 0.42, 0.20),
}
PAL_SATURATED = {
    "A": (0.06, 0.30, 0.80),
    "C": (0.05, 0.52, 0.46),
    "B": (0.88, 0.28, 0.06),
    "D": (0.68, 0.10, 0.44),
    "HEM": (0.97, 0.76, 0.06),
    "FE": (1.0, 0.85, 0.10),
}
PAL_NEUTRAL = {
    "A": (0.56, 0.56, 0.56),
    "C": (0.50, 0.50, 0.50),
    "B": (0.60, 0.60, 0.60),
    "D": (0.53, 0.53, 0.53),
    "HEM": (0.46, 0.46, 0.46),
    "FE": (0.50, 0.50, 0.50),
}
# Two near-neighbour desaturated hues; haems the only saturated warm thing.
PAL_ACCENT_SITE = {
    "A": (0.43, 0.47, 0.53),
    "C": (0.43, 0.47, 0.53),
    "B": (0.45, 0.51, 0.49),
    "D": (0.45, 0.51, 0.49),
    "HEM": (0.95, 0.40, 0.06),
    "FE": (1.0, 0.55, 0.10),
}
# The inversion: accent spent on the largest object, rarest object left neutral.
PAL_ACCENT_INVERTED = {
    "A": (0.06, 0.38, 0.92),
    "C": (0.06, 0.38, 0.92),
    "B": (0.06, 0.74, 0.42),
    "D": (0.06, 0.74, 0.42),
    "HEM": (0.52, 0.52, 0.52),
    "FE": (0.56, 0.56, 0.56),
}
PAL_PAPER = dict.fromkeys(PAL_CARRIER, (0.9, 0.9, 0.9))
PAL_FAMILY = {
    "A": (0.60, 0.64, 0.69),
    "C": (0.60, 0.64, 0.69),
    "B": (0.60, 0.64, 0.69),
    "D": (0.60, 0.64, 0.69),
    "HEM": (0.46, 0.51, 0.57),
    "FE": (0.46, 0.51, 0.57),
}

# =============================================================== materials


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


def mn_colour(nt, x=-900, y=0):
    n = nt.nodes.new("ShaderNodeGroup")
    n.node_tree = NG_MN_COLOR
    n.location = (x, y)
    return n


def facing_mask(nt, threshold=0.55, soft=0.18, x=-500, y=-450):
    """1.0 inside the silhouette band, 0.0 head-on. |dot(N, I)|."""
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (x - 400, y)
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.location = (x - 200, y)
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    nt.links.new(geo.outputs["Incoming"], dot.inputs[1])
    ab = nt.nodes.new("ShaderNodeMath")
    ab.operation = "ABSOLUTE"
    ab.location = (x - 40, y)
    nt.links.new(dot.outputs["Value"], ab.inputs[0])
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.location = (x + 120, y)
    mr.inputs["From Min"].default_value = max(0.0, threshold - soft)
    mr.inputs["From Max"].default_value = threshold
    mr.inputs["To Min"].default_value = 1.0
    mr.inputs["To Max"].default_value = 0.0
    mr.clamp = True
    nt.links.new(ab.outputs["Value"], mr.inputs["Value"])
    return mr.outputs["Result"]


def mat_photoreal(
    name,
    roughness=0.40,
    coat=0.03,
    mix_to=None,
    mix_fac=0.0,
    override_rgb=None,
    metallic=0.0,
):
    """Restrained matte dielectric, colour from the molecule's Color attribute.

    mix_to/mix_fac push the albedo toward a target colour -- the per-copy knob
    that makes the receding-instance panels a true one-variable pair.
    """
    m, nt, out = new_mat(name)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (250, 0)
    if override_rgb is not None:
        src = nt.nodes.new("ShaderNodeRGB")
        src.location = (-600, 0)
        src.outputs[0].default_value = (*override_rgb, 1.0)
        colour_socket = src.outputs[0]
        alpha_socket = None
    else:
        c = mn_colour(nt)
        colour_socket, alpha_socket = c.outputs["Color"], c.outputs["Alpha"]
    if mix_to is not None and mix_fac > 0.0:
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MIX"
        mix.location = (-350, 0)
        mix.inputs["Factor"].default_value = mix_fac
        nt.links.new(colour_socket, mix.inputs[6])
        mix.inputs[7].default_value = (*mix_to, 1.0)
        colour_socket = mix.outputs[2]
    nt.links.new(colour_socket, bsdf.inputs["Base Color"])
    if alpha_socket is not None:
        nt.links.new(alpha_socket, bsdf.inputs["Alpha"])
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Transmission Weight"].default_value = 0.0
    bsdf.inputs["Coat Weight"].default_value = coat
    bsdf.inputs["Coat Roughness"].default_value = 0.10
    bsdf.inputs["Specular IOR Level"].default_value = 0.45
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_ao_key_outline(
    name, ao_distance=1.0, ao_exponent=2.0, key_weight=0.45, outline_threshold=0.55
):
    """A4: the same Color-AO emission A2 uses, PLUS a weak lit term, PLUS an
    outline. Built as A2 + key + outline so the panel's claim is exactly its
    construction."""
    m, nt, out = new_mat(name)
    c = mn_colour(nt, -900, 100)
    cao = nt.nodes.new("ShaderNodeGroup")
    cao.node_tree = NG_COLOR_AO
    cao.location = (-650, 100)
    nt.links.new(c.outputs["Color"], cao.inputs["Color"])
    cao.inputs["Distance"].default_value = ao_distance
    cao.inputs["Exponent"].default_value = ao_exponent

    emis = nt.nodes.new("ShaderNodeEmission")
    emis.location = (-380, 200)
    nt.links.new(cao.outputs["Result"], emis.inputs["Color"])
    emis.inputs["Strength"].default_value = 1.0

    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.location = (-380, 0)
    nt.links.new(c.outputs["Color"], diff.inputs["Color"])
    diff.inputs["Roughness"].default_value = 0.5

    add = nt.nodes.new("ShaderNodeMixShader")
    add.location = (-120, 100)
    add.inputs["Fac"].default_value = key_weight
    nt.links.new(emis.outputs["Emission"], add.inputs[1])
    nt.links.new(diff.outputs["BSDF"], add.inputs[2])

    ink = nt.nodes.new("ShaderNodeEmission")
    ink.location = (-120, -250)
    ink.inputs["Color"].default_value = (0.02, 0.02, 0.025, 1.0)
    ink.inputs["Strength"].default_value = 1.0
    mask = facing_mask(nt, threshold=outline_threshold)
    mixo = nt.nodes.new("ShaderNodeMixShader")
    mixo.location = (250, 0)
    nt.links.new(mask, mixo.inputs["Fac"])
    nt.links.new(add.outputs["Shader"], mixo.inputs[1])
    nt.links.new(ink.outputs["Emission"], mixo.inputs[2])
    nt.links.new(mixo.outputs["Shader"], out.inputs["Surface"])
    return m


def mat_paper(name):
    """D1: flat diffuse white. No hue anywhere -- the Color attribute is not
    read at all, so form can only be carried by silhouette, edge and shadow."""
    m, nt, out = new_mat(name)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (250, 0)
    bsdf.inputs["Base Color"].default_value = (0.86, 0.86, 0.86, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.88
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Specular IOR Level"].default_value = 0.22
    bsdf.inputs["Sheen Weight"].default_value = 0.20
    bsdf.inputs["Sheen Roughness"].default_value = 0.6
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_bronze(name, ao_distance=0.55):
    """D2: ONE metal material whose patina is driven by the ambient occlusion
    we are already computing. Recesses green-black and dielectric, raised
    edges copper and metallic. only_local so the ground does not paint the
    molecule."""
    m, nt, out = new_mat(name)
    ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
    ao.location = (-900, 0)
    ao.samples = 16
    ao.inside = False
    ao.only_local = True
    ao.inputs["Distance"].default_value = ao_distance

    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (-640, 120)
    ramp.color_ramp.interpolation = "B_SPLINE"
    e0 = ramp.color_ramp.elements[0]
    e0.position = 0.05
    e0.color = (0.020, 0.055, 0.045, 1.0)  # verdigris-black, deep recess
    e1 = ramp.color_ramp.elements[1]
    e1.position = 0.96
    e1.color = (0.560, 0.300, 0.115, 1.0)  # copper, raised edge
    mid = ramp.color_ramp.elements.new(0.62)
    mid.color = (0.115, 0.230, 0.170, 1.0)  # verdigris green
    nt.links.new(ao.outputs["AO"], ramp.inputs["Fac"])

    rough = nt.nodes.new("ShaderNodeMapRange")
    rough.location = (-640, -120)
    rough.inputs["To Min"].default_value = 0.82  # patina is rough
    rough.inputs["To Max"].default_value = 0.26  # bare metal is polished
    nt.links.new(ao.outputs["AO"], rough.inputs["Value"])

    metal = nt.nodes.new("ShaderNodeMapRange")
    metal.location = (-640, -320)
    metal.inputs["From Min"].default_value = 0.45
    metal.inputs["From Max"].default_value = 0.95
    metal.inputs["To Min"].default_value = 0.0  # patina is dielectric
    metal.inputs["To Max"].default_value = 1.0  # exposed bronze is metal
    nt.links.new(ao.outputs["AO"], metal.inputs["Value"])

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (250, 0)
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    nt.links.new(metal.outputs["Result"], bsdf.inputs["Metallic"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def set_style_material(node, mat):
    mn.material.set_socket_material(node.inputs["Material"], mat)


# =============================================================== studio


def build_cyclorama(
    z_ground, y_curve, radius=25.0, height=70.0, half_width=260.0, y_front=-260.0, segs=48
):
    """A real cyclorama: ground sweeping up into a backdrop. Built once and
    held constant; only its albedo changes between panels, so 'graded backdrop'
    means graded by light falloff, not by a painted gradient."""
    prof = [(y_front, 0.0), (y_curve, 0.0)]
    for i in range(1, segs + 1):
        t = (math.pi / 2) * i / segs
        prof.append((y_curve + radius * math.sin(t), radius * (1 - math.cos(t))))
    prof.append((y_curve + radius, radius + height))

    verts, faces = [], []
    for _yi, (y, z) in enumerate(prof):
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


def make_ground_mat():
    m, nt, out = new_mat("M_ground")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (250, 0)
    bsdf.inputs["Roughness"].default_value = 0.62
    bsdf.inputs["Specular IOR Level"].default_value = 0.30
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


GROUND_MAT = make_ground_mat()


def set_ground(rgb, roughness=0.62):
    b = GROUND_MAT.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1.0)
    b.inputs["Roughness"].default_value = roughness


# --------------------------------------------------------------- world


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


def set_world_gradient(low_rgb, high_rgb, strength=1.0):
    """C2: all colour supplied by the world. A vertical grade on the view ray."""
    w = scene.world or bpy.data.worlds.new("W")
    scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (600, 0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (400, 0)
    bg.inputs["Strength"].default_value = strength
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (-400, 0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-200, 0)
    nt.links.new(tc.outputs["Generated"], sep.inputs["Vector"])
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.location = (0, 0)
    mr.inputs["From Min"].default_value = -0.55
    mr.inputs["From Max"].default_value = 0.55
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (180, 0)
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (*low_rgb, 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (*high_rgb, 1.0)
    nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])


# --------------------------------------------------------------- lights


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


def add_sun(name, direction, energy, angle, rgb=(1, 1, 1)):
    ld = bpy.data.lights.new(name, "SUN")
    ld.energy = energy
    ld.angle = angle
    ld.color = rgb
    ob = bpy.data.objects.new(name, ld)
    scene.collection.objects.link(ob)
    ob.location = (0, 0, 0)
    ob.rotation_euler = Vector(direction).to_track_quat("-Z", "Y").to_euler()
    return ob


# =============================================================== camera

FRAME_PTS = HERO.position[IDX["frame"]]
CENTROID = Vector(FRAME_PTS.mean(axis=0).tolist())

# Custom viewpoint: 12 deg down, 20 deg azimuth -- a three-quarter studio angle
# that shows the tetramer's silhouette and leaves room for a contact shadow.
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
CAM_RIGHT = (CAM_MATRIX.to_3x3() @ Vector((1, 0, 0))).normalized()
CAM_UP = (CAM_MATRIX.to_3x3() @ Vector((0, 1, 0))).normalized()
D0 = (CENTROID - CAM_POS).dot(CAM_FWD)
TAN_X = (SENSOR / 2.0) / LENS
TAN_Y = TAN_X * RES[1] / RES[0]
ORTHO_SCALE = 2.0 * D0 * TAN_X
log(
    f"[camera] loc={tuple(round(v, 3) for v in CAM_POS)} "
    f"rot={tuple(round(math.degrees(v), 2) for v in CAM_OB.rotation_euler)} "
    f"d0={D0:.3f} ortho_scale={ORTHO_SCALE:.3f}"
)


def cam_space_point(u, v, depth):
    """Normalised screen coords (u,v in [-1,1]) at a given depth -> world."""
    return (
        CAM_POS
        + CAM_FWD * depth
        + CAM_RIGHT * (u * depth * TAN_X)
        + CAM_UP * (v * depth * TAN_Y)
    )


def restore_camera():
    CAM_OB.matrix_world = CAM_MATRIX.copy()
    CAM_OB.data.type = "PERSP"
    CAM_OB.data.lens = LENS
    CAM_OB.data.ortho_scale = ORTHO_SCALE
    CAM_OB.data.shift_x = 0.0
    CAM_OB.data.shift_y = 0.0
    bpy.context.view_layer.update()


# Ground height from the EVALUATED geometry, not the atom centres, so the
# molecule really touches the ground and casts a real contact shadow.
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


def set_dof(on, fstop=0.25, target=None):
    d = CAM_OB.data.dof
    d.use_dof = on
    d.focus_object = target if target is not None else FOCUS
    d.aperture_fstop = fstop


# =============================================================== rigs

KEY_LOC = CENTROID + Vector((-26.0, -22.0, 26.0))


def rig_studio_key():
    clear_lights()
    add_area("Key", KEY_LOC, CENTROID, size=34.0, energy=48000.0, rgb=(1.0, 0.98, 0.95))


def rig_none():
    clear_lights()


def rig_weak_key():
    clear_lights()
    add_area("Key", KEY_LOC, CENTROID, size=34.0, energy=12000.0, rgb=(1.0, 0.98, 0.95))


def rig_colour():
    # Energies deliberately BELOW the carrier's key: under AgX a bright
    # coloured light desaturates toward white in the highlights, so the colour
    # has to live in the midtones or the panel's whole claim washes out.
    clear_lights()
    add_area(
        "KeyWarm",
        CENTROID + Vector((-26.0, -22.0, 26.0)),
        CENTROID,
        size=34.0,
        energy=26000.0,
        rgb=(1.00, 0.42, 0.10),
    )
    add_area(
        "RimCool",
        CENTROID + Vector((22.0, 26.0, 14.0)),
        CENTROID,
        size=26.0,
        energy=42000.0,
        rgb=(0.08, 0.32, 1.00),
    )


def rig_paper_sun():
    clear_lights()
    add_sun(
        "RakingSun",
        Vector((0.62, 0.72, -0.31)),
        energy=5.0,
        angle=math.radians(4.0),
        rgb=(1.0, 1.0, 1.0),
    )


def rig_raking_key():
    clear_lights()
    add_area(
        "Raking",
        CENTROID + Vector((-34.0, -10.0, 7.0)),
        CENTROID,
        size=10.0,
        energy=44000.0,
        rgb=(1.0, 0.94, 0.86),
    )


# =============================================================== signatures


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
        round(d.ortho_scale, 4),
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


DIMS = {
    "camera": sig_camera,
    "dof": sig_dof,
    "lights": sig_lights,
    "world": sig_world,
    "ground": sig_ground,
    "material": sig_material,
    "color": sig_color,
    "geometry": sig_geometry,
}


def snapshot_state():
    return {k: f() for k, f in DIMS.items()}


# =============================================================== rendering

RESULTS = []
STATES = {}
CARRIER = None


def render(pid, name, varies, build, note):
    """Build a panel, enforce its declared variables, render it."""
    global CARRIER
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
        build()
        bpy.context.view_layer.update()
        st = snapshot_state()
        STATES[pid] = st
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
                    log(f"    carrier: {str(CARRIER[dim])[:220]}")
                    log(f"    panel  : {str(val)[:220]}")
        for dim in sorted(varies):
            log(f"[discipline] declared variable: {dim}")
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


# --------------------------------------------------------------- materials

M_PHOTOREAL = mat_photoreal("M_photoreal")
M_AO = mn.material.AmbientOcclusion(distance=1.0, exponent=2.0, name="M_ao")
M_FLAT = mn.material.Flat(outline=True, threshold=0.8, name="M_flat")
M_HALFLIT = mat_ao_key_outline("M_halflit")
M_PAPER = mat_paper("M_paper")
M_BRONZE = mat_bronze("M_bronze")


def use_material(mat, ob=None):
    for n in style_nodes_of(ob or HERO_OB):
        set_style_material(n, mat)


def carrier_studio():
    """The A1 look, restored before every panel that claims to use it."""
    restore_camera()
    HERO_OB.matrix_world = HERO_M0.copy()
    set_dof(False)
    rig_studio_key()
    set_world_flat(0.06)
    set_ground((0.30, 0.30, 0.31))
    write_colours(PAL_CARRIER)
    use_material(M_PHOTOREAL)
    clear_copies()
    show_hero()


# --------------------------------------------------------------- copies

COPIES = []


def clear_copies():
    global COPIES
    for ob in COPIES:
        try:
            if ob.modifiers and ob.modifiers[0].node_group:
                bpy.data.node_groups.remove(ob.modifiers[0].node_group)
        except Exception:
            pass
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    COPIES = []
    for m in list(bpy.data.materials):
        if m.name.startswith("M_recede_"):
            bpy.data.materials.remove(m)


def add_copy(tag, u, v, depth, scale, rot_deg, mix_to, mix_fac, roughness):
    """A receding instance. Per-copy MATERIAL (its own node-group copy), so
    the only thing that differs between B3 and B5 is the mix target."""
    dup = HERO_OB.copy()
    dup.data = HERO_OB.data
    dup.name = f"copy_{tag}"
    scene.collection.objects.link(dup)
    dup.modifiers[0].node_group = HERO_OB.modifiers[0].node_group.copy()
    mat = mat_photoreal(
        f"M_recede_{tag}", roughness=roughness, mix_to=mix_to, mix_fac=mix_fac
    )
    for n in style_nodes_of(dup):
        set_style_material(n, mat)
        if "Quality" in n.inputs:
            n.inputs["Quality"].default_value = 2
    target = cam_space_point(u, v, depth)
    M = (
        Matrix.Translation(target)
        @ Matrix.Rotation(math.radians(rot_deg), 4, "Z")
        @ Matrix.Scale(scale, 4)
        @ Matrix.Translation(-CENTROID)
    )
    dup.matrix_world = M
    COPIES.append(dup)
    return dup


HERO_HIDDEN = []


def show_hero():
    HERO_OB.hide_render = False
    for ob in HERO_HIDDEN:
        ob.hide_render = True


# Receding array geometry -- identical for B3 / B4 / B5.
RECEDE = [
    ("r1", 0.34, 0.10, 1.34, 0.94, 14.0),
    ("r2", 0.60, 0.20, 1.76, 0.88, 29.0),
    ("r3", 0.80, 0.29, 2.28, 0.82, 47.0),
    ("r4", 0.96, 0.36, 2.92, 0.77, 63.0),
]
RECEDE_MIX = [0.46, 0.63, 0.77, 0.88]
RECEDE_ROUGH = [0.46, 0.52, 0.58, 0.64]
BG_HUE = (0.30, 0.30, 0.31)  # the world/ground colour of the carrier studio


def build_recession(mix_to):
    for (tag, u, v, dmul, sc, rot), mf, rg in zip(
        RECEDE, RECEDE_MIX, RECEDE_ROUGH, strict=False
    ):
        add_copy(tag, u, v, D0 * dmul, sc, rot, mix_to, mf, rg)


# =============================================================== GROUP A


def A1():
    carrier_studio()


render(
    "A1",
    "photoreal_studio",
    set(),
    A1,
    "Principled matte dielectric, real ground the object touches, contact "
    "shadow, backdrop graded by key falloff. The carrier.",
)


def A2():
    carrier_studio()
    use_material(M_AO)
    rig_none()
    set_world_flat(0.85)
    set_ground((0.82, 0.82, 0.82))
    CAM_OB.data.type = "ORTHO"
    CAM_OB.data.ortho_scale = ORTHO_SCALE
    bpy.context.view_layer.update()


render(
    "A2",
    "occlusion_only",
    {"material", "lights", "world", "ground", "camera"},
    A2,
    "mn.material.AmbientOcclusion: emission = albedo * AO^2 at 10 A. "
    "No lights at all; the ground is lit by the world only. Ortho camera.",
)


def A2b():
    A2()
    restore_camera()


render(
    "A2b",
    "occlusion_only_persp",
    {"material", "lights", "world", "ground"},
    A2b,
    "BONUS control: A2 in perspective, so A2b vs A3 differ in the shading "
    "model ALONE. Without it the A2/A3 comparison carries two variables.",
)


def A3():
    carrier_studio()
    use_material(M_FLAT)
    rig_none()
    set_world_flat(0.85)
    set_ground((0.82, 0.82, 0.82))


render(
    "A3",
    "flat_ink",
    {"material", "lights", "world", "ground"},
    A3,
    "mn.material.Flat(outline=True): no lighting model whatsoever. The graphic register.",
)


def A4():
    carrier_studio()
    use_material(M_HALFLIT)
    rig_weak_key()
    set_world_flat(0.85)
    set_ground((0.82, 0.82, 0.82))


render(
    "A4",
    "half_lit_middle",
    {"material", "lights", "world", "ground"},
    A4,
    "AO emission + weak key + outline, i.e. A2's shader plus two things. "
    "The evidence predicts this reads amateur; here it is, testable.",
)


def A5():
    A3()
    set_world_flat(0.012)
    set_ground((0.022, 0.022, 0.026))


render(
    "A5",
    "ink_on_dark",
    {"material", "lights", "world", "ground"},
    A5,
    "A3 with ONE change: ground and world albedo. Predicted failure -- the "
    "dark outline should vanish into the dark ground.",
)

# =============================================================== GROUP B


def B1():
    carrier_studio()


render(
    "B1",
    "hero_centred",
    set(),
    B1,
    "Centred, full margin, everything sharp. The control -- and a "
    "self-check: B1 must be identical to A1 or the studio leaks state.",
)


def B2():
    carrier_studio()
    set_dof(True, fstop=FSTOP)


render(
    "B2",
    "hero_dof",
    {"dof"},
    B2,
    "B1 plus shallow depth of field on a single isolated subject.",
)


def B3():
    carrier_studio()
    set_dof(True, fstop=FSTOP)
    build_recession(BG_HUE)


render(
    "B3",
    "receding_instances",
    {"dof", "geometry", "material"},
    B3,
    "Hero plus four copies receding, each rotated, smaller, rougher, and "
    "desaturated toward the BACKGROUND HUE. The corpus's depth device.",
)


def B4():
    carrier_studio()
    set_dof(True, fstop=FSTOP)
    build_recession(BG_HUE)
    add_copy("front", 0.80, -0.62, D0 * 0.46, 1.0, -22.0, BG_HUE, 0.12, 0.42)
    restore_camera()
    canvas.camera.frame_points(FRAME_PTS, margin=-0.30)
    bpy.context.view_layer.update()
    # Push the subject off the left and bottom edges by moving the camera
    # right and up in its own frame.
    CAM_OB.matrix_world = CAM_OB.matrix_world @ Matrix.Translation(
        Vector((0.42 * D0 * TAN_X, 0.34 * D0 * TAN_Y, 0.0))
    )
    bpy.context.view_layer.update()


render(
    "B4",
    "fragment_crop",
    {"dof", "geometry", "camera", "material"},
    B4,
    "B3 cropped past the subject's edge so it runs off the left and bottom "
    "edges, with one blurred copy IN FRONT of the focus plane.",
)


def B5():
    carrier_studio()
    set_dof(True, fstop=FSTOP)
    build_recession((0.0, 0.0, 0.0))


render(
    "B5",
    "fade_to_black",
    {"dof", "geometry", "material"},
    B5,
    "B3 with ONE change: the recession desaturates toward black instead of "
    "toward the world colour. The move a conventional viewer's fog makes.",
)

# =============================================================== GROUP C


def C1():
    carrier_studio()
    write_colours(PAL_SATURATED)


render(
    "C1",
    "colour_in_object",
    {"color"},
    C1,
    "Saturated protein, neutral world. Colour lives in the object.",
)


def C2():
    carrier_studio()
    write_colours(PAL_NEUTRAL)
    rig_colour()
    set_world_gradient((1.00, 0.30, 0.05), (0.02, 0.14, 0.46), strength=1.6)


render(
    "C2",
    "colour_in_light",
    {"color", "lights", "world"},
    C2,
    "Protein driven to near-neutral; ALL colour supplied by a graded "
    "coloured world plus warm key and cool rim.",
)


def C3():
    carrier_studio()
    write_colours(PAL_ACCENT_SITE)


render(
    "C3",
    "accent_at_the_site",
    {"color"},
    C3,
    "Alpha and beta chains in two near-neighbour desaturated hues; the four "
    "haems the only saturated warm thing in the frame.",
)


def C4():
    carrier_studio()
    write_colours(PAL_ACCENT_INVERTED)


render(
    "C4",
    "accent_inverted",
    {"color"},
    C4,
    "The deliberate failure: whole protein saturated, haems neutral. The "
    "accent spent on the largest object rather than the rarest.",
)

# =============================================================== GROUP D


def D1():
    carrier_studio()
    write_colours(PAL_PAPER)
    use_material(M_PAPER)
    rig_paper_sun()
    set_world_flat(0.13)
    set_ground((0.92, 0.92, 0.92), roughness=0.90)


render(
    "D1",
    "material_paper",
    {"material", "lights", "world", "ground", "color"},
    D1,
    "Cut-and-folded white paper. No hue anywhere -- the material does "
    "not read the Color attribute at all. Cartoon ribbons supply the "
    "real thickness; a low raking sun supplies long soft shadows.",
)


def D2():
    carrier_studio()
    write_colours(PAL_NEUTRAL)
    use_material(M_BRONZE)
    rig_raking_key()
    set_world_flat(0.05)
    set_ground((0.085, 0.085, 0.095))


render(
    "D2",
    "material_bronze",
    {"material", "lights", "world", "ground", "color"},
    D2,
    "Patinated bronze: ONE metal material whose verdigris is driven by "
    "the ambient occlusion already being computed. Recesses green-black "
    "and dielectric, raised edges copper and metallic. One raking key. "
    "Geometry is the carrier's, so this is a bronze CAST, not a relief.",
)


def relief_matrix(s):
    """Compress along the camera's view axis: the projected silhouette is
    almost unchanged, but the form becomes a shallow slab -- an actual
    bas-relief rather than a cast."""
    f = CAM_FWD
    M3 = Matrix.Identity(3)
    for i in range(3):
        for j in range(3):
            M3[i][j] -= (1.0 - s) * f[i] * f[j]
    return Matrix.Translation(CENTROID) @ M3.to_4x4() @ Matrix.Translation(-CENTROID)


def D2b():
    D2()
    HERO_OB.matrix_world = relief_matrix(0.30) @ HERO_M0


render(
    "D2b",
    "material_bronze_relief",
    {"material", "lights", "world", "ground", "color", "geometry"},
    D2b,
    "BONUS: D2 compressed to 30% along the view axis -- a real bas-relief. "
    "Isolates whether 'relief' adds anything over 'cast' at this camera.",
)


# --------------------------------------------------------- D3: two representations

D3_MOL = None
D3_OB = None
D3_SURF = None
D3_BS = None


def mat_ghost(name, alpha=0.32, roughness=0.30):
    """A see-through surface. Needed only because D3 as specified fails: an
    opaque molecular surface hides a BURIED ligand completely."""
    m, nt, out = new_mat(name)
    c = mn_colour(nt)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (250, 0)
    nt.links.new(c.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Alpha"].default_value = alpha
    bsdf.inputs["Coat Weight"].default_value = 0.03
    bsdf.inputs["Specular IOR Level"].default_value = 0.45
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def build_d3():
    global D3_MOL, D3_OB, D3_SURF, D3_BS
    if D3_MOL is not None:
        return
    D3_MOL = mn.Molecule.load(os.path.join(CACHE, "4HHB.bcif"), name="d3")
    D3_OB = D3_MOL.object
    n = D3_MOL.universe.select_atoms("protein").n_atoms
    assert n > 0, "D3 protein selection empty"
    nh = D3_MOL.universe.select_atoms("resname HEM").n_atoms
    assert nh > 0, "D3 HEM selection empty"
    s_surf = add_style_tracked(D3_MOL, "surface", selection="protein")
    s_bs = add_style_tracked(D3_MOL, "ball_and_stick", selection="resname HEM")
    col = np.zeros((D3_MOL.universe.atoms.n_atoms, 4), dtype=np.float32)
    col[:, :3] = PAL_FAMILY["A"]
    col[:, 3] = 1.0
    col[D3_MOL.universe.select_atoms("resname HEM").indices, :3] = PAL_FAMILY["HEM"]
    D3_MOL.store_named_attribute(col, "Color", "FLOAT_COLOR")
    for nd in (s_surf, s_bs):
        set_style_material(nd, M_PHOTOREAL)
    D3_SURF, D3_BS = s_surf, s_bs
    HERO_HIDDEN.append(D3_OB)
    log(f"[D3] surface inputs: {[s.name for s in s_surf.inputs]}")
    # How much of the ligand can the camera actually see through an opaque
    # surface? 4HHB's haems are buried in the globin fold, so the honest
    # answer is "almost none" -- which is the panel's result, not a bug.
    D3_OB.hide_render = True


def D3():
    carrier_studio()
    build_d3()
    HERO_OB.hide_render = True
    D3_OB.hide_render = False
    set_style_material(D3_SURF, M_PHOTOREAL)
    set_style_material(D3_BS, M_PHOTOREAL)


render(
    "D3",
    "two_representations",
    {"geometry", "material", "color"},
    D3,
    "One frame, same atoms, two representations: protein as a surface, "
    "haems as ball-and-stick in the SAME colour family. No colour help, no "
    "label -- the representation change alone must be the pointer.",
)

M_GHOST = mat_ghost("M_ghost")


def D3b():
    carrier_studio()
    build_d3()
    HERO_OB.hide_render = True
    D3_OB.hide_render = False
    set_style_material(D3_SURF, M_GHOST)


render(
    "D3b",
    "two_representations_ghosted",
    {"geometry", "material", "color"},
    D3b,
    "BONUS: D3 with the surface at alpha 0.32. D3 as specified fails "
    "because an OPAQUE surface hides a buried ligand; this is the minimum "
    "change that makes the representation-as-pointer device actually work.",
)


# --------------------------------------------------------- D4: uncertainty

D4_MOL = None
D4_OB = None
D4_INFO = {}
D4_OFFSET = [0.0, 0.0, 0.0]


def build_d4():
    global D4_MOL, D4_OB
    if D4_MOL is not None:
        return
    D4_MOL = mn.Molecule.load(os.path.join(CACHE, "P04637.bcif"), name="d4")
    D4_OB = D4_MOL.object
    u = D4_MOL.universe
    plddt = np.asarray(u.atoms.tempfactors, dtype=np.float64)
    lo, hi = float(plddt.min()), float(plddt.max())
    D4_INFO.update(
        plddt_min=round(lo, 2),
        plddt_max=round(hi, 2),
        plddt_mean=round(float(plddt.mean()), 2),
        n_atoms=len(plddt),
    )
    log(f"[D4] MEASURED pLDDT range {lo:.2f}..{hi:.2f} mean {plddt.mean():.2f}")
    # Ramp set to the MEASURED range, not a default. AlphaFold's own
    # confident/low boundary is 70; check it actually splits this model.
    cutoff = 70.0
    conf_mask = plddt >= cutoff
    n_conf, n_low = int(conf_mask.sum()), int((~conf_mask).sum())
    D4_INFO.update(cutoff=cutoff, n_confident=n_conf, n_low=n_low)
    log(f"[D4] cutoff={cutoff}: confident={n_conf} low={n_low}")
    assert n_conf > 0 and n_low > 0, "pLDDT cutoff does not split this model"
    conf_ag = u.atoms[conf_mask]
    low_ag = u.atoms[~conf_mask]
    s_conf = add_style_tracked(D4_MOL, "surface", selection=conf_ag)
    s_low = add_style_tracked(D4_MOL, "surface", selection=low_ag)
    if "Quality" in s_conf.inputs:
        s_conf.inputs["Quality"].default_value = 3
    # Featureless: inflate and smooth the low-confidence blob so no atomic
    # detail survives, even in its contour.
    #
    # The MN surface style's real knobs are Scale / Relax / Offset / Fillet /
    # Mean Width / Mean Iterations. An earlier version of this guessed at
    # "Probe Size" / "Scale Radii" / "Resolution", none of which exist, and the
    # `if name in inputs` test made that a SILENT no-op -- the panel rendered
    # happily and demonstrated nothing. Hence the assert below.
    log(f"[D4] low-confidence style inputs: {[s.name for s in s_low.inputs]}")
    # EXACTLY the pair validated by the d4_probe2.py sweep -- nothing else.
    #
    # Offset is a surface expansion, default 0.15: 0.35 closes the contour into
    # a readable silhouette, while 2.4 inflates it into a balloon that swallows
    # the confident core. Mean Iterations (default 1) does the smoothing.
    #
    # Do NOT also "tidy up" Relax / Mean Width / Fillet. Relax defaults to 5,
    # and an earlier version set it to 1 believing it was raising the
    # smoothing; that single change turned the silhouette back into the thin
    # stringy arc the panel is supposed to have fixed. The probe validated one
    # combination and the script must ship that combination, not a nearby one.
    smoothing = {"Offset": 0.35, "Mean Iterations": 12}
    applied = []
    for cand, val in smoothing.items():
        if cand not in s_low.inputs:
            log(f"[D4] no socket {cand!r} on the surface style -- skipped")
            continue
        try:
            cur = s_low.inputs[cand].default_value
            s_low.inputs[cand].default_value = val
            got = s_low.inputs[cand].default_value
            applied.append(cand)
            log(f"[D4] {cand}: {cur} -> {got}")
        except Exception as e:
            log(f"[D4] could not set {cand}: {e}")
    assert applied, (
        "no smoothing parameter was applied to the low-confidence "
        "surface -- the panel would silently demonstrate nothing"
    )
    D4_INFO["smoothing_applied"] = applied
    col = np.zeros((len(plddt), 4), dtype=np.float32)
    col[:, :3] = (0.60, 0.63, 0.68)
    col[:, 3] = 1.0
    D4_MOL.store_named_attribute(col, "Color", "FLOAT_COLOR")
    set_style_material(s_conf, M_PHOTOREAL)
    set_style_material(s_low, M_FLAT)
    # Measured extents (BU): confident 5.6x5.2x7.1, low-confidence
    # 9.9x11.7x8.2, all 9.9x11.7x8.5. The disordered part only doubles the
    # extent, so the canonical margin on ALL atoms frames it correctly --
    # framing the core instead and padding to compensate made it a speck.
    D4_INFO["frame_target"] = "all atoms, canonical margin"
    D4_MOL.conf_positions = D4_MOL.position[conf_mask]

    # Seat p53 in the studio. The cyclorama was built at 4HHB's floor height;
    # P04637's coordinates put half of it BELOW that plane, so the ground
    # sliced the model in two and what survived read as a thin arch -- the
    # top of a ring with its lower half buried. Nothing about the styles was
    # wrong; the subject was simply underground.
    p = D4_MOL.position
    cen = p.mean(axis=0)
    D4_OFFSET[:] = [
        CENTROID.x - float(cen[0]),
        CENTROID.y - float(cen[1]),
        Z_GROUND - float(p[:, 2].min()) + 0.10,
    ]
    D4_OB.location = tuple(D4_OFFSET)
    log(f"[D4] seated in studio, offset={[round(v, 3) for v in D4_OFFSET]}")
    HERO_HIDDEN.append(D4_OB)
    D4_OB.hide_render = True


def D4():
    carrier_studio()
    build_d4()
    HERO_OB.hide_render = True
    D4_OB.hide_render = False
    # Light ground: Flat has no outline-colour control, so on a dark ground the
    # low-confidence silhouette edge would disappear entirely.
    set_ground((0.78, 0.78, 0.78))
    set_world_flat(0.30)
    restore_camera()
    bpy.context.view_layer.update()
    # Frame the points where they actually ARE, i.e. after the studio offset.
    pts = D4_MOL.position.copy()
    pts += np.array(D4_OFFSET, dtype=pts.dtype)
    canvas.camera.frame_points(pts, margin=MARGIN)
    bpy.context.view_layer.update()


render(
    "D4",
    "uncertainty_as_material",
    {"geometry", "material", "color", "camera", "world", "ground"},
    D4,
    "P04637 (AlphaFold p53). Confident regions as a full-quality surface; "
    "low-confidence regions as a flat featureless silhouette with no atomic "
    "detail. Ramp set to the MEASURED pLDDT range.",
)

# =============================================================== manifest

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
    "ortho_scale": round(ORTHO_SCALE, 4),
    "camera_matrix": [round(v, 5) for row in CAM_MATRIX for v in row],
    "d4": D4_INFO,
    "panels": RESULTS,
}

# Pairwise checks: the claims that only survive if two panels differ in
# exactly one thing.
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
    "A1", "B1", set(), "the carrier is stateless: A1 and B1 must be the same image"
)
check_pair("A2b", "A3", {"material"}, "AO vs flat ink: the shading model alone")
check_pair(
    "A3",
    "A5",
    {"world", "ground"},
    "ink on light vs dark ground: the ground albedo alone",
)
check_pair("A2", "A2b", {"camera"}, "ortho vs perspective for the same AO shading")
check_pair(
    "B3", "B5", {"material"}, "recede to background hue vs to black: the mix target alone"
)
check_pair("B1", "B2", {"dof"}, "depth of field alone")
check_pair("C1", "C3", {"color"}, "where the colour sits: object vs site")
check_pair("C3", "C4", {"color"}, "accent at the rarest vs the largest object")
check_pair("C1", "C4", {"color"}, "saturated chains, accent moved")
check_pair(
    "D3", "D3b", {"material"}, "opaque vs ghosted surface: the surface material alone"
)
check_pair("D2", "D2b", {"geometry"}, "bronze cast vs bas-relief: form alone")
manifest["pairs"] = pairs

with open(os.path.join(OUT, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
with open(os.path.join(OUT, "build_log.txt"), "w") as f:
    f.write("\n".join(LOG))

log("\n===== SUMMARY =====")
for r in RESULTS:
    log(
        f"{r['id']:4} {r['status']:6} {r.get('seconds', '?'):>6}s  {r['name']}"
        + (f"  VIOLATIONS={r['violations']}" if r["violations"] else "")
        + (f"  ERR={r.get('error')}" if r["status"] != "ok" else "")
    )
log(f"total render seconds: {sum(r.get('seconds', 0) for r in RESULTS):.1f}")
