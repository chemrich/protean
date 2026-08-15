"""open_viewer must not hand the handshake token to the model by default.

From the code review of the handshake work. The token is what authenticates a
socket, and `_allowed_origin` allows an *absent* Origin so that non-browser
clients can connect at all — so anything holding the URL can drive the viewer
and answer for it. A tool reply is read by a model, kept in a transcript and
often written to a log, which makes a token in one a credential in all three.
"""

import webbrowser
from typing import Any

import pytest

from protean_mcp import server


class FakeBridge:
    """Enough bridge to answer open_viewer, recording what was launched."""

    def __init__(self, *, connected: bool) -> None:
        self.token = "SECRET-TOKEN-VALUE"
        self.port = 9878
        self.viewer_connected = connected
        self.opened: list[str] = []

    @property
    def viewer_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    @property
    def display_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    async def start(self) -> int:
        return self.port

    async def wait_for_viewer(self, timeout: float) -> None:
        self.viewer_connected = True

    async def request(self, action: str, args: Any = None, timeout: float = 0) -> Any:
        return {}


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    fake = FakeBridge(connected=False)
    monkeypatch.setattr(server, "get_bridge", lambda: fake)
    monkeypatch.setattr(server, "_static_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_visibility_note", lambda _: "")

    def record(url: str) -> bool:
        fake.opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", record)
    return fake


async def test_the_reply_does_not_carry_the_token(bridge):
    reply = await server.open_viewer(timeout=0)
    assert bridge.token not in reply
    assert "http://127.0.0.1:9878/" in reply


async def test_the_browser_still_gets_the_token(bridge):
    """Withholding it from the reply must not open a viewer that cannot connect."""
    await server.open_viewer(timeout=0)
    assert bridge.opened == [f"http://127.0.0.1:9878/?token={bridge.token}"]


async def test_reveal_url_hands_it_over_when_asked(bridge):
    """Deliberate is the point: a second browser or a forwarded port needs it."""
    reply = await server.open_viewer(timeout=0, reveal_url=True)
    assert bridge.token in reply


async def test_an_already_connected_viewer_is_reported_without_the_token(
    monkeypatch, tmp_path
):
    """The idempotent path returns an address too, and it was leaking the same one."""
    fake = FakeBridge(connected=True)
    monkeypatch.setattr(server, "get_bridge", lambda: fake)
    monkeypatch.setattr(server, "_static_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_visibility_note", lambda _: "")

    reply = await server.open_viewer(timeout=0)

    assert fake.token not in reply
    assert "already connected" in reply


async def test_the_unbuilt_viewer_message_does_not_leak_it_either(monkeypatch):
    """Three return paths, and all three used to carry the token."""
    fake = FakeBridge(connected=False)
    monkeypatch.setattr(server, "get_bridge", lambda: fake)
    monkeypatch.setattr(server, "_static_dir", lambda: None)

    reply = await server.open_viewer(timeout=0)

    assert fake.token not in reply
    assert "not built" in reply
