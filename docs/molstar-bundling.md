# Bundling Mol\* from source

**A measurement, taken 2026-08-21, of the assumption protean has been built on
since the first commit.**

`viewer/src/main.ts` says, in a comment at the top of the file:

> Boots the prebuilt Mol\* viewer (loaded globally from molstar.js — bundling
> molstar from source needs >4 GB RAM, the prebuilt bundle needs none)

Everything downstream follows from that sentence. `docs/views.md` §5.9 put
cross-hatching in Pillow rather than in a render pass because of it, and the
soft-matter plan's Phase 1 keystone — a user post-processing pass slot — and
every one of its 25 mesh-based treatments are unreachable while it holds.

**It does not hold, and it appears to have been measuring the wrong thing.**

## What was measured

Vite pointed at `molstar/lib`, building an entry that imports the `Viewer`
class *and* the geometry machinery a custom representation needs — `MeshBuilder`,
`addSphere`, `UnitsMeshParams`, `ParamDefinition`, `Color`, `Vec3` — and then
runs all of it in a browser.

The two cannot be split, which is why the entry imports both. A custom
representation shares registries and classes with the running plugin, so
bundling `mol-geo` *alongside* the prebuilt UMD bundle is not a cheaper version
of this: it is two copies of the module graph, and a treatment registered into
one would be invisible to the other.

| | prebuilt UMD, today | bundled from `lib/` |
|---|---|---|
| Peak RSS to build | not built — copied | **1.19–1.27 GB** across five runs |
| Build time | — | **4.6 s** (1747 modules) |
| Bundle | 4.8 MB, 1.4 MB gzipped | 4.9 MB, 1.4 MB gzipped |
| `MeshBuilder` at runtime | unreachable | **162 vertices, 320 triangles** |
| Draws a molecule | yes | **yes — 1CRN, 327 atoms, cartoon** |

The last two rows are the ones that matter, and they were taken from a running
browser rather than from a build log. A bundle that compiles and then throws on
the first `new` would make every other number here misleading, and no build log
can tell those apart.

## Why the old figure was probably right about something else

`>4 GB` is a plausible cost for building **Mol\*'s own repository** from
TypeScript. It is not the cost of what protean would actually do, because the
published npm package ships `lib/` as **3,000 already-compiled ESM `.js`
files** with the GLSL inlined as JavaScript string modules —
`lib/mol-gl/shader/*.frag.js` are strings, not shader sources needing a loader.
Vite pointed at `lib/` is bundling prebuilt JavaScript. It never compiles
Mol\*.

## Three things a real switch still has to settle

- **The UI skin is SCSS and does not compile here.** `mol-plugin-ui/skin/light.scss`
  fails under Vite 5.4 with sass-embedded 1.103 — a BOM-prefixed `@use "sass:meta";`
  is rejected with `expected "{"`. The spike stubbed it, which is legitimate
  rather than a dodge: protean already ships `public/molstar.css` copied from
  the prebuilt build, so styling has a source either way. But a switch has to
  choose — keep copying the built CSS, or fix the toolchain. **With the skin
  stubbed, no Sass compiler is needed at all**; the build succeeds with
  `sass-embedded` uninstalled.
- **Import `apps/viewer/app`, not `apps/viewer`.** The index module imports its
  own `index.html`, `embedded.html` and `mvs.html`, which exist for Mol\*'s
  standalone app build and drag Vite's html plugin into a library build until it
  fails. The `Viewer` class lives one file down.
- **A page is not protean.** The spike booted a bare viewer. It did not carry
  protean's bridge, its dispatch table, its raf-pump, or the hidden-tab
  behaviour that `withRenderPump` exists for, and none of those were exercised.

## What this unblocks, and what it does not decide

It removes the stated reason not to bundle Mol\*, and with it the reason a
custom post-processing pass and mesh-based representations were out of reach.
Charlie has raised forking Mol\* as a direction under consideration; this
measurement says a fork is **not** required to reach any of it — bundling the
published package is enough.

It does not say protean *should* switch. The prebuilt bundle costs nothing to
build and pins one artifact; bundling puts a 5-second, 1.2 GB step into every
build and CI run, and makes protean responsible for a build it currently
inherits. That is a trade to make deliberately, against a treatment someone
actually wants to draw.
