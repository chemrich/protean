"""Parser and emitter tests for the PyMOL → MolScript compiler.

Pure Python; no browser. Semantic correctness against a real structure is
covered by the differential suite in test_selection_differential.py.
"""

from __future__ import annotations

import pytest

from protean_mcp.selections import (
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
    to_molscript,
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


# -- emitting ----------------------------------------------------------------


def test_emit_chain():
    assert to_molscript("chain A") == (
        "(sel.atom.atom-groups :chain-test (= atom.auth_asym_id A))"
    )


def test_emit_set_membership():
    assert "set.has (set CA CB)" in to_molscript("name CA+CB")


def test_emit_integer_range():
    out = to_molscript("resi 50-60")
    assert "(>= atom.auth_seq_id 50)" in out and "(<= atom.auth_seq_id 60)" in out


def test_emit_negative_range():
    out = to_molscript("resi -5--1")
    assert "(>= atom.auth_seq_id -5)" in out and "(<= atom.auth_seq_id -1)" in out


def test_emit_and_uses_intersect_by_with_named_arg():
    """Mol* modifiers take positional arg 0 plus named args; two positionals
    silently evaluate to nothing."""
    out = to_molscript("chain A and name CA")
    assert out.startswith("(sel.atom.intersect-by ")
    assert " :by " in out


def test_emit_not_is_except_by_from_all():
    out = to_molscript("not chain A")
    assert out.startswith("(sel.atom.except-by (sel.atom.all) :by ")


def test_emit_or_uses_merge():
    assert to_molscript("chain A or chain B").startswith("(sel.atom.merge ")


def test_emit_byres_expands_residue_key():
    assert ":property (atom.key.res)" in to_molscript("byres name CA")


def test_emit_uses_backticks_for_awkward_values():
    """mol-script delimits strings with backticks; double quotes are accepted
    by the parser and then match nothing."""
    assert "`polypeptide(L)`" in to_molscript("protein")


def test_emit_leaves_nucleic_atom_names_bare():
    """Apostrophes tokenize fine bare; quoting C1' would match nothing."""
    assert "C1'" in to_molscript("name C1'")
    assert "`" not in to_molscript("name C1'")


def test_emit_bare_identifier_is_unquoted():
    assert "`" not in to_molscript("resn HEM")


def test_emit_expand_keeps_source():
    out = to_molscript("resn HEM expand 5")
    assert "include-surroundings" in out and ":as-whole-residues false" in out


def test_emit_around_subtracts_source():
    out = to_molscript("resn HEM around 5")
    assert out.startswith("(sel.atom.except-by ")


def test_emit_metals_is_not_empty():
    """Mol*'s transpiler ships `metals` as a description-only stub."""
    out = to_molscript("metals")
    assert "atom.atomic-number" in out and "26" in out


def test_emit_sidechain_is_polymer_minus_backbone():
    out = to_molscript("sidechain")
    assert out.startswith("(sel.atom.except-by ") and "label_atom_id" in out


def test_nested_expression_round_trips():
    out = to_molscript("byres (polymer within 4 of resn HEM) and not solvent")
    assert out.count("(") == out.count(")")


# -- error handling ----------------------------------------------------------


@pytest.mark.parametrize(
    "selection",
    ["ss H", "bymolecule resi 50", "last chain A", "bound_to resn HEM",
     "resi 50-60 extend 2", "rank 1"],
)
def test_unsupported_constructs_raise(selection):
    """The core contract: never answer an unsupported construct with silence."""
    with pytest.raises(SelectionError, match="not supported"):
        to_molscript(selection)


def test_unknown_keyword_lists_alternatives():
    with pytest.raises(SelectionError, match="Unknown selection keyword"):
        to_molscript("banana")


def test_unbalanced_parenthesis_raises():
    with pytest.raises(SelectionError, match="Unbalanced"):
        to_molscript("(chain A")


def test_trailing_token_raises():
    with pytest.raises(SelectionError, match="trailing"):
        to_molscript("chain A chain B")


def test_empty_selection_raises():
    with pytest.raises(SelectionError, match="Empty"):
        to_molscript("   ")


def test_within_without_of_raises():
    with pytest.raises(SelectionError, match="Expected 'of'"):
        to_molscript("chain A within 5 chain B")


def test_non_numeric_comparison_raises():
    with pytest.raises(SelectionError, match="Expected a number"):
        to_molscript("b > high")


def test_non_integer_resi_raises():
    with pytest.raises(SelectionError, match="integer, range, or insertion code"):
        to_molscript("resi fifty")


def test_insertion_code_compiles():
    """Antibody numbering: resi 100A is a distinct residue from resi 100."""
    out = to_molscript("resi 100A")
    assert "(= atom.auth_seq_id 100)" in out
    assert "(= atom.pdbx_PDB_ins_code A)" in out


def test_glycan_selects_branched_entities():
    """Glycans are mmCIF `branched` entities, not non-polymer."""
    assert "(= atom.entity-type branched)" in to_molscript("glycan")


def test_organic_spans_branched_and_non_polymer():
    out = to_molscript("organic")
    assert "non-polymer" in out and "branched" in out
