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
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

import numpy as np
from biotite.structure import (
    AtomArray,
    CellList,
    connect_via_residue_names,
    filter_amino_acids,
    filter_carbohydrates,
    filter_monoatomic_ions,
    filter_nucleotides,
    filter_solvent,
)
from biotite.structure.io.pdb import PDBFile
from biotite.structure.io.pdbx import CIFFile, get_assembly, get_structure

from .analysis.secondary_structure import assign as assign_secondary_structure
from .selections import (
    And,
    Compare,
    Expand,
    Extend,
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
_HYDROGEN = frozenset({"H", "D"})

# N/CA/C/O, plus the second oxygen of the C-terminal carboxylate. OXT is main
# chain by any chemical reading — it hangs off the same carbonyl carbon as O —
# and leaving it out put it in `sidechain`, where it is certainly not.
_PROTEIN_BACKBONE = frozenset({"N", "CA", "C", "O", "OXT"})

# The sugar-phosphate backbone: phosphate, then the whole ribose ring. The
# sugar belongs to the backbone under the name everyone uses for it, which
# leaves `sidechain` meaning the nucleobase — the variable part that decides
# which residue this is, exactly as a protein sidechain does.
#
# Legacy spellings are included because they still arrive: O1P/O2P/O3P are the
# pre-2007 phosphate oxygens, and primes were written as asterisks before the
# PDB remediation.
_NUCLEIC_BACKBONE = frozenset(
    {"P", "OP1", "OP2", "OP3", "O1P", "O2P", "O3P"}
    | {
        f"{atom}{prime}"
        for atom in ("O5", "C5", "C4", "O4", "C3", "O3", "C2", "O2", "C1")
        for prime in ("'", "*")
    }
)
# Elements 1-118, plus the hydrogen isotopes that carry their own symbol in
# neutron structures. Unlike a chain id or a residue number this is a closed
# set, so a symbol outside it is a typo rather than a question about a molecule.
_ELEMENT_SYMBOLS = (
    "H HE LI BE B C N O F NE NA MG AL SI P S CL AR K CA SC TI V CR MN FE CO NI "
    "CU ZN GA GE AS SE BR KR RB SR Y ZR NB MO TC RU RH PD AG CD IN SN SB TE I "
    "XE CS BA LA CE PR ND PM SM EU GD TB DY HO ER TM YB LU HF TA W RE OS IR PT "
    "AU HG TL PB BI PO AT RN FR RA AC TH PA U NP PU AM CM BK CF ES FM MD NO LR "
    "RF DB SG BH HS MT DS RG CN NH FL MC LV TS OG D T"
)
_ELEMENTS = frozenset(_ELEMENT_SYMBOLS.split())


@dataclass
class LoadedStructure:
    """A parsed structure and what it actually contains.

    ``copies`` is how many symmetry copies of the deposited coordinates are
    present. It is reported rather than inferred because it is the number that
    decides whether an atom count here means the same thing as an atom count
    in the viewer.

    ``altloc_surplus`` is how many atoms the viewer holds that this array does
    not, purely because biotite resolves alternate conformers at parse time and
    Mol\\* draws all of them. It is a knowable difference rather than evidence
    the two are holding different molecules, so it is measured and subtracted
    before anything is called a mismatch.
    """

    array: AtomArray[Any]
    assembly: str
    copies: int
    note: str = ""
    altloc_surplus: int = 0


# A whole viral capsid is 60 copies of its asymmetric unit; expanding one
# unasked would turn a routine load into gigabytes.
MAX_ASSEMBLY_COPIES = 12


def assembly_multiplicity(text: str) -> int:
    """How many copies assembly 1 would make, read without building it."""
    try:
        block = CIFFile.read(io.StringIO(text)).block
        return len(block["pdbx_struct_oper_list"]["id"].as_array())
    except Exception:
        return 1


# The columns that, together, name one physical atom site. Two rows agreeing on
# all of them are alternate conformers of the same atom, not two atoms. Both
# sequence numberings are included because label_seq_id is "." for anything that
# is not a polymer residue, which would fold every het atom of a chain together.
_SITE_COLUMNS = (
    "label_asym_id",
    "label_seq_id",
    "auth_seq_id",
    "label_comp_id",
    "label_atom_id",
    "pdbx_PDB_ins_code",
)


def altloc_surplus(text: str, fmt: str) -> int:
    """How many atoms the viewer will hold that the analysis will not.

    biotite keeps one conformer per atom site; Mol\\* draws every conformer in
    the file. On a structure with alternate conformations the two therefore
    disagree about the atom count while describing the same molecule — 217
    atoms apart on 5FJI, which read as a mismatch for a whole phase.

    Counted from the file rather than by differencing the two builders, because
    a difference that explains itself explains any bug along with it.
    """
    if fmt == "pdb":
        return _pdb_altloc_surplus(text)
    try:
        site = CIFFile.read(io.StringIO(text)).block["atom_site"]
    except Exception:
        return 0
    columns = set(site.keys())
    if "label_alt_id" not in columns:
        return 0

    rows = np.ones(len(site["label_alt_id"].as_array()), dtype=bool)
    if "pdbx_PDB_model_num" in columns:
        models = site["pdbx_PDB_model_num"].as_array()
        rows = models == models[0]

    alternate = rows & (site["label_alt_id"].as_array() != ".")
    if not alternate.any():
        return 0
    keys = [
        site[name].as_array()[alternate].tolist()
        for name in _SITE_COLUMNS
        if name in columns
    ]
    return int(alternate.sum()) - len(set(zip(*keys, strict=True)))


# The insertion code is the last column a site identity reads, at index 26, so
# a record shorter than this cannot be one.
_PDB_SITE_WIDTH = 27


def _pdb_altloc_surplus(text: str) -> int:
    """The same count over fixed-column PDB records.

    Only the first model is counted, matching what ``get_structure`` parses.
    """
    sites: set[tuple[str, ...]] = set()
    alternate = 0
    for line in text.splitlines():
        if line.startswith("ENDMDL"):
            break
        if not line.startswith(("ATOM", "HETATM")) or len(line) < _PDB_SITE_WIDTH:
            continue
        if line[16] == " ":
            continue
        alternate += 1
        # name, resName, chainID, resSeq, iCode — every field but altLoc.
        sites.add((line[12:16], line[17:20], line[21], line[22:26], line[26]))
    return alternate - len(sites)


def load_structure(text: str, fmt: str, assembly: str = "biological") -> LoadedStructure:
    """Parse coordinates with the fields selections need.

    ``assembly`` chooses what "the structure" means. The biological assembly is
    the molecule as it exists — haemoglobin is a tetramer — and is what Mol\\*
    renders by default, so it is the default here too: analysis that describes
    something other than what is on screen is worse than useless. "asymmetric"
    gives the deposited coordinates instead.

    The two must be chosen together. Loading one here and the other in the
    viewer means every atom count in a result refers to a different molecule
    than the picture, which is invisible until someone compares them.
    """
    if assembly not in ("biological", "asymmetric"):
        raise SelectionError(f"Unknown assembly {assembly!r} (biological, asymmetric)")
    if fmt not in ("pdb", "mmcif"):
        raise SelectionError(f"Unsupported format {fmt!r} (expected 'pdb' or 'mmcif')")

    if fmt == "pdb":
        try:
            array = PDBFile.read(io.StringIO(text)).get_structure(
                model=1, extra_fields=EXTRA_FIELDS
            )
        except Exception as exc:
            raise SelectionError(f"Could not parse pdb coordinates: {exc}") from exc
        note = ""
        if assembly == "biological":
            # PDB carries assemblies in REMARK 350, which biotite does not read.
            note = (
                "PDB input carries its assembly in REMARK 350, which is not "
                "parsed; the asymmetric unit was loaded instead"
            )
        return LoadedStructure(
            array=array,
            assembly="asymmetric",
            copies=1,
            note=note,
            altloc_surplus=altloc_surplus(text, fmt),
        )

    surplus = altloc_surplus(text, fmt)
    try:
        handle = CIFFile.read(io.StringIO(text))
        if assembly == "asymmetric":
            array = get_structure(handle, model=1, extra_fields=EXTRA_FIELDS)
            return LoadedStructure(
                array=array, assembly="asymmetric", copies=1, altloc_surplus=surplus
            )

        copies = assembly_multiplicity(text)
        if copies > MAX_ASSEMBLY_COPIES:
            array = get_structure(handle, model=1, extra_fields=EXTRA_FIELDS)
            return LoadedStructure(
                array=array,
                assembly="asymmetric",
                copies=1,
                note=(
                    f"the biological assembly is {copies} copies, over the "
                    f"limit of {MAX_ASSEMBLY_COPIES}; the asymmetric unit was "
                    "loaded instead and the viewer will show more than this"
                ),
                altloc_surplus=surplus,
            )
        array = get_assembly(handle, model=1, extra_fields=EXTRA_FIELDS)
    except SelectionError:
        raise
    except Exception as exc:
        raise SelectionError(f"Could not parse mmcif coordinates: {exc}") from exc

    present = int(np.unique(np.asarray(array.sym_id)).size) if _has_sym(array) else 1
    # Every copy carries the same alternate conformers, so the excess scales
    # with the expansion the same way the atom count does.
    return LoadedStructure(
        array=array,
        assembly="biological",
        copies=present,
        altloc_surplus=surplus * present,
    )


def _has_sym(array: AtomArray[Any]) -> bool:
    return "sym_id" in array.get_annotation_categories()


def residue_labels(array: AtomArray[Any]) -> Any:
    """A string per atom identifying its residue uniquely.

    Symmetry copies share chain ids and residue numbers, so in an assembly the
    copy has to be part of the identity. Leaving it out merges two physically
    distinct residues into one, which silently sums their buried area and
    halves their count.
    """
    labels = np.char.add(
        np.char.add(array.chain_id.astype(str), "|"),
        np.char.add(array.res_id.astype(str), array.ins_code.astype(str)),
    )
    if _has_sym(array):
        labels = np.char.add(
            np.char.add(labels, "#"), np.asarray(array.sym_id).astype(str)
        )
    return labels


def _residue_keys(array: AtomArray[Any]) -> Mask:
    """One integer per residue, so masks can be widened to residue granularity."""
    _, inverse = np.unique(residue_labels(array), return_inverse=True)
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


def _backbone(array: AtomArray[Any], protein: Mask, nucleic: Mask) -> Mask:
    """Backbone atoms of either polymer, by the names each one uses.

    Kept as one mask rather than two keywords because a structure can hold
    both, and asking for "the backbone" of a protein-DNA complex should not
    make the caller name which half they meant.
    """
    names = array.atom_name
    return (protein & np.isin(names, list(_PROTEIN_BACKBONE))) | (
        nucleic & np.isin(names, list(_NUCLEIC_BACKBONE))
    )


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
        return _backbone(array, protein, nucleic)
    if name == "sidechain":
        # Whatever the polymer is, sidechain is the rest of it. That only
        # answers the right question once backbone knows about both polymers:
        # while it was protein-only, `sidechain` on DNA returned every atom of
        # the molecule and looked like a real answer.
        return polymer & ~_backbone(array, protein, nucleic)
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
        present = np.char.upper(array.element.astype(str))
        wanted = [v.upper() for v in node.values]
        _check_elements(wanted, present)
        return np.isin(present, wanted)
        return np.isin(
            np.char.upper(array.element.astype(str)),
            [v.upper() for v in node.values],
        )
    if prop == "ss":
        return _secondary_structure(array, node.values)
    if prop == "resi":
        return _numeric_terms(array.res_id, node.values, array, insertion=True)
    if prop == "index":
        return _numeric_terms(_field(array, "atom_id"), node.values, array)
    if prop == "rank":
        # PyMOL's rank is the atom's 0-based position within the object, which
        # here is its position in the array. Distinct from `index`, which is
        # the file's own atom_site id and need not start at 0 or be contiguous.
        _check_rank_is_transportable(array)
        return _numeric_terms(np.arange(array.array_length()), node.values, array)
    raise SelectionError(f"Property {prop!r} is not supported by the Python evaluator")


def _check_rank_is_transportable(array: AtomArray[Any]) -> None:
    """Refuse `rank` on an assembly with more than one symmetry copy.

    Decision 9 rests on an invariant: everything the tools produce is symmetric
    across symmetry copies, which is why handles can travel to the viewer as
    `atom.id` ranges even though an assembly duplicates those ids. `rank` is
    the first selector that breaks it. It picks one atom by array position, and
    that atom shares its `atom_id` with its counterpart in every other copy, so
    the handle selects one atom here and lights up N in the picture — a count
    and a rendering that disagree, with nothing to say they do.

    Refused rather than quietly widened to all copies: `rank 5` means one atom
    in PyMOL, and answering with N atoms would be a different question. The
    asymmetric unit has one copy of everything and is where the selector works.

    This is backlog item 7 seen from a new direction — the fix is a handle
    transport that can name a copy, not a patch here.
    """
    if not _has_sym(array):
        return
    copies = int(np.unique(np.asarray(array.sym_id)).size)
    if copies <= 1:
        return
    raise SelectionError(
        f"'rank' is a position in the atom array, and this assembly has "
        f"{copies} symmetry copies that share atom ids — so a rank selects one "
        "atom here and would highlight one per copy in the viewer. Load with "
        'assembly="asymmetric" to use it, or select by index, name or residue'
    )


def _check_elements(wanted: list[str], present: Mask) -> None:
    """Refuse an element symbol that does not exist.

    ``elem Zz`` used to return 0 atoms and no complaint, which reads as "this
    structure has none of those" rather than "you misspelled it".

    A symbol is only refused when it is neither a real element nor present in
    this structure. Checking the structure too means the refusal can never
    swallow a selection that would have matched something — a file carrying a
    symbol this table has never heard of still answers rather than raising —
    and a real element that is simply absent still answers 0, because that is
    a true statement about the molecule rather than a mistake.
    """
    unknown = [
        symbol
        for symbol in wanted
        if symbol not in _ELEMENTS and not bool(np.isin(present, [symbol]).any())
    ]
    if not unknown:
        return
    known = sorted(set(present.tolist()) | _ELEMENTS)
    suggestions = [
        close for symbol in unknown for close in get_close_matches(symbol, known, n=1)
    ]
    hint = f" Did you mean {suggestions[0]!r}?" if suggestions else ""
    listed = ", ".join(repr(symbol) for symbol in unknown)
    raise SelectionError(
        f"No such element: {listed}.{hint} Element symbols are a closed set, so "
        "this would have matched nothing whatever the structure held"
    )


# Selection token -> the DSSP classes it means. Assignment distinguishes all
# three helix types, so `ss` can address each one as well as the lumped
# categories PyMOL offers. PyMOL's own long names are kept, because a model
# writing `ss helix` should not be told that is a typo.
#
# **`ss S` is sheet, not bend.** S is PyMOL's letter for strand and DSSP's
# letter for bend, and those are different things. PyMOL wins, because its is
# the syntax a caller actually writes; bend is reachable as `ss bend`.
_HELIX = frozenset("HGI")
_STRAND = frozenset("EB")
_UNSTRUCTURED = frozenset({"T", "S", "-"})

_SS_CLASSES: dict[str, frozenset[str]] = {
    "H": _HELIX,  # every helix type, which is what PyMOL's `ss H` means
    "HELIX": _HELIX,
    "ALPHA": frozenset("H"),
    "HELIX_ALPHA": frozenset("H"),
    "3-10": frozenset("G"),
    "310": frozenset("G"),
    "G": frozenset("G"),
    "HELIX_310": frozenset("G"),
    "PI": frozenset("I"),
    "I": frozenset("I"),
    "HELIX_PI": frozenset("I"),
    "S": _STRAND,
    "E": _STRAND,
    "SHEET": _STRAND,
    "STRAND": _STRAND,
    "EXTENDED": frozenset("E"),
    "BRIDGE": frozenset("B"),
    "B": frozenset("B"),
    "TURN": frozenset("T"),
    "T": frozenset("T"),
    "BEND": frozenset("S"),
    "L": _UNSTRUCTURED,
    "C": _UNSTRUCTURED,
    "LOOP": _UNSTRUCTURED,
    "COIL": _UNSTRUCTURED,
}


def _secondary_structure(array: AtomArray[Any], values: tuple[str, ...]) -> Mask:
    """Atoms whose residue is helix, strand, loop, or one specific class.

    Assigned here rather than read from the file. A deposited HELIX/SHEET
    record is the depositor's opinion and is missing altogether from anything
    predicted or minimised, so computing it means `ss` answers the same way for
    every structure.

    The assignment is DSSP (see analysis/secondary_structure.py), which
    replaced P-SEA because P-SEA has classes for alpha and beta only: 3-10 and
    pi helices were not misclassified by it, they were unreachable.

    Like `elem`, the vocabulary is closed, so an unrecognised value is refused
    rather than quietly matching nothing.
    """
    wanted: set[str] = set()
    for value in values:
        classes = _SS_CLASSES.get(value.upper())
        if classes is None:
            listed = ", ".join(sorted(_SS_CLASSES))
            raise SelectionError(
                f"Unknown secondary structure {value!r}. Expected one of: {listed}"
            )
        wanted |= classes

    # One class per atom, '' for anything that has none — every non-amino-acid,
    # and any residue whose backbone is too incomplete to assign. Neither can
    # match, including via `ss L`.
    codes = assign_secondary_structure(array)

    # A CA-only trace has amino acids and no assignable residue, so every `ss`
    # class comes back empty — "no helix, no strand, and no loop either", three
    # answers about the molecule that are really one fact about the file. DSSP
    # needs N, CA, C and O; say so instead.
    if not (codes != "").any() and bool(filter_amino_acids(array).any()):
        raise SelectionError(
            "Secondary structure needs a complete backbone (N, CA, C and O) and "
            "no residue in this structure has one, so `ss` cannot be evaluated "
            "here. A CA-only or coarse-grained model is the usual reason"
        )
    return np.isin(codes, sorted(wanted))


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


def _bond_pairs(array: AtomArray[Any]) -> Mask:
    """Every bond as a pair of atom indices.

    Computed on demand rather than at load: only four selectors need topology,
    and it costs 25 ms on a 59,000-atom assembly, which is not worth paying on
    every load that never asks. Bonds carried by the file are used when
    present; otherwise they come from residue templates, which gives a het
    group its real connectivity rather than a distance guess.
    """
    bonds = array.bonds
    if bonds is None:
        try:
            bonds = connect_via_residue_names(array)
        except Exception as exc:
            raise SelectionError(
                f"Bond topology could not be derived for this structure: {exc}"
            ) from exc
    pairs: Mask = np.asarray(bonds.as_array()[:, :2])
    return pairs


def _bonded_to(array: AtomArray[Any], source: Mask) -> Mask:
    """Atoms directly bonded to *source*, excluding *source* itself.

    PyMOL's `neighbor` and `bound_to` name the same set; both are this.
    """
    pairs = _bond_pairs(array)
    found = np.zeros(array.array_length(), dtype=bool)
    if len(pairs) == 0:
        return found
    first, second = pairs[:, 0], pairs[:, 1]
    found[first[source[second]]] = True
    found[second[source[first]]] = True
    return found & ~source


def _extend(array: AtomArray[Any], source: Mask, depth: int) -> Mask:
    """*source* plus everything reachable within *depth* bonds, source kept.

    Two things this deliberately does not do naively, both of which cost real
    time on a large structure:

    - it derives the bond topology **once**, not once per pass. Every pass used
      to call `_bonded_to`, which re-runs `connect_via_residue_names` — 25 ms
      on a 59,000-atom assembly, paid `depth` times over.
    - it stops as soon as a pass adds nothing. The walk saturates at the size
      of the connected component, and every further pass rebuilds the identical
      mask.

    Depth is bounded at parse time too. That bound keeps a typo finite; this
    keeps an in-range but oversized depth cheap.
    """
    pairs = _bond_pairs(array)
    if len(pairs) == 0:
        return source
    grown = source
    for _ in range(depth):
        widened = _one_bond_shell(grown, pairs)
        if not (widened & ~grown).any():
            return grown
        grown = widened
    return grown


def _one_bond_shell(source: Mask, pairs: Mask) -> Mask:
    """*source* widened by exactly one bond, source kept.

    Split out from :func:`_extend` so the number of shells actually walked can
    be counted. Saturating early returns the same mask as running the loop to
    its full depth, so a test comparing results cannot tell the two apart —
    only counting passes can.
    """
    first, second = pairs[:, 0], pairs[:, 1]
    widened = source.copy()
    widened[first[source[second]]] = True
    widened[second[source[first]]] = True
    return widened


def _molecules(array: AtomArray[Any]) -> Mask:
    """One component label per atom, so bonded atoms share a label.

    Label propagation with pointer jumping, which settles in rounds
    proportional to the log of the longest chain rather than its length.
    Written out rather than taken from scipy, which is present here only as
    somebody else's transitive dependency and so cannot be relied on.
    """
    n = array.array_length()
    labels = np.arange(n)
    pairs = _bond_pairs(array)
    if len(pairs) == 0:
        return labels
    first, second = pairs[:, 0], pairs[:, 1]
    while True:
        nxt = labels.copy()
        np.minimum.at(nxt, first, labels[second])
        np.minimum.at(nxt, second, labels[first])
        nxt = nxt[nxt]  # jump each atom to its own label's label
        if np.array_equal(nxt, labels):
            return labels
        labels = nxt


def _within(array: AtomArray[Any], source: Mask, radius: float) -> Mask:
    """Atoms within *radius* of any atom in *source*."""
    if not source.any():
        return np.zeros(array.array_length(), dtype=bool)
    cell_list = CellList(array[source], cell_size=max(radius, 1.0))
    neighbours = cell_list.get_atoms(array.coord, radius=radius)
    return np.asarray((neighbours >= 0).any(axis=-1))


def _modifier(node: Modifier, inner: Mask, array: AtomArray[Any]) -> Mask:
    """A prefix expansion applied to an already-evaluated child."""
    if node.kind == "first":
        out = np.zeros(array.array_length(), dtype=bool)
        hits = np.flatnonzero(inner)
        if len(hits):
            out[hits[0]] = True
        return out
    # PyMOL treats these two as synonyms, and a caller should not have to guess
    # which one it wants. Mol*'s transpiler keeps the source atoms in `bound_to`
    # and drops them from `neighbor`; the differential suite pins that.
    if node.kind in ("neighbor", "bound_to"):
        return _bonded_to(array, inner)
    if node.kind == "bymolecule":
        return _widen(inner, _molecules(array))
    # bychain widens over the *same* chain identifier that `chain` selects on.
    # The MolScript backend widens over Mol*'s chain key, which follows
    # label_asym_id, so there `chain A` and `bychain` disagree about what a
    # chain is; here they cannot.
    keys = _residue_keys(array) if node.kind == "byres" else array.chain_id
    return _widen(inner, np.asarray(keys))


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
        return _modifier(node, evaluate(node.child, array), array)
    if isinstance(node, Within):
        near = _within(array, evaluate(node.target, array), node.radius)
        selected = near & evaluate(node.child, array)
        if node.exclude_self:
            selected = selected & ~evaluate(node.target, array)
        return selected
    if isinstance(node, Expand):
        inner = evaluate(node.child, array)
        return inner | _within(array, inner, node.radius)
    if isinstance(node, Extend):
        return _extend(array, evaluate(node.child, array), node.depth)
    raise SelectionError(f"Cannot evaluate node: {node!r}")


def select_mask(selection: str, array: AtomArray[Any]) -> Mask:
    """Compile and evaluate a PyMOL-syntax selection against *array*."""
    return evaluate(parse(selection), array)
