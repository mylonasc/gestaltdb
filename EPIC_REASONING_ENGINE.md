# Reasoning engine: delivered scope and remaining roadmap

**Reviewed at:** 2026-11-01T13:00:35Z (UTC).

**Reviewed source:** [`7f288085fc01ecd2fac66def9d3c29981054af26`](https://github.com/mylonasc/gestaltdb/commit/7f288085fc01ecd2fac66def9d3c29981054af26), `origin/main` after roadmap/workbook sanitation #452. The #194 evidence below is explicitly pending PR #450, not part of that main revision or the published baseline.

**Published baseline:** [v0.16.0](https://github.com/mylonasc/gestaltdb/releases/tag/v0.16.0), source `0ae4d4bb1624605efc0351f9a029f396baf291dc`, published 2026-10-29T00:03:19Z.

**Authority:** this file is the reasoning roadmap/status source of truth. GitHub records work and dependencies; code/tests establish behavior. The Excel workbook is a generated, review-dated view of section 7, not a second editable delivery ledger.

## 1. Current disposition

- **Delivered:** parent [#156](https://github.com/mylonasc/gestaltdb/issues/156), RE-01–RE-27 and all five phase epics are closed. Delivery includes explicitly bounded implementations, experiments and approved defer decisions; it does **not** mean every aspirational native/OWL acceptance criterion is implemented.
- **Delivered composability:** [#391](https://github.com/mylonasc/gestaltdb/issues/391) and #392–#396 are closed: typed first-witness explanations, selected temporal negation/aggregates, structural validation, explicit inferred Cypher and typed graph mappings. See sections 2–3 for fragment and release boundaries.
- **Pending native follow-up:** [#194](https://github.com/mylonasc/gestaltdb/issues/194) has actual-adapter measurements and an explicit **defer automatic adoption** decision in open PR #450, not yet on reviewed main. The locator implementation is already on main; #451 separately tracks dense-history construction/authentication and locator read-amplification debt. Integration/review, not an absent native scan API, remains for #194.
- **Proposed, not delivered:** weighted/probabilistic semantics #124 and feedback composition #128. Architecture #388 and typing #410 are partially delivered independent programs. Tutorial #273 and CDM #409 are separate documentation/application work.
- **Activity:** PR #450 is the open native-locator benchmark/documentation follow-up; sanitation #452 is merged. PRs #441/#443/#444/#445 are dependency updates. An open issue or an old started-work comment is not evidence that implementation is currently active.

No merge, tag, release, issue closure or new feature commitment is authorized by this document. Current changes start from accepted `main` unless explicitly directed otherwise. `onto-dev` and `feature/onto-dev-epic-2` below are **historical integration targets**, not current PR instructions.

### Completed phases

| Phase | Issues / scope | Completion disposition |
| --- | --- | --- |
| RE-E1 #157 | RE-01–06: contracts, compact batches, Polars, plans, extraction, executor choice | Closed 2026-10-25 after evidence reconciliation; implementation exit was declared 2026-10-21 at `79e4369`. Indexed Python retained; no bespoke kernel required. |
| RE-E2 #158 | RE-07–13: typed rules, ontology subset, validation, negation, equality, demand and integration | Closed 2026-10-23. Scoped semantics delivered; signed-denial premises and evaluated TBox reuse were explicitly deferred, not silently implemented. |
| RE-E3 #159 | RE-14–18: generations, supports, insertion/deletion, catalogs and gates | Closed 2026-10-25 after #370 crash/concurrency evidence and explicit #381 disposition. |
| RE-E4 #160 | RE-19–22: interval algebra, range closure, updates/publication and conformance | Closed 2026-10-25 after evidence reconciliation; gap-preserving bounded range workflows delivered. |
| RE-E5 #161 | RE-23–27: profile gates, experimental EL, context/scale decisions and explanations/checkpoints | Closed 2026-10-25. Experimental/design-gated/deferred outcomes are the deliverables, not full OWL, soft logic or spill. |

Review #200 and dispositions #371/#373/#374 are closed. Parent #156 closed 2026-10-31; its native child graph also includes release-readiness #385. #194 remains independent of completed phase exits.

## 2. Supported surfaces and composition boundaries

The core/compiler remain storage-independent. Database extraction, source authentication, mutable snapshots and publication are explicit adapters; no second unsynchronized backend handle or silent capability fallback is assumed.

| Surface | Delivered behavior | Boundary that callers must retain |
| --- | --- | --- |
| Binary Datalog | `DatalogEngine`: indexed predicate/subject/object candidates, repeated-variable unification, exact set membership, designated delta pivots and predicate-triggered dispatch | Python execution; finite positive binary entity facts. Not a native vectorized executor. |
| Prepared binary plans | Positive SCCs, reusable compilation identities, dynamic smallest-bound-bucket ordering, `explain_plan` and optional telemetry | Heuristic, not general cost-based optimization; component-round limits differ from the binary default's global rounds. |
| Typed Datalog | `GenericEngine`: n-ary facts, strict literal kinds/comparisons, multi-head normalization, explicit predicate-domain expansion, stratified absence and nonrecursive aggregates | Positive and stratified entry points are explicit; no recursive/lattice aggregates, arbitrary structured terms, implicit predicate-variable execution or signed-denial premises. |
| Compact/Arrow/Polars | Snapshot-local dictionaries, immutable compact relation buffers, optional Arrow exchange and explicit binary closure-only Polars prototype | Dictionary/context must match exactly. Imports may copy; no universal zero-copy/RSS guarantee. Polars has no proof/maintenance/publication parity and is not selected automatically. |
| Ontology compiler/RDF | Named subclass/subproperty/equivalence, domain/range, inverse/symmetry/transitivity, two-link chains, directional class conjunction/union and scoped reflexivity; selected offline RDF normalization | Named subsets, not complete RDFS/OWL RL/DL. `CompiledSchemaCache` memoizes compilation with LRU eviction, not evaluated schema/TBox closure. |
| Equality and validation | Snapshot-local representatives/aliases, explicit inequality conflicts, selected functional equality links; ground-denial/disjointness/functional conflict reports | Never physically merge entities or explode under contradiction. Denial validation is not a negative premise. Equality changes require explicit scoped rebuild. |
| Structural validation | Required fields, min/max fact cardinality, literal-kind checks and exclusive/exactly-one groups on explicitly complete classes | Read-only logical-fact evidence, not full SHACL, datatype facets or exact claim-version provenance. Input is buffered; report caps are not memory caps. |
| Typed proofs | Opt-in generation-bound first witnesses with ordered premises, grounded comparisons, scoped successful absence and aggregate evidence; truncation is explicit | Direct `GenericEngine` proof collection is available. Typed application executor/session dispatch remains closure-only. First witnesses are not all-support maintenance or independent certification. Reevaluate after support/program changes. |
| Stored claim rules | `graph.rules.create_rule/run/maintain/explain`, `not_when`/`closed_world`, versioned justifications and historical explanations | Entity-claim, point-selected rule service with recomputation-based reconciliation; not automatically the standalone typed DRed API. Partial negative-interval overlap conservatively blocks a derivation. |
| Support maintenance | Exact bounded dependency indexes, changed-predicate insertion propagation and DRed-style reverse overdeletion/rederivation | `maintenance.py` excludes negation, aggregates and closed scopes. Deletion evaluates survivors and rediscovers supports; not exclusively local work. Changed rules/equality demand explicit rebuild, not stale-index reuse. |
| Sessions/admission | Exact assertion-version multiplicities, corrections/retractions, bounded commit-delta advance, durable atomic JSON replacement, locking/CAS and separately authenticated resume | File checksums alone do not authenticate source facts or prove closure. Positive typed/session and binary/current-source profiles are distinct. |
| Temporal closure | Disjoint half-open interval sets, event-scheduled change segments, per-segment evaluation/evidence, selected negation/nonrecursive aggregates, equality scopes, memoized updates and independent point-conformance gate | No native interval join or arbitrary temporal calculus. Changed/truncated generations rebuild explicitly; segment reuse is not signed differential execution. Proof truncation must not truncate answers. |
| Publication | Explicit staged point generations and bound range extraction/computation, guarded freshness checks, target-history CAS and visibility-marked interval commits | Raw caller-supplied staging is not authenticated admission. Persistence guarantees are backend/capability-qualified; no transparent global materialization store is implied. |
| Demand/query/sampler | Magic-set/demand specialization; temporal/current authenticated frontiers; typed graph projection; explicit inferred artifacts; generation-pinned asserted-only/asserted-plus-inferred Cypher | Inferred Cypher is current binary entity, read-only single-node/fixed-one-hop projection. No ordinary-query rewrite, physical edge multiplicity/properties or unrestricted inferred paths. Selected adapters do not universally compose all typed/range features. |
| Native extraction | Explicit plain-PyRex full-record and historical index-locator sources; native world/predicate/subject prefixes, exact evidence and canonical correction/retraction conformance | Full catalog authentication/canonical reads remain. Object/agent/source filters are post-selection. Raw transactional scans and verifiable mutable snapshots do not automatically admit transactional native claims. No speed/adoption claim. |

Runtime defaults remain indexed Python (`reasoning/executors.py`). Confidence stays metadata; `CalculusRegistry` has no approved calculus by default. `SpillBudget`/`WorkerPolicy` are admission policies, not a spill engine or parallel fixpoint implementation. `checkpoint_closure` binds program/fact/support/index consistency, not arbitrary caller-data authentication or resumable native mid-join execution.

## 3. What was implemented when

Dates below are **GitHub merge timestamps in UTC**, not claims about when coding began. Commits were verified as ancestors of reviewed main. The original work integrated into `feature/onto-dev-epic-2`; first main integration was PR #307 on **2026-10-23T10:01:10Z**, commit `1d9caad`, followed by v0.13.0rc1. Later follow-through, service changes and composability reached main in subsequent integrations. All RE-01–RE-27 deliveries are included in v0.16.0; the post-tag rows below are not.

### RE delivery ledger (replaces “implementation started” diaries)

| Feature | Merged UTC | PR / integration commit | Delivered outcome |
| --- | --- | --- | --- |
| RE-01 | 2026-10-20T22:10:00Z | #189 / `1c69373` | Relational contracts, baseline audit, budgets and structural inspection; not executable general n-ary IR. |
| RE-02 | 2026-10-20T22:22:11Z | #190 / `865f3b1` | Context-bound compact dictionaries/batches and Arrow copy/lifetime contract. |
| RE-03 | 2026-10-20T22:42:43Z | #191 / `b320c33` | Optional Polars binary delta-fixpoint prototype and measured tradeoffs. |
| RE-04 | 2026-10-20T23:15:47Z | #192 / `07588ae` | Reusable positive SCC plans, indexed delta sites and opt-in telemetry. |
| RE-05 | 2026-10-21T00:40:47Z | #193 / `0b9f32d` | Pinned batched claim extraction and capability-gated raw native scans. |
| RE-06 | 2026-10-21T00:52:37Z | #195 / `79e4369` | Explicit executor decision, cooperative cancellation and no-bespoke-kernel defer. |
| RE-07 | 2026-10-21T01:23:20Z | #196 / `15c2d78` | Strict literals, n-ary rules, comparisons, normalization and restricted aggregates. |
| RE-08 | 2026-10-21T05:46:32Z | #197 / `3919287` | Two-link property chains, directional class forms and compilation cache/offline normalization. |
| RE-09 | 2026-10-21T05:55:33Z | #198 / `684d7a8` | Ground-denial/conflict validation and static class-conflict checks; not signed premises. |
| RE-10 | 2026-10-21T06:10:37Z | #199 / `f035847` | Explicit closed predicates and stratified typed absence. |
| RE-11 | 2026-10-21T06:30:49Z | #201 / `bf43570` | Snapshot-local equality/inequality, explicit functional links and safe rebuild boundaries. |
| RE-12 | 2026-10-21T06:47:24Z | #202 / `48a9d12` | Goal-directed demand specialization and identity-bound cache; no longer “in review”. |
| RE-13 | 2026-10-21T07:01:15Z | #203 / `8034231` | Explicit relation expansion/fanout and inferred seed handoff. |
| RE-14 | 2026-10-21T07:09:36Z | #204 / `ea38931` | Staged point generations, compare-and-publish and explicit orphan recovery. |
| RE-15 | 2026-10-21T07:18:15Z | #205 / `31f72d5` | Bounded supports/dependency indexes and insertion propagation. |
| RE-16 | 2026-10-22T00:23:45Z | #206 / `8b2dcce` | DRed-style support-safe deletion, rebuild fallback and randomized parity. |
| RE-17 | 2026-10-22T00:32:07Z | #218 / `cec7e0a` | Pinned indexed catalog, migration and explicit compatibility/rebind gates. |
| RE-18 | 2026-10-22T00:42:08Z | #219 / `f8cc14e` | Incremental/recovery workload gates and measured maintenance scenarios. |
| RE-19 | 2026-10-22T00:52:21Z | #220 / `ebd652d` | Storage-neutral interval-set and support-segment algebra. |
| RE-20 | 2026-10-22T01:01:23Z | #231 / `93ce554` | Change-segment full-range closure and scoped equality, initially positive. |
| RE-21 | 2026-10-22T01:20:45Z | #235 / `cfd35a7` | Segment-memoized updates, range fallback and managed interval publication. |
| RE-22 | 2026-10-22T01:30:07Z | #236 / `1eb4f6e` | Independent temporal conformance, cost evidence and historical workflows. |
| RE-23 | 2026-10-22T01:39:17Z | #237 / `0e0ec9a` | Named construct/profile admission matrix; no full-OWL claim. |
| RE-24 | 2026-10-22T01:52:01Z | #238 / `1c64d3a` | Bounded experimental EL completion, proof replay and finite-model oracle. |
| RE-25 | 2026-10-22T02:00:38Z | #239 / `3341b83` | Context/modal/confidence policy gates; no new combination/inheritance evaluator. |
| RE-26 | 2026-10-22T02:09:37Z | #240 / `59cc1a4` | Join/deletion measurements and explicit advanced-runtime/spill defer decisions. |
| RE-27 | 2026-10-22T02:20:21Z | #241 / `3b14d61` | Bounded one-step minimal/top-k witnesses, export and closure checkpoint validation. |

### Later implementation and release boundaries

| When | Evidence | Outcome / release disposition |
| --- | --- | --- |
| 2026-10-22–25 | #242, #313/#315, #349, #366/#367/#368 | Native evidence plumbing, ordinary grouped-read fixes, repeated 100K measurements, explicit admitted full-record source and raw capability re-audit. Earlier timeouts remain evidence, not successful timings. Full-record native measurements did not justify adoption. |
| 2026-10-22–25 | #248–#256; #363–#365 | Generation/admission identities, demand/frontier integration, exact evidence ledger, durable/authenticated sessions and inferred consumers; scoped follow-through issues closed. |
| 2026-10-25 | #371/#373/#374/#370/#381 | Original review findings fixed or explicitly disposed/deferred; crash/concurrency phase evidence reconciled. Closed dispositions are not promises that deferred capabilities exist. |
| 2026-10-25T23:34:33Z | #399 / `fc0ec33` (#394) | Initial closed-class structural validation, later included in v0.16.0. |
| 2026-10-25T23:34:50Z | #400 / `738a0c6` (#396) | Initial typed n-ary graph mapping; later collision/reference hardening #408, included in v0.16.0. |
| 2026-10-25T23:45:01Z | #402 / `cda8dae` (#392) | Initial typed first-witness tracking, included in v0.16.0. |
| 2026-10-25T23:51:52Z | #403 / `07affaa` (#393) | Selected temporal negation/nonrecursive aggregates, later answer-versus-proof truncation fixes, included in v0.16.0. |
| 2026-10-26T06:55:18Z | #369 / `49d4638` (#194) | Historical native claim-index locator admission merged; included in v0.16.0. Actual-adapter benchmark/decision is delivered separately in unmerged PR #450, not in reviewed main. |
| 2026-10-27T23:33:49Z | #426 / `47f0fde` (#395) | Generation-pinned read-only inferred Cypher node/one-hop projection, included in v0.16.0. |
| 2026-10-29T00:03:19Z | v0.16.0 / `0ae4d4b` | Published stable baseline; API migration uses `graph.temporal`, `graph.epistemic`, `graph.rules` and current services. |
| 2026-10-31T14:48:05Z | #447 / `ac838da` | **Post-v0.16.0:** PyRex >=0.7.1 dependency floor and point-read regression recovery evidence. |
| 2026-10-31T21:56:49Z | #448 / `2fdc160` | **Post-v0.16.0:** structural constraint identity/evidence, deterministic ordering, cancellation and acceptance hardening. |
| 2026-10-31T22:06:13Z | #440 / `7551425` | **Post-v0.16.0:** verifiable transactional PyRex mutable snapshots with runtime >=0.7.0. |
| 2026-10-31T22:06:34Z | #449 / `6e9793f` | **Post-v0.16.0:** typed proof comparison/absence/aggregate consistency and result-binding hardening. |

The package metadata still says 0.16.0 on reviewed main. That does not make post-tag code part of the published wheel. Validation of this checkout is not retrospective certification of that wheel's stronger guarantees.

## 4. Pending work and deliberate deferrals

| Issue | State at review | Remaining acceptance / dependency |
| --- | --- | --- |
| [#194](https://github.com/mylonasc/gestaltdb/issues/194) | Open; implemented source on main; measurement/decision in unmerged #450 | PR #450 preserves 18/18 exact fact/version-ID parity observations at 100K sparse history, including selective/nonselective reads and full authentication/materialization costs, versions and time/RSS. Decision: defer automatic native adoption. Complete integration/review; no rule-closure, repeated-performance or OS-cold claim. Do not substitute raw/assertion-only/full-record modes. Optional, not a #156 blocker. |
| [#451](https://github.com/mylonasc/gestaltdb/issues/451) | Open technical debt; not remediated | Resolve dense-history supersession/commit validation and native locator read amplification without weakening authentication. Dense 100K construction timed out at 900 s before extraction; sparse-history success is different evidence. Instrument/batch verified target resolution, bound redundant catalog/history work and remeasure isolated repeated time/RSS. Native adoption remains deferred. |
| [#124](https://github.com/mylonasc/gestaltdb/issues/124) | Open proposal; design-gated | Choose probabilistic, fuzzy **or** importance semantics; prove combination, retraction, convergence and provenance with independent tests and a named user scenario. No silent confidence multiplication. |
| [#128](https://github.com/mylonasc/gestaltdb/issues/128) | Open composition proposal | Model/task/version provenance, source/world/assertion policy, bounded iterations and leakage controls. Weighted feedback depends on #124; a small deterministic loop need not. Audit export #126 is delivered. |
| [#388](https://github.com/mylonasc/gestaltdb/issues/388) | Open, partially delivered | Finish concrete facade/consumer coupling: legacy adjacency/BFS, update composition, key/serializer helpers and query/viz/transaction/snapshot/lifecycle integration. Services already exist; require standalone boundary/compatibility tests, not a facade line-count goal. |
| [#410](https://github.com/mylonasc/gestaltdb/issues/410) | Open, partially delivered | Remaining diagnostics, read-view/facade and optional adapter contracts, normalized construction inputs and installed consumer checks. `py.typed` and scoped strict ratchets already ship. Old diagnostic totals are not current runtime bug counts. |
| [#273](https://github.com/mylonasc/gestaltdb/issues/273) | Open documentation track | Complete/reconcile tutorial coverage and publish from its separate branch; old gap lists need current fragments. Not a missing core engine. |
| [#409](https://github.com/mylonasc/gestaltdb/issues/409) | Open application proposal | CDM schema impact, collateral/lifecycle/temporal-ML scenarios with explicit expected results and data/version policies. No automatic CDM/Rune translation or financial-calculation semantics implied. |

Sampling #106/#118–#121 and security #88 remain separate. Importance sampling is not probabilistic entailment; memmap/tensor exports are not a reasoning spill engine.

### Native follow-up evidence and newcomer orientation (#194 / #451)

**Reviewed main versus pending evidence:** `7f28808` includes sanitation #452 and
the production locator API, but not PR #450's harness, reports or measured-decision
documentation. PR #450's measured source is `48b7d53`; its final evidence is
preserved at `f3782fb` (100K) and `9a47873` (10K). Those observations must not be
presented as benchmarks of `7f28808` or the published v0.16.0 wheel.

A reasoning engine needs the right base facts before applying rules. Corrections
and retractions can change which historical claim version is eligible, even for
one requested subject. Resolving and authenticating that input is separate from
rule joins/fixpoint evaluation. **Dense history** here means amendments throughout
the claim population, not densely connected graph edges or thousands of versions
per claim: 100,000 assertions would have 134,286 total versions in the dense
fixture; the sparse fixture has 100,343 because only its first 1,000 assertions
receive amendments. Optimizing this work helps knowledge ingestion and preparation
of time-specific reasoning inputs, not the expressiveness of the rule engine.
Confidence remains metadata and exact evidence/version semantics must not change.

In PR #450, sparse-history 100K completed 18/18 exact evidence parity observations
across three sources, three selections and valid times 30/50. Locator pipeline
time was approximately 2.5–2.8x ordinary in these single observations, with higher
whole-worker RSS. Dense 10K completed 36/36 first/primed parity observations, with
a documented launch-versus-later-worker harness provenance caveat. Dense 100K
**fixture construction** timed out at 900 seconds: no extraction, rule evaluation,
parity result or RSS measurement was obtained from that attempt. Successful
sparse-history reads do not remediate dense-history construction/read costs.

The measured decision is **defer automatic native adoption**, retain the ordinary
source default and native sources as explicit alternatives. #451 owns remediation:
repeated supersession-target searches, commit-envelope revalidation and broad
catalog/index/history work are the slices to instrument and reduce. Preserve
complete source/index authentication, correction/retraction winners, exact evidence,
pinned horizons and fail-closed corruption/concurrency gates. The issue includes
a fuller newcomer guide, proposed performance goals and remediation exit criteria;
those goals are not current guarantees. Source-scoped reuse is not permission for
an unauthenticated persistent cache. Measurements remain single observations with
uncontrolled OS cache/host contention, not repeated medians or OS-cold evidence.

### Deferred or experimental, not scheduled by closed RE issues

- Bespoke C++/Rust reasoning kernel, automatic Polars/native adoption, general cost-based/parallel joins, B/F/differential maintenance and out-of-core spill. RE-06/RE-26 delivered benchmark-gated decisions instead.
- Full OWL RL/DL, production EL classification, unrestricted existential witnesses, disjunctive heads, keys/value/self/universal/complement semantics, datatype facets and recursive/lattice aggregation/negation. Consult `reasoning/profiles.py`; experimental EL is separately admitted.
- Signed-denial rule premises, reusable evaluated TBox closure, general executable cross-context inheritance/temporal-modal closure and probabilistic propagation. Policy objects/approval registries are not implementations of these calculi.
- Globally minimal recursive proofs, arbitrary independent closure certification, hard RSS bounds and pausing/resuming arbitrary native joins.

Select a deferred capability only through a new bounded issue with semantic, termination, composition and validation gates. Some deferrals have closed disposition records rather than open implementation issues; they are not implicitly “in implementation”.

## 5. Completed feature issue index and original dependencies

**All 27 issues below are closed.** Dependencies describe original delivery order, not current blockers. Section 3 records actual dates/outcomes; section 7 records whether each broader GR acceptance criterion is complete, partial, experimental or deferred.

| Feature | Issue | Delivered scope / disposition | Prerequisites |
| --- | --- | --- | --- |
| RE-01 | [#162](https://github.com/mylonasc/gestaltdb/issues/162) | Contracts, baseline reconciliation and budgets | None |
| RE-02 | [#163](https://github.com/mylonasc/gestaltdb/issues/163) | Compact dictionaries, batches and Arrow boundaries | 01 |
| RE-03 | [#164](https://github.com/mylonasc/gestaltdb/issues/164) | Experimental Polars join/delta prototype | 02 |
| RE-04 | [#165](https://github.com/mylonasc/gestaltdb/issues/165) | Binary SCC plans, selected indexes and telemetry | 01, 02 |
| RE-05 | [#166](https://github.com/mylonasc/gestaltdb/issues/166) | Pinned batched extraction and capability gates | 02 |
| RE-06 | [#167](https://github.com/mylonasc/gestaltdb/issues/167) | Executor decision; bespoke kernel explicitly deferred | 03, 04, 05 |
| RE-07 | [#168](https://github.com/mylonasc/gestaltdb/issues/168) | Literals, n-ary rules, comparisons and restricted aggregates | 01, 04 |
| RE-08 | [#169](https://github.com/mylonasc/gestaltdb/issues/169) | Chains, directional class forms and compilation memoization | 04 |
| RE-09 | [#170](https://github.com/mylonasc/gestaltdb/issues/170) | Ground-denial/conflict validation; signed premises deferred | 07, 08 |
| RE-10 | [#171](https://github.com/mylonasc/gestaltdb/issues/171) | Scoped stratified absence | 07, 09 |
| RE-11 | [#172](https://github.com/mylonasc/gestaltdb/issues/172) | Snapshot-local equality, inequality and functionality subset | 07, 09 |
| RE-12 | [#173](https://github.com/mylonasc/gestaltdb/issues/173) | Demand queries and identity-bound caching | 04, 08 |
| RE-13 | [#174](https://github.com/mylonasc/gestaltdb/issues/174) | Explicit graph/query/sampler handoff | 08, 12 |
| RE-14 | [#175](https://github.com/mylonasc/gestaltdb/issues/175) | Recoverable point generations and compare-and-publish | 01, 05, 06 |
| RE-15 | [#176](https://github.com/mylonasc/gestaltdb/issues/176) | Bounded supports/dependencies and insertions | 04, 14 |
| RE-16 | [#177](https://github.com/mylonasc/gestaltdb/issues/177) | DRed-style deletion and explicit rule-change rebuild | 15, 10, 11 |
| RE-17 | [#178](https://github.com/mylonasc/gestaltdb/issues/178) | Pinned catalog and compatibility gates | 14, 15, 16 |
| RE-18 | [#179](https://github.com/mylonasc/gestaltdb/issues/179) | Incremental correctness/recovery workload gates | 16, 17 |
| RE-19 | [#180](https://github.com/mylonasc/gestaltdb/issues/180) | Interval-set and support-segment algebra | 01, 15 |
| RE-20 | [#181](https://github.com/mylonasc/gestaltdb/issues/181) | Change-segment range closure and scoped evidence | 19, 08, 11 |
| RE-21 | [#182](https://github.com/mylonasc/gestaltdb/issues/182) | Segment-memoized updates and interval publication | 16, 20, 14 |
| RE-22 | [#183](https://github.com/mylonasc/gestaltdb/issues/183) | Temporal conformance and audit scenarios | 20, 21 |
| RE-23 | [#184](https://github.com/mylonasc/gestaltdb/issues/184) | Gated named-profile matrix, not complete OWL RL | 09, 11, 22 |
| RE-24 | [#185](https://github.com/mylonasc/gestaltdb/issues/185) | Experimental bounded EL feasibility | 23 |
| RE-25 | [#186](https://github.com/mylonasc/gestaltdb/issues/186) | Context/modal/confidence design gates only | 22 |
| RE-26 | [#187](https://github.com/mylonasc/gestaltdb/issues/187) | Measured advanced-runtime/spill deferrals | 06, 18, 22 |
| RE-27 | [#188](https://github.com/mylonasc/gestaltdb/issues/188) | Bounded ranked witnesses and closure checkpoint contracts | 18, 22 |

## 6. Evidence catalogue for the live register

Paths are repository-relative. Keys below expand into code/test evidence in Excel. A passing test establishes its declared fragment, not universal performance/standards certification.

| Key | Implementation / decision | Tests / measurements |
| --- | --- | --- |
| F | `src/gestaltdb/epistemic_engine.py`, `src/gestaltdb/epistemic.py` | `tests/test_epistemic.py`, `tests/test_temporal_epistemic_workflow.py` |
| R | `src/gestaltdb/reasoning/engine.py`, `src/gestaltdb/reasoning/typed.py` | `tests/test_reasoning_engine.py`, `tests/test_reasoning_typed.py`, `tests/test_reasoning_negation.py` |
| B | `src/gestaltdb/reasoning/batches.py`, `src/gestaltdb/reasoning/adapters/arrow.py` | `tests/test_reasoning_batches.py`, `tests/test_reasoning_arrow.py` |
| P | `src/gestaltdb/reasoning/plans.py`, `src/gestaltdb/reasoning/relational.py` | `tests/test_reasoning_plans.py`, `tests/test_reasoning_relational.py` |
| O | `src/gestaltdb/ontology/compiler.py`, `src/gestaltdb/ontology/adapters/rdf.py` | `tests/test_ontology_compiler.py`, `tests/test_ontology_chains.py`, `tests/test_ontology_rdf.py`, `tests/test_ontology_conformance.py` |
| E | `src/gestaltdb/reasoning/equality.py` | `tests/test_reasoning_equality.py` |
| V | `src/gestaltdb/reasoning/validation.py`, `src/gestaltdb/reasoning/structural.py` | `tests/test_reasoning_validation.py`, `tests/test_reasoning_structural.py` |
| M | `src/gestaltdb/reasoning/maintenance.py`, `src/gestaltdb/_graph/truth_maintenance.py` | `tests/test_reasoning_maintenance.py`, `tests/test_reasoning_deletion.py`, `tests/test_truth_maintenance.py`, `tests/test_reasoning_dependency_lookups.py` |
| S | `src/gestaltdb/reasoning/adapters/session_store.py`, `src/gestaltdb/reasoning/adapters/authenticated_sessions.py` | `tests/test_reasoning_sessions_durable.py`, `tests/test_reasoning_authenticated_sessions.py`, `tests/test_reasoning_crash_evidence.py` |
| G | `src/gestaltdb/reasoning/adapters/publication.py`, `src/gestaltdb/reasoning/adapters/range_publication.py`, `src/gestaltdb/reasoning/generation.py` | `tests/test_reasoning_generations.py`, `tests/test_reasoning_publication_freshness.py`, `tests/test_reasoning_bound_range_publication.py` |
| I | `src/gestaltdb/reasoning/intervals.py`, `src/gestaltdb/reasoning/temporal_closure.py`, `src/gestaltdb/reasoning/temporal_sweep.py` | `tests/test_reasoning_intervals.py`, `tests/test_reasoning_temporal.py`, `tests/test_reasoning_temporal_negation_aggregates.py`, `tests/test_reasoning_conformance.py` |
| X | `src/gestaltdb/reasoning/explanations.py`, `src/gestaltdb/reasoning/typed_proofs.py` | `tests/test_reasoning_explanations.py`, `tests/test_reasoning_typed_proofs.py` |
| N | `src/gestaltdb/reasoning/adapters/native.py`, `src/gestaltdb/reasoning/adapters/native_locator_claims.py`, `src/gestaltdb/reasoning/adapters/native_claims.py` | `tests/test_reasoning_native_scan.py`, `tests/test_reasoning_native_locator_claims.py`, `tests/test_reasoning_native_claims.py` |
| Q | `src/gestaltdb/reasoning/demand.py`, `src/gestaltdb/reasoning/adapters/frontier.py`, `src/gestaltdb/reasoning/adapters/queries.py` | `tests/test_reasoning_demand.py`, `tests/test_reasoning_frontier.py`, `tests/test_reasoning_queries.py` |
| T | `src/gestaltdb/reasoning/terms.py`, `src/gestaltdb/reasoning/typed.py` | `tests/test_reasoning_terms.py`, `tests/test_reasoning_typed.py`, `tests/test_reasoning_aggregate_rows.py`, `tests/test_reasoning_expansion.py` |
| A | `src/gestaltdb/reasoning/adapters/polars.py` | `tests/test_reasoning_polars.py`, `benchmarks/results/reasoning-re03-comparison.json` |
| C | `src/gestaltdb/reasoning/profiles.py`, `src/gestaltdb/reasoning/scopes.py`, `src/gestaltdb/reasoning/scale.py` | `tests/test_reasoning_profiles.py`, `tests/test_reasoning_scopes.py`, `tests/test_reasoning_scale.py`, `planning/reasoning-engine/design-dispositions.md` |
| L | `src/gestaltdb/reasoning/el.py` | `tests/test_reasoning_el.py` |
| H | `src/gestaltdb/temporal_engine.py`, `src/gestaltdb/kvstores.py`, `src/gestaltdb/modal.py` | `tests/test_readview.py`, `tests/test_graphdb_versions.py`, `tests/test_modal.py`, `tests/test_pyrex_verifiable_snapshots.py` |
| K | `src/gestaltdb/ingestion_service.py`, `src/gestaltdb/_graph/ingest_edges.py` | `tests/test_ingestion_service.py`, `tests/test_ingestion.py` |
| Z | `benchmarks/reasoning_pipeline.py`, `benchmarks/reasoning_maintenance.py`, `benchmarks/reasoning_temporal.py`, `benchmarks/reasoning_frontier_pipeline.py` | `tests/test_reasoning_pipeline_benchmark.py`, `tests/test_reasoning_matrix.py`, `tests/test_reasoning_frontier_pipeline.py` |

## 7. Live 136-feature register (Excel source)

Stable GR IDs, original construct names and original acceptance criteria remain in the workbook. **Available** means implemented for the scope stated here; **Partial** means a broader criterion remains unmet; **Experimental** means prototype-only; **Deferred** means not implemented/selected for delivery, not necessarily actively pending. Suggested native layers in the original audit are proposals, not requirements to reimplement correct Python behavior. Explicitly native/scale criteria remain partial/deferred where unproven. No row status is inferred merely from a closed RE issue.

| ID | Construct | Status | Evidence | Delivered scope / remaining boundary |
| --- | --- | --- | --- | --- |
| GR-001 | Stable entity & relationship IDs | Available | F,H | Stable external IDs and immutable version identities survive reopen. |
| GR-002 | Typed binary relationships | Available | R,H | Stored directed typed adjacency and binary fact relations. |
| GR-003 | Subject-predicate-object claims | Available | F | Sourced claims remain separate from mutable graph edges. |
| GR-004 | Positive/negative evidence | Available | F | Independent polarity with supported/refuted/both/unknown status; not negative rule premises. |
| GR-005 | Sourced claim metadata | Available | F | Agent/source/world/confidence persisted; confidence is metadata. |
| GR-006 | Bitemporal immutable versions | Available | F,H | Append-only versions and valid/system horizons. |
| GR-007 | Arbitrary-arity internal relations | Available | T | Executable typed n-ary facts/rules; structural relational IR remains a separate surface. |
| GR-008 | Typed literals in rules | Available | T | Strict finite literal vocabulary and comparisons, no implicit entity/literal coercion. |
| GR-009 | Integer symbol/entity dictionary | Available | B | Deterministic snapshot-local uint64 IDs; not a persistent/native symbol service. |
| GR-010 | Base vs inferred fact stores | Available | G,M | Explicit managed derived worlds/generations preserve authoritative base claims. |
| GR-011 | Canonical fact identity | Available | F,B | Proposition/fact identity is distinct from independent evidence IDs. |
| GR-012 | Deduplicated set relations | Available | B,R | Exact bounded set membership and iteration deduplication; no unlimited-scale guarantee. |
| GR-013 | Equality/equivalence facts | Available | E | Explicit snapshot-local equality links and rewrite. |
| GR-014 | Equivalence-class representatives | Available | E | Canonical smallest-member aliases, explicit deletion rebuild. |
| GR-015 | Context-scoped fact partitions | Available | B,G | Explicit context/dictionary/source identities; contexts never join implicitly. |
| GR-016 | Compressed columnar fact segments | Partial | B,C | Compact contiguous buffers exist; compressed spill/reload executor deferred. |
| GR-017 | Predicate-indexed relation scan | Available | N,R | Pinned predicate extraction and explicit native prefixes; complete native catalog authentication retained. |
| GR-018 | Variable binding & unification | Available | R | Constants/repeated variables checked in binary/typed engines. |
| GR-019 | Selection & projection | Available | T,A | Typed filters and optional batched Polars projection/selection. |
| GR-020 | Multi-relation equijoins | Available | R,T | Indexed multi-atom joins in supported binary/n-ary fragments. |
| GR-021 | Hash join | Partial | A,P | Native Polars equijoin and Python hashed indexes; no explicit partitioned hash-join runtime contract. |
| GR-022 | Index nested-loop join | Available | R,P | Bound endpoint/position buckets and exact probes avoid unbound relation scans. |
| GR-023 | Sort-merge join | Deferred | C | No dedicated selectable sorted-batch reasoning operator; benchmark-gated. |
| GR-024 | Semi-join | Experimental | A | Internal bounded Polars semi-join; not a general public native algebra. |
| GR-025 | Anti-join | Available | R,A | Exact scoped stratified absence; Polars internal anti-join is set difference, not user NAF. |
| GR-026 | Union and distinct | Available | B,R | Exact set union/dedup across alternatives and rounds. |
| GR-027 | Difference | Available | B,A | Stable/new delta difference and optional native column operations. |
| GR-028 | Group/reduce | Available | T | Nonrecursive aggregate stratum with explicit grouping/type semantics. |
| GR-029 | Typed arithmetic/comparisons | Partial | T | Kind-strict comparisons/reductions exist; general arithmetic-expression rule algebra absent. |
| GR-030 | Predicate-position variables | Available | T | Controlled explicit finite-domain expansion, not implicit unrestricted predicates. |
| GR-031 | Cost-based join ordering | Deferred | P,C | Current smallest-bound-bucket heuristic is not a general cost model/operator optimizer. |
| GR-032 | Cardinality/selectivity statistics | Partial | P | Optional relation/bucket/skew observations; no persistent multicolumn optimizer statistics. |
| GR-033 | Vectorized batch executor | Experimental | A | Explicit binary closure-only Polars native operators; Python fixpoint orchestration remains. |
| GR-034 | Worst-case-optimal multiway joins | Deferred | C | No dedicated multiway join engine; retain measured revisit gate. |
| GR-035 | Safe positive Horn rules | Available | R | Unsafe heads rejected before execution. |
| GR-036 | Recursive rules and fixpoint | Available | R | Bounded least fixpoint over supported finite positive programs. |
| GR-037 | Semi-naive delta filtering | Available | R | Delta-sensitive evaluation with exact dedup. |
| GR-038 | Per-atom delta-join variants | Available | R,P | Explicit body-position pivots and prepared delta occurrences. |
| GR-039 | Stable/delta/new relation lifecycle | Available | B,R | Immutable/reference relation generations and exact per-round new sets; not persistent native delta stores. |
| GR-040 | Rule dependency graph | Available | P,T | Positive/negative predicate dependencies and explicit safety checks. |
| GR-041 | SCC and stratum scheduling | Available | P,T | Prepared positive SCCs and typed stratification; negative cycles rejected. |
| GR-042 | Rule IR compilation | Partial | P | Reusable binary compiled schedules/inspection; no common executable relational IR for every typed feature. |
| GR-043 | Typed term normalization | Partial | T | Constants/variables/strict literals normalized; arbitrary structured terms absent. |
| GR-044 | Stratified negation | Available | R,T | Explicit closed predicates and completed lower-stratum checks. |
| GR-045 | Safe recursive aggregates | Partial | T,C | Safe nonrecursive aggregate stratum delivered; recursive/lattice aggregates deferred. |
| GR-046 | Existential witnesses (bounded) | Experimental | L | Experimental bounded EL completion/witness identity and termination oracle; not production existential Datalog. |
| GR-047 | Goal-directed evaluation | Available | Q | Demand-specialized answers and authenticated positive binary frontiers. |
| GR-048 | Magic set/demand rewriting | Available | Q | Explicit supported-fragment specialization with full-answer parity. |
| GR-049 | Parallel rule execution | Deferred | A,C | Polars native threading is not a parallel rule scheduler or demonstrated scaling contract. |
| GR-050 | Work-queue-driven inference | Available | R,M | Predicate-triggered round dispatch and changed-predicate insertion scheduling; no asynchronous native queue. |
| GR-051 | Multi-head normalization | Available | T | Explicit normalization to single-head rules. |
| GR-052 | Class assertions | Available | O | Explicit class membership vocabulary and graph mapping. |
| GR-053 | Subclass closure | Available | O | Named subclass inheritance compiled to recursive Horn rules. |
| GR-054 | Subproperty closure | Available | O | Named subproperty propagation under selected profile. |
| GR-055 | Domain & range | Available | O | Entail membership from subjects/objects; not shape validation. |
| GR-056 | Inverse & symmetric | Available | O | Directional generated Horn rules with exact axiom origins. |
| GR-057 | Transitive relationships | Partial | O,A | Correct indexed closure and optional Polars prototype; no general native performance certification. |
| GR-058 | Reflexive/irreflexive | Available | O,V | Scoped reflexivity plus explicit irreflexive-conflict reports; no unscoped reflexivity. |
| GR-059 | Equivalent classes/properties | Available | O | Named bidirectional implications, not general anonymous OWL equivalence. |
| GR-060 | Property chains | Partial | O | Two-link chains compile; arbitrary-length imported chains not supported. |
| GR-061 | Disjoint classes/properties | Partial | V | Class disjointness reports delivered; general property-disjointness axioms absent. |
| GR-062 | sameAs / differentFrom | Available | E | Scoped links, canonicalization and inequality evidence; no physical merge. |
| GR-063 | Functional/inverse functional | Partial | E,V | Functional conflict reports/explicit equality links; no general inverse-functional compiler support. |
| GR-064 | Intersection/union class expressions | Partial | O | Horn-safe directional named forms; disjunctive heads/complements not inferred. |
| GR-065 | Complement class expressions | Deferred | C | No general complement semantics; scoped absence is not OWL complement. |
| GR-066 | Existential class restrictions | Experimental | L | Bounded selected EL feasibility only; general ontology compiler restrictions deferred. |
| GR-067 | Universal class restrictions | Deferred | C | No general allValuesFrom support. |
| GR-068 | Value/self restrictions | Deferred | C | No general hasValue/hasSelf compiler support. |
| GR-069 | Cardinality constraints | Partial | V,C | Closed-class fact-count validation exists; equality-normalized OWL filler cardinality reasoning absent. |
| GR-070 | HasKey identity | Deferred | C | No property-key identity inference. |
| GR-071 | Ontology satisfiability | Partial | V,L | Static subclass/disjoint class conflicts and experimental EL oracle; not complete satisfiability. |
| GR-072 | Complete OWL2 DL reasoning | Deferred | C,L | Explicit research non-goal, no production DL/JVM reasoner. |
| GR-073 | Immutable rule justifications | Available | F,M | Stored rule/premise versions and exact historical explanation evidence. |
| GR-074 | Multiple independent supports | Available | M,S | Bounded all-support maintenance and exact assertion-version multiplicity. |
| GR-075 | Historical explanation graphs | Available | F,X | Historical claim/rule versions and bounded typed witnesses are distinct supported contracts. |
| GR-076 | Premise→conclusion dependencies | Available | M | Persisted reconciliation metadata and reusable standalone reverse dependency index. |
| GR-077 | Rule→conclusion dependencies | Available | M | Rule dependency lookups and explicit revision invalidation; local stratum optimization not implied. |
| GR-078 | Support counts | Available | M,S | Counts derive from deduplicated support/version identities; counts alone never decide recursive deletion. |
| GR-079 | Deletion-and-rederivation | Available | M | DRed-style overdeletion/rederivation with recursive-cycle oracle parity. |
| GR-080 | Backward/forward incremental deletion | Deferred | C | Compared/revisit decision recorded; no second B/F executor delivered. |
| GR-081 | Signed differential updates | Deferred | C | Logical evidence deltas exist; no signed-multiplicity differential runtime. |
| GR-082 | Materialization generations | Available | G | Staged point and bound interval publication with source/target guards. |
| GR-083 | Incremental rule changes | Partial | M,G | Revision-bound invalidation/rebuild correctness; localized affected-stratum rule maintenance absent. |
| GR-084 | Minimal explanations | Partial | X | Deterministic bounded one-step minimum-premise selection; global recursive minimality deferred. |
| GR-085 | Ranked provenance | Partial | X,C | Deterministic top-k one-step supports; no probabilistic semiring propagation. |
| GR-086 | Four-valued claim status | Available | F | Unknown/support/refutation/both remain distinct. |
| GR-087 | Explicit negative rule premises | Deferred | V,C | Ground denials validate positives; no signed-denial rule premise mechanism. |
| GR-088 | Negation as failure | Available | R,T | Absence only under explicit stratified closed predicates. |
| GR-089 | Class disjointness conflicts | Available | V | Bounded non-explosive conflict evidence. |
| GR-090 | Inequality conflicts | Available | E | DifferentFrom versus equality-path evidence reported in scope. |
| GR-091 | Functional property checks | Available | E,V | Explicit conflict/equality policy for supported functional properties. |
| GR-092 | Datatype canonicalization | Available | T | Strict finite lexical/value vocabulary including instant microseconds; not full RDF datatype facets. |
| GR-093 | Constraint provenance | Partial | V | Logical supporting facts and canonical constraint IDs; not arbitrary source axiom/claim-version provenance. |
| GR-094 | Classical/paraconsistent profiles | Partial | C,V | Explicit named non-explosive profiles/gates; no general selectable classical OWL calculus. |
| GR-095 | Predicate-level closed-world scope | Available | T,R | Explicit closed declarations and negative-cycle rejection. |
| GR-096 | SHACL-style validation kernels | Partial | V | Required/cardinality/literal-kind/choice kernels on closed classes; not SHACL shape language. |
| GR-097 | Probabilistic/fuzzy reasoning | Deferred | C | No approved calculus; #124/#128 remain separate proposals. |
| GR-098 | Half-open valid intervals | Available | I,H | Exact half-open overlap/intersection, touching bounds and unbounded ends. |
| GR-099 | System time horizons | Available | F,H | Pinned commit prefixes and historical correction/retraction replay. |
| GR-100 | Consistent read-view provenance | Available | H,G | Explicit authenticated source identity and compatible generation admission; capabilities differ by backend. |
| GR-101 | Temporal premise intersection | Available | I | Per-segment valid-time evidence and gap-preserving support intersections. |
| GR-102 | Bounded modal accessibility | Available | F,H | Separate finite belief/knowledge/modal frames with bounds. |
| GR-103 | Temporal delta relations | Partial | I | Active-set event sweep and unchanged-segment reuse; not general native temporal delta relations. |
| GR-104 | Interval sets preserving gaps | Available | I | Disjoint interval unions retain unsupported gaps. |
| GR-105 | Temporal coalescing | Available | I | Truth unions and equal-support segment coalescing are explicitly distinct. |
| GR-106 | Native interval join | Deferred | I,C | Scalar interval algebra/change segments exist; no native batched interval join executor. |
| GR-107 | Full-range temporal closure | Available | I | Every supported change segment evaluated, including selected absence/aggregates; not single-point sampling. |
| GR-108 | World-qualifier on native joins | Partial | N,B | Native world-prefix extraction/context binding; reasoning joins still scoped Python or explicit Polars, not general native world joins. |
| GR-109 | Cross-world inference policies | Partial | C | Default-deny explicit grant values/gates exist; general inheritance evaluation deferred. |
| GR-110 | Confidence combination policy | Partial | C | Approval gate prevents accidental probability semantics; no approved combination/retraction calculus. |
| GR-111 | Temporal modal closure | Deferred | C,H | Bounded point modal evaluation exists; no general temporal-modal closure executor. |
| GR-112 | Native columnar ingest | Available | K | Optional PyRex native payload writer preserved; ingestion is not native inference. |
| GR-113 | Batched indexed scan | Available | N | Explicit native prefix/range index scans with bounds, snapshot lifetime and full source admission. |
| GR-114 | MultiGet batch | Deferred | N,H | No admitted native multi-key reasoning read API; prefix batches/point materialization are different operations. |
| GR-115 | Arrow C Data/C Stream bridge | Available | B | Explicit capsule exchange and ownership checks; imports validate/copy as documented. |
| GR-116 | Native symbol dictionary | Partial | B | Snapshot-local Python dictionary/compact IDs exist; no bespoke native encoding service. |
| GR-117 | Native compact working relations | Experimental | B,A | Compact buffers and optional Polars native columns; not production general native relation storage. |
| GR-118 | Native joins & delta loops | Partial | A | Native Polars joins with Python round orchestration; million-row native-loop criterion unproven. |
| GR-119 | Multithreaded hash/merge joins | Partial | A,C | Optional native threading measurements exist; no admitted scaling/strategy guarantee. |
| GR-120 | Atomic derived generation swap | Available | G | Guarded visibility-marked old-or-new point/range publication, not arbitrary persistent native stores. |
| GR-121 | Spilling & memory budgets | Deferred | C | Explicit budget policy only; no spill engine or hard RSS bound. |
| GR-122 | GIL-free batch execution | Partial | A,N | Optional native batch calls; Python fixpoint/decoding remains, no whole-run GIL-free guarantee. |
| GR-123 | Native instrumentation | Partial | P,A,N | Wrapper/operator work/time counters; not complete native allocator/copy/backend-I/O instrumentation. |
| GR-124 | Cancellation and time limits | Partial | R,G | Cooperative cancellation/bounded work fails without partial publication; native calls/sorts/blocking iterators not forcibly interrupted. |
| GR-125 | Zero-copy ownership/lifetime tests | Available | B,N | Arrow buffer/snapshot release, ownership, foreign-source and cancellation tests; not universal zero-copy operation. |
| GR-126 | Bounded resource controls | Available | R,G | Input/facts/rules/rounds/candidate/proof/publication limits; resource units remain explicit. |
| GR-127 | Reasoning plan inspection | Available | P | Binary prepared/runtime inspection with heuristic estimates/actual work; telemetry opt-in. |
| GR-128 | Soundness differential tests | Available | R,O,M,I | Independent grounding/ontology/update/point-range comparisons under declared fragments. |
| GR-129 | Completeness profile tests | Available | O,C | Selected named-profile oracle/conformance corpus; no complete RDFS/OWL standards badge. |
| GR-130 | Deterministic repeated-run tests | Available | R,V | Deterministic logical closure/report ordering and retry tests; freshly allocated version IDs need not match. |
| GR-131 | Native/Python property tests | Experimental | A | Randomized Polars/reference closure parity for prototype fragment; no bespoke native engine exists. |
| GR-132 | RDF/OWL import normalization | Partial | O | Strict bounded offline supported forms; general RDF/OWL import semantics rejected. |
| GR-133 | Inference-specific benchmark suite | Available | Z | Separate extraction/evaluation/update/range/publication evidence; measured workloads only. |
| GR-134 | Operator-level timing/memory | Partial | P,Z | Timings/work/whole-worker RSS exist; not complete per-operator CPU/allocation/spill/backend-I/O accounting. |
| GR-135 | Snapshot/plan compatibility checks | Available | P,G,S | Exact program/catalog/source/policy/dictionary identities and stale-generation rejection. |
| GR-136 | Recovery/checkpoint | Partial | S,X,G | Durable session/closure recovery, checks and explicit discard; no arbitrary long-running native computation resume. |

## 8. Synchronization, validation and historical evidence

### Update workflow

1. Update this file's review timestamp/source SHA, relevant current/delivery/pending rows and GR dispositions after checking code/tests and GitHub.
2. Update `planning/reasoning-engine/roadmap.json` delivery metadata and `tracking-status.json` only when their checked fields change. They are supporting machine indexes, not competing narrative roadmaps.
3. Run `uv run --with openpyxl python planning/reasoning-engine/sync_tracker.py` to regenerate the workbook from section 7, retaining original acceptance criteria, priorities and unassigned owners. `--check` detects divergence without writing. Do not edit an open workbook: save/close LibreOffice first.
4. Run `uv run python planning/reasoning-engine/check_roadmap.py`, `uv run python planning/reasoning-engine/check_tracking.py --github`, and the documentation/example/tests gates below. Live GitHub checks are read-only.

The workbook's original audit is preserved in its **Original audit** sheet. Historical binary/source hashes and original architecture proposals remain reproducible from git; they must not prevent updating the live workbook. Original 136 GR identities and acceptance criteria are not silently rewritten to turn partial delivery into completion. Closed issue counts and status totals are not performance measurements.

### Validation at reviewed source

- Python 3.13.11 full suite: **4,095 passed, 15 skipped in 84.97s**. Command: `uv run --python 3.13 --extra backends --extra serializers --extra rdf --extra polars --extra bloom --extra docs --with-requirements benchmarks/ontology-requirements.txt pytest -q --tb=short`.
- All **8 modular documentation examples passed** with those extras (without extra oracle requirements). Documentation/API drift and original roadmap/DAG checks passed.
- Runtime versions: pytest 9.1.1, pyrex-rocksdb 0.7.1, LMDB 3.0.0, plyvel 1.5.1, PyArrow 24.0.0, Polars 1.41.2, RDFLib 7.6.0, Clingo 5.8.2 and OWL-RL 7.6.2.
- The initial live tracking check exposed hardcoded open #156 and missing child #385; this sanitation refreshes the supporting snapshot/checker. It does not alter GitHub issues.
- No new performance benchmark, Python 3.10–3.12 matrix, browser/ML-framework or installed-release artifact certification was performed in the source review. Committed benchmark reports are dated evidence only. Fresh-worker/first-reopened is not OS-cold; copied bytes are not assumed zero; RSS often includes the whole worker.

### Sanitation validation (2026-11-01T11:44:00Z)

- Final working-branch full suite: **4,115 passed, 14 skipped in 92.34s**, using the same Python/extras command above plus `--with openpyxl`. The new synchronization/tracking regression module separately passes **19 tests**, including drift rejection, historical-criteria preservation, save/reopen round trips and refusing to overwrite an open workbook.
- All **8 documentation examples**, documentation/API drift, live GitHub tracking, roadmap/DAG/original-audit checks, Excel `--check`, targeted correctness lint and `git diff --check` pass. The stdlib roadmap check also passes with `python -S`, without openpyxl/site packages or old git objects; shallow CI need not fetch the historical workbook commit.
- Excel totals: **80 Available, 33 Partial, 6 Experimental, 17 Deferred**. These are scoped feature dispositions, not test counts or promises that deferred features are scheduled.

### Native-follow-up reconciliation (2026-11-01T13:00:35Z)

Reviewed sanitation merge `7f28808` against PR #450: main changed documentation,
tracker tooling/workbook and CI policy, not the runtime source measured by the
earlier pilots. No new performance measurement is claimed for this merge. The
pending-work rows now distinguish #194's unmerged measurement/decision delivery
from #451's unimplemented performance remediation. GR feature dispositions remain
unchanged; regenerating Excel updates its review/roadmap view, not the original
acceptance criteria or a performance certification. Earlier full-suite counts
above retain their original review scope and are not relabelled tests of this SHA.

Reconciled PR #450 working-branch validation after integrating `7f28808`: **4,199
passed, 41 skipped** (Python 3.13 with backend/serializer/Arrow/Polars/RDF/NetworkX/
bloom/docs extras and openpyxl); all **8** modular documentation examples passed.
Roadmap/original-audit, live GitHub tracking (including open #451), generated Excel
`--check`, documentation/API drift, Sphinx `-W` and diff checks passed. This validates
the merged working branch, not a new performance run or an installed-release claim.

### Historical records, not current operating instructions

- The original architecture assessment is `planning/reasoning-engine/source-assessment.md`; the original workbook bytes are in git at `3403530:gestaltdb_reasoning_feature_tracker.xlsx`. Both describe their original baselines, not today's missing-feature list.
- `roadmap.json` preserves the original baseline `c71f043`, phase dependencies, GR coverage and accepted work packages. Historical branch/worktree names are provenance only. The delivery ledger above supersedes its former RE-12 “in-review” status and all started-work diaries.
- `planning/reasoning-engine/release-readiness.md`, `tracking-audit.md`, design/overlap dispositions and `planning/reasoning-followthrough/` retain detailed dated investigations/measurement contracts. Their old candidate/branch instructions and pending claims do not override sections 1–4 here.
- `EPIC_ONTOLOGY_EXTENSIONS_251026.md` is retained as a pointer to this consolidated status and the five implementation issues, not a second live proposal ledger. Long obsolete diaries and release orders remain available through git history rather than being copied into more floating planning documents.

Future release decisions require explicit authorization and fresh exact-SHA CI/artifact validation; the old proposal to publish 0.13.0rc1 and target the integration branch is fulfilled history, not an instruction to repeat it.
