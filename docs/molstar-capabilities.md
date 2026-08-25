# What Mol\* can do that protean does not

Commissioned 2026-08-25, after `lens()` found that **Mol\*'s default fog has
been on since the beginning and has never been visible** — bit-identical to off
at its default of 15, measured with no tolerance. If one inherited default was a
no-op, others might be, and the soft-matter plan was written without anyone
reading Mol\*'s parameter surface at all.

Nine agents read the shipped source at `molstar` 5.11.0 in
`viewer/node_modules/molstar/lib`, the prebuilt UMD bundle, and protean at
`d4ca3e5`. Every claim carries a file and a line.

## How to read this

**Most of this is read, not rendered.** §5 lists what the audit could not settle
by reading, and that list is the honest one — given this project's history with
fog and with felt, those are exactly the measurements that should precede
building. Treat a §2 effort estimate as a plan, not a result.

## Corrections made after the audit

**The shadow rationale is false, in the audit and in protean's own source.**

The audit is right that `postprocessing.shadow` ships at `steps: 1,
maxDistance: 3` against maxima of 64 and 256, that protean pins those
(`dispatch.ts:344`), and that raising them is worth doing. Measured on the
`painting` preset at `steps: 16, maxDistance: 32`:

| | |
|---|---|
| molecule pixels changed | **48.6%**, darkening ~13.8 levels |
| off-molecule pixels changed | **0.0015%** |

So raising it is a real improvement to *self*-shadowing — half the molecule,
visibly deeper crevices — and it puts **nothing** on the ground.

It never could. Mol\*'s background is a flat colour, not geometry, and its
shadow pass is screen-space self-shadowing. There is nothing for a shadow to be
cast onto at any setting.

Which makes `server.py`'s own comment wrong — the buff ground is not "where the
cast shadow has somewhere to fall", because no cast shadow can fall anywhere.
The audit inherited that framing from protean's source and repeated it. **Both
the explanation and the code comment were plausible, repeated, and never
measured**, which is the same shape as the bake-off retraction and the fog
finding.

The knob survives the correction. The reason given for it does not.

---

All claims verified against the shipped source at HEAD `d4ca3e5`. Report follows.

---

# What Mol\* can do that protean does not

**Audited against** `molstar` 5.11.0 in `/Users/charlie/code/protean/viewer/node_modules/molstar/lib`, the shipped UMD bundle `viewer/public/molstar.js`, and protean at HEAD `d4ca3e5` (clean tree). Every claim below I re-read out of the source myself; where a prior reader was wrong about protean's own tree, I say so in §6.

---

## The answer

**Most of what protean is leaving on the table is one job, not thirty.**

protean's `show()` forwards exactly two representation parameters — `sizeFactor` and `alpha` (`viewer/src/dispatch.ts:1342-1345`). Its `color()` forwards *no* theme parameters at all unless the argument is a literal hex code (`dispatch.ts:3221-3228`). Roughly twenty of the most valuable unexposed knobs in Mol\* — the colour palettes, the B-factor range, the carbon colour, the ribbon shape, the surface probe size, flat shading, cutaways, the shader animation — are all blocked behind those same two missing dictionaries. Six readers each priced their find as "one handler" without noticing they share one prerequisite. Build the pass-through once and the rest of this list becomes call sites.

`docs/views.md` already hit this wall from the other side and wrote it down (line 373): the approved carbon-colour fix "needs `show()` to pass theme parameters, which it cannot today."

**The soft-matter plan's cost model is wrong in both directions, and the errors cluster by tier rather than scattering.** The tier it priced highest and gated behind a two-week prerequisite (surface-derived treatments) is the tier Mol\* has most already built — surfaces ship fully parameterised, and clipping a molecule open, which the plan buries inside an L-effort treatment, is two independent parameter sets that ship today. The tier it priced at 6-8 weeks (per-atom generated geometry: radiolaria, pom-poms, vacuum tubes) is the one the prebuilt bundle genuinely cannot do at any price. So the middle of the plan's schedule — roughly weeks 5 through 20 — is mostly work that either already exists or cannot be done from the bundle at all.

**And one defect worth acting on this week regardless:** protean's `painting` preset asks for a cast shadow and gets a contact smudge. Its own comment (`src/protean_mcp/server.py:4429-4430`) says the buff ground is "where the cast shadow has somewhere to fall" — but `_set_effects(shadow=True)` pins Mol\*'s defaults of `steps: 1, maxDistance: 3` (`dispatch.ts:344`), which is a single ray-march sample three units toward the light. That is the fog finding again, on a different parameter, in a shipped flagship view.

---

## 1. The prerequisite, stated plainly

Two functions, both one level deep, both destroying whatever they do not mention:

| Where | What it does today |
|---|---|
| `viewer/src/dispatch.ts:1342-1345` | builds `typeParams` from `sizeFactor` and `alpha` only |
| `viewer/src/dispatch.ts:3221-3228` | returns bare `{color}` unless the string starts with `#` |

Mol\* merges both with a **shallow** `Object.assign` — `structure-representation-params.js:59` for `typeParams`, `:27` for `colorParams`. That means passing `{material: {metalness: 1}}` silently wipes `roughness` and `bumpiness`. protean's `material()` is safe today only because it writes the whole group every time (`dispatch.ts:1791, 1822`). Any pass-through must merge whole groups, not partial ones.

Several of the highest-value parameters are additionally **mapped** — shaped `{name, params}` rather than a plain dict. Mol\* accepts a bare dict without complaint and changes nothing. This is the exact shape of the `cameraFog` bug protean already documents in `lens()`. A generic pass-through has to know which keys are mapped, or it will hand a model a silent no-op generator.

**Effort: 2-3 days.** Design, the whole-group merge, a mapped-shape guard, and read-back-rather-than-echo confirmation in the reply.

---

## 2. Ranked shortlist

Ranked by picture-per-day, accounting for the shared prerequisite. Items 3-6 and 9 are gated on §1.

### 1. Shadow that actually casts a shadow
**What it looks like:** one part of a molecule throwing a dark shape across another. The single strongest depth cue available short of path tracing, and roughly what a raking desk lamp does to a physical model.
**Parameter:** `postprocessing.shadow.params.{steps, maxDistance, tolerance}` — `mol-canvas3d/passes/shadow.js:21-25`. Defaults `steps: 1, maxDistance: 3, tolerance: 1.0`; `maxDistance` and `tolerance` are multiplied by `camera.scale` at `:62-63`, so they are **not** ångströms. `steps 16` with `maxDistance 32` is the setting that gives real cast shadows.
**Why first:** protean pins all three (`dispatch.ts:344`) and offers shadow as a bare on/off. The `painting` preset is depending on it right now.
**Effort: half a day.** Three optional arguments on `effects()`. Not gated on §1 — `effects()` already writes the whole `postprocessing` group correctly.

### 2. Shader wiggle and tumble — thermal motion for free
**What it looks like:** the molecule visibly breathes. Nearby atoms move together rather than jittering independently, so it reads as warmth rather than noise.
**Parameter:** `typeParams.animation` — a plain group defined at `mol-geo/geometry/animation.js:8-18`, attached to every drawable geometry (`mesh.js:564`, `spheres.js:236`, `cylinders.js:133`, `points.js:104`). `wiggleAmplitude` and `tumbleAmplitude` both default to **0**, so the whole group is inert until raised. `wiggleMode: 'position'` is the spatially-correlated one.
**Why it matters here:** protean already built `boil`, which reloads the structure for every pose and destroys unregistered representations (`server.py:3362-3366`). Mol\*'s version is a uniform on geometry that already exists, edits no coordinates, and self-drives its own frames — `scene.js:284-298` sets `hasAnimation` whenever amplitude, speed and frequency are all above zero, and `canvas3d.js:512-513` folds that into `shouldRender`. So no render pump. The scene loss the status doc records as inherent to `boil` is not inherent.
There is also a per-region data channel: `WiggleStructureRepresentation3DFromBundle`, id verified present in the shipped bundle.
**The trap nobody found:** `renderer.setTime` is called from exactly **one** place — `canvas3d.js:587`, inside the live tick. `ImagePass` never calls it. So a wiggling scene captured twice gives two different poses, and your render-differential test suite becomes non-deterministic. The fix is on the public API and unused: `canvas3d.resetTime(t)` (`canvas3d.js:620`, exported at `:1015`). Pin time before every capture.
**Effort: 2-3 days**, including the determinism fix. Not gated on §1 if you write it as a dedicated handler; gated if you route it through a generic pass-through.

### 3. Cutaways — open a molecule and look inside
**What it looks like:** the surface sliced open so a buried pocket, a channel interior, or a bound drug is visible, with the cartoon inside left whole. protean has **no clipping of any kind** today — I grepped for `cameraClipping` and `clip:` across `viewer/src/dispatch.ts` and found nothing, and `near()` (`server.py:852`) is a selection tool, not a clip.
**Parameter:** `typeParams.clip` — `mol-util/clip.js:25-38`. It is on `BaseGeometry.Params` (`mol-geo/geometry/base.js:92`), so *every* representation has it. Shape: `{variant: 'instance'|'pixel', objects: [{type, invert, position, rotation:{axis,angle}, scale, transform}]}`, where `type` is one of `none | plane | sphere | cube | cylinder | infiniteCone`.
**Pair it with** `typeParams.interior` (`mol-geo/geometry/interior.js:11-17`) — the colour and material of the cut face, which defaults to dark grey `0x4C4C4C`. That default is why a cut-open Mol\* surface reads as a black hole rather than a cutaway.
**Use this, not the camera slab.** The global alternative `cameraClipping` (§3) is silently wiped by any camera move — `camera.js:89` `getFocus` assigns `state.radius = r`, which is the same field, and protean has three tools that go through it. Clip objects live in world space and survive.
A sphere or infinite-cone window goes well past PyMOL's flat slab, which fits the brief.
**Effort: 3-4 days** on top of §1. The `ObjectList` shape means the tool's own API is most of the work.

### 4. Colour palettes on the fourteen index themes
**What it looks like:** every chain-coloured picture protean has ever made used the same 25-colour qualitative set. This is the difference between a fruit-salad assembly and a smooth viridis gradient across chains, or a constrained all-blue one.
**Parameter:** `colorParams.palette` — `mol-util/color/palette.js:18-33`. Mapped, two branches. `{name:'colors', params:{list:{kind, colors}}}` takes any ramp. `{name:'generate', params:{hue:[200,260], chroma, luminance, maxCount:75, ...}}` computes perceptually distinct colours inside a constrained box — an all-blue assembly, a pastel figure that takes black labels, near-greyscale for print. Spread into `chain-id.js:16` and thirteen more themes identically.
**Two traps.** A `ColorList` value is `{kind, colors}`, never a name string — `PD.ColorList` resolves a name at *definition* time only (`param-definition.js:84-94`), so `list: 'viridis'` throws. And the mapped shape must be complete or you get the `cameraFog` no-op.
**Also:** Mol\* ships 57 named colour lists (`mol-util/color/lists.js`) but they are **not** on the prebuilt bundle's export surface — `molstar.lib` exposes only `{structure, volume, shape, loci, math, plugin, extensions}`. About eight are harvestable out of registered themes' defaults; the rest must be copied into protean. protean's current substitute is four hand-written 3-stop ramps whose "viridis" is a 3-point approximation of a 51-point curve (`dispatch.ts:241-246`).
**Effort: 2 days** on §1, plus ~1 day to copy the colour tables.

### 5. The two colour defects `docs/views.md` already names
Both are one line each once §1 exists.

**B-factor ramp range.** "B-factor" is the crystallographer's number for how blurred each atom's position is — high means floppy, low means rigid. Mol\*'s `uncertainty.domain` is `PD.Interval([0, 100])` (`mol-theme/color/uncertainty.js:14`), but a well-refined structure has B-factors between about 5 and 40. Ramped over a fixed 0-100 the whole molecule lands in the first 40% of the colour list and comes out nearly monochrome — the picture looks like the theme failed. `docs/views.md` records exactly this: "A view coloured by B-factor uses only the cold half of its ramp... The fix is a domain on `color()`." It reaches four shipped paths at once: `preset('putty')`, `preset('plddt')`, `color_by_rmsf` and `color_by_conservation` all route through this theme.

**Carbon colour.** `element-symbol.carbonColor` defaults to `chain-id` (`mol-theme/color/element-symbol.js:29`), which is why `skeleton` and `spacefill` come out with arbitrarily-coloured carbons instead of the grey a textbook uses. `{name:'element-symbol', params:{}}` gives true grey; `{name:'uniform', params:{value, saturation:0, lightness:0}}` gives the PyMOL green-carbons convention. Mapped, so must be passed whole.
**Effort: 1 day for both**, on §1.

### 6. Outline threshold — the lever `docs/views.md` concluded did not exist
**What it looks like:** at 0.05 every bond, helix turn and surface ridge gets a line — the actual textbook-illustration look. At 1.0 only the outer silhouette survives — the clean sticker look.
**Parameter:** `postprocessing.outline.params.threshold` — `mol-canvas3d/passes/outline.js:23`, `PD.Numeric(0.33, {min: 0.01, max: 1, step: 0.01})`, consumed at `:54` as `50 * threshold * pixelRatio`.
`docs/views.md:791` says "The thinner outline does not exist. Mol\*'s `outline.scale` is `min: 1, step: 1`... so the floor *is* the default." Correct about `scale`, and it stopped one line short. `threshold` is on the very next line of the same file, and it is a bigger lever on how an outlined figure reads than `scale` is. protean pins it at 0.33 (`dispatch.ts:332`) and exposes only colour and scale.
**Effort: 2 hours.** One argument on `effects()`. Not gated.

### 7. The bundled backdrop artwork — one line in a build script
**What it looks like:** the molecule sitting inside a cell (a public-domain micrograph), or against a nebula skybox that a chrome material can actually reflect.
**Where it is:** `viewer/node_modules/molstar/build/viewer/images/` holds `cells.jpg` and six `nebula_*.jpg` faces — I listed them. The bundle asks for them at the relative URL `images/cells.jpg` (verified by grep on `viewer/public/molstar.js`). `viewer/package.json:7`'s `sync-molstar` copies only `molstar.js`, `molstar.css` and the LICENSE, and `src/protean_mcp/static/` has no `images/` directory.
**Why it fails silently:** `mol-canvas3d/passes/background.js:418-421` does `assetManager.resolve(...).run().then(...)` with **no** `.catch`. The 404 becomes an unhandled promise rejection, `img.src` is never assigned, `onload` never fires, and the background keeps whatever it had. Setting the preset would report success and change nothing.
protean's own plumbing already accepts an arbitrary image or skybox URL (`dispatch.ts:2082-2120`).
**Effort: 1 hour.** Add `build/viewer/images` to the copy, verify it renders.

### 8. Ribbon and surface shape
Two clusters of stock parameters that change what a molecule *is shaped like*, not just what colour it is.

**Ribbon** (`mol-repr/structure/visual/polymer-trace-mesh.js:23-32`): `aspectRatio` (default 5) is the ribbon's width-to-thickness — at 1 every helix becomes a round tube. `tubularHelices` (false) is the classic Richardson tube look. `arrowFactor` (1.5) sizes the arrowheads; 0 removes them. And `radialSegments` (16) is filed under "quality" but is a style switch in disguise: at 2 the builder draws a zero-thickness flat ribbon, at 4 a boxy square-section one.

**Surface** (`mol-math/geometry/molecular-surface.js:44-46`, `mol-repr/structure/visual/util/gaussian.js:13-16`): `probeRadius` (1.4 — the size of a water molecule) at 3-4 smooths crevices into a shape-reading blob. `radiusOffset` (0) at 2-3 melts a whole domain into one smooth envelope; Mol\*'s own description says it is "useful to create coarse, low resolution surfaces." That coarse-envelope picture is unreachable in protean by any other route.

Add `flatShaded` (`mesh.js:556`) for a faceted low-poly look, and `doubleSided` / `transparentBackfaces` (`:554, :560`) — with both off, which is the default, a see-through surface looks like a hollow film rather than a volume. That combination is why protean's `opacity()` looks wrong.
**Effort: 1-2 days** on §1.

### 9. Exploded and unwound assemblies
**What it looks like:** a capsid or a ribosome coming apart so you can see the subunit interfaces a packed assembly hides. Or the reverse: an assembly collapsing back onto the single deposited copy it was generated from, which is the clearest way to show that a 60-mer is one protein repeated.
**Parameter:** `StateTransforms.Representation.ExplodeStructureRepresentation3D` with `{t: 0..1}` (`mol-plugin-state/transforms/representation.js:201-206`); `UnwindStructureAssemblyRepresentation3D` at `:165-170`. Both transform ids **verified present in the shipped bundle**, and `StateTransforms` is on the global at `molstar.lib.plugin`.
Because `t` is a transform parameter rather than an animation, a single value gives a reproducible still — which is what the differential test suite needs.
**Caveat:** these attach to one representation node, not to the structure, so two representations of the same molecule explode independently and drift apart.
**Effort: 1-2 days.** Not gated on §1. Needs `main.ts`'s `declare const molstar` widened.

### 10. Non-covalent contacts, drawn correctly
**What it looks like:** the dashed lines showing what holds a drug in its pocket — hydrogen bonds, salt bridges, ring stacking. Mol\* computes these itself, no network.
**Status:** `show(representation='interactions')` already passes protean's name check today, because protean validates against the live registry (`dispatch.ts:1112-1113`). It draws almost nothing useful, for two independent reasons — both listed in §3 below, both fixable with parameters. This is a picture protean nearly has and does not know it.
**Effort: 1-2 days** on §1, mostly for `includeParent`/`parentDisplay` and switching on the three off-by-default contact kinds.

**Just outside the ten:** geometry export (`extensions/geo-export/controls.js:17-24` — glTF, STL, OBJ, USDZ of whatever is on screen). Not a picture, but a deliverable no molecular MCP surface offers: 3D-print the pocket, drop the glTF into Blender, hand the USDZ to a phone for AR. Reaching it relies on an implementation detail of Mol\*'s React base class, so pin the version if you build it.

---

## 3. Things that look like features and are not

The calibration case you gave me is the template. `cameraFog` is on by default at intensity 15, and commit `d4ca3e5` measured it bit-identical to *off* at 5, 15 and 25 — first flicker at 40, substantial only from 60. The mechanism explains it: `fogNear = cameraDistance - normalizedFar * (-(50 - fog)/50)` (`mol-canvas3d/camera.js:432-433`), so at 15 the fog band is confined to the last 15% of the scene's depth, where there is essentially no geometry. **"The default is on" and "the default does something" are different claims.** Here is the rest of that family.

### Defaults that are on and do nothing

| Parameter | Default | Why it is a no-op |
|---|---|---|
| `postprocessing.shadow` | `steps: 1, maxDistance: 3` (`shadow.js:22-23`) | One ray-march sample, three camera-scaled units toward the light. A contact ring, not a cast shadow. protean's `painting` preset depends on the latter. |
| `postprocessing.bloom` | mode `'emissive'` (`bloom.js:29`) | Glows only where a representation has `emissive > 0`. On an ordinary scene it draws literally nothing. protean already reports this honestly as `bloom_will_show`; the unexposed fix is `mode: 'luminosity'`, which glows anything brighter than a threshold with no material change. |
| `cameraClipping.radius` | `100`, declared `max: 99` (`canvas3d.js:68`) | The default is **outside its own declared maximum**, and is the exact value the setter refuses: `(sceneRadius/100) * (100 - 100) = 0`, which fails the `radius > 0` guard at `canvas3d.js:1071-1075`. A deliberate no-op. The read-back does not round-trip either (`:849-851` computes `100 - round(...)`, reporting ~0 for an unclipped scene) — so protean's read-back-rather-than-echo discipline would report failure here when nothing failed. |
| `sceneRadiusFactor` | 1, range 1-10 | Only feeds `radiusMax`, which only reaches the picture when `cameraClipping.far` is false. On its own, factor 1, 3 and 10 give identical output. |

### Parameters that cannot change a saved PNG

protean only ever looks at PNGs, so these would move the screen, report success, and leave the file byte-identical.

- **`multiSample`** — pinned at *pass creation* by the screenshot helper (`mol-plugin/util/viewport-screenshot.js:136-151`: `mode: 'on'`, `sampleLevel: 4`). Worse, the `imagePass` getter at `:155-166` re-sends `cameraHelper`, `transparentBackground`, `postprocessing`, `marking` and `illumination` but **not** `multiSample` — so it cannot take effect even after the first capture.
- **`occlusion.samples` and `.resolutionScale`** — overridden to 128 and the device pixel ratio on every capture (`viewport-screenshot.js:117-119`).
- **`hiZ`** — works through `renderer.setOcclusionTest`, and `passes/image.js:86` explicitly calls `setOcclusionTest(null)`.
- **`viewport`** — `ImagePass.render` does `Viewport.set(this._camera.viewport, 0, 0, width, height)` and ignores it. Not a route to a fixed aspect ratio in a figure.
- **`checkeredTransparentBackground`** — writes a CSS `background-image` on the canvas element (`canvas3d.js:273-287`). Invisible to any capture.
- **`camera.stereo`** — this one is protean's signature failure delivered by a Mol\* parameter. `canvas3d.js:538, :650` use `stereoCamera` for the live canvas; `passes/image.js:81-84` renders through its own plain `this._camera` and never touches it. Flip the flag and you get **stereo on screen and mono in the file**. A real stereo pair means capturing twice with the camera offset by hand and compositing — Python-side work protean is already good at.

### Silent no-ops from wrong shape

Mol\*'s **mapped** parameters look like plain dicts and are not. A bare dict is accepted and ignored, every time, with no message:

- `cameraFog: {intensity: 60}` → nothing. Needs `{name:'on', params:{intensity:60}}`. protean's `lens()` gets this right and documents why.
- `{providers: {hydrophobic: {distanceMax: 4}}}` → nothing. Needs `{hydrophobic: {name:'on', params:{distanceMax:4}}}` (`interactions.js:185-192`).
- `{mode: 'palindrome'}` on trajectory playback → nothing (`animation/built-in/model-index.js:16-33`).
- `{cameraTransition: 250}` on a state snapshot → nothing (`mol-plugin/state.js:202-208`).
- `camera.helper.axes: {name:'on', params:{}}` → **nothing, and NaN internally**. `CameraHelper.setProps` dereferences `props.axes.params.scale` unguarded (`helper/camera-helper.js:92-118`). The full 22-field group is required.

Related: a hex **string** passed where Mol\* wants a `Color` paints pure black and reports success. protean's `colorParams` converts with `parseInt` so it is safe today, but any new handler forwarding a user's `'#rrggbb'` straight into a colour slot will render black.

### protean's own reverse flags

Four places where protean reports success and the picture disagrees.

**`show(size=n)` on `molecular-surface` or `gaussian-surface`.** Passes protean's size gate and returns `size_validated: true` while changing nothing. Both representations *do* declare `sizeFactor` — so `dispatch.ts:1318-1325` lets it through — but I grepped, and that `sizeFactor` exists **only** on the wireframe visual (`molecular-surface-wireframe.js:20`, `gaussian-surface-wireframe.js:17`). The default `visuals` is the mesh (`molecular-surface.js:23`, `gaussian-surface.js:23`), and the mesh files contain no `sizeFactor` at all. That gate was built specifically to catch this class of failure, and this case walks straight through it. Surface thickness comes from the size theme and `probeRadius`/`radiusOffset` instead.

**`spin(speed=-0.1)` is refused by protean and promised by protean.** `server.py:3169` tells the model "Mol\* offers it in the range -2 to 2 for spinning, negative for the other direction." `dispatch.ts:1588-1590` throws "Spin speed must be above 0". Mol\* agrees with the docstring: `trackball.js:68` is `PD.Numeric(0.1, {min: -2, max: 2})`. A model following protean's own tool description gets an error. Separately, `dispatch.ts:1605-1607` hardcodes `axis: [0,-1,0]` in both branches — the axis is in *camera* space, so `[1,0,0]` tumbles the molecule end over end and `[0,0,1]` rolls it in the picture plane. Three quite different motions from one parameter protean never accepts.

**`path_trace()` after `effects(outline=True)` silently drops the outline.** `illumination.ignoreOutline` defaults to `true` (`mol-canvas3d/passes/illumination.js:58`) and gates the outline in both the transparent path (`:118`) and the compose path (`:287`). protean's `path_trace` writes five illumination keys and not this one. One key fixes it.

**The `define_elements` justification comment is factually wrong.** `dispatch.ts:2649-2652` states: "Its `element-symbol` theme has exactly one parameter — `carbonColor` — and every other element comes from a fixed CPK table with no way in." The shipped theme has **four** parameters (`element-symbol.js:28-49`), and `colors` is a mapped param whose `custom` branch is a full group of one colour per element — all 118 of them. `define_elements` may still be worth keeping for its friendlier named-palette API, but the stated reason for its existence is not true of the code, and this is exactly the kind of comment that misleads the next change. Note also that `lightness` defaults to **0.2**, not 0 — so protean has never drawn Mol\*'s true unlightened element colours.

### Two visuals that are switched on and drawing nothing, forever

The `interactions` representation defaults `visuals` to `['intra-unit','inter-unit','bridge']` (`representations/interactions.js:26`) — but the only bridge provider, water-bridges, is default **off** (`interactions/interactions.js:218` calls `getBridgeProviderParams([])`). A visual is enabled, running, and producing zero geometry with no message.

And `hydrophobic`, `ionic` and `weak-hydrogen-bonds` are all commented out of the default-on list (`interactions/interactions.js:191-202` — I read the commented lines). So asking for a hydrophobic pocket or a salt-bridge network today draws nothing.

Compounding both: the interactions property is `type: 'local'` and `includeParent` defaults `false` (`representations/shared.js:14`). So `show(representation='interactions', selection='resn ATP')` computes contacts **inside the ligand** and draws almost nothing — silent success in protean's exact signature shape.

---

## 4. What the soft-matter plan gets wrong

| Plan item | Plan's estimate | What the source says |
|---|---|---|
| **§6.4 shared prerequisite** — "surface generation, seam extraction, region growing, stable re-tessellation... budget ~2 weeks," gating all nine G3 treatments | 2 weeks, gates 9 treatments | **Wrong in both directions.** Surface generation ships fully parameterised (`mol-math/geometry/molecular-surface.js:44-46`) and protean already draws it. Seam-curve extraction and region growing are *not reachable at all* from the prebuilt bundle. Half the prerequisite was done years ago; the other half cannot be done. This is the single largest cost error because of what it gates. |
| **§4.3** — "budget one week in Phase 1 to add a user post-processing pass slot; five G4 treatments depend on it" | 1 week | **No hook exists and no week buys one.** `Passes` is constructed inside `Canvas3D.create` (`canvas3d.js:163`) with no injection point, and `PostprocessingParams` is a closed key set composited by one `#define`-driven fragment shader (`postprocessing.js:102-137`). The options are an upstream PR or a fork. Moot anyway — protean shipped three of the five as capture-time Pillow finishes with zero engine work. |
| **SD-04 Geode** — clipping buried inside an L-effort treatment behind the 2-week prerequisite | L (2-4 weeks) | Clipping is **entirely parameters**, and protean has none of any kind. Two independent routes ship: `cameraClipping.{radius, far}` globally (`canvas3d.js:68-69`), and per-representation `clip` objects (`mol-util/clip.js:25-38`), plus `interior` for the cut face. Only the crystal terminations are out of reach. See shortlist §3. |
| **SD-09 Laser-cut contour model** — M, plus writing a DXF exporter | M + exporter | Both halves ship. `plane` is a registered structure representation drawing a coloured cross-section through the molecule, with `offset` sweeping it along an axis (`plane-image.js:27-49`); protean's `show` already accepts the name and can reach none of its controls. And `geo-export` ships glTF/STL/OBJ/USDZ of the actual scene — four formats against the one DXF the plan budgeted writing. |
| **SD-02 Stained glass** — "robust seam extraction is the crux," risk medium-high; §10 fallback is screen-space edge detection | L, high risk | The §10 fallback is **one unexposed number**: `outline.threshold` (`outline.js:23`). Dropped toward 0.05, every crease on a spacefill gets a line — and on a spacefill those creases *are* the intersection curves. The glass half also has an unexposed knob protean never touches: `renderer.xrayEdgeFalloff` (`mol-gl/renderer.js:43`), the one number deciding what `shading(style='xray')` actually looks like. |
| **SS-04 Pointillism** — "stipple pass" | S | No pass needed. `point` is a registered built-in and `pointStyle: 'fuzzy'` (`points.js:93-105`) turns each atom into a soft gaussian dot in one parameter. `pointSizeAttenuation: true` makes near dots larger than far ones — the plan's depth→density binding, delivered geometrically. Both defaults are the flattest possible: `'square'` with attenuation off. |
| **SS-02 / SS-03 / SS-06** — M each, "requires the pass slot" | 3× M + pass slot | All three shipped as Pillow arithmetic on the captured PNG. No render pass, no engine work. |
| **SM-03 Brutalist concrete** — "cheap: grain bump + hard raking shadow" | S | Cheap *and* currently broken. The raking shadow is the whole treatment and protean's shadow is one sample over three units. See §3 and shortlist §1. |
| **§3.3** — DC-SASA and DC-DEPTH "pulled into Phase 0 despite moderate cost... this is why Phase 0 runs 3-4 weeks rather than 2-3" | +1 week | The extra week buys nothing. Shrake-Rupley SASA ships parameterised (`shrake-rupley.js:16-21`), is attached by a **default** plugin behavior (`mol-plugin/spec.js:96`), and that behavior registers an `accessible-surface-area` colour theme. Since protean validates theme names against the live registry, **`color("accessible-surface-area")` already works today** and nobody has noticed. |
| **G1 sphere-substitute tier** (radiolaria, pom-pom, wound yarn, urchin, vacuum tube, PCB, breadboard) | 6-8 weeks | Genuinely unreachable — the geometry builders are not on the bundle's export surface. The escape route neither the plan nor the review named is the PLY/OBJ/VTP shape loader (`mol-plugin-state/formats/shape.js:10-75`): Python computes the mesh, Mol\* draws it, using the same three calls `load_volume` already makes. But it produces a `ShapeRepresentation`, so picking yields `ShapeGroup.Loci` and `select`/`color`/`focus` stop working — which §4.1 calls an automatic reject. Keep these parked; the reason is now the loci fork alone. |
| **G6 Publication-grade still** — "4K offscreen render path, deterministic" | assumed achievable by declaration | Harder. `antialiasing` defaults to SMAA (`postprocessing.js:120`) and protean cannot reach it, so an edge-smoothing pass sits inside every differential comparison. `multiSample` cannot be changed after the first capture at all. Determinism is not a setting you can declare here. |
| **G4 / §9 MolViewSpec** — "requires an MVS custom-node proposal; open that conversation early, upstream dependency" | upstream negotiation | Much cheaper on capability — the primitives vocabulary and a full declarative animation tree with 19 easings already ship on the global. But `loadMVS` calls `snapshot.clear()` then applies a fresh snapshot (`mvs/load.js:81, :92`), **replacing the entire state tree**. It is a scene generator, never an overlay. It can carry a treatment; it cannot add an arrow to protean's existing scene. |
| **§8 Picking integrity** — "non-negotiable" automated test | correctly prioritised, incompletely specified | Two silent failure modes the plan does not name: `visuals: []` or a misspelled visual name renders zero geometry with no error (`mol-repr/representation.js:229, 241`), and an unknown representation name returns `EmptyRepresentationProvider` whose `getParams` is `() => ({})` (`:32-42, :78`) — so every size and opacity gate passes vacuously. protean's `checkName` is load-bearing. |
| **TM-01 Stop-motion boil** — "highest charm-per-line-of-code in the plan" | S, built | Built, and Mol\* ships a cheaper mechanism that fixes its one recorded defect. See shortlist §2. |

---

## 5. What I could not settle by reading

Everything below is read-from-source plus bundle-grep. None of it was rendered. Given this codebase's history with fog and with felt, these are exactly the measurements that should precede building.

1. **Is Mol\*'s wiggle visible at protean's usual framing, and at what amplitude?** The parameter is `0..5` with default 0. I have no idea whether 0.5 is a shimmer or an earthquake on a 25 Å protein. *Settle it:* sweep amplitude 0.1 / 0.5 / 1 / 2 / 5 on 1UBQ cartoon, capture with `resetTime` pinned, diff against amplitude 0.
2. **Does `shadow` at `steps: 16, maxDistance: 32` actually read at default framing?** Both numbers are multiplied by `camera.scale`, so 32 is not 32 ångströms and I cannot predict the result. *Settle it:* sweep `maxDistance` 3 / 8 / 16 / 32 / 64 at `steps` 1 / 8 / 16 and measure frame fraction changed, the same way fog was measured.
3. **Does a `clip` plane on a surface give a readable cutaway once `interior` is set?** I can read the parameter shape but not the picture. *Settle it:* one plane object through a molecular surface with `interior.color` at three values, screenshot each.
4. **Is `dpoit` (correct depth-sorted transparency) supported on your GPU?** `DrawPass.setTransparency` downgrades to `'blended'` when unsupported while `props.transparency` still reads `'dpoit'` (`passes/draw.js:20-36`). The only honest read-back is `plugin.canvas3dContext.passes.draw.transparencyMode`. *Settle it:* set it and read that field, not the props.
5. **Do the background images load once copied?** I verified the files exist and the bundle asks for them at `images/*.jpg`, but not that the resolved path matches how protean serves `static/`. *Settle it:* copy them, set the preset, screenshot.
6. **Whether the `animation` group actually reaches a live representation through `typeParams`.** It is on `Mesh.Params`/`Spheres.Params`/`Cylinders.Params` and both uniform names are in the shipped bundle, and the `typeParams` merge is a plain `Object.assign` — but I did not load the registry and confirm the key survives. *Settle it:* one call, then read `plugin.canvas3d.props` back.

---

## 6. Where the readers disagreed

**Two of the six readers were wrong about protean's own tree while being right about Mol\*.** Both are worth knowing before you act on the rest.

- The **postprocessing reader** claimed `dispatch.ts:1958` sets `props.cameraFog = {intensity: fog}` — "the exact bare shape the comment four lines above it warns is silently ignored." **It does not.** I read the shipped handler: it builds `{name:'on'|'off', params:{intensity}}` and maps `fog=0` to the off branch, with a comment explaining exactly why. That reader appears to have been looking at a pre-`d4ca3e5` tree.
- The **themes reader** claimed `git status` shows three uncommitted scratch-file deletions. **It is clean at `d4ca3e5`.**
- The **extensions reader** wrote "protean has a `near` tool that clips." **It does not** — `near()` (`server.py:852`) is a selection tool returning atoms within a distance. protean has no clipping of any kind, which makes the clipping finding *stronger*, not weaker.

**A genuine disagreement about reachability, now resolved.** The geometry reader said Mol\*'s state transforms are not reachable from the prebuilt bundle; the extensions reader said they are. Both were partly right and the distinction matters: I grepped `viewer/public/molstar.js` and found `explode-structure-representation-3d`, `unwind-structure-assembly-representation-3d`, `spin-structure-representation-3d`, `wiggle-structure-representation-3d`, `emissive-...-from-bundle` and `substance-...-from-bundle` all present, and `StateTransforms` is exported on `molstar.lib.plugin`. The **MVS** primitive transforms are the ones that are not — they are not in `StateTransforms`, and `StateBuilder.apply` needs a transformer object rather than a name. So: the assembly and per-loci transforms are in hand; MVS primitives are not, except through the whole-scene-replacing `loadMVS`.

**A systematic error rather than a disagreement.** Every one of the six readers tagged its finding `[one handler]` or `[a handler plus care]` in isolation. None noticed that about twenty of them are blocked on the same two missing dictionaries described in §1. Costed individually the list looks like twenty small jobs; costed correctly it is one seam plus twenty trivial call sites. That is the single most consequential correction in this report, and it is the reason the shortlist is ordered the way it is.
