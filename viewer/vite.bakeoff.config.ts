import { defineConfig, type Plugin } from 'vite';

/** See docs/molstar-bundling.md: the skin is stubbed, and no Sass is then needed. */
const stubStyles = (): Plugin => ({
  name: 'stub-styles',
  enforce: 'pre',
  resolveId: (id) => (id.endsWith('.scss') ? '\0stub-style.css' : null),
  load: (id) => (id === '\0stub-style.css' ? '' : null),
});

export default defineConfig({
  root: __dirname,
  base: './',
  plugins: [stubStyles()],
  build: {
    outDir: '/private/tmp/claude-501/-Users-charlie-code-protean-viewer/f047b725-a8a9-4c3a-a20a-fe788b9118b5/scratchpad/bake-dist',
    emptyOutDir: true,
    rollupOptions: { input: 'bakeoff/index.html' },
  },
});
