# Staged GraphDB decomposition

Tracking: https://github.com/mylonasc/gestaltdb/issues/388 (open follow-up).

The initial lint change adds prevention, **not** a 7,113-line behavioral refactor.
Module ceilings alone cannot prove cohesion or reduce the coupling of GraphDB's
many responsibilities. Each extraction needs focused tests plus real-backend
release coverage; avoid one large release-finishing rewrite.

## Proposed logical groups

1. **Entities and lifecycle**: Node/Edge and legacy entity serialization, database
   manifest/configuration, backend construction, resource ownership. Preserve
   public import aliases and persisted Pickle compatibility before moving types.
2. **Mutable graph indexes and adjacency**: secondary/range index maintenance,
   native labels/types, typed adjacency and counts. Keep constraint and index
   freshness checks at all existing write boundaries.
3. **Columnar ingestion**: Arrow/Polars entity and serialized-payload paths,
   batching, deferred indexes, and native writer integration. Preserve the fast
   ingestion path, including append-only adjacency semantics.
4. **Sampling integration**: live traversal and snapshot-building handoffs; keep
   array-native execution in the existing sampling package. Preserve fast sampling
   and authenticated read-view lifetimes.
5. **Temporal storage internals**: commit sequencing and durable writer guard,
   history records, visibility, orphan recovery, temporal indexes and selection.
   Split further into modules below the ceiling, not another giant temporal file.
6. **Stored rules and truth maintenance**: rule catalog, fixpoint evaluation,
   supports/absence and explanations. This remains distinct from standalone
   Datalog; the core reasoning/compiler must not acquire storage dependencies.
7. **Claim/modal integration**: assertion/correction/retraction, indexed claim
   selection, status and accessibility; keep evaluators in their existing modules.
8. **Query/viz adapters**: small facade methods delegate to existing engines.

These are review boundaries, not a mandate for eight mixins or multiple
inheritance. Prefer explicit private collaborators/functions with documented
state/lock ownership; choose the mechanism in the first extraction after checking
cycles and private adapter dependencies. Preserve public signatures and wrappers.

## Per-extraction acceptance

* Keep public imports, class identity where persistence requires it, signatures,
  byte/string ID boundaries, and exception behavior stable.
* Never move work across transaction, snapshot, or temporal writer-guard scopes.
* Document state ownership and cross-component calls. No wildcard-import glue or
  runtime monkeypatching simply to meet the line budget.
* Preserve real LMDB/LevelDB/PyRex and serializer integration coverage, historical
  selection/commit conformance, fault-injection tests, and executable docstrings.
* Compare ingestion/sampling benchmarks when their hot paths change; architectural
  cleanup is not permission to silently regress these first-class capabilities.
* New modules stay <=800 physical lines; lower the legacy budget after extraction.
  Regenerate the agent API index if source locations change, update architecture
  docs, and run Sphinx/API checks.

The other seven legacy exceptions (backends, sampling, Cypher parser/runtime/
semantics, and typed reasoning) need their own cohesive extraction plans. Do not
silently exclude them or regenerate larger baselines when adding functionality.
