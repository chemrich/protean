"""A writing tool must not quietly turn one kind of file into another.

Backlog 21, from the going-public security pass. Every tool that takes an
output path wrote wherever it was pointed, creating parent directories, with no
check at all. Demonstrated: `save_session` replaced a 21-byte JSON file with
32 kB of gzip, and `electrostatics(path=…)` — an *output* path that reads like
an input — wrote an OpenDX grid over a file named `secret.key`.

The rule is deliberately narrower than "never overwrite", because overwriting
is half of how these tools are used: capture a figure, adjust the scene,
capture it again over the same name. What is never intended is a write that
changes what a file *is*, which is the shape both demonstrations had.
"""

import base64
import inspect
from pathlib import Path
from typing import Any

import pytest

from protean_mcp import server
from protean_mcp.analysis import encode
from protean_mcp.connection import ViewerError
from protean_mcp.server import _writable

MOVIE_CONTAINERS: set[str] = encode.CONTAINERS


def test_writing_where_nothing_exists_is_fine(tmp_path):
    out = tmp_path / "new.png"
    assert _writable(out, (".png",), overwrite=False) == out


def test_replacing_a_file_of_the_same_kind_is_fine(tmp_path):
    """The iterate-on-a-figure workflow, which a blanket refusal would break."""
    out = tmp_path / "figure.png"
    out.write_bytes(b"old")
    assert _writable(out, (".png",), overwrite=False) == out


@pytest.mark.parametrize(
    ("name", "writes"),
    [
        ("secret.key", (".dx",)),  # electrostatics over a private key
        ("important.json", (".protean",)),  # save_session over a JSON file
        ("notes.md", (".png",)),  # a screenshot over prose
        ("id_rsa", (".png",)),  # no extension at all
    ],
)
def test_replacing_a_file_of_another_kind_is_refused(tmp_path, name, writes):
    victim = tmp_path / name
    victim.write_text("something the user wanted")
    with pytest.raises(ViewerError, match="already exists"):
        _writable(victim, writes, overwrite=False)
    assert victim.read_text() == "something the user wanted"


def test_the_refusal_says_what_would_have_happened(tmp_path):
    """A refusal nobody can act on gets worked around rather than understood."""
    victim = tmp_path / "secret.key"
    victim.write_text("x")
    with pytest.raises(ViewerError) as caught:
        _writable(victim, (".dx",), overwrite=False)
    assert "overwrite=True" in str(caught.value)
    assert ".dx" in str(caught.value)


def test_overwrite_means_overwrite(tmp_path):
    victim = tmp_path / "secret.key"
    victim.write_text("x")
    assert _writable(victim, (".dx",), overwrite=True) == victim


def test_a_directory_is_refused_whatever_the_flag_says(tmp_path):
    """There is no file to replace, and the write would fail later and worse."""
    folder = tmp_path / "somewhere"
    folder.mkdir()
    for overwrite in (False, True):
        with pytest.raises(ViewerError, match="is a directory"):
            _writable(folder, (".png",), overwrite=overwrite)


def test_the_extension_check_ignores_case(tmp_path):
    """A figure saved as .PNG is still a figure."""
    out = tmp_path / "figure.PNG"
    out.write_bytes(b"old")
    assert _writable(out, (".png",), overwrite=False) == out


WRITERS = ("snapshot", "screenshot", "save_session", "movie", "electrostatics")


def test_every_writing_tool_takes_the_flag():
    """A helper nothing calls is evidence the callers were missed.

    This repo has been caught by exactly that before: a shared conformer helper
    was written, and `contacts.py` went on open-coding its own.
    """
    for name in WRITERS:
        tool = getattr(server, name)
        signature = inspect.signature(getattr(tool, "fn", tool))
        assert "overwrite" in signature.parameters, f"{name} cannot refuse a clobber"


def test_every_writing_tool_actually_calls_the_guard():
    """Taking the argument is not the same as using it.

    Written because the signature test above passes with the guard deleted from
    the tool body — which is the same "helper nothing calls" shape it warns
    about, reproduced inside the test written to warn about it.
    """
    for name in WRITERS:
        tool = getattr(server, name)
        source = inspect.getsource(getattr(tool, "fn", tool))
        assert "_writable(" in source, f"{name} never consults the guard"
        assert "overwrite=overwrite" in source, f"{name} ignores its own flag"


async def test_save_session_refuses_to_clobber_an_unrelated_file(tmp_path, monkeypatch):
    """The demonstration from the pass: 21 bytes of JSON became 32 kB of gzip."""

    class Bridge:
        viewer_connected = True

        async def request(self, action: str, args: Any = None, timeout: float = 0) -> Any:
            return {"handles": {}, "snapshot": {"data": {"tree": {"transforms": []}}}}

    bridge = Bridge()
    monkeypatch.setattr(server, "_bridge", bridge)
    monkeypatch.setattr(server, "get_bridge", lambda: bridge)
    victim = tmp_path / "important.json"
    victim.write_text('{"real": "user data"}')

    with pytest.raises(ViewerError, match="already exists"):
        await server.save_session(str(victim))
    assert victim.read_text() == '{"real": "user data"}'

    await server.save_session(str(victim), overwrite=True)
    assert victim.read_bytes()[:2] == b"\x1f\x8b"  # gzip, so the flag works


async def test_screenshot_refuses_to_clobber_an_unrelated_file(tmp_path, monkeypatch):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()

    class Bridge:
        viewer_connected = True

        async def request(self, action: str, args: Any = None, timeout: float = 0) -> Any:
            return {"data_uri": f"data:image/png;base64,{png}"}

    bridge = Bridge()
    monkeypatch.setattr(server, "_bridge", bridge)
    monkeypatch.setattr(server, "get_bridge", lambda: bridge)
    victim = tmp_path / "secret.key"
    victim.write_text("a private key")

    with pytest.raises(ViewerError, match="already exists"):
        await server.screenshot(path=str(victim))
    assert victim.read_text() == "a private key"


def test_the_movie_containers_come_from_one_table():
    """Two copies of a table agreeing is evidence of the copy, not the fact.

    Asserted through the source because mypy's strict mode will not let a test
    reach a re-exported name: what matters is that `movie` consults the table
    encode.py validates against, rather than a second list of extensions that
    happens to match it today.
    """
    source = inspect.getsource(getattr(server.movie, "fn", server.movie))
    assert "MOVIE_CONTAINERS" in source
    assert '{".mp4"' not in source and '[".mp4"' not in source, (
        "the container list is declared a second time here"
    )
    assert {".mp4", ".gif", ".webm"} == encode.CONTAINERS


def test_the_movie_guard_can_actually_fire(tmp_path):
    """Written after the first version could not.

    It passed the target's own suffix as the allowed set, so the check compared
    a suffix with itself and admitted everything — a guard that reports success
    for every input, which is this codebase's oldest failure mode.
    """
    victim = tmp_path / "thesis.docx"
    victim.write_text("x")
    with pytest.raises(ViewerError, match="already exists"):
        _writable(victim, tuple(MOVIE_CONTAINERS), overwrite=False)

    fine = tmp_path / "turntable.mp4"
    fine.write_text("x")
    assert _writable(fine, tuple(MOVIE_CONTAINERS), overwrite=False) == fine


def test_a_relative_path_is_still_checked(tmp_path, monkeypatch):
    """The guard must not be defeated by not spelling the path out."""
    monkeypatch.chdir(tmp_path)
    Path("secret.key").write_text("x")
    with pytest.raises(ViewerError, match="already exists"):
        _writable(Path("secret.key"), (".dx",), overwrite=False)
