# Reasoning testing-release readiness

Priority: ship a bounded, honest reasoning milestone for external testing soon,
not the entire enhancement backlog. Parent: #156; policy:
[`EPIC_REASONING_ENGINE.md`](../../EPIC_REASONING_ENGINE.md).

This is a release decision register, not a claim that the candidate is ready.
Initial audited baseline: `4789f94` (2026-10-23, after PR #305). Any main/release
decision must identify its exact SHA and rerun applicable gates after changes.
Proposed version: **0.13.0rc1**, pending release preparation; current metadata
still says 0.12.1 and must not be published as the reasoning milestone.

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
