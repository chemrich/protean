import { defineConfig } from 'vite';

export default defineConfig({
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
