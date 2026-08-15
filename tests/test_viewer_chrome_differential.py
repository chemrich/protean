"""The viewer opens out of the way, and everything is one click back.

Mol\\*'s panels are its controls for a person driving Mol\\* directly: the left
one loads structures, the right one edits the state tree. Used here they change
the picture and nothing else — the analysis half lives in the Python process,
so the model goes on answering, correctly, about the molecule it loaded rather
than the one now on screen.

So they start collapsed rather than removed. A viewer you cannot inspect is its
own kind of opaque: when the picture looks wrong, the state tree is where the
answer is. The default is out of the way; reaching them costs one click.

Asserted against a real browser rather than against `main.ts`, because the
options are strings handed to a library — a typo leaves the panel open and
nothing complains — and because the right-hand tab is our own DOM, whose first
version flipped its chevron while the panel stayed shut.
"""

from __future__ import annotations

import asyncio

import pytest

from .browser import BROWSER_MARKS, viewer_session

pytestmark = BROWSER_MARKS

RAIL = ".msp-layout-left"
PANEL = ".msp-layout-right"


@pytest.fixture(scope="module")
async def page():
    async with viewer_session("1ubq") as session:
        yield session


CLICK_TAB = "(document.getElementById('panel-tab').click(), JSON.stringify('ok'))"


async def width_of(page, selector: str) -> int:
    return int(
        await page.evaluate(
            f"JSON.stringify(document.querySelector('{selector}')?.offsetWidth ?? 0)"
        )
    )


async def test_the_canvas_is_there(page):
    """The floor: decluttering must not have removed the thing itself."""
    assert await page.evaluate("JSON.stringify(!!document.querySelector('canvas'))")


async def test_the_left_panel_is_a_rail_rather_than_a_panel(page):
    """Collapsed, not hidden — Mol* renders it as a 32px strip of icons."""
    assert 0 < await width_of(page, RAIL) <= 48


async def test_the_right_panel_starts_shut(page):
    assert await width_of(page, PANEL) == 0


async def test_the_sequence_strip_is_there(page):
    """The one panel that reports rather than acts, so it stays on by default.

    Reading along while a model works is most of why a person has the viewer
    open. Its clicks set a Mol* focus the Python side never hears about, which
    costs a highlight and changes no analysis.
    """
    count = await page.evaluate(
        "JSON.stringify(document.querySelectorAll('.msp-sequence').length)"
    )
    assert count == 1


async def test_the_status_pill_clears_the_sequence_strip(page):
    """Both are pinned to the top-right; the pill sat in the strip's band.

    A long chain wraps across that band, so the residues would have run under
    the pill. Measured off the strip rather than nudged by a constant.
    """
    clears = await page.evaluate(
        "JSON.stringify("
        "document.getElementById('status').getBoundingClientRect().top >="
        "document.querySelector('.msp-sequence').getBoundingClientRect().bottom)"
    )
    assert clears is True


async def test_the_canvas_gets_nearly_the_whole_window(page):
    """The point of the default, stated as a measurement."""
    ratio = await page.evaluate(
        "JSON.stringify(document.querySelector('canvas').getBoundingClientRect().width"
        " / window.innerWidth)"
    )
    assert ratio > 0.9, f"the canvas is only {ratio:.0%} of the window"


async def test_the_tab_opens_the_panel_and_shuts_it_again(page):
    """The claim the whole design rests on, and the one that was false first.

    The first version called `plugin.layout.setProps`, which writes the layout
    state without firing the event React redraws on. The chevron flipped, the
    state read `full`, and the panel stayed 0px wide — a control reporting
    success while doing nothing. Asserting the *width* is what caught it;
    asserting the chevron would have passed.
    """
    assert await width_of(page, PANEL) == 0

    await page.evaluate(
        "(document.getElementById('panel-tab').click(), JSON.stringify('ok'))"
    )
    await asyncio.sleep(1)
    assert await width_of(page, PANEL) > 100, "the tab did not open the panel"

    await page.evaluate(
        "(document.getElementById('panel-tab').click(), JSON.stringify('ok'))"
    )
    await asyncio.sleep(1)
    assert await width_of(page, PANEL) == 0, "the tab did not shut the panel"


async def test_the_status_pill_survives_and_moves_out_of_the_way(page):
    """It is pinned to the corner the panel opens into, and used to sit on it."""
    assert (
        await page.evaluate(
            "JSON.stringify(document.getElementById('status').textContent)"
        )
        == "connected"
    )

    await page.evaluate(
        "(document.getElementById('panel-tab').click(), JSON.stringify('ok'))"
    )
    await asyncio.sleep(1)
    offset = await page.evaluate(
        "JSON.stringify(document.getElementById('status').style.right)"
    )
    await page.evaluate(
        "(document.getElementById('panel-tab').click(), JSON.stringify('ok'))"
    )
    await asyncio.sleep(1)
    assert offset not in ("", "0px", "8px"), f"the pill stayed under the panel ({offset})"
