# Reasoning overlap dispositions (#373)

Audited integration: `9473497` (PR #375 + #376 merged).
Parent review: #200. Roadmap: #156. Related: #371 (D2/D3/D6).

Rule for this issue, per #371 D2: no grand unified engine, no digest or
round-count semantic change merely for cleanup. Each area below records
**keep with justification** or **unify** with linked evidence. An area
escalates to a correctness issue (like #374) only on shown incorrect
results, identity leakage, or unsafe publication.

## Mechanical slice (merged via #376 at `b395e99`)

| Area | Disposition | Evidence |
|---|---|---|
| Unreachable lines `reasoning/intervals.py:243-245` | Removed | Post-`return` dead copy; full suite green on PR #376 |
| Duplicated `active` + double `check_cancelled` in `maintenance.py:285-293` | Removed second computation | Identical comprehension shadowing the first; 35 focused temporal/range/scale tests green |
| Stale `temporal_sweep.py:1` docstring ("not yet wired") | Fixed | Wired at `temporal_closure.py:269-272` since the sweep integration |

## Per-area dispositions (remaining)

| # | Area | Locations | Disposition |
|---|---|---|---|
| 1 | `_Budget` vs `_TypedBudget` (11 identical lines) | `reasoning/engine.py:69-80`, `reasoning/typed.py:730-740` | **Keep parallel, documented.** Private, tiny, zero semantic risk either way; unifying creates a new cross-module dependency for no behavioral gain. Revisit only if budget accounting itself changes. |
| 2 | Fixpoint loops (binary / typed-positive / stratified / planned-SCC) | `engine.py:198-235`, `typed.py:802-836,883-925`, `plans.py:347-386` | **Keep separate schedulers; share nothing implicitly.** Stratified and SCC iteration counts are deliberately total-component-rounds, not global rounds (`plans.py:295-301` already documents the divergence). Unifying loop headers risks changing published `iterations` telemetry. |
| 3 | Matcher DFS x5 (`_choose`/`_matches`, typed, stratified, maintenance premise-tracking, frontier inline) | `engine.py:82-135`, `typed.py:939-990,1045-1072`, `maintenance.py:325-352`, `frontier.py:142-168` | **Keep, with drift warning.** Only deltas are arity/index-key, filter/negation gating, premise capture. A generic DFS is the natural unification *if* a heuristic drift bug is ever shown; until then the duplication is safer than a shared subtle-behavior change. |
| 4 | `_Index` (binary) vs `_TypedIndex` (n-ary literal-aware) | `engine.py:22-50`, `typed.py:675-709` | **Keep split.** Bucketing and exact-match semantics differ intentionally (`-0.0` probe at `typed.py:685-689`). Shared base only for a future common `Budget`/dispatch helper, not the index itself. |
| 5 | SCC/stratification (`typed._stratify` vs `plans._components`) | `typed.py:470-552`, `plans.py:29-92` | **Keep fork, documented.** Positive-subgraph vs negative-edge stratification serve different contracts; component order may differ and both are deterministic. Sharing one Kosaraju helper is allowed only with the negative-edge parameter pinned by test. |
| 6 | Demand vs frontier vs plans (goal strategies) | `demand.py:66-163`, `frontier.py:42-177`, `plans.py:295-391` | **Keep three modes.** Offline n-ary rewrite, online binary callback-driven, precompiled full closure. Unify vocabulary (`BoundGoal`/`FrontierRequest`) in docs only; no dispatch-table change. `demand.SpecializationCache` horizon non-invalidation stays caller-managed and documented. |
| 7 | `GenerationBinding` x4 + execution brackets | `adapters/generation.py:36`, `adapters/session_generation.py:100`, `adapters/graph_execution.py:30`, `adapters/range_generation.py:78`; `adapters/execution.py:111-203 vs 237-290` | **Keep types; share the bracket idiom by example, not base class.** Temporal/current/range identities are deliberately distinct (`CurrentSourceIdentity` vs `TemporalSourceIdentity` vs `TemporalRangeSourceIdentity`). A generic `GenerationBinding[S,P]` is approved *if* a third copy appears; two parallel shapes do not justify it yet. |
| 8 | Evidence/support shapes (5 types, 3 digest prefixes) | `protocols.Evidence`, `proofs.FactWitness`, `maintenance.Support`, `temporal_closure.TemporalDerivation`, `batches.RelationEvidence`, `explanations.RankedWitness` | **Keep distinct.** One bounded witness, N supports, per-segment derivations, and row refs serve different consumers by design (`model.py:262-270`, `proofs.py:1-6` pin the single-witness contract). No shared `PremiseSet` trait until a consumer needs cross-shape comparison. |
| 9 | `iterations`/`work` telemetry vocabulary | All engines + `maintenance.py:298-300`, `temporal_closure.py:353-360`, `frontier.py:70-77` | **Document, do not rename.** Counts are incomparable by design (candidate rows vs scheduler visits vs support probes). Renaming breaks published telemetry; cross-engine joins must go through per-engine documented meaning. This note is the canonical reference. |
| 10 | Publication guard/fingerprint salts | `adapters/publication.py` vs `adapters/range_publication.py` | **Keep split.** Own-computation vs caller-trusted staging is a semantic boundary, not duplication. Guard sharing only via the existing `_temporal_writer_guard` + orphan-audit helpers. |

## What stays out of this issue

- Range memo, provenance triple, error-hierarchy split, verify sampling: tracked and fixed/narrowed in #374 (sub-issue of #160), not here.
- Native locator/pushdown: #194 / PR #369.
- Durable crash/concurrency evidence: #370. Design accounting: #371.
