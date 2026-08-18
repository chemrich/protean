"""A corpus that drives protean two ways, and reports only the surprises.

Every probe declares what it expects — "ok" or "refused" — and the run prints
the ones that came out the other way. That is what makes several hundred probes
readable: a probe that behaves is not news, and a bad input that succeeds is
the failure mode this project is built around.

Two personalities drive it:

  careful      a structural biologist doing conventional work on real
               structures. Finds coverage gaps: things that ought to work and
               do not.
  adversarial  boundary values, wrong order, abuse and nonsense. Finds silent
               successes: things that ought to be refused and are not.

Run from the repository root with a viewer build present:

    npm run build --prefix viewer
    PROTEAN_DIFFERENTIAL=1 \
      PROTEAN_CHROME_FLAGS="--headless=new --no-sandbox --window-size=800,600" \
      uv run python docs/benchmark/protean_corpus.py
"""

import asyncio
import sys
from pathlib import Path

# Run as a script from anywhere: the test suite's browser harness is the
# shortest way to get a viewer connected, and it lives at the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import protean_mcp.server as s
from tests.browser import viewer_session

SURPRISES: list[str] = []
COUNT = {"ok": 0, "refused": 0, "surprise": 0}


async def probe(persona, label, expect, coro):
    """Run one probe. `expect` is "ok" or "refused"."""
    try:
        result = await coro
        outcome, detail = "ok", str(result)[:120]
    except Exception as exc:
        outcome, detail = "refused", f"{type(exc).__name__}: {str(exc)[:120]}"
    COUNT[outcome] += 1
    if outcome != expect:
        COUNT["surprise"] += 1
        SURPRISES.append(
            f"[{persona}] {label}\n    expected {expect}, got {outcome}: {detail}"
        )
    return outcome


# Cached and small enough to run many probes against.
STRUCTURES = ["1ubq", "1ca2", "1bna", "5fji", "1l2y", "1hho", "4hhb"]

# Selections a structural biologist actually writes.
CAREFUL_SELECTIONS = [
    "all",
    "polymer",
    "protein",
    "nucleic",
    "solvent",
    "hetatm",
    "organic",
    "inorganic",
    "metals",
    "ion",
    "backbone",
    "sidechain",
    "hydro",
    "chain A",
    "not chain A",
    "resi 1-10",
    "resi 5+7+9",
    "name CA",
    "name CA+CB",
    "elem C",
    "elem N",
    "ss H",
    "ss S",
    "ss L",
    "ss H+S",
    "bymolecule (resi 1)",
    "bound_to (resi 1)",
    "neighbor (resi 1)",
    "resi 1 extend 1",
    "resi 1 extend 2",
    "rank 5",
    "rank 0-10",
    "resn ALA",
    "b > 30",
    "b < 10",
    "byres (name CA)",
    "polymer within 5 of hetatm",
    "byres (polymer within 4 of solvent)",
    "chain A and not solvent",
    "(polymer or hetatm) and not hydro",
]

# Things that should be refused, one way or another.
ADVERSARIAL_SELECTIONS = [
    "",
    "   ",
    "and",
    "or not",
    "chain",
    "resi",
    "resi abc",
    "resi 1-",
    "chain A and",
    "((chain A)",
    "chain A)",
    "not",
    "within 5 of",
    "byres",
    "elem",
    "elem Zz",
    "ss Q",
    "ss helical",
    "name",
    "b >",
    "b > abc",
    "alt A",
    "extend 2",
    "resi 1 extend 0",
    "resi 1 extend 1.5",
    "bymolecule",
    "chain A within of chain B",
    "resi 999999999999",
    "chain \x00",
    "a" * 500,
    "chain A" * 50,
]


async def careful(session):
    """Conventional workflows across structure classes."""
    for pdb in STRUCTURES:
        await probe("careful", f"fetch {pdb}", "ok", s.fetch_structure(pdb))
        for sel in CAREFUL_SELECTIONS:
            await probe(
                "careful", f"{pdb}: select {sel!r}", "ok", s.select(sel, name="t")
            )
        for rep in (
            "cartoon",
            "ball-and-stick",
            "spacefill",
            "molecular-surface",
            "line",
        ):
            await probe(
                "careful",
                f"{pdb}: show {rep}",
                "ok",
                s.show(representation=rep, selection="polymer", name="p"),
            )
        for theme in (
            "chain-id",
            "element-symbol",
            "residue-name",
            "uncertainty",
            "hydrophobicity",
            "sequence-id",
        ):
            await probe(
                "careful", f"{pdb}: color {theme}", "ok", s.color(theme, name="p")
            )
        await probe("careful", f"{pdb}: focus", "ok", s.focus(name="p"))
        await probe("careful", f"{pdb}: orient", "ok", s.orient())
        await probe("careful", f"{pdb}: label", "ok", s.label(name="p", level="residue"))
        await probe("careful", f"{pdb}: hide/unhide", "ok", s.hide(name="p"))
        await probe("careful", f"{pdb}: unhide", "ok", s.unhide(name="p"))
        await probe("careful", f"{pdb}: capabilities", "ok", s.capabilities())
        await probe("careful", f"{pdb}: list_selections", "ok", s.list_selections())

    # Multi-chain analysis where it is meaningful.
    for pdb, a, b in (("1hho", "A", "B"), ("4hhb", "A", "B"), ("4hhb", "A", "C")):
        await probe("careful", f"fetch {pdb} for interface", "ok", s.fetch_structure(pdb))
        await probe("careful", f"{pdb}: interface {a}/{b}", "ok", s.interface(a, b))

    # Style surface, applied the way a figure would.
    await probe("careful", "fetch 1ubq for styling", "ok", s.fetch_structure("1ubq"))
    await probe(
        "careful",
        "show cartoon",
        "ok",
        s.show(representation="cartoon", selection="polymer", name="p"),
    )
    for rig in ("standard", "flat", "three-point", "rim", "ring", "studio"):
        await probe("careful", f"lighting {rig}", "ok", s.lighting(rig=rig))
    for finish in ("matte", "satin", "glossy", "metallic", "chrome"):
        await probe(
            "careful", f"material {finish}", "ok", s.material(finish=finish, name="p")
        )
    for style in ("normal", "cel", "xray", "xray-inverted", "flat"):
        await probe("careful", f"shading {style}", "ok", s.shading(style=style, name="p"))
    for gradient, kwargs in (
        ("off", {}),
        ("horizontal", {"gradient_from": "#112233", "gradient_to": "#445566"}),
        ("radial", {"gradient_from": "#000000", "gradient_to": "#ffffff"}),
    ):
        await probe(
            "careful",
            f"background gradient {gradient}",
            "ok",
            s.background(gradient=gradient, **kwargs),
        )
    for name in ("publication-cartoon", "illustrative", "ghost-heart"):
        await probe("careful", f"preset {name}", "ok", s.preset(name))
    for effect in (
        {"outline": True},
        {"occlusion": False},
        {"shadow": True},
        {"depth_of_field": True},
        {"bloom": False},
        {"sharpening": True},
    ):
        await probe("careful", f"effects {effect}", "ok", s.effects(**effect))
    await probe("careful", "opacity 0.3", "ok", s.opacity(0.3, name="p"))
    await probe(
        "careful",
        "snapshot single 150dpi",
        "ok",
        s.snapshot("/tmp/corpus_fig", column="single", dpi=150),
    )
    await probe(
        "careful",
        "snapshot width_mm",
        "ok",
        s.snapshot("/tmp/corpus_fig2", width_mm=40.0, dpi=150, format="tiff"),
    )
    await probe("careful", "save session", "ok", s.save_session("/tmp/corpus.protean"))
    await probe("careful", "load session", "ok", s.load_session("/tmp/corpus.protean"))


async def adversarial(session):
    """Boundary values, wrong order, and nonsense."""
    await probe("adversarial", "fetch nonsense id", "refused", s.fetch_structure("zzzz"))
    await probe("adversarial", "fetch empty id", "refused", s.fetch_structure(""))
    await probe(
        "adversarial",
        "fetch path traversal",
        "refused",
        s.fetch_structure("../../etc/passwd"),
    )
    await probe("adversarial", "fetch 1ubq (recover)", "ok", s.fetch_structure("1ubq"))

    for sel in ADVERSARIAL_SELECTIONS:
        await probe(
            "adversarial", f"select {sel[:40]!r}", "refused", s.select(sel, name="t")
        )

    # Handles used wrongly.
    await probe(
        "adversarial",
        "show unknown handle",
        "refused",
        s.show(representation="cartoon", handle="nope"),
    )
    await probe(
        "adversarial",
        "colour unknown handle",
        "refused",
        s.color("chain-id", name="nope"),
    )
    await probe(
        "adversarial", "opacity unknown handle", "refused", s.opacity(0.5, name="nope")
    )
    await probe(
        "adversarial", "material unknown handle", "refused", s.material(name="nope")
    )
    await probe(
        "adversarial", "shading unknown handle", "refused", s.shading("cel", name="nope")
    )
    await probe("adversarial", "focus unknown handle", "refused", s.focus(name="nope"))
    await probe("adversarial", "label unknown handle", "refused", s.label(name="nope"))
    await probe("adversarial", "remove unknown handle", "refused", s.remove(name="nope"))
    await probe(
        "adversarial",
        "measure unknown handles",
        "refused",
        s.measure("distance", ["nope", "nope2"]),
    )
    await probe(
        "adversarial",
        "combine unknown handles",
        "refused",
        s.combine("union", ["nope", "x"], "y"),
    )
    await probe(
        "adversarial", "near unknown handle", "refused", s.near("nope", 5.0, name="z")
    )
    await probe(
        "adversarial", "invert unknown handle", "refused", s.invert("nope", name="z")
    )

    # Numeric boundaries.
    await probe("adversarial", "select all (setup)", "ok", s.select("all", name="p"))
    await probe(
        "adversarial",
        "show polymer (setup)",
        "ok",
        s.show(representation="cartoon", selection="polymer", name="pp"),
    )
    for value in (-1.0, 1.5, 50.0, float("nan"), float("inf")):
        await probe(
            "adversarial", f"opacity {value}", "refused", s.opacity(value, name="pp")
        )
    for value in (-1.0, 2.0, float("nan")):
        await probe(
            "adversarial",
            f"material metalness {value}",
            "refused",
            s.material(metalness=value, name="pp"),
        )
    for value in (0, -5, 1000):
        await probe(
            "adversarial",
            f"shading cel_steps {value}",
            "refused",
            s.shading("cel", name="pp", cel_steps=value),
        )
    for value in (-1.0, 0.0):
        await probe(
            "adversarial",
            f"lighting intensity {value}",
            "refused",
            s.lighting(rig="standard", intensity=value),
        )
    for radius in (-1.0, 0.0, 1e9):
        expect = "ok" if radius > 0 else "refused"
        await probe(
            "adversarial", f"near radius {radius}", expect, s.near("p", radius, name="z")
        )

    # Colours.
    for colour in ("", "red", "#fff", "#gggggg", "ff0000", "#ff00000", "rgb(1,2,3)"):
        await probe(
            "adversarial",
            f"background colour {colour!r}",
            "refused",
            s.background(color=colour),
        )
    await probe("adversarial", "background nothing at all", "refused", s.background())
    await probe(
        "adversarial",
        "background two variants",
        "refused",
        s.background(gradient="radial", image="/tmp/none.png"),
    )

    # Enums given rubbish.
    await probe("adversarial", "lighting unknown rig", "refused", s.lighting(rig="disco"))
    await probe(
        "adversarial", "shading unknown style", "refused", s.shading("toon", name="pp")
    )
    await probe(
        "adversarial",
        "material unknown finish",
        "refused",
        s.material(finish="velvet", name="pp"),
    )
    await probe("adversarial", "preset unknown", "refused", s.preset("cinematic"))
    await probe(
        "adversarial",
        "preset active-site without handle",
        "refused",
        s.preset("active-site"),
    )
    await probe(
        "adversarial",
        "path_trace unknown quality",
        "refused",
        s.path_trace(quality="ultra9"),
    )
    await probe(
        "adversarial",
        "combine unknown operation",
        "refused",
        s.combine("xor", ["p", "pp"], "q"),
    )
    await probe(
        "adversarial",
        "measure unknown kind",
        "refused",
        s.measure("torsion", ["p", "pp"]),
    )
    await probe(
        "adversarial", "measure wrong arity", "refused", s.measure("distance", ["p"])
    )
    await probe(
        "adversarial",
        "label unknown level",
        "refused",
        s.label(name="pp", level="molecule"),
    )
    await probe(
        "adversarial",
        "show unknown representation",
        "refused",
        s.show(representation="ribbons", selection="all"),
    )
    await probe(
        "adversarial",
        "show both selection and handle",
        "refused",
        s.show(representation="cartoon", selection="all", handle="p"),
    )
    await probe(
        "adversarial", "show neither", "refused", s.show(representation="cartoon")
    )

    # Snapshot and capture boundaries.
    await probe("adversarial", "snapshot no width", "refused", s.snapshot("/tmp/x"))
    await probe(
        "adversarial",
        "snapshot both widths",
        "refused",
        s.snapshot("/tmp/x", column="single", width_mm=50.0),
    )
    await probe(
        "adversarial",
        "snapshot unknown column",
        "refused",
        s.snapshot("/tmp/x", column="triple"),
    )
    await probe(
        "adversarial",
        "snapshot unknown format",
        "refused",
        s.snapshot("/tmp/x", column="single", format="bmp"),
    )
    await probe(
        "adversarial",
        "snapshot zero dpi",
        "refused",
        s.snapshot("/tmp/x", column="single", dpi=0),
    )
    await probe(
        "adversarial",
        "snapshot absurd dpi",
        "refused",
        s.snapshot("/tmp/x", column="double", dpi=9600),
    )
    await probe(
        "adversarial",
        "snapshot jpeg + transparent",
        "refused",
        s.snapshot("/tmp/x", column="single", format="jpeg", transparent=True),
    )
    await probe(
        "adversarial", "turntable one frame", "refused", s.turntable("/tmp/t", frames=1)
    )
    await probe(
        "adversarial",
        "turntable runaway frames",
        "refused",
        s.turntable("/tmp/t", frames=99999),
    )
    await probe(
        "adversarial",
        "turntable zero width",
        "refused",
        s.turntable("/tmp/t", frames=4, width=0),
    )

    # Trajectory operations with no trajectory.
    await probe("adversarial", "frame with no trajectory", "refused", s.frame(0))
    await probe("adversarial", "rmsf with no trajectory", "refused", s.rmsf())
    await probe(
        "adversarial", "rmsd_series with no trajectory", "refused", s.rmsd_series()
    )
    await probe(
        "adversarial",
        "record_trajectory with none",
        "refused",
        s.record_trajectory("/tmp/r"),
    )
    await probe(
        "adversarial",
        "load_trajectory missing file",
        "refused",
        s.load_trajectory("/tmp/none.xtc"),
    )
    await probe(
        "adversarial",
        "load_trajectory wrong format",
        "refused",
        s.load_trajectory("/etc/hosts"),
    )

    # Movies and timelines with nothing to work from.
    await probe(
        "adversarial",
        "movie from missing dir",
        "refused",
        s.movie("/tmp/none", "/tmp/x.mp4"),
    )
    await probe(
        "adversarial", "movie unknown container", "refused", s.movie("/tmp", "/tmp/x.avi")
    )
    await probe(
        "adversarial",
        "timeline with no keyframes",
        "refused",
        s.record_timeline("/tmp/tl", frames=4),
    )
    await probe(
        "adversarial",
        "keyframe removal of nothing",
        "refused",
        s.keyframe("ghost", remove=True),
    )
    await probe("adversarial", "spin unknown mode", "refused", s.spin(mode="tumble"))

    # Sessions.
    await probe(
        "adversarial",
        "load missing session",
        "refused",
        s.load_session("/tmp/none.protean"),
    )
    await probe(
        "adversarial", "load non-session file", "refused", s.load_session("/etc/hosts")
    )

    # Handle names that are awkward rather than wrong.
    for name in (
        "a b",
        "with/slash",
        "with.dot",
        "UPPER",
        "1",
        "-",
        "x" * 300,
        "emoji-\N{DNA DOUBLE HELIX}",
    ):
        await probe(
            "adversarial",
            f"handle name {name[:24]!r}",
            "ok",
            s.select("resi 1", name=name),
        )

    # Repetition and ordering.
    await probe("adversarial", "reuse a handle name", "ok", s.select("resi 2", name="p"))
    await probe("adversarial", "remove then use", "ok", s.remove(name="p"))
    await probe(
        "adversarial", "use after remove", "refused", s.color("chain-id", name="p")
    )
    await probe("adversarial", "clear viewer", "ok", s.clear_viewer())
    await probe("adversarial", "select after clear", "ok", s.select("all", name="after"))


async def main():
    async with viewer_session("1ubq") as session:
        s._bridge = session.bridge
        await careful(session)
        await adversarial(session)

    total = COUNT["ok"] + COUNT["refused"]
    print(
        f"\n{total} probes: {COUNT['ok']} ok, {COUNT['refused']} refused, "
        f"{COUNT['surprise']} surprises\n"
    )
    for surprise in SURPRISES:
        print(surprise)


asyncio.run(main())
