# The soft-matter bake-off

**A prototype, not part of protean.** Nothing here is imported by
`viewer/src`, none of it ships in the bundle protean serves, and it is outside
`tsconfig.json`'s `include`, so **CI does not typecheck it**. It exists to
answer one question — do the soft-matter treatments carry data legibly enough
to be worth 28 weeks — and the answer is in `docs/bakeoff.md`.

It builds against Mol\* bundled from `molstar/lib` rather than the prebuilt UMD
bundle protean serves, because the mesh route needs it. See
`docs/molstar-bundling.md` for what that costs.

```
npx vite build --config vite.bakeoff.config.ts
```

`radiolaria.ts` is the piece worth keeping: a working `UnitsMeshVisual` with
picking intact and a per-atom channel bound to porosity. If the plan proceeds,
start there — and read the two ways it was wrong first.
