# Cypher Engine Refactor Plan

This plan tracks the path from the current read-only Cypher subset to a feature-complete, high-performance Cypher engine. It was last reconciled with the current implementation after the parser/planner/runtime split, multi-`MATCH` support, index-backed predicates, result shaping, and agent documentation release.

Current architecture snapshot:

- Public entry point: `GraphDB.query(cypher, parameters=None)` delegates to `gestaltdb.cypher.execute`.
- Frontend: `cypher_parser.py` is a regex-assisted small parser that returns dataclass AST objects from `cypher_ast.py`. It is not a full openCypher grammar.
- Planning: `cypher_plan.py` exposes logical operator dataclasses and `plan(query)`, primarily for inspection and future optimizer work.
- Runtime: `cypher_runtime.py` executes parsed query shapes directly as iterator pipelines with query-local node/edge hydration caches.
- Storage integration: label, relationship type, exact property, composite property, and scalar range indexes are maintained by `GraphDB` and used opportunistically by runtime scans.

## Phase 0: Stabilize Current Cypher Subset

Goal: make current behavior correct, predictable, and measurable without broadening the language much.

- [x] Keep public API as `GraphDB.query(cypher: str, parameters: dict | None = None)`.
- [x] Add repeated-variable binding validation for nodes and relationships.
- [x] Decide and enforce identity projection semantics for `n.id` and `r.id`.
- [x] Make `LIMIT 0` and early `LIMIT` behavior explicit.
- [x] Stream label-only scans instead of materializing all label IDs.
- [x] Add query-local node/edge hydration cache for traversal.
- [x] Normalize parser/literal errors into consistent `ValueError` messages.
- [x] Remove or isolate graph-layer hazards affecting query correctness, especially duplicate `delete_edge` and broad exception handling in `put_edges_bulk`.

Success criteria:

- [x] Current supported Cypher queries keep passing.
- [x] Reused variables enforce equality instead of silently overwriting bindings.
- [x] `LIMIT 0` returns no rows without unnecessary hydration.
- [x] `MATCH (n:Label) RETURN n LIMIT 1` does not consume the full label index.
- [x] Projection semantics are documented and tested.
- [x] Bulk edge writes do not mask failures with a broad `except`.

Tests:

- [x] Reused node variable on a multi-hop traversal.
- [x] Reused relationship variable on a multi-hop traversal.
- [x] `LIMIT 0` for label scans and traversals.
- [x] Label scan iterator spy proving early stop.
- [x] Property named `id` projection behavior.
- [x] Self-loop traversal.
- [ ] Duplicate edge traversal.
- [x] Missing source/target node and missing edge safety for anchored and unanchored traversal paths.
- [x] Bad literal gives clear `ValueError`.

## Phase 1: Real Parser And AST

Goal: replace regex parsing with a parser that can support real Cypher features.

- [x] Introduce parser module using Lark, ANTLR/openCypher grammar, or a small recursive-descent parser.
- [x] Add AST nodes for current query shapes, clauses, typed relationship patterns, return shaping, parameters, literals, and supported boolean expressions.
- [ ] Replace the regex-assisted parser with a real grammar frontend before adding broad Cypher syntax such as multiple pattern parts, variable-length paths, aggregation, and writes.
- [x] Parse current supported syntax into AST.
- [x] Parse Cypher literals: `true`, `false`, `null`, strings, ints, floats, lists, and maps.
- [x] Add parameter syntax support through `GraphDB.query(cypher, parameters=None)`.
- [x] Preserve `GraphDB.query(cypher)` compatibility.

Success criteria:

- [x] Current Cypher tests pass through the new parser.
- [x] Parser accepts Cypher literals and parameters.
- [x] Parser rejects unsupported syntax with precise errors.
- [x] Execution code no longer depends on regex parse groups.

## Phase 2: Logical Plan, Physical Operators, Streaming Runtime

Goal: introduce reusable query-engine architecture for performance.

- [x] Add logical operators: node ID seek, label scan, property seek, relationship type/property seek, expand, filter, project, procedure call, and limit.
- [ ] Add explicit logical operators for `ORDER BY`, `SKIP`, and `DISTINCT`; they are currently represented on parsed query dataclasses and applied in result materialization.
- [x] Add runtime iterator helpers over existing KV primitives for node scans, relationship scans, expansion, filtering, projection, and limit handling.
- [x] Add `QueryContext` with parameters, node cache, and edge cache.
- [ ] Add optional backend snapshot/read context.
- [ ] Execute from a physical operator tree instead of having `cypher_runtime.py` branch directly on parsed query dataclasses.
- [x] Push down `LIMIT` where safe.
- [ ] Use bulk hydration where a frontier batch is materialized.

Success criteria:

- [x] Current queries execute through reusable runtime helper functions.
- [ ] Logical plans are executable directly or compiled into physical operators.
- [x] Label scan with limit stops early.
- [x] Traversal with limit avoids avoidable path explosion.
- [x] Hydration is cached within a query.

## Phase 3: Core Read-Only Cypher Features

Goal: reach a useful read-only Cypher subset.

- [x] Implement `WHERE`.
- [x] Implement parameters in execution.
- [x] Implement equality, inequality, ordered comparisons, `AND`, `IN`, `IS NULL`, and `IS NOT NULL` for node scans, anchored traversals, chained `MATCH`, and unanchored relationship scans.
- [x] Implement multiple labels.
- [x] Implement target-node and relationship property predicates.
- [x] Implement relationship type alternatives.
- [x] Implement unanchored node and relationship scans.
- [x] Implement aliases, `RETURN *`, `ORDER BY`, `SKIP`, `LIMIT`, and `DISTINCT`.

Success criteria:

- [x] Typical read-only Cypher queries against labeled property graphs work.
- [x] Indexed predicates use indexes when available.
- [x] Non-indexed predicates still work via scan/filter.
- [x] Unsupported clauses fail explicitly.

## Phase 4: Index, Metadata, And Schema Foundations

Goal: make query performance persistent and reliable across reopens and ingestion modes.

- [x] Add metadata namespace in each backend.
- [x] Persist node and edge property index definitions.
- [x] Auto-load index definitions in `GraphDB.__init__`.
- [x] Add label-property composite indexes.
- [x] Add range indexes.
- [x] Add edge type-property composite indexes.
- [x] Use edge type-property indexes for relationship scan predicates.
- [x] Add index definition/statistics API surface through `GraphDB.index_statistics()` and count helpers.
- [x] Make columnar ingestion index-aware or expose an explicit required rebuild workflow.

Success criteria:

- [x] Reopening a DB preserves index maintenance behavior.
- [ ] Planner can estimate rough cardinality. Current code exposes index definitions and count helpers, but `cypher_plan.py` does not yet use cardinality estimates for plan selection.
- [x] Range predicates can use range indexes.
- [x] Columnar ingestion does not silently make Cypher indexes stale.

## Phase 5: Joins, Aggregation, WITH, OPTIONAL MATCH

Goal: support common analytical Cypher.

- [ ] Implement multiple pattern parts in one `MATCH`.
- [x] Implement multiple `MATCH` clauses.
- [x] Add nested-loop apply execution for chained `MATCH` clauses.
- [ ] Add hash join and expand-join operators.
- [ ] Implement `OPTIONAL MATCH`.
- [ ] Implement `WITH`.
- [ ] Implement `count`, `collect`, `sum`, `avg`, `min`, and `max`.
- [ ] Implement grouping semantics.
- [ ] Add memory-bounded sort/aggregation guardrails.

Success criteria:

- [ ] Multi-pattern queries produce correct joined bindings.
- [ ] `OPTIONAL MATCH` preserves rows with nulls.
- [ ] Aggregation follows Cypher grouping semantics.
- [ ] `WITH` can chain query parts.

## Phase 6: Paths And Graph Algorithms

Goal: support Cypher path semantics and high-performance traversal queries.

- [ ] Add path value representation.
- [ ] Implement named paths.
- [ ] Implement `nodes(p)`, `relationships(p)`, and `length(p)`.
- [ ] Implement variable-length relationships.
- [ ] Add relationship uniqueness semantics.
- [ ] Add traversal memory/row limits.
- [ ] Add shortest path after bounded variable-length paths are stable.

Success criteria:

- [ ] Bounded variable-length typed traversal works.
- [ ] Path projection works.
- [ ] Traversal avoids infinite cycles.
- [ ] Limits and predicates are pushed into traversal where safe.

## Phase 7: Mutating Cypher

Goal: add write queries safely.

- [ ] Add write transaction abstraction.
- [ ] Implement `CREATE`.
- [ ] Implement `SET`.
- [ ] Implement `DELETE`.
- [ ] Implement `DETACH DELETE`.
- [ ] Implement `REMOVE`.
- [ ] Implement `MERGE` after uniqueness/index support is reliable.
- [ ] Add schema/index DDL if desired.

Success criteria:

- [ ] Mutating queries update records and all indexes consistently.
- [ ] Failed writes do not leave partial index state.
- [ ] `MERGE` is deterministic under uniqueness constraints.

## Phase 8: Performance Engineering And Benchmarks

Goal: make the engine fast enough to justify the architecture.

- [x] Add initial benchmark scripts and external graph database comparison scaffolding under `scripts/`.
- [ ] Add a maintained Cypher benchmark dataset generator and query suite covering label lookup, property lookup, expansions, relationship scans, aggregation, and paths.
- [ ] Add plan cache.
- [ ] Add compiled expression evaluation if needed.
- [ ] Add backend-specific batch APIs where useful.
- [ ] Add memory usage tracking for operators.
- [ ] Add optional profiling hooks.

Success criteria:

- [ ] Cypher-specific benchmarks run reproducibly.
- [ ] Performance regressions are caught.
- [ ] Planner improvements are measurable.
- [ ] Query memory usage is bounded for large traversals.
