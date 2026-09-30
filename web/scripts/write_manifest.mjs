// Writes src/gestaltdb/viz/static/viz-manifest.json after `vite build`.
// Records exact dependency versions and bundle hashes so Python (VIZ-03)
// can warn when artifacts are served from a stale bundle.
import { createHash } from "node:crypto";
import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { sourceInputs } from "./build_inputs.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const webDir = dirname(here);
const staticDir = join(webDir, "..", "src", "gestaltdb", "viz", "static");
const pkg = JSON.parse(readFileSync(join(webDir, "package.json"), "utf-8"));
const lock = JSON.parse(readFileSync(join(webDir, "package-lock.json"), "utf-8"));
// Notices are generated after Vite clears/recreates the output directory.
execFileSync(process.env.VIZ_BUILD_PYTHON ?? "python3", [join(here, "check_licenses.py"), "--notices",
  join(staticDir, "third-party-notices.txt")], { cwd: webDir, stdio: "inherit" });
const inputs = sourceInputs(webDir);

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const manifest = {
  manifest_version: 2,
  app_version: pkg.version,
  bundle_js: "gestaltdb-viz.js",
  bundle_css: "gestaltdb-viz.css",
  bundle_js_sha256: sha256(join(staticDir, "gestaltdb-viz.js")),
  bundle_css_sha256: sha256(join(staticDir, "gestaltdb-viz.css")),
  third_party_notices: "third-party-notices.txt",
  third_party_notices_sha256: sha256(join(staticDir, "third-party-notices.txt")),
  d3_version: lock.packages["node_modules/d3"].version,
  react_version: lock.packages["node_modules/react"].version,
  react_dom_version: lock.packages["node_modules/react-dom"].version,
  source_files: inputs.files,
  source_sha256: inputs.digest,
  toolchain: { node: process.version, npm: process.env.npm_config_user_agent ?? "unknown",
    typescript: lock.packages["node_modules/typescript"].version, vite: lock.packages["node_modules/vite"].version },
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
