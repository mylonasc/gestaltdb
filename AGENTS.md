# GestaltDB Agent Guide

This repository contains `gestaltdb`, a pure Python graph database toolkit for attributed graphs. It stores nodes, directed edges, native node labels, typed adjacency records, secondary indexes, and read-optimized sampler snapshots on embedded key-value backends.

Use this file when you are an agent trying to understand or modify the library with minimal probing. For runnable snippets, see `EXAMPLES.md`. For user-facing narrative docs, see `docs/`.

## Start Here

- Core package: `src/gestaltdb/`
- Public graph model and database: `src/gestaltdb/graphdb.py`
- Storage backends: `src/gestaltdb/kvstores.py`
- Serializers: `src/gestaltdb/serializers.py`
- Columnar ingestion containers and enums: `src/gestaltdb/ingestion.py`
- Canonical temporal values and selection semantics: `src/gestaltdb/temporal.py`
- Immutable temporal version records and write descriptors: `src/gestaltdb/versioning.py`
- Epistemic claim values and four-valued status: `src/gestaltdb/epistemic.py`
- Cypher engine: `src/gestaltdb/query_engine/cypher/`
- Legacy Cypher import shims: `src/gestaltdb/cypher.py` and `src/gestaltdb/cypher_*.py`
- Sampling API: `src/gestaltdb/sampling/`
- Sphinx docs: `docs/`
- Tests: `tests/`
- Modular subsystem guides: `.opencode/skills/gestaltdb-docs-maintainer/references/`

## Import Map

Prefer explicit submodule imports for core graph objects:

```python
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.serializers import JSONSerializer, PickleSerializer
```

The package root currently re-exports selected ingestion, Cypher result, and sampling helpers:

```python
from gestaltdb import EdgeList, IndexMaintenanceMode, NodeList, QueryResult
from gestaltdb import HardNegativeConfig, SamplerEngine, SamplerSnapshot
from gestaltdb import SamplingHop, SamplingPattern
from gestaltdb import TemporalContext, TemporalInstant, TemporalInterval
from gestaltdb import TemporalDate, TemporalDuration, TemporalLocalDateTime
from gestaltdb import TemporalLocalTime, TemporalTime
from gestaltdb import GraphReadView, ReadViewProvenance
from gestaltdb import Claim, ClaimObjectKind, ClaimPolarity, ClaimStatus
```

Do not assume `GraphDB`, `Node`, `Edge`, backend classes, or serializer classes are available from `import gestaltdb`; import them from their modules unless the API is intentionally changed.

## Core Concepts

- `Node(node_id=..., labels=[...], properties={...})` stores a stable ID, deduplicated native labels, and arbitrary properties.
- `Edge(edge_id=..., source=..., target=..., properties={"type": ...})` stores a directed edge. Typed traversal and relationship Cypher use `edge.properties["type"]`.
- `GraphDB(store, serializer, indexed_node_properties=None, indexed_edge_properties=None)` is the main object for reads, writes, indexes, Cypher, ingestion, and sampling snapshot creation.
- `GraphDB.create(path, backend="pyrex", serializer="json", ...)` creates a self-describing database directory with `gestaltdb_manifest.json`; `GraphDB.open(path)` reopens it.
- Backends implement the `KVStore` interface. Current backends are `LMDBStore`, `LevelDBStore`, and `PyRexStore`.
- Serializers convert graph entities to bytes. Current serializers are `PickleSerializer`, `JSONSerializer`, `MessagePackSerializer`, and `ProtobufSerializer`.
- `TemporalInstant`, `TemporalInterval`, and `TemporalContext` define timezone-aware UTC-microsecond instants, half-open `[start, end)` intervals, and point/window matching.
- Cypher temporal expressions return immutable `TemporalDate`, `TemporalTime`, `TemporalLocalTime`, `TemporalLocalDateTime`, `TemporalInstant`, and `TemporalDuration` values. They are query-only and cannot yet be persisted as graph properties.
- `put_node_version`, `put_edge_version`, correction/retraction helpers, and `commit_versions` append immutable temporal history. `get_node_as_of`, `get_edge_as_of`, and `iter_edges_as_of` use derived temporal indexes; deferred temporal writes require `rebuild_temporal_indexes()` or `rebuild_deferred_indexes()`. Current graph records, Cypher views, and sampler snapshots remain separate.
- `graph.read_view(valid_time=...)` pins temporal reads and point-materialized sampler builds to one contiguous visible commit prefix. Its authenticated `ReadViewProvenance` verifies stable database, backend, serializer, commit, and visibility identity during snapshot hydration.
- `assert_claim`, `correct_claim`, and `retract_claim` append serializer-neutral sourced claims to temporal history. Deterministic statement IDs identify propositions; claim IDs additionally include polarity, agent, source, and world. `iter_claims_as_of` and `claim_status` provide indexed bitemporal lookup and open-world `supported`/`refuted`/`both`/`unknown` semantics.

## Backend Guidance

- Use `LevelDBStore` for small local graphs, tests, and examples when LevelDB/plyvel is available.
- Use `LMDBStore` when LMDB's embedded storage model is desirable and `map_size` can be chosen ahead of time.
- Use `PyRexStore` for RocksDB-backed bulk ingestion and append-heavy workloads, especially with Arrow/Polars columnar ingestion.
- Use `PyRexStore(transactional=True)` only when graph-level atomicity is required; the default non-transactional path keeps the native columnar writer available.
- Always close `GraphDB` handles in scripts and notebooks with `graph.close()` or a `try/finally`.

## Indexing Rules

- Node labels are indexed automatically by `put_node`, `put_nodes`, and columnar ingestion.
- Relationship types are indexed automatically when edges have `properties["type"]`.
- Property indexes are explicit. Call `create_node_property_index("name")` or `create_edge_property_index("score")` before relying on index-backed property lookups.
- Exact lookup helpers include `nodes_by_property`, `nodes_by_label_property`, `edges_by_property`, and `edges_by_type_property`.
- Range helpers include `nodes_by_property_range`, `nodes_by_label_property_range`, `edges_by_property_range`, and `edges_by_type_property_range`.
- Deferred columnar ingestion can mark secondary indexes stale. Run `rebuild_deferred_indexes()` before index-backed queries if using `IndexMaintenanceMode.DEFER`.

## Ingestion Rules

- Use object writes (`put_node`, `put_edge`, `put_nodes`, `put_edges_bulk`) for incremental updates and simple examples.
- Use `ingest_arrow` or `ingest_polars` for tabular bulk loads.
- `ColumnarIngestionMode.ENTITY_COLUMNS` builds payloads from structured ID, label, type, and property columns.
- `ColumnarIngestionMode.SERIALIZED_PAYLOADS` expects `node_value` and `edge_value` columns that already contain serializer-compatible bytes.
- `IndexMaintenanceMode.MAINTAIN` keeps secondary indexes valid immediately.
- `IndexMaintenanceMode.DEFER` writes canonical records and typed adjacency but leaves secondary indexes stale until `rebuild_deferred_indexes()`.
- `IndexMaintenanceMode.DEFER_REBUILD` defers during ingest and rebuilds before returning; it is the high-level default for `ingest_arrow` and `ingest_polars`.
- Columnar edge ingestion is append-only and maintains typed adjacency, but intentionally skips legacy adjacency blobs.

## Cypher Support

Use `GraphDB.query(cypher, parameters=None)` for Cypher reads and writes. It returns `QueryResult(columns, records)`, and iterating over the result yields record dictionaries.

Supported features include:

- Node scans by label, multiple labels, multi-entry inline property maps, and parameters.
- Label expressions (`:A|B` alternatives, `:!A` negation) on node patterns.
- Typed relationship traversal using `edge.properties["type"]`, plus canonical all-edge scans for untyped patterns.
- Relationship property maps (`-[r:T {score: 1}]->`) with literal or parameter values.
- Anchored and unanchored fixed-length paths, anonymous pattern elements, endpoint filters, and comma-separated pattern parts.
- Variable-length paths (`*`, `*m..n`) with trail semantics and relationship-list bindings.
- Shortest-path matching (`SHORTEST [k]`, `ANY SHORTEST`, `ALL SHORTEST`, `shortestPath`, `allShortestPaths`).
- Path binding (`p = (a)-[:T]->(b)`) with path values plus `nodes()`/`relationships()`/`length(p)` accessors.
- `CREATE` (fresh UUIDs unless properties supply `id`, bound-node reuse), `SET` (`prop=`/`+=`/`=`/labels), `REMOVE`, and `DELETE`/`DETACH DELETE` (relationship-existence check vs cascade), executed per row inside a backend transaction when supported.
- `MERGE pattern [ON CREATE SET ...][ON MATCH SET ...]` matching or creating idempotently within the per-query atomicity boundary.
- `FOREACH (x IN list | ...)` write-only loop with scoped loop variables and post-loop entity refresh.
- Persisted single-property node `UNIQUE` and `IS NOT NULL` constraints through `CREATE CONSTRAINT`, `DROP CONSTRAINT`, and `SHOW CONSTRAINTS`, enforced on Cypher writes.
- `SHOW INDEX[ES]` for deterministic configured property-index introspection.
- `WHERE` with arithmetic, expression comparisons, `NOT`/`AND`/`XOR`/`OR`, `IN`, null predicates, string predicates, regex matching, quantified predicates (`all`/`any`/`none`/`single`), and `exists(property)`.
- Cypher three-valued null logic for predicates.
- Stable `ORDER BY` with Cypher value ordering, nulls last ascending/first descending, and strict Boolean predicate/coercion rules.
- `RETURN`, aliases, `RETURN *`, general projection expressions (including `CASE`, subscripts/slices, list comprehensions, `reduce`, map projections), `DISTINCT`, alias-aware `ORDER BY`, and literal or parameterized `SKIP`/`LIMIT`.
- Chained `MATCH` clauses with clause-local `WHERE`.
- Query-wide bitemporal `MATCH`/`OPTIONAL MATCH` qualifiers (`FOR VALID_TIME AS OF`, optional `FOR SYSTEM_TIME AS OF`) propagated through paths, `UNION`, and subqueries, with `versionId`/`validFrom`/`validTo`/`systemFrom` metadata functions; qualified queries are read-only.
- `OPTIONAL MATCH` as a left-outer join with `None`-filled rows, post-optional `WHERE` filtering, and null-safe downstream matching.
- `UNWIND list AS x` expanding rows per element, usable mid-pipeline or as the opening clause.
- `UNION [ALL]` over independently planned branches with matching columns; branch modifiers apply within branches, `UNION` deduplicates cumulatively.
- `CALL { ... }` correlated subqueries with `RETURN` exports and per-row execution.
- `WITH` with scope replacement and local `WHERE`, `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT`.
- Core aggregates (`count`, `collect`, `sum`, `avg`, `min`, `max`) with implicit grouping, aggregate `DISTINCT`, and documented null/empty-input behavior.
- Scalar expressions containing aggregate results, such as `count(*) + 1`.
- Core scalar functions (`coalesce`, `id`/`elementId`, `type`, `labels`, `startNode`/`endNode`, `properties`, `head`/`last`, `size`/`length`, `toBoolean`/`toInteger`/`toFloat`/`toString`, string ops, math ops including `sign`/`exp`/`log`/`sin`/`cos`/`tan`/`pi`/`e`, `rand`/`randomUUID`, `range`, `reverse`, `tail`, `keys`), including property access on computed values such as `startNode(r).name`.
- Generalized top-level `CALL name(...) YIELD field [AS alias] RETURN alias` parsing with allowlisted execution; `pg.sample_typed_paths` is registered and accepts parameters.

Unsupported Cypher currently includes mutating clauses beyond `CREATE`/`SET`/`REMOVE`/`DELETE`/`MERGE`/`FOREACH`, pattern comprehensions, `exists()` with patterns, GQL quantified paths (with migration hints), unregistered procedures, and relationship or multi-property constraints.

## Sampling APIs

There are two sampling layers:

- `GraphDB` typed traversal sampling uses stored typed adjacency and external node IDs. Use `SamplingHop`, `SamplingPattern`, `sample_neighbors`, `sample_typed_paths`, and `sample_typed_subgraph`.
- `SamplerSnapshot` plus `SamplerEngine` is array-native and optimized for ML training. It uses compact integer node, edge, and relation IDs.

`SamplerSnapshot.build(graph, output_path, ...)` and `graph.build_sampler_snapshot(output_path, ...)` persist immutable `.npy` arrays and metadata. `SamplerEngine.load(path, mode="ram"|"memmap", seed=...)` loads the arrays for neighbor, multihop, subgraph, positive-triple, and hard-negative sampling.

`graph.build_sampler_snapshot(output_path, temporal=True, system_time=..., time_bucket="day")` builds format-v2 temporal history from one authenticated read view. It persists edge-version validity and commit/system provenance plus source/relation/start-time indexes. V2 loads authenticate the completion manifest and every array; legacy v1 snapshots remain loadable without temporal guarantees. `SamplerEngine` accepts `TemporalContext` point/window filters, `overlap`/`contained` window policies, and monotonic valid-start causal policies; filters run before fanout and temporal result arrays stay aligned with sampled edges.

Temporal snapshots also index node availability and positive-triple intervals. `HardNegativeConfig` supports `any_time`, `at_positive_time`, and window positive rejection, trailing candidate windows, explicit future-candidate opt-in, and deterministic `raise`/`repeat` exhaustion. Temporal batches return aligned positive/negative example times and rejection diagnostics.

`SampledSubgraphBatch` uses local node IDs in `senders`, `receivers`, `positives`, and `negatives`. Use `node_ids_global` to map local batch rows back to compact global snapshot IDs, and use `snapshot.external_node_id(...)` or `snapshot.global_triple_to_external(...)` to recover external IDs.

## Visualization

- Packaged offline viz lives in `src/gestaltdb/viz/` (Python, stdlib only) plus `web/` (React + D3 + Vite source, build-time only). The prebuilt bundle is committed at `src/gestaltdb/viz/static/` and shipped via `package-data`.
- Import from `gestaltdb.viz.api` (`VizOptions`, `VizFigure`, `visualize_nodes_edges`, `visualize_query`, `visualize_sample`, `visualize_sampler_batch`) or use `graph.visualize(cypher=...)` / `graph.visualize(seeds=..., pattern=...)`.
- `VizGraph` IR builders (`gestaltdb.viz.ir`) cover nodes/edges, Cypher results, sampled subgraphs, and sampler batches with deterministic caps (2000 nodes / 5000 edges, 2x absolute ceilings).
- Rebuild the bundle with `npm run build` in `web/` after front-end changes (`npm ci` first; `npm run check:licenses` gates JS licenses). Python tests never need npm.

## Development Commands

- Run tests: `uv run pytest`
- Run a focused test: `uv run pytest tests/test_cypher.py -q`
- Check documentation & API drift: `uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py`
- Test documentation examples: `uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py test-examples`
- Build Sphinx docs: `uv run sphinx-build -b html docs docs/_build/html`
- Install docs extras when needed: `python -m pip install ".[docs]"`

Optional backend dependencies may be missing in a local environment. If a failure is dependency-related, inspect `tests/conftest.py` and optional dependency tests before changing production code.

## Release Workflow

- This repository publishes to PyPI through GitHub Actions Trusted Publishing. Do not run local `twine upload` unless explicitly asked and credentials are intentionally available.
- The remote `Publish` workflow triggers on a published GitHub release and supports `workflow_dispatch`. Inspect it with `gh workflow view Publish --yaml` if local `.github/workflows/` files are absent.
- To release a tagged version, first ensure the version is committed on `main`, tests and docs checks passed, and local build artifacts pass `twine check`; then create the GitHub release, e.g. `gh release create vX.Y.Z --target main --title "gestaltdb X.Y.Z" --notes "..."`.
- After creating a release, watch the publish run with `gh run list --workflow Publish --limit 5` and `gh run watch <run-id> --exit-status`; verify PyPI with `https://pypi.org/project/gestaltdb/X.Y.Z/` or the PyPI JSON API.
- Verify installed wheels against the declared Python range, not the agent host default if it is outside `requires-python`. Use `uv run --python 3.12 --with gestaltdb==X.Y.Z ...` for release smoke tests.
- Do not assume package attributes or helper names during smoke tests. Inspect or use documented APIs, e.g. `importlib.metadata.version("gestaltdb")`, `python -m gestaltdb.agent_docs list`, `agent_docs.read_skill()`, and `agent_docs.read_topic("quickstart")`.

## Documentation Maintenance

- Keep `AGENTS.md` as the shortest reliable map for coding agents.
- Keep `EXAMPLES.md` runnable, compact, and aligned with tested APIs.
- Keep `src/gestaltdb/__init__.py` focused on package-level orientation and import guidance, not full tutorials.
- When changing core APIs, update Sphinx docs under `docs/`, then update `AGENTS.md`, `EXAMPLES.md`, and the package docstring if import paths or workflows changed.
- Prefer examples using temporary directories or clearly disposable paths.
- Do not document unsupported Cypher syntax or unimplemented backend behavior as available.
- If unsure, verify behavior against tests or implementation before editing docs.
