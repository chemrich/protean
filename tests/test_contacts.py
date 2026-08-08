"""Interface analysis: buried area, interface residues, contacts."""

from __future__ import annotations

from typing import Any

import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

from protean_mcp.analysis.contacts import ContactError, interface


def _atom(
    chain: str,
    res_id: int,
    res_name: str,
    atom_name: str,
    element: str,
    coord: list[float],
) -> Atom:
    return Atom(
        coord,
        chain_id=chain,
        res_id=res_id,
        res_name=res_name,
        atom_name=atom_name,
        element=element,
    )


@pytest.fixture
def salt_bridge_pair() -> AtomArray[Any]:
    """An aspartate carboxylate facing an arginine guanidinium, 3 A apart."""
    return atom_array(
        [
            _atom("A", 1, "ASP", "CA", "C", [0.0, 0.0, 0.0]),
            _atom("A", 1, "ASP", "CG", "C", [1.5, 0.0, 0.0]),
            _atom("A", 1, "ASP", "OD1", "O", [2.2, 1.0, 0.0]),
            _atom("A", 1, "ASP", "OD2", "O", [2.2, -1.0, 0.0]),
            _atom("B", 2, "ARG", "CA", "C", [8.0, 0.0, 0.0]),
            _atom("B", 2, "ARG", "CZ", "C", [6.5, 0.0, 0.0]),
            _atom("B", 2, "ARG", "NH1", "N", [5.2, 1.0, 0.0]),
            _atom("B", 2, "ARG", "NH2", "N", [5.2, -1.0, 0.0]),
        ]
    )


def test_salt_bridge_is_recognised(salt_bridge_pair):
    result = interface(salt_bridge_pair, "A", "B")
    kinds = {c.kind for c in result.contacts}
    assert "salt_bridge" in kinds


def test_contacts_are_sorted_by_distance(salt_bridge_pair):
    result = interface(salt_bridge_pair, "A", "B")
    distances = [c.distance for c in result.contacts]
    assert distances == sorted(distances)


def test_contact_endpoints_name_chain_residue_and_atom(salt_bridge_pair):
    payload = interface(salt_bridge_pair, "A", "B").as_dict()
    first = payload["contacts"][0]
    assert first["a"].startswith("A/ASP1/")
    assert first["b"].startswith("B/ARG2/")


def test_criterion_states_hydrogens_were_absent(salt_bridge_pair):
    payload = interface(salt_bridge_pair, "A", "B").as_dict()
    assert "no hydrogens" in payload["criterion"]


def test_distant_chains_share_no_interface():
    far = atom_array(
        [
            _atom("A", 1, "ALA", "CA", "C", [0.0, 0.0, 0.0]),
            _atom("B", 2, "ALA", "CA", "C", [80.0, 0.0, 0.0]),
        ]
    )
    result = interface(far, "A", "B")
    assert result.contacts == []
    assert result.buried_area == pytest.approx(0.0, abs=1e-6)


def test_same_chain_is_refused(salt_bridge_pair):
    with pytest.raises(ContactError, match="must differ"):
        interface(salt_bridge_pair, "A", "A")


def test_missing_chain_names_the_available_ones(salt_bridge_pair):
    with pytest.raises(ContactError, match="chains present: A, B"):
        interface(salt_bridge_pair, "A", "Z")


def test_water_is_left_out_of_contacts_by_default():
    with_water = atom_array(
        [
            _atom("A", 1, "SER", "OG", "O", [0.0, 0.0, 0.0]),
            _atom("A", 2, "HOH", "O", "O", [1.0, 0.0, 0.0]),
            _atom("B", 3, "HOH", "O", "O", [3.5, 0.0, 0.0]),
            _atom("B", 4, "SER", "OG", "O", [3.0, 0.0, 0.0]),
        ]
    )
    default = interface(with_water, "A", "B")
    assert all("HOH" not in (c.comp_a, c.comp_b) for c in default.contacts)

    included = interface(with_water, "A", "B", include_water=True)
    assert any("HOH" in (c.comp_a, c.comp_b) for c in included.contacts)


def test_buried_area_ignores_solvent_either_way():
    """biotite gives water no radius, so it cannot contribute buried area."""
    with_water = atom_array(
        [
            _atom("A", 1, "SER", "OG", "O", [0.0, 0.0, 0.0]),
            _atom("A", 2, "HOH", "O", "O", [1.0, 0.0, 0.0]),
            _atom("B", 4, "SER", "OG", "O", [3.0, 0.0, 0.0]),
        ]
    )
    a = interface(with_water, "A", "B").buried_area
    b = interface(with_water, "A", "B", include_water=True).buried_area
    assert a == pytest.approx(b)


def test_interface_residues_report_how_much_each_buries(salt_bridge_pair):
    payload = interface(salt_bridge_pair, "A", "B").as_dict()
    for side in ("interface_residues_a", "interface_residues_b"):
        for residue in payload[side]:
            assert set(residue) == {"chain", "seq", "comp", "buried"}
            assert residue["buried"] > 0


def test_contact_limit_is_respected(salt_bridge_pair):
    assert len(interface(salt_bridge_pair, "A", "B", contact_limit=1).contacts) == 1
