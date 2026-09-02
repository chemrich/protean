import asyncio
from tests.test_glass_differential import _capture_shot, coverage
from tests.browser import viewer_session

async def main():
    async with viewer_session("1ubq") as session:
        shot = await _capture_shot(session)
        err1 = await session.evaluate("window.GL_ERROR_1 || 0")
        err2 = await session.evaluate("window.GL_ERROR_2 || 0")
        print("GL_ERROR_1 (Refraction):", err1)
        print("GL_ERROR_2 (Copy):", err2)
        print("COVERAGE:", coverage(shot))

if __name__ == "__main__":
    asyncio.run(main())
