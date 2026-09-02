import asyncio
import sys
import numpy as np
from PIL import Image
sys.path.append("/Users/charlie/code/protean")
from tests.browser import viewer_session
from tests.test_glass_differential import _capture_shot

async def main():
    async with viewer_session("1ubq") as session:
        await session.request("lighting", {"rig": "studio"})
        baseline = await _capture_shot(session)
        print("baseline mean:", np.mean(baseline.pixels), "shape:", baseline.pixels.shape)
        img = Image.fromarray(baseline.pixels)
        img.save("scratch/baseline_test.png")

asyncio.run(main())
