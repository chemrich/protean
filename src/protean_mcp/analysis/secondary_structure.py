"""Secondary structure by the Kabsch-Sander criterion, with helix types.

protean assigned secondary structure with biotite's P-SEA until this module
existed. P-SEA works off backbone geometry and has exactly two classes, alpha
and beta, so 3-10 and pi helices were not misclassified — they were invisible.
On 1UBQ that is four short segments and 50 atoms of the helix count.

This is the published DSSP algorithm (Kabsch & Sander 1983) implemented here
rather than shelled out to `mkdssp`, which is not in homebrew-core, ships no
wheel, and would make an optional binary the only route to an answer — the test
decision 8 applied to APBS. `mkdssp` is instead the *reference* this is
validated against, behind `PROTEAN_DSSP=1`.

What it assigns, using DSSP's own letters:

    H   alpha-helix          3.6 residues per turn, the common one
    G   3-10 helix           3 per turn, tighter, usually one or two turns
    I   pi-helix             4.4 per turn, rare, usually a bulge inside an H
    E   extended strand      a bridge that is part of a ladder
    B   isolated beta-bridge a ladder of length one
    T   hydrogen-bonded turn
    S   bend
    -   none of the above

`ss H` selects H, G and I together, which is what PyMOL's `ss H` means and what
a caller almost always wants. Each type is separately addressable as
`ss alpha`, `ss 3-10` and `ss pi`, so one can be selected and coloured alone.

**Cost.** 189 ms on 5FJI's 15,712 atoms, and memoised on content, so a
compound selection pays once. It was 559 ms and paid per `ss` node: the bridge
scan tested every residue pair, which on 5FJI was more time than the
hydrogen-bond search it depends on and grew quadratically from there.
Candidates now come out of the bond set instead. What remains is the
hydrogen-bond loop itself, which is linear in residues and vectorisable if it
ever matters.

**Not implemented: DSSP 4's polyproline II helix (`P`).** It is a 2021 addition
rather than part of the published algorithm, and it never overrides H, G, I, E
or B — it competes with T, S and blank. Residues DSSP 4 calls P therefore land
in T or `-` here, and the reference tests compare the structured classes.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from biotite.structure import AtomArray, CellList, filter_amino_acids

# Kabsch-Sander electrostatic hydrogen-bond energy. The partial charges are
# 0.42e on the carbonyl and 0.20e on the amide; 332 converts e^2/A to kcal/mol.
_Q1Q2F = 0.42 * 0.20 * 332.0

# "We find that an energy cutoff of -0.5 kcal/mol is a good compromise." Every
# rule below is expressed in terms of this predicate, so the cutoff is the one
# number that moves everything at once.
_HBOND_ENERGY_CUTOFF = -0.5

# Nothing bonds across this far, and an O(n^2) energy over every residue pair
# is unusable on an assembly. 9 A between CA atoms is the standard screen and
# is generous: a real bond has its O and N within about 3.5 A.
_NEIGHBOUR_RADIUS = 9.0

# A peptide bond is ~1.33 A. Beyond this the chain is broken, whatever the
# residue numbering says, and turn and bridge rules must not step across it.
_PEPTIDE_BOND_MAX = 2.5

# DSSP's bend criterion: the angle between the CA(i-2)->CA(i) and
# CA(i)->CA(i+2) virtual bonds.
_BEND_ANGLE = 70.0

# A residue and its immediate neighbour are held at a fixed distance by the
# peptide bond, so a "bond" between them measures the backbone, not a contact.
_MIN_BOND_SEPARATION = 2

# Two atoms closer than this are a modelling error rather than a strong bond,
# and 1/r would return an enormous negative energy and invent one.
_MIN_ATOM_SEPARATION = 0.5
_CLASHED_ENERGY = -9.9

# A beta-bulge takes an extra residue on one strand while the other runs
# straight past, so the two gaps are asymmetric. Symmetric limits would weld
# together strands that merely pass near each other.
_BULGE_LONG = 6
_BULGE_SHORT = 3

HELIX_CLASSES = ("H", "G", "I")
STRAND_CLASSES = ("E", "B")


class _Backbone:
    """The N, CA, C and O of every residue that has all four, in chain order.

    Residues without a complete backbone cannot donate or accept and cannot sit
    in a turn, so they are dropped here rather than special-cased in seven
    rules. `index` maps back onto the residue axis of the input.
    """

    def __init__(self, array: AtomArray[Any]) -> None:
        amino = filter_amino_acids(array)
        wanted = {"N": 0, "CA": 1, "C": 2, "O": 3}

        # Residue identity has to include the chain and the insertion code:
        # two chains routinely number from 1, and keying on res_id alone welds
        # them into one chain whose "consecutive" residues are far apart.
        keys: dict[tuple[Any, ...], int] = {}
        order: list[tuple[Any, ...]] = []
        for i in np.flatnonzero(amino):
            key = (
                array.chain_id[i],
                int(array.res_id[i]),
                str(array.ins_code[i]),
                str(array.res_name[i]),
            )
            if key not in keys:
                keys[key] = len(order)
                order.append(key)

        coords = np.full((len(order), 4, 3), np.nan, dtype=np.float64)
        for i in np.flatnonzero(amino):
            name = str(array.atom_name[i])
            slot = wanted.get(name)
            if slot is None:
                continue
            key = (
                array.chain_id[i],
                int(array.res_id[i]),
                str(array.ins_code[i]),
                str(array.res_name[i]),
            )
            # First occurrence wins: an unresolved altloc column would
            # otherwise let the last conformer silently define the geometry.
            if np.isnan(coords[keys[key], slot]).all():
                coords[keys[key], slot] = array.coord[i]

        complete = ~np.isnan(coords).any(axis=(1, 2))
        self.residue_keys = [k for k, ok in zip(order, complete, strict=True) if ok]
        self.coords = coords[complete]
        self.chain_id = np.array([k[0] for k in self.residue_keys])
        self.res_name = np.array([k[3] for k in self.residue_keys])
        self.n = len(self.residue_keys)

    @property
    def N(self) -> np.ndarray:
        return self.coords[:, 0]

    @property
    def CA(self) -> np.ndarray:
        return self.coords[:, 1]

    @property
    def C(self) -> np.ndarray:
        return self.coords[:, 2]

    @property
    def O(self) -> np.ndarray:  # noqa: E743 - the atom is called O
        return self.coords[:, 3]

    def connected(self) -> np.ndarray:
        """Whether residue i is peptide-bonded to residue i+1. Length n-1."""
        if self.n < _MIN_BOND_SEPARATION:
            return np.zeros(max(self.n - 1, 0), dtype=bool)
        gap = np.linalg.norm(self.N[1:] - self.C[:-1], axis=1)
        same_chain = self.chain_id[1:] == self.chain_id[:-1]
        return np.asarray((gap < _PEPTIDE_BOND_MAX) & same_chain)


def _amide_hydrogens(bb: _Backbone) -> np.ndarray:
    """Amide H positions, NaN where the residue cannot donate.

    Kabsch-Sander place H one angstrom from N along the preceding residue's
    C=O direction. A residue with no bonded predecessor has no such direction,
    and proline has no amide hydrogen at all — its nitrogen is in the ring.
    """
    hydrogens = np.full((bb.n, 3), np.nan, dtype=np.float64)
    if bb.n < _MIN_BOND_SEPARATION:
        return hydrogens
    linked = bb.connected()
    direction = bb.C[:-1] - bb.O[:-1]
    length = np.linalg.norm(direction, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        unit = direction / length
    placed = bb.N[1:] + unit
    hydrogens[1:][linked] = placed[linked]
    hydrogens[bb.res_name == "PRO"] = np.nan
    return hydrogens


def _hydrogen_bonds(bb: _Backbone, hydrogens: np.ndarray) -> set[tuple[int, int]]:
    """Pairs (i, j) where the C=O of residue i accepts the N-H of residue j.

    The direction matters and is easy to invert: every turn and bridge rule
    below reads as "the NH of one residue bonds to the CO of another", and
    swapping the two silently produces a plausible but mirrored assignment.
    """
    bonds: set[tuple[int, int]] = set()
    if bb.n < _MIN_BOND_SEPARATION:
        return bonds

    cells: CellList[Any] = CellList(bb.CA.astype(np.float32), _NEIGHBOUR_RADIUS)
    for j in range(bb.n):
        if np.isnan(hydrogens[j]).any():
            continue
        near = cells.get_atoms(bb.CA[j].astype(np.float32), _NEIGHBOUR_RADIUS)
        for neighbour in near[near >= 0]:
            i = int(neighbour)
            # A residue cannot bond to itself or to its own neighbour: the
            # geometry is fixed by the peptide bond, not by an interaction.
            if abs(i - j) < _MIN_BOND_SEPARATION:
                continue
            energy = _energy(bb.O[i], bb.C[i], hydrogens[j], bb.N[j])
            if energy < _HBOND_ENERGY_CUTOFF:
                bonds.add((i, j))
    return bonds


def _energy(o: np.ndarray, c: np.ndarray, h: np.ndarray, n: np.ndarray) -> float:
    r_on = float(np.linalg.norm(o - n))
    r_ch = float(np.linalg.norm(c - h))
    r_oh = float(np.linalg.norm(o - h))
    r_cn = float(np.linalg.norm(c - n))
    if min(r_on, r_ch, r_oh, r_cn) < _MIN_ATOM_SEPARATION:
        return _CLASHED_ENERGY
    return _Q1Q2F * (1.0 / r_on + 1.0 / r_ch - 1.0 / r_oh - 1.0 / r_cn)


def _turns(bonds: set[tuple[int, int]], n_residues: int) -> dict[int, np.ndarray]:
    """n-turn(i) for n in 3, 4, 5: the CO of i bonds to the NH of i+n."""
    return {
        n: np.array(
            [(i, i + n) in bonds for i in range(n_residues)],
            dtype=bool,
        )
        for n in (3, 4, 5)
    }


def _helices(
    turns: dict[int, np.ndarray], linked: np.ndarray, n_residues: int
) -> dict[str, np.ndarray]:
    """Helix classes from consecutive n-turns.

    Two consecutive n-turns start a helix of that type, and it covers the n
    residues from the later turn's origin. The run must not straddle a chain
    break: two peptides in contact can otherwise satisfy the bond pattern
    without anything being a helix.

    Where two helix types would claim the same residues, the *whole* minimal
    helix of the weaker type is discarded, not just its overlapping residues.
    Trimming instead leaves one- and two-residue stubs of G hanging off the
    ends of alpha-helices, which is what a first reading of the paper produces
    and is 13 wrong residues on 4HHB alone.

    Only G is suppressed this way. I and H are allowed to overlap and are
    separated afterwards by the summary priority, because discarding an
    alpha-helix that merely abuts a pi-helix loses 8 real H residues on 5FJI —
    the two are different readings of the same rule and only measurement tells
    them apart.
    """

    def minimal(n: int, blocked: np.ndarray | None) -> np.ndarray:
        mask = np.zeros(n_residues, dtype=bool)
        turn = turns[n]
        for i in range(1, n_residues):
            if not (turn[i - 1] and turn[i]):
                continue
            if not _contiguous(linked, i - 1, i + n):
                continue
            if blocked is not None and blocked[i : i + n].any():
                continue
            mask[i : i + n] = True
        return mask

    pi = minimal(5, None)
    alpha = minimal(4, None)
    return {"I": pi, "H": alpha, "G": minimal(3, pi | alpha)}


def _contiguous(linked: np.ndarray, start: int, stop: int) -> bool:
    """Whether residues start..stop are one unbroken peptide run."""
    if start < 0 or stop > len(linked):
        return False
    return bool(linked[start:stop].all())


def _bridge_candidates(
    bonds: set[tuple[int, int]], n_residues: int
) -> list[tuple[int, int]]:
    """Residue pairs that could possibly form a bridge.

    Every bridge rule is a conjunction of hydrogen bonds among
    {i-1, i, i+1} x {j-1, j, j+1}, so a pair with no bond in that neighbourhood
    cannot satisfy any of them. Reading the candidates out of `bonds` therefore
    loses nothing and turns an O(n_residues^2) scan into one linear in the
    number of hydrogen bonds — on 5FJI the pair scan was 0.334 s of a 0.565 s
    assignment, more than the bond search it depends on, and it grew
    quadratically from there.

    Each bond (a, b) is mapped back through every rule position it could
    occupy. Returned sorted, because `_ladders` chains bridges in the order
    they arrive.
    """
    candidates: set[tuple[int, int]] = set()
    for a, b in bonds:
        for i, j in (
            (a + 1, b),  # (i-1, j)
            (b - 1, a),  # (j,   i+1)
            (b, a + 1),  # (j-1, i)
            (a, b - 1),  # (i,   j+1)
            (a, b),  # (i,   j)
            (b, a),  # (j,   i)
            (a + 1, b - 1),  # (i-1, j+1)
            (b - 1, a + 1),  # (j-1, i+1)
        ):
            if 1 <= i <= n_residues - 2 and i + 3 <= j <= n_residues - 2:
                candidates.add((i, j))
    return sorted(candidates)


def _bridges(bonds: set[tuple[int, int]], n_residues: int) -> list[tuple[int, int, str]]:
    """Beta-bridges as (i, j, kind), kind being 'P' parallel or 'A' anti."""
    has = bonds.__contains__
    found: list[tuple[int, int, str]] = []
    for i, j in _bridge_candidates(bonds, n_residues):
        parallel = (has((i - 1, j)) and has((j, i + 1))) or (
            has((j - 1, i)) and has((i, j + 1))
        )
        anti = (has((i, j)) and has((j, i))) or (
            has((i - 1, j + 1)) and has((j - 1, i + 1))
        )
        if parallel:
            found.append((i, j, "P"))
        elif anti:
            found.append((i, j, "A"))
    return found


def _bulge_link(
    ladders: list[list[tuple[int, int, str]]],
) -> list[list[tuple[int, int]]]:
    """Merge ladders separated by a beta-bulge, and fill the bulge in.

    A bulge is an extra residue on one strand of a sheet where the other strand
    runs straight past. The two ladders either side are one strand pairing, so
    DSSP calls the whole thing E — including the bulge residues themselves,
    which sit in no bridge at all and would otherwise come out as loop.

    Returns one list of (strand_a, strand_b) residue spans per merged group.
    """
    spans: list[dict[str, Any]] = []
    for ladder in ladders:
        i_values = [i for i, _, _ in ladder]
        j_values = [j for _, j, _ in ladder]
        spans.append(
            {
                "i": (min(i_values), max(i_values)),
                "j": (min(j_values), max(j_values)),
                "kind": ladder[0][2],
                "size": len(ladder),
            }
        )

    parent = list(range(len(spans)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(spans)):
        for b in range(a + 1, len(spans)):
            if spans[a]["kind"] != spans[b]["kind"]:
                continue
            gap_i = _gap(spans[a]["i"], spans[b]["i"])
            gap_j = _gap(spans[a]["j"], spans[b]["j"])
            if gap_i is None or gap_j is None:
                continue
            if (gap_i < _BULGE_LONG and gap_j < _BULGE_SHORT) or (
                gap_i < _BULGE_SHORT and gap_j < _BULGE_LONG
            ):
                parent[find(a)] = find(b)

    groups: dict[int, list[int]] = {}
    for index in range(len(spans)):
        groups.setdefault(find(index), []).append(index)

    out: list[list[tuple[int, int]]] = []
    for members in groups.values():
        # A lone bridge that linked to nothing stays a lone bridge, and becomes
        # B rather than E. Merging is what promotes it.
        if len(members) == 1 and spans[members[0]]["size"] == 1:
            continue
        i_lo = min(spans[m]["i"][0] for m in members)
        i_hi = max(spans[m]["i"][1] for m in members)
        j_lo = min(spans[m]["j"][0] for m in members)
        j_hi = max(spans[m]["j"][1] for m in members)
        out.append([(i_lo, i_hi), (j_lo, j_hi)])
    return out


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int | None:
    """Residues between two spans on one strand, or None if they overlap."""
    if a[1] < b[0]:
        return b[0] - a[1]
    if b[1] < a[0]:
        return a[0] - b[1]
    return None


def _ladders(bridges: list[tuple[int, int, str]]) -> list[list[tuple[int, int, str]]]:
    """Bridges chained into ladders.

    A ladder continues when both partners step together: (i+1, j+1) for a
    parallel bridge, (i+1, j-1) for an antiparallel one. A ladder of one is an
    isolated bridge and becomes B rather than E.
    """
    by_start = {(i, j): (i, j, kind) for i, j, kind in bridges}
    used: set[tuple[int, int]] = set()
    out: list[list[tuple[int, int, str]]] = []
    for i, j, kind in bridges:
        if (i, j) in used:
            continue
        run = [(i, j, kind)]
        used.add((i, j))
        step = 1 if kind == "P" else -1
        a, b = i + 1, j + step
        while (a, b) in by_start and by_start[(a, b)][2] == kind and (a, b) not in used:
            run.append(by_start[(a, b)])
            used.add((a, b))
            a, b = a + 1, b + step
        out.append(run)
    return out


def _bends(bb: _Backbone, linked: np.ndarray) -> np.ndarray:
    """Bend where the chain turns by more than 70 degrees over four residues."""
    bend = np.zeros(bb.n, dtype=bool)
    for i in range(2, bb.n - 2):
        if not _contiguous(linked, i - 2, i + 2):
            continue
        before = bb.CA[i] - bb.CA[i - 2]
        after = bb.CA[i + 2] - bb.CA[i]
        norms = np.linalg.norm(before) * np.linalg.norm(after)
        if norms == 0:
            continue
        cosine = float(np.clip(np.dot(before, after) / norms, -1.0, 1.0))
        if np.degrees(np.arccos(cosine)) > _BEND_ANGLE:
            bend[i] = True
    return bend


def assign_per_residue(array: AtomArray[Any]) -> dict[tuple[Any, ...], str]:
    """DSSP class for every amino-acid residue with a complete backbone.

    Keyed by (chain_id, res_id, ins_code, res_name) so the caller can spread it
    back over atoms without assuming residue order survived anything.
    """
    bb = _Backbone(array)
    if bb.n == 0:
        return {}

    linked = bb.connected()
    hydrogens = _amide_hydrogens(bb)
    bonds = _hydrogen_bonds(bb, hydrogens)
    turns = _turns(bonds, bb.n)
    helix = _helices(turns, linked, bb.n)

    ladders = _ladders(_bridges(bonds, bb.n))
    strand = np.zeros(bb.n, dtype=bool)
    for (i_lo, i_hi), (j_lo, j_hi) in _bulge_link(ladders):
        strand[i_lo : i_hi + 1] = True
        strand[j_lo : j_hi + 1] = True

    # Whatever a bulge-linked group did not claim and sits in a lone bridge is
    # B. Computed as a remainder rather than tracked separately, so a residue
    # cannot end up in both.
    bridge = np.zeros(bb.n, dtype=bool)
    for ladder in ladders:
        for i, j, _ in ladder:
            bridge[i] = True
            bridge[j] = True
    bridge &= ~strand

    # T covers the residues *inside* an n-turn, not its two bonded ends. Marking
    # the endpoints too over-assigns T by about 2 residues per turn, and because
    # T outranks S it also swallows most of the bends.
    turn = np.zeros(bb.n, dtype=bool)
    for n, flags in turns.items():
        for index in np.flatnonzero(flags):
            start = int(index)
            if _contiguous(linked, start, start + n):
                turn[start + 1 : start + n] = True

    bend = _bends(bb, linked)

    # DSSP's summary column takes the first class that applies, in this order.
    #
    # **I outranks H**, which is not what the 1983 paper says and is not what a
    # reading of it produces. DSSP 4 reversed the two, so a pi-helix is reported
    # as I where the older ordering reported the same residues as H. Measured
    # rather than assumed: on 4HHB the reversal moves exactly 15 residues, and
    # taking H first gives 394 H / 7 I against mkdssp's 379 / 22.
    priority = (
        ("I", helix["I"]),
        ("H", helix["H"]),
        ("B", bridge),
        ("E", strand),
        ("G", helix["G"]),
        ("T", turn),
        ("S", bend),
    )
    out: dict[tuple[Any, ...], str] = {}
    for position, key in enumerate(bb.residue_keys):
        code = "-"
        for letter, flags in priority:
            if flags[position]:
                code = letter
                break
        out[key] = code
    return out


def _fingerprint(array: AtomArray[Any]) -> tuple[Any, ...]:
    """A content key for the cache: every input the assignment reads.

    Deliberately not `id(array)`. Arrays are rebuilt and reused freely here,
    and a trajectory sets coordinates in place on the same object, so identity
    would serve a previous frame's helices for the current frame's geometry —
    a wrong answer that looks like a fast one. Hashing costs 0.31 ms on 15,712
    atoms against a 189 ms assignment.
    """
    return (
        array.coord.tobytes(),
        array.res_id.tobytes(),
        array.chain_id.tobytes(),
        array.ins_code.tobytes(),
        array.res_name.tobytes(),
        array.atom_name.tobytes(),
        array.sym_id.tobytes() if "sym_id" in array.get_annotation_categories() else b"",
    )


# One structure is selected against many times in a row, so a single live entry
# is most of the win; a few guard against alternating between two structures.
# Bounded because each value is one byte per atom and the keys hold the
# coordinates they were made from.
_CACHE_ENTRIES = 4
_cache: dict[tuple[Any, ...], np.ndarray] = {}


def assign(array: AtomArray[Any]) -> np.ndarray:
    """Per-atom DSSP class, `-` for anything without one.

    Per-atom rather than per-residue because that is what a selection needs and
    because spreading it here keeps the residue keying in one place.

    Memoised on content: `ss` re-derives the whole assignment per node, so
    `ss H or ss S` used to pay for it twice and a compound selection more.
    """
    key = _fingerprint(array)
    cached = _cache.get(key)
    if cached is not None:
        return cached.copy()

    codes = _assign_uncached(array)
    if len(_cache) >= _CACHE_ENTRIES:
        _cache.pop(next(iter(_cache)))
    _cache[key] = codes
    return codes.copy()


def _assign_uncached(array: AtomArray[Any]) -> np.ndarray:
    per_residue = assign_per_residue(array)
    # Empty, not "-": a water has no secondary structure rather than an
    # unassigned one, and `ss L` must not select the solvent.
    codes = np.full(array.array_length(), "", dtype="<U1")
    if not per_residue:
        return codes
    amino = filter_amino_acids(array)
    for i in np.flatnonzero(amino):
        key = (
            array.chain_id[i],
            int(array.res_id[i]),
            str(array.ins_code[i]),
            str(array.res_name[i]),
        )
        code = per_residue.get(key)
        if code is not None:
            codes[i] = code
    return codes
