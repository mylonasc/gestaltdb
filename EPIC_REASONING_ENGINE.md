# Reasoning engine roadmap — reviewed against `onto-dev`

Review date: 2026-10-20. Baseline: `onto-dev` / `origin/onto-dev` at
`c71f0433fc40251b7dcec08b60370e80b40d36d8` (completed #131).
Planning/integration branch: **`feature/onto-dev-epic-2`**, inherited from that
baseline, not `main`. This is a plan, not an implementation or release claim.
Parent roadmap: [#156](https://github.com/mylonasc/gestaltdb/issues/156).

## Sources and tracking authority

- Original assessment, preserved unchanged: [source-assessment.md](planning/reasoning-engine/source-assessment.md).
- Original 136-row workbook, preserved unchanged: [gestaltdb_reasoning_feature_tracker.xlsx](gestaltdb_reasoning_feature_tracker.xlsx).
- Reviewed work packages, acceptance criteria, dependencies and GR-ID coverage:
  [roadmap.json](planning/reasoning-engine/roadmap.json).
- GitHub issue index: [issues.json](planning/reasoning-engine/issues.json).
- Validate source hashes, complete GR coverage and dependency DAG:
  `uv run python planning/reasoning-engine/check_roadmap.py`.
- Previous delivery and its precise boundaries: [ONTO-EPIC.md](ONTO-EPIC.md).

This document and the new parent tracker carry the **whole** reviewed roadmap.
The workbook is historical input, not the current delivery dashboard: its audit
statuses describe public `main`, not this baseline. Preserve original GR-001–136
identities; do not treat its 81 “Required” entries as 81 missing features here.
Update the parent, relevant phase/child issue, JSON register and this document
when scope, dependencies, decisions or acceptance evidence change.

## Review verdict

**Adopt the relational execution direction, not the proposed rewrite order.**
Compact terms, reusable plans, selected indexes, columnar operations, explicit
delta lifecycles and support-aware maintenance are well-motivated architecture
for the larger roadmap. But neither a new C++/Rust engine nor RocksDB-only
execution is a prerequisite for useful reasoning features.

### Corrections to the input assessment

| Assessment assumption | Actual `onto-dev` baseline and consequence |
| --- | --- |
| Only predicate-list scans with post-match delta filtering | True of legacy `GraphDB.run_rules` / `maintain_truth`, not `reasoning/engine.py`. The standalone kernel already has predicate/subject/object indexes, exact membership, per-body-position delta pivots, smallest-bound-bucket ordering and predicate-triggered dispatch. Improve and measure these, do not reimplement them as missing functionality. |
| Ontology compiler and elementary axioms are future M3 | `ontology/model.py` and `compiler.py` already implement immutable revisions, exact axiom origins, named subclass/subproperty/equivalence, domain/range, inverse, symmetric and transitive properties. Extend the compiler under a new profile; preserve `gestalt-core-v1`. |
| Reference/conformance/determinism tests and reasoning benchmarks are missing | `tests/test_ontology_conformance.py`, reasoning tests, `benchmarks/ontology.py` and four committed matrices exist. Extend their independent oracles and workload sizes, rather than discard the baseline. |
| RDF import is absent | The optional, bounded offline RDF adapter exists. Expand only alongside supported semantics; never silently accept an incomplete profile. |
| All provenance has multiple supports | Legacy rule provenance stores multiple justifications. Standalone proofs deliberately retain a bounded single witness. Point publication recomputes base closure and is not an all-support TMS. These contracts are not interchangeable. |
| Atomic temporal publication implies general derived generations | Existing `reconcile_point` uses the writer guard and a visibility-marked commit for exactly `[t,t+1us)` in a separate world. It is not a general generation store, broad interval publisher or transparent nontransactional recovery protocol. |
| RocksDB should own the engine | LMDB, LevelDB and PyRex are supported storage choices. Keep the standalone core/compiler storage-independent; specialize extraction behind explicit capabilities. No second unsynchronized RocksDB handle. |
| Compact IDs solve long persistent keys | Snapshot-local integer interning helps joins but does not remove existing 511-byte canonical index-key constraints. Persistent dictionary/key-layout changes need independent format, migration and collision-safety design. |
| “Composite indexes” implies arbitrary tuple indexes | Existing graph indexes combine label/type with a single property. General relation multi-column indexes are separate work. |

Evidence: `reasoning/{model,engine,protocols,proofs}.py`,
`ontology/{model,compiler}.py`, `reasoning/adapters/{claims,graph,publication}.py`,
`graphdb.py:run_rules/maintain_truth`, ontology tests and `docs/ontology*.rst`.
The existing dense N=128 benchmark reports pipeline medians of 537.02ms native,
622.88ms native-proofs and 54.79ms Clingo; these are small synthetic **baseline**
measurements, not evidence for million-fact performance or equivalent provenance.
Extraction dominates the duplicate-source claims workload. Clingo remains a
benchmark comparator, not an approved production dependency.

## Architecture and user-facing principles

1. Keep the current simple `Fact` / `Atom` / `Program` and ontology workflow working.
   Introduce a versioned relational IR underneath or through explicit new types;
   no surprising string-to-variable conversion or silent semantic upgrades.
2. Public Python semantics/compiler -> inward-only plans/operators -> interchangeable
   execution implementations. Storage, temporal normalization, clocks, I/O and
   publication remain in explicit adapters. Core interval values, if introduced,
   are storage-neutral; do not import database temporal records into the compiler.
3. Keep the standard-library evaluator as an available reference/fallback. Arrow,
   Polars, RDF and any bespoke native runtime stay optional; unsupported backend
   features fail clearly before work/publication, never quietly change semantics.
4. Separate logical fact IDs, exact immutable evidence/version IDs, dictionary
   generations, materialization IDs, context identity and execution-plan identity.
   Compact IDs are only meaningful with their dictionary; external IDs stay stable.
5. A run pins the fact horizon **and** ruleset/ontology, mapping, semantics profile,
   source policy and dictionary/schema versions. Cached plans/results authenticate
   all relevant identities; contexts/worlds never join implicitly.
6. Explicit denial, absence under a closed-world declaration, validation violations
   and OWL inconsistency are distinct. Default remains open-world and non-explosive.
   Classical checking is an explicit profile with declared unsupported behavior,
   not a “NOT” switch or an implied complete OWL reasoner.
7. Bounded single-witness proofs are not maintenance indexes. Maintenance needs a
   bounded support representation and recursive deletion algorithm, or falls back
   visibly to complete rematerialization. Never enumerate all recursive paths.
8. Prefer reusable architecture over local optimizations when it unlocks substantial
   roadmap coverage. Throw-away implementations may establish executable semantics;
   correctness and roadmap progress need not wait for first-pass maximum performance.

## Ordered parent epics

Five phase epics sit beneath one roadmap tracker. Arrows below mean phase exit
prerequisite, not that every child in the later phase depends on every earlier
optimization. Child dependencies in the register are the actual implementation
DAG; design/fixtures may proceed early after the required interfaces freeze.

| Phase | Priority / user value | Scope / original milestones | Exit gate |
| --- | --- | --- | --- |
| [RE-E1 #157](https://github.com/mylonasc/gestaltdb/issues/157) | P0: reusable, inspectable and faster execution | RE-01–06: baseline/IR, compact relations, Polars prototype, SCC/plans, batched extraction and measured backend choice; revises M0–M2 | Reference parity, compatibility, measured end-to-end time/RSS/footprint and limits; no native rewrite assumed |
| [RE-E2 #158](https://github.com/mylonasc/gestaltdb/issues/158) | P1: practical rules, ontology checks and selective answers | RE-07–13: literals/n-ary rules, chains/intersections, explicit negatives/violations, scoped stratified negation, equality, demand queries and explicit graph/sampling integration; M3 plus useful M6 moved forward | Runnable compliance/type-resolution examples, declared profile coverage, intuitive APIs, no semantic leakage |
| [RE-E3 #159](https://github.com/mylonasc/gestaltdb/issues/159) | P1: maintain live derived knowledge safely | RE-14–18: recoverable generations, support/dependency indexes, insertion/deletion, rule-catalog pinning and opt-in legacy migration; M4 | Old-or-new visibility, crash/concurrency tests, randomized update parity, recursive cycle deletion and documented rebuild crossover |
| [RE-E4 #160](https://github.com/mylonasc/gestaltdb/issues/160) | P1: correct derived history, not sampled points | RE-19–22: interval algebra, complete range closure, temporal updates/publication and audit examples; M5 | No gap filling; exact time-local supports, point/range agreement, reproducible system-time history and bounded interval growth |
| [RE-E5 #161](https://github.com/mylonasc/gestaltdb/issues/161) | P2/P3 gated extensions, not a full-OWL promise | RE-23–27: profile expansion, bounded existential/EL experiments, context/soft logic design, advanced operators and provenance/checkpoint extensions; remaining M6/M7 and inventory | Each extension gets a separately approved profile/termination/performance contract; deferred outcomes remain explicitly tracked |

```mermaid
flowchart LR
  B[onto-dev c71f043 / completed ONTO v1] --> E1[RE-E1 Execution foundation]
  E1 --> E2[RE-E2 Useful reasoning and queries]
  E2 --> E3[RE-E3 Durable incremental maintenance]
  E3 --> E4[RE-E4 Complete temporal closure]
  E4 --> E5[RE-E5 Gated advanced extensions]
```

This ordering does **not** hold point-only useful APIs hostage to complete temporal
closure. Equality starts snapshot-local with split/rebuild on evidence removal;
its temporal scope must be revisited in RE-E4. Negation gets explicit closed-world
scope before anti-joins become user-facing. Read-only demand evaluation can ship
before incremental persistence. Existing #154 recovery work is addressed explicitly
in RE-14/21, not silently declared fixed by planning.

### Feature issue index

Prerequisites below are RE feature IDs, not GitHub issue numbers. All items are
planned/open; original GR coverage and detailed acceptance live in the register
and corresponding child issues.

| Feature | Issue | Deliverable | Prerequisites |
| --- | --- | --- | --- |
| RE-01 | [#162](https://github.com/mylonasc/gestaltdb/issues/162) | Contracts, baseline reconciliation and budgets | None |
| RE-02 | [#163](https://github.com/mylonasc/gestaltdb/issues/163) | Compact dictionaries, batches and Arrow boundaries | 01 |
| RE-03 | [#164](https://github.com/mylonasc/gestaltdb/issues/164) | Polars join/delta prototype | 02 |
| RE-04 | [#165](https://github.com/mylonasc/gestaltdb/issues/165) | SCC plans, selected indexes and telemetry | 01, 02 |
| RE-05 | [#166](https://github.com/mylonasc/gestaltdb/issues/166) | Pinned batched extraction | 02 |
| RE-06 | [#167](https://github.com/mylonasc/gestaltdb/issues/167) | Executor decision and production hardening | 03, 04, 05 |
| RE-07 | [#168](https://github.com/mylonasc/gestaltdb/issues/168) | Literals, n-ary rules, comparisons and aggregates | 01, 04 |
| RE-08 | [#169](https://github.com/mylonasc/gestaltdb/issues/169) | Chains, class expressions and schema closure | 04 |
| RE-09 | [#170](https://github.com/mylonasc/gestaltdb/issues/170) | Explicit negatives and validation reports | 07, 08 |
| RE-10 | [#171](https://github.com/mylonasc/gestaltdb/issues/171) | Scoped stratified negation | 07, 09 |
| RE-11 | [#172](https://github.com/mylonasc/gestaltdb/issues/172) | Equality, inequality and functionality | 07, 09 |
| RE-12 | [#173](https://github.com/mylonasc/gestaltdb/issues/173) | Demand queries and safe caching | 04, 08 |
| RE-13 | [#174](https://github.com/mylonasc/gestaltdb/issues/174) | Explicit graph/query/sampler integration | 08, 12 |
| RE-14 | [#175](https://github.com/mylonasc/gestaltdb/issues/175) | Recoverable generations and compare-and-publish | 01, 05, 06 |
| RE-15 | [#176](https://github.com/mylonasc/gestaltdb/issues/176) | Maintenance supports, dependencies and inserts | 04, 14 |
| RE-16 | [#177](https://github.com/mylonasc/gestaltdb/issues/177) | Recursive deletion and rule invalidation | 15, 10, 11 |
| RE-17 | [#178](https://github.com/mylonasc/gestaltdb/issues/178) | Pinned catalog and opt-in legacy migration | 14, 15, 16 |
| RE-18 | [#179](https://github.com/mylonasc/gestaltdb/issues/179) | Incremental safety/performance gates | 16, 17 |
| RE-19 | [#180](https://github.com/mylonasc/gestaltdb/issues/180) | Interval-set and support-segment algebra | 01, 15 |
| RE-20 | [#181](https://github.com/mylonasc/gestaltdb/issues/181) | Complete range closure and proofs | 19, 08, 11 |
| RE-21 | [#182](https://github.com/mylonasc/gestaltdb/issues/182) | Temporal deltas and interval publication | 16, 20, 14 |
| RE-22 | [#183](https://github.com/mylonasc/gestaltdb/issues/183) | Temporal conformance and audit workflows | 20, 21 |
| RE-23 | [#184](https://github.com/mylonasc/gestaltdb/issues/184) | Gated profile/validation expansion | 09, 11, 22 |
| RE-24 | [#185](https://github.com/mylonasc/gestaltdb/issues/185) | Bounded existential / EL feasibility | 23 |
| RE-25 | [#186](https://github.com/mylonasc/gestaltdb/issues/186) | Context, modal and confidence semantics | 22 |
| RE-26 | [#187](https://github.com/mylonasc/gestaltdb/issues/187) | Advanced joins, differential updates and spill | 06, 18, 22 |
| RE-27 | [#188](https://github.com/mylonasc/gestaltdb/issues/188) | Minimal/ranked proofs and checkpoints | 18, 22 |

## Polars / Arrow decision and fundamental operations

RE-02/03 establish compact integer columns and a small relation-operator contract.
Prototype selection, projection, equijoins, semi/anti-join, union, distinct and
stable-minus-new operations in Polars; use designated delta inputs each round.
Keep plans reusable where supported and measure per-round collection/planning.
Represent evidence/support references in side tables, not one Python object per
hot-path row. Preserve repeated-variable checks, exact set semantics and body
occurrence order for provenance when joins are reordered.

Arrow is the exchange format, not an assumption that every algorithm must be an
Arrow table. Document schema, fixed-width IDs, dictionary reuse, null rejection,
literal encodings, ownership, chunk boundaries and release/cancellation lifetimes.
Avoid `.to_dicts()` / Python row callbacks in the optimized path. No universal
zero-copy claim: decoding KV values, recoding dictionaries, sorting/rechunking and
some exports necessarily allocate. Do not claim Polars streaming provides a hard
RSS bound for arbitrary recursive joins.

Compare the current indexed Python kernel, compact Python/NumPy where useful,
Polars and the existing Clingo benchmark on identical supported fragments. A
bespoke C++/Rust kernel is a **conditional RE-06 outcome**, justified by measured
conversion/planning, memory, proof or recursion limits that existing components
cannot meet. A useful reference-backed feature can ship before that optimization.
PyRex native scans require checking the actual external binding/API first; this
repository cannot promise an unimplemented upstream native entry point.

## Acceptance and performance policy

- Every slice has focused semantic tests, limits/failure tests, docs and at least
  one runnable user scenario. Every phase can produce an independently useful cut.
- Differential tests use the independent small grounding oracle, baseline kernel
  and legacy evaluator only on mutually compatible contracts. W3C/OWL-RL comparisons
  project the declared fragment; no full standards badge from selected examples.
- Exercise duplicate evidence, repeated atoms/variables, constants, disconnected
  and skewed joins, diamonds, recursion, long IRIs, equality splits, source/world
  isolation, support loss/resurrection, cancellation and incompatible snapshots.
- Record cold startup, compile, extraction/authentication, conversion, join/fixpoint,
  proofs/supports, publication, decode, total pipeline, P50/P95 updates, peak RSS,
  bytes copied/spilled, thread count, dependency/wheel footprint and actual work.
  Compare closure-only, witness and maintenance modes separately.
- RE-01 freezes reproducible workload-specific time/RSS budgets before selecting
  the executor; do not invent speedups now. Small interactive workloads must not
  inherit avoidable heavy startup. Benchmark sparse and dense outputs separately;
  quadratic closure is not a constant-memory workload.
- Enforce budgets before materializing unbounded join intermediates. Counters,
  allocator estimates, cooperative deadlines and process-level RSS limits are
  different guarantees. If an executor cannot interrupt a batch safely, document
  that limitation or use worker isolation; no successful partial closure/publication.
- Full suite, Python 3.10–3.13, optional-extra/install isolation and capability-specific
  backend CI remain phase gates. No requirement that unavailable native features
  be emulated with weaker snapshot or atomicity guarantees.

## Worktree, branch and issue agreement

- Keep this branch as the designated second-slice integration branch. Each RE-NN
  feature uses its own persistent worktree and `feature/re-NN-<slug>` branch based
  on its accepted prerequisites here. PRs target `feature/onto-dev-epic-2`, not
  `main`; changing that destination requires an explicit roadmap update.
- Create feature worktrees when implementation starts, not dozens of stale empty
  worktrees during planning. Stacking is only for true dependencies. Preserve all
  unrelated example/feature worktrees; no forced reset or shared force-push.
- Commit and push coherent tested milestones often. Link worktree/branch/PR and
  validation evidence in each child. Keep native GitHub parent/sub-issue and
  blocked-by relationships synchronized with textual dependency lists.
- Phase completion requires evidence for all acceptance items, or an explicit
  parent-approved scope/defer decision with a linked issue. No roadmap item
  disappears merely because a prototype or profile is inconvenient.
- Related existing issues remain authoritative for their broader scopes: #125
  (negation), #126 (unified explanations), #127 (hierarchical query/sampling),
  #124 (weighted semantics), #128 (neuro-symbolic composition), #154 (recovery and
  intervals). Link the new work rather than close or overwrite these as done.
- No implementation, merge into `onto-dev`/`main`, tag or release is authorized by
  this planning deliverable alone.

## Review validation

Focused existing ontology/reasoning suite on this checkout: **181 passed, 72
skipped** without optional extras. Repeated with LMDB, LevelDB and RDFLib, plus
graph interface/Cypher/sampling regression suites: **492 passed, 128 skipped**.
Documentation drift check passed; modular examples with LevelDB/Arrow/Polars:
**7 passed, 1 skipped** (PyRex unavailable). The roadmap validator confirms all
136 IDs, original source hashes and the acyclic issue graph. Native GitHub
parent/sub-issue and blocked-by relationships were read back and verified.

These checks confirm existing baseline behavior and planning consistency, not
new performance or all-backend guarantees. Previous baseline integration evidence
is recorded in `ONTO-EPIC.md`. No new executor was implemented or benchmarked
during this review.
