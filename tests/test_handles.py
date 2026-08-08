"""Handles: named selections as values, and set operations over them."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from biotite.structure import Atom, AtomArray
from biotite.structure import array as atom_array

from protean_mcp.handles import (
    HandleError,
    HandleRegistry,
    combine,
    summarise,
    to_molscript,
)


def _atom(chain: str, res_id: int, res_name: str, atom_name: str, atom_id: int) -> Atom:
    return Atom(
        [float(atom_id), 0.0, 0.0],
        chain_id=chain,
        res_id=res_id,
        ins_code="",
        res_name=res_name,
        atom_name=atom_name,
        element="C",
        hetero=False,
        atom_id=atom_id,
    )


@pytest.fixture
def array() -> AtomArray[Any]:
    atoms = [
        _atom("A", 1, "ALA", "N", 1),
        _atom("A", 1, "ALA", "CA", 2),
        _atom("A", 2, "GLY", "CA", 3),
        _atom("B", 3, "SER", "CA", 4),
        _atom("B", 4, "VAL", "CA", 5),
    ]
    return atom_array(atoms)


@pytest.fixture
def registry() -> HandleRegistry:
    reg = HandleRegistry()
    reg.set("a", np.array([0, 1, 2]), "chain A")
    reg.set("b", np.array([3, 4]), "chain B")
    reg.set("overlap", np.array([2, 3]), "middle")
    return reg


def test_union_intersect_subtract(registry):
    assert list(combine(registry, "union", ["a", "b"])) == [0, 1, 2, 3, 4]
    assert list(combine(registry, "intersect", ["a", "overlap"])) == [2]
    assert list(combine(registry, "subtract", ["a", "overlap"])) == [0, 1]


def test_operations_fold_left_to_right(registry):
    registry.set("c", np.array([0]), "just the first")
    assert list(combine(registry, "subtract", ["a", "overlap", "c"])) == [1]


def test_unknown_operation_is_rejected_even_with_one_operand(registry):
    """The check must not live inside the fold: with a single operand the loop
    body never runs, and an unknown operation would pass silently."""
    with pytest.raises(HandleError, match="Unknown operation"):
        combine(registry, "frobnicate", ["a"])


def test_subtract_needs_two_operands(registry):
    with pytest.raises(HandleError, match="at least two"):
        combine(registry, "subtract", ["a"])


def test_unknown_handle_lists_the_known_ones(registry):
    with pytest.raises(HandleError, match="Known: a, b, overlap"):
        registry.get("nope")


def test_handles_deduplicate_and_sort():
    reg = HandleRegistry()
    handle = reg.set("x", np.array([5, 1, 5, 1, 3]), "messy")
    assert list(handle.indices) == [1, 3, 5]
    assert len(handle) == 3


def test_dropping_an_unknown_handle_raises(registry):
    registry.drop("a")
    with pytest.raises(HandleError):
        registry.drop("a")


# -- summarising --------------------------------------------------------------


def test_summarise_counts_atoms_and_residues(array):
    out = summarise(array, np.array([0, 1, 2]))
    assert out["atom_count"] == 3
    assert out["residue_count"] == 2
    assert out["chains"] == ["A"]


def test_summarise_caps_the_residue_list_but_not_the_count(array):
    out = summarise(array, np.array([0, 1, 2, 3, 4]), limit=1)
    assert out["residue_count"] == 4
    assert len(out["residues"]) == 1
    assert out["truncated"] is True


# -- rendering for the viewer -------------------------------------------------


def test_contiguous_atoms_become_a_single_range(array):
    script = to_molscript(array, np.array([0, 1, 2]))
    assert script.count("atom.id") == 2  # one >= and one <=
    assert ">= atom.id 1" in script and "<= atom.id 3" in script


def test_scattered_atoms_become_set_membership(array):
    script = to_molscript(array, np.array([0, 2, 4]))
    assert "set.has" in script
    assert "1 3 5" in script


def test_empty_selection_renders_as_empty(array):
    assert to_molscript(array, np.array([], dtype=int)) == "(sel.atom.empty)"


def test_mixed_runs_and_singletons_are_both_present(array):
    script = to_molscript(array, np.array([0, 1, 2, 4]))
    assert ">= atom.id 1" in script and "set.has" in script
    assert script.startswith("(sel.atom.atom-groups :atom-test (or ")
