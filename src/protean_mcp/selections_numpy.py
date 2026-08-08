"""Evaluate a parsed selection against coordinates, in Python.

The AST from :mod:`selections` is evaluated here into a boolean atom mask.
This is now the only engine: a selection cannot mean two different things
depending on whether it was drawn or analysed, because only one thing decides
what it means. The viewer is told the resulting atoms, not the query.

Evaluating in Python also means selections and analysis work with no browser
at all — headless, in CI, in a script.
"""

from __future__ import annotations

import io
import re
from typing import Any

import numpy as np
from biotite.structure import (
    AtomArray,
    CellList,
    filter_amino_acids,
    filter_carbohydrates,
    filter_monoatomic_ions,
    filter_nucleotides,
    filter_solvent,
)
from biotite.structure.io.pdb import PDBFile
from biotite.structure.io.pdbx import CIFFile, get_structure

from .selections import (
    And,
    Compare,
    Expand,
    Keyword,
    Modifier,
    Not,
    Or,
    Property,
    SelectionError,
    Within,
    parse,
)

Mask = np.ndarray[Any, Any]

# b-factor and occupancy are not parsed unless asked for, and a selection on a
# missing field would quietly match nothing.
EXTRA_FIELDS = ["b_factor", "occupancy", "atom_id"]

_METAL_SYMBOLS = (
    "LI BE NA MG AL K CA SC TI V CR MN FE CO NI CU ZN GA RB SR Y ZR NB MO TC "
    "RU RH PD AG CD IN SN CS BA LA HF TA W RE OS IR PT AU HG TL PB BI"
)
_METALS = frozenset(_METAL_SYMBOLS.split())
_BACKBONE = frozenset({"N", "CA", "C", "O"})
_HYDROGEN = frozenset({"H", "D"})


def load_structure(text: str, fmt: str) -> AtomArray[Any]:
    """Parse coordinates with the fields selections need."""
    handle = io.StringIO(text)
    if fmt not in ("pdb", "mmcif"):
        raise SelectionError(f"Unsupported format {fmt!r} (expected 'pdb' or 'mmcif')")
    try:
        if fmt == "pdb":
            array = PDBFile.read(handle).get_structure(model=1, extra_fields=EXTRA_FIELDS)
        else:
            array = get_structure(
                CIFFile.read(handle), model=1, extra_fields=EXTRA_FIELDS
            )
    except Exception as exc:
        raise SelectionError(f"Could not parse {fmt} coordinates: {exc}") from exc
    return array


def _residue_keys(array: AtomArray[Any]) -> Mask:
    """One integer per residue, so masks can be widened to residue granularity."""
    labels = np.char.add(
        np.char.add(array.chain_id.astype(str), "|"),
        np.char.add(array.res_id.astype(str), array.ins_code.astype(str)),
    )
    _, inverse = np.unique(labels, return_inverse=True)
    return np.asarray(inverse)


def _widen(mask: Mask, keys: Mask) -> Mask:
    """Every atom sharing a key with a selected atom."""
    return np.isin(keys, np.unique(keys[mask]))


def _field(array: AtomArray[Any], name: str) -> Mask:
    if name not in array.get_annotation_categories():
        raise SelectionError(
            f"This structure has no {name!r} field, so that selection cannot be "
            "evaluated here"
        )
    return np.asarray(array.get_annotation(name))


def _has_carbon(array: AtomArray[Any], mask: Mask) -> Mask:
    """Residues within *mask* that contain at least one carbon."""
    keys = _residue_keys(array)
    carbon = mask & (array.element == "C")
    return mask & _widen(carbon, keys)


def _keyword(name: str, array: AtomArray[Any]) -> Mask:  # noqa: PLR0911, PLR0912
    n = array.array_length()
    protein = np.asarray(filter_amino_acids(array))
    nucleic = np.asarray(filter_nucleotides(array))
    solvent = np.asarray(filter_solvent(array))
    hetero = np.asarray(array.hetero)
    polymer = protein | nucleic

    if name == "all":
        return np.ones(n, dtype=bool)
    if name == "none":
        return np.zeros(n, dtype=bool)
    if name == "polymer":
        return polymer
    if name == "protein":
        return protein
    if name == "nucleic":
        return nucleic
    if name == "solvent":
        return solvent
    if name == "hetatm":
        return hetero
    if name == "backbone":
        return polymer & np.isin(array.atom_name, list(_BACKBONE))
    if name == "sidechain":
        return polymer & ~np.isin(array.atom_name, list(_BACKBONE))
    if name == "hydro":
        return np.isin(array.element, list(_HYDROGEN))
    if name == "metals":
        return np.isin(np.char.upper(array.element.astype(str)), list(_METALS))
    if name == "glycan":
        return np.asarray(filter_carbohydrates(array))
    if name == "ion":
        return np.asarray(filter_monoatomic_ions(array))
    if name == "organic":
        return _has_carbon(array, hetero & ~solvent)
    if name == "inorganic":
        return hetero & ~solvent & ~_has_carbon(array, hetero & ~solvent)
    raise SelectionError(f"Keyword {name!r} is not supported by the Python evaluator")


def _property(node: Property, array: AtomArray[Any]) -> Mask:  # noqa: PLR0911
    prop = node.prop
    if prop == "chain":
        return np.isin(array.chain_id, list(node.values))
    if prop == "segi":
        # biotite carries no separate segment id; auth chain is the closest thing.
        return np.isin(array.chain_id, list(node.values))
    if prop == "resn":
        return np.isin(
            np.char.upper(array.res_name.astype(str)),
            [v.upper() for v in node.values],
        )
    if prop == "name":
        return np.isin(
            np.char.upper(array.atom_name.astype(str)),
            [v.upper() for v in node.values],
        )
    if prop == "elem":
        return np.isin(
            np.char.upper(array.element.astype(str)),
            [v.upper() for v in node.values],
        )
    if prop == "resi":
        return _numeric_terms(array.res_id, node.values, array, insertion=True)
    if prop == "index":
        return _numeric_terms(_field(array, "atom_id"), node.values, array)
    raise SelectionError(f"Property {prop!r} is not supported by the Python evaluator")


def _numeric_terms(
    values: Mask, terms: tuple[str, ...], array: AtomArray[Any], insertion: bool = False
) -> Mask:
    mask = np.zeros(array.array_length(), dtype=bool)
    numbers = np.asarray(values)
    for term in terms:
        span = re.fullmatch(r"(-?\d+)-(-?\d+)", term)
        if span:
            low, high = int(span.group(1)), int(span.group(2))
            mask |= (numbers >= low) & (numbers <= high)
            continue
        if insertion:
            coded = re.fullmatch(r"(-?\d+)([A-Za-z])", term)
            if coded:
                mask |= (numbers == int(coded.group(1))) & (
                    np.char.upper(array.ins_code.astype(str)) == coded.group(2).upper()
                )
                continue
        try:
            mask |= numbers == int(term)
        except ValueError:
            # Everything this package refuses has to arrive as a SelectionError;
            # a bare ValueError escapes the tool layer's error handling and
            # reaches the caller as a crash instead of an explanation.
            raise SelectionError(
                f"Expected an integer, range, or insertion code, got {term!r}"
            ) from None
    return mask


def _compare(node: Compare, array: AtomArray[Any]) -> Mask:
    field = {"b": "b_factor", "q": "occupancy"}[node.prop]
    values = _field(array, field)
    op = node.op
    if op == ">":
        return np.asarray(values > node.value)
    if op == "<":
        return np.asarray(values < node.value)
    if op == ">=":
        return np.asarray(values >= node.value)
    if op == "<=":
        return np.asarray(values <= node.value)
    if op == "=":
        return np.asarray(values == node.value)
    return np.asarray(values != node.value)


def _within(array: AtomArray[Any], source: Mask, radius: float) -> Mask:
    """Atoms within *radius* of any atom in *source*."""
    if not source.any():
        return np.zeros(array.array_length(), dtype=bool)
    cell_list = CellList(array[source], cell_size=max(radius, 1.0))
    neighbours = cell_list.get_atoms(array.coord, radius=radius)
    return np.asarray((neighbours >= 0).any(axis=-1))


def evaluate(node: object, array: AtomArray[Any]) -> Mask:  # noqa: PLR0911
    """Turn a parsed selection into a boolean atom mask."""
    if isinstance(node, Keyword):
        return _keyword(node.name, array)
    if isinstance(node, Property):
        return _property(node, array)
    if isinstance(node, Compare):
        return _compare(node, array)
    if isinstance(node, Not):
        return ~evaluate(node.child, array)
    if isinstance(node, And):
        return evaluate(node.left, array) & evaluate(node.right, array)
    if isinstance(node, Or):
        return evaluate(node.left, array) | evaluate(node.right, array)
    if isinstance(node, Modifier):
        inner = evaluate(node.child, array)
        if node.kind == "first":
            out = np.zeros(array.array_length(), dtype=bool)
            hits = np.flatnonzero(inner)
            if len(hits):
                out[hits[0]] = True
            return out
        # bychain widens over the *same* chain identifier that `chain` selects
        # on. The MolScript backend widens over Mol*'s chain key, which follows
        # label_asym_id, so there `chain A` and `bychain` disagree about what a
        # chain is; here they cannot.
        keys = _residue_keys(array) if node.kind == "byres" else array.chain_id
        return _widen(inner, np.asarray(keys))
    if isinstance(node, Within):
        near = _within(array, evaluate(node.target, array), node.radius)
        selected = near & evaluate(node.child, array)
        if node.exclude_self:
            selected = selected & ~evaluate(node.target, array)
        return selected
    if isinstance(node, Expand):
        inner = evaluate(node.child, array)
        return inner | _within(array, inner, node.radius)
    raise SelectionError(f"Cannot evaluate node: {node!r}")


def select_mask(selection: str, array: AtomArray[Any]) -> Mask:
    """Compile and evaluate a PyMOL-syntax selection against *array*."""
    return evaluate(parse(selection), array)
