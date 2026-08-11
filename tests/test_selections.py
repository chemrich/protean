"""Parser tests for protean's PyMOL selection grammar.

Pure Python; no browser. Semantic correctness against a real structure is
covered by the differential suite in test_selection_differential.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

from protean_mcp.selections import (
    COMPARABLE,
    KEYWORDS,
    PROPERTIES,
    And,
    Compare,
    Keyword,
    Modifier,
    Not,
    Or,
    Property,
    SelectionError,
    Within,
    parse,
)
from protean_mcp.selections_numpy import select_mask


@pytest.fixture
def tiny_structure() -> AtomArray[Any]:
    """Enough of a structure for every vocabulary name to resolve against.

    Deliberately mixed: a protein residue, a water and a metal ion, so a
    keyword cannot pass by matching everything or nothing on a uniform array.
    """

    def atom(
        chain: str,
        res_id: int,
        res_name: str,
        atom_name: str,
        element: str,
        hetero: bool,
        atom_id: int,
    ) -> Atom:
        return Atom(
            [float(atom_id), 0.0, 0.0],
            chain_id=chain,
            res_id=res_id,
            ins_code="",
            res_name=res_name,
            atom_name=atom_name,
            element=element,
            hetero=hetero,
            atom_id=atom_id,
            b_factor=10.0,
            occupancy=1.0,
        )

    return atom_array(
        [
            atom("A", 1, "ALA", "N", "N", False, 1),
            atom("A", 1, "ALA", "CA", "C", False, 2),
            atom("A", 1, "ALA", "CB", "C", False, 3),
            atom("A", 2, "HOH", "O", "O", True, 4),
            atom("A", 3, "ZN", "ZN", "ZN", True, 5),
        ]
    )


# -- tokenizing and parsing --------------------------------------------------


def test_simple_property():
    assert parse("chain A") == Property("chain", ("A",))


def test_value_list_and_ranges():
    assert parse("resi 50-60+70") == Property("resi", ("50-60", "70"))
    assert parse("name CA+CB") == Property("name", ("CA", "CB"))
    assert parse("resi 1,2,3") == Property("resi", ("1", "2", "3"))


def test_aliases_normalise():
    assert parse("c. A") == parse("chain A")
    assert parse("r. HEM") == parse("resn HEM")
    assert parse("bb.") == Keyword("backbone")
    assert parse("water") == Keyword("solvent")


def test_precedence_not_binds_tighter_than_and():
    # not chain A and polymer  ==  (not chain A) and polymer
    assert parse("not chain A and polymer") == And(
        Not(Property("chain", ("A",))), Keyword("polymer")
    )


def test_precedence_and_binds_tighter_than_or():
    # a or b and c  ==  a or (b and c)
    assert parse("solvent or polymer and chain A") == Or(
        Keyword("solvent"), And(Keyword("polymer"), Property("chain", ("A",)))
    )


def test_parentheses_override_precedence():
    assert parse("(solvent or polymer) and chain A") == And(
        Or(Keyword("solvent"), Keyword("polymer")), Property("chain", ("A",))
    )


def test_symbolic_operators():
    assert parse("chain A & polymer") == parse("chain A and polymer")
    assert parse("chain A | polymer") == parse("chain A or polymer")
    assert parse("!solvent") == parse("not solvent")


def test_comparisons():
    assert parse("b > 50") == Compare("b", ">", 50.0)
    assert parse("q<0.5") == Compare("q", "<", 0.5)


def test_within_parses_as_binary():
    node = parse("chain A within 5 of resn HEM")
    assert node == Within(Property("chain", ("A",)), 5.0, Property("resn", ("HEM",)))


def test_bare_within_is_implicitly_all():
    """PyMOL rejects this and Mol*'s transpiler answers 0; we read the intent."""
    assert parse("within 5 of resn HEM") == Within(
        Keyword("all"), 5.0, Property("resn", ("HEM",))
    )


def test_within_binds_tighter_than_and():
    node = parse("polymer and chain A within 5 of resn HEM")
    assert isinstance(node, And)
    assert isinstance(node.right, Within)


def test_modifier_applies_to_parenthesised_expression():
    node = parse("byres (chain A within 4 of chain B)")
    assert isinstance(node, Modifier) and node.kind == "byres"
    assert isinstance(node.child, Within)


def test_modifier_binds_tighter_than_and():
    """`byres X and Y` is `(byres X) and Y`, per PyMOL's precedence table.

    Mol*'s transpiler swallows the `and` across the parenthesis boundary and
    computes `byres (X and Y)` instead; see the differential suite.
    """
    node = parse("byres chain A and solvent")
    assert node == And(Modifier("byres", Property("chain", ("A",))), Keyword("solvent"))


def test_around_excludes_source():
    node = parse("resn HEM around 5")
    assert isinstance(node, Within) and node.exclude_self


# -- error handling ----------------------------------------------------------


@pytest.mark.parametrize(
    "selection",
    [
        "ss H",
        "bymolecule resi 50",
        "last chain A",
        "bound_to resn HEM",
        "resi 50-60 extend 2",
        "rank 1",
    ],
)
def test_unsupported_constructs_raise(selection):
    """The core contract: never answer an unsupported construct with silence."""
    with pytest.raises(SelectionError, match="not supported"):
        parse(selection)


def test_unknown_keyword_lists_alternatives():
    with pytest.raises(SelectionError, match="Unknown selection keyword"):
        parse("banana")


def test_unbalanced_parenthesis_raises():
    with pytest.raises(SelectionError, match="Unbalanced"):
        parse("(chain A")


def test_trailing_token_raises():
    with pytest.raises(SelectionError, match="trailing"):
        parse("chain A chain B")


def test_empty_selection_raises():
    with pytest.raises(SelectionError, match="Empty"):
        parse("   ")


def test_within_without_of_raises():
    with pytest.raises(SelectionError, match="Expected 'of'"):
        parse("chain A within 5 chain B")


def test_non_numeric_comparison_raises():
    with pytest.raises(SelectionError, match="Expected a number"):
        parse("b > high")


# -- distances have to be positive --------------------------------------------


@pytest.mark.parametrize(
    "selection",
    [
        "polymer within 0 of resn ZN",
        "polymer within -3 of resn ZN",
        "resn ZN around 0",
        "resn ZN around -3",
        "resn ZN expand 0",
        "resn ZN expand -3",
        "resn ZN expand nan",
        "resn ZN expand inf",
    ],
)
def test_a_non_positive_radius_is_refused(selection):
    """All three spatial operators, not just the one the backlog named.

    `within 0` and `within -3` answered with an empty set and `expand 0` with
    the source unchanged, both of which read as results rather than as the
    rejected questions they are. `nan` slips past a bare `<= 0`.
    """
    with pytest.raises(SelectionError, match="radius greater than 0"):
        parse(selection)


def test_the_refusal_names_the_operator_and_the_value():
    with pytest.raises(SelectionError, match="'expand' needs a radius greater than 0"):
        parse("resn ZN expand -3")
    with pytest.raises(SelectionError, match="got -3"):
        parse("resn ZN expand -3")


def test_a_positive_radius_still_parses():
    """Guards the tests above: refusing everything would satisfy them."""
    assert isinstance(parse("polymer within 5 of resn ZN"), Within)
    assert isinstance(parse("resn ZN around 0.5"), Within)
    assert parse("resn ZN expand 4") is not None


def test_a_comparison_may_still_be_zero_or_negative(tiny_structure):
    """The bound belongs to distances, not to every number in the grammar.

    A b-factor of zero is real data, and putting the check in the shared
    number parser would have refused it.
    """
    assert parse("b > 0") == Compare("b", ">", 0.0)
    assert parse("b > -5") == Compare("b", ">", -5.0)
    select_mask("b > 0", tiny_structure)


def test_non_integer_resi_raises(tiny_structure):
    """Caught when the value is used, since the grammar accepts any value list.

    It has to surface as a SelectionError: a bare ValueError out of int() would
    escape the tool layer's handling and reach the caller as a crash rather
    than an explanation.
    """
    with pytest.raises(SelectionError, match="integer, range, or insertion code"):
        select_mask("resi fifty", tiny_structure)


def test_non_integer_index_raises(tiny_structure):
    with pytest.raises(SelectionError, match="integer, range, or insertion code"):
        select_mask("index abc", tiny_structure)


def test_insertion_code_survives_parsing():
    """Antibody numbering: resi 100A is a distinct residue from resi 100.

    That it *resolves* to the inserted residue is asserted against real
    coordinates in test_selections_numpy.py.
    """
    assert parse("resi 100A") == Property("resi", ("100A",))


# -- vocabulary ---------------------------------------------------------------


@pytest.mark.parametrize("keyword", sorted(KEYWORDS))
def test_every_keyword_is_evaluable(keyword, tiny_structure):
    """The grammar's vocabulary and the evaluator's must not drift.

    A name accepted here but unknown to the evaluator would parse and then
    fail at use, which is the failure this table exists to prevent.
    """
    select_mask(keyword, tiny_structure)


# A value each property will accept. Most take free text, so a placeholder is
# fine; `resi` and `index` need a number, and `elem` is the one property whose
# vocabulary is closed, where a placeholder is now correctly refused.
_PROBE_VALUES = {"resi": "1", "index": "1", "elem": "C"}


@pytest.mark.parametrize("prop", sorted(PROPERTIES))
def test_every_property_is_evaluable(prop, tiny_structure):
    select_mask(f"{prop} {_PROBE_VALUES.get(prop, 'X')}", tiny_structure)


@pytest.mark.parametrize("prop", sorted(COMPARABLE))
def test_every_comparable_is_evaluable(prop, tiny_structure):
    select_mask(f"{prop} > 0", tiny_structure)
