# Original design/disposition findings D2–D6 (#371)

Parent review: #200. Roadmap: #156. Work branch: `feature/re-371-dispositions`.
T1–T7/E1–E3/D1 are closed via earlier fixes (#207–#217, #223–#230, #232–#234);
this note dispositions only D2–D6. Rule: no grand unified engine, no digest or
round-count change merely for cleanup (same D2 constraint as #373).

## D2 — execution plumbing (keep, documented)

What #255 shares across binary/prepared/typed application dispatch is the
admission/telemetry bracket (`adapters/execution.py`: executor check,
`require_compilation`, `admit`, `max_input_facts` materialization,
`StageTelemetry`/`ExecutionOutcome`, explicit Polars budget). Engine dispatch
stays separate: `DatalogEngine` (binary), `PlannedDatalogEngine` (SCC),
`GenericEngine`/`evaluate_stratified` (typed). Matcher DFS (x5), `_Budget`
twins, and the Kosaraju fork are recorded keep-with-justification in
`planning/reasoning-engine/overlap-dispositions.md` (#373, merged). No
further unification: shared helpers already prevented T1/T5-class drift via
per-entry budget checks, and unifying loop headers would change published
`iterations` telemetry. **Status: disposed (documented keep).**

## D3 — iterator ownership (keep separate, documented)

`PinnedClaimSource` (direct iteration) vs `PinnedBatchedClaimSource`
(`FactBatch` with predicate/subject/object pushdown) vs
`PinnedNativeClaimSource` (full-record scan, post-selection filters) are three
evidence contracts, not one iterator with two wrappers: pushdown changes which
facts are read, post-selection filtering does not, and native conformance
checks full-history coverage that the index path trusts. #374 (merged via
#375) already unified their binding hygiene (store/serializer identity pins
in both `_check_binding`). Extracting one shared iterator would erase the
pushdown-vs-post-selection distinction that #194 measurements rely on.
**Status: disposed (documented keep + #374 hardening linked).**

## D4 — schema-cache accounting (corrected + deferred)

Two targeted corrections in this slice:
1. `ontology/compiler.py: CompiledSchemaCache` docstring claimed FIFO
   eviction while hits refresh recency (LRU). Fixed to
   least-recently-used; `tests/test_ontology_chains.py` renamed to
   `test_schema_cache_reuses_revisions_and_evicts_lru` with a hit-refresh
   ordering sequence that discriminates LRU from FIFO.
2. Acceptance wording: the cache memoizes compilation output by revision —
   it never materializes a reusable TBox/instance closure. `docs/ontology*.rst`
   and `docs/reasoning-validation.rst` do not overclaim; the honest boundary
   already lives in `release-readiness.md` (compilation memoization delivered,
   reusable TBox closure not delivered). The `roadmap.json` RE-08 title phrase
   is original-plan wording, preserved as history.
Reusable schema-closure optimization across changed ABoxes: **explicit defer**
(no ABox-sensitive reuse without dedicated tests). **Status: corrected + deferred.**

## D5 — negative premises and datatypes (accounting corrected, capability deferred)

`validation.Denial`/`ValidationSuite` are ground-denial conflict inputs;
`GenericRule.negated` is scoped absence under a program-level `closed`
declaration (`reasoning/typed.py`); there is no signed-denial rule-premise
mechanism and no datatype facet/shape check
(`profiles.py`: `datatype-facets` deferred). `docs/reasoning-validation.rst`
already states exactly this boundary, so no doc fix is needed. Delivered
subset recorded: ground-denial validation, scoped absence, strict literal
identity. Signed-denial premises and datatype validation: **explicit defer**
— a polarity-aware premise path or facet reasoner needs its own issue with
acceptance tests (a rule satisfiable only by a truly denied premise).
**Status: corrected (docs already honest) + deferred.**

## D6 — validation internals (corrected + documented)

Targeted correction in this slice: removed the unused `by_subject_predicate`
index from `validate()` (populated, never read; every check rescans
`ordered`/`members`). Remaining behavior kept and disclosed, not rewritten:
full input buffering + sort before periodic cancellation (`docs`:
cancellation cooperates during indexed processing, does not interrupt a
blocking iterator; `max_violations` bounds the report, not input memory —
`docs/reasoning-validation.rst`, `release-readiness.md`); violation sources
are generated constraint labels (`denial:`, `disjoint:`, `irreflexive:`,
`functional:`), not caller-supplied axiom IDs or claim-version references.
Streaming validation and caller-ID/exact-version provenance: **explicit
defer** (unpromised optimizations). **Status: corrected + deferred.**

## Map

| Finding | Disposition | Evidence |
|---|---|---|
| D2 | Documented keep (+ #373) | overlap-dispositions.md; #255 via #363–#365 |
| D3 | Documented keep (+ #374) | #375 (binding pins); this note |
| D4 | Docstring+test corrected; TBox-closure defer | compiler.py; test_ontology_chains.py; release-readiness.md |
| D5 | Docs already honest; signed/datatype defer | reasoning-validation.rst:64-68; profiles.py |
| D6 | Dead index removed; buffering/provenance disclosed + deferred | validation.py; reasoning-validation.rst:44-52,73-78 |
