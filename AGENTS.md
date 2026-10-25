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
- Bounded modal expressions and evaluation: `src/gestaltdb/modal.py`
- Positive Horn rule values and evaluation results: `src/gestaltdb/rules.py`
- Model-output/logical audit export: `src/gestaltdb/inference.py`, `docs/inference-audit.rst`. Explicit `model_output` provenance; scores are not logical probabilities; bounded historical evidence export, not independent proof verification.
- Standalone ontology: `src/gestaltdb/ontology/`; Datalog core: `src/gestaltdb/reasoning/`; explicit database adapters: `reasoning/adapters/`. Core/compiler must never import GraphDB, storage, temporal records or adapters. `ontology/adapters/hierarchy.py` builds `LabelHierarchy`/`RelationHierarchy` from direct `SubClassOf`/`SubPropertyOf` axioms and fails closed on equivalence/intersection/union axioms. Guide: `docs/ontology.rst`; measured boundaries: `docs/ontology-benchmarks.rst`.
- Relational IR/structural inspection: `reasoning/relational.py`, `docs/reasoning-execution.rst`. Entity-only n-ary representation is not yet executable; preserve legacy `Program.digest`. Second roadmap: `EPIC_REASONING_ENGINE.md` / #156.
- Compact batches: `reasoning/batches.py`; optional Arrow exchange: `reasoning/adapters/arrow.py` / `docs/reasoning-batches.rst`. IDs require the exact dictionary/context; immutable batches and reference set operations are not a native executor or all-support provenance.
- Experimental closure-only Polars: `reasoning/adapters/polars.py`, `docs/reasoning-polars.rst` (`polars` extra). Explicit `PolarsRun`, no proofs/publication; columnar work accounting differs from indexed Python. Keep the default evaluator unchanged.
- Prepared indexed plans: `reasoning/plans.py`, `docs/reasoning-plans.rst`. Exact identity checks, positive SCCs, optional witnesses/telemetry; iteration limits count total component rounds. Default evaluator/global-round behavior remains unchanged.
- Executor decision and hardening: `reasoning/executors.py`, `docs/reasoning-executors.rst`. Default stays indexed Python; Polars opt-in only; proofs route to indexed engines. Cooperative `cancel` raises `ReasoningCancelledError` without partial results; native kernels are never interrupted mid-call. No bespoke native kernel: recorded defer, revisit via RE-26 evidence.
- Explicit pipeline physical budget: `reasoning/adapters/execution.py` and `graph_execution.py` accept `polars_max_intermediate_rows` only for explicit Polars requests (default 100,000 unchanged). `ExecutionOutcome.polars_run` exposes native metrics separately from common telemetry; physical budgets do not alter logical identity or imply RSS/adoption guarantees.
- Typed Datalog: `reasoning/terms.py` + `reasoning/typed.py`, `docs/reasoning-typed.rst`. Strict literals, filtered n-ary rules, normalized multi-heads, single-pass stratified aggregates, domain-expanded predicate variables; generic engine has no witnesses. Old binary APIs unchanged.
- Demand evaluation: `reasoning/demand.py`, `docs/reasoning-demand.rst`. Magic-set specialization with identity-bound caching and full-closure parity; unsupported combinations fail closed with a documented complete strategy.
- Pinned frontier demand: `reasoning/frontier.py` + `reasoning/adapters/frontier.py`, `docs/reasoning-frontier.rst`. Opt-in positive binary entity goals, interleaved indexed claim reads under one authenticated temporal view, cumulative budgets and exact fetched evidence; not full closure, proofs, publication or automatic executor selection.
- Current-graph frontier: `reasoning/adapters/graph_frontier.py`, `docs/reasoning-current-frontier.rst`. Same scheduler over explicit `GraphMapping`, authenticated mutable snapshots, label/property indexes and typed adjacency. Complete unindexed property scans are reported; temporal claims and persistent generation admission stay separate.
- Current-graph admission: `reasoning/adapters/graph_execution.py`, `docs/reasoning-current-admission.rst`. Distinct `CurrentSourceIdentity` with unchanged temporal digests; authenticated complete projection, exact generation pins and explicit executor dispatch. Not frontier-result admission, persistent cache migration or publication.
- Typed point admission: `reasoning/adapters/session_generation.py`, `docs/reasoning-generation-identity.rst`. Explicit source/active-version/catalog verification plus shared generation/state pins; not closure proof, current-graph or range admission. Admission is bounded/cancellable and performs an opt-in historical source scan.
- Typed execution: `reasoning/adapters/execution.py:execute_typed_closure`, `adapters/typed_execution.py`, `docs/reasoning-typed-execution.rst`. Distinct GenericEngine-only capability identity and common telemetry; authenticated sessions recompute from exact active base versions and compare closure. No binary recoding, witnesses, support certification or publication.
- Authenticated durable sessions: `reasoning/adapters/authenticated_sessions.py`, `docs/reasoning-authenticated-sessions.rst`. Exact caller generation/state pins, source-verified old/new state and guarded v1 resume. Cooperating file writers use locking/exact-state CAS; checksums are not source authentication or closure proof.
- Authenticated inferred artifacts: `reasoning/adapters/authenticated_inferred_sampler.py`, `docs/reasoning-authenticated-inferred.rst`. Existing consumer only: exact source/session pins, separate canonical projection selection, immutable new-path builds and independently reconstructed full artifact hydration. Legacy partial mapping pins are not source admission or closure proof.
- Range admission: `reasoning/adapters/range_generation.py`, `docs/reasoning-range-admission.rst`. Distinct finite-range identity, historical-boundary/canonical-point extraction and exact assertion-version intervals. Re-admission re-extracts and recomputes before conformance checks; not a fast cache hit or publication permit.
- Bound range publication: `reasoning/adapters/range_publication.py`, `docs/reasoning-bound-range-publication.rst`. Own computation, guarded re-admission/source freshness/target history CAS and one interval-batch commit. Content-idempotent; partial external validity changes fail closed. Raw caller-derived `stage_range` remains separate.
- Live reasoning: `reasoning/maintenance.py` + `reasoning/catalog.py`, `docs/reasoning-maintenance.rst`, `docs/reasoning-catalog.rst`, `docs/reasoning-generations.rst`, `docs/reasoning-gates.rst`. Staged generations with compare-and-publish, bounded supports, insertion propagation, DRed deletion, pinned catalogs and workload gates; no correctness trade for latency.
- Temporal reasoning: `reasoning/intervals.py` + `reasoning/temporal_closure.py`, `docs/reasoning-intervals.rst`, `docs/reasoning-temporal.rst`, `docs/reasoning-ranges.rst`, `docs/reasoning-conformance.rst`. Change-segment range closure with per-interval evidence, segment-memoized delta maintenance, managed interval publication, per-segment equality and the `verify_closure` conformance gate.
- Explicit-negative validation (`reasoning/validation.py`, `docs/reasoning-validation.rst`) and snapshot-local equality (`reasoning/equality.py`, `docs/reasoning-equality.rst`): bounded conflict reports and canonical representatives; never merge, never explode, valid for one snapshot only.
- Pinned batched extraction: `reasoning/extraction.py` (stdlib-only) + `reasoning/adapters/batched.py` / `docs/reasoning-extraction.rst`. Bounded batches, exact evidence IDs, capability probing; LevelDB snapshots fail closed. Raw native scans (`reasoning/adapters/native.py`) use actual PyRex entry points (`>=0.5.0`); 0.6.0 adds transactional default-family scans, not named-family or authenticated transactional claim-source admission. Decoding stays caller-owned.
- Authenticated native claims: `reasoning/adapters/native_claims.py`, `docs/reasoning-native-claims.rst`. Explicit plain-PyRex full-record coverage and canonical point-selection conformance under the temporal writer guard; shared batched policy/evidence context. Post-selection filters, no index-locator acceleration or speed/adoption claim; transaction/range/current sources remain separate.
- Authenticated native locators: `reasoning/adapters/native_locator_claims.py`, `docs/reasoning-native-locator-claims.rst`. Explicit native world/predicate/subject prefixes plus complete logical histories and point materialization; full catalog authentication/canonical conformance retained. Independent catalog/native-row bounds; no speed claim or automatic source adoption.
- Optional offline RDF: `ontology/adapters/rdf.py` (`rdf` extra). Explicit current-graph projection/fanout: `reasoning/adapters/graph.py` / `docs/ontology-graph.rst`. Point-only separate-world publication: `reasoning/adapters/publication.py` / `docs/ontology-publication.rst`; nontransactional orphan recovery is caller-managed, never silently repaired.
- Cypher engine: `src/gestaltdb/query_engine/cypher/`
- Legacy Cypher import shims: `src/gestaltdb/cypher.py` and `src/gestaltdb/cypher_*.py`
- Explicit type hierarchies: `src/gestaltdb/hierarchy.py`; `RelationHierarchy` expands sampler relations and opt-in Cypher relationship sub-property matching, `LabelHierarchy` enables opt-in Cypher node sub-type matching. The historical `sampling/hierarchy.py` path is a compatibility shim.
- Sampling API: `src/gestaltdb/sampling/`
- Sphinx docs: `docs/`; user-facing filtering and sampling guides: `docs/property-filtering.rst`, `docs/typed-sampling.rst`; performance boundaries and benchmark commands: `docs/performance.rst`
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
from gestaltdb import ExternalNeighborSamplingSpec, HardNegativeConfig, NeighborSamplingSpec, SamplerEngine, SamplerSnapshot
from gestaltdb import SamplingHop, SamplingPattern
from gestaltdb import TemporalContext, TemporalInstant, TemporalInterval
from gestaltdb import TemporalDate, TemporalDuration, TemporalLocalDateTime
from gestaltdb import TemporalLocalTime, TemporalTime
from gestaltdb import GraphReadView, ReadViewProvenance
from gestaltdb import CurrentGraphReadView, CurrentReadProvenance
from gestaltdb import Claim, ClaimObjectKind, ClaimPolarity, ClaimStatus
from gestaltdb import AccessibilityKind, ModalExpression, ModalOperator
from gestaltdb import ModalEntailmentResult, ModalEvaluationLimitError
from gestaltdb import ClaimExplanation, RuleEvaluationLimitError, RuleRunResult
from gestaltdb import RuleVersion, TruthMaintenanceResult
```

Do not assume `GraphDB`, `Node`, `Edge`, backend classes, or serializer classes are available from `import gestaltdb`; import them from their modules unless the API is intentionally changed.

## Core Concepts

- `Node(node_id=..., labels=[...], properties={...})` stores a stable ID, deduplicated native labels, and arbitrary properties.
- `Edge(edge_id=..., source=..., target=..., properties={"type": ...})` stores a directed edge. Typed traversal and relationship Cypher use `edge.properties["type"]`.
- `GraphDB(store, serializer, indexed_node_properties=None, indexed_edge_properties=None)` is the main object for reads, writes, indexes, Cypher, ingestion, and sampling snapshot creation.
- `GraphDB.create(path, backend="pyrex", serializer="json", ...)` creates a self-describing database directory with `gestaltdb_manifest.json`; `GraphDB.open(path)` reopens it.
- Backends implement the `KVStore` interface. Current backends are `LMDBStore`, `LevelDBStore`, and `PyRexStore`.
- Serializers convert graph entities to bytes. Current serializers are `PickleSerializer`, `JSONSerializer`, `MessagePackSerializer`, and `ProtobufSerializer`.
- KV backends and binary serializers are independent optional extras. Combine them additively, for example `gestaltdb[leveldb,msgpack]` or `gestaltdb[lmdb,protobuf]`; Pickle and JSON need no serializer extra.
- `TemporalInstant`, `TemporalInterval`, and `TemporalContext` define timezone-aware UTC-microsecond instants, half-open `[start, end)` intervals, and point/window matching.
- Cypher temporal expressions return immutable `TemporalDate`, `TemporalTime`, `TemporalLocalTime`, `TemporalLocalDateTime`, `TemporalInstant`, and `TemporalDuration` values. They persist recursively in node and edge properties across every serializer and participate in configured exact/range property indexes.
- `put_node_version`, `put_edge_version`, correction/retraction helpers, and `commit_versions` append immutable temporal history. `get_node_as_of`, `get_edge_as_of`, and `iter_edges_as_of` use derived temporal indexes; deferred temporal writes require `rebuild_temporal_indexes()` or `rebuild_deferred_indexes()`. Current graph records, Cypher views, and sampler snapshots remain separate.
- Filesystem-backed temporal writers use a durable sidecar sequence and database-wide lock. `list_temporal_orphans()` audits gaps/incomplete publications; `reclaim_temporal_orphans(through_commit=...)` removes only validated unmarked artifacts and never reuses commit IDs.
- Deprecated `TimeIndexedEdge` records are mutable legacy data, not temporal history. Back up and quiesce the database, run `migrate_time_indexed_edges(delete_legacy=False)`, validate, then rerun with deletion; changed or corrupt legacy input fails closed.
- `graph.read_view(valid_time=...)` pins temporal reads and point-materialized sampler builds to one contiguous visible commit prefix. Its authenticated `ReadViewProvenance` verifies stable database, backend, serializer, commit, and visibility identity during snapshot hydration.
- `graph.current_read_view()` pins mutable records, adjacency, metadata, and indexes to one backend snapshot. LMDB provenance includes its transaction sequence plus a full-state digest; transactional PyRex pins reads but provenance depends on runtime sequence support; the multi-database LevelDB layout fails explicit snapshot requests closed.
- `assert_claim`, `correct_claim`, and `retract_claim` append serializer-neutral sourced claims to temporal history. Deterministic statement IDs identify propositions; claim IDs additionally include polarity, agent, source, and world. `iter_claims_as_of` and `claim_status` provide indexed bitemporal lookup and open-world `supported`/`refuted`/`both`/`unknown` semantics.
- `create_rule` appends validated safe Horn-rule versions. It accepts positive `when` atoms plus explicit closed-world `not_when` absence atoms and `closed_world` declarations; undeclared negation and negative cycles are rejected before the catalog changes. `run_rules(as_of=...)` performs bounded stratified semi-naive evaluation over positive entity claims in a shared world, intersects premise validity, and persists deduplicated conclusions with positive premise and absence evidence. `maintain_truth` revalidates absence after base changes; `explain_claim` emits `absence` nodes. Absence is not a negative claim, and partial negative-interval overlap conservatively blocks derivation.
- `maintain_truth(as_of=...)` incrementally reconciles derived claims, retracting a conclusion only after its final independent support disappears. `explain_claim` returns a bounded finite graph of exact historical claim/rule versions; retries after interrupted dependency-index publication are idempotent.
- `assert_world_accessibility` stores agent-specific temporal links in separate belief, knowledge, and modal frames. `entails` and allowlisted `kg.entails` evaluate finite nested modal formulas under one read view with explicit depth/state bounds and deterministic temporal/derivation evidence.

## Backend Guidance

- A base install has no embedded KV backend. Install `lmdb`, `leveldb`, or `rocksdb`, or use the `backends` bundle.
- Parameterless `GraphDB.create(path)` retains the `pyrex` compatibility default and requires the `rocksdb` extra; never add silent backend fallback.
- `GraphDB.create/open` preflight selected optional dependencies before opening or replacing storage and report the required extra when one is unavailable.
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
- Encoded secondary/range index keys are limited to 511 bytes across all backends; oversized IDs or indexed values fail before backend I/O and are never truncated.

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

Use `GraphDB.query(cypher, parameters=None)` for Cypher reads and writes. It returns `QueryResult(columns, records)`, and iterating over the result yields record dictionaries. Pass `label_hierarchy=LabelHierarchy({...})` and/or `relation_hierarchy=RelationHierarchy({...})` to opt into transitive node sub-type or relationship sub-property matching for scans, label expressions, endpoint filters, property maps, variable-length paths and indexed lookups; default matching stays exact.

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
- Generalized top-level `CALL name(...) YIELD field [AS alias] RETURN alias` parsing with allowlisted execution; `pg.sample_typed_paths` and `kg.entails` are registered and accept parameters.

Unsupported Cypher currently includes mutating clauses beyond `CREATE`/`SET`/`REMOVE`/`DELETE`/`MERGE`/`FOREACH`, pattern comprehensions, `exists()` with patterns, GQL quantified paths (with migration hints), unregistered procedures, and relationship or multi-property constraints.

## Sampling APIs

There are two sampling layers:

- `GraphDB` typed traversal sampling uses stored typed adjacency and external node IDs. Use `SamplingHop`, `SamplingPattern`, `sample_neighbors`, `sample_typed_paths`, and `sample_typed_subgraph`.
- `SamplerSnapshot` plus `SamplerEngine` is array-native and optimized for ML training. It uses compact integer node, edge, and relation IDs.

For repeated property-constrained sampling, select cohorts through explicit indexes, then define the eligible graph with snapshot `node_filter`/`edge_filter`. Filtering seeds alone does not constrain every hop. Build callbacks scan decoded source records (no automatic index pushdown); runtime sampling uses relation/direction/type/temporal policies over arrays. Memmap avoids eager array loading, not all memory use. Keep these two first-class features prominent in README and the docs landing page.

`sample_node2vec_walks` samples second-order biased walks with return/in-out factors over directed candidates and undirected-adjacency distances (first step uniform; static edge weights multiply the bias). `LayeredSampleBatch.to_blocks` repackages layers as bipartite message-passing blocks with unique node frames and a per-edge-occurrence `edge_index` oriented neighbor-to-input; graph-edge orientation is not encoded. `to_pyg_blocks` converts each block to `torch.long` tensors for direct PyG bipartite-block construction. `RelationHierarchy` expands parent relation names to transitive sub-properties before sampling; `SamplerSnapshot.expand_relations` fails closed on unknown snapshot relations and fanout applies to the union. `LabelHierarchy` plus `SamplerSnapshot.expand_node_types` does the same for semantic `node_type_values`; snapshots without recorded type names fail closed. `GraphDB.sample_neighbors`, `sample_typed_paths`, and `sample_typed_subgraph` accept `relation_hierarchy=` for live traversal, with `sample_size` shared across the expanded union; default traversal stays literal. `SamplerSnapshot.build(graph, output_path, ...)` and `graph.build_sampler_snapshot(output_path, ...)` persist immutable `.npy` arrays and metadata. Snapshots may include edge weights for weighted neighbor, edge, and random-walk sampling. `SamplerEngine.load(path, mode="ram"|"memmap", seed=...)` loads the arrays for node/edge seed, neighbor, heterogeneous layer, first-order random walk, multihop, subgraph, positive-triple, and hard-negative sampling. `sample_nodes`, `sample_edges`, and `sample_neighbors` accept `balanced=True` to split a draw into deterministic equal shares across compact node types or relation IDs; groups smaller than their share return fewer rows when `replace=False`, and unused quota is not borrowed. Layer specs expose the same flag. `sample_nodes` and `sample_edges` also accept `temporal`/`window_policy` to filter node availability or edge-version validity before seed selection. `GraphDB.sample_nodes` reservoir-samples live node IDs with an optional label index and node predicate; memory is bounded by sample size, but eligible keys are still scanned. `sample_partition_block` samples a uniform random balanced partition block with exact normalization weights (uniform, not cut-optimized; self-loops carry the node weight). `sample_packed_subgraphs` samples one independent subgraph per seed-edge group into a single batch with `graph_node_offsets`/`graph_edge_offsets` component frames (positive rows follow group order; diagnostics summed). `sample_induced_subgraph` draws uniform node sets and returns all eligible induced edges under a required `max_edges` cap; exceeding the cap fails closed. `normalize=True` attaches exact GraphSAINT inclusion-reciprocal `node_weights`/`edge_weights` (self-loops carry the node weight; filters do not change the weights).

High-level non-temporal snapshot builds automatically use authenticated mutable-state snapshots when the backend exposes a verifiable identity. Pass `read_snapshot=True` to require this guarantee or `False` to opt out; unsupported requests fail before building.

`graph.build_sampler_snapshot(output_path, temporal=True, system_time=..., time_bucket="day")` builds format-v2 temporal history from one authenticated read view. It persists edge-version validity and commit/system provenance plus source/relation/start-time indexes. V2 loads authenticate the completion manifest and every array; legacy v1 snapshots remain loadable without temporal guarantees. `SamplerEngine` accepts `TemporalContext` point/window filters, `overlap`/`contained` window policies, and monotonic valid-start causal policies; filters run before fanout and temporal result arrays stay aligned with sampled edges.

Temporal snapshots also index node availability and positive-triple intervals. `HardNegativeConfig` supports `any_time`, `at_positive_time`, and window positive rejection, trailing candidate windows, explicit future-candidate opt-in, and deterministic `raise`/`repeat` exhaustion. `in_batch=True` tries other positive heads/tails with the same relation before random/context candidates, while normal type, availability, duplicate, and positive rejection still apply. `candidate_distribution="degree"` degree-weights only the random fallback by `incident_degree ** degree_exponent`; zero-degree graphs fall back to uniform and non-finite weights fail closed. Temporal batches return aligned positive/negative example times and rejection diagnostics.

`SampledSubgraphBatch` uses local node IDs in `senders`, `receivers`, `positives`, and `negatives`. Use `node_ids_global` to map local batch rows back to compact global snapshot IDs, and use `snapshot.external_node_id(...)` or `snapshot.global_triple_to_external(...)` to recover external IDs.

Resolve external IDs with `snapshot.node_int(...)`, `snapshot.edge_int(...)`, and `snapshot.relation_int(...)`; plural variants preserve input order. Convenience methods ending in `_external` accept external node, edge, and relation IDs while retaining compact array-native results. Use `to_external(snapshot)` on neighbor, layered, and random-walk batches when external output IDs are needed. Temporal edge IDs can be ambiguous across versions; use compact edge rows in that case.

## Visualization

- Packaged offline viz lives in `src/gestaltdb/viz/` (Python, stdlib only) plus `web/` (React + D3 + Vite source, build-time only). The prebuilt bundle is committed at `src/gestaltdb/viz/static/` and shipped via `package-data`.
- Import from `gestaltdb.viz.api` (`VizOptions`, `VizFigure`, `visualize_nodes_edges`, `visualize_query`, `visualize_sample`, `visualize_sampler_batch`) or use `graph.visualize(cypher=...)` / `graph.visualize(seeds=..., pattern=...)`.
- `VizGraph` IR builders (`gestaltdb.viz.ir`) cover nodes/edges, Cypher results, sampled subgraphs, and sampler batches with deterministic caps (2000 nodes / 5000 edges, 2x absolute ceilings).
- `VizOptions(node_label_property="name", edge_labels="all")` configures readable node/relationship labels. `VizFigure.as_svg()` / `save_svg()` provide dependency-free static circle/grid layouts or supplied coordinates; static output does not capture the notebook iframe state.
- Attribute properties configure node size/color and edge width/color in HTML/SVG. Browser export controls support scope/dimensions/transparency/scale; local view-state JSON restores positions, pins, zoom, settings, filters, and simulation membership for the same topology.
- Rebuild the bundle with `npm run build` in `web/` after front-end changes (`npm ci` first; `npm run check:licenses` gates JS licenses). Python tests never need npm.
- Browser checks: `npx playwright install chromium`, then `npm test` in `web/` (`VIZ_TEST_PYTHON` selects an installed Python environment); tests open generated artifacts offline.
- `npm run check:bundle` verifies source-aware build provenance; full runtime notices ship in `viz/static/third-party-notices.txt` and are embedded in HTML.

## Development Priorities and Roadmap Tracking

- Difficult features may start with simpler, less-performant, throw-away implementations to facilitate testing.
- Balance high performance with pragmatism: completing the roadmap takes priority over achieving high performance in the first implementation.
- Well-motivated architecture work that enables large portions of ambitious roadmaps always takes priority.
- Use GitHub issues and their dependency relationships to represent roadmap dependencies and track work.

## Development Commands

- Run tests: `uv run pytest`
- Run a focused test: `uv run pytest tests/test_cypher.py -q`
- Run the deterministic temporal smoke: `uv run python -m benchmarks temporal --nodes 100 --edges 500 --queries 100`
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
