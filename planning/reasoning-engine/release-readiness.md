# Reasoning testing-release readiness

Priority: ship a bounded, honest reasoning milestone for external testing soon,
not the entire enhancement backlog. Parent: #156; policy:
[`EPIC_REASONING_ENGINE.md`](../../EPIC_REASONING_ENGINE.md).

This is a release decision register, not a claim that the candidate is ready.
Initial audited baseline: `4789f94` (2026-10-23, after PR #305). Any main/release
decision must identify its exact SHA and rerun applicable gates after changes.
Candidate version: **0.13.0rc1**, prepared separately from the readiness gate.
It must not be published until the final candidate and main push gates pass.

## Blocker triage

| Scope | Current evidence / release treatment |
|---|---|
| #200 T1–T6, E1–E2: typed semantics, aggregate limits, extraction restrictions/identity | Fixes #207–#212/#214–#215 are closed and merged (#223–#228/#230/#232). Regressions are discovered by the reasoning CI glob; validate again in the full candidate matrix. |
| #200 T7/E3/D1: misleading traces, counters, independent negation oracle | #213/#216/#217 closed and merged (#229/#233/#234). Blocker-only traces are diagnostics, not absence proofs. |
| #244–#247: range construction, changed-program reuse, publication freshness, CI discovery | Closed; fixes are in integration (including #258/#259/#271). Keep range, revision, freshness and workflow-selection regressions in the release gate. |
| #275/#277/#282: temporal commit reads, duplicate detection, cache corruption/mutability | Closed and merged (#276/#278/#283). Integrity regression coverage must remain enabled; do not substitute performance-cache trust for storage verification. |
| #200 D2/D3/D6: duplicated plumbing, validation indexing/cancellation/provenance | Refactor work is deferred, not a reason for an engine rewrite. Release docs must disclose full validation input buffering, cooperative cancellation boundaries and structural labels rather than exact claim-version provenance. |
| #200 D4/D5: schema reuse and negative-premise delivery accounting | Release only the implemented subset: compilation memoization, ground-denial validation, scoped absence and strict literal values. Reusable TBox closure, signed-denial rule premises and datatype shape validation are not delivered by those mechanisms. |
| #248/#250/#251: shared identity, evidence ledger, incremental sessions | Merged increments #261/#264/#265 are opt-in APIs with explicit scopes. Do not claim every legacy publisher/cache uses a universal identity or that every rule combination supports incremental advance. Run admission, exact-evidence, session and fresh-recomputation regressions. Broader issue scope stays open. |
| #249/#252/#253/#254: demand/frontier/support/sweep performance | Existing tested increments remain; remaining storage pushdown, broader optimizer integration and sweep adoption are deferred unless a candidate correctness regression requires them. |
| #255/#256: executor and inferred-consumer composition | #269/#270 deliver explicit admission and a composed scenario, not universal engine interchangeability or transparent query rewriting. Keep scenario transition parity enabled. Remaining broad integration is not a prerequisite. |
| #194 / draft #262 | Native extraction benchmark expansion is deferred. Do not merge a draft or promise unmeasured native/100K-scale behavior. |
| Unrelated tooling/tutorials and sampling roadmap | Not prerequisites; do not grow candidate scope for them. Existing features still participate in full-suite regression checks. |

This triage is based on issue/merge history and existing tests, not a new formal
proof or exhaustive composition audit. Newly reproduced incorrect results,
isolation leaks, unsafe publication or data loss in an advertised workflow
remain release blockers even if its originating issue is closed.

## Evidence history and phase reconciliation

- PR #306, head `c333899`: full suites (including docs/examples/Sphinx),
  installed artifacts and ontology checks passed on Python 3.10–3.13 (12 jobs).
  Local full-extras runs on 3.10 and 3.13 each passed **2367 tests / 1 skip**.
  The skip is the LMDB case of nontransactional publication fault injection;
  the nontransactional backend cases run. No test deselections were used.
- Integration merge `0db0133`: push checks must be verified separately; green
  PR results above are not a claim about this or later merged SHAs.
- Draft main-target PR #307 enables the general, optional dependency,
  documentation, security, visualization and fuzzing checks. Its early runs
  predate some hardening and are not final release evidence.
- PR #308 (`release/reasoning-0.13.0rc1`, head `a59211d`): curated 0.13.0rc1
  metadata, changelog, `docs/reasoning-testing.rst` user-testing guide and
  phase-evidence reconciliation. All 17 PR checks passed; merged as `352ecad`.
  Post-merge integration push runs 39931451048 (readiness) and 39931451017
  (ontology) both succeeded on that SHA.
- Issue #309 / PR #310: main-target review exposed a real saved-view zoom
  bug (ResizeObserver auto-fit overwrote the restored viewport). Fixed at the
  runtime level with exact-transform assertions, post-restore resize
  regression and explicit-Fit behavior; merged as `6e5c3ff`. All 10 PR checks
  passed, including offline browser and fuzzing gates.
- Post-merge integration push run 39931993057 (readiness) succeeded on
  `6e5c3ff`. `origin/main` (`25daaf9`) remains a strict ancestor: 292
  integration-only commits, zero main-only commits.
- Main-target PR #307 at `6e5c3ff`: general Tests, optional-dependency,
  documentation, security, visualization and readiness gates all green;
  ClusterFuzzLite address gate was the last remaining check at the time of
  this update (see PR for final status). No main merge or publication yet.

| Phase | Existing acceptance evidence inspected | Remaining decision |
|---|---|---|
| #157 / RE-E1 | `test_reasoning_execution_baseline.py`, `test_reasoning_plans.py`, `test_reasoning_polars.py`, `test_reasoning_executors.py`, extraction policy/identity tests; `docs/reasoning-executors.rst` and checked-in RE-03 reports record the indexed-default/opt-in-Polars/no-native decision. | Small synthetic reports are not scale certification; #194's expanded extraction matrix remains deferred. Final candidate matrix/installation gates still required. |
| #158 / RE-E2 | Closed after prior verification; typed/aggregate/negation/oracle/demand/equality/validation suites rerun in #306. | Preserve narrower validation/provenance and combination boundaries documented in this release; do not reopen scope merely to implement every construct. |
| #159 / RE-E3 | Maintenance/deletion/catalog/session/freshness and publication tests; `test_reasoning_workload_gates.py` checks cooperative cancellation boundaries, stale identities and recovery consent; RE-18 maintenance reports record small-workload crossover evidence. | Cancellation/fault injection is not proof of every OS/process/crash scenario. Do not certify a blanket crash/concurrency guarantee or close the phase solely on these tests; broader durable recovery acceptance remains open. |
| #160 / RE-E4 | `test_reasoning_intervals.py`, `test_reasoning_temporal.py`, `test_reasoning_ranges.py`, revision/conformance and publication tests; RE-22 temporal reports and `docs/reasoning-conformance.rst`. | Advertise positive range closure and explicit unsupported fragments only; retain the broader crash-safe publication phase gate pending reconciliation with #159. |
| #161 / RE-E5 | Profile/EL/scope/scale/proof tests and corresponding docs provide named subsets, bounded experiments and explicit defers. | Full OWL DL, probabilistic/context calculi, spill, native kernels and differential runtimes are not this release's prerequisites. Do not claim their implementation or generic composition. |

The open phase epics are not automatically release blockers for a clearly
bounded testing slice. Any advertised stronger durability or composition
guarantee would require its missing evidence first. This register does not
close those epics or supersede their broader acceptance criteria.

## Required evidence before main merge

- [ ] Record the candidate SHA and compare with fresh `origin/main`; incorporate
  any independent main fixes without discarding integration work.
- [ ] Full suite with backend/serializer/RDF/Arrow/Polars/oracle extras on
  Python 3.10–3.13; record skips and failures, with no hidden deselections.
- [ ] Docs drift, all runnable markdown examples, demand quickstart regression,
  Sphinx warnings-as-errors and roadmap validation.
- [ ] Build/check wheel and sdist; run standalone installed-wheel demand parity
  without optional backends, then persisted session insert/correct/retract
  and reopen parity on a database created by published 0.12.0 (LMDB/JSON).
  This is a representative compatibility fixture, not every historic format.
- [ ] Existing optional-extra isolation and backend capability gates pass;
  LevelDB unified mutable snapshots remain unsupported and fail closed.
- [ ] Reconcile #157/#159/#160/#161 exit evidence, recording which experimental
  extensions are explicitly deferred rather than closing phases by child count.
- [ ] Curated changelog, prerelease version/lock metadata, compatibility notes,
  supported/experimental boundary and user-testing instructions reviewed.
- [ ] Main-target integration PR passes all applicable workflows, not only
  ontology CI. No unrelated feature additions during hardening.

The `Reasoning release readiness` workflow adds repeatable full-suite and
installed-artifact gates on integration and main. Existing general, optional
dependency, security and visualization gates still apply on the main PR.

## Required evidence after main merge

- [ ] Applicable main push workflows succeed on the exact merged SHA.
- [ ] Publish the prerelease from that SHA via the repository's trusted
  publishing workflow, not local credentials or a local twine upload.
- [ ] Publish workflow succeeds; verify PyPI metadata and install the actual
  published distribution for a final smoke test.
- [ ] Link results and known limitations in #156; continue only small,
  main-synchronized fixes toward stable promotion after user feedback.

Do not mark a gate complete merely because its job exists. Publishing is a
separate distribution action after validation, never part of this CI workflow.
