# Epic: Temporal and Epistemic Knowledge Graphs

Status: **in progress** (`TKG-01` implemented on `temporal-epistemic-kg`)

GitHub parent: [#63](https://github.com/mylonasc/gestaltdb/issues/63)

## Objective

Evolve GestaltDB from a mutable attributed property graph into a foundation for
bitemporal knowledge graphs, leakage-safe temporal sampling, and temporal
epistemic reasoning while preserving existing non-temporal behavior.

The target capabilities are:

- immutable valid-time and system-time fact history;
- indexed as-of entity resolution and traversal;
- temporal values, functions, and graph views in Cypher;
- reproducible sampler snapshots tied to a consistent source view;
- point-, window-, and causality-constrained graph sampling;
- time-aware positive membership and hard-negative generation;
- sourced, possibly inconsistent epistemic claims;
- rule evaluation, truth maintenance, explanations, and bounded modal queries.

## Baseline and Non-Goals

GestaltDB currently stores one mutable canonical record per node or edge.
Temporal properties can be modeled manually, but updates overwrite history,
property range indexes are scalar rather than interval-aware, and sampler
snapshots discard arbitrary temporal properties. `TimeIndexedEdge` is a narrow
timestamp-key helper and is not the temporal storage model for this epic.

This epic does not attempt distributed consensus, an unbounded theorem prover,
full OWL/RDF compatibility, or unrestricted first-order modal logic. Initial
reasoning is finite, resource-bounded, provenance-producing, and explicit about
open-world and contradiction semantics.

## Semantic Contract

- An instant is a timezone-aware value normalized to signed UTC epoch
  microseconds.
- A validity interval is non-empty and half-open: `[start, end)`.
- `end=None` means positive infinity.
- Valid time says when a statement holds in the modeled domain.
- System time says when a version became visible to GestaltDB.
- Historical fact payloads are immutable; corrections and retractions append
  versions.
- One read view fixes a system-time horizon and may select a valid-time instant
  or window.
- Existing non-temporal APIs retain their current-view behavior unless an API
  explicitly requests temporal semantics.

## Staged Delivery

| Stage | Features | Exit outcome |
| --- | --- | --- |
| 1. Foundation | TKG-01 to TKG-03 | Canonical values, immutable versions, indexed as-of traversal |
| 2. Query views | TKG-04 to TKG-06 | Temporal Cypher values and consistent bitemporal queries |
| 3. Temporal ML | TKG-07 to TKG-09 | Temporal snapshots, constrained sampling, leakage-safe negatives |
| 4. Epistemic reasoning | TKG-10 to TKG-13 | Claims, rules, maintenance, and bounded modal evaluation |
| 5. Production readiness | TKG-14 | Compatibility, migration, docs, benchmarks, and release hardening |

## Dependency Graph

```text
TKG-01 ─┬─> TKG-02 ─┬─> TKG-03 ─┬─> TKG-06
        │            │            ├─> TKG-07 ─> TKG-08 ─> TKG-09
        │            │            ├─> TKG-10 ─> TKG-11 ─> TKG-12 ─> TKG-13
        │            └─> TKG-05 ──┼─> TKG-06
        │                         ├─> TKG-07
        │                         └─> TKG-10
        └─> TKG-04 ────────────────> TKG-06

TKG-01..TKG-13 ─> TKG-14
```

## Stage 1: Foundation

### TKG-01: Canonical Temporal Primitives and Sortable Encoding

GitHub: [#50](https://github.com/mylonasc/gestaltdb/issues/50)

Dependencies: none.

Scope:

- Add immutable `TemporalInstant`, `TemporalInterval`, and `TemporalContext`.
- Normalize aware datetimes and ISO-8601 offsets to exact UTC microseconds.
- Define `[start, end)` containment, overlap, and intersection.
- Add chronological eight-byte signed sortable encoding.
- Keep the API independent from GraphDB storage, Cypher, and snapshots.

Example:

```python
from gestaltdb.temporal import TemporalContext, TemporalInstant, TemporalInterval

validity = TemporalInterval.parse("2025-01-01T00:00:00Z", None)
as_of = TemporalContext.as_of(TemporalInstant.parse("2026-01-01T00:00:00Z"))
assert as_of.matches(validity)
```

Acceptance criteria:

- Aware datetimes normalize deterministically; naive datetimes are rejected.
- Pre-epoch, epoch, offset, boundary, and encoding round trips are tested.
- Empty/reversed intervals are rejected and adjacent intervals do not overlap.
- Existing graph storage formats and behavior do not change.

### TKG-02: Immutable Bitemporal Node and Edge Versions

GitHub: [#49](https://github.com/mylonasc/gestaltdb/issues/49)

Dependencies: TKG-01.

Scope:

- Separate logical entity IDs from immutable version IDs.
- Append valid interval, system commit, operation, payload hash, and
  supersession metadata.
- Add monotonic commit IDs and visibility markers for crash-safe history.
- Add assertion, correction, and retraction APIs for nodes and edges.
- Preserve existing non-temporal `put_*` behavior.

Example:

```python
version = graph.put_edge_version(edge, valid=TemporalInterval.parse("2022-01-01T00:00:00Z"))
graph.retract_edge_version("employment-1", valid_from="2025-04-01T00:00:00Z")
```

Acceptance criteria:

- Corrections never mutate earlier versions or earlier as-of results.
- Transactional backends commit a version batch atomically.
- Non-transactional backends hide incomplete commits through commit markers.
- Existing databases open without migration.

### TKG-03: Temporal Indexes and As-Of Traversal

GitHub: [#61](https://github.com/mylonasc/gestaltdb/issues/61)

Dependencies: TKG-01, TKG-02.

Scope:

- Index logical/version IDs, valid intervals, system commits, types, endpoints,
  and directions.
- Add temporal outgoing/incoming typed adjacency.
- Resolve visible versions at a valid instant and system horizon.
- Add entity history and indexed as-of traversal APIs.
- Integrate temporal indexes with deferred maintenance and rebuilds.

Example:

```python
edges = graph.iter_edges_as_of(
    source="alice", edge_type="WORKS_FOR", direction="out",
    valid_time="2023-06-01T00:00:00Z", system_time="2026-01-01T00:00:00Z",
)
```

Acceptance criteria:

- Point lookup and typed traversal avoid full historical scans.
- `[start, end)` boundaries, corrections, retractions, and parallel edges are
  covered.
- Rebuilt and incrementally maintained indexes return identical results.

## Stage 2: Query Views

### TKG-04: Cypher Temporal Values and Functions

GitHub: [#51](https://github.com/mylonasc/gestaltdb/issues/51)

Dependencies: TKG-01.

Scope:

- Implement Cypher `date`, `time`, `localtime`, `datetime`, `localdatetime`,
  and `duration` values and constructors.
- Add comparison, property access, ordering, null behavior, parameters, and
  supported arithmetic.
- Convert aware Python temporal values to the canonical primitives.

Example:

```cypher
RETURN datetime('2025-03-01T12:30:00Z') AS observedAt,
       date({year: 2025, month: 3, day: 1}) AS observedDate,
       duration({days: 7}) AS retention
```

Acceptance criteria:

- Equivalent offsets compare equal after normalization.
- Temporal values work in `WHERE`, `ORDER BY`, `CASE`, lists, maps, and
  parameters.
- Invalid mixed-type operations produce deterministic semantic errors.

### TKG-05: Consistent Read Views and Provenance Tokens

GitHub: [#52](https://github.com/mylonasc/gestaltdb/issues/52)

Dependencies: TKG-02.

Scope:

- Add `GraphDB.read_view` with database identity, commit horizon, default valid
  time, serializer, backend snapshot identity, and source provenance.
- Use backend read transactions where available and commit visibility elsewhere.
- Make snapshot builds atomic and tied to one read view.

Example:

```python
with graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
    snapshot = view.build_sampler_snapshot("snapshots/2024-06")
```

Acceptance criteria:

- A view never observes commits newer than its captured horizon.
- Snapshot node and edge scans cannot mix horizons.
- Provenance is deterministic, JSON serializable, and checked on hydration.

### TKG-06: Bitemporal As-Of Cypher Querying

GitHub: [#53](https://github.com/mylonasc/gestaltdb/issues/53)

Dependencies: TKG-03, TKG-04, TKG-05.

Scope:

- Add valid-time and system-time qualifiers to `MATCH` and `OPTIONAL MATCH`.
- Propagate temporal context through paths, unions, and subqueries.
- Add entity version metadata functions.
- Plan temporal matches against TKG-03 indexes.

Example:

```cypher
MATCH (p:Person)-[r:WORKS_FOR]->(c:Company)
FOR VALID_TIME AS OF datetime($validAt)
FOR SYSTEM_TIME AS OF datetime($knownAt)
RETURN c.name, validFrom(r), systemFrom(r), versionId(r)
```

Acceptance criteria:

- A query uses one stable system horizon.
- Optional and variable-length matching only exposes visible versions.
- Queries without qualifiers preserve current behavior.

## Stage 3: Temporal ML and Sampling

### TKG-07: Temporal Sampler Snapshot Arrays and Indexes

GitHub: [#62](https://github.com/mylonasc/gestaltdb/issues/62)

Dependencies: TKG-03, TKG-05.

Scope:

- Version the snapshot format.
- Persist edge version IDs, `valid_from_us`, `valid_to_us`, and open-end masks.
- Build node/relation/time ordered adjacency indexes.
- Record the exact source view and temporal encoding in metadata.
- Validate arrays, checksums, and completion atomically on load.

Example:

```python
snapshot = graph.build_sampler_snapshot(
    "snapshots/history", temporal=True,
    system_time="2026-01-01T00:00:00Z", time_bucket="day",
)
```

Acceptance criteria:

- All temporal arrays align with edge arrays in RAM and memmap modes.
- Time indexes narrow candidate ranges without changing exact results.
- Builds from the same source view and options are deterministic.

### TKG-08: Runtime Temporal Neighbor and Subgraph Sampling

GitHub: [#55](https://github.com/mylonasc/gestaltdb/issues/55)

Dependencies: TKG-07.

Scope:

- Add point and window filters to neighbor, multihop, and subgraph sampling.
- Filter before random selection.
- Add interval-overlap/containment and monotonic causal path policies.
- Return sampled edge versions and temporal arrays in batches.

Example:

```python
sample = engine.sample_neighbors(
    [alice_id], fanout=20, relations=[works_for_id],
    temporal=TemporalContext.as_of(cutoff),
)
```

Acceptance criteria:

- Exclusive valid-to boundaries never leak into samples.
- Relation, direction, and temporal filters compose.
- Seeded RAM and memmap sampling are equivalent and reproducible.

### TKG-09: Time-Aware Hard Negatives and Positive Membership

GitHub: [#54](https://github.com/mylonasc/gestaltdb/issues/54)

Dependencies: TKG-08.

Scope:

- Index positive triple histories and intervals.
- Add at-example-time, window, and any-time rejection policies.
- Restrict candidate entities and neighborhoods to temporal availability.
- Separate context leakage prevention from future-safe false-negative rejection.

Example:

```python
config = HardNegativeConfig(
    negatives_per_positive=8,
    temporal_positive_policy="at_positive_time",
    temporal_candidate_window_days=90,
)
```

Acceptance criteria:

- Positives under the selected temporal policy are never emitted as negatives.
- Future facts cannot shape context or candidate pools unless explicitly allowed.
- Exhaustion behavior and diagnostics are deterministic and configurable.

## Stage 4: Epistemic Reasoning

### TKG-10: Bitemporal Epistemic Claims and Provenance

GitHub: [#59](https://github.com/mylonasc/gestaltdb/issues/59)

Dependencies: TKG-02, TKG-03, TKG-05.

Scope:

- Add immutable subject-predicate-object claims with entity or literal objects.
- Record polarity, agent, source, confidence, world/context, provenance, and
  temporal version metadata.
- Permit simultaneous positive and negative claims.
- Index claims by statement, source, agent, world, polarity, and time.

Example:

```python
claim = graph.assert_claim(
    subject="alice", predicate="WORKS_FOR", object="acme",
    polarity="positive", agent="source:hr-feed", confidence=0.97,
    world="reported", valid_from="2024-01-01T00:00:00Z",
)
```

Acceptance criteria:

- Confidence and polarity are independent and validated.
- Contradictory sourced claims coexist without destructive updates.
- Claim lookup and retraction honor both temporal dimensions.

### TKG-11: Rules and Semi-Naive Fixpoint Evaluation

GitHub: [#60](https://github.com/mylonasc/gestaltdb/issues/60)

Dependencies: TKG-03, TKG-10.

Scope:

- Add a versioned catalog of safe positive Horn rules.
- Implement semi-naive delta evaluation to a bounded fixpoint.
- Intersect premise validity when deriving conclusions.
- Record rule version and premise version IDs for every justification.

Example:

```python
graph.create_rule(
    "employment-implies-affiliation",
    when=[("?p", "WORKS_FOR", "?c"), ("?c", "MEMBER_OF", "?g")],
    then=("?p", "AFFILIATED_WITH", "?g"),
)
graph.run_rules(as_of="2025-01-01T00:00:00Z")
```

Acceptance criteria:

- Unsafe rules are rejected before persistence.
- Recursive finite rule sets reach a stable, resource-bounded fixpoint.
- Equivalent conclusions deduplicate while retaining all justifications.

### TKG-12: Incremental Truth Maintenance and Explanations

GitHub: [#56](https://github.com/mylonasc/gestaltdb/issues/56)

Dependencies: TKG-02, TKG-10, TKG-11.

Scope:

- Index premise/rule dependencies and count independent supports.
- Incrementally add or invalidate derivations after temporal changes.
- Retract conclusions only after their final valid justification disappears.
- Return finite explanation graphs for active and historical conclusions.

Example:

```python
graph.retract_claim(claim_id, reason="source correction")
graph.maintain_truth()
explanation = graph.explain_claim(derived_claim_id, valid_time=at, system_time=known_at)
```

Acceptance criteria:

- Incremental maintenance equals full recomputation.
- Earlier system-time conclusions and explanations remain reproducible.
- Cyclic derivations terminate safely and interrupted maintenance is resumable.

### TKG-13: Bounded Modal and Epistemic Evaluation

GitHub: [#57](https://github.com/mylonasc/gestaltdb/issues/57)

Dependencies: TKG-10, TKG-11, TKG-12.

Scope:

- Model finite worlds and agent-specific accessibility as temporal facts.
- Add resource-bounded `BELIEVES`, `KNOWS`, `POSSIBLE`, and `NECESSARY`
  evaluation through an allowlisted procedure.
- Return four-valued outcomes: supported, refuted, both, or unknown.
- Include temporal and derivation evidence in explanations.

Example:

```cypher
CALL kg.entails(
  'alice', {subject: 'bob', predicate: 'LOCATED_IN', object: 'paris'},
  'KNOWS', {world: 'actual', validTime: $at, maxDepth: 4}
)
YIELD status, confidence, explanation
RETURN status, confidence, explanation
```

Acceptance criteria:

- Evaluation is scoped by agent, world, valid time, and system horizon.
- Accessibility and nested modality limits guarantee termination.
- `KNOWS` and `BELIEVES` remain distinct under configured frame semantics.

## Stage 5: Production Readiness

### TKG-14: Compatibility, Migration, Documentation, and Benchmarks

GitHub: [#58](https://github.com/mylonasc/gestaltdb/issues/58)

Dependencies: TKG-01 through TKG-13.

Scope:

- Document semantics, guarantees, unsupported cases, and runnable examples.
- Add migration policy for existing databases, snapshots, and `TimeIndexedEdge`.
- Benchmark temporal writes, reads, traversal, snapshots, sampling, negatives,
  inference, and maintenance.
- Add backend matrices, crash recovery, corruption checks, retention, and
  release capability gates.

Acceptance criteria:

- Existing non-temporal tests and supported persisted data remain compatible.
- Documentation examples, Sphinx, API drift checks, and package smoke tests pass.
- Benchmarks report latency, throughput, memory, and storage amplification.
- Experimental and stable APIs are explicitly identified in release notes.

## Cross-Cutting Engineering Rules

Every TKG feature must:

- preserve existing behavior unless its issue explicitly declares a versioned
  compatibility change;
- test pre-epoch values, exact interval boundaries, open ends, and corrections
  where temporal semantics apply;
- distinguish valid time from system time in APIs and documentation;
- avoid future-data leakage in snapshots, sampling, and negatives;
- record sufficient provenance to reproduce a result;
- add focused tests and run the relevant existing subsystem suite;
- update user, Sphinx, and agent-facing documentation only for implemented APIs.

## Current Implementation Status

- [x] Epic and dependency plan drafted.
- [x] `TKG-01` implemented with canonical temporal primitives.
- [x] `TKG-01` verification and issue closure.
- [ ] `TKG-02` onward.
