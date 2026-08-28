/// <reference types="vitest" />
import { defineConfig, type Plugin } from 'vite';

import { patchBackgroundTest } from './src/molstar-patches';

/**
 * Applies the Mol* background-test fix to every shader that still needs it.
 *
 * `src/molstar-patches.ts` says what the fix is and why protean is carrying it.
 * The plugin's only job beyond calling it is the assertion below.
 *
 * **It fails the build if it patched nothing.** A find-and-replace against
 * someone else's source is exactly the shape of change that stops matching
 * silently — a Mol* upgrade reindents the file, or renames the function, and the
 * replace quietly becomes a no-op while the build stays green and every capture
 * goes back to costing three times what it should. No picture would show it, so
 * the build has to.
 */
function molstarShaderPatches(): Plugin {
  let patched = 0;
  return {
    name: 'protean:molstar-shader-patches',
    transform(code, id) {
      const fixed = patchBackgroundTest(code, id);
      if (fixed === null) return null;
      patched += 1;
      return { code: fixed, map: null };
    },
    buildEnd() {
      if (patched === 0) {
        this.error(
          "protean patches Mol*'s isBackground() predicate in its shaders " +
            '(see viewer/src/molstar-patches.ts), and this build matched none ' +
            'of them. Either Mol* has fixed it upstream — in which case delete ' +
            'the patch and the test that guards it — or the source it matches ' +
            'on has changed and the patch needs rewriting. It has NOT silently ' +
            'stopped being needed.'
        );
      }
    },
  };
}

export default defineConfig({
  plugins: [molstarShaderPatches()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
  base: './',
  build: {
    outDir: '../src/protean_mcp/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 5000,
  },
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    'process.env.DEBUG': 'false',
  },
});
