"""Type a ligand's atoms by what they can do, from element and connectivity.

A pharmacophore is a claim about what a site *wants*: which atoms can donate a
hydrogen bond, which can accept one, which are greasy. It is not a list of
contacts. Mol\\*'s `interactions` extension computes interactions **between**
atoms, which is a different claim entirely and cannot type a ligand's atoms —
this document's plan assumed it could, twice, and was wrong both times.

**What this can and cannot know.** Most crystal structures carry no hydrogens,
so donor and acceptor cannot be *read*; they are inferred from element and
heavy-atom connectivity, which is the same rule a chemist applies by eye and
wrong in the same places. An oxygen with one heavy neighbour is treated as both
donor and acceptor because a hydroxyl is both; an oxygen with two is an ether
and accepts only. Nitrogen is treated as a donor unless it is bonded to three
heavy atoms, which usually means a tertiary amine or an aromatic nitrogen with
no hydrogen to give.

Every one of those is a rule of thumb. The typing is reported alongside the
picture so that it can be argued with, rather than presented as a measurement.
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["CLASS_COLOURS", "classify"]

# Deliberately few. A dozen feature classes is a taxonomy nobody reads off a
# picture; these four are the ones a person looks for.
CLASS_COLOURS: dict[str, str] = {
    "donor": "#4ec9c9",  # teal, as nitrogen is in the element palette
    "acceptor": "#c9a0dc",  # mauve, as oxygen is
    "both": "#8fb8e0",  # a hydroxyl does both, and reads as its own thing
    "hydrophobe": "#d3d3d3",  # light grey, as carbon is
}

_POLAR = frozenset({"N", "O"})
_GREASY = frozenset({"C", "S", "F", "CL", "BR", "I"})

# A nitrogen bonded to this many heavy atoms has no hydrogen left to donate.
_SATURATED_NITROGEN = 3
# An oxygen with more than one heavy neighbour is an ether or an ester.
_HYDROXYL_NEIGHBOURS = 1


def classify(array: Any, atoms: Any, bonds: Any) -> tuple[dict[int, str], dict[str, int]]:
    """Assign a feature class to each atom in *atoms*, and count the classes.

    array: the whole structure.
    atoms: indices to type — a ligand, normally.
    bonds: every bond as a pair of atom indices, from the selection engine.

    Returns the assignment by atom index, and how many of each class, so the
    reply can say what it decided rather than only draw it.
    """
    wanted = {int(i) for i in atoms}
    neighbours: dict[int, list[int]] = {i: [] for i in wanted}
    for pair in bonds:
        first, second = int(pair[0]), int(pair[1])
        if first in wanted:
            neighbours[first].append(second)
        if second in wanted:
            neighbours[second].append(first)

    elements = np.char.upper(np.asarray(array.element, dtype=str))
    assigned: dict[int, str] = {}
    for i in sorted(wanted):
        symbol = str(elements[i])
        heavy = [j for j in neighbours[i] if str(elements[j]) != "H"]
        if symbol == "O":
            assigned[i] = "both" if len(heavy) <= _HYDROXYL_NEIGHBOURS else "acceptor"
        elif symbol == "N":
            assigned[i] = "acceptor" if len(heavy) >= _SATURATED_NITROGEN else "donor"
        elif symbol in _GREASY:
            # A carbon next to a polar atom is not greasy in the way that
            # matters: it is part of a polar group and reads as one.
            near_polar = any(str(elements[j]) in _POLAR for j in heavy)
            if symbol == "C" and near_polar:
                continue
            assigned[i] = "hydrophobe"

    counts: dict[str, int] = {}
    for feature in assigned.values():
        counts[feature] = counts.get(feature, 0) + 1
    return assigned, counts
