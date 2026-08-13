"""Does the session-isolation fixture in conftest actually isolate anything?

This file exists because the obvious way to check it does not work. The
symptom that motivated the fixture -- two "no trajectory loaded" refusals
failing when `test_render_differential.py` runs before `test_server.py` --
is *also* fixed by `fetch_structure` clearing `_trajectory`, and
`test_server.py` fetches a structure long before it reaches those tests. So
the whole suite passes with the fixture disabled, and a full-suite run says
nothing about whether the fixture works.

These two tests are ordered and deliberately coupled: the first leaks, the
second asserts the leak was cleaned up. pytest runs them in definition order
within a file, and they are alone in this file so nothing can come between
them. Disable the fixture and the second one fails; that is the only check
that speaks to the fixture itself.

Assignment here is direct rather than through `monkeypatch`, because
`monkeypatch` reverts on its own and would prove nothing.
"""

from __future__ import annotations

import numpy as np

import protean_mcp.server as server_mod

_SENTINEL = object()


def test_leak_session_state_on_purpose():
    """Stand-in for any test that calls a real tool and leaves state behind."""
    server_mod._trajectory = _SENTINEL
    server_mod._structure_identifier = "left-behind"
    server_mod._keyframes["leaked"] = {"position": [0.0, 0.0, 1.0]}
    server_mod._conservation_scores["leaked"] = {"scores": []}
    server_mod._handles.set("leaked", np.array([0]), "leaked")


def test_the_leak_did_not_reach_the_next_test():
    """The claim. Every one of these would carry over without the fixture."""
    assert server_mod._trajectory is not _SENTINEL
    assert server_mod._structure_identifier != "left-behind"
    assert "leaked" not in server_mod._keyframes
    assert "leaked" not in server_mod._conservation_scores
    assert "leaked" not in server_mod._handles.handles
