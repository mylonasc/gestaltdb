# GraphDB Refactoring Epic (#388)

Tracking issue: https://github.com/mylonasc/gestaltdb/issues/388
Branch: `feature/graphdb-refactor-388`
Base: `release/reasoning-next-candidate` at `4bcd071`

## Goal

Decompose the 7,113-line `GraphDB` god class (`src/gestaltdb/graphdb.py`,
221 methods, 134 public) into smaller classes with well-defined interfaces,
without changing observable behavior, storage format, or public imports.

Success means cohesive, independently understandable capabilities — not merely
satisfying the 800-line module ceiling.

## Non-goals

- No public API redesign in this epic. `from gestaltdb.graphdb import GraphDB,
  Node, Edge` keeps working. New capability surfaces (e.g. `graph.claims`)
  are explicitly deferred until internals stabilize.
- No storage-format, logical-identity, or commit-visibility changes.
- No “improvements” to transaction, locking, snapshot, or publication
  semantics during extraction.
- No behavior-preserving-only rewrite of the other seven oversized modules.
  They get their own plans after GraphDB demonstrates the pattern.

## Constraints (from prior plan + CI)

- Pinned Ruff correctness rules (`E4,E7,E9,F63,F7,F82`) must pass.
- New library modules ≤800 physical lines; `graphdb.py` has an exact
  shrinking budget in `planning/code-quality/module-size-baseline.json`.
  Lower the budget in the same change as every extraction.
- Lightweight `Lint` workflow (Python 3.10, no backends) runs on relevant
  changes; full real-backend suites still gate release.
- Standalone reasoning core must never import storage. Stored-rule
  implementation may depend on narrow claim/commit interfaces only.
- Executable docstrings, architecture docs, and the agent API index must
  stay green; regenerate the index if source locations change.

## Current inventory

Counted from `src/gestaltdb/graphdb.py` at base:

- 221 `GraphDB` methods (134 public, 87 private).
- Rough responsibility split: temporal/rules/claims ~63, edge/adjacency
  ~58, node/index ~48, lifecycle/query/misc ~29, ingestion ~10,
  sampling/views ~4, other ~9.
- Module-level residents: `Node`, `Edge`, `TimeIndexedEdge`,
  `GraphEntityDictSerializer`, `_TemporalWriterLock` + global lock table,
  `_SimpleProgress`, `_normalize_labels`, property-index encoders,
  datetime helpers, backend/serializer registries, ~30 module constants.
- External private coupling: `readview.py` reaches into
  `graph.store`, `graph._temporal_visible_key/_commit_key`,
  `graph._store_path/_backend_name/_serializer_name/database_id/manifest`;
  `reasoning/adapters/*` use `graph._temporal_writer_guard`,
  `graph._get_temporal_commit_cached`, `graph._temporal_record_key`,
  `graph._database_id`, `_DATABASE_ID_METADATA_KEY`,
  `_TEMPORAL_RECORD_PREFIX`, `_TEMPORAL_VISIBLE_PREFIX`,
  `_TemporalWriterLock`; `transaction()` constructs a sibling `GraphDB`;
  `current_read_view()` shallow-copies `GraphDB` and swaps `store`.

## Target components

Names are provisional. Prefer explicit private collaborators with documented
state/lock ownership over mixins or multiple inheritance.

1. **Entities / value codecs** — `Node`, `Edge`, `TimeIndexedEdge`,
   `GraphEntityDictSerializer`, label normalization, property-index
   encoders, datetime helpers. Pure values; no store access.
2. **Database lifecycle** — backend/serializer selection, manifests,
   database identity, handle creation/close, resource ownership.
3. **Current graph records** — node/edge CRUD, bulk object writes,
   legacy adjacency blobs, BFS helpers.
4. **Graph indexes** — label/type/property/range indexes, constraint
   metadata, freshness checks, rebuild/deferred coordination.
5. **Typed adjacency** — typed edge adjacency, counts, traversal
   primitives; owns its count cache.
6. **Columnar ingestion** — Arrow/Polars entity + serialized-payload
   paths, chunking, deferred-index coordination, native writer integration.
7. **Temporal history** — immutable records, commit decode, history
   selection (`*_as_of`, `iter_*_versions`).
8. **Temporal commit writer** — write validation, sequence reservation,
   descriptor/record writes, index publication, visibility markers.
   Sequence → descriptor → records → indexes → visibility marker is a
   protected ordering contract.
9. **Temporal indexes + recovery** — temporal index maintenance/rebuilds,
   orphan audit/reclaim, sequence/state helpers.
10. **Claims** — assertion/correction/retraction, claim selection,
    four-valued status; uses temporal history + commit writer.
11. **Stored rules + truth maintenance** — catalog, evaluation,
    supports/absence, reconciliation, explanation. Split further into
    catalog/selection, evaluation, maintenance, explanation — not one
    800-line rules module.
12. **Sampling / read-view / query facades** — thin delegations to
    existing sampling, Cypher, modal, and viz engines.

## State-ownership design

The central risk is scope binding:

- `transaction()` builds a sibling `GraphDB` on `tx_store` and copies
  selected config while sharing the writer lock and clock.
- `current_read_view()` shallow-copies the graph and substitutes a
  snapshot store, clearing snapshot-local caches.

Rule: **components bind to an explicit store; never retain the parent’s
live store by accident.**

- Shared: database identity, writer coordination, serializer/backend
  capabilities, clock (where tests inject it).
- Store-bound: decoded-record caches, adjacency-count caches,
  snapshot-local metadata copies, transaction/snapshot handles.
- Scope-owned: backend transaction/snapshot lifetimes.
- Config: index/constraint sets (copied per scope, as today).

No giant mutable “context” object. Pass narrow dependencies
(store, serializer, index config, commit writer, claim reader) to each
component. Typing protocols only where multiple implementations or
test doubles justify them.

Stored rules depend on a small surface: selected claims, exact claim
versions, rule catalog, commit path. They do not receive unrestricted
`GraphDB` access. Same discipline for claims → temporal history.

## Phases

### Phase 0 — Behavioral contract (in progress)

- Inventory public signatures + private adapter dependencies (this file).
- Add characterization tests for transaction/snapshot binding and cache
  isolation: writes via `tx_graph` visible only after commit; rollback
  discards; snapshot reads pin; snapshot caches don’t leak to live;
  writer-guard and clock sharing preserved.
- Record representative fixtures/baselines for ingestion/sampling where
  hot paths will move.
- Identify subclassing/monkeypatching/fault-injection seams in tests.

### Phase 1 — Entities + pure codecs (first extraction)

- New `src/gestaltdb/_graph/entities.py` (≤800 lines): `Node`, `Edge`,
  `TimeIndexedEdge`, `GraphEntityDictSerializer`, `_normalize_labels`,
  `_property_value_to_{index,range_index}_bytes`, `datetime_to_bytes`,
  `bytes_to_datetime`.
- `graphdb.py` re-exports them for `from gestaltdb.graphdb import …`
  plus `TimeIndexedEdge`, datetime helpers, and keeps pickle streams
  stable by preserving `__module__ = "gestaltdb.graphdb"` on moved
  entity classes.
- Lower `graphdb.py` budget in `module-size-baseline.json` in the same
  change. No behavior change; AST without docstrings unchanged except
  imports.

### Phase 2 — Scope-binding mechanism

- Introduce internal store-bound component construction used by
  `__init__`, `transaction()`, and `current_read_view()`.
- Keep copy-vs-share semantics identical; add regression tests proving
  a component can never fall back to the live store.
- Migrate private adapter dependencies to the new internal interfaces
  where trivial; otherwise keep compat shims with deprecation notes.

### Phase 3 — Current-graph storage

- Extract typed adjacency, index operations, then record coordination
  in small steps. Keep node/edge write orchestration together where it
  enforces record/index/adjacency consistency.
- Preserve backend integration, constraints, deferred-index behavior,
  and cache invalidation.

### Phase 4 — Ingestion + sampling coordination

- Move columnar paths onto narrow write/index capabilities; no per-row
  service calls in hot loops.
- Preserve append-only adjacency semantics and native bulk paths.
- Parity tests + measured ingestion/sampling comparisons.

### Phase 5 — Temporal storage

- Codecs/read helpers → temporal indexes/history selection → commit
  coordination → recovery. Never another single giant temporal module.
- Preserve sequence/descriptor/visibility ordering, durability
  fault-injection evidence, and historical conformance.

### Phase 6 — Claims + stored rules

- Claims on temporal interfaces; then rule catalog, evaluation,
  maintenance, explanation as separate units.
- Migrate `reasoning/adapters/*` off `graphdb._*` privates.
- Keep standalone Datalog storage-independent.

### Phase 7 — Facade + docs

- `graphdb.py` becomes thin delegations; remove obsolete forwarding.
- Update `docs/architecture.rst`, `docs/code-quality.rst`,
  `docs/api-workflows.rst`, regenerate agent API index, Sphinx/API checks.
- Lower/remove legacy budget; each new module stays ≤800 lines.

## Per-extraction acceptance

- Public imports, signatures, return shapes, byte/string ID boundaries,
  and exception behavior stable.
- No storage-format or logical-identity change.
- Correct transaction/snapshot binding; no cross-scope work migration.
- Writer-lock and publication boundaries unchanged.
- Real LMDB/LevelDB/PyRex + serializer coverage; historical
  selection/commit conformance; fault-injection tests.
- Executable docstrings preserved; no unexplained ingestion/sampling
  regression.
- New modules ≤800 physical lines; legacy budget lowered in same commit.

## Risks

- Shallow-copy + shared-mutable-state bugs (mitigated by Phase 0 tests
  and explicit store-bound construction).
- Pickle compat for moved entity classes (mitigated by `__module__`
  preservation + round-trip tests).
- Private-adapter coupling (`graph._*`, constants, lock) — migrate
  deliberately, keep shims until callers move.
- 134 public methods + docstrings may not fit an 800-line facade;
  do not delete docs or generate opaque forwarding. First remove
  implementation complexity; review any facade-only exception explicitly.
- Large rewrite temptation — land in small reviewable slices with green
  real-backend evidence each time.

## Progress log

- 2026-10-25: Epic created; branch `feature/graphdb-refactor-388` from
  `4bcd071`. Fixed `docs/code-quality.rst` heading underlines (CI
  `sphinx -W` failure). Phase 0 scope tests + Phase 1 entity extraction
  in progress.
- 2026-10-25 (slice 1, landed): Phase 1 entities extracted to
  `src/gestaltdb/_graph/entities.py` (431 lines) + package init;
  `graphdb.py` 7113 → 6737 lines, budget lowered in same change.
  Pickle path preserved via `__module__ = "gestaltdb.graphdb"` with
  round-trip regression. Phase 0 `tests/test_graphdb_scopes.py` locks
  tx/snapshot store binding, shared-vs-isolated state, rollback, and
  cache isolation. Gates: Ruff, size ratchet, 136 backend tests
  (transactions/snapshots/interfaces/scopes), 67 workflow/agent/policy
  tests, docs drift + Sphinx `-W` green; API index regenerated.
- 2026-10-25 (slice 2, landed): Phase 2 scope binding centralized in
  `src/gestaltdb/_graph/scopes.py` (74 lines). `transaction()` and
  `current_read_view()` delegate shared-vs-store-bound field binding to
  `configure_transaction_child` / `configure_snapshot_child`; inline
  copies removed. `graphdb.py` 6737 → 6722 lines, budget lowered in same
  change. Added index-config copy-isolation regression. Same gates green
  (Ruff, size ratchet, 164 backend/policy tests, docs drift, Sphinx -W).
- 2026-10-25 (slice 3, landed): Phase 3 first step — typed-adjacency reads
  extracted to `src/gestaltdb/_graph/adjacency.py` (104 lines, pure
  store+bytes+cache functions). `get/count/iter_typed_adjacency`,
  `neighbors/edges_by_edge_type` delegate after ID normalization; public
  docstrings and generator-laziness preserved (invalid direction still
  raises on first `next()`, verified). Key codecs (`node/edge_key_to_bytes`,
  `key_to_string`) moved to `entities.py`; `GraphDB` keeps thin wrappers
  so bound/unbound call sites and doctests are unchanged. Write side stays
  until record/index orchestration moves. `graphdb.py` 6722 → 6692 lines,
  budget lowered in same change. 175 + 118 backend tests green.
- 2026-10-25 (slice 4, landed): adjacency write side moved into `adjacency.py` (`put/delete_typed_adjacency_for_edge`, `update_count_cache`; module now 154 lines). GraphDB private writers delegate with unchanged signatures, so all `put_edge`/delete/columnar/rebuild call sites are untouched. `graphdb.py` 6692 -> 6670 lines, budget lowered in same change. 175 + 109 backend tests green.
- 2026-10-25 (slice 5, landed): secondary-index maintenance writers moved to `index_maintenance.py` (put/delete node+edge indexes, rebuild-batch flush as store+config+record functions). GraphDB private writers delegate; all put/delete/rebuild/columnar call sites untouched. `graphdb.py` 6670 -> 6564 lines, budget lowered in same change. 395 backend tests green.
- 2026-10-26 (slice 7, landed): 11 index ID-iterators (node/edge label/property/type exact+range, full edge scan) moved to `index_lookup.py` (169 lines) as store+guard functions; entity-mapping/count wrappers untouched. Deferred-vs-eager guard timing verified identical. `graphdb.py` 6531 -> 6456 lines, budget lowered in same change. 361 backend tests green.
- 2026-10-26 (slice 8, landed): stale/index-metadata helpers (load/persist/mark/clear stale, ensure-current guard, property-index + constraint metadata) moved to `index_maintenance.py` (239 lines) as store+key functions; wrappers delegate. `graphdb.py` 6456 -> 6415 lines, budget lowered in same change. 395 backend tests green.
- 2026-10-26 (slice 9, landed): temporal key codecs (commit/visible/record keys, locators, instant encodings) + key-prefix constants moved to `temporal_keys.py` (57 lines); static wrappers delegate, adapters/tests keep importing prefixes from `graphdb`. `graphdb.py` 6415 -> 6409 lines, budget lowered in same change. Native-claim failures seen locally are a stale pyrex 0.4.1 install; all 63 pass with CI-pinned 0.6.0. Temporal/cache/version suites (157 tests) green.
- 2026-10-26 (slice 10, landed): edge record orchestration (put/get/delete, legacy adjacency blobs, endpoint cleanup) moved to `edge_records.py` (153 lines) as explicit store+serializer+config+cache functions; GraphDB keeps transaction boundaries and thin wrappers (vestigial `edge_key_serializer` kwarg preserved). Full optional suite: 3418 passed / 3 expected skips.
- 2026-10-26 (slice 11, landed): node record orchestration (put/get/get_many/keys/delete with incident-edge cascade via `edge_records`) moved to `node_records.py` (82 lines); GraphDB keeps transaction boundaries and thin wrappers. `update_node` stays as public-API composition. `graphdb.py` 6326 -> 6291 lines, budget lowered in same change. 461 focused backend tests green.
- 2026-10-26 (slice 13, landed): temporal sequence read/reserve/system-time helpers moved to `temporal_sequence.py` (115 lines) as explicit store+path+clock functions; sequence constants consolidated in `temporal_keys.py`. Writer guard stays on GraphDB. `graphdb.py` 6206 -> 6143 lines, budget lowered in same change. 116 temporal/durability tests green.
- 2026-10-26 (slice 14, landed): live sampling traversals (nodes/neighbors/paths/subgraph) moved to `sampling.py` (162 lines) as store+serializer+guard functions; snapshot building stays as facade orchestration. `graphdb.py` 6143 -> 6053 lines, budget lowered in same change. 309 sampling/traversal/workflow tests green.
- 2026-10-26 (slice 15, landed): ingestion support (mode validation/coercion, tqdm/SimpleProgress reporter, Polars frame introspection/chunking, per-chunk node index upkeep) moved to `ingest_support.py` (210 lines); unused `sys`/`time` imports dropped. Caught and fixed a wrapper-ordering bug in `_low_level_index_mode` before landing (validation must not precede the DEFER_REBUILD early return). `graphdb.py` 6053 -> 5943 lines, budget lowered in same change. 55 ingestion/benchmark tests green.
- 2026-10-26 (slice 16, landed): node columnar ingestion cores (arrow/polars payload + polars/arrow entity paths) moved to `ingest_nodes.py` (230 lines); entity variants reuse the tx-aware arrow wrapper passed as a callable, preserving nested-transaction behavior. `graphdb.py` 5943 -> 5806 lines, budget lowered in same change. 238 ingestion/cypher/benchmark tests green.
- 2026-10-26 (slice 18, landed): temporal index state/naming/checksum/ensure/history helpers moved to `temporal_indexes.py` (145 lines); entry builders stay until their index-name constants migrate with selection paths. `graphdb.py` 5524 -> 5472 lines, budget lowered in same change. 126 temporal/durability tests green.
- 2026-10-26 (slice 19, landed): temporal commit reads and version lookups (decode, cached commit, version/at/through, system horizon, typed version entry points, commit iteration) moved to `temporal_reads.py` (400 lines) behind a narrow `TemporalReader` struct (store+serializer+cache+keys); GraphDB keeps a `_temporal_reader()` factory and thin wrappers incl. adapter-used private names. Two transcription slips caught in review before landing (`normalize_logical_id`, claim-branch `TemporalVersionError`, `epistemic.Claim` import). `graphdb.py` 5472 -> 5217 lines, budget lowered in same change. 116 + 389 temporal/claims/rules tests green.
- 2026-10-26 (slice 20, landed): temporal index entry encoding + index writes moved to `temporal_indexes.py` (now 290 lines); GraphDB wrappers resolve the generation and delegate. Entries verified AST-identical to the original modulo naming/generation-resolution after catching two transcription slips. `graphdb.py` 5217 -> 5092 lines, budget lowered in same change. 123 temporal tests green.
- 2026-10-26 (slice 17, landed): edge columnar ingestion cores (bulk writer, arrow/polars payload + entity paths, per-chunk property indexes) moved to `ingest_edges.py` (413 lines); entity variants reuse tx-aware wrappers. `graphdb.py` 5806 -> 5524 lines, budget lowered in same change. 70 + 285 ingestion-adjacent tests green.
- 2026-10-26 (slice 12, landed): node/edge index rebuild scans moved to `index_rebuild.py` (131 lines) as store+serializer+config+key functions composing the records/maintenance modules; GraphDB keeps transaction recursion and thin wrappers. `graphdb.py` 6291 -> 6206 lines, budget lowered in same change. 185 focused backend tests green. `graphdb.py` 6409 -> 6326 lines, budget lowered in same change.
- 2026-10-25 (slice 6, landed): backend/serializer registries, version and manifest timestamp helpers moved to `registry.py` (55 lines); unused `importlib.metadata` import dropped. `graphdb.py` 6564 -> 6531 lines, budget lowered in same change. Same gates green.
