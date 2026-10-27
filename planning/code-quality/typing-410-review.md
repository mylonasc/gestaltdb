# Typing review and implementation proposal (#410)

Review date: 2026-10-27. Baseline: the current `issue-410-typing` worktree,
including its uncommitted typing slice. This is a proposal, not a claim that
the work below has been implemented.

## Measurement and conclusions

The badge configuration reports **19,826 precise / 32,120 eligible lines =
61.7248%**, with **107 errors in 29 files** (154 source files checked).
There are 12,294 eligible lines classified as Any or imprecise.
The checked-in badge has not been changed by this review.

Reproduce with the same environment as the badge:

```sh
uv run --extra lint --with 'numpy>=1.23' --with 'lark>=1.2' python -m mypy \
  --lineprecision-report /tmp/opencode/gestaltdb-410-typing-review \
  --any-exprs-report /tmp/opencode/gestaltdb-410-typing-review src/gestaltdb
```

The denominator is `lines - empty - unanalyzed`, not physical source lines.
The Any-expression report helps identify causes; its expression counts are
not interchangeable with line counts. Untyped bodies can have very few
reported Any expressions while still contributing many non-precise lines.

**Error elimination is not the same as precision improvement.** For example,
`temporal_engine.py` has no diagnostics but only 28 precise lines out of 300;
`reasoning/plans.py` has no diagnostics but 199 non-precise lines. Conversely,
several remaining diagnostics are small inference/narrowing fixes with little
direct effect on the badge. Track both measures separately.

Typing makes sense throughout the library's authored algorithms, public APIs,
storage contracts, and persisted record schemas. The largest useful changes
are contracts that stop Any from propagating into many callers, not annotating
each local variable independently.

## Coverage opportunities, ranked by direct impact

All paths below are relative to `src/gestaltdb/`. A gap is the module's current
`Any + Imprecise` line count. The percentage-point column is an **upper bound**
if every gap line became precise at a fixed denominator, not an effort estimate
or promised gain. Legitimate dynamic values mean some gaps should remain.

| Rank | Module | Gap lines | Maximum badge gain | Main intervention |
| --- | --- | ---: | ---: | --- |
| 1 | `kvstores.py` | 931 | 2.90 pp | Complete backend method and snapshot/transaction contracts |
| 2 | `sampling/snapshot.py` | 882 | 2.75 pp | Dtype-aware arrays, metadata schemas, source capability |
| 3 | `sampling/engine.py` | 648 | 2.02 pp | Propagate array types through results and kernels |
| 4 | `query_engine/cypher/runtime.py` | 598 | 1.86 pp | Typed row streams, context and path/seek state |
| 5 | `query_engine/cypher/parser.py` | 583 | 1.82 pp | Concrete transformer outputs and typed parser boundary |
| 6 | `query_engine/cypher/write.py` | 346 | 1.08 pp | Typed clause handlers and entity update helpers |
| 7 | `graphdb.py` | 324 | 1.01 pp | Constructor/state and remaining facade signatures |
| 8 | `_graph/truth_maintenance.py` | 310 | 0.97 pp | Fact schemas, join rows and dependency maps |
| 9 | `viz/reasoning.py` | 309 | 0.96 pp | Typed normalized graph/explanation records |
| 10 | `_graph/temporal_selection.py` | 285 | 0.89 pp | Reader, hydration callback and version-kind overloads |
| 11 | `temporal_engine.py` | 272 | 0.85 pp | Typed engine state and delegated method signatures |
| 12 | `viz/ir.py` | 233 | 0.73 pp | Object-to-JSON normalization boundary |
| 13 | `readview.py` | 225 | 0.70 pp | Pinned capabilities and provenance contracts |
| 14 | `_graph/stored_rules.py` | 223 | 0.69 pp | Shared fact and join-row contracts |
| 15 | `reasoning/typed.py` | 217 | 0.68 pp | Remaining evaluation helpers and generic containers |
| 16 | `reasoning/plans.py` | 199 | 0.62 pp | Components, observers and plan explanation results |

The first six modules contain 3,988 gap lines: a 12.42-point direct ceiling.
Improvements to shared contracts may also benefit callers outside these modules;
do not add those propagated gains to these ceilings without remeasurement.

## Recommended implementation order

Order accounts for shared dependencies and risk as well as the ranked gaps.
Keep each slice independently reviewable and behavior-preserving.

### 1. Storage and serialization foundations

Start with `kvstores.py`: complete overrides of metadata, index/range scans,
bulk reads, typed adjacency, transactions and snapshot methods. Use
`bytes | None`, ordered sequences of optional values, iterator element types,
and context-manager return types. Distinguish a snapshot's read capability from
a transaction's commit/rollback capability; do not promise writes on read views.

Reuse existing `KVStore`/`KVTransaction` contracts before introducing protocols.
Use small structural protocols only where native backend handles need them;
do not replicate the complete LMDB/RocksDB API in local stubs. Keep a documented
dynamic boundary around missing third-party typing and optional native features.

`serializers.py` uses bare `dict` throughout its payload interface. Parameterize
those dictionaries without pretending arbitrary graph properties are JSON-only.
`_graph/entities.py:GraphEntityDictSerializer` already has Node/Edge literal
overloads: reuse this contract in temporal readers/writers instead of
`entity_serializer: object`, or extract its minimal structural interface if
custom serializers require that flexibility. Do not redesign serialization.

Investigate diagnostics rather than declaring fictional state. For example,
`kvstores.py:SimpleKV.put` accesses `self.env`, but its constructor does not
initialize it. Establish whether injection is supported or the helper is broken;
any runtime repair belongs in a separate, regression-tested bug-fix slice.

### 2. Array-native sampling (1,794 gap lines across the sampling package)

First type `sampling/adjacency.py:CSRAdjacency` and `build_csr`, then the
containers in `sampling/batch.py` and `sampling/engine.py`, then propagate into
`sampling/snapshot.py` and the engine kernels. This package has a 5.59-point
direct ceiling. Bare `np.ndarray` is a major source of omitted-generic Any;
adding more bare arrays will not solve it.

Use `numpy.typing.NDArray` aliases for verified int64 IDs/offsets/timestamps,
float64 weights, Boolean masks and string IDs. Accept `ArrayLike` only where the
implementation actually accepts and normalizes it. Preserve memmap operation,
existing dtypes, and conversion/copy behavior. Dtype typing does not prove array
shape, alignment, local/global ID meaning, or cross-array optionality; keep the
existing runtime validation and tests.

Particular targets: snapshot array fields, `build`, `build_temporal`,
`from_edge_arrays`, `load`, `_write`, `_build_sampling_indexes`,
`_build_temporal_csr`, and engine result dataclasses/selection helpers.
Give fixed snapshot metadata and temporal encoding sections TypedDict schemas;
retain extensible user metadata separately.

`SampledSubgraphBatch.to_numpy` currently promises arrays but can return `None`
for companion temporal fields when a caller constructs a partial batch. Do not
cast these away merely because normal builders populate them together. Decide
whether the public contract permits partial construction; model the current
behavior accurately, or enforce a new invariant in a separate tested change.

Type the framework-neutral portion of `to_arrow`, `to_pyg`, `to_tf_gnns`, and
`to_dgl`; isolate unavoidable framework-specific Any at those conversions.
Keep optional dependencies optional at runtime.

### 3. Temporal/epistemic engines and stored-rule execution

The engines, history/readview, temporal helpers, stored rules and truth
maintenance together have 1,964 gap lines (6.11-point ceiling).

Start with `TemporalReader` serializer/cache fields and commit/version returns,
then `_graph/temporal_selection.py`, then `TemporalEngine`, `TemporalHistory`
and read-view methods. Supply `NodeVersion | EdgeVersion | ClaimVersion`
aliases and literal-kind overloads for `entity_as_of`; typed hydrate/live-store
callbacks should preserve the current instrumentation and rebinding seams.
Type the commit cache's actual key/entry shape and store identity, not just
`dict[str, object]`. Preserve sentinel-versus-None semantics in claim filters;
a private sentinel type can express this if needed without changing behavior.

For `_graph/stored_rules.py` and `_graph/truth_maintenance.py`, introduce a
shared internal fact TypedDict and a join-row tuple alias covering bindings,
premise IDs, optional validity/world, and delta participation. The initial
`rows = [({}, (), None, None, False)]` currently infers an unusably narrow type.
Then type `maintain_truth`, `explain_claim`, dependency maps and engine methods.
Do not move standalone reasoning contracts into storage-dependent modules.

### 4. Cypher execution, then parser internals

The Cypher package has 1,948 gap lines (6.06-point ceiling). Prioritize runtime
and writes over a wholesale grammar-transformer rewrite.

Reuse `BindingRow` and existing context/plan types in `runtime.py` path/seek
helpers: `_edge_seek_spec`, `_path_start_rows`, `_variable_paths`,
`_shortest_paths`, and `_shortest_between`. Type stream inputs/outputs and
fixed path state; retain heterogeneous user values behind an honest value
boundary. In `write.py`, target `apply_create`, `apply_merge`, `apply_foreach`,
`apply_set`, `apply_remove`, `apply_delete`, and property/entity helpers.

In `parser.py`, type `_build_match_query` and concrete transformer returns
(AST node, pattern, expression or tagged suffix). Lark's heterogeneous child
lists may need narrowly scoped Any or casts at grammar-controlled positions.
Do not force every callback into one enormous union or falsely claim all
children are expressions. Keep parsing outputs precise even when the library
callback inputs cannot be. The grammar is authored text, not generated Python;
that is not grounds to exclude this module.

### 5. Lower-effort follow-ups and typed admission adapters

`reasoning/plans.py` plus `reasoning/typed.py` have 416 gap lines (1.30 points).
Annotate `_components`, `_access_candidates`, `_path`, observer keys/counters,
`explain_plan`, `_matches_stratified`, and `_evaluate_aggregates`. These can be
small intervening slices while larger clusters are reviewed. Keep
`reasoning/typed.py` at or below its current 1,105-line budget.

Then propagate temporal contracts through `reasoning/adapters/native_claims.py`,
`native_locator_claims.py`, `range_generation.py`, `range_publication.py`, and
`authenticated_inferred_sampler.py`: 753 gap lines, a 2.34-point ceiling.
Explicit `_read_winners`/`extract`/target-state types matter here because they
describe authentication and publication boundaries, despite few diagnostics.
Typing must not change freshness checks, evidence completeness or cancellation.

Finish smaller reasoning/ontology errors separately: container annotations,
term narrowing, homogeneous loops and constructor contracts. Where constructors
intentionally accept iterables and freeze them in `__post_init__`, make the
constructor typing honest rather than silently narrowing accepted inputs.
Ontology has only 111 gap lines (0.35 points); it is not the main coverage lever.

### 6. Visualization normalization (827 gap lines, 2.57-point ceiling)

Start with `viz/ir.py` normalization and fixed output records. Accept `object`
for unconstrained external values, narrow before use, and return a recursive
JSON-value type from `_sanitize_property_value` and `_sanitize_properties`.
Do not impose this JSON constraint on graph properties before sanitization.

Propagate typed records into `viz/reasoning.py`, then `chain.py`, SVG/HTML and
the public API. In `chain.py`, use an explicit keyword TypedDict or explicit
arguments for heterogeneous `**limits`; a plain `dict[str, int | None]` loses
parameter-specific constraints. Keep duck-typed external inputs permissive
only at the ingestion seam, rather than making the whole rendering pipeline Any.

## Exclusions and honest dynamic boundaries

**No new exclusions are recommended or applied in this review.** All inspected
Python subsystems contain useful contracts worth checking. Static JS/assets,
grammar strings and generated JSON are already outside Python line precision;
compatibility shims are tiny and already precise. Optional backends and ML
adapters are not automatically untypable.

Appropriate remaining dynamic boundaries include arbitrary user property values,
pickle payloads, extensible metadata, heterogeneous Lark callback input and
third-party objects without usable type information. Prefer `object` plus
narrowing when operations are constrained; retain documented, local Any when
dynamic behavior is intentional. A mass Any-to-object substitution is not a
useful precision improvement if it just pushes casts into every caller.

`# type: ignore[code]` suppresses a diagnostic; it does not establish precision
or remove a line from the badge denominator. `@typing.no_type_check` disables
checking and exposes an Any callable; it is not a precision annotation. Do not
use either to manufacture coverage. If genuinely generated/vendor Python is
introduced later, document its provenance and exclude it explicitly from the
report scope, taking imported-module behavior into account; disclose the scope
change and report the old denominator alongside the new one.

## Acceptance and tracking

* Record before/after precise, Any, imprecise and unanalyzed line counts,
  denominator, diagnostic count and changed-module results for every slice.
  Unanalyzed growth or a smaller denominator is not improved typing.
* Keep the same checker/dependency environment for comparisons. Mypy is pinned
  to 2.4.0; NumPy/Lark resolution can still change, so record/fix those versions
  in the comparison environment. Do not compare different mypy flags as a ratchet.
* Add small positive/negative mypy fixtures for public contracts: missing
  entities, read-only capabilities, literal-kind version returns, batch arrays,
  and generic feeder results. Runtime tests alone do not prove static typing.
* Introduce per-module `check_untyped_defs`, then `disallow_untyped_defs`,
  `disallow_any_generics` and `warn_return_any` only after the relevant slice is
  clean. Compare stricter diagnostics separately before changing badge policy.
* Preserve public inputs, serializer compatibility, read-view lifetimes,
  transaction boundaries and native fast paths. Keep bug fixes separate.
* Run ruff, the module-size ratchet and focused tests per slice; lower recorded
  size budgets when modules shrink. At integration, regenerate the API index,
  run docs drift checks and the full suite with the intended optional extras.
  The current known missing-pyarrow failure must be reported, not suppressed as
  a typing issue. Refresh the badge only after measuring validated changes.

Recommendation: implement storage foundations first, then dtype-aware sampling.
These address the largest individual modules and propagate useful types broadly.
Follow with temporal contracts and Cypher, interleaving small reasoning slices;
do not target 100% by excluding the library's difficult, high-value code.

## Implementation progress: storage and array-contract slice

Implemented after the review:

* Completed byte-level storage signatures across LMDB, LevelDB and PyRex,
  including missing-value results, key/record/index iterators, transactions,
  snapshots and columnar entry points. Native handle/options objects remain a
  documented typing boundary rather than acquiring invented backend state.
* Added shared rank-independent, dtype-aware array aliases; propagated them
  through CSR adjacency, sampling containers, snapshot fields/build helpers,
  selection kernels, temporal masks and export methods. Memmaps remain valid.
* Added `NumpyBatch`, a TypedDict distinguishing always-present integer arrays
  from optional temporal fields and nullable companion values. Existing partial
  batch construction and export behavior is unchanged.
* Typed the generic async feeder, with a cast after its existing sentinel/error
  checks. Added static consumer tests, negative typing cases checked with unused
  ignores enabled, and runtime contracts for partial exports and memmaps.
* Enabled per-module untyped-definition/generic/Any-return ratchets for the
  array aliases, CSR adjacency and feeder modules. No whole-library strict-mode
  switch and no precision scope exclusions were introduced.

Measured in the same mypy 2.4.0 / NumPy 2.5.2 / Lark 1.3.1 environment:

| Measure | Review baseline | After slice |
| --- | ---: | ---: |
| Precise lines | 19,826 | 21,086 |
| Imprecise lines | 2,027 | 1,427 |
| Any lines | 10,267 | 9,639 |
| Unanalyzed lines | 379 | 382 |
| Eligible denominator | 32,120 | 32,152 |
| Precision | 61.7248% | 65.5822% |
| Diagnostics | 107 / 29 files | 102 / 27 files |

The denominator grew, not shrank; the additional three unanalyzed lines are
reported explicitly rather than counted as newly precise. The badge is 65.6%.
Scalar min/max callable casts work around a reproduced NumPy/mypy stub issue
without changing native calls. One code-specific override ignore records the
existing PyRex nested-transaction method's deliberate rejection of positional
options; it is not a precision exclusion. The pre-existing `SimpleKV.env` and
generic `LevelDBStore.db` diagnostics remain visible rather than being hidden
with fictional attributes or bundled runtime fixes.

Validation: 3,482 tests passed / 137 skipped with pyarrow available; the original
environment produced 3,374 passed / 244 skipped and its known missing-pyarrow
failure. Python 3.10 with NumPy 1.23.5 passed the static contracts and available
runtime contracts (4 passed / 24 optional-backend skips). Ruff, module-size,
documentation drift and API-index regeneration checks passed; executable docs
had 6 passes / 2 optional-dependency skips. A normalized AST comparison also
confirmed no algorithm/branch changes in the six edited storage/sampling files.

Next: remaining storage/native boundary and serializer schemas, snapshot
metadata schemas and framework-specific conversions, followed by the temporal
reader/selection/engine contracts in step 3. The current slice does not claim
to have completed these remaining parts of the proposal.

## Implementation progress: temporal and stored-rule contracts

The next slice completes typed signatures and checked bodies for the temporal
reader, selection, commit writer, indexes, orphan recovery and entity-write
helpers, plus temporal/history/epistemic/rule services and stored-rule execution.
Shared internal aliases and TypedDicts describe actual cache entries, validated
version fields, index state, rule facts, join rows, outputs and support references.
Literal node/edge/claim kinds select precise version return types. Hydration,
commit and index-writing callbacks preserve the existing instrumentation seams.

Fifteen modules now enforce untyped-definition, omitted-generic and Any-return
ratchets. Raw JSON decoding, extensible manifest values and existing forwarded
keyword filters retain narrow dynamic boundaries. No runtime dependency or
coverage exclusion was added. In particular, index-state format typing retains
the existing acceptance of JSON `5.0`, and claim corrections retain the distinction
between omitted values and explicit None. New tests cover literal-kind return
types, typed-kind reads, cache payload detachment and correction sentinel behavior.

| Measure | Before temporal slice | After temporal slice |
| --- | ---: | ---: |
| Precise lines | 21,086 | 22,899 |
| Imprecise lines | 1,427 | 1,349 |
| Any lines | 9,639 | 8,029 |
| Unanalyzed lines | 382 | 398 |
| Eligible denominator | 32,152 | 32,277 |
| Precision | 65.5822% | 70.9453% |
| Diagnostics | 102 / 27 files | 72 / 20 files |

The denominator again increased; unanalyzed lines are disclosed, not counted
as newly precise. Validation: **3,488 passed / 137 skipped** with pyarrow;
Python 3.10 / NumPy 1.23.5 consumer contracts passed (4 passed / 30 optional-backend
skips). All 15 ratcheted modules pass their stricter check, while the whole-library
diagnostics above remain visible. Ruff, size gate, API-index freshness and docs
drift checks pass; executable docs have 6 passes / 2 optional-dependency skips.
A normalized AST comparison confirmed unchanged algorithms/branches across all
15 edited implementation files, excluding type declarations, casts and local
variable renames.

The next large cluster is Cypher runtime/write/parser typing. Read-view forwarding,
snapshot metadata, optional framework conversions and remaining native boundary
schemas are still follow-up opportunities; this is not a claim of complete
whole-library typing.
