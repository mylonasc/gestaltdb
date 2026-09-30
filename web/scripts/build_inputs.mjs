import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export function sourceInputs(webDir) {
  const paths = ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html",
    "scripts/build_inputs.mjs", "scripts/write_manifest.mjs", "scripts/check_licenses.py"];
  function visit(relative) {
    for (const entry of readdirSync(join(webDir, relative), { withFileTypes: true })) {
      const path = `${relative}/${entry.name}`;
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile()) paths.push(path);
    }
  }
  visit("src");
  const files = Object.fromEntries(paths.sort().map(path => [path,
    createHash("sha256").update(readFileSync(join(webDir, path))).digest("hex")]));
  const digest = createHash("sha256").update(Object.entries(files).map(([path, hash]) => `${path}\0${hash}\n`).join("")).digest("hex");
  return { files, digest };
}
