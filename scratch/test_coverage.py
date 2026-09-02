import asyncio
from tests.test_glass_differential import _capture_shot, coverage
from tests.browser import viewer_session

async def main():
    async with viewer_session("1ubq") as session:
        shot = await _capture_shot(session)
        print("COVERAGE:", coverage(shot))

if __name__ == "__main__":
    asyncio.run(main())
