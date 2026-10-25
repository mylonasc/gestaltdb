# GitHub/roadmap sanitation — 2026-10-25

Reviewed integration: `2ed6ade764d6af4d27a3fab52bc3fc3d5f5c41e5` (PR #368).
This is a tracking/evidence audit, not new feature delivery or release approval.
Original workbook, source assessment, baseline hashes, feature IDs and dependency
DAG are preserved. PR #369 is a separate, unmerged native-locator implementation;
its green checks do not count as integrated delivery in this audit.

## Why completed dependencies did not imply completed epics

GitHub does not automatically close a parent when its children/dependencies
close. The original 27 feature issues are all closed and `roadmap.json` records
their integration, but phase exit gates are separate. The status failure was a
mixture of stale closure, unrecorded acceptance work and old narrative text:

| Phase | Audited disposition | Evidence / remaining gate |
|---|---|---|
| RE-E1 #157 | Closed as completed | #162–#167 integrated; #157's own delivery comment already declared exit at `79e4369`. Baseline/oracle parity, prepared/Polars reports, explicit indexed-default decision, resource/cancellation and installed-artifact gates exist. Later #365 retains 48 exact-parity 100K application observations; #194's optional native follow-up is explicitly not an E1 blocker. |
| RE-E2 #158 | Remains closed | Existing acceptance review at `131f2ce`, selected-profile/demand/validation/equality examples and tests. Closing stale #157 removes its only open native blocker; the edge remains as historical prerequisite. Narrow profile coverage is not signed-premise or datatype-shape delivery. |
| RE-E3 #159 | Open: concrete evidence gate #370 | Maintenance/DRed, source-bound generations, cooperating writer/file CAS, process restart and guarded publication are implemented. Cancellation/exception injection is not abrupt process-death/power-loss evidence for every publication boundary/backend. Reconcile the actual supported crash/concurrency guarantee in #370 instead of leaving an unexplained open epic or claiming universal durability. |
| RE-E4 #160 | Open: blocked by #159 (transitively #370) | Complete positive range closure, evidence intervals, correction/retraction and bound range-publication/history CAS exist. Its crash-safe bounded-publication exit shares #370; do not equate logical commit visibility with an unqualified physical-crash guarantee. |
| RE-E5 #161 | Own bounded delivery/defer gate reviewed; sequentially blocked by #160 | Named profile matrix, bounded EL, context/calculus approval gates, scale no-adoption decisions and proof/checkpoint contracts are delivered. Full OWL DL, spill, differential runtimes and probabilistic semantics are explicit non-deliveries/defers, not hidden implementation tasks. Preserve the original phase prerequisite rather than closing out of order. |

Relevant E3/E4 evidence: `docs/reasoning-authenticated-sessions.rst`,
`docs/reasoning-bound-range-publication.rst`,
`tests/test_reasoning_authenticated_sessions.py` (cross-process exact-state CAS,
restart and fsync/failure behavior), `tests/test_temporal_commit_allocation.py`,
`tests/test_reasoning_workload_gates.py`, and the range/publication regressions.
The old release register already retained this broader evidence gap; this audit
does not invent a new universal storage requirement or silently approve a defer.

## Review umbrella and original delivery accounting

#200 stays open, but its current remaining scope is now an explicit child #371:
per-finding disposition of D2–D6. T1–T7/E1–E3/D1 fixes and #244–#256 follow-ups
are closed. In particular #248, #249, #254 and #255 are no longer merely planned
or pending. Their original acceptance closure is distinct from completing every
optional optimizer/refactoring idea mentioned by a review comment.

D4 still requires honest accounting of compilation-output memoization versus
reusable TBox closure; D5 distinguishes ground-denial validation, signed rule
premises, closed-world absence, strict literal values and datatype shape checks.
D2/D3/D6 need explicit delivered/deferred dispositions for plumbing, policy
ownership and validation costs/provenance. #371 tracks reconciliation/approved
defers or targeted implementation, not an authorized rewrite. RE-E2's narrower
verified phase contract remains intact; broader claims must not be inferred
from its closed state or original copied feature checklists.

## Native graph repairs

- #156 visibly owns phase epics plus the supplemental #194 and #200 follow-ups.
- #159 owns evidence gate #370 as an open sub-issue; #160 remains blocked by
  #159 (transitively covering #370), and #161 retains #160. #200 owns design
  disposition #371 as an open sub-issue. Open sub-issues, not parent-blocked-by-child
  edges, carry the remaining work so the native graph stays acyclic.
- Remove #194 as a whole-issue blocker of already-completed #249/#254/#255.
  The measurement/extraction prerequisites needed by their accepted scopes have
  been delivered; continuing #194 is not a reason to show these closed issues as
  blocked. Their historical bodies/comments are preserved with a current note.
- Preserve original RE feature prerequisites and completed dependency edges;
  closing an issue does not require deleting its historical DAG.
- #194 stays open. #366/#367 full-record admission and measured no-adoption
  decision, #368 raw capability re-audit, and unmerged #369 locator work are
  separate delivery levels. Existing failure reports remain unchanged.
- Unrelated security, sampling, tutorials, tooling and Dependabot work is not
  closed or reparented by this reasoning audit.

## Release-register correction

`v0.13.0rc1` **was published on 2026-10-23**, not a future candidate. Main-target
PR #307 merged as `1d9caad7deb4b6efdda90f8375fb3c794bb567f6`; trusted Publish
run `39936450890` succeeded on that SHA. The release is marked prerelease.
#156's existing release comment records installed-distribution smoke evidence.
Those historical gates apply to that published cut, not post-release integration
changes. Open phase evidence work did not block that expressly bounded testing
release; no stable release, new tag or new main merge is authorized here.

## Verification and drift prevention

Re-read 44 reasoning issue states and their native sub-issue/dependency lists,
all five phase bodies/recent delivery comments and the original review findings;
also inventoried all 182 repository issues to avoid sweeping unrelated work.
On the reviewed integration, **224 focused tests passed** covering E1 execution/
plans/Polars/extraction, durable sessions/writer allocation/workload gates and E5
profile/EL/scopes/scale/proofs. This is not an OS-kill or hardware durability test.

`tracking-status.json` is the dated, explicit status/relationship snapshot.
`check_tracking.py` validates it offline; `--github` reads current native issue
states/edges and reports drift without modifying or closing anything. The
historical `roadmap.json` feature plan is not rewritten as a live GitHub cache.
