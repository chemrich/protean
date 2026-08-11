"""A wider corpus: flex protean across structure classes, analysis and errors."""

import asyncio
import sys

sys.path.insert(0, "/Users/charlie/code/protean")
import protean_mcp.server as s
from tests.browser import viewer_session


async def probe(label, coro):
    try:
        r = await coro
        if isinstance(r, dict):
            keys = {
                k: (f"list[{len(v)}]" if isinstance(v, list) else v)
                for k, v in list(r.items())[:6]
            }
            print(f"OK    {label}: {keys}")
        else:
            print(f"OK    {label}: {str(r)[:150]}")
        return r
    except Exception as exc:
        print(f"FAIL  {label}: {type(exc).__name__}: {str(exc)[:170]}")
        return None


async def main():
    async with viewer_session("1ubq") as session:
        s._bridge = session.bridge

        # --- structure classes -------------------------------------------------
        for pdb, note in (
            ("1bna", "B-DNA, no protein"),
            ("5fji", "glycoprotein"),
            ("1aon", "GroEL/GroES, large"),
            ("1l2y", "NMR, 38 models"),
        ):
            await probe(f"fetch {pdb} ({note})", s.fetch_structure(pdb))
            await probe(f"  select polymer on {pdb}", s.select("polymer", name="p"))

        # --- nucleic-specific selections ---------------------------------------
        await probe("fetch 1bna", s.fetch_structure("1bna"))
        for sel in ("nucleic", "backbone", "sidechain", "resn DA", "name P"):
            await probe(f"  select {sel!r}", s.select(sel, name="t"))

        # --- measurements -------------------------------------------------------
        await probe("fetch 1ubq", s.fetch_structure("1ubq"))
        await probe("  select CA 1", s.select("resi 1 and name CA", name="a"))
        await probe("  select CA 2", s.select("resi 2 and name CA", name="b"))
        await probe("  select CA 3", s.select("resi 3 and name CA", name="c"))
        await probe("  select CA 4", s.select("resi 4 and name CA", name="d"))
        await probe("  distance", s.measure("distance", ["a", "b"]))
        await probe("  angle", s.measure("angle", ["a", "b", "c"]))
        await probe("  dihedral", s.measure("dihedral", ["a", "b", "c", "d"]))

        # --- set operations -----------------------------------------------------
        await probe("  combine union", s.combine("union", ["a", "b"], "ab"))
        await probe("  combine intersect", s.combine("intersect", ["a", "ab"], "i"))
        await probe("  near", s.near("a", 5.0, name="n"))
        await probe("  invert", s.invert("a", name="inv"))

        # --- errors that should be refused loudly -------------------------------
        await probe(
            "ERR unknown representation",
            s.show(representation="cartoonn", selection="all"),
        )
        await probe("ERR unknown colour theme", s.color("not-a-theme", name="p"))
        await probe(
            "ERR missing handle", s.show(representation="cartoon", handle="ghost")
        )
        await probe("ERR bad selection syntax", s.select("chain and and", name="x"))
        await probe("ERR frame without trajectory", s.frame(3))
        await probe("ERR interface with unknown chain", s.interface("A", "Z"))
        await probe(
            "ERR snapshot both widths",
            s.snapshot("/tmp/x", column="single", width_mm=50.0),
        )

        # --- sessions -----------------------------------------------------------
        await probe("save session", s.save_session("/tmp/corpus.protean"))
        await probe("load session", s.load_session("/tmp/corpus.protean"))


asyncio.run(main())
