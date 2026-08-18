"""The channel a button uses, and the rule that keeps it narrow.

docs/views.md §3 and §4. A control in the viewer does not draw: it asks the
server, and the server drives the viewer over the ordinary action channel. One
code path, two entry points, so a handle made by a click is an ordinary handle
and the picture a click makes is the picture a model would have made.

The narrowness is the security property, and it is checked against the live
tool registry rather than against a list in a file — the going-public pass
found a hand-written list of nine where fourteen tools existed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import aiohttp
import pytest

import protean_mcp.server as server_mod
from protean_mcp.connection import ViewerError

from .test_server import _load, _record, _tiny_protein_pdb

# A click has to answer within a click's worth of time. Bounded rather than
# awaited outright because the failure this guards against — running the
# handler inside the socket's own message loop — deadlocks rather than errors,
# and a test that hangs is not evidence of anything.
INVOKE_TIMEOUT = 10


@pytest.fixture
def wired(bridge, viewer, monkeypatch):
    """The server driving this test bridge, adopted the way production adopts one."""
    monkeypatch.setattr(server_mod, "_bridge", None)
    server_mod.use_bridge(bridge)
    return viewer


async def _click(viewer, view: str, rid: str = "click-1") -> dict[str, Any]:
    """Ask for a view the way the page does, answering what it triggers.

    One reader, routing: the invoke path is the only place the server sends
    actions *while* handling a message from the page, so the click's reply and
    the actions it caused share a socket. Two coroutines both calling
    `receive()` race, and the loser eats the other's message.
    """
    await viewer.ws.send_json({"action": "protean_invoke", "id": rid, "view": view})
    while True:
        message = await asyncio.wait_for(viewer.ws.receive(), INVOKE_TIMEOUT)
        data: dict[str, Any] = json.loads(message.data)
        if data.get("action") == "protean_invoked":
            return data
        await viewer.answer(data)


# -- criterion 5: the allowlist admits no tool that takes a path ---------------


def _path_taking(tools: list[Any]) -> set[str]:
    return {
        tool.name
        for tool in tools
        if any("path" in prop.lower() for prop in tool.inputSchema.get("properties", {}))
    }


async def test_no_tool_reachable_from_the_page_takes_a_path():
    """The constraint §3.4 calls non-negotiable, read from the live registry.

    The socket is token-authenticated, but a page holding that token can
    already reach the viewer. If a click reached the tool surface it would gain
    snapshot(path=), save_session(path=), movie(path=) and
    electrostatics(path=) — each of which writes where the caller says. The
    write-protection in backlog 21 refuses to change *what a file is*, which is
    not the same as refusing to write.
    """
    writers = _path_taking(await server_mod.mcp.list_tools())

    assert writers, "found no path-taking tools at all, so this proves nothing"
    assert server_mod._PAGE_TOOLS.isdisjoint(writers), (
        f"the page can reach {sorted(server_mod._PAGE_TOOLS & writers)}, "
        "each of which writes to a caller-chosen path"
    )


async def test_the_page_tools_are_tools_that_exist():
    """A guard on the guard: an allowlist of names nothing answers to is vacuous."""
    names = {tool.name for tool in await server_mod.mcp.list_tools()}
    assert server_mod._PAGE_TOOLS
    assert names >= server_mod._PAGE_TOOLS


async def test_every_view_a_click_can_ask_for_is_a_real_preset():
    """The drift guard. A menu entry the server cannot honour is worse than no
    menu: it offers something and then refuses it."""
    assert server_mod._PAGE_VIEWS
    for name, (preset_name, _) in server_mod._PAGE_VIEWS.items():
        assert preset_name in server_mod._PRESETS, (
            f"the page may ask for {name!r}, which means preset "
            f"{preset_name!r}, and no such preset exists"
        )


async def test_a_bridge_the_server_adopts_knows_what_a_click_may_ask(bridge, monkeypatch):
    """The wiring, not the comment about the wiring.

    A bridge reaching the server without this is a socket a page can talk to
    and no rule about what it may ask for.

    The monkeypatch is not decoration: `use_bridge` sets the module global, and
    `_isolate_session_state` deliberately leaves `_bridge` alone — it is a
    connection, not session state. Without this the suite would carry a stopped
    bridge into every later file, which is the shape of backlog item 12.
    """
    monkeypatch.setattr(server_mod, "_bridge", server_mod._bridge)
    assert bridge._invoke is None
    assert server_mod.use_bridge(bridge)._invoke is server_mod._invoke_from_page


# -- criteria 3, 6 and 8: what a click does, and how it is refused -------------


async def test_a_click_runs_the_same_calls_the_tool_would(wired, tmp_path):
    """Criterion 3, asserted on the calls issued rather than on the picture.

    The picture alone would pass with the page drawing for itself, which is the
    arrangement this design exists to rule out: a selection made in the browser
    is a handle the Python side has never heard of, so the model could not refer
    to what the user is looking at.
    """
    await _load(wired, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    calls: list[tuple[str, dict[str, Any]]] = []
    _record(wired, calls)
    clicked_reply = await _click(wired, "ghost-surface")
    clicked = list(calls)
    calls.clear()
    serving = wired.serve(len(clicked))
    await server_mod.preset("ghost-surface")
    await serving
    by_tool = list(calls)

    assert clicked_reply["ok"] is True, clicked_reply.get("error")
    assert clicked, "the click issued no viewer calls at all"
    assert [action for action, _ in clicked] == [action for action, _ in by_tool]


async def test_an_unlisted_view_is_refused_and_says_what_is_available(wired):
    """Criterion 6. The refusal has to be useful, not merely a refusal.

    It also has to be *this* refusal. A channel that forwarded any name would
    still be refused by preset(), with a message naming every preset — which
    reads much the same and means something entirely different, so the vocabulary
    in the message is what separates them.
    """
    reply = await _click(wired, "not-a-view")

    assert reply["ok"] is False
    assert "not-a-view" in reply["error"]
    for view in server_mod._PAGE_VIEWS:
        assert view in reply["error"]
    unlisted = set(server_mod._PRESETS) - set(server_mod._PAGE_VIEWS)
    assert unlisted, "every preset is clickable, so this proves nothing"
    for preset_name in unlisted:
        assert preset_name not in reply["error"], (
            f"the refusal offers {preset_name!r}, which is a preset and not a "
            "view a click may ask for — this is preset() refusing, not the list"
        )


async def test_a_preset_that_is_not_a_listed_view_cannot_be_clicked(wired, tmp_path):
    """The allowlist is the boundary, and this is what shows it bears weight.

    `active-site` is a real preset and not a listed view, so it is the case
    that separates "the list decides" from "the name is forwarded and preset()
    decides". A channel that forwarded would carry it — and every other name
    just as happily, which is the whole risk.

    **The assertion is on which refusal comes back, not that one does.**
    `active-site` needs a handle, so a forwarding channel would also fail — for
    a different reason, from `preset()` rather than from the allowlist. Only
    the wording tells the two apart, so only the wording is asserted. This test
    used `putty` until `putty` became a listed view, at which point its premise
    quietly disappeared.
    """
    await _load(wired, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    assert "active-site" in server_mod._PRESETS
    assert "active-site" not in server_mod._PAGE_VIEWS
    # Handlers registered on purpose. Without them a forwarded name would fail
    # on the first viewer action and this would pass for the wrong reason —
    # which it did, until a mutation that opened the allowlist left it green.
    _record(wired, [])

    reply = await _click(wired, "active-site")

    assert reply["ok"] is False
    assert "Unknown view" in reply["error"], (
        "refused, but by preset() rather than by the allowlist — so the "
        f"channel forwarded the name: {reply['error']}"
    )
    assert "active-site" in reply["error"]
    assert server_mod._user_actions == []


async def test_a_view_request_naming_nothing_is_refused(wired):
    reply = await _click(wired, None)  # type: ignore[arg-type]
    assert reply["ok"] is False
    assert "name a view" in reply["error"]


async def test_a_click_with_nothing_loaded_fails_like_the_tool_does(wired):
    """Criterion 8: same refusal, same wording, so one explanation covers both."""
    server_mod._structure = None
    server_mod._structure_error = None

    by_click = await _click(wired, "ghost-surface")
    with pytest.raises(ViewerError) as by_tool:
        await server_mod.preset("ghost-surface")

    assert by_click["ok"] is False
    assert by_click["error"] == str(by_tool.value)


async def test_a_click_is_answered_while_the_handler_drives_the_viewer(wired, tmp_path):
    """The deadlock this design walks into if the handler runs inline.

    The handler drives the viewer, so it sends an action and waits for a reply —
    a reply the socket's own message loop has to read. Run inline, that loop is
    inside the handler while the handler waits on the loop, and the click hangs
    until its own budget runs out and then blames the viewer. Being answered at
    all, inside the timeout, is the whole assertion.
    """
    await _load(wired, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    _record(wired, [])
    reply = await asyncio.wait_for(_click(wired, "ghost-surface"), INVOKE_TIMEOUT)

    assert reply["ok"] is True, reply.get("error")


# -- criterion 7: the model is told what the user did --------------------------


async def test_the_next_tool_reply_names_what_the_user_did(wired, tmp_path):
    """Without this the model answers about a scene it did not produce."""
    await _load(wired, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    _record(wired, [])
    await _click(wired, "ghost-surface")
    told = await server_mod.list_selections()
    again = await server_mod.list_selections()

    assert "ghost-surface" in told["user_actions"]
    assert "user_actions" not in again, "one click was reported twice"


async def test_the_news_survives_a_tool_that_reaches_other_tools(wired, tmp_path):
    """The version above cannot fail, and this is the one that could.

    `list_selections` is a leaf: it calls no other tool, so the wrapper around
    it is the only one in play. Several tools are not leaves — `preset` reaches
    `hide` through `_take_the_scene` and `effects` through `_set_effects` — and
    an inner wrapper that drains puts the news into a reply its caller throws
    away. The model then hears nothing, which is worse than never recording it:
    the tools that nest are the presets, and a preset is what a click applies.

    Asserted through `preset` for that reason. Before the outermost-only guard
    this failed while the leaf test passed.
    """
    await _load(wired, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    _record(wired, [])
    await _click(wired, "ghost-surface")

    task = wired.serve(20)
    try:
        told = await server_mod.preset("publication-cartoon")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert "ghost-surface" in told["user_actions"]


async def test_a_second_click_does_not_swallow_the_first(wired, tmp_path):
    """A click runs `preset`, and `preset` is a tool whose reply can drain.

    That reply goes back to the page, where no model will ever read it, so a
    click that drained would delete the news of the click before it. Two
    clicks, one report, and the model would be told about half of what
    happened.
    """
    await _load(wired, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    _record(wired, [])
    await _click(wired, "ghost-surface", rid="click-1")
    await _click(wired, "ghost-surface", rid="click-2")

    assert len(server_mod._user_actions) == 2


async def test_a_reply_says_nothing_when_the_user_did_nothing(wired, tmp_path):
    """Otherwise every reply carries a sentence a model has to read past."""
    await _load(wired, _tiny_protein_pdb(tmp_path / "gly.pdb"))
    assert "user_actions" not in await server_mod.list_selections()


async def test_a_refused_click_is_not_reported_as_something_the_user_did(wired):
    """It changed nothing, so saying so would describe a scene that never was."""
    await _click(wired, "not-a-view")
    assert server_mod._user_actions == []


# -- the catalogue the menu is drawn from --------------------------------------


def test_a_view_needing_a_target_is_not_offered():
    """`active-site` needs a handle to point at, and a click has none to give."""
    assert "active-site" not in server_mod._PAGE_VIEWS


def test_the_catalogue_says_what_each_view_does_to_the_scene():
    """The kinds are load-bearing, not decoration.

    A `draws` view replaces the scene *and* brings its own styling, so picking
    one after a `styles` discards it. A menu that showed nine equal items would
    hide that, and the first surprise would be a chosen background vanishing.
    """
    catalogue = server_mod._page_view_catalogue()
    assert {entry["name"] for entry in catalogue} == set(server_mod._PAGE_VIEWS)

    kinds = {entry["name"]: entry["kind"] for entry in catalogue}
    assert kinds["putty"] == server_mod._VIEW_DRAWS
    assert kinds["cinematic"] == server_mod._VIEW_STYLES
    assert kinds["ghost-surface"] == server_mod._VIEW_LAYERS


async def test_the_page_is_told_what_it_may_ask_for(wired):
    """Sent on the handshake, so the page never keeps a list of its own."""
    bridge = server_mod._bridge
    assert bridge is not None  # the wired fixture installed it
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(f"ws://127.0.0.1:{bridge.port}/ws?token={bridge.token}")
    try:
        await ws.send_json({"action": "protean_ping", "version": 1})
        pong = json.loads((await asyncio.wait_for(ws.receive(), INVOKE_TIMEOUT)).data)
        assert pong["action"] == "protean_pong"
        offered = {entry["name"] for entry in pong["views"]}
        assert offered == set(server_mod._PAGE_VIEWS), pong
    finally:
        await ws.close()
        await session.close()
