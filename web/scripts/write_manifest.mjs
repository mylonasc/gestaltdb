// Writes src/gestaltdb/viz/static/viz-manifest.json after `vite build`.
// Records exact dependency versions and bundle hashes so Python (VIZ-03)
// can warn when artifacts are served from a stale bundle.
import { createHash } from "node:crypto";
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webDir = dirname(here);
const staticDir = join(webDir, "..", "src", "gestaltdb", "viz", "static");
const pkg = JSON.parse(readFileSync(join(webDir, "package.json"), "utf-8"));

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const manifest = {
  app_version: pkg.version,
  bundle_js: "gestaltdb-viz.js",
  bundle_css: "gestaltdb-viz.css",
  bundle_js_sha256: sha256(join(staticDir, "gestaltdb-viz.js")),
  bundle_css_sha256: sha256(join(staticDir, "gestaltdb-viz.css")),
  d3_version: pkg.dependencies.d3,
  react_version: pkg.dependencies.react,
  react_dom_version: pkg.dependencies["react-dom"],
  built_at: new Date().toISOString(),
};

// The dev index.html entry is not part of the shipped bundle; Python
// (VIZ-03) owns the artifact shell.
const strayIndex = join(staticDir, "index.html");
if (existsSync(strayIndex)) {
  rmSync(strayIndex);
}

writeFileSync(join(staticDir, "viz-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
console.log(`viz manifest written for gestaltdb-viz ${pkg.version}`);
