# GestaltDB Agent Guide

This repository contains `gestaltdb`, a pure Python graph database toolkit for attributed graphs. It stores nodes, directed edges, native node labels, typed adjacency records, secondary indexes, and read-optimized sampler snapshots on embedded key-value backends.

Use this file when you are an agent trying to understand or modify the library with minimal probing. For runnable snippets, see `EXAMPLES.md`. For user-facing narrative docs, see `docs/`.

## Start Here

- Core package: `src/gestaltdb/`
- Public graph model and database: `src/gestaltdb/graphdb.py`
- Storage backends: `src/gestaltdb/kvstores.py`
- Serializers: `src/gestaltdb/serializers.py`
- Columnar ingestion containers and enums: `src/gestaltdb/ingestion.py`
- Cypher facade: `src/gestaltdb/cypher.py`
- Cypher parser/planner/runtime internals: `src/gestaltdb/cypher_*.py`
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
```

Do not assume `GraphDB`, `Node`, `Edge`, backend classes, or serializer classes are available from `import gestaltdb`; import them from their modules unless the API is intentionally changed.

## Core Concepts

- `Node(node_id=..., labels=[...], properties={...})` stores a stable ID, deduplicated native labels, and arbitrary properties.
- `Edge(edge_id=..., source=..., target=..., properties={"type": ...})` stores a directed edge. Typed traversal and relationship Cypher use `edge.properties["type"]`.
- `GraphDB(store, serializer, indexed_node_properties=None, indexed_edge_properties=None)` is the main object for reads, writes, indexes, Cypher, ingestion, and sampling snapshot creation.
- `GraphDB.create(path, backend="pyrex", serializer="json", ...)` creates a self-describing database directory with `gestaltdb_manifest.json`; `GraphDB.open(path)` reopens it.
- Backends implement the `KVStore` interface. Current backends are `LMDBStore`, `LevelDBStore`, and `PyRexStore`.
- Serializers convert graph entities to bytes. Current serializers are `PickleSerializer`, `JSONSerializer`, `MessagePackSerializer`, and `ProtobufSerializer`.

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

Use `GraphDB.query(cypher, parameters=None)` for read-only Cypher. It returns `QueryResult(columns, records)`, and iterating over the result yields record dictionaries.

Supported features include:

- Node scans by label, multiple labels, multi-entry inline property maps, and parameters.
- Typed relationship traversal using `edge.properties["type"]`, plus canonical all-edge scans for untyped patterns.
- Relationship property maps (`-[r:T {score: 1}]->`) with literal or parameter values.
- Anchored and unanchored fixed-length paths, anonymous pattern elements, endpoint filters, and comma-separated pattern parts.
- `WHERE` with arithmetic, expression comparisons, `NOT`/`AND`/`XOR`/`OR`, `IN`, null predicates, string predicates, regex matching, quantified predicates (`all`/`any`/`none`/`single`), and `exists(property)`.
- Cypher three-valued null logic for predicates.
- `RETURN`, aliases, `RETURN *`, general projection expressions (including `CASE`, subscripts/slices, list comprehensions, `reduce`, map projections), `DISTINCT`, alias-aware `ORDER BY`, and literal or parameterized `SKIP`/`LIMIT`.
- Chained `MATCH` clauses with clause-local `WHERE`.
- `OPTIONAL MATCH` as a left-outer join with `None`-filled rows, post-optional `WHERE` filtering, and null-safe downstream matching.
- `UNWIND list AS x` expanding rows per element, usable mid-pipeline or as the opening clause.
- `UNION [ALL]` over independently planned branches with matching columns; branch modifiers apply within branches, `UNION` deduplicates cumulatively.
- `CALL { ... }` correlated subqueries with `RETURN` exports and per-row execution.
- `WITH` with scope replacement and local `WHERE`, `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT`.
- Core aggregates (`count`, `collect`, `sum`, `avg`, `min`, `max`) with implicit grouping, aggregate `DISTINCT`, and documented null/empty-input behavior.
- Core scalar functions (`coalesce`, `id`/`elementId`, `type`, `labels`, `startNode`/`endNode`, `properties`, `head`/`last`, `size`/`length`, `toBoolean`/`toInteger`/`toFloat`/`toString`, string ops, math ops including `sign`/`exp`/`log`/`sin`/`cos`/`tan`/`pi`/`e`, `rand`/`randomUUID`, `range`, `reverse`, `tail`, `keys`), including property access on computed values such as `startNode(r).name`.
- GestaltDB-specific `CALL pg.sample_typed_paths(...) YIELD path RETURN path`.

Unsupported Cypher currently includes mutating clauses, variable-length paths, and path binding such as `p = (a)-[:T]->(b)`.

## Sampling APIs

There are two sampling layers:

- `GraphDB` typed traversal sampling uses stored typed adjacency and external node IDs. Use `SamplingHop`, `SamplingPattern`, `sample_neighbors`, `sample_typed_paths`, and `sample_typed_subgraph`.
- `SamplerSnapshot` plus `SamplerEngine` is array-native and optimized for ML training. It uses compact integer node, edge, and relation IDs.

`SamplerSnapshot.build(graph, output_path, ...)` and `graph.build_sampler_snapshot(output_path, ...)` persist immutable `.npy` arrays and metadata. `SamplerEngine.load(path, mode="ram"|"memmap", seed=...)` loads the arrays for neighbor, multihop, subgraph, positive-triple, and hard-negative sampling.

`SampledSubgraphBatch` uses local node IDs in `senders`, `receivers`, `positives`, and `negatives`. Use `node_ids_global` to map local batch rows back to compact global snapshot IDs, and use `snapshot.external_node_id(...)` or `snapshot.global_triple_to_external(...)` to recover external IDs.

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
