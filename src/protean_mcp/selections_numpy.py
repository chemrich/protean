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

    ``altloc_surplus`` is how many rows of the file are alternate conformers
    beyond the first at their site. Both halves now hold every conformer, so
    this is no longer a difference to explain away -- it is how much of the
    shared atom count is alternates, which is what a caller needs to know
    before reading a number computed over one conformer state.
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
    """How many rows are alternate conformers beyond one per atom site.

    217 on 5FJI, 12 on 1AKE. This used to measure a disagreement: biotite kept
    one conformer per site and Mol\\* drew all of them, so the two halves
    reported different atom counts for the same molecule. Both now load every
    conformer and the counts agree, so the number has stopped being a
    discrepancy and become a description -- how much of the structure is
    alternates, and therefore how much of it a single conformer state leaves
    out.

    Still counted from the file rather than by differencing the two builders,
    because a difference that explains itself explains any bug along with it.
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
            array = _normalise_altloc(
                PDBFile.read(io.StringIO(text)).get_structure(
                    model=1, extra_fields=EXTRA_FIELDS, altloc="all"
                )
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
            array = _normalise_altloc(
                get_structure(handle, model=1, extra_fields=EXTRA_FIELDS, altloc="all")
            )
            return LoadedStructure(
                array=array, assembly="asymmetric", copies=1, altloc_surplus=surplus
            )

        copies = assembly_multiplicity(text)
        if copies > MAX_ASSEMBLY_COPIES:
            array = _normalise_altloc(
                get_structure(handle, model=1, extra_fields=EXTRA_FIELDS, altloc="all")
            )
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
        array = _normalise_altloc(
            get_assembly(handle, model=1, extra_fields=EXTRA_FIELDS, altloc="all")
        )
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


# biotite writes "." for an atom with no alternate when parsing mmCIF and " "
# when parsing PDB, and PyMOL spells the same thing "". One marker is chosen
# and the annotation normalised to it at load, so nothing downstream has to
# know which format a structure arrived in.
NO_ALTLOC = "."
_NO_ALTLOC_SPELLINGS = frozenset({NO_ALTLOC, "", " "})


def _normalise_altloc(array: AtomArray[Any]) -> AtomArray[Any]:
    """Make "no alternate" one string regardless of the source format."""
    if "altloc_id" not in array.get_annotation_categories():
        return array
    ids = np.asarray(array.get_annotation("altloc_id")).astype(str)
    array.set_annotation(
        "altloc_id",
        np.where(np.isin(ids, list(_NO_ALTLOC_SPELLINGS)), NO_ALTLOC, ids),
    )
    return array


def has_altlocs(array: AtomArray[Any]) -> bool:
    """Does this array carry alternate conformers to choose between?"""
    if "altloc_id" not in array.get_annotation_categories():
        return False
    return bool((np.asarray(array.get_annotation("altloc_id")) != NO_ALTLOC).any())


def conformer_state(array: AtomArray[Any], altloc: str | None = None) -> Mask:
    """The atoms of one conformer state: the shared atoms plus one letter.

    Alternate conformers of an atom never coexist -- each molecule in the
    crystal is in one state or the other -- so geometry computed over both at
    once describes a molecule that does not exist. Every tool that reads
    coordinates resolves a state first.

    **The states overlap.** Only the atoms that actually differ carry a
    letter; the rest of the residue is shared and tagged ".". So state A is
    the "." atoms plus the "A" atoms, and state B shares those same "." atoms
    with it. That is why alternate conformers cannot be a residue-identity
    field the way ``sym_id`` is: keying on them would split one residue into a
    shared fragment and two partial ones.

    With no ``altloc``, the choice is made **per site**: each atom keeps its
    own highest-occupancy alternate. Choosing one letter for the whole
    structure instead looks equivalent and is not -- an atom labelled `B` with
    no `A` counterpart, which is how a partially occupied ion or ligand is
    routinely modelled, would be **deleted from the geometry entirely**, with
    no note and a plausible answer. 5FJI has 11 atoms labelled `C` that a
    global `A` discards.

    Naming a letter explicitly asks a different question -- "conformer B" --
    and is answered literally: the shared atoms plus that letter, matching
    what ``alt ''+B`` selects. A site with no `B` contributes nothing, because
    it has no B conformer.
    """
    if not has_altlocs(array):
        return np.ones(array.array_length(), dtype=bool)
    ids = np.asarray(array.get_annotation("altloc_id"))
    shared = ids == NO_ALTLOC
    if altloc is not None:
        return np.asarray(shared | (ids == altloc))

    keep: Mask = np.asarray(shared.copy())
    labelled = np.flatnonzero(~shared)
    if not len(labelled):
        return keep
    sites = _site_labels(array)[labelled]
    if "occupancy" in array.get_annotation_categories():
        occupancy = np.asarray(array.occupancy, dtype=float)[labelled]
    else:
        occupancy = np.zeros(len(labelled))
    # Sort by site, then descending occupancy, then letter, and take the first
    # row of each site. Ties fall to the alphabetically first letter, which is
    # arbitrary but stated: 1AKE's ARG167 is 0.5/0.5.
    order = np.lexsort((ids[labelled], -occupancy, sites))
    ordered = sites[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = ordered[1:] != ordered[:-1]
    keep[labelled[order[first]]] = True
    return np.asarray(keep)


def _site_labels(array: AtomArray[Any]) -> Any:
    """One string per atom naming the physical site it is a conformer of.

    Two rows agreeing on all of these are alternate positions of the same
    atom, not two atoms. The symmetry copy is included for the reason it is in
    ``residue_labels``: copies repeat every chain id and residue number.
    """
    parts = [
        array.chain_id.astype(str),
        array.res_id.astype(str),
        array.ins_code.astype(str),
        array.atom_name.astype(str),
    ]
    if _has_sym(array):
        parts.append(np.asarray(array.sym_id).astype(str))
    labels = parts[0]
    for part in parts[1:]:
        labels = np.char.add(np.char.add(labels, "|"), part)
    return labels


def labelled_atom_count(array: AtomArray[Any]) -> int:
    """How many atoms carry an alternate-conformer label.

    Distinct from :func:`altloc_surplus`, which counts rows beyond the first
    at a site. A single partially occupied ion labelled `B` is one row at one
    site: the surplus is 0 while the atom is an alternate, so a note keyed on
    the surplus said "0 of these are alternate conformers" and then named one.
    """
    if "altloc_id" not in array.get_annotation_categories():
        return 0
    ids = np.asarray(array.get_annotation("altloc_id"))
    return int((ids != NO_ALTLOC).sum())


def conformers_used(array: AtomArray[Any], state: Mask | None = None) -> str:
    """The letters a resolved state actually kept, for the reply.

    Usually one, and named as such. Where different sites favour different
    conformers -- 5FJI resolves to `A+B` -- all of them are named, because
    "conformer A" would be a false description of what was measured.
    """
    if not has_altlocs(array):
        return ""
    ids = np.asarray(array.get_annotation("altloc_id"))
    chosen = ids[state if state is not None else conformer_state(array)]
    return "+".join(sorted({str(v) for v in chosen} - {NO_ALTLOC}))


def dominant_altloc(array: AtomArray[Any]) -> str:
    """The altloc letter carrying the most occupancy across the structure.

    For *describing* a structure -- which conformer predominates. Deliberately
    not what :func:`conformer_state` resolves with: a single letter applied
    structure-wide deletes every site that does not carry it.
    """
    ids = np.asarray(array.get_annotation("altloc_id"))
    letters = sorted({str(v) for v in ids} - {NO_ALTLOC})
    if not letters:
        return NO_ALTLOC
    if "occupancy" not in array.get_annotation_categories():
        return letters[0]
    occupancy = np.asarray(array.occupancy, dtype=float)
    totals = {letter: float(occupancy[ids == letter].sum()) for letter in letters}
    best = max(totals.values())
    return next(letter for letter in letters if totals[letter] == best)


def resolve_conformers(
    array: AtomArray[Any], altloc: str | None = None
) -> tuple[AtomArray[Any], str]:
    """One conformer state of *array*, and a label for what it kept.

    The single entry point for every tool that reads coordinates, so that
    "which conformer did this number come from?" has one answer and one
    implementation. Returns the array unchanged with an empty label when there
    is nothing to resolve, so a caller can put the label straight in its reply
    without special-casing the ordinary structure.
    """
    if not has_altlocs(array):
        return array, ""
    state = conformer_state(array, altloc)
    return array[state], conformers_used(array, state)


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
        return _numeric_terms(np.arange(array.array_length()), node.values, array)
    if prop == "alt":
        # Literal, as PyMOL means it: the atoms carrying this label, not the
        # conformer state. Only the atoms that actually differ carry one, so
        # `alt A` on a residue whose backbone is shared is a side chain with
        # no backbone. The state is `alt ''+A`, and the state that analysis
        # computes over is chosen inside the tools, not here.
        _check_alt_is_available(array)
        wanted = [_altloc_value(v) for v in node.values]
        return np.isin(np.asarray(array.get_annotation("altloc_id")), wanted)
    if prop == "sym":
        # Which copy of the asymmetric unit an atom belongs to, 0-based, as
        # biotite annotates it. A selection with no `sym` term keeps meaning
        # "every copy", so this narrows an answer rather than changing one.
        _check_sym_is_available(array)
        return _numeric_terms(np.asarray(array.sym_id), node.values, array)
    raise SelectionError(f"Property {prop!r} is not supported by the Python evaluator")


def _altloc_value(value: str) -> str:
    """One `alt` value, normalised to what the annotation actually holds.

    PyMOL spells "no alternate" as `alt \'\'`, and the quotes survive
    tokenisation as part of the word -- so the value arrives here as the
    two-character string `\'\'` rather than as an empty one. Stripping them is
    what makes `alt \'\'` mean anything at all; without it the selection is
    empty and reads as "this structure has no unlabelled atoms", which is the
    silent-empty answer the grammar exists to avoid.
    """
    stripped = value.strip("\"'")
    if stripped in _NO_ALTLOC_SPELLINGS:
        return NO_ALTLOC
    # Returned as written. `label_alt_id` is a free-form code and lowercase
    # ones exist, so upper-casing the request while leaving the annotation as
    # parsed made `alt a` match nothing on such a file -- the silent-empty
    # answer `_check_alt_is_available` exists to prevent.
    return stripped


def _check_alt_is_available(array: AtomArray[Any]) -> None:
    """Refuse `alt` where there are no alternate conformers to choose between.

    Named rather than silently empty, as `sym` and `ss` refuse: most
    structures have no alternates at all, and `alt A` coming back empty would
    read as "this structure has no conformer A" rather than "it has none".
    """
    if has_altlocs(array):
        return
    raise SelectionError(
        "'alt' names an alternate conformer, and no atom in this structure has "
        "one. Every atom is modelled in a single position"
    )


def _check_sym_is_available(array: AtomArray[Any]) -> None:
    """Refuse `sym` where there are no copies to choose between.

    Named rather than silently empty, the same way `ss` refuses a CA-only
    model: an asymmetric unit carries no ``sym_id`` at all, and ``sym 0``
    coming back empty would read as "this structure has no first copy".
    """
    if _has_sym(array):
        return
    raise SelectionError(
        "'sym' names a copy of the asymmetric unit, and this structure holds "
        "the asymmetric unit itself, which has only one. Load with "
        'assembly="biological" to address a copy'
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
    return _drop_cross_conformer_bonds(array, pairs)


def _drop_cross_conformer_bonds(array: AtomArray[Any], pairs: Mask) -> Mask:
    """Remove bonds joining one alternate conformer to another.

    Templates match by atom name and cannot tell conformers apart, so they
    wire them together: 16 such bonds on 1AKE, including
    ``ARG167/CD(A) -- ARG167/NE(B)``, leaving CD(A) with three bonds where it
    should have two. `extend`, `bymolecule`, `bound_to` and `neighbor` would
    then walk out of one conformer into another along a path no molecule has.

    Dropped here rather than by filtering the array first, because these
    selectors answer about the structure as loaded, with every conformer
    visible and selectable; only the impossible edges between states are
    wrong. Two atoms sharing a letter, or either being unlabelled, are the
    same state and stay bonded.
    """
    if not has_altlocs(array) or not len(pairs):
        return pairs
    ids = np.asarray(array.get_annotation("altloc_id"))
    left, right = ids[pairs[:, 0]], ids[pairs[:, 1]]
    across = (left != NO_ALTLOC) & (right != NO_ALTLOC) & (left != right)
    return pairs[~across] if across.any() else pairs


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
