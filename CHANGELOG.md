# Changelog

Notable changes to protean. Versions follow [semantic versioning](https://semver.org);
nothing is released yet, so everything below is unreleased.

## Unreleased

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
