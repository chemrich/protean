"""Chemical typing, tested on molecules whose answer chemistry already gives.

Pure — element and connectivity in, feature classes out — so unlike the render
suite this can assert exactly. Which matters more here than usual: the picture
looks equally confident whichever rule fired, so the rules are what has to be
pinned.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom
from biotite.structure import array as atom_array

from protean_mcp.analysis.pharmacophore import (
    CLASS_COLOURS,
    UNCLASSIFIED,
    NoConnectivity,
    classify,
)


def _molecule(*atoms: tuple[str, str]) -> Any:
    """A residue from (atom name, element) pairs, laid out along a line."""
    return atom_array(
        [
            Atom(
                [float(i), 0.0, 0.0],
                chain_id="A",
                res_id=1,
                ins_code="",
                res_name="LIG",
                atom_name=name,
                element=element,
                hetero=True,
            )
            for i, (name, element) in enumerate(atoms)
        ]
    )


def _typed(molecule: Any, bonds: Any) -> tuple[dict[str, str], dict[str, int]]:
    assigned, counts = classify(molecule, range(molecule.array_length()), bonds)
    return {molecule.atom_name[i]: feature for i, feature in assigned.items()}, counts


def test_a_hydroxyl_both_donates_and_accepts():
    """One heavy neighbour means there is a hydrogen on it, whether or not the
    file carries one — which is the inference this whole module rests on."""
    ethanol = _molecule(("C1", "C"), ("C2", "C"), ("O1", "O"))
    typed, _ = _typed(ethanol, np.array([[0, 1], [1, 2]]))

    assert typed["O1"] == "both"


def test_an_ether_oxygen_only_accepts():
    """Two heavy neighbours leaves nothing to donate."""
    ether = _molecule(("C1", "C"), ("O1", "O"), ("C2", "C"))
    typed, _ = _typed(ether, np.array([[0, 1], [1, 2]]))

    assert typed["O1"] == "acceptor"


def test_a_tertiary_nitrogen_has_no_hydrogen_left_to_give():
    amine = _molecule(("N1", "N"), ("C1", "C"), ("C2", "C"), ("C3", "C"))
    typed, _ = _typed(amine, np.array([[0, 1], [0, 2], [0, 3]]))

    assert typed["N1"] == "acceptor"


def test_a_primary_nitrogen_donates():
    amine = _molecule(("N1", "N"), ("C1", "C"))
    typed, _ = _typed(amine, np.array([[0, 1]]))

    assert typed["N1"] == "donor"


def test_a_carbon_next_to_a_polar_atom_is_not_counted_as_greasy():
    """It is part of a polar group and reads as one. Calling every carbon
    hydrophobic would paint a sugar as a lump of grease."""
    methanol = _molecule(("C1", "C"), ("O1", "O"))
    typed, _ = _typed(methanol, np.array([[0, 1]]))

    assert "C1" not in typed
    assert typed["O1"] == "both"


def test_a_carbon_among_carbons_is_greasy():
    propane = _molecule(("C1", "C"), ("C2", "C"), ("C3", "C"))
    typed, counts = _typed(propane, np.array([[0, 1], [1, 2]]))

    assert set(typed.values()) == {"hydrophobe"}
    assert counts == {"hydrophobe": 3}


def test_every_class_it_can_assign_has_a_colour():
    """A class with no colour paints the unclassified grey and reads as a gap
    in the data rather than as a gap in the palette."""
    assignable = {"donor", "acceptor", "both", "hydrophobe"}

    assert assignable <= set(CLASS_COLOURS)
    for colour in CLASS_COLOURS.values():
        assert colour.startswith("#") and len(colour) == 7


def test_an_empty_selection_types_nothing_rather_than_guessing():
    nothing = _molecule(("C1", "C"))
    assigned, counts = classify(nothing, [], np.zeros((0, 2), dtype=int))

    assert assigned == {} and counts == {}


@pytest.mark.parametrize("halogen", ["F", "CL", "BR", "I"])
def test_halogens_count_as_greasy(halogen):
    """They are, and a fluorinated drug painted as "nothing here" would be
    missing the half of it that does the binding."""
    molecule = _molecule(("C1", "C"), ("X1", halogen))
    typed, _ = _typed(molecule, np.array([[0, 1]]))

    assert typed["X1"] == "hydrophobe"


def test_typing_refuses_when_there_is_no_connectivity_at_all():
    """`bond_pairs` falls back to residue templates, and a template lookup
    returns nothing for a name the dictionary does not know — every UNL and
    LIG, which is every docking pose and every novel compound.

    Typed from element alone, each of those comes back with every oxygen a
    hydroxyl and every carbon greasy, stated as confidently as a real answer.
    """
    unknown = _molecule(("C1", "C"), ("O1", "O"), ("N1", "N"))
    with pytest.raises(NoConnectivity):
        classify(unknown, range(3), np.zeros((0, 2), dtype=int))


def test_greasy_and_featureless_are_not_the_same_colour():
    """They started as two near-identical greys carrying opposite meanings. On
    a drug-like ligand that made the aromatic ring — the point of the picture —
    indistinguishable from the carbons flanking an amide."""
    assert CLASS_COLOURS["hydrophobe"] != CLASS_COLOURS[UNCLASSIFIED]
    assert UNCLASSIFIED in CLASS_COLOURS, "a caller cannot decode a colour it is not told"
