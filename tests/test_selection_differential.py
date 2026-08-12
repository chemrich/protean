"""Differential tests: the Python engine vs Mol*'s bundled PyMOL transpiler.

Two independent implementations of the same grammar, evaluated against 4HHB.
Where they agree, we have a regression signal for free. Where they disagree,
the divergence is asserted explicitly — those are the cases from PLAN.md
decision 5 where the bundled transpiler silently answers nothing.

Absolute expected counts are asserted too, so that a *mutual* failure (both
returning 0) cannot masquerade as agreement.

Three different claims are checked here, and they are not interchangeable:

  ground truth  the Python engine against counts derived by hand from 4HHB.
  semantics     the Python engine against a foreign implementation of PyMOL
                selection syntax. This is the only independent opinion we
                have about what a selection *means*.
  transport     atom sets resolved in Python, emitted as explicit-atom-id
                MolScript, and re-counted by Mol*. This is the production
                path every handle travels to reach the viewer, and the one
                place a set can be corrupted between computed and drawn.

The third used to be untested: the suite compiled selections with a second
MolScript emitter that the server never called, so the browser only ever saw
code that shipped to nobody. Retiring that emitter meant pointing these tests
at the path that actually runs.

Requires a real browser and is opt-in:

    PROTEAN_DIFFERENTIAL=1 uv run pytest tests/test_selection_differential.py
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from protean_mcp.fetch import fetch_structure_data
from protean_mcp.handles import to_molscript as indices_to_molscript
from protean_mcp.selections_numpy import load_structure, select_mask

from .browser import BROWSER_MARKS, viewer_session

FIXTURE = "4hhb"

# Structure classes whose entity typing differs enough to break selectors that
# look fine on a plain globular protein. Glycans in particular are a separate
# mmCIF entity type ("branched"), not non-polymer.
CLASS_FIXTURES: dict[str, dict[str, str]] = {
    "1bna": {  # B-DNA dodecamer: no protein at all
        "nucleic": "nonzero",
        "protein": "zero",
        "polymer": "nonzero",
        "backbone": "nonzero",  # the sugar-phosphate backbone
        "sidechain": "nonzero",  # the bases, not the whole molecule
    },
    "5fji": {  # glycoprotein: branched oligosaccharide entities
        "protein": "nonzero",
        "glycan": "nonzero",
        "nucleic": "zero",
    },
    "1ca2": {  # carbonic anhydrase: a single catalytic Zn
        "metals": "nonzero",
        "protein": "nonzero",
        "ion": "nonzero",
        "glycan": "zero",
    },
}

pytestmark = BROWSER_MARKS

# Ground truth on 4HHB, cross-checked by hand:
#   4 protein chains + 4 HEM + 221 waters = 4779 atoms
#   574 residues x 4 backbone atoms + 4 C-terminal OXT = 2300
EXPECTED: dict[str, int] = {
    "all": 4779,
    "none": 0,
    "polymer": 4384,
    "solvent": 221,
    "hetatm": 395,
    "organic": 172,
    "inorganic": 2,
    "chain A": 1168,
    "chain A or chain B": 2392,
    "not chain A": 3611,
    "resi 50": 34,
    "resi 50-60": 308,
    "resi 50+60+70": 90,
    "resi 50-60 and chain A": 77,
    "name CA": 574,
    "name CA+CB": 1108,
    "elem C": 2954,
    "elem Fe": 4,
    "resn HEM": 172,
    "not solvent": 4558,
    "hydro": 0,
    "b > 50": 271,
    "b < 20": 2284,
    "byres (chain A within 4 of chain B)": 130,
    "(chain A or chain B) and resi 1-50 and polymer": 782,
    # Bond topology. Both counts confirmed against PyMOL 3.1.0 on this file:
    # residue 50's CA in four chains, three bonded partners each; and every
    # CA plus its bonded neighbours.
    "neighbor (resi 50 and name CA)": 12,
    "name CA extend 1": 2256,
}

# Selections where the bundled transpiler is wrong. Value is the correct count;
# the test asserts we produce it AND that the transpiler does not.
DIVERGENCES: dict[str, int] = {
    # Not silence, unlike most of these: OXT. We count the C-terminal
    # carboxylate's second oxygen as backbone and they do not, so the two
    # differ by one atom per chain — four in 4HHB. PyMOL agrees with us at
    # 2300, and OXT hangs off the same carbonyl carbon as O, so calling it a
    # sidechain atom was the odd position.
    "backbone": 2300,
    "sidechain": 2084,
    "chain A and not backbone": 603,
    "metals": 4,  # their keyword table has a @desc but no implementation
    "chain A and not hydro": 1168,  # their `not` collapses on an empty operand
    "within 5 of resn HEM": 535,  # they require an explicit left operand
    "first chain A": 1,  # silently 0
    # Silently 0 for them. 4779 is PyMOL's answer: `bychain` widens over the
    # same chain id it selected on, so the hemes come with their chain. The
    # retired MolScript backend said 4384 because Mol*'s chain key follows
    # label_asym_id, which splits the hemes off into their own chains.
    "bychain resi 50": 4779,
    # Prefix-operator precedence. PyMOL binds `byres` tighter than `and`, so
    # this is (byres X) and Y = 295. The transpiler swallows the `and` across
    # the parenthesis boundary and computes byres (X and Y) = 502 — which is
    # the right answer to a different question. Confirmed by the disambiguated
    # control above, where both engines return 295.
    "byres (polymer within 4 of resn HEM) and sidechain": 295,
    # ...and when you write that other question explicitly, they return 0.
    "byres ((polymer within 4 of resn HEM) and sidechain)": 502,
    # Silently 0 for them, both of them. PyMOL agrees with our answers.
    "bymolecule resn HEM": 172,
    "rank 5": 1,
    # Not silence but a different definition: their `bound_to` keeps the source
    # atoms, so they return 16 where PyMOL and we return the 12 partners. PyMOL
    # treats `bound_to` and `neighbor` as synonyms and so do we; they do not,
    # and only their `neighbor` matches.
    "bound_to (resi 50 and name CA)": 12,
}

# Cross-checked against the transpiler but without an independently derived
# count. Useful for isolating where a composite selection starts to drift.
AGREEMENT_ONLY: tuple[str, ...] = (
    "polymer within 4 of resn HEM",
    "byres (polymer within 4 of resn HEM)",
    "chain A within 5 of resn HEM",
    "resn HEM expand 5",
    "byres name CA",
    # Fully disambiguated, both engines agree — the control for the precedence
    # divergence below.
    "(byres (polymer within 4 of resn HEM)) and sidechain",
)

CORPUS = list(EXPECTED) + list(AGREEMENT_ONLY) + list(DIVERGENCES)

_EVAL_JS = r"""(async () => {
  const p = window.__protean.plugin;
  const struct = p.managers.structure.hierarchy.current.structures[0];
  const ref = struct.cell.transform.ref;
  const out = [];
  for (const [key, language, expression] of %s) {
    const entry = { key };
    let sel = null;
    try {
      sel = await p.builders.structure.tryCreateComponent(ref, {
        type: { name: 'script', params: { language, expression } },
        nullIfEmpty: false, label: 'diff',
      }, 'diff-key');
      const data = sel && (sel.data || (sel.cell && sel.cell.obj && sel.cell.obj.data));
      if (data) entry.count = data.elementCount;
      else if (sel && sel.cell && sel.cell.status === 'error')
        entry.error = String(sel.cell.errorText).slice(0, 200);
      else entry.count = 0;
    } catch (err) {
      entry.error = String(err && err.message ? err.message : err).slice(0, 200);
    }
    try { if (sel && sel.ref) await p.state.data.build().delete(sel.ref).commit(); }
    catch (err) { /* leave the tree alone; next iteration overwrites */ }
    out.push(entry);
  }
  return JSON.stringify(out);
})()"""


async def _evaluate(
    pdb_id: str, cases: list[list[str]], assembly: str = "asymmetric"
) -> dict[str, dict[str, object]]:
    """Load *pdb_id* in a throwaway browser and evaluate every case in it.

    `assembly` defaults to the deposited coordinates because the ground-truth
    counts are derived from those, and both engines must be shown the same
    molecule for the comparison to mean anything.
    """
    async with viewer_session(pdb_id, assembly=assembly) as session:
        raw = await session.evaluate(_EVAL_JS % json.dumps(cases))
    return {entry["key"]: entry for entry in raw}


@pytest.fixture(scope="module")
async def counts() -> dict[str, dict[str, object]]:
    """One browser session carrying both browser-side claims.

    `theirs::` is their transpiler compiling the PyMOL string — the semantics
    oracle. `roundtrip::` is our own atom set, resolved in Python and emitted
    as explicit atom ids — the transport check. They are deliberately in the
    same session so a structure that loaded differently cannot make one look
    right while the other is wrong.
    """
    structure = await fetch_structure_data(FIXTURE)
    array = load_structure(structure.data, structure.format, "asymmetric").array

    cases: list[list[str]] = []
    for selection in CORPUS:
        cases.append([f"theirs::{selection}", "pymol", selection])
        indices = np.flatnonzero(select_mask(selection, array))
        cases.append(
            [
                f"roundtrip::{selection}",
                "mol-script",
                indices_to_molscript(array, indices),
            ]
        )
    return await _evaluate(FIXTURE, cases)


@pytest.fixture(scope="module")
async def class_counts() -> dict[str, dict[str, int]]:
    """Class-specific selectors, resolved by the Python engine.

    No browser: the engine under test is the Python one, and these fixtures
    exist to exercise entity typing on structure classes a globular protein
    would never reach.
    """
    out: dict[str, dict[str, int]] = {}
    for pdb_id, expectations in CLASS_FIXTURES.items():
        structure = await fetch_structure_data(pdb_id)
        array = load_structure(structure.data, structure.format, "asymmetric").array
        out[pdb_id] = {
            selection: int(select_mask(selection, array).sum())
            for selection in expectations
        }
    return out


def _count(counts, prefix: str, selection: str) -> int:
    entry = counts[f"{prefix}::{selection}"]
    if "error" in entry:
        pytest.fail(f"{prefix} '{selection}' errored: {entry['error']}")
    count: int = entry["count"]
    return count


@pytest.mark.parametrize("selection", sorted(EXPECTED))
async def test_matches_ground_truth(python_counts, selection):
    """The Python engine produces the hand-checked atom count."""
    assert python_counts[selection] == EXPECTED[selection]


@pytest.mark.parametrize("selection", sorted(EXPECTED) + sorted(AGREEMENT_ONLY))
async def test_agrees_with_bundled_transpiler(counts, python_counts, selection):
    """Two independent implementations of one grammar.

    This is the whole safety argument for evaluating selections in Python:
    something that did not read our code still has to agree about what the
    selection means.
    """
    assert python_counts[selection] == _count(counts, "theirs", selection)


@pytest.mark.parametrize("selection", sorted(DIVERGENCES))
async def test_beats_bundled_transpiler(counts, python_counts, selection):
    """Cases where Mol*'s transpiler is silently wrong and we are not.

    Asserting *both* halves keeps this honest: if upstream ever fixes one of
    these, the test fails and we retire the divergence rather than carrying a
    stale claim.
    """
    expected = DIVERGENCES[selection]
    theirs = _count(counts, "theirs", selection)
    assert python_counts[selection] == expected
    assert theirs != expected, (
        f"Mol*'s transpiler now returns {theirs} for {selection!r}; "
        "move it out of DIVERGENCES"
    )


@pytest.mark.parametrize(
    "selection", sorted(EXPECTED) + sorted(AGREEMENT_ONLY) + sorted(DIVERGENCES)
)
async def test_handles_survive_the_trip_to_the_viewer(counts, python_counts, selection):
    """The production path: Python resolves it, Mol* has to agree on the set.

    Every handle reaches the viewer as explicit atom ids compressed into
    ranges. A bug in that compression would show up here as a count that
    drifts from what Python resolved — and nowhere else, because the numbers
    the tools report all come from the Python side.
    """
    assert python_counts[selection] == _count(counts, "roundtrip", selection)


@pytest.mark.parametrize(
    ("pdb_id", "selection", "expectation"),
    [(p, s, e) for p, sels in CLASS_FIXTURES.items() for s, e in sels.items()],
)
async def test_class_selectors(class_counts, pdb_id, selection, expectation):
    """Selectors that only a non-globular-protein structure can exercise.

    Entity typing is where these break: glycans are `branched`, not
    `non-polymer`, so a protein-only fixture would never catch it.
    """
    count = class_counts[pdb_id][selection]
    if expectation == "nonzero":
        assert count > 0, f"'{selection}' found nothing in {pdb_id}"
    else:
        assert count == 0, f"'{selection}' unexpectedly matched {count} atoms in {pdb_id}"


@pytest.fixture(scope="module")
async def python_counts() -> dict[str, int]:
    """The corpus evaluated by the Python engine, no browser involved."""
    structure = await fetch_structure_data(FIXTURE)
    array = load_structure(structure.data, structure.format, "asymmetric").array
    return {sel: int(select_mask(sel, array).sum()) for sel in CORPUS}


# -- assembly agreement --------------------------------------------------------

# 1HHO is the case that exposed the split: its asymmetric unit is one alpha-beta
# dimer and its biological assembly is the alpha2beta2 tetramer, so the two
# halves of the system used to describe different molecules without saying so.
ASSEMBLY_FIXTURE = "1hho"
ASSEMBLY_EXPECTED = {"asymmetric": 2396, "biological": 4792}


@pytest.fixture(scope="module")
async def assembly_counts() -> dict[str, dict[str, int]]:
    """Atom counts from both engines, for both assembly settings."""
    structure = await fetch_structure_data(ASSEMBLY_FIXTURE)
    out: dict[str, dict[str, int]] = {}
    for mode in ASSEMBLY_EXPECTED:
        loaded = load_structure(structure.data, structure.format, mode)
        viewer = await _evaluate(
            ASSEMBLY_FIXTURE,
            [["all::", "mol-script", "(sel.atom.all)"]],
            assembly=mode,
        )
        out[mode] = {
            "python": int(loaded.array.array_length()),
            "molstar": _count(viewer, "all", ""),
        }
    return out


@pytest.mark.parametrize("mode", sorted(ASSEMBLY_EXPECTED))
async def test_viewer_and_analysis_hold_the_same_molecule(assembly_counts, mode):
    """The invariant the assembly work exists to establish.

    Both numbers are also asserted absolutely, so the two engines agreeing on
    the wrong molecule cannot pass as agreement.
    """
    counts = assembly_counts[mode]
    assert counts["python"] == counts["molstar"]
    assert counts["python"] == ASSEMBLY_EXPECTED[mode]


async def test_the_two_settings_are_actually_different(assembly_counts):
    """Guards the test above: if both modes gave the same molecule it proves nothing."""
    assert (
        assembly_counts["biological"]["python"] != assembly_counts["asymmetric"]["python"]
    )


# -- alternate conformers ------------------------------------------------------

# 5FJI carries 206 atom sites with two conformers and 11 with a third. biotite
# keeps one per site and Mol* draws all of them, so the two disagree by 217
# atoms while describing the same molecule. That difference read as a mismatch
# — and as "treat every number as unreliable" — until it was measured.
CONFORMER_FIXTURE = "5fji"
CONFORMER_EXPECTED = {"python": 15712, "molstar": 15929, "surplus": 217}


@pytest.fixture(scope="module")
async def conformer_counts() -> dict[str, int]:
    structure = await fetch_structure_data(CONFORMER_FIXTURE)
    loaded = load_structure(structure.data, structure.format, "asymmetric")
    viewer = await _evaluate(
        CONFORMER_FIXTURE, [["all::", "mol-script", "(sel.atom.all)"]]
    )
    return {
        "python": int(loaded.array.array_length()),
        "molstar": _count(viewer, "all", ""),
        "surplus": loaded.altloc_surplus,
    }


async def test_the_conformer_surplus_accounts_for_the_whole_difference(
    conformer_counts,
):
    """The claim the explained-difference branch rests on.

    Measured against a real Mol* rather than against the file's row count,
    because what matters is what the viewer actually built.
    """
    assert (
        conformer_counts["python"] + conformer_counts["surplus"]
        == conformer_counts["molstar"]
    )


async def test_the_conformer_counts_are_the_expected_ones(conformer_counts):
    """Guards the test above: 0 + 0 == 0 would satisfy it otherwise."""
    assert conformer_counts == CONFORMER_EXPECTED
    assert conformer_counts["python"] != conformer_counts["molstar"]


# -- the nucleic backbone ------------------------------------------------------

# `backbone` used to be protein N/CA/C/O only, so on B-DNA it found nothing and
# `sidechain` — defined as "polymer and not backbone" — returned all 486 atoms
# and read as a real answer.
#
# These counts are the sugar-phosphate backbone and the bases. PyMOL 3.1.0 on
# the same file gives 258 and 228, and so does Mol*'s bundled transpiler, so
# three independent implementations are pinned here rather than one.
NUCLEIC_FIXTURE = "1bna"
NUCLEIC_EXPECTED = {"polymer": 486, "backbone": 258, "sidechain": 228}


@pytest.fixture(scope="module")
async def nucleic_counts() -> dict[str, dict[str, int]]:
    structure = await fetch_structure_data(NUCLEIC_FIXTURE)
    array = load_structure(structure.data, structure.format, "asymmetric").array
    viewer = await _evaluate(
        NUCLEIC_FIXTURE,
        [[f"theirs::{selection}", "pymol", selection] for selection in NUCLEIC_EXPECTED],
    )
    return {
        "python": {
            selection: int(select_mask(selection, array).sum())
            for selection in NUCLEIC_EXPECTED
        },
        "molstar": {
            selection: _count(viewer, "theirs", selection)
            for selection in NUCLEIC_EXPECTED
        },
    }


@pytest.mark.parametrize("selection", sorted(NUCLEIC_EXPECTED))
async def test_nucleic_backbone_matches_ground_truth(nucleic_counts, selection):
    assert nucleic_counts["python"][selection] == NUCLEIC_EXPECTED[selection]


@pytest.mark.parametrize("selection", sorted(NUCLEIC_EXPECTED))
async def test_nucleic_backbone_agrees_with_bundled_transpiler(nucleic_counts, selection):
    """A second implementation that did not read our code splits DNA the same way."""
    assert nucleic_counts["molstar"][selection] == NUCLEIC_EXPECTED[selection]


async def test_nucleic_backbone_and_sidechain_partition_the_polymer(nucleic_counts):
    """The property the bug broke: sidechain was the whole molecule."""
    counts = nucleic_counts["python"]
    assert counts["backbone"] + counts["sidechain"] == counts["polymer"]
    assert counts["sidechain"] != counts["polymer"]


async def test_we_beat_the_transpiler_on_nucleic():
    """Their `nucleic` finds nothing in a structure that is nothing else.

    Noticed while taking their opinion on the backbone split. Asserted both
    ways, like the other divergences, so that an upstream fix retires the
    claim rather than leaving it stale.
    """
    structure = await fetch_structure_data(NUCLEIC_FIXTURE)
    array = load_structure(structure.data, structure.format, "asymmetric").array
    viewer = await _evaluate(NUCLEIC_FIXTURE, [["theirs::nucleic", "pymol", "nucleic"]])
    assert int(select_mask("nucleic", array).sum()) == NUCLEIC_EXPECTED["polymer"]
    assert _count(viewer, "theirs", "nucleic") == 0


# -- what counts as a bond -----------------------------------------------------


async def test_the_two_engines_disagree_about_metal_coordination():
    """Not a bug on either side: a real question with two defensible answers.

    Extending one bond from the hemes returns the hemes unchanged for us and
    for PyMOL, because a residue template gives iron no bond to the protein.
    Mol* models the Fe-NE2 coordination bond and so reaches the proximal
    histidine, four atoms further.

    Recorded rather than resolved, and asserted from both sides so that either
    engine changing its bond model shows up here. It is why `resn HEM extend 1`
    is not in the agreement table.
    """
    structure = await fetch_structure_data(FIXTURE)
    array = load_structure(structure.data, structure.format, "asymmetric").array
    viewer = await _evaluate(
        FIXTURE, [["theirs::resn HEM extend 1", "pymol", "resn HEM extend 1"]]
    )
    ours = int(select_mask("resn HEM extend 1", array).sum())
    theirs = _count(viewer, "theirs", "resn HEM extend 1")
    assert ours == 172, "the hemes alone, which is PyMOL's answer too"
    assert theirs == ours + 4, "one proximal histidine nitrogen per heme"


# -- secondary structure -------------------------------------------------------

# `ss` was refused entirely until secondary structure was assigned here. It is
# assigned with P-SEA, which works off backbone geometry.
#
# On 1UBQ Mol*'s transpiler says 132 helix atoms and 274 strand, where P-SEA
# says 89 and 217. **Mol* is not computing an assignment.** 132 and 274 are the
# deposited `struct_conf` and `struct_sheet_range` records in the mmCIF, and
# parsing those records directly reproduces both numbers exactly —
# tests/test_secondary_structure_reference.py pins that, and it is the whole
# correction to backlog item 10. PyMOL reports the same two numbers for the same
# reason; its own computed assignment (`cmd.dss()`) is a third answer, 135/266.
#
# So this is a computed assignment against a file annotation, and says nothing
# about either algorithm. Real DSSP scores 82% against that annotation — exactly
# what P-SEA scores.
#
# Asserted anyway, from both sides. What reaches the viewer has to stay
# predictable whatever it derives from, and a silent change in either the
# annotation or our assignment should fail loudly rather than move every `ss`
# answer in the project.
SS_FIXTURE = "1ubq"
SS_OURS = {"ss H": 89, "ss S": 217}
SS_DEPOSITED = {"ss H": 132, "ss S": 274}


@pytest.fixture(scope="module")
async def ss_counts() -> dict[str, dict[str, int]]:
    structure = await fetch_structure_data(SS_FIXTURE)
    array = load_structure(structure.data, structure.format, "asymmetric").array
    viewer = await _evaluate(SS_FIXTURE, [[f"theirs::{s}", "pymol", s] for s in SS_OURS])
    return {
        "python": {s: int(select_mask(s, array).sum()) for s in SS_OURS},
        "molstar": {s: _count(viewer, "theirs", s) for s in SS_OURS},
    }


@pytest.mark.parametrize("selection", sorted(SS_OURS))
async def test_secondary_structure_matches_our_assignment(ss_counts, selection):
    assert ss_counts["python"][selection] == SS_OURS[selection]


@pytest.mark.parametrize("selection", sorted(SS_DEPOSITED))
async def test_the_transpiler_reports_the_deposited_annotation(ss_counts, selection):
    """A recorded divergence, not a passing grade — and not a second opinion.

    Mol* returns the depositor's own helix and sheet ranges here, so this pins
    what the transpiler does rather than scoring our assignment against it. If
    upstream ever starts computing secondary structure instead, these counts
    move and the claim retires rather than going stale.
    """
    assert ss_counts["molstar"][selection] == SS_DEPOSITED[selection]
    assert ss_counts["python"][selection] < ss_counts["molstar"][selection]
