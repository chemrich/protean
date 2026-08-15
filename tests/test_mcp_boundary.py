"""Tools called the way a client calls them, not the way Python does.

Every other test in this suite calls a tool as a plain function:
`await screenshot(path=...)`. A real client goes through `call_tool`, which
validates arguments, runs the tool and then *serialises the result* — and that
last step is where `screenshot` broke while every test stayed green.

The failure, verbatim:

    Unable to serialize unknown type:
    <class 'mcp.server.fastmcp.utilities.types.Image'>

FastMCP derives an output schema from the return annotation. `-> list[Any]`
gets one, so the reply is serialised as structured content, which an `Image`
cannot be. A bare `list` gets no schema and worked, which is why the pin
`mcp[cli]>=1.2.0,<2` could carry this in without a line of protean changing.

So these tests exist at the boundary rather than at the function, and they are
deliberately about *shape* — no viewer is needed to learn that a reply cannot
be encoded.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from protean_mcp import server

# Tools whose replies are not plain JSON, and therefore the ones whose
# serialisation is worth asserting. Everything else returns a dict or a str.
IMAGE_TOOLS = ["screenshot"]


class Bridge:
    """A viewer that answers a capture request with a one-pixel PNG."""

    viewer_connected = True
    #: The smallest thing Pillow will open, so `_open_snapshot` has real bytes.
    PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    async def request(self, action: str, args: Any = None, timeout: float = 0) -> Any:
        return {
            "data_uri": f"data:image/png;base64,{base64.b64encode(self.PNG).decode()}"
        }


@pytest.fixture
def viewer(monkeypatch, tmp_path):
    bridge = Bridge()
    monkeypatch.setattr(server, "_bridge", bridge)
    monkeypatch.setattr(server, "get_bridge", lambda: bridge)
    return bridge


@pytest.mark.parametrize("name", IMAGE_TOOLS)
async def test_an_image_returning_tool_declares_no_output_schema(name):
    """The condition that broke it, asserted where it is cheap to check.

    An output schema means FastMCP will try to encode the reply as structured
    content. For a tool that returns an image there is nothing to encode it
    *as*, so the schema must not be there.
    """
    tool = next(t for t in await server.mcp.list_tools() if t.name == name)
    assert tool.outputSchema is None, (
        f"{name} declares an output schema, so its reply will be serialised as "
        "structured content — which an Image cannot be"
    )


async def test_screenshot_survives_the_trip_through_call_tool(viewer, tmp_path):
    """The end-to-end claim: a client can actually call this.

    `call_tool` is what an MCP client reaches; the direct call every other test
    makes never touches the encoder.
    """
    result = await server.mcp.call_tool(
        "screenshot", {"path": str(tmp_path / "shot.png")}
    )
    content = result[0] if isinstance(result, tuple) else result
    kinds = [type(item).__name__ for item in content]

    assert "ImageContent" in kinds, f"no image came back, only {kinds}"
    assert "TextContent" in kinds, f"the saved path is missing, only {kinds}"
    assert (tmp_path / "shot.png").is_file()


async def test_every_tool_can_be_listed(monkeypatch):
    """A schema that cannot be built breaks discovery for the whole server.

    Cheap, and it fails loudly for the next tool that returns something the
    library cannot describe.
    """
    tools = await server.mcp.list_tools()
    assert len(tools) > 50
    assert all(tool.inputSchema for tool in tools)
