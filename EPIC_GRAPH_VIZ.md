# Epic: Packaged JS-Based Graph Visualization

Status: **implemented** (branch `grestaltdb-graph-visualization`)

GitHub parent: #65

## Objective

Ship high-quality HTML/JS graph visualization together with the core
`gestaltdb` library so users can inspect property graphs, Cypher results, and
sampled ML subgraphs without installing any JavaScript toolchain.

The target capabilities are:

- a deterministic, JSON-serializable visualization intermediate
  representation (IR) built from `GraphDB` records, Cypher `QueryResult`
  rows, typed `sample_typed_subgraph` output, and `SamplerSnapshot` /
  `SamplerEngine` batches;
- a D3.js + React front end whose **source** lives in `web/` and whose
  **prebuilt bundles** are committed under `src/gestaltdb/viz/static/` and
  packaged with the library (sdist + wheel);
- self-contained offline `.html` artifacts (inlined JS/CSS/payload, no CDN,
  no network) plus first-class Jupyter rendering via `_repr_html_`;
- an interactive canvas (force layout, pan/zoom, label/type coloring,
  selection + property inspection, neighborhood expansion, Cypher highlight
  overlay) suitable for everyday graphs (budget: ~2k nodes / ~5k edges
  smooth on a commodity laptop);
- explicit large-graph policy (deterministic caps + sampling entrypoints)
  instead of silently dumping 100k+ node graphs into the DOM;
- zero JS runtime dependency for library users: the Python side uses only
  the standard library plus already-declared runtime dependencies to emit
  artifacts.

## Baseline and Non-Goals

GestaltDB currently has no visualization layer. Users inspect graphs via
`get_node` / `get_edge`, index lookups, Cypher `QueryResult.records`, typed
traversal (`neighbors_by_edge_type`, `sample_typed_paths`,
`sample_typed_subgraph`), and array-native `SamplerSnapshot` /
`SamplerEngine` batches. Notebooks under `notebooks/` render tables and
matplotlib-style plots, not interactive graphs. `docs/_static/` holds
hand-built SVGs for benchmark charts, not graph canvases.

This epic does not attempt a live backend-connected explorer, in-viz graph
editing, a general BI dashboard, GQL/OWL diagramming, or label/type
supernode aggregation in v1 (aggregation is an explicit post-v1 follow-up).
Temporal-version animation is out of scope, although the IR carries an
optional passthrough slot so the temporal epic (TKG) can reuse it later.

## Visualization Contract

- One IR backs every surface: static HTML file, Jupyter cell output, and
  any future export (`PNG`/`SVG` are client-side renderings of the same IR).
- The IR is deterministic: nodes sorted by stable ID, edges sorted by
  `(source, target, edge_id)`; repeated builds from the same graph state and
  options produce byte-identical payloads (modulo the envelope metadata).
- Payloads are JSON-safe and HTML-safe: all strings survive `json.dumps`
  plus HTML escaping without breaking out of the embedding `<script>` tag.
- Artifacts are offline: opening a saved `.html` via `file://` with no
  network must render fully. No CDN, no web fonts, no telemetry.
- The Python emitter gains no new mandatory runtime dependency. The JS
  toolchain (npm/Vite) runs only at library-dev/build time, never at
  user runtime.
- Large graphs never silently explode the DOM: any truncation is
  deterministic, counted, and surfaced as a visible banner plus machine
  readable `truncation` metadata.
- Only permissively licensed JS components are allowed:
  `MIT`, `ISC`, `Apache-2.0`, `BSD-*`, `CC0`. `GPL`/`AGPL`/`LGPL` (and
  unclear licenses) fail the license gate.

## Staged Delivery

| Stage | Features | Exit outcome |
| --- | --- | --- |
| 1. Foundation + pipeline | VIZ-01 to VIZ-02 | Deterministic Viz IR + licensed Vite/React/D3 pipeline with checked-in bundle |
| 2. Artifact + canvas | VIZ-03 to VIZ-04 | Offline self-contained HTML + React-shell/D3-force interactive canvas |
| 3. Interaction + Python API | VIZ-05 to VIZ-06 | Selection/inspect/expand + `GraphDB.visualize()` + notebook rendering |
| 4. Scale + ship | VIZ-07 to VIZ-08 | Caps/sampling entrypoints, packaging, docs, tests, release hardening |

## Dependency Graph

```text
VIZ-01 ─┬─> VIZ-02 ─> VIZ-03 ─> VIZ-04 ─> VIZ-05
        │                        │
        └─> VIZ-06 ──────────────┘

VIZ-01..VIZ-06 ─> VIZ-07 ─> VIZ-08
```

`VIZ-06` (Python API) needs the IR shape from `VIZ-01` and can be developed
against fixture payloads in parallel with the front end; it is finalized
against the real bundle from `VIZ-03`. `VIZ-07` and `VIZ-08` require all
preceding behavior to be in place.

## Stage 1: Foundation + Pipeline

### VIZ-01: Viz IR and Python Export Model

Dependencies: none.

Scope:

- Add `src/gestaltdb/viz/ir.py` with `VizNode`, `VizEdge`, `VizGraph`,
  and `TruncationInfo` dataclasses plus `to_dict()` / `to_json()` with
  deterministic ordering (nodes by `id`, edges by
  `(source, target, edge_id)`).
- Add pure-Python builders (stdlib only, no new runtime dependency):
  `from_nodes_edges(nodes, edges)`,
  `from_cypher_result(result)` (entity columns and `id`-like scalar pairs
  lower to nodes/edges; everything else becomes highlight metadata),
  `from_sampled_subgraph(subgraph)` for the `sample_typed_subgraph`
  `{"nodes", "edges", "paths"}` mapping,
  `from_sampler_batch(batch, snapshot)` mapping local batch rows through
  `node_ids_global` / `snapshot.external_node_id(...)` /
  `snapshot.global_triple_to_external(...)`.
- Define cap enforcement with deterministic keep order (sorted-ID prefix)
  plus exact `truncated: {nodes_dropped, edges_dropped, truncated: bool}`.
- Define optional passthrough slots (`valid`, `score`, `highlight`) reserved
  for the temporal epic and ML overlays; core rendering must ignore unknown
  slots without failing.
- Sanitize property values to JSON-safe primitives; drop or stringify
  anything else deterministically and record the coercion.

Example:

```python
from gestaltdb.viz.ir import VizGraph

viz = VizGraph.from_nodes_edges(nodes, edges, max_nodes=2000, max_edges=5000)
payload = viz.to_dict()
assert payload["truncation"]["truncated"] in (True, False)

sampled = graph.sample_typed_subgraph(["drug-1"], pattern)
viz2 = VizGraph.from_sampled_subgraph(sampled)
```

Acceptance criteria:

- Repeated builds from identical inputs are byte-identical (`to_json()`
  equality across runs with fixed inputs).
- Cap behavior is deterministic: same keep set, exact drop counts, and a
  `truncated` flag whenever anything was dropped.
- Malicious strings (`</script>`, quotes, unicode separators) round-trip
  through `to_json()` without breaking JSON parsing.
- Existing graph storage formats and behavior do not change; no new
  mandatory runtime dependency is introduced.

### VIZ-02: JS Scaffold, Vite Build, and License Gate

Dependencies: VIZ-01 (IR JSON schema).

Scope:

- Add `web/` source tree: React + TypeScript + Vite + D3 front end
  (`react`, `react-dom` MIT; `d3` family ISC; `vite`, `typescript` MIT).
  The app boots from `window.__GESTALTDB_VIZ__` payload and nothing else.
- Add `web/scripts/check-licenses.py` (or npm `license-checker`
  configuration) enforcing the permissive allowlist
  (`MIT`, `ISC`, `Apache-2.0`, `BSD`, `BSD-2-Clause`, `BSD-3-Clause`,
  `CC0`, `CC-BY-3.0` for D3-adjacent assets only) and failing CI on
  `GPL`/`AGPL`/`LGPL`/unknown.
- Wire the build to emit versioned artifacts into
  `src/gestaltdb/viz/static/gestaltdb-viz.{js,css}` plus a
  `viz-manifest.json` recording `{app_version, bundle_hash, d3_version,
  react_version, built_at}`.
- Add `web/README.md` with pinned toolchain (`node --version`, `npm ci`),
  `npm run build`, and reproducibility notes.
- Commit the built bundle so Python tests and packaging never need npm.

Example:

```sh
cd web
npm ci
npm run build        # -> ../src/gestaltdb/viz/static/
npm run check:licenses
```

Acceptance criteria:

- `npm ci && npm run build` reproduces the committed bundle
  byte-identically (modulo the `built_at` manifest field) on a pinned
  toolchain.
- The license gate passes with only allowlisted licenses and fails when a
  probe `GPL-3.0` dependency is added.
- No Python runtime import touches `web/`; tests pass with npm absent.

## Stage 2: Artifact + Canvas

### VIZ-03: Self-Contained Offline HTML Artifact Builder

Dependencies: VIZ-01, VIZ-02.

Scope:

- Add `src/gestaltdb/viz/html.py` with `build_html(viz_graph, options)`
  using stdlib only (`json`, `html.escape`, `pathlib`): one HTML shell,
  inlined CSS/JS from `viz/static/`, payload assigned to
  `window.__GESTALTDB_VIZ__` via `json.dumps` with `</script>`-safe escaping.
- Add `save_html(path)` creating parent directories, writing UTF-8, and
  returning the resolved path.
- Add a freshness check: Python reads `viz-manifest.json` and warns when
  the bundle hash does not match the committed manifest.
- Guarantee offline behavior: no external URLs (scripts, styles, fonts,
  images), no `eval`, inline `Content-Security-Policy`-compatible markup.

Example:

```python
from gestaltdb.viz.html import save_html
from gestaltdb.viz.ir import VizGraph

viz = VizGraph.from_nodes_edges(nodes, edges)
path = save_html(viz, "/tmp/graph.html", title="Drug graph")
```

Acceptance criteria:

- The saved file renders fully over `file://` with networking disabled
  (no failed requests in a headless-browser or link-check pass).
- An XSS probe (labels/properties containing `</script><script>alert(1)`)
  renders as inert text and the payload still parses.
- Bundle-manifest mismatch produces a warning, not silent stale output.

### VIZ-04: Core Canvas — React Shell + D3 Force Layout

Dependencies: VIZ-03.

Scope:

- Implement the front end: D3 force simulation (tick + drag + pin/unpin),
  pan/zoom, directed edge arrows, node labels, label/type color assignment
  with legend, node/edge counters, and layout controls (charge, link
  distance, pause/resume, refit).
- Implement theming (light/dark) and a colorblind-safe default palette;
  label/type colors must be stable across reloads for the same payload.
- Meet the performance budget: ~2k nodes / ~5k edges interactive on a
  commodity laptop (canvas or SVG-with-virtualization decision documented
  in the issue; force layout must be pausable).
- Render the `truncation` banner whenever the payload reports dropped
  nodes/edges.

Example:

```ts
// web/src/main.tsx
declare global { interface Window { __GESTALTDB_VIZ__: VizPayload } }
renderApp(document.getElementById("root")!, window.__GESTALTDB_VIZ__);
```

Acceptance criteria:

- Pan, zoom, drag, pin, pause, refit, theme toggle, and legend filtering
  all work from a saved `.html` opened offline.
- Colors are deterministic per label/type set; dark mode meets a documented
  minimum contrast for labels.
- The truncation banner appears if and only if the payload is truncated,
  showing exact dropped counts.

## Stage 3: Interaction + Python API

### VIZ-05: Selection, Inspection, Search, and Cypher Overlay

Dependencies: VIZ-04.

Scope:

- Add click-select with a properties side panel (labels, id, all scalar
  properties), hover tooltip, and keyboard navigation (tab order, arrow
  movement documented, `Esc` clears selection).
- Add search by node id/label and prefix filtering by node label and
  edge type; filters update counts and keep layout stable.
- Add client-side 1-hop neighborhood expansion from the embedded payload
  (no backend round trip in v1): double-click (or button) reveals hidden
  neighbors already present in the payload.
- Add highlight overlay: payload `highlight: {nodes, edges}` (emitted by
  `from_cypher_result`) renders matched elements distinctly; everything
  else dims but stays visible.

Example:

```python
result = graph.query('MATCH (a:Person)-[:knows]->(b) RETURN a, b LIMIT 25')
viz = VizGraph.from_cypher_result(result)  # matched ids -> highlight set
```

Acceptance criteria:

- Selecting any node/edge shows its exact stored properties (no lossy
  formatting of numbers/booleans) with a clear empty-properties state.
- Search + label/type filters compose and report visible/total counts.
- Highlight mode dims non-matched elements while keeping them navigable,
  and turning it off restores the full view.

### VIZ-06: Public Python API and Jupyter Rendering

Dependencies: VIZ-01 (shape), VIZ-03 (finalized against the real bundle).

Scope:

- Add `src/gestaltdb/viz/api.py` with `VizOptions` (`max_nodes=2000`,
  `max_edges=5000`, `height=600`, `theme="light"|"dark"`,
  `layout={charge, link_distance}`, `title`, `show_properties`) and a
  `VizFigure` exposing `save(path)`, `as_html()`, and `_repr_html_()`
  (iframe via `srcdoc`, height-bounded, no external requests).
- Add thin conveniences that delegate to the IR builders without importing
  JS or notebook machinery at module import time:
  `visualize_nodes_edges()`, `visualize_query(cypher, parameters=None)`,
  `visualize_sample(seeds, pattern)`, and (optionally, reusing the
  existing `GraphDB` class) `GraphDB.visualize(...)` wiring decided in the
  issue.
- Keep imports lazy (`viz` must not slow down or break `import gestaltdb`
  when optional notebook front ends are absent).

Example:

```python
from gestaltdb.viz.api import VizOptions, visualize_query

fig = visualize_query(
    graph, 'MATCH (a:Person)-[:knows]->(b) RETURN a, b LIMIT 25',
    options=VizOptions(title="Knows subgraph", theme="dark"),
)
fig.save("/tmp/knows.html")   # shareable artifact
fig                       # Jupyter: inline interactive figure
```

Acceptance criteria:

- `save()` output equals `as_html()` bytes written to disk; both render
  offline.
- Jupyter rendering works via `_repr_html_()` with no new mandatory
  dependency and no top-level import-time side effects.
- All public entrypoints validate caps up front and surface truncation in
  both the returned figure metadata and the rendered banner.

## Stage 4: Scale + Ship

### VIZ-07: Large-Graph Caps and Sampling Entrypoints

Dependencies: VIZ-01 through VIZ-06.

Scope:

- Harden the cap policy end to end: defaults (`2000` nodes / `5000`
  edges), user-overridable `VizOptions`, deterministic sorted-ID keep
  order, exact drop accounting, UI banner + `truncation` metadata, and a
  Python `warnings.warn` (or logged notice decided in the issue) whenever
  truncation occurs.
- Promote sampling-first exploration as the documented path for large
  graphs: `visualize_sample(seeds, SamplingPattern)` over
  `sample_typed_subgraph`, and `visualize_query(...)` with `LIMIT` for
  Cypher-driven slices; document the `SamplerSnapshot` batch path
  (`node_ids_global` → external IDs) for ML subgraph inspection.
- Add guardrails: refuse (with an actionable error) to build payloads
  beyond an absolute ceiling (e.g. 2x caps) instead of freezing the
  browser; point at sampling in the message.

Example:

```python
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.viz.api import visualize_sample

pattern = SamplingPattern([
    SamplingHop("binds", direction="out", sample_size=5),
    SamplingHop("associated_with", direction="out", sample_size=3),
])
fig = visualize_sample(graph, ["drug-1"], pattern)
fig.save("/tmp/sample.html")
```

Acceptance criteria:

- A 100k-node fixture Export attempt truncates deterministically, warns,
  banners exact counts, and never produces an unloadable artifact by
  default.
- Sampling and Cypher-slice paths produce small, fully interactive
  artifacts from the same large fixture.
- Over-ceiling requests raise an actionable error naming the sampling
  alternative (message asserted in tests).

### VIZ-08: Packaging, Docs, Tests, and Release Hardening

Dependencies: VIZ-01 through VIZ-07.

Scope:

- Extend `[tool.setuptools.package-data]` in `pyproject.toml` to ship
  `viz/static/*` and `viz/templates/*`; assert the bundle is present in
  both sdist and wheel (`python -m build` inspection in CI or a packaging
  test).
- Add `docs/visualization.rst` (Sphinx, linked from `docs/index.rst`),
  runnable `EXAMPLES.md` entries, and a packaged agent-docs topic so
  agents can discover `visualize_*` without reading the bundle source.
- Add the Python test suite: IR determinism/caps/XSS vectors, offline HTML
  assertions (no `http(s)://` references outside allowlisted namespaces),
  manifest freshness, API/`_repr_html_` smoke tests, and sampling-path
  coverage using existing `LevelDBStore` fixtures.
- Add client-side export (PNG/SVG snapshot, JSON payload download —
  all local, no server), finalize keyboard/a11y pass, and record a
  `CHANGELOG.md` entry marking viz stable vs experimental surfaces.

Example:

```python
# tests/test_viz.py (illustrative)
fig = visualize_query(graph, "MATCH (n) RETURN n LIMIT 5")
html = fig.as_html()
assert "window.__GESTALTDB_VIZ__" in html
assert "</script><script>" not in html
```

Acceptance criteria:

- Fresh `pip install` from a built wheel renders an artifact with no npm,
  no network, and no new mandatory runtime dependency.
- `uv run pytest tests/test_viz.py -q`, the docs drift check
  (`.opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py`),
  and the Sphinx build all pass.
- `CHANGELOG.md`, `EXAMPLES.md`, Sphinx, and agent docs describe only
  implemented behavior.

## Cross-Cutting Engineering Rules

Every VIZ feature must:

- preserve existing behavior and add no mandatory runtime dependency
  (stdlib-only Python emission; JS toolchain is dev/build-time only);
- use only allowlisted permissively licensed JS (`MIT`/`ISC`/`Apache-2.0`/
  `BSD`/`CC0`) with the license gate passing;
- keep artifacts offline-capable and `file://`-openable with no external
  requests;
- escape all user data at the Python boundary and keep rendering free of
  `innerHTML`-style injection sinks for label/property strings;
- test determinism, cap/truncation accounting, and XSS vectors where user
  strings reach the DOM;
- update user, Sphinx, and agent-facing documentation only for implemented
  APIs.

## Current Implementation Status

- [x] Epic and dependency plan drafted.
- [x] `VIZ-01` Viz IR and Python export model (#66).
- [x] `VIZ-02` JS scaffold, Vite build, and license gate (#67).
- [x] `VIZ-03` offline HTML artifact builder (#68).
- [x] `VIZ-04` core canvas (React shell + D3 force) (#69).
- [x] `VIZ-05` selection/inspection/search/highlight (#70).
- [x] `VIZ-06` public Python API + Jupyter (#71).
- [x] `VIZ-07` large-graph caps + sampling entrypoints (#72).
- [x] `VIZ-08` packaging, docs, tests, hardening (#73).
