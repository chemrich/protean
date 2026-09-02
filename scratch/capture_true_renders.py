import asyncio
from tests.browser import viewer_session
from tests.test_glass_differential import _capture_shot, coverage
import numpy as np

async def main():
    async with viewer_session("1ubq") as session:
        shot = await _capture_shot(session)
        print("1ubq baseline coverage:", coverage(shot))
