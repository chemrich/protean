# Changelog

Notable changes to protean. Versions follow [semantic versioning](https://semver.org);
nothing is released yet, so everything below is unreleased.

## Unreleased

### Views

- **`default` is the way back.** Every drawing view hides the scene the load
  built and replaces the one handle they share, so a run of them left no way to
  the picture you started from — watched go wrong, eight clicks in. It restores
  what is *drawn* and leaves lighting and ground alone: a "default" that
  silently reset carefully built lighting because someone wanted the cartoon
  back would be a worse surprise than the one it fixes.

- **Three views that take an argument**, which is why they are tools rather
  than menu entries — a button has nothing to type into.

  `ligand_view("GLC")` takes the name a caller actually has where `active-site`
  wanted a handle, draws the ligand and the residues lining its pocket, and
  reports which ligand, how many copies and how many residues line it. Refused
  when the structure does not contain it, naming what is bound instead.

  `interface_view(a, b)` puts two chains down in flat contrasting colours and
  brings the contact residues up as sticks. Refused when the chains do not
  touch, rather than drawing an empty highlight over an ordinary two-colour
  cartoon — which looks like an interface with nothing in it.

  `mutation_view("A123G,V45L")` draws the positions a mutation would change,
  **and checks the residue is what the notation says it is.** MCPymol does not,
  and this is the one worth doing better: a view that highlights the wrong
  residue because the numbering is offset by a construct tag looks exactly like
  one that worked. It refuses with "position 1 holds MET, not TRP".

- **A second palette, for whatever the picture is about.** A ligand drawn in
  the pocket's own grey disappears into the sidechains around it; drawn in
  Mol\*'s default it comes out chain-coloured brown, which is the thing the
  palette exists to fix and only looks deliberate by accident. Same colours,
  warmer carbon.


- **A review found fifteen things wrong with the three entries below**, and
  they are fixed rather than filed. The ones worth naming: `define_elements`
  checked a name against the colour registry alone, so `"physical"` — a size
  theme with no colour twin — could be claimed and then **deleted from Mol\***
  by the cleanup path; `superpose`'s deviations dropped insertion codes, so
  residue 8 could be painted with 8A's motion; registering the field could
  fail a superposition that had already happened, discarding the rmsd and the
  transform with it; the ligand stayed on screen when the next view took over,
  which is the double-draw its own handle exists to prevent; and two identical
  structures stretched a colour ramp across floating-point noise and painted a
  speckle that read as a hinge.

- **A view draws what is bound, not only the polymer.** `textbook` and `putty`
  select `polymer`, and a ligand is not polymer — so maltose-binding protein
  came up with no maltose in it, which is most of the reason anyone loads that
  structure. Non-solvent hetero is drawn alongside, under its own handle and in
  the same element palette the sidechains use. Solvent stays out: a crystal
  structure's waters are most of its non-polymer atoms and none of its point.

- **`superpose()` registers a field of how far each residue moved.** A
  superposed pair drawn in two colours is close to unreadable — where the two
  agree the backbones interleave at one depth and read as a mottle of both, and
  where they disagree looks no different. Painting one copy by the distance the
  other moved says the thing the picture was for.

  **Measured over every residue the two share, not the ones the fit kept**, and
  the difference is the whole point. `superimpose_homologs` discards outliers
  to find its transform, and on a hinge motion the residues it discards are
  exactly the ones that moved: superposing the open and closed states of
  maltose-binding protein keeps 185 residues of 370, and the 185 are the half
  that stayed put. A field built from the fit paints the rigid lobe and leaves
  the hinge blank. Residues are matched by chain, number *and* name — two
  structures numbered differently would otherwise pair off residues that are
  not the same residue and report the mismatch as motion.

  A handle for the target copy is registered with it, because the mobile copy
  carries no value and colouring the whole scene paints half of it the no-data
  grey.

- **Sidechains are attached to the molecule now.** They floated: `sidechain` is
  "polymer and not backbone" and the alpha carbon *is* backbone, so every stick
  began at CB with no bond back to anything — a cloud of fragments beside the
  ribbon they belong to. The view draws the anchor as well. The selection
  keyword is untouched, because its definition is right; only what this view
  draws changed.

  That broke the refusal, and a test caught it before it shipped: every polymer
  has an alpha carbon, so "does this have sidechains" became always true and
  glycine — whose sidechain is a hydrogen — would have reported success for a
  view of nothing but anchors. What exists and what is drawn are now asked
  separately.

- **An element palette you can choose, because Mol\* has none.** Its
  `element-symbol` theme takes exactly one parameter, `carbonColor`; oxygen,
  nitrogen and sulfur come from a fixed CPK table with no way in. So an
  all-atom view could not be made to agree with the cartoon under it.
  `define_elements()` registers a theme that reads the element symbol, and the
  sidechains view uses it: light grey carbon, teal nitrogen, mauve oxygen,
  burnt sienna sulfur, and a fallback colour so an unusual metal is never
  invisible. Chosen to sit quietly next to a secondary-structure cartoon rather
  than to match CPK.

- **And they are thicker.** Mol\*'s default of 0.15 drew hairlines that read as
  noise. 0.22, picked by rendering three widths and looking; 0.4 was tried
  first and buried the ribbon completely, which is the opposite failure and
  just as useless.


- **`snapshot(finish="hedcut")` redraws a capture in ink.** Tone becomes line:
  the image is banded by brightness and each band filled with strokes, more of
  them where it is darker, the way an engraving carries shading without any
  greys. Two styles — `cross-hatch` lays a second and third direction across
  the first as the tone deepens, `hedcut` keeps one direction and thickens the
  stroke, which is what makes it read as engraved rather than sketched.

  **Mol\* has no hatching, stippling or halftone anywhere** — its whole
  post-processing vocabulary is antialiasing, background, bloom, depth of
  field, occlusion, outline, shadow and sharpening, checked across the tree.
  Adding one would mean a custom render pass and a Mol\* built from source. So
  this runs afterwards, on the pixels, which is where engravers worked too.

  The cost is stated where a caller meets it: **the viewer cannot show it.**
  There is no live preview and no menu entry, a caller sees it only in the
  file, and the reply says the finish was applied after the capture rather than
  leaving that to be noticed. It also reports the **ink fraction**, because the
  caller is usually a model that cannot look: a near-black ground engraves to
  an almost solid rectangle with the molecule showing through as a few light
  strokes, and nothing else in the reply would say so.

  Three things the plan for this had wrong, all found by looking at the output.
  A "white" ground is about 252, not 255, so the lightest band caught the whole
  background and sprinkled strokes over the empty half of the frame. Mapping
  darkness straight onto bands put a mid grey two thirds of the way to solid,
  so a cartoon came out a black mass with holes; darkness is raised to 1.7
  first. And strokes three pixels apart with four crossed directions is not
  cross-hatching but a dot screen.


- **Any number you have computed can be drawn, without borrowing a column to
  carry it.** `define_field(name, values)` registers a per-residue scalar as
  both a colour theme and a size theme, after which `color(name)` paints it and
  `size(name)` gives it width — an ordinary theme from that point on, listed in
  `capabilities()` beside Mol\*'s own.

  What it replaces: protean's existing scalar colouring writes the numbers into
  the **B-factor column and re-sends the whole structure**, so that Mol\*'s
  `uncertainty` theme has something to ramp over. That costs an upload per
  colouring, flattens the values into that theme's fixed [0, 100] domain so they
  stop meaning what they measured, and allows exactly one scalar at a time
  because there is one B-factor column.

  It takes the shape the analysis tools return — entries with `chain`, `seq`,
  and a number — and finds the number whatever it is called, so `rmsf()`'s
  residues go in unchanged. `conservation()`'s carry two numbers, entropy and
  conservation, so that one needs `key=` to say which; an entry with more than
  one number refuses rather than guessing, and names the choices. An earlier
  draft of this entry claimed both tools went in unchanged, which was not true
  of the second and was caught in review rather than by using it.

  The reply says how many residues on screen the field did *not* reach, because
  analysis replies truncate their residue list to `limit` and a field built from
  a truncated one covers part of the molecule while looking deliberate.

  **Keyed by residue, not by atom index.** Index alignment is shorter and breaks
  on the first biological assembly, where the viewer holds symmetry copies the
  analysis array does not — silently wrong on exactly the structures where it
  matters. A residue key gives every copy the value that residue earned.

  Refused when the field matches no residue in the loaded structure. Such a
  field registers cleanly and paints the whole molecule the no-data grey, which
  looks like a rendering fault rather than a mistake in the numbers.

- **Width is a channel now, not a thing `putty` happened to do.** `size()` sets
  what decides the width of a drawn selection — `uncertainty` for B-factor,
  `physical` for van der Waals radius, `uniform` to flatten it — the same way
  `color()` sets what decides its colour, validated against Mol\*'s live size
  registry and reported in `capabilities()`.

  This was written up as blocked. docs/views.md recorded `putty`'s tube width
  as possibly needing "a size theme protean does not expose", which would have
  tied it to the cryo-EM "size by scalar" work; and Mol\* 4.18 had a bug that
  made a size-only theme update silently do nothing, so it could not have been
  exposed cleanly anyway. Mol\* 5 fixed that bug, and nothing else was in the
  way.

  It also nearly shipped with a refusal for representations that "have no
  width", written on the assumption that only tubes and spheres do. Measured
  before shipping: `physical` moves 0.0337 of the frame on a **cartoon**, more
  than it moves on a putty. Every representation has a width. The refusal would
  have blocked something that works, and there is a test carrying that
  measurement so the assumption is harder to make twice.

### Mol\* 5

- **The viewer runs on Mol\* 5.11, up from 4.18.** Fourteen months and 32
  releases behind, which was making every "can Mol\* do this?" answer
  unreliable. Nothing protean uses went away: the live registries gained a
  `polyhedron` representation and the `residue-charge` and `volume-instance`
  colour themes, and lost nothing. Every render differential passes, and all
  but one at its existing threshold — which is the claim worth making, because
  a renderer that shaded differently would have moved numbers tuned to three
  decimals.

  The exception is the outline, and it is worth stating plainly because the
  first version of this entry claimed the clean sweep: 5.11 draws a thinner
  outline than 4.18. Measured on the same fixture and flags at CI's headless
  frame size, the green it puts on screen fell from 0.00107 to 0.00074, and CI
  itself came in at 0.00047 against a bar of 0.0005. The bar had been derived
  from a measurement taken at a different frame size, so it was never really
  2.5x of margin. It is now a noise floor, with the fidelity claim moved to
  something the frame cannot affect: widening the outline has to widen the
  outline, which it does by 44x on the frame that failed.

- **`spin()` and `rock()` turned nothing at all, briefly.** Mol\* 5 added a
  required `axis` parameter to both animation groups and dereferences it every
  frame, and `TrackballControls.setProps` shallow-assigns rather than filling
  in group defaults — so protean's params object replaced the animation with
  one Mol\* could not run. The tool answered `{mode: 'spin', speed: 1}` and the
  camera sat byte-identical. Caught in review of this branch, not by the suite,
  which had been asking the viewer what it had been told rather than asking the
  camera where it was. There is a test for that now, and it fails when the axis
  is taken away again.

  Same shape as the `bloom` parameter below, and the reason to state it twice:
  the audit that caught bloom went through the six postprocessing effects and
  did not think to check the trackball. "Nothing protean uses went away" was
  true and beside the point — this was something new that became required.

- **`spin(speed=)` means turns per second now, not radians.** Mol\* 5 changed
  what the number means without changing its name, so the same call spins
  2\*pi times faster. protean follows the new unit rather than converting: one
  value, held in one place, rather than a reported number that disagrees with
  the viewer's. A model reading the docstring gets "1 is one revolution a
  second", which is the more useful thing to be told anyway.

- **Mol\*'s licence notice is shipped — for the first time, it turns out.**
  4.18 built with webpack, which extracted bundled licences into
  `molstar.js.LICENSE.txt`; 5.11 builds with esbuild, which emits no such file
  and leaves its dependencies' notices inline in the JavaScript instead.
  Noticing that file was gone is what prompted a look at what it had contained,
  and the answer was: safe-buffer, immutable, and React. **Not Mol\*'s own.**

  Neither published bundle carries it. Searched in both, "mol\* contributors"
  appears zero times in 4.18 and zero times in 5.11 — the only copyright line
  in either is a third-party shader's. Mol\*'s own notice lives in the npm
  package's top-level LICENSE, which nothing was copying. So this is not
  something the upgrade broke: protean had been redistributing Mol\* without
  Mol\*'s notice for as long as it has redistributed Mol\* at all.

  `sync-molstar` copies that LICENSE now, as `molstar-LICENSE.txt`. The
  packaging test written to catch exactly this had been passing throughout, on
  React's and immutable's notices, because it asked whether the file contained
  "MIT License" and "Copyright" rather than whose. It asks for "Mol\*
  contributors" now. Reported upstream: their own source headers say
  `/** Copyright ... mol* contributors` with no `@license` marker, so their
  bundler treats them as ordinary comments and drops them, while React's
  `/** @license` survives.

- **Mol\*'s PyMOL transpiler changed what `within` means, and we did not.**
  Five selections that agreed exactly under 4.18 disagree under 5.11, all of
  them a `within` with an explicit left operand: they return 456 where we
  return 121 for `polymer within 4 of resn HEM`. Nothing in their changelog
  mentions it. Checked against a third opinion before assuming the other
  implementation was the one that moved — a plain numpy distance calculation,
  owing nothing to either transpiler, returns our numbers. They are recorded
  as divergences, which assert both halves, so if upstream restores the old
  behaviour the test fails and the claim gets retired rather than carried.

- **CI runs Node 22.** Mol\* 5.11 declares `node >=22.0.0`; CI was on 20, which
  npm reported as a warning and then built anyway. A dependency's stated
  engine requirement is not a suggestion, and finding out which parts of it
  were load-bearing during a later debugging session is the expensive way.

- **`bloom` gained a parameter.** protean spells every screen-space effect's
  parameters out rather than toggling a name, because a Mol\* effect enabled
  with an empty params object renders from something nobody chose. A key
  missing from that table is the same hazard, and 5.11 added `transparency`
  to bloom. Checked key by key across all six effects; bloom was the only one.

### Views

- **A control in the viewer that asks the server rather than drawing.** One
  button, one view — `ghost-heart` — and the rule that makes it worth having:
  **a button never draws, it asks**, and the server runs the same `preset()` a
  model would call. One code path, two entry points, so a handle made by a click
  is an ordinary handle and the picture a click makes is the picture the model
  would have made. Any other arrangement lets the GUI and the model render the
  same view differently, and eventually they will.

  **The channel carries view names from a fixed list and nothing else.** The
  socket is token-authenticated, but a page holding that token can already reach
  the viewer, and the tool surface would hand it `snapshot(path=)`,
  `save_session(path=)`, `movie(path=)` and `electrostatics(path=)` — each of
  which writes where the caller says. A test enumerates the live tool registry,
  finds the nine tools taking a path, and asserts none is reachable from the
  page.

- **`ghost-heart` no longer wraps the water.** A molecular surface is computed
  per atom, so an isolated solvent molecule gets its own closed blob: 1UBQ drew
  fifty-eight of them, detached spheres floating around the fold and 14% of
  everything on screen — coverage fell from 0.1154 to 0.0996 with them gone.
  Ligands and ions stay, because they are part of the molecule's shape and the
  envelope should bulge around a bound ligand rather than ignore it. Found by
  someone looking at the picture and saying it looked strange, which is the only
  instrument that was ever going to catch it.

- **Every tool reply says what the person at the viewer did.** Without it the
  model answers about a scene it did not produce and has no way to know changed
  — this project's oldest failure mode, arriving through a door we opened
  ourselves. Drained rather than repeated, so one click is reported once. MCP
  can push notifications and client support is uneven, so it rides out on the
  next reply instead, which needs no client support at all.

- **More presets, so `preset()` covers the styles worth borrowing from
  MCPymol.** `textbook`, `putty`, `hydrophobic-surface`, `spacefill` and
  `skeleton` decide what is drawn; `cinematic`, `light-ground` and `dark-ground`
  only restyle what is there, as `publication-cartoon` and `illustrative`
  already did. None of them needed new rendering — Mol\* has the
  representations and the themes, and protean validates against its live
  registries, so every one is a composition of tools that already existed. The
  reply lists every call each one made, so any of it can be adjusted afterwards.

  Two of these replaced earlier drafts. `bfactor` said the same thing `putty`
  does, in one channel instead of two, and `pointillist` was a novelty rather
  than a way of reading a structure; `spacefill` and `skeleton` answer questions
  — how does this pack, and what are the atoms — that nothing else in the
  catalogue answered.

  The drawing presets hide what the load preset built and draw through one
  shared handle, `auto_view`. Sharing it is the point: applying a second view
  rebuilds that component rather than adding to it, so switching views ends at
  a view instead of at all of them at once.

- **`putty`'s tube width follows B-factor, and that is Mol\*'s own default
  rather than anything protean adds.** The plan for these views recorded it as
  an open question — whether putty needed a size theme protean does not expose,
  which would have tied it to the cryo-EM "size by scalar" work. It does not.
  Measured against the same coordinates loaded twice, once with the deposited
  B-factors and once with every B-factor flattened to their mean: the putty
  frames differ by 0.020 of the frame, the cartoon control by 0.000125.

- **A whole-scene preset reframes the camera, deliberately and in the reply.**
  Drawing the same handle twice through `show()` lands on two different
  cameras — the first draw keeps the framing the load preset chose, the second
  refits to what is on screen and then holds, 0.144 of the frame apart on 1UBQ
  with no preset involved. So a view applied once was framed for a scene that
  was no longer there, and applying it twice gave two pictures. The presets now
  ask for the frame outright, which costs a camera the caller had moved and
  says so; given a handle they leave the camera alone.

- **A view refuses rather than drawing an empty scene.** A handle with no atoms,
  or a whole-scene view of a structure with no polymer, previously drew nothing
  and reported success.

- **Loading a structure now waits for the camera the load preset moved.**
  `focus`, `orient` and `reset_view` have always waited; `load_structure` never
  did. Mol\* tweens the preset's framing over ~250 ms like any other camera
  move, and waiting for the *geometry* to stop changing says nothing about it,
  so a capture taken straight after a load could be mid-flight. Found by CI
  rather than by reasoning: two loads of identical coordinates produced frames
  0.008 apart on a runner where this machine reads 0.000125.

  **The wait has to come after the render pump, not inside the action**, which
  the first version of this fix got wrong. Mol\* resolves a requested camera
  reset from `commit()` and only once `commitScene` reports everything
  committed — "Only reset the camera after the full scene has been commited",
  `canvas3d.js` — so a wait placed before the geometry settles watches a camera
  that has not started moving, counts stillness as arrival, and returns just in
  time for the tween to begin behind it.

- **A preset states every screen-space effect, rather than only the ones it
  changes.** `effects()` leaves anything omitted exactly as it was, which is
  right for a tool composing calls and wrong for a recipe declaring a whole
  look. `cinematic` is the only preset that turns depth of field on, so
  `textbook`, `illustrative` and `hydrophobic-surface` — none of which mentioned
  it — rendered blurred after it and reported success.

- **The steps a preset reports are derived from the calls it makes.** They were
  written out by hand beside each call and had drifted: three omitted an
  argument that had been sent, so replaying the reported steps produced a
  different picture than the preset did.

- **A refused view leaves the scene alone.** The refusal path hid the viewer's
  own scene and rebuilt the shared handle *before* checking the selection had
  matched anything, so declining to draw left a blank viewer, an empty
  `auto_view` in the handle table, and an error mentioning neither.

- **`remove()` drops the handle as well as the component.** The handle survived
  on the Python side while its component was deleted in the viewer, so the two
  disagreed about what existed and a later call on that name resolved here and
  then failed there.

### The viewer

- **The viewer opens as a canvas, with Mol\*'s panels collapsed to slices.**
  Both were shown in full by default, and they are Mol\*'s controls for a
  person driving Mol\* directly: the left one loads structures, the right one
  edits the state tree. Used here they change the picture and nothing else —
  the analysis half lives in the Python process, so the model goes on
  answering, correctly, about the molecule it loaded rather than the one now on
  screen.

  They are collapsed rather than removed, because a viewer you cannot inspect
  is its own kind of opaque: when the picture looks wrong, the state tree is
  where the answer is. Mol\* collapses its left region to a 32 px icon rail on
  its own; its right region has no collapsed state, so protean supplies a 16 px
  tab that opens the panel and moves to sit against its edge. Measured on a
  1280×800 window: Mol\*'s panel greys fell from 42% of it to 0.5%, and the
  molecule rose from 20.5% to 36%.

  The sequence strip stays — it is the one panel that *reports* rather than
  acts, and reading along while a model works is most of why a person has the
  viewer open. The viewport's buttons go, except Mol\*'s camera reset and the
  controls toggle. The status pill moves to the lower right, the one corner
  Mol\* leaves empty.

### Packaging

- **The wheel ships Mol\*'s licence notice, which it is obliged to carry.** The
  built viewer travels inside the wheel, so `pip install protean-mcp` delivers
  `molstar.js` and everything bundled into it — React, immutable, safe-buffer,
  all MIT. The bundle's first line points at `molstar.js.LICENSE.txt`, and the
  sync step copied the script and the stylesheet but not that file, so the
  artifact carried a dangling reference to the notice MIT requires. A packaging
  test now fails if the wheel loses it.
- **protean's own licence is machine-readable.** `license = { file = "LICENSE" }`
  left `License` empty in installed metadata — an audit of the dependency tree
  read protean-mcp itself as `UNSTATED`. Now an SPDX expression (PEP 639), so
  the wheel reports `License-Expression: MIT`.

### Fixed

- **A stale server now says so, in the first reply of every session.** An MCP
  server is long-lived: it keeps running the code it loaded at start while
  serving the viewer page off disk, so a rebuilt page can meet a server from
  three days ago. That happened, and took twenty minutes and a hand-rolled
  WebSocket to diagnose. `open_viewer` now reports the build that answered,
  when the process started, and — the part that carries the information —
  whether its source still matches what is on disk, naming what changed.

  **Version numbers could not have done this**, which is why they are not what
  it relies on: `__version__` has read `0.1.0.dev0` for every build there has
  been, and `PROTOCOL_VERSION` has been `1` since the first commit including
  across the change that caused the incident. Both are reported anyway — they
  are the right answer when two *machines* compare notes, just never when two
  moments do. The viewer also compares the handshake's protocol number and says
  so in its status pill, which catches a deliberate break from here on.

  For an installed wheel none of this fires, because nothing rewrites the files
  under one.
- **`show()` no longer takes the camera at a moment nobody chose.** Drawing a
  handle moved the camera roughly one time in seven and held still the rest,
  because Mol\* requests an automatic camera fit whenever a scene commit decides
  the visible bounding sphere has moved out from under it — and a commit has a
  250 ms budget, so which boundary a `hide` and a `show` landed on decided
  whether that test ran against the old scene or the new one. A caller could
  rely neither on the camera moving nor on it staying, which is worse than
  either: a figure captured after a draw was framed unpredictably, and applying
  the same view twice could give two pictures.

  The viewer now takes the camera off automatic fitting and asks for the one
  fit a load wants. `focus()` and `reset_view()` are unchanged, so the camera
  moves where a caller asked for it and nowhere else. The camera's *limits*
  still follow the scene, because the flag that stops Mol\* re-framing also
  stops it maintaining them — left alone, a map spanning more than its protein
  would be clipped away by a slab drawn from the protein's radius.

- **A capture is allowed time in proportion to the pixels it asks for.** Every
  capture shared one fixed 300 s budget, which the range of sizes makes
  meaningless: 12000×9000 takes about 20 s on a real GPU, while under software
  rendering the same machine takes 6.5 s for a 1200 px capture and 105 s for a
  4323 px one — 183 mm at 600 dpi, an ordinary journal figure. A CI runner is
  roughly three times slower again. Above about 5000 px the fixed budget could
  not be met on any renderer that slow, including locally, for sizes the tool
  accepts without complaint.

  The budget is now 60 s per megapixel of the requested size, with a 300 s
  floor for small captures — about 10x what the development machine needs for a
  journal figure and 3x what a CI runner needs. Positioning the scene (a
  trajectory frame, a camera move, an orbit step) borrowed the capture's budget
  when there was only one, and keeps the old 300 s under its own name, so a
  camera move that never answers is not given a render's patience.

  There is no progress signal to use instead: Mol\*'s ordinary image pass
  renders in a single synchronous call, so the page's main thread is blocked
  for the whole capture and could not send a heartbeat if asked for one.
  Silence is what a healthy large capture looks like, and only the pixel count
  separates it from a stall.

  This is not what made the journal-figure test flaky in CI — that was a lost
  reply, below — but the two share a cause worth naming: a long render is
  indistinguishable from a stall from the outside, so both the budget and the
  failure reporting were guessing.

- **A capture's reply is no longer lost with its socket.** During a
  figure-sized capture the page's main thread is blocked for tens of seconds,
  and the WebSocket can die inside that window — observed closing 62 s into a
  68 s capture, abnormally (1006, no close frame), with the page itself
  surviving. The page then replied on the socket the request had arrived on,
  and `send` on a closed socket does nothing, so the answer vanished although
  the work had succeeded. Nothing failed the waiting request either, so it ran
  out its whole budget and reported a stall — *"Viewer timed out on
  'snapshot'"*, which is what CI had been printing.

  The page now keeps a reply it cannot send and delivers it on the next
  authenticated socket, and the handshake declares what that page still owes.
  A viewer that reconnects mid-render keeps its request alive; one that
  reloaded, or a second tab that takes the connection, ends it immediately with
  the reason rather than at the end of the budget. A plain disconnect
  deliberately fails nothing: the reply may still be on its way.

- **`screenshot` works again through an MCP client.** It failed for every
  caller with `Unable to serialize unknown type: Image`, while the test suite
  stayed green. FastMCP derives an output schema from the return annotation,
  and `-> list[Any]` gets one — so the reply was encoded as *structured*
  content, which an image cannot be. A bare `list` gets no schema and worked,
  which is how the floating `mcp[cli]>=1.2.0,<2` pin brought this in without a
  line of protean changing. The tool now declares `structured_output=False`,
  putting the image back in unstructured content where it belongs.

  **The tests could not have caught it**, and that gap is now closed too: every
  test called tools as plain Python functions, so nothing ever crossed the
  serialisation boundary a real client goes through.
  `tests/test_mcp_boundary.py` calls them the way a client does.

- **`load_session` no longer leaves the analysis describing the previous
  molecule.** It restored the viewer and never touched the Python side, so
  every count, distance and selection afterwards answered about whatever was
  loaded before — measured at viewer 100 atoms against `_structure`'s 660,
  with the identifier still reading `1ubq` and nothing reporting a
  discrepancy.

  Both halves are restored now, or neither is. The analysis structure is
  rebuilt from the session's own embedded copy — no network, and no question
  about which file, since it is the same bytes the viewer parsed. **The
  viewer's atom count decides how to build it**: the same deposited text
  assembles two ways and nothing in the file records which was chosen (1HHO
  reads 4792 biological, 2396 asymmetric), so a fixed default would have been
  silently wrong for half of all sessions. If neither reading matches the
  viewer, the analysis is left empty and the reply says so with both numbers,
  because a structure that disagrees with the picture is the failure this fixes
  rather than a caveat to attach.

- **A viewer that cannot connect now says why, instead of retrying forever.**
  The WebSocket API hides the handshake's HTTP status from the page, so a
  refused socket and an unreachable server arrive as the same event — and the
  page retried every 1.5 s indefinitely, showing only "disconnected". Two cases
  make that a silent failure rather than a hiccup: the bridge mints a token per
  process, so restarting the server leaves an open tab refused on every attempt
  for as long as it lives; and a page opened by hand at
  `http://127.0.0.1:9878/` has no token at all, loads, looks alive, and can
  never connect. The page now stops after ~30 s and names both causes, or says
  immediately that it was opened without a token, and points at `open_viewer`
  either way. A completed handshake resets the budget, so a long session is not
  capped.

- **A writing tool will no longer turn one kind of file into another.**
  `snapshot`, `screenshot`, `save_session`, `movie` and `electrostatics` wrote
  wherever they were pointed, with no check: during the security pass
  `save_session` replaced a 21-byte JSON file with 32 kB of gzip, and
  `electrostatics(path=…)` — an *output* path that reads like an input — wrote
  an OpenDX grid over a file named `secret.key`. An existing file is now
  replaced only when it already holds what that tool writes, so capturing a
  figure over its own earlier version still works while replacing prose,
  a key or a config file is refused. `overwrite=True` asks for it explicitly.

### Security

- **`open_viewer` no longer hands the handshake token to the model.** The URL
  it returned carried the token, so the credential that authenticates a viewer
  socket landed in the model's context, in transcripts and in any log of tool
  results — and the `Origin` check is no backstop for a leaked token, because
  an *absent* Origin is allowed so non-browser clients can connect at all. The
  address now comes back without it while the real one goes straight to the
  browser; `reveal_url=True` asks for it deliberately, for a second browser or
  a forwarded port. All three of `open_viewer`'s return paths were leaking it.

- **A session file is no longer trusted to say where the viewer should look.**
  `load_session` handed the file's embedded Mol\* state tree straight to
  `setSnapshot`, which applies it as given, so a `.protean` file could name a
  URL and the browser would fetch it — and then draw whatever came back, while
  `load_session` returned a normal reply naming the atom count it had been
  handed. Demonstrated against a live viewer with an outbound GET to a stand-in
  attacker server. The format exists to be shared, so a session someone sent
  you is its ordinary use, not an exotic one.

  A session is now checked two ways, because neither alone is enough:

  - **No string in it may name a location to fetch from**, except this bridge's
    own relative `/volumes/<handle>` route and — by exact value — the three
    third-party URLs Mol\* serialises as its own custom-property defaults. Both
    exceptions were measured from real sessions; a blanket "no URLs" rule would
    have refused every session, and allowing the *key* would have permitted the
    same providers to fetch from anywhere.
  - **No transformer may appear that `save_session` never writes.** This is the
    half that does not depend on spotting a URL: `create-volume-streaming-info`
    fetches from Mol\*'s own public default when the file names no URL at all,
    so there is nothing for the first check to find.

  Decompression is bounded at 512 MB as well: 9 kB of gzip reaches 2 GB, and
  the file was read whole before anything checked it. Malformed files now
  refuse rather than raising `AttributeError`, `KeyError` or `RecursionError`.

- **The viewer handshake is authenticated.** The bridge's WebSocket accepted any
  connection: no `Origin` check, no token. A WebSocket is not subject to the
  same-origin policy and the port is `DEFAULT_PORT` plus a small scan range, so
  any site the user was visiting could connect, send `protean_ping` — which is
  designed to displace the incumbent — and from then on receive every action and
  answer every one of them. Demonstrated with a socket carrying
  `Origin: https://evil.example`: accepted, and the real viewer was superseded
  and closed.

  A spoofed viewer returning fabricated counts defeats the one guarantee this
  project exists to make, while every call returns cleanly.

  Now a per-bridge token (`secrets.token_urlsafe(32)`, compared with
  `compare_digest`) plus an `Origin` check, both **before** `prepare()` so a
  refused caller never reaches the message loop. `ViewerBridge.viewer_url` is the
  single place the URL is built, so a viewer cannot be opened that its own socket
  would refuse. Found by the going-public security pass, which is the argument
  for running that pass before the flip rather than after.

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
  ghost-heart and active-site.
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
