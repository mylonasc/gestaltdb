// Rebuild and verify committed assets/provenance; intended as a CI gate.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const manifestPath = new URL("../../src/gestaltdb/viz/static/viz-manifest.json", import.meta.url);
const previous = JSON.parse(readFileSync(manifestPath, "utf8"));
execFileSync("npm", ["run", "build"], { cwd: new URL("..", import.meta.url), stdio: "inherit" });
const current = JSON.parse(readFileSync(manifestPath, "utf8"));
for (const key of ["bundle_js_sha256", "bundle_css_sha256", "third_party_notices_sha256", "source_sha256"]) {
  if (previous[key] !== current[key]) {
    console.error(`Committed visualization assets are stale (${key}); rebuild and include assets in the change.`);
    process.exitCode = 1;
  }
}
