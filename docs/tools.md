# Tool reference

All 68 tools protean registers, grouped by what you would be
doing when you reach for one.

**This page is generated** from the decorators in
`src/protean_mcp/server.py` by
[`docs/generate/tool_reference.py`](generate/tool_reference.py). Edit the
docstrings, not this file.

Each tool's full argument documentation lives in its docstring, which is
what your model sees. `capabilities()` reports the live value lists for
representations, colour themes, size themes, lighting rigs, shading
styles, material finishes, gradients, presets and path-trace quality —
read off the running Mol\*, so it is the authority rather than any table.

> **Values are checked, not guessed.** No style argument is an `enum` in
> the generated JSON schema — they are plain strings. What protects you is
> the other end: an unknown value is refused *by name, with the complete
> list of valid ones attached*, rather than quietly drawing nothing.

## Contents

- [Session](#session) — 6 tools
- [Selections](#selections) — 6 tools
- [Display](#display) — 8 tools
- [One-call views](#one-call-views) — 8 tools
- [Custom themes](#custom-themes) — 2 tools
- [Camera](#camera) — 7 tools
- [Analysis](#analysis) — 5 tools
- [Scalar colouring](#scalar-colouring) — 3 tools
- [Style](#style) — 7 tools
- [Capture](#capture) — 7 tools
- [Trajectories](#trajectories) — 4 tools
- [Volumes](#volumes) — 5 tools

---

## Session

### `open_viewer`

```python
open_viewer(timeout: float = 20, reveal_url: bool = False)
```

Launch the protean viewer in a browser tab and wait for it to connect.

### `fetch_structure`

```python
fetch_structure(identifier: str, source: str = 'auto', name: str | None = None, assembly: str = 'biological')
```

Fetch a structure and load it into the viewer.

### `clear_viewer`

```python
clear_viewer()
```

Remove all loaded structures and volumes from the viewer.

### `save_session`

```python
save_session(path: str, overwrite: bool = False)
```

Save the whole scene to a .protean file.

### `load_session`

```python
load_session(path: str)
```

Restore a scene previously written by save_session().

### `capabilities`

```python
capabilities()
```

List the representation and colour-theme names this viewer accepts.

---

## Selections

### `select`

```python
select(selection: str, name: str = 'sele', limit: int = 200)
```

Resolve a PyMOL-syntax selection into a named handle.

### `combine`

```python
combine(operation: str, of: list[str], name: str)
```

Build a handle from existing ones: union, intersect or subtract.

### `near`

```python
near(of: str, radius: float = 5.0, whole_residues: bool = True, exclude_self: bool = True, name: str = 'near')
```

Atoms within a distance of an existing handle.

### `invert`

```python
invert(of: str, name: str)
```

Everything the given handle does not contain.

### `list_selections`

```python
list_selections()
```

The named handles in this session, with sizes and where each came from.

### `remove`

```python
remove(name: str = 'sele')
```

Delete a named selection and its representations from the scene.

---

## Display

### `show`

```python
show(representation: str = 'cartoon', selection: str | None = None, handle: str | None = None, color: str | None = None, size: float | None = None, opacity: float | None = None, pickable: bool | None = None, name: str = 'sele')
```

Display a selection, given either a handle or a selection string.

### `hide`

```python
hide(name: str = 'sele')
```

Hide a named selection without discarding it; unhide() brings it back.

### `unhide`

```python
unhide(name: str = 'sele')
```

Show a selection previously hidden with hide().

### `color`

```python
color(color: str, name: str = 'sele')
```

Recolour an existing named selection.

### `size`

```python
size(size: str, name: str = 'sele')
```

Set what decides the *width* of an already-displayed selection.

### `opacity`

```python
opacity(opacity: float, name: str = 'sele')
```

Make an already-displayed selection transparent.

### `label`

```python
label(name: str = 'sele', level: str = 'residue')
```

Draw text labels on a named selection.

### `measure`

```python
measure(kind: str, names: list[str])
```

Add a distance, angle, or dihedral between named selections.

---

## One-call views

### `ligand_view`

```python
ligand_view(resn: str, around: float = 5.0)
```

Draw a bound ligand and the residues that line its pocket.

### `pocket_view`

```python
pocket_view(resn: str, around: float = 5.0)
```

Show the cavity a ligand sits in, as a surface.

### `interface_view`

```python
interface_view(chain_a: str, chain_b: str)
```

Draw two chains apart and pick out where they touch.

### `mutation_view`

```python
mutation_view(mutations: str, chain: str | None = None)
```

Draw the residues named in a mutation string, checking they are those.

### `crosslink_view`

```python
crosslink_view(distance: float = 2.5)
```

Pick out what holds a fold together: disulfides and metal sites.

### `pharmacophore_view`

```python
pharmacophore_view(resn: str)
```

Colour a ligand's atoms by what each can do: donate, accept, or be greasy.

### `conservation_view`

```python
conservation_view(chain: str | None = None, mode: str = 'gradient', representation: str | None = None, scale: str = 'relative')
```

Colour a chain by how conserved each position is. Blue is conserved.

### `electrostatic_view`

```python
electrostatic_view(method: str = 'auto', ph: float = 7.0, ionic_strength: float = 0.15, spacing: float = 1.0, padding: float = 10.0, domain: list[float] | None = None, selection: str = 'polymer')
```

Show the charge on a molecule's surface: red acidic, blue basic.

---

## Custom themes

### `define_field`

```python
define_field(name: str, values: list[dict[str, Any]], key: str | None = None, domain: list[float] | None = None, palette: str = 'blue-white-red', sizes: list[float] | None = None)
```

Register a per-residue number as something colour() and size() can use.

### `define_elements`

```python
define_elements(name: str = _ELEMENT_THEME, colors: dict[str, str] | None = None)
```

Register a colour theme that paints each element a colour you choose.

---

## Camera

### `focus`

```python
focus(name: str = 'sele')
```

Zoom the camera to a named selection, returning the resulting camera target.

### `orient`

```python
orient()
```

Align the camera to the structure's principal axes.

### `reset_view`

```python
reset_view()
```

Reset the camera to frame the whole scene.

### `lens`

```python
lens(projection: str | None = None, fog: float | None = None)
```

How the camera sees: the projection, and how far the distance fades.

### `spin`

```python
spin(mode: str = 'spin', speed: float | None = None, angle: float | None = None)
```

Set the viewer turning on its own, for looking rather than capturing.

### `keyframe`

```python
keyframe(name: str, remove: bool = False)
```

Remember where the camera is now, under a name.

### `list_keyframes`

```python
list_keyframes()
```

The camera positions saved so far, in the order a timeline will use.

---

## Analysis

### `interface`

```python
interface(chain_a: str, chain_b: str, identifier: str | None = None, contact_limit: int = 200, name_a: str = 'iface_a', name_b: str = 'iface_b', copy: int | None = None)
```

Describe the interface between two chains: buried area and contacts.

### `superpose`

```python
superpose(mobile: str, target: str, mobile_chain: str | None = None, target_chain: str | None = None, mode: str = 'sequence', show: bool = True, mobile_suffix: str = '_2')
```

Superpose one structure onto another and report how well it fits.

### `conservation`

```python
conservation(chain: str | None = None, conserved_percentile: float = 25.0, variable_percentile: float = 75.0, name_conserved: str = 'conserved', name_variable: str = 'variable', use_env: bool = True, force_refresh: bool = False, limit: int = 200)
```

Score a chain by evolutionary conservation and register the extremes.

### `electrostatics`

```python
electrostatics(method: str = 'auto', ph: float = 7.0, ionic_strength: float = 0.15, spacing: float = 1.0, padding: float = 10.0, handle: str | None = None, path: str | None = None, limit: int = 50, overwrite: bool = False)
```

Electrostatic potential around the loaded structure, in kT/e.

### `sasa`

```python
sasa(selection: str | None = None, probe_radius: float = 1.4, limit: int = 0)
```

How much of each residue the solvent reaches, and how deep the rest sits.

---

## Scalar colouring

### `color_by_potential`

```python
color_by_potential(handle: str = 'sele', path: str | None = None, domain: list[float] | None = None, palette: str = 'red-white-blue')
```

Colour a displayed selection by an electrostatic potential grid.

### `color_by_conservation`

```python
color_by_conservation(chain: str | None = None, mode: str = 'gradient', representation: str | None = None, scale: str = 'relative', bins: int = 7, palette: str = 'conservation', prefix: str = 'cons', hide_others: bool = True)
```

Colour the structure by the conservation scores from conservation().

### `color_by_rmsf`

```python
color_by_rmsf(representation: str = 'cartoon', scale: str = 'relative', hide_others: bool = True)
```

Colour the structure by how much each atom moves across the trajectory.

---

## Style

### `preset`

```python
preset(name: str, handle: str | None = None)
```

Apply a named recipe: lighting, effects, shading and materials at once.

### `background`

```python
background(color: str | None = None, transparent: bool | None = None, gradient: str | None = None, gradient_from: str | None = None, gradient_to: str | None = None, image: str | None = None, skybox: str | None = None, blur: float | None = None)
```

Set the canvas background: a flat colour, a gradient, or nothing at all.

### `lighting`

```python
lighting(rig: str = 'standard', intensity: float | None = None, ambient: float | None = None, exposure: float | None = None)
```

Light the scene with a named rig.

### `effects`

```python
effects(outline: bool | None = None, outline_color: str | None = None, outline_scale: float | None = None, occlusion: bool | None = None, shadow: bool | None = None, depth_of_field: bool | None = None, bloom: bool | None = None, sharpening: bool | None = None)
```

Switch screen-space effects on or off.

### `shading`

```python
shading(style: str, name: str = 'sele', cel_steps: int | None = None)
```

Change how a displayed selection is shaded.

### `material`

```python
material(finish: str = 'matte', name: str = 'sele', metalness: float | None = None, roughness: float | None = None, emissive: float | None = None, bumpiness: float | None = None, bump_frequency: float | None = None)
```

Give a displayed selection a surface finish.

### `path_trace`

```python
path_trace(enabled: bool = True, quality: str = 'standard', bounces: int | None = None, shadows: bool | None = None, denoise: bool | None = None)
```

Switch the renderer to Mol*'s progressive path tracer.

---

## Capture

### `screenshot`

```python
screenshot(path: str | None = None, overwrite: bool = False)
```

Capture the current viewport as a PNG.

### `snapshot`

```python
snapshot(path: str, column: str | None = None, width_mm: float | None = None, dpi: int = 300, format: str = 'png', transparent: bool | None = None, crop: bool = False, finish: str | None = None, overwrite: bool = False)
```

Save a publication-resolution figure at a real physical size.

### `turntable`

```python
turntable(directory: str, frames: int = 36, width: int = 1200, degrees: float = 360.0, transparent: bool | None = None)
```

Capture a numbered frame sequence orbiting the structure.

### `boil`

```python
boil(directory: str, frames: int = 24, amplitude: float = 0.45, hold: int = _BOIL_HOLD, width: int = 1200, seed: int = 0, transparent: bool | None = None, trails: bool = False)
```

Redraw the molecule every few frames, slightly differently — a stop-motion boil.

### `record_trajectory`

```python
record_trajectory(directory: str, width: int = 1200, stride: int = 1, transparent: bool | None = None)
```

Capture one image per trajectory frame, ready to encode.

### `record_timeline`

```python
record_timeline(directory: str, frames: int = 60, width: int = 1200, easing: str = 'ease-in-out', transparent: bool | None = None)
```

Capture a camera move through the saved keyframes.

### `movie`

```python
movie(directory: str, path: str, fps: int = 30, overwrite: bool = False)
```

Encode a directory of captured frames into a movie.

---

## Trajectories

### `load_trajectory`

```python
load_trajectory(path: str, stride: int = 1, max_frames: int = 100)
```

Lay a coordinate trajectory onto the structure already loaded.

### `frame`

```python
frame(index: int)
```

Show one frame of the loaded trajectory.

### `rmsf`

```python
rmsf(per: str = 'residue', limit: int = 50)
```

Per-atom fluctuation across the trajectory, as numbers.

### `rmsd_series`

```python
rmsd_series(reference: int = 0)
```

RMSD of every frame against one of them, after superposing onto it.

---

## Volumes

### `load_volume`

```python
load_volume(path: str, name: str | None = None, format: str = 'auto', provenance: str = 'unknown')
```

Load a density map into the viewer and report what it actually holds.

### `isosurface`

```python
isosurface(name: str, level: float, unit: str = 'sigma', style: str = 'surface', opacity: float | None = None)
```

Contour a loaded volume at `level` and draw it.

### `volume_info`

```python
volume_info(name: str)
```

Report a loaded volume's dimensions and value statistics.

### `list_volumes`

```python
list_volumes()
```

List the volumes currently loaded, with their statistics and provenance.

### `remove_volume`

```python
remove_volume(name: str)
```

Remove one loaded volume from the viewer.

---

## See also

- [Selections](selections.md) — the language every `selection` argument takes
- [The gallery](gallery.md) — what each style value looks like
- [The cookbook](cookbook.md) — these tools in combination
- [Troubleshooting](troubleshooting.md) — what a refusal means
