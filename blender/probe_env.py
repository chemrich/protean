#!/usr/bin/env python3
"""probe_env.py -- answer the unknowns before writing the sheet.

1. biotite SASA from an AtomArray built out of MN's own positions
2. a SECOND geometry-nodes modifier stacked after MN's, doing
   Realize Instances -> Distribute Points on Faces -> Instance on Points
3. how many points that actually produces, and the surface area available
4. whether Sample Nearest / Sample Index can read a float attribute off a
   separate atom-position point cloud
"""

import os
import traceback

import bpy
import numpy as np

CACHE = os.path.expanduser("~/MolecularNodesCache")

bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.molecularnodes")
import bl_ext.blender_org.molecularnodes as mn  # noqa

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

scene = bpy.context.scene
scene.render.engine = "CYCLES"

mol = mn.Molecule.load(os.path.join(CACHE, "4HHB.bcif"), name="hero")
ob = mol.object
u = mol.universe
pos = mol.position
print(f"[probe] atoms={u.atoms.n_atoms} pos.shape={pos.shape}")
print(f"[probe] pos range x {pos[:, 0].min():.3f}..{pos[:, 0].max():.3f}")
print(f"[probe] pos range y {pos[:, 1].min():.3f}..{pos[:, 1].max():.3f}")
print(f"[probe] pos range z {pos[:, 2].min():.3f}..{pos[:, 2].max():.3f}")
print(
    f"[probe] MDAnalysis coords range x "
    f"{u.atoms.positions[:, 0].min():.3f}..{u.atoms.positions[:, 0].max():.3f}"
)

# ---------------------------------------------------------------- 1. SASA
try:
    import biotite
    import biotite.structure as struc

    print(f"[probe] biotite {biotite.__version__}")
    n = u.atoms.n_atoms
    arr = struc.AtomArray(n)
    # MN world positions are angstrom/10; SASA wants angstrom.
    arr.coord = np.ascontiguousarray(pos * 10.0, dtype=np.float32)
    elems = np.array([e.upper() for e in u.atoms.elements], dtype="U2")
    arr.element = elems
    arr.atom_name = np.array(u.atoms.names, dtype="U6")
    arr.res_name = np.array(u.atoms.resnames, dtype="U5")
    arr.res_id = np.asarray(u.atoms.resids, dtype=int)
    arr.chain_id = np.array(
        u.atoms.segids if len(set(u.atoms.segids)) > 1 else u.atoms.chainIDs, dtype="U4"
    )
    print(f"[probe] elements present: {sorted(set(elems.tolist()))}")
    s = struc.sasa(arr, vdw_radii="Single", point_number=100)
    print(
        f"[probe] sasa raw: nan={int(np.isnan(s).sum())} "
        f"min={np.nanmin(s):.3f} max={np.nanmax(s):.3f} "
        f"mean={np.nanmean(s):.3f} total={np.nansum(s):.1f} A^2"
    )
    sf = np.nan_to_num(s, nan=0.0)
    print(f"[probe] fraction of atoms with sasa>0: {(sf > 0).mean():.3f}")
    print(
        f"[probe] percentiles: "
        f"{[round(float(np.percentile(sf, p)), 2) for p in (50, 75, 90, 99)]}"
    )
except Exception as e:
    print("[probe] SASA FAILED:", e)
    traceback.print_exc()

# ------------------------------------------------- 2. evaluated geometry
s_cart = None
before = set(ob.modifiers[0].node_group.nodes.keys())
mol.add_style("cartoon", selection="protein")
mol.add_style("ball_and_stick", selection="resname HEM")
for nd in ob.modifiers[0].node_group.nodes:
    if nd.name.startswith("Style") and "Quality" in nd.inputs:
        nd.inputs["Quality"].default_value = 3

bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
ev = ob.evaluated_get(dg)
print(f"[probe] evaluated type={ev.type}")
try:
    me = ev.to_mesh()
    print(f"[probe] to_mesh: verts={len(me.vertices)} polys={len(me.polygons)}")
    ev.to_mesh_clear()
except Exception as e:
    print("[probe] to_mesh failed:", e)

# instances?
ninst = 0
for inst in dg.object_instances:
    if inst.is_instance and inst.parent and inst.parent.original == ob:
        ninst += 1
print(f"[probe] depsgraph instances under hero: {ninst}")

# ------------------------------------------- 3. second GN modifier: scatter
# build a tiny blade mesh
bm = bpy.data.meshes.new("blade")
bm.from_pydata(
    [(0, 0, 0), (0.01, 0, 0), (0.005, 0.008, 0), (0.005, 0.004, 0.10)],
    [],
    [(0, 1, 3), (1, 2, 3), (2, 0, 3), (0, 2, 1)],
)
bm.update()
blade = bpy.data.objects.new("blade", bm)
scene.collection.objects.link(blade)
blade.hide_render = True

ng = bpy.data.node_groups.new("GN_scatter", "GeometryNodeTree")
ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
gi = ng.nodes.new("NodeGroupInput")
gi.location = (-800, 0)
go = ng.nodes.new("NodeGroupOutput")
go.location = (800, 0)
real = ng.nodes.new("GeometryNodeRealizeInstances")
real.location = (-600, 0)
ng.links.new(gi.outputs[0], real.inputs[0])
dist = ng.nodes.new("GeometryNodeDistributePointsOnFaces")
dist.location = (-400, 0)
dist.distribute_method = "RANDOM"
dist.inputs["Density"].default_value = 60.0
ng.links.new(real.outputs[0], dist.inputs[0])
oi = ng.nodes.new("GeometryNodeObjectInfo")
oi.location = (-400, -300)
oi.inputs["Object"].default_value = blade
oi.transform_space = "RELATIVE"
iop = ng.nodes.new("GeometryNodeInstanceOnPoints")
iop.location = (-150, 0)
ng.links.new(dist.outputs["Points"], iop.inputs["Points"])
ng.links.new(oi.outputs["Geometry"], iop.inputs["Instance"])
# align to normal
alg = ng.nodes.new("FunctionNodeAlignRotationToVector")
alg.location = (-400, -150)
alg.axis = "Z"
ng.links.new(dist.outputs["Normal"], alg.inputs["Vector"])
ng.links.new(alg.outputs["Rotation"], iop.inputs["Rotation"])
join = ng.nodes.new("GeometryNodeJoinGeometry")
join.location = (400, 0)
ng.links.new(gi.outputs[0], join.inputs[0])
ng.links.new(iop.outputs[0], join.inputs[0])
ng.links.new(join.outputs[0], go.inputs[0])

md = ob.modifiers.new("Scatter", "NODES")
md.node_group = ng
print(f"[probe] modifier stack: {[(m.name, m.type) for m in ob.modifiers]}")
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
ev = ob.evaluated_get(dg)
ninst2 = 0
for inst in dg.object_instances:
    if inst.is_instance and inst.parent and inst.parent.original == ob:
        ninst2 += 1
print(f"[probe] instances AFTER scatter: {ninst2}  (was {ninst})")
try:
    me = ev.to_mesh()
    print(
        f"[probe] to_mesh after scatter: verts={len(me.vertices)} "
        f"polys={len(me.polygons)}"
    )
    ev.to_mesh_clear()
except Exception as e:
    print("[probe] to_mesh after scatter failed:", e)

# surface area of the realized cartoon, to calibrate density
ng2 = bpy.data.node_groups.new("GN_area", "GeometryNodeTree")
ng2.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
ng2.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
print("[probe] done")
