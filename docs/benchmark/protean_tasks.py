"""The protean half of the benchmark.

Run from the repository root, with a viewer build present:

    npm run build --prefix viewer
    PROTEAN_DIFFERENTIAL=1 \
      PROTEAN_CHROME_FLAGS="--headless=new --no-sandbox --window-size=800,600" \
      uv run python docs/benchmark/protean_tasks.py

It borrows the test suite's throwaway-browser harness, because protean's tools
need a viewer connected and that is the shortest way to get one.
"""

import asyncio

import protean_mcp.server as s
from tests.browser import viewer_session


async def main():
    async with viewer_session("1hho") as session:
        s._bridge = session.bridge
        await s.fetch_structure("1hho")
        r = await s.interface("A", "B")
        print("INTERFACE keys:", ", ".join(sorted(r)))
        for k in sorted(r):
            v = r[k]
            if isinstance(v, list):
                print(f"INTERFACE {k} = list of {len(v)}; first {v[0] if v else None}")
            else:
                print(f"INTERFACE {k} = {v}")
        sup = await s.superpose("1ake", "4ake")
        print(
            "SUPERPOSE rmsd {:.3f} residues {} identity {:.1f}%".format(
                sup["rmsd"], sup["aligned_residues"], sup["sequence_identity"] * 100
            )
        )
        await s.fetch_structure("1ca2")
        site = await s.select("byres (polymer within 2.6 of metals)", name="site")
        print(
            "SITE atoms {} residues {}".format(site["atom_count"], site["residue_count"])
        )
        print(
            "SITE listed:", [(x["chain"], x["seq"], x["comp"]) for x in site["residues"]]
        )
        for bad in ["ss H", "bymolecule (resi 10)", "rank 5", "alt A"]:
            try:
                await s.select(bad, name="t")
                print(f"REFUSED {bad!r} -> accepted")
            except Exception as exc:
                print(f"REFUSED {bad!r} -> {str(exc)[:90]}")


asyncio.run(main())
