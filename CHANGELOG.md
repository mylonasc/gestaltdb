# Changelog

## Unreleased

- Added optional NetworkX import/export and static GEXF 1.1draft/1.2draft
  loading (#437), including parallel-edge preservation, explicit ID/label/type
  mappings, opt-in undirected expansion, hardened bounded XML parsing, and
  preflight validation with structured partial-write reporting.
- Replaced engine positive-code sets and temporal group dictionaries with exact
  sorted triple membership (#429). New non-temporal snapshots persist the
  authenticated memory-mappable index; temporal snapshots reuse history keys.
  Legacy non-temporal snapshots build a sorted RAM index once. Membership
  lookup now uses binary search; candidate buffers and validation still allocate.
- Added versioned sparse sampler relation/temporal directories (#427), avoiding
  dense node × relation offsets on new builds while retaining legacy dense
  v1/v2 loading and seeded candidate order. Binary-search lookup trades latency
  for directory memory. Exact positive membership is handled separately above.
- Hardened serializer decoding of malformed tagged payloads: JSON and
  MessagePack deserialization now raise `ValueError` instead of leaking
  `AttributeError`/`KeyError`, and serialization tolerates non-string tag values.
- Tightened test false-greens: the serializer fuzz target no longer swallows
  unexpected codec errors or silent roundtrip drift, the lint workflow now runs
  the strict consumer typing test and the typed-module mypy ratchet, backend
  fixtures no longer convert constructor regressions into skips, and broad
  session/inference exception assertions now require their domain error types.

## 0.16.0 — generation-pinned reasoning-aware Cypher

- Raised the optional MessagePack minimum to 1.2.3 and locked 1.2.4 to address
  CVE-2026-114613 (map-deserialization memory-leak denial of service).
- Added explicit generation-pinned, read-only Cypher over selected logical
  inference facts via `ReasoningQueryView` (#395). Supports node/one-hop
  patterns, explicit asserted-only/plus-inferred modes, source freshness,
  projection/read caps and origin metadata; ordinary Cypher is unchanged.

## 0.15.0 — native Python typing

- Added native service, entity, storage, temporal, sampling and Cypher typing
  contracts, packaged `py.typed`, dtype-aware arrays, structured dictionary
  records and focused read/write capability protocols (#410).
- Added positive/negative static consumer fixtures, per-module typing ratchets
  and a measured precision badge. Arbitrary properties and optional native
  boundaries remain intentionally dynamic; whole-library mypy is not yet clean.
- Preserved runtime behavior in the final annotation slices and retained the
  reasoning correctness fixes from `0.14.0rc3` during integration.

## 0.14.0rc3 — reasoning correctness fixes

- Preserved complete temporal conclusions when proof budgets are exhausted
  (#393): `TemporalClosure` separates logical conclusions from bounded
  derivations, warns with `TemporalProofTruncatedWarning`, and reports missing
  evidence instead of silently dropping answers.
- Made typed graph projection fail closed on identity collisions and renamed
  source references to unambiguous JSON components (#396). Merging subjects now
  requires an explicit `identity_collision="merge"` opt-in, and colon-bearing
  IDs or property names cannot alias.
- Included main's stratified aggregate proof-collector fix (#411/#412), so
  aggregate conclusions carry witnesses during stratified evaluation.

## 0.14.0rc2 — integrated reasoning and visualization testing candidate

- Added bounded typed n-ary derivation tracking and proof verification (#392),
  temporal negation/aggregates over interval partitions (#393), declarative
  structural validation (#394), opt-in inferred graph queries (#395), and typed
  graph-to-fact projection (#396).
- Added explicit historical native claim-index locator admission (#369), with
  complete catalog authentication, canonical conformance, cumulative budgets,
  exact version evidence and correction/retraction handling. Full authentication
  still reads unrelated records; no actual-adapter acceleration or automatic
  native-source adoption is claimed. Larger-scale measurement remains #194.
- Integrated offline reasoning/proof/ontology visualization, deterministic tree
  layouts, shape filters and gentle interaction behavior (#404).
- Added the portable read-only issue atlas (#304); foreign-repository issue
  numbers cannot alias local dependency/parent nodes.
- Reconciled integration features with the scoped services already on main,
  preserving the service decomposition, visualization and CI release gates.
  Extracted typed result contracts without changing their existing import paths.
- Updated pinned CI/publishing actions and benchmark-only LMDB/pycparser pins
  (#316–#322). This remains an opt-in prerelease, not full OWL/SHACL or universal
  crash-recovery/performance certification.

## 0.14.0rc1 — GraphDB service decomposition testing milestone

Published prerelease for opt-in user testing (2026-10-25). Public behavior and
signatures are unchanged: this milestone decomposes the `GraphDB` facade into
scope-owned services without altering storage, transaction, or query semantics.

- Split temporal, epistemic, and Horn-rule operations out of the facade into
  `graph.temporal`, `graph.epistemic`, and `graph.rules` engine services (#388).
  Internal callers (read views, reasoning adapters, stored rules, Cypher
  `kg.entails`) consume the engines directly; `GraphDB` keeps no wrapper methods.
- Moved current-graph node/edge CRUD and batched writes into `NodeRepository`/
  `EdgeRepository` over explicit `StorageSession` scopes (`graph.nodes`,
  `graph.edges`), with ordinary transaction coordination and no facade
  redispatch (#388).
- Moved current secondary-index lookup, rebuilds, and property-index DDL into
  `IndexService`/`IndexReader` (`graph.indexes`); configuration publishes only
  after successful commit. Snapshot views expose a read-only `view.indexes` (#388).
- Moved columnar ingestion into `ArrowIngestor`/`PolarsIngestor`
  (`graph.ingest.*`) plus a graph-wide `IngestionCoordinator` for mode dispatch
  and deferred-index rebuilds; native/JSON fast paths and rollback semantics
  are unchanged (#388).
- Moved live typed-adjacency traversal and reservoir sampling into
  `TraversalService`/`AdjacencyReader` (`graph.traversal`, read-only
  `view.traversal`) and decoded index-backed reads into `EntityLookup`
  (`graph.lookup`, `view.lookup`) (#388).
- Hardened explicit execution admission, bounded input consumption, and exact
  evidence handoff across binary, prepared, and typed reasoning workflows.
- Added authenticated durable-session resume, current-graph and finite-range
  admission, bound interval publication, and inferred-sampler artifact checks.
  These remain separate opt-in contracts, not transparent engine substitution.
- Added pinned frontier queries and explicit native full-record claim extraction.
  Indexed Python remains the default; native/Polars measurements do not imply
  automatic adoption, universal speedups, or hard RSS limits.
- Added process-termination and cross-process regression evidence. This does not
  establish power-loss, torn-page, network-filesystem, or arbitrary
  mid-publication crash recovery guarantees. See reliability scope record #381.
- Added explicit authenticated typed-point session admission with exact active
  evidence/catalog checks, shared generation/state pins and bounded cancellation
  controls (#248). This verifies source input, not closure soundness.
- Fixed incremental and persisted reasoning sessions dropping active evidence
  when a retraction's valid interval excludes the pinned query time (#327).

## 0.13.0rc1 — reasoning engine testing milestone

Published prerelease for opt-in user testing (2026-10-23). This
is a minor feature milestone, not a claim that the reasoning roadmap is complete.

- Added the standalone indexed Python reasoning engine and ontology compiler,
  inspectable/prepared plans, typed n-ary rules and strict literals, scoped
  stratified negation, aggregates and selective demand queries.
- Added explicit negative validation, snapshot-local equality, bounded
  explanations and named ontology subsets; no full OWL or SHACL claim.
- Added authenticated graph/claim extraction, staged materialization generations,
  support-safe maintenance, incremental sessions and bounded temporal range
  closure/publication with explicit provenance and fail-closed capabilities.
- Added opt-in Arrow exchange and experimental closure-only Polars execution;
  indexed Python remains the default. No bespoke native executor or out-of-core
  reasoning is introduced.
- Fixed rule normalization, aggregate row/limit semantics, signed-zero matching,
  extraction policy/identity, temporal revision reuse, publication freshness and
  mutable/corrupt temporal commit-cache handling (#207–#217, #244–#247, #282).
- Includes the integration branch's existing opt-in hierarchy/query and sampling
  enhancements; these do not imply automatic reasoning-aware query rewriting or
  framework-native heterogeneous graph adapters.
- Added release validation of installed artifacts, supported Python versions and
  baseline database reopen/transition workflows. See
  `docs/reasoning-testing.rst` for scope, installation and feedback guidance.
- Featured high-performance sampling and indexed property-graph filtering in the README, docs landing/navigation, dedicated filtering guide, sampling API map, examples, and packaged agent guides; clarified snapshot costs, filtering stages, and performance boundaries.
- Fixed the sampler benchmark's temporary output path and added a CLI smoke regression so the documented command runs with immutable snapshot output rules.

## 0.12.1

- Fixed JSON bulk edge ingestion by storing legacy adjacency IDs as strings consistently with single-edge writes, normalizing older byte-ID adjacency when touched (#109).
- Fixed `correct_claim(Claim, ...)` silently ignoring explicit confidence/provenance overrides; both object and ID forms now validate and apply them consistently (#110).
- Reject retractions of unknown claim, node, or edge identities before allocating a temporal commit, including batched writes and deferred-index workflows (#111).

## 0.12.0

- Reconciled the previously unmerged sampling policies with current temporal snapshots: weighted neighbors and edge seeds, node seeds, heterogeneous layers, random/metapath walks, and external-ID lookup/conversion helpers.
- Authenticated weights and lookup arrays in v2 snapshots while retaining old v1/v2 compatibility; preserved temporal filtering and version-row ambiguity.
- Added sampling validation and self-loop/frontier fixes, plus generated API cards for packaged agent-doc searches.
- Refreshed locked optional documentation/benchmark dependencies to PyJWT 2.15.1, multidict 6.9.1, and urllib3 2.8.0, addressing 18 dependency advisories. These packages are not part of the locked base/backend runtime; this lockfile refresh does not impose new transitive minimum versions on pip consumers.
- Updated the visualization build dependency source-map-js to 1.2.2 to address GHSA-68fv-2mgg-jv7q and regenerated the packaged bundle provenance. The affected source-map parser is build-time-only, not a runtime dependency of the offline viewer.
- Updated GitHub Actions dependencies used for tests, documentation, visualization, and publishing.

## 0.11.0

- Improved the packaged offline graph visualization: configurable node/edge property labels, distinct selectable paths for parallel/reciprocal/self-loop relationships, property-driven node size/color and edge width/color, directed multi-hop neighborhood focus, local saved view state, and dependency-free static SVG output via `VizFigure.as_svg()`/`save_svg()`.
- Fixed PNG export content-policy compatibility and preserved theme/label styling in SVG/PNG exports, with inline export error reporting.
- Added export scope/background/dimension/scale controls, explicit pin actions, layout reset/visible-only relayout, and responsive/keyboard accessibility improvements.
- Added offline Playwright browser regressions, source-aware bundle provenance with packaged third-party notices, and a visualization CI workflow.
- Expanded `notebooks/09_graph_visualization.ipynb` and the packaged visualization agent-docs topic.

## 0.10.2

- Hardened the repository for the OpenSSF Scorecard: least-privilege workflow token permissions, commit-SHA-pinned GitHub Actions, `SECURITY.md` with a private vulnerability reporting process, Dependabot for GitHub Actions and Python dependencies, CodeQL static analysis on push/PR/weekly, Sigstore-signed release distributions with SLSA-style build attestations attached to GitHub releases, `CODEOWNERS`, and ClusterFuzzLite continuous fuzzing of the JSON/MessagePack deserializers.

## 0.10.1

- Added automated Trivy vulnerability scanning over the cartesian product of KV backends (LevelDB, LMDB, RocksDB) and serializers (builtin JSON/Pickle, MessagePack, Protobuf); reports are published as ``<backend>-<serializer>`` artifacts and fail on HIGH/CRITICAL findings.

## 0.10.0

- Made LMDB, LevelDB/Plyvel, RocksDB/PyRex, MessagePack, and Protobuf fully optional, independently composable extras; added ``backends`` and ``serializers`` convenience bundles.
- Raised the MessagePack security floor to ``msgpack>=1.2.1`` and the supported Python floor to 3.10.
- Added managed-store dependency preflight before filesystem mutation, actionable extra-specific errors, and core/combination installation CI coverage.

## 0.9.0

- Raised the supported Python floor from 3.9 to 3.10 and added CI coverage for Python 3.10 through 3.13.
- Added durable temporal commit reservations, cross-process writer serialization, orphan-publication recovery, and reverse range-index seeks for bounded system-time horizon lookup.
- Added valid-start/valid-end candidate intersection and an interval-heavy benchmark metric to avoid decoding expired temporal versions.
- Added copy-on-build temporal index generations with visible-history checksums, atomic activation, interrupted-build safety, and prior-generation cleanup.
- Enforced a documented collision-free 511-byte portable limit for all encoded secondary and temporal index keys.
- Added recursive tagged persistence for Cypher temporal property values across every serializer, with stable exact/range index encodings and reopen compatibility.
- Added unified mutable-state read snapshots: LMDB MVCC identity and provenance, transaction-pinned PyRex reads when supported, explicit LevelDB failure, and coherent Cypher/sampler integration.
- Added fail-closed, retryable migration from deprecated `TimeIndexedEdge` records to immutable temporal edge history through `GraphDB.migrate_time_indexed_edges`.
- Added packaged offline graph visualization: deterministic `VizGraph` IR builders (nodes/edges, Cypher results, sampled subgraphs, sampler batches), self-contained `.html` artifacts with an inline-only CSP, and a prebuilt D3.js + React bundle shipped via `package-data` (no JS toolchain or network needed at runtime).
- Added an interactive canvas (D3 force layout with pin/zoom/refit/pause, deterministic colorblind-safe legend, selection + property inspector, search, label/type filters, 1-hop focus, Cypher highlight overlay) with client-side SVG/PNG/JSON export.
- Added the `gestaltdb.viz.api` surface (`VizOptions`, `VizFigure`, `visualize_nodes_edges/query/sample/sampler_batch`) with Jupyter `_repr_html_`, `GraphDB.visualize(cypher=...)` / `GraphDB.visualize(seeds=..., pattern=...)`, deterministic caps (2000 nodes / 5000 edges) with `TruncationWarning` plus exact on-canvas banners, and `VizCapExceededError` ceilings (default 2x caps) pointing at sampling.
- Added `web/` build-time source (Vite + TypeScript, stable filenames, byte-identical rebuilds, `viz-manifest.json` freshness checks) with a permissive-license gate (`MIT`/`ISC`/`Apache-2.0`/`BSD`/`CC0`).
- Added `docs/visualization.rst`, `EXAMPLES.md` entries, a packaged `visualization` agent-docs topic, and `AGENTS.md` contributor guidance for viz.

## 0.8.0

- Added extended Cypher expressions, strict three-valued semantics, stable cross-type ordering, and scalar expressions containing aggregates.
- Added `OPTIONAL MATCH`, `UNWIND`, `UNION [ALL]`, correlated `CALL { ... }`, relationship property maps, variable-length and shortest paths, named paths, and label expressions.
- Added Cypher writes through `CREATE`, `SET`, `REMOVE`, `DELETE`, `MERGE`, and `FOREACH`, with backend-aware transaction handling.
- Added persisted node uniqueness/not-null constraints plus `SHOW CONSTRAINTS` and `SHOW INDEXES` schema introspection.
- Hardened planner join ordering, exact/range index reuse, bulk range-index maintenance, and write behavior around terminal `LIMIT`.
- Generalized registered procedure calls with `YIELD` aliases and parameters, while preserving an allowlisted execution boundary.
- Added actionable migration errors for deferred GQL quantified relationships and path patterns.
- Rewrote Cypher user and packaged agent documentation with current read/write examples and limitations.

## 0.7.0

- Replaced regex-based Cypher parsing with a Lark grammar supporting comments, Unicode and escaped names, source-located errors, expression precedence, multiple inline properties, and parameterized pagination.
- Added arithmetic, expression comparisons, boolean and string predicates, regex matching, alias-aware ordering, Cypher three-valued null logic, and relationship-isomorphism enforcement for fixed paths.
- Corrected logical plans for `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT`, and removed unsafe intermediate `LIMIT` pushdown during multi-hop traversal.
- Added generalized fixed-length patterns with anonymous elements, optional relationship types, bracketless relationships, endpoint labels/properties, unanchored multi-hop traversal, comma-separated pattern parts, and canonical all-edge iteration for untyped matches.
- Added typed Cypher binding/result rows and executable projection, sorting, distinct, skip, and limit operators, then made the planner-derived logical plan authoritative for query execution.
- Added a canonical, source-spanned clause AST through `gestaltdb.cypher.parse_ast()` while preserving the existing specialized `parse()` results.
- Added ordered Cypher semantic scope analysis (`cypher_semantics.analyze_query`) for `MATCH`/`WHERE`/`RETURN`, with shared source-located errors in `cypher_errors`.
- Added general projection expressions (arithmetic, literals, parameters, lists, maps) in `RETURN` and `ORDER BY`, with deterministic unaliased column names.
- Added `WITH` with scope replacement, clause-local `WHERE`, and local `DISTINCT`/`ORDER BY`/`SKIP`/`LIMIT`, executed through staged `MatchStep`/`ProjectItems` plans.
- Added a shared Cypher value identity (`cypher_value_key`) for result `DISTINCT`, separating booleans from numbers and keying entities by kind plus stable ID.
- Added core Cypher aggregates (`count`, `collect`, `sum`, `avg`, `min`, `max`) with `count(*)`, aggregate `DISTINCT`, implicit grouping, and documented null/empty-input behavior in `WITH` and `RETURN`.

## 0.6.2

- Added a `backends` packaged agent-docs topic covering `GraphDB.create/open`, manifests, LevelDB/PyRex/RocksDB selection, and DB inspection (`manifest`, `index_statistics`, entity properties).
- Made `python -m gestaltdb.agent_docs search` return bounded, ranked, paged results with context snippets (`--limit`, `--page`, `--context`).
- Fixed the packaged skill/`Cypher` benchmark assertion to use tuple-safe `QueryResult.columns` comparison.
- Extended the agent benchmark runner with Qwen 27B max-context config, per-run context metadata, HTML trace viewer with context usage, packaged skill installation into worktrees, and a robust judge JSON extractor.
- Added persistence/inspection and PyRex/RocksDB Polars ingestion benchmark tasks.

## 0.6.0

- Added `AGENTS.md` as a concise agent-facing map of the repository, public API modules, backend guidance, indexing rules, ingestion modes, Cypher support, sampling APIs, and verification commands.
- Added `EXAMPLES.md` with runnable usage patterns for graph creation, manifest-backed stores, backend and serializer selection, indexing, read-only Cypher, typed traversal sampling, Arrow/Polars ingestion, and sampler snapshots.
- Added an opencode documentation-maintenance skill for keeping `AGENTS.md`, `EXAMPLES.md`, and the package docstring aligned with implementation and Sphinx docs.
- Expanded the `gestaltdb` package docstring with import guidance, a minimal write/query example, and key usage notes for developers and agents.
