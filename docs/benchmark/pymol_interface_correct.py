from pymol import cmd

cmd.fetch("1hho", async_=0)
cmd.remove("solvent")
cmd.set("dot_solvent", 1)
cmd.create("chA", "chain A")
cmd.create("chB", "chain B")
a, b = cmd.get_area("chA"), cmd.get_area("chB")
ab = cmd.get_area("1hho")
print(
    f"SPLIT A {a:.1f} B {b:.1f} AB {ab:.1f} buried_total {a + b - ab:.1f} per_side {(a + b - ab) / 2:.1f}"
)
