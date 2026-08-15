"""The viewer is a canvas, and the panels that could desync it are not there.

Mol\\*'s own UI is built for a person driving it, and every panel it ships is a
way to load a molecule or edit the state tree by hand. Here that is a hazard
rather than a convenience: the analysis half lives in the Python process, so
"Download Structure → 1TQN → Apply" in the left panel changes the picture and
nothing else, and the model goes on answering — correctly — about a molecule
that is no longer on screen.

Asserted against a real browser rather than against `main.ts`, because the
options are strings handed to a library: a typo in one leaves the panel there
and nothing complains. These tests read the rendered DOM.
"""

from __future__ import annotations

import pytest

from .browser import BROWSER_MARKS, viewer_session

pytestmark = BROWSER_MARKS


# Class names Mol* gives the chrome. Read off the rendered page rather than
# recalled: `.msp-layout-left` is the data-loading panel, `.msp-layout-right`
# the state editor, `.msp-sequence` the residue strip.
PANELS = {
    "left panel (loads structures by hand)": ".msp-layout-left",
    "right panel (edits the state tree)": ".msp-layout-right",
    "sequence strip": ".msp-sequence",
    "log": ".msp-log",
}


@pytest.fixture(scope="module")
async def page():
    async with viewer_session("1ubq") as session:
        yield session


async def test_the_canvas_is_there(page):
    """The floor: decluttering must not have removed the thing itself."""
    found = await page.evaluate("JSON.stringify(!!document.querySelector('canvas'))")
    assert found is True


@pytest.mark.parametrize(("what", "selector"), sorted(PANELS.items()))
async def test_the_chrome_is_gone(page, what, selector):
    count = await page.evaluate(
        f"JSON.stringify(document.querySelectorAll('{selector}').length)"
    )
    assert count == 0, f"{what} is back on screen ({selector})"


async def test_the_status_pill_survives(page):
    """The one piece of UI protean puts there itself.

    It is how a watcher knows the tab is still the one being driven, and the
    connection tests in the viewer suite assert its text.
    """
    text = await page.evaluate(
        "JSON.stringify(document.getElementById('status').textContent)"
    )
    assert text == "connected"


async def test_the_canvas_fills_the_window(page):
    """The point of removing the panels, stated as a measurement.

    With the left and right panels drawn, the canvas gets roughly half the
    width; without them it should be within a few per cent of the whole window.
    """
    ratio = await page.evaluate(
        "JSON.stringify(document.querySelector('canvas').getBoundingClientRect().width"
        " / window.innerWidth)"
    )
    assert ratio > 0.95, f"the canvas is only {ratio:.0%} of the window"
