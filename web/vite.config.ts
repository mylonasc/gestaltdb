import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build-time only: emits a stable-filename bundle that Python inlines into
// self-contained offline .html artifacts. No CDN, no code-splitting, no
// hashed filenames so packaging stays deterministic.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/gestaltdb/viz/static",
    emptyOutDir: true,
    assetsInlineLimit: 100000000,
    rollupOptions: {
      output: {
        entryFileNames: "gestaltdb-viz.js",
        assetFileNames: "gestaltdb-viz.[ext]",
        inlineDynamicImports: true,
      },
    },
  },
});
