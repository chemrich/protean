"""A session file is untrusted input, and load_session has to treat it as such.

The attack these guard against was demonstrated against a live viewer on
2026-08-15: a .protean file whose embedded Mol* state tree names a URL makes
the browser fetch it and draw whatever comes back, while load_session returns
a normal-looking reply. Mol* applies the tree as given — it re-runs any
transform whose `version` differs from the one in the current state — so the
file's author, not the user, chooses what is on screen.
"""

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from protean_mcp import server
from protean_mcp.connection import ViewerError
from protean_mcp.server import (
    SESSION_FORMAT,
    SESSION_VERSION,
    _decompress_session,
    _remote_references,
    _unknown_transformers,
    load_session,
)


def write_session(path: Path, snapshot: dict[str, Any]) -> Path:
    document = {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "created": "2026-08-15T00:00:00+00:00",
        "handles": {},
        "molstar": snapshot,
    }
    path.write_bytes(gzip.compress(json.dumps(document).encode()))
    return path


def tree(*transforms: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"tree": {"transforms": list(transforms)}}}


def test_a_session_that_embeds_its_data_carries_no_references():
    """What save_session actually writes: raw-data, no URL anywhere."""
    snapshot = tree(
        {"transformer": "ms-plugin.raw-data", "params": {"data": "data_1UBQ\n"}},
        {"transformer": "ms-plugin.parse-cif", "params": {}},
    )
    assert _remote_references(snapshot) == []


def test_a_volume_url_with_a_newline_is_not_this_bridge():
    """Anchored with \\Z for the same reason the decoder pattern is."""
    snapshot = tree(
        {"transformer": "ms-plugin.download", "params": {"url": "/volumes/fixture\n"}}
    )
    assert len(_remote_references(snapshot)) == 1


def test_a_volume_route_is_this_bridge_and_is_allowed():
    """Measured from a real session: a loaded volume is a relative /volumes path.

    This is the reason the check cannot simply refuse every URL — sessions with
    a volume in them legitimately contain one.
    """
    snapshot = tree(
        {"transformer": "ms-plugin.download", "params": {"url": "/volumes/fixture"}}
    )
    assert _remote_references(snapshot) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example/beacon",
        "https://evil.example/beacon",
        "//evil.example/beacon",
        "file:///Users/someone/.ssh/id_rsa",
        "http://127.0.0.1:9878/volumes/fixture",  # absolute, so not necessarily us
        "/volumes/../../etc/passwd",
    ],
)
def test_a_url_pointing_anywhere_else_is_found(url):
    snapshot = tree({"transformer": "ms-plugin.download", "params": {"url": url}})
    assert _remote_references(snapshot) == [
        f"snapshot.data.tree.transforms[0].params.url = {url}"
    ]


def test_an_asset_url_is_found_through_its_wrapper():
    """Mol* also carries a URL as an Asset.Url, which nests it one deeper."""
    snapshot = tree(
        {
            "transformer": "ms-plugin.download",
            "params": {"url": {"kind": "url", "url": "http://evil.example/x"}},
        }
    )
    assert _remote_references(snapshot) == [
        "snapshot.data.tree.transforms[0].params.url.url = http://evil.example/x"
    ]


def test_the_mvs_uri_param_is_found_too():
    """The MVS extension names its URL `uri`; the prebuilt bundle registers it."""
    snapshot = tree({"transformer": "mvs-primitives", "params": {"uri": "http://x/y"}})
    assert _remote_references(snapshot) == [
        "snapshot.data.tree.transforms[0].params.uri = http://x/y"
    ]


def test_a_url_buried_in_a_list_is_found():
    """DownloadBlob takes a list of sources, so depth alone must not hide one."""
    snapshot = tree(
        {
            "transformer": "ms-plugin.download-blob",
            "params": {"sources": [{"id": "a", "url": "http://evil.example/z"}]},
        }
    )
    assert len(_remote_references(snapshot)) == 1


def test_a_bare_string_in_a_list_is_found():
    """A URL inside a list has no key, so a key-name check cannot see it.

    This was a live bypass of the first version of the guard, found by review.
    """
    assert _remote_references({"url": ["http://evil.example/x"]}) == [
        "snapshot.url[0] = http://evil.example/x"
    ]


def test_a_fetching_param_that_is_not_named_url_is_found():
    """`serverUrl` on create-volume-streaming-info is a PD.Text, not a PD.Url.

    So it is invisible to a grep for PD.Url — which is exactly how the first
    version of this guard was built, and exactly how it was bypassed. Mol*
    fetches from it all the same (volume-streaming/transformers.js:163).
    """
    snapshot = tree(
        {
            "transformer": "ms-plugin.create-volume-streaming-info",
            "params": {"serverUrl": "http://evil.example/ds"},
            "version": "new",
        }
    )
    assert _remote_references(snapshot) == [
        "snapshot.data.tree.transforms[0].params.serverUrl = http://evil.example/ds"
    ]


# Written out rather than read from _SESSION_DEFAULT_URLS: looping over the set
# under test passes when the set is empty, which is a test that cannot fail.
MOLSTAR_DEFAULTS = [
    "https://www.ebi.ac.uk/pdbe/api/validation/residuewise_outlier_summary/entry/",
    "https://files.rcsb.org/pub/pdb/validation_reports",
    "https://data.rcsb.org/graphql",
]


@pytest.mark.parametrize("url", MOLSTAR_DEFAULTS)
def test_molstars_own_serialised_defaults_are_allowed_by_exact_value(url):
    """A real session carries three third-party URLs it never asked for.

    Mol* serialises the defaults of the custom-property providers the prebuilt
    bundle registers. Measured from real sessions — so the guard cannot simply
    refuse every absolute URL, and cannot allow the *key* either, since those
    providers do fetch from whatever value is there.
    """
    allowed = tree(
        {
            "transformer": "ms-plugin.custom-structure-properties",
            "params": {"properties": {"p": {"serverUrl": url}}},
        }
    )
    assert _remote_references(allowed) == []


def test_the_default_list_is_the_one_that_was_measured():
    """Guards the parametrised test above against the set being emptied."""
    assert frozenset(MOLSTAR_DEFAULTS) == server._SESSION_DEFAULT_URLS


def test_a_default_url_swapped_for_another_host_is_found():
    swapped = tree(
        {
            "transformer": "ms-plugin.custom-structure-properties",
            "params": {
                "properties": {"p": {"serverUrl": "https://evil.example/graphql"}}
            },
        }
    )
    assert len(_remote_references(swapped)) == 1


def test_a_url_inside_a_longer_string_is_not_a_reference():
    """The match is anchored, so text that merely mentions a URL is not one.

    This is what keeps every real session loadable: an mmCIF header cites
    http://mmcif.pdb.org/... in audit_conform. A Mol* param that gets fetched
    holds the URL as its whole value, never embedded in prose.
    """
    snapshot = tree(
        {
            "transformer": "ms-plugin.raw-data",
            "params": {"data": "_audit_conform.dict_location http://mmcif.pdb.org/x.dic"},
        }
    )
    assert _remote_references(snapshot) == []


def test_embedded_file_content_is_skipped_even_when_it_starts_with_a_url():
    """`data` carries file bytes, which are parsed and never fetched.

    Anchoring alone does not cover this: a payload can begin with something
    URL-shaped, and refusing a session over the first line of its own
    structure file would be a false positive on a legitimate file.
    """
    snapshot = tree(
        {
            "transformer": "ms-plugin.raw-data",
            "params": {"data": "http://mmcif.pdb.org/x.dic is where this came from"},
        }
    )
    assert _remote_references(snapshot) == []
    # The same string anywhere else is a reference, which is what makes the
    # skip specific to embedded content rather than a hole.
    assert (
        len(_remote_references(tree({"params": {"url": "http://mmcif.pdb.org/x.dic"}})))
        == 1
    )


def test_a_transformer_protean_never_writes_is_refused():
    """The half of the check that does not depend on spotting a URL.

    create-volume-streaming-info fetches from Mol*'s own public default when
    the file names no URL at all, so there is nothing for the URL walk to find.
    """
    snapshot = tree(
        {"transformer": "ms-plugin.create-volume-streaming-info", "params": {}}
    )
    assert _remote_references(snapshot) == []  # nothing to see, by construction
    assert _unknown_transformers(snapshot) == ["ms-plugin.create-volume-streaming-info"]


def test_the_transformers_a_real_session_uses_are_all_allowed():
    """Measured by building a scene with every state-adding tool and saving it."""
    measured = [
        "build-in.root",
        "ms-plugin.raw-data",
        "ms-plugin.parse-cif",
        "ms-plugin.trajectory-from-mmcif",
        "ms-plugin.model-from-trajectory",
        "ms-plugin.custom-model-properties",
        "ms-plugin.structure-from-model",
        "ms-plugin.custom-structure-properties",
        "ms-plugin.structure-component",
        "ms-plugin.structure-representation-3d",
        "ms-plugin.model-unitcell-3d",
        "ms-plugin.create-group",
        "ms-plugin.structure-multi-selection-from-bundle",
        "ms-plugin.structure-selections-distance-3d",
        "ms-plugin.download",
        "ms-plugin.parse-ccp4",
        "ms-plugin.volume-from-ccp4",
        "ms-plugin.volume-representation-3d",
    ]
    snapshot = tree(*({"transformer": name} for name in measured))
    assert _unknown_transformers(snapshot) == []


@pytest.mark.parametrize(
    "name",
    [
        "ms-plugin.parse-dx",
        "ms-plugin.parse-dsn6",
        "ms-plugin.parse-cube",
        "ms-plugin.volume-from-dx",
        "ms-plugin.volume-from-density-server-cif",
        "ms-plugin.trajectory-from-pdb",
        "ms-plugin.trajectory-from-gro",
        "ms-plugin.trajectory-from-xyz",
    ],
)
def test_the_decoder_families_are_allowed_by_pattern(name):
    """Which decoder appears depends on the format the caller loaded.

    Safe as a family because none of these fetch: each consumes the object its
    parent produced. Note that is a claim about the *transforms*, not their
    files — model.js does fetch, in the two custom-property transforms, which
    are allowlisted by name and have their URLs pinned by value.

    gro and xyz are not reachable today: `fetch.py` maps only .pdb/.ent and
    .cif/.mmcif, and `dispatch.ts` collapses everything but 'pdb' to 'mmcif'.
    They are here because the family is admitted as a family, so the next
    format protean learns does not repeat the PDB regression below.
    """
    assert _unknown_transformers(tree({"transformer": name})) == []


@pytest.mark.parametrize(
    "name",
    ["ms-plugin.parse-cif\n", "ms-plugin.trajectory-from-pdb\n", "\nms-plugin.parse-dx"],
)
def test_a_decoder_name_with_a_newline_is_not_a_decoder(name):
    """`$` matches before a trailing newline; `\\Z` is what was meant.

    Not exploitable — Mol* has no transformer registered under that name, so
    the tree fails to apply — but it defeats the stated contract: a name
    save_session never writes would pass this check and surface as a raw Mol*
    error rather than protean's refusal naming the transformer.
    """
    assert _unknown_transformers(tree({"transformer": name})) == [name]


def test_a_session_from_a_pdb_file_is_not_refused():
    """The regression the pattern exists for, kept as its own case.

    A structure loaded from a .pdb reaches Mol* through `trajectory-from-pdb`
    where an mmCIF uses `trajectory-from-mmcif`, so naming the transformers one
    by one made protean refuse a session it had written itself seconds earlier
    — measured end to end against a live viewer. Everything in the census
    behind that list came from RCSB, and RCSB serves mmCIF.
    """
    snapshot = tree(
        {"transformer": "build-in.root"},
        {"transformer": "ms-plugin.raw-data", "params": {"data": "ATOM      1  N\n"}},
        {"transformer": "ms-plugin.trajectory-from-pdb"},
        {"transformer": "ms-plugin.model-from-trajectory"},
        {"transformer": "ms-plugin.structure-from-model"},
        {"transformer": "ms-plugin.structure-representation-3d"},
    )
    assert _unknown_transformers(snapshot) == []
    assert _remote_references(snapshot) == []


async def test_load_session_refuses_a_file_that_reaches_outside_itself(tmp_path):
    """The end-to-end refusal, and it must land before the viewer is called.

    No viewer is connected here, so if the guard did not run first this would
    fail with "No viewer connected" instead.
    """
    hostile = write_session(
        tmp_path / "hostile.protean",
        tree(
            {
                "transformer": "ms-plugin.download",
                "params": {"url": "http://evil.example/beacon"},
                "version": "any-new-version",
            }
        ),
    )
    with pytest.raises(ViewerError, match="fetch from somewhere else"):
        await load_session(str(hostile))


async def test_the_refusal_names_the_url_it_objected_to(tmp_path):
    """A refusal nobody can act on gets worked around rather than understood."""
    hostile = write_session(
        tmp_path / "hostile.protean",
        tree({"transformer": "ms-plugin.download", "params": {"url": "http://evil/x"}}),
    )
    with pytest.raises(ViewerError) as caught:
        await load_session(str(hostile))
    assert "http://evil/x" in str(caught.value)


def test_a_small_file_that_decompresses_enormously_is_refused(tmp_path, monkeypatch):
    """9 kB of gzip reaches 2 GB; gzip.decompress() would allocate all of it.

    The bound is shrunk rather than the payload grown: asserting the property
    against the real 512 MB bound costs over a GB of RSS per test to prove
    something about the comparison, not about the size.
    """
    monkeypatch.setattr(server, "_MAX_SESSION_BYTES", 4096)
    bomb = tmp_path / "bomb.protean"
    bomb.write_bytes(gzip.compress(b"A" * (64 * 1024)))
    assert bomb.stat().st_size < 1024  # small on disk, by construction
    with pytest.raises(ViewerError, match="decompresses to more than"):
        _decompress_session(bomb)


async def test_the_bomb_is_refused_through_load_session_too(tmp_path, monkeypatch):
    """The bound has to survive load_session's own except clause.

    ViewerError is a RuntimeError, so it passes through the (OSError,
    BadGzipFile, JSONDecodeError) handler around the parse rather than being
    reworded into "not a readable protean session".
    """
    monkeypatch.setattr(server, "_MAX_SESSION_BYTES", 4096)
    bomb = tmp_path / "bomb.protean"
    bomb.write_bytes(gzip.compress(b"A" * (64 * 1024)))
    with pytest.raises(ViewerError, match="decompresses to more than"):
        await load_session(str(bomb))


async def test_json_that_is_not_an_object_is_refused(tmp_path):
    """`[1, 2, 3]` reached .get() and raised a bare AttributeError."""
    odd = tmp_path / "odd.protean"
    odd.write_bytes(gzip.compress(b"[1, 2, 3]"))
    with pytest.raises(ViewerError, match="not a protean session"):
        await load_session(str(odd))


async def test_a_session_with_no_scene_is_refused(tmp_path):
    """Format and version say session; the scene is simply absent.

    This raised KeyError: 'molstar' from the call site, because the guard read
    it with .get() and the line after it did not.
    """
    empty = tmp_path / "empty.protean"
    empty.write_bytes(
        gzip.compress(
            json.dumps({"format": SESSION_FORMAT, "version": SESSION_VERSION}).encode()
        )
    )
    with pytest.raises(ViewerError, match="carries no scene"):
        await load_session(str(empty))


async def test_a_deeply_nested_file_is_refused_rather_than_traced(tmp_path):
    """155 bytes of brackets used to surface as an unhandled RecursionError."""
    deep = tmp_path / "deep.protean"
    body = '{"format": "protean-session", "version": 1, "handles": {}, "molstar": '
    deep.write_bytes(gzip.compress((body + "[" * 30000 + "]" * 30000 + "}").encode()))
    assert deep.stat().st_size < 1024
    with pytest.raises(ViewerError, match="nested too deeply"):
        await load_session(str(deep))


def test_an_ordinary_session_still_reads(tmp_path):
    """The bound must not be reachable by a real scene."""
    ordinary = tmp_path / "fine.protean"
    ordinary.write_bytes(gzip.compress(b'{"format": "protean-session"}'))
    assert json.loads(_decompress_session(ordinary))["format"] == "protean-session"
