# Changelog

Notable changes to protean. Versions follow [semantic versioning](https://semver.org);
nothing is released yet, so everything below is unreleased.

## Unreleased

### Volumes

- **Density maps can be contoured.** `isosurface(name, level, unit, style,
  opacity)` draws a volume as a solid surface or a wireframe mesh. The unit is
  named, never assumed: EMDB publishes author-recommended levels as ABSOLUTE
  values while most viewers contour in sigma, and EMD-30913's published 0.05 is
  3.16 sigma for that map — typed in as sigma it contours noise and looks like
  an ordinary bad map rather than a unit error.

  **A sigma level is converted against the sigma measured off the voxels**, and
  Mol\* is handed an absolute value it cannot reinterpret. Left to itself Mol\*
  converts using `grid.stats`, which for CCP4/MRC is the file header — its own
  default isosurface is 2 sigma against exactly those stored fields. The reply
  reports the `sigma` and `mean` used, and `stated_absolute`: what the header
  would have given for the same request. A large gap between the two says the
  file disagrees with itself.

  The wiggles-em backend no longer refuses an `Isosurface` op; it lowers it,
  carrying the unit rather than the number alone. A carve is still refused.

- **A volume can say where it came from, and protean never guesses.**
  `load_volume(..., provenance=)` takes one of `measured`, `sharpened`,
  `nn_enhanced`, `generated`, `unknown`, and every volume reply carries a
  `caveat` line to show beside a picture of the map. A filename saying
  `deepemhancer` is not evidence: a guessed label is believed, where a missing
  one prompts a question, so an undeclared map stays `unknown`. A typo is
  refused rather than coerced, since coercion would turn a caller who declared
  their map into one who appears not to have. The vocabulary is wiggles-em's
  `Provenance`, reused rather than duplicated, because the backend lowers its
  scenes onto this viewer and the two have to agree.
- Density maps load into the viewer: MRC/CCP4 (gzipped or not), DSN6, OpenDX,
  Gaussian cube and BinaryCIF. Four tools — `load_volume`, `volume_info`,
  `list_volumes`, `remove_volume` — taking the tool count from 49 to 53.
  protean could parse exactly one volume format before this, OpenDX, because
  that is what APBS writes.
- Format is detected from the MRC magic at byte 208 first and the extension
  second, on the *decompressed* bytes — `emd_30913.map.gz` has suffixes
  `['.map', '.gz']` and carries its magic only once unwrapped.
- Volumes travel over HTTP, not inline in the RPC message. A 110³ float32
  reconstruction is ~5 MB and a 400³ one ~256 MB, and base64 through a JSON
  WebSocket frame is the wrong pipe. The bridge serves them from a
  handle-keyed table, so only what was explicitly published is reachable.
- **The reported statistics are computed from the voxels, not read from the
  file header.** Mol\*'s `grid.stats` passes through MRC's stored DMIN, DMAX,
  DMEAN and RMS, which are not always true: a cropped or rescaled map keeps
  whatever header nobody updated. Since those numbers exist to convert a
  published absolute contour into sigma, a stale header would put the contour
  in the wrong place while every call returned cleanly. The header's own
  claims are still reported, under `stated`, because a large disagreement says
  the file has been through something.

  Found by a browser test written so it could not pass on the old behaviour:
  its fixture writes deliberately false header statistics (−999/999/42/7) and
  requires the reply to match the data instead. On its first run it failed with
  `min came back as the header's false value -999.0`.

### Alternate conformers

- Every alternate conformer is loaded, so the viewer and the analysis hold the
  same atoms — 15929 on 5FJI, where analysis previously held 15712 and the
  difference had to be explained in the load message.
- `alt A` selects atoms carrying that label, as PyMOL does; the whole
  conformer is `alt ''+A`, and `alt ''` and `alt .` both mean "no alternate".
- Analysis resolves one conformer state before computing — each site keeping
  its own highest-occupancy alternate — and reports which letters it used.
  Resolving per structure instead would delete any site not carrying the
  winning letter, which is how a partially occupied ion is routinely modelled. Alternate conformers never coexist, so
  a buried area computed over both belongs to no molecule, and because a
  residue's shared atoms carry no label both states would otherwise land in
  one residue entry and sum.
- Bonds joining one conformer to another are dropped, so `extend` and
  `bound_to` do not step between mutually exclusive states.

### Session state

- Loading a structure now ends the session before it. A trajectory and any
  saved keyframes belong to the molecule they were made for, and were
  previously carried across: `rmsf()` kept answering about the old trajectory
  while the viewer showed the new structure, with nothing to say so. The reply
  now names what was discarded.
- Tests restore the server's session globals between cases, so a test that
  loads something no longer changes what the next test sees.

### Addressing one symmetry copy

- `sym N` selects one copy of the asymmetric unit in a biological assembly,
  numbered from 0. Copies share chain ids and residue numbers, so `chain A`
  on an assembly means every copy of that chain and `chain A and sym 0` is
  the single subunit. A selection with no `sym` term still means every copy.
- Handles now carry the copy to the viewer. They travel as atom-id
  predicates, which an assembly duplicates, so a set covering one copy could
  not previously be drawn as one copy; each copy's ids are now keyed on its
  Mol* symmetry operator. Sets that are symmetric across copies are emitted
  exactly as before.
- `interface()` takes `copy=N`, and with no copy named reports a `per_copy`
  breakdown beside the total. On 1HHO the A-B total is 5530.2 A^2 where one
  alpha-beta pair buries 1776.9 -- the same number the deposited asymmetric
  unit gives.
- `rank` is no longer refused on multi-copy assemblies; it was refused only
  because the handle could not name a copy.

### Trajectories and animation (Phase 5)

- `load_trajectory()` reads XTC, TRR, DCD and NetCDF onto the loaded structure,
  with `stride` and `max_frames` for long runs. Atom counts must match: a
  trajectory carries no atom names, so the wrong pairing animates smoothly and
  means nothing.
- `frame()` steps through it; `rmsf()` and `rmsd_series()` return structured
  numbers, superposed first so bulk drift is not read as fluctuation.
- `color_by_rmsf()` draws the same values on the molecule.
- `record_trajectory()`, `turntable()` and `record_timeline()` capture frame
  sequences; `movie()` encodes them with ffmpeg to MP4, GIF or WebM.
- `keyframe()` and `record_timeline()` interpolate a camera between saved
  views, swinging around the subject rather than sliding between positions.
- `spin()` sets the viewer turning for on-screen reading.

### Publication rendering (Phase 4)

- `snapshot()` renders at a real physical size — Nature's column widths or an
  explicit width in millimetres — and writes the DPI into the file, as PNG,
  TIFF or JPEG.
- `background()` for colour, transparency, gradients, images and skyboxes;
  `lighting()` with six named rigs; `effects()` for outline, occlusion, shadow,
  depth of field, bloom and sharpening.
- `shading()` (cel, xray, flat), `material()` with five PBR finishes, and
  `path_trace()` for Mol\*'s progressive path tracer.
- `preset()` composes those into publication-cartoon, illustrative,
  ghost-surface and active-site.
- A pixel-assertion harness underpins all of it: rendering is verified by
  reading the image, not the reply.

### Analysis (Phase 3)

- `interface()` reports buried area per side and classified contacts;
  `superpose()` aligns by sequence and applies the transform.
- `superpose(mode="structural")` matches residues by the shape of their local
  backbone rather than by sequence, for proteins too diverged for a sequence
  alignment to mean anything. On haemoglobin's alpha and beta chains it
  superposes 139 residues of the shared fold where sequence mode anchors 64.
  The reply now names the mode that produced it.
- `conservation()` scores an MMseqs2 alignment; `electrostatics()` computes a
  screened Coulomb potential, or runs APBS when it is installed.
- Scalar colouring for potential and conservation, as a gradient or as bands.

### Selections and core (Phases 1–2)

- Selections are named handles: `select()` takes PyMOL syntax for leaf
  predicates, and `combine()`, `near()` and `invert()` compose them.
- `extend`, `bymolecule`, `bound_to`, `neighbor` and `rank` all resolve, having
  been refused as unimplemented. Bond topology comes from residue templates and
  is derived on demand, since only these four selectors need it. Every count
  matches PyMOL 3.1.0 exactly on the same file. `alt` is still refused, but the
  refusal now names the tradeoff — every conformer can be loaded, at the cost
  of computing buried areas over atoms that overlap each other — rather than
  claiming it cannot be done.
- `backbone` includes `OXT`, the C-terminal carboxylate's second oxygen, which
  used to fall into `sidechain`. Four atoms per structure, and the reason
  PyMOL and protean disagreed about `backbone` on anything with a modelled
  C-terminus. Mol\*'s transpiler still excludes it; the difference is asserted
  in the differential suite rather than left to drift.
- `ss H`, `ss S` and `ss L` select secondary structure, which used to be
  refused outright. It is computed from backbone geometry with P-SEA rather
  than read from the file, so it answers the same way for a predicted model as
  for a deposited one — but it is not the DSSP-style criterion PyMOL and Mol\*
  use, and assigns slightly smaller elements than either. The difference is
  asserted in the differential suite and recorded in docs/backlog.md.
- Representations, colour themes, camera control, measurements and labels.
- Sessions save and load as gzipped `.protean` files that embed the structure.

### Fixed

- The wheel now carries the built viewer. It is a gitignored npm artifact and
  hatchling honours VCS ignores, so an installed protean had a server and no
  viewer.
- A visible tab now settles before replying: actions used to answer before
  Mol\* had built what they described, so a screenshot could photograph an
  empty canvas.
- `screenshot()` builds the image pass before capturing. The first capture of a
  session went through a freshly created pass and came back measurably worse
  than every identical one after it.
- `snapshot()` refuses an incomplete capture. At large sizes a renderer can
  return an image of exactly the right dimensions with most of it never
  written.
- `superpose()` applies its transform and displays the pair.
- The viewer and the analysis load the same assembly, and say so.
- A structure with alternate conformers is no longer reported as a mismatch.
  The analysis keeps one conformer per atom site and the viewer draws all of
  them, which on 5FJI is a 217-atom difference between two descriptions of the
  same molecule; the load reply now says so instead of declaring every count,
  buried area and potential in the session unreliable. A difference the
  conformers do not fully account for is still a mismatch.
- A distance must be greater than zero, in `near()` and in the grammar's
  `within`, `around` and `expand`. A non-positive radius used to answer with an
  empty set — or, for `expand`, the source unchanged — both of which read as
  results rather than as the rejected questions they are. `nan` and `inf` are
  refused with them.
- `backbone` and `sidechain` understand nucleic acids. `backbone` was protein
  N/CA/C/O only, so it found nothing in DNA and `sidechain` — "polymer and not
  backbone" — returned every atom of the molecule as though that were an
  answer. `backbone` is now the sugar-phosphate backbone as well, which leaves
  `sidechain` meaning the nucleobase: 258 and 228 atoms on 1BNA, matching both
  PyMOL and Mol\*'s transpiler.
- `elem` refuses a symbol that is not an element, with a suggested correction:
  `elem Zz` used to return 0 atoms and no complaint, which reads as "this
  structure has none of those" rather than "you misspelled it". A symbol is
  refused only if it is neither a real element nor present in the file, so a
  real element that is simply absent still answers 0.
