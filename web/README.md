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
npm run check:bundle # rebuild and detect stale committed assets/source provenance
npm run dev          # local dev server with the fixture payload in index.html
```

## Offline browser regressions

```sh
npx playwright install chromium
VIZ_TEST_PYTHON=../.venv/bin/python npm test
```

Tests generate a production artifact using the selected Python environment and
open it over `file://` with networking disabled. `VIZ_TEST_PYTHON` defaults to
`python3`; the environment needs GestaltDB's Python dependencies. Playwright and
Chromium are development-only and are not required by library users.

The canvas uses shared `geometry.ts` paths for relationship strokes, hit targets,
and labels, and `labels.ts` for property/fallback formatting. React owns controls
and selection; D3 owns the SVG children and force simulation. Exports resolve
computed styles into a standalone SVG before serializing or rasterizing it.

## Reproducibility

- `package-lock.json` is committed; `npm ci` restores exact versions.
- The build writes stable filenames (`gestaltdb-viz.js`/`.css`, no hashes)
  plus `viz-manifest.json` with asset/notice hashes, resolved dependency and
  toolchain versions, source/config/lockfile hashes, and build time.
  Asset hashes are reproducible with the locked toolchain; build-time/toolchain
  metadata records the actual environment. `check:bundle` compares asset/source
  hashes, rather than timestamps or environment-specific toolchain metadata.
- Never import a dependency outside the permissive allowlist
  (`MIT`, `ISC`, `Apache-2.0`, `BSD-*`, `CC0`); `npm run check:licenses`
  fails CI otherwise.
- The build gates all locked dependencies and generates full runtime license
  texts in `third-party-notices.txt`. Python embeds these texts in generated
  HTML, where users can download them. Installed dependencies are checked by
  lockfile path/version, without deduplicating distinct nested versions.

## Components

- `ExportControls`: image scope/dimensions/transparency/scale and local view files.
- `ExplorationControls`: property search, secondary labels, directed bounded focus.
- `AppearanceControls` / `appearance.ts`: serializable, bounded attribute mappings.
- `session.ts`: topology-aware whole-file validation before view restoration.
- `GraphCanvas`: SVG scene and imperative layout snapshot/restore/pin/relayout handle.
- `geometry.ts` / `labels.ts`: shared relationship geometry and label formatting.

View state contains no executable code and imports no graph data. It preserves
the active simulation subset as well as coordinates, pins, zoom, settings, and
filters. JSON graph export remains separate. Python SVG accepts the saved node
coordinates but does not capture browser viewport/filter state.

## Payload contract

The app boots exclusively from `window.__GESTALTDB_VIZ__`, whose schema
mirrors `src/gestaltdb/viz/ir.py` (`VizGraph.to_dict()`). See `src/viz-types.ts`.
