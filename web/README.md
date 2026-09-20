# gestaltdb-viz front end (build-time only)

D3.js + React source for GestaltDB packaged graph visualization. This
toolchain runs **only at library-dev time**: `vite build` emits a
self-contained bundle into `../src/gestaltdb/viz/static/`, which is committed
and shipped with the Python package. Library users never need Node or npm.

## Prereqs

Pinned toolchain (recorded in CI):

- Node 24 (`node --version`)
- npm 11 (`npm --version`)

## Workflow

```sh
npm ci
npm run build        # tsc + vite build + manifest -> ../src/gestaltdb/viz/static/
npm run check:licenses
npm run dev          # local dev server with the fixture payload in index.html
```

## Reproducibility

- `package-lock.json` is committed; `npm ci` restores exact versions.
- The build writes stable filenames (`gestaltdb-viz.js`/`.css`, no hashes)
  plus `viz-manifest.json` with `{app_version, bundle_*_sha256, d3_version,
  react_version, built_at}`. Rebuilds are byte-identical modulo `built_at`.
- Never import a dependency outside the permissive allowlist
  (`MIT`, `ISC`, `Apache-2.0`, `BSD-*`, `CC0`); `npm run check:licenses`
  fails CI otherwise.

## Payload contract

The app boots exclusively from `window.__GESTALTDB_VIZ__`, whose schema
mirrors `src/gestaltdb/viz/ir.py` (`VizGraph.to_dict()`). See `src/viz-types.ts`.
