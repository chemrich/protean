"""The PyMOL half of the benchmark. Run: pymol -cq docs/benchmark/pymol_tasks.py

Written the way a model that knows PyMOL would write it, not golfed and not
padded. Output is captured verbatim into docs/benchmark.md.
"""

from pymol import cmd


def banner(n, title):
    print(f"\n===== TASK {n}: {title} =====")


# 1 -- catalytic zinc site of carbonic anhydrase II
banner(1, "catalytic site")
cmd.fetch("1ca2", async_=0)
cmd.remove("solvent")
cmd.select("zn", "resn ZN")
cmd.select("site", "byres (polymer within 2.6 of zn)")
print("site atoms:", cmd.count_atoms("site"))
model = cmd.get_model("site and name CA")
print("site residues:", [(a.chain, a.resi, a.resn) for a in model.atom])
for a in cmd.get_model("site and elem N and (name ND1+NE2)").atom:
    d = cmd.get_distance("zn", f"index {a.index}")
    if d < 2.6:
        print(f"  Zn-{a.resn}{a.resi}.{a.name}: {d:.2f} A")

# 2 -- interface between two chains
banner(2, "chain interface, buried area")
cmd.delete("all")
cmd.fetch("1hho", async_=0)
cmd.remove("solvent")
cmd.set("dot_solvent", 1)
a = cmd.get_area("chain A")
b = cmd.get_area("chain B")
ab = cmd.get_area("chain A or chain B")
print(f"area A {a:.1f}  B {b:.1f}  AB {ab:.1f}")
print(f"buried total {(a + b - ab):.1f} A^2  (per side {(a + b - ab) / 2:.1f})")
cmd.select("iface", "byres (chain A within 4.5 of chain B)")
seen = set()
for at in cmd.get_model("iface and name CA").atom:
    seen.add((at.chain, at.resi, at.resn))
print("interface residues on A:", len(seen))
print("  first five:", sorted(seen, key=lambda r: int(r[1]))[:5])

# 3 -- superposition
banner(3, "superposition")
cmd.delete("all")
cmd.fetch("1ake", async_=0)
cmd.fetch("4ake", async_=0)
r = cmd.align("4ake", "1ake")
print(f"align: RMSD {r[0]:.3f} A over {r[1]} atoms, {r[6]} residues aligned")
r2 = cmd.cealign("1ake", "4ake")
print(f"cealign: RMSD {r2['RMSD']:.3f} A over {r2['alignment_length']} residues")

# 5 -- selections protean refuses
banner(5, "selection grammar")
cmd.delete("all")
cmd.fetch("1ubq", async_=0)
for sel in [
    "ss H",
    "ss S",
    "byres (name CA extend 1)",
    "bymolecule (resi 10)",
    "rank 5",
    "alt A",
    "bound_to (resi 10 and name CA)",
]:
    try:
        n = cmd.count_atoms(sel)
        print(f"  {sel!r:38} -> {n} atoms")
    except Exception as exc:
        print(f"  {sel!r:38} -> ERROR {exc}")
