# Reasoning testing-release readiness

Priority: ship a bounded, honest reasoning milestone for external testing soon,
not the entire enhancement backlog. Parent: #156; policy:
[`EPIC_REASONING_ENGINE.md`](../../EPIC_REASONING_ENGINE.md).

## Current disposition (2026-10-25)

**0.13.0rc1 was released on 2026-10-23.** Main-target #307 merged at
`1d9caad7deb4b6efdda90f8375fb3c794bb567f6`; trusted Publish `39936450890`
succeeded on that SHA. The GitHub release is a prerelease, and #156 records
installed-distribution smoke evidence. This file is not an unfulfilled rc1
publication checklist. The historical preparation baseline was `4789f94`.

Post-release integration at `2ed6ade` contains additional accepted work, not a
newly released artifact. Future main/release decisions require a fresh exact-SHA
gate; this sanitation authorizes neither. Phase evidence remains a separate
decision: E1/E2 complete, E3/E4 gated by #370, E5 sequentially gated by E4;
review D2–D6 disposition remains #371. See [tracking-audit.md](tracking-audit.md).

## Blocker triage

| Scope | Current evidence / release treatment |
|---|---|
| #200 T1–T6, E1–E2: typed semantics, aggregate limits, extraction restrictions/identity | Fixes #207–#212/#214–#215 are closed and merged (#223–#228/#230/#232). Regressions are discovered by the reasoning CI glob; validate again in the full candidate matrix. |
| #200 T7/E3/D1: misleading traces, counters, independent negation oracle | #213/#216/#217 closed and merged (#229/#233/#234). Blocker-only traces are diagnostics, not absence proofs. |
| #244–#247: range construction, changed-program reuse, publication freshness, CI discovery | Closed; fixes are in integration (including #258/#259/#271). Keep range, revision, freshness and workflow-selection regressions in the release gate. |
| #275/#277/#282: temporal commit reads, duplicate detection, cache corruption/mutability | Closed and merged (#276/#278/#283). Integrity regression coverage must remain enabled; do not substitute performance-cache trust for storage verification. |
| #200 D2/D3/D6: duplicated plumbing, validation indexing/cancellation/provenance | Refactor work is deferred, not a reason for an engine rewrite. Release docs must disclose full validation input buffering, cooperative cancellation boundaries and structural labels rather than exact claim-version provenance. |
| #200 D4/D5: schema reuse and negative-premise delivery accounting | Release only the implemented subset: compilation memoization, ground-denial validation, scoped absence and strict literal values. Reusable TBox closure, signed-denial rule premises and datatype shape validation are not delivered by those mechanisms. |
| #248: shared authenticated identity and existing consumers | Closed after #362 acceptance reconciliation and green integration checks. Point/current/range admission, publication, durable-session and inferred-artifact consumers retain distinct contracts; legacy APIs are not silently authenticated. See `planning/reasoning-followthrough/generation-contract.md`. |
| #250/#251: evidence ledger and incremental sessions | Closed scoped implementations #264/#265, extended by authenticated durable resume #360. Run exact-evidence, session and fresh-recomputation regressions. Not every rule combination supports incremental advance; unsupported modes require explicit documented rebuild/failure. |
| #249/#252/#253/#254: demand/frontier/support/sweep performance | All closed against scoped acceptance. Pinned frontier/current-source reads, support indexing and event sweeps are integrated, not merely planned. Broader optimizer/autoadoption ideas remain distinct from those closures. |
| #255/#256: executor and inferred-consumer composition | Both closed. #255 completed capability/conversion/identity/telemetry/application integration and measured explicit-budget pipelines via #363–#365. #256 and later authenticated inferred consumers are integrated. No universal engine interchangeability or transparent rewriting is implied. |
| #194 | Open native follow-up: full-record admission #366, grouped correction/40 parity observations #367 and raw PyRex audit #368 are integrated; historical locator PR #369 is separate and unmerged in this audit. Earlier failures remain retained. No automatic native adoption; old draft #262 is not current work. |
| #370 / #371 | Explicit remaining phase durability evidence and original review design/accounting dispositions. These replace vague, unowned “broader gates remain” notes; they do not authorize a new feature/release cycle. |
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
- The preceding entries are historical preparation snapshots, not current state.
  #307 subsequently passed its final checks, merged at `1d9caad`, main push
  checks passed, and trusted Publish `39936450890` succeeded. Release
  `v0.13.0rc1` was published at 2026-10-23T10:04:16Z. #156's release comment
  records isolated installation, demand parity and packaged-doc smoke results.

| Phase | Existing acceptance evidence inspected | Remaining decision |
|---|---|---|
| #157 / RE-E1 | Baseline/oracle/plans/Polars/executor/extraction suites and reports, explicit indexed-default/limits/cancellation/install isolation; #365 adds 48 exact-parity 100K application observations. | Complete: stale-open epic corrected. #194 is an optional follow-up, not an E1 exit requirement. No universal scale certification is claimed. |
| #158 / RE-E2 | Closed after prior verification; typed/aggregate/negation/oracle/demand/equality/validation suites rerun in #306. | Preserve narrower validation/provenance and combination boundaries documented in this release; do not reopen scope merely to implement every construct. |
| #159 / RE-E3 | Maintenance/deletion/catalog/session/publication gates plus later authenticated file CAS, cross-process restart/writer allocation and fsync tests. | Open: #370 maps exact supported crash/concurrency claims and remaining process-termination evidence. Exception/cancellation injection is not blanket physical durability proof. |
| #160 / RE-E4 | Complete ranges, support intervals, correction/retraction, conformance and bound range-publication source/target-history CAS. | Open: #159 and shared #370 crash-safe interval-publication evidence. |
| #161 / RE-E5 | Profile/EL/scope/scale/proof tests, named subsets, bounded experiments and explicit defers. | Own bounded/defer deliverables reviewed; original sequential prerequisite #160 remains open. Full OWL DL, probabilistic calculi, spill/native/differential runtimes are not secretly missing mandatory implementation. |

The open phase epics are not automatically release blockers for a clearly
bounded testing slice. Any advertised stronger durability or composition
guarantee would require its missing evidence first. This register does not
close those epics or supersede their broader acceptance criteria.

## Published rc1 versus any future candidate

- [x] rc1 main-target integration and applicable exact-merge push checks:
  #307 / `1d9caad` (historical release evidence recorded in #156).
- [x] rc1 trusted publication: Publish `39936450890`, release `v0.13.0rc1`.
- [x] Published-distribution installation/demand/packaged-doc smoke recorded in
  #156. These are observations about rc1, not later integration commits.
- [ ] Phase #159/#160 durable-exit reconciliation: #370; independent of the
  already-published bounded testing release.
- [ ] Original review design/delivery-accounting dispositions: #371.

No next candidate is approved here. Any future stable/prerelease promotion must
name a fresh SHA, re-triage advertised workflows, run the supported-Python full
suite, docs/examples, wheel/sdist/compatibility and optional-backend/install
isolation gates, pass applicable main-target and exact merged-main checks,
and receive explicit release authorization. Existing general, security,
visualization and dependency checks still apply. A historical green job or
published rc1 does not certify a new integration snapshot.
