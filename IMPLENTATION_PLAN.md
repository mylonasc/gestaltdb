# Ontological reasoning implementation plan

Review baseline: integration `6e44816`, 2026-10-24. Parent roadmap: #156;
follow-through review: #200. Implementation PRs target
`feature/onto-dev-epic-2`, not `main`. The filename is intentional.

## Findings

The integration CI is green and 102 focused execution/range/session/sampler
tests passed, but adversarial reproductions expose four dispatcher defects in
`src/gestaltdb/reasoning/adapters/execution.py`:

1. An unrelated prepared plan executes instead of the requested program:
   the dispatcher supplies `plan.identity` as its own compatibility check.
2. Admission accepts a generation for another program and an executor different
   from the actual dispatch. Context/profile/dictionary must also agree.
3. Eager `list(facts)` consumes all input before engine bounds/cancellation:
   an input limit of one consumed ten records before failure.
4. Converting `Evidence` to `Fact` loses exact source references in proofs.
   Direct evaluation retained `claim-version-123`; dispatch erased it.

These block wider adoption of #334/#255; direct engine callers are unchanged.

Range publication #337 correctly detects commit movement between staging and
publication. It does not authenticate the computation of caller-supplied triples:
`stage_range` captures provenance after receiving already-derived output.
Document staging freshness separately from derivation-source binding. The docs
also overstate commit metadata: the provenance token currently appears only in
claim provenance. Full authenticated range admission remains #248 scope.

Inferred sampler metadata identifies a caller session, not independently verified
source/closure soundness. Partial expected-generation mappings and directory
checks are not universal identity admission or concurrent atomic publication.

## Ordered implementation

### Native upstream capability re-audit (#194)

Dedicated `feature/re-194-native-capability-audit` / `re-194-native-capability-audit`
on integration `96a9103`. #367 post-merge Ontology `40171463854` / readiness
`40171463642` passed, following 13 green exact-head checks. Refreshed owner/PR
audit found no competing implementation; unrelated work preserved.

A bounded probe of installed PyRex 0.6.0 confirmed that TransactionDB now has
working default-column-family `scan_arrow`/`create_snapshot`, unlike the recorded
0.5.0 audit. Extend explicit raw snapshot/range/lifetime/transaction visibility
gates and version-qualified capability notes; verify named column families are
not silently scanned through the default-family API. Do not promote raw method
availability to authenticated claim-source admission: the latter still rejects
transactional handles. Map original #194 acceptance against delivered evidence
and distinguish genuine remaining requirements from the broader optional
measurement register. No backend/source default, new kernel or release change.

Raw capability gates added for plain/transactional native snapshots, copied
prefix/range rows, mutation isolation, owned/borrowed release, single-use/limits,
uncommitted/committed/rollback visibility and default-vs-named family separation.
Runtime 0.6.0 focused set initially 100 passed / 3 skipped (serializer extras
not requested); 0.5.0 with serializer extras **98 passed / 5 skipped** (expected
unavailable transactional raw contracts, explicit rejection checked). No source
admission change. Original requirement map in `native-extraction-acceptance.md`
separates completed larger measurements/re-audit from genuine general
locator/pushdown gaps and the expanded optional stage register; #194 stays open.

Final audit validation: with serializer extras, focused runtime 0.5.0 **98 passed /
5 skipped**, 0.6.0 **102 passed / 1 skipped** (unsupported/not-applicable raw
contracts labelled). Full optional 0.6.0 suite **3,343 passed / 3 skipped**
(59.75s). Docs/API drift, 8/8 examples, corrected Sphinx `-W`, roadmap and
whitespace gates passed. No authenticated source/executor admission broadened.

### Actual admitted native-source measurement continuation (#194)

Dedicated `feature/re-194-native-evidence` / `re-194-native-evidence` based on
`3ab78ab` (#366 merged after 13 exact-head checks; post-merge watcher active).
Refreshed owner/open-PR audit found no competing implementation. Add separately
named `native-authenticated` real-pipeline method; preserve old assertion-only
benchmark methods and explicitly label their different source contracts. Record
native row/candidate profiles and flushed worker-stage checkpoints so timeouts
remain attributable without presenting partial work as successful timings.
Small exact evidence/analytical recursive closure smoke precedes staged clean
committed-source measurements. Retain canonical point authentication costs,
bounded/timeout evidence and no-adoption decision; do not infer the new source's
performance from older restricted benchmark decoder results.

#366 post-merge gates passed: Ontology `40169585486` / readiness `40169585473`.
Harness extension validation: **29 focused passed**, full optional suite
**3,327 passed / 2 skipped** (67.21s). Planned serial staged collection from a
clean committed tree, output outside checkout: PyRex selective 1K first/primed
three repeats, then a 10K first-read gate (120-second worker limit), then a
100K first-read gate only if the 10K comparison completes. Retain failures and
stop escalation rather than spending a large matrix on a demonstrably slow
correctness baseline. No current measurement or performance result presumed.

Baseline clean-source `c7011bb` collection retained: 12/12 parity successes at
1K first/primed (three repeats), 2/2 at the single-sample 10K first gate, and
one 100K reference success plus a **120s native extraction timeout**. No native
100K timing/closure result is claimed. Native extraction medians 3.290/3.269s
at 1K first/primed versus reference 0.055/0.054s; 10K single samples 35.462s
native versus 0.430s reference. These expose retained per-claim canonical point
revalidation, not native scan throughput. Preserve reports before correcting
that composition: replace N canonical point operations with the existing
grouped canonical point iterator once per pass, still comparing every selected
version to complete admitted native history and verifying the source before/
after. Add omission/duplicate/substitution and bounded-read regression gates,
then repeat the clean-source staged collection; no authentication skip.

Grouped composition implemented: one canonical unfiltered point iterator per
pass, with exact native-history conformance for every result, no missing active
candidate, and no duplicate/old/non-version acceptance. The canonical iterator
retains commit-group integrity checks and successful-exhaustion revalidation;
before/after source verification is unchanged. Bounded-read regression rejects
per-claim get operations and caps metadata reads for 100/200-row single commits.
Validation before new measurements: 95 focused native/harness/counter tests;
full optional suite **3,333 passed / 2 skipped** (56.66s). Repeat the original
staged gates first; only after success collect a three-repeat first/primed 100K
selective/sparse comparison serially. Baseline timeout evidence remains immutable.

Corrected clean `1730fb7` collection completed **40/40 exact-evidence/full
analytical closure parity observations** across 1K/10K/100K gates and repeated
100K selective/sparse first/primed comparisons. No timeout/bound failure in the
corrected cut. Native total 100K medians 16.725/9.589s selective and
17.189/10.555s sparse versus ordinary 10.756/7.402s and 11.609/8.361s. Native
worker RSS remains higher (~992--1020 MB vs 634--662 MB), not query-only memory.
Grouping fixed the composition regression without establishing native benefit.
Original 120s timeout and pre-correction reports retained unchanged. Full
runtime/source/hash/P95/accounting limits in native guide/measurement register.
#194 remains open for explicit locator/support/publication and reconciliation.

Final evidence/correction validation: **3,334 passed / 2 skipped** (67.04s), full
optional backend/serializer/oracle requirements. Executable native example,
docs/API drift, 8/8 existing examples, Sphinx `-W`, roadmap and whitespace checks
passed. Report tests preserve baseline failure attribution and bind all 40
corrected samples to exact source/harness/evidence/closure and physical counters.

### Authenticated native claim source continuation (#194)

Dedicated `feature/re-194-native-admission` / `re-194-native-admission`, based on
integration `4a2c393`. #255 closed after #365's 12 exact-head checks and
post-merge Ontology `40168451602` / readiness `40168451655` passed. No competing
implementation/owner found in refreshed audit; unrelated PRs/worktrees preserved.

Implement an explicit native full-record candidate scan under one temporal
writer guard and authenticated read view. Native bytes must match canonical
commit membership/key/ordinal and complete expected record coverage. Resolve
candidate logical histories with the existing canonical point selector before
policy filtering, including correction/retraction and historical prefixes.
Share existing batched claim policy/context/evidence validation rather than
inventing a second logical mapping contract. Bound raw rows and candidates,
probe plain-PyRex/Arrow capabilities, reject rebinding/corruption, and test
historical parity, movement, cancellation and iterator lifetime. Do not claim
index-locator acceleration, automatic adoption or reduced authentication costs
from a correctness-first full-scan path. Remaining #194 measurement/support/
publication acceptance stays open until independently reconciled.

First implementation pushed at `6e610d5`: explicit `PinnedNativeClaimSource`
and shared batched physical-provider hook; extraction cancellation now flows
through inherited evaluation into native admission too. Canonical point-index
selection is checked against final eligible full-scan records, rejecting omitted
index histories as well as corrupted native streams. Tests cover mixed-kind/
same-commit ordinal winners, correction/retraction/alternative evidence, half-open
points, old prefixes, interrupted descriptors/reservation gaps, four serializers,
restart and multistep bounded witnesses, handle/view rebinding, corruption after
partial output, cancellation/bounds/retry, writer blocking and deterministic
iterator release. Physical profiles explicitly report no filter pushdown.
PyRex 0.6.0 exposes transactional scan methods, but transaction source admission
remains deliberately rejected pending a separate conformance/capability audit;
this API admits only the explicit plain-PyRex path. No new performance claim.

Final native-admission validation: **3,322 passed / 2 skipped** (58.46s), full
optional backends/serializers/RDF/Polars/docs/bloom and ontology requirements.
Executable native guide, 8/8 existing examples, docs/API drift, Sphinx `-W`,
roadmap and whitespace gates passed. #194 remains open for remaining measured
native locator/support/publication scope and original-acceptance reconciliation.

### Explicit-budget pipeline evidence continuation (#255)

Dedicated `feature/re-255-pipeline-evidence` / `re-255-pipeline-evidence`, based
on `1860e41` (#364). Post-merge Ontology `40166592538` and readiness
`40166592546` passed. Ownership/open-PR audit found no competing work.
The existing real-pipeline harness now forwards an explicit physical Polars
intermediate cap only to eligible workers, records requested/effective bounds
and separately prefixed native metrics, and emits schema version 2. Historical
#342 reports remain immutable and retain their measured harness hash.

Validation before measurement: 22 focused harness tests passed; full optional
suite **3,262 passed / 2 skipped** (56.24s). Collect reports outside the checkout
from a clean committed tree: 100K selective/sparse, LMDB/PyRex, indexed Python
versus explicit Polars, first/warm, three repeats, serial workers, 1,000,000-row
intermediate cap. Require exact evidence and analytical complete-closure parity;
retain bounded/error/timeout diagnostics. No default/adoption/source-policy change.

Collection completed: four clean-source reports at `b87ef1c`, 48/48 successful
exact-evidence/analytical-complete-closure parity observations. No bounded/error/
timeout results in this continuation; historical #342 diagnostics unchanged.
Polars median total pipeline improvement about 3--17%, with conversion/output
decode dominating native fixpoint time; no consistent RSS benefit. Conservative
native peaks 100028 selective / 200019 sparse rows; the chosen cap is physical,
not a hard memory guarantee or logical identity. Full median/P95/runtime/RSS
boundaries recorded in `docs/reasoning-frontier-benchmarks.rst` and immutable
reports. Original #255 acceptance mapped in `executor-admission.md`; indexed
default unchanged, #194 and broader roadmap gates remain separate.

Final evidence validation: **3,263 passed / 2 skipped** (57.24s), all optional
backend/oracle requirements; report-matrix invariants included. Sphinx `-W`,
docs/API drift, 8/8 examples, roadmap and whitespace checks passed. Closure of
#255 remains gated on exact-head PR and post-merge integration checks.

PR #365 first CI run exposed a host assumption in the new end-to-end harness
test: GitHub runners lack `/tmp/opencode`. The orchestrator now prefers that
root when present and otherwise uses the platform tempfile root, with a
simulated absent-root regression. This changes only fixture placement; measured
reports retain their original clean source/hash. Initial watcher also expected
an inapplicable fuzz workflow; monitor only actual applicable workflows.

Portable-temp correction validation: 24 focused harness tests; full optional
suite **3,264 passed / 2 skipped** (84.19s). Measured report bytes unchanged.

### Broader roadmap continuation (2026-10-25)

Child #350 of #249 delivers the current-property-graph demand path on
`feature/re-350-current-frontier`, worktree `re-350-current-frontier`, based on
accepted integration `9f287c6`. Reuse the bounded frontier scheduler and full
`GraphMapping` projection/evidence convention under one authenticated mutable
snapshot. Label/property/type/adjacency reads follow bindings; unindexed
properties use reported complete scans. Stale/unverifiable sources fail closed.
Current identity stays separate from temporal-prefix identity. No default-mode
changes or silent persistent cache/publication admission.

Parent #249 closure requires an acceptance map across temporal predicate/alias/
frontier work and this mutable projection, plus parity/measurement evidence.
Typed/negation/equality execution is outside this positive binary demand cut.
The #342 authentication bottleneck is not solved by skipping verification.

Validation: 2,790 tests passed / 1 skipped, documentation drift and 8/8 examples,
Sphinx with warnings as errors, roadmap and diff checks passed. Clean-source
`c340af4` reports at 10K/100K nodes contain 12 parity-verified observations.
100K median total: 6.478s full projection vs 1.015s frontier; candidate reads
199,999 vs 9. Full-state authentication remains outside candidate budgets;
synthetic selective evidence does not justify default executor changes.

### Current-state admission continuation (#352)

Baseline `b809af5` includes #351 and closes #249 after exact-head and post-merge
green. #352, branch `feature/re-352-current-admission` / worktree
`re-352-current-admission`, advances #248 and coordinates #255: add a distinct
storage-neutral mutable snapshot source identity without changing temporal
generation digests. Authenticate complete current-record projection, bind the
actual program/catalog/mapping/profile/dictionary, compare optional exact pins
before record extraction, and invoke the existing explicit executor pipeline.
Never admit partial frontier output as closure or invent temporal horizons for
mutable sequences. Full canonical projection needs no secondary indexes.

Metadata binding enables plan preparation but is not a closure certificate;
execution independently authenticates and projects its input. Latest-source
checks reject movement; exact active-view checks permit isolated old reads but
cannot reopen expired snapshots. Persistent cache/range/session/sampler
migration and publication remain separate #248 scope. Cancellation must also
cover unmapped canonical records, which may yield no evidence to the dispatcher.
Admission audit also requires backend/serializer provenance fields to match the
snapshot graph manifest independently and pins the decoder object through reads;
matching a copied provenance token alone must not admit forged representation
metadata or a decoder rebound after authentication.

Validation for #352: 2,830 passed / 1 skipped with optional backends, serializers,
RDF, Polars and independent reasoning oracles; docs drift, 8/8 examples, Sphinx
with warnings as errors, roadmap and diff gates passed. Regressions cover exact
pin dimensions before record extraction, catalog content/version differences,
temporal digest stability and source-kind isolation, real indexed/SCC/Polars
closure parity, exact bounded witness references, active-old/latest/expired
views, forged representation metadata, decoder rebinding, unmapped-record
cancellation and stale-index-independent complete projection. This is an
admission/integration result, not new executor performance evidence.

### Range identity and re-admission continuation (#354)

Baseline `b93254a` includes accepted #353/#352. On
`feature/re-354-range-admission` / `re-354-range-admission`, advance #248 with
a distinct finite temporal-range source identity, preserving both existing
point and current generation digests. Complete historical validity cuts include
corrections/retractions; canonical point reads at every segment resolve active
positive entity versions under one authenticated prefix. Preserve aliases,
independent version evidence and disjoint clipped intervals; never seed-only
or min/max envelope filtering.

Re-admission must independently re-extract exact source evidence and compare
fresh complete closure (including derivations), then run the conformance gate.
The existing gate checks point closure but alone is not an independent check
of every rule application, so matching metadata or calling only that gate is
insufficient to admit arbitrary caller supports. Bounds and cancellation cover
yielded history, repeated point candidates, retained evidence/segments/supports;
authentication/internal backend work stays explicitly outside those counters.

This is authenticated range computation/re-admission, not publication migration.
Raw `stage_range` still authenticates staging only. The next consumer must bind
computed interval outputs and preserve source freshness plus target CAS; a
multi-interval batch needs an explicit atomicity contract rather than silently
publishing first and accepting later intervals after its source horizon moves.
Persistent session/sampler/cache consumers remain separate #248 follow-through.

Validation: 2,923 passed / 1 skipped with optional backends/serializers/RDF/
Polars/docs and independent reasoning oracles; docs drift, 8/8 examples, executable
range guide, Sphinx with warnings as errors, roadmap and diff checks pass.
Independent fixture grounding checks every microsecond across correction,
retraction, overlap and disjoint gaps on LMDB/LevelDB/PyRex; re-admission rejects
omitted equal-fact versions, altered intervals, base/segment/support/program
tampering, source-kind drift and moved prefixes. No performance claim.

Explicit remaining #248 consumers: #355 authenticated range computation to
guarded interval publication (blocked by #354), #356 authenticated durable
session resume/advance, #357 identity alignment for existing inferred sampler
artifacts (not sampling-roadmap expansion). Source-independent
`SpecializationCache` needs no source invalidation; the adapter re-admission
path handles supplied range closures rather than inventing a phantom cache.

### Authenticated interval publication continuation (#355)

Baseline `9a4767f` includes #358/#354 after all exact-head and post-merge gates.
Branch `feature/re-355-range-publication`, worktree `re-355-range-publication`.
The new consumer owns range extraction/evaluation, then independently re-admits
source evidence and exact closure under the temporal writer guard before one
logical commit containing every interval assertion/correction/retraction.
Latest source checks remain separate from explicit historical mode and target
CAS. Exact managed history/tombstones prevent an empty-target ABA bypass.

Discover historical interval namespaces, not only current closure segments, so
removed intervals are actually retracted. Fact-specific source namespaces
preserve disjoint validity as distinct logical IDs; partial external validity
changes fail closed. Validate typed output as binary entities before writes.
Bind content to exact evidence/program/catalog/policy/output rather than the
horizon alone: unchanged output after our own prior commit remains idempotent
and retains truthful old provenance. Claims/new commits carry exact generation
metadata; interrupted nontransactional publication remains caller-recovered.

Raw caller-derived `stage_range` and point publication remain unchanged.
This correctness-first path intentionally re-evaluates under the guard; it
makes no short-lock-duration/performance or independent engine-proof claim.
#356 durable sessions and #357 existing inferred-artifact identity remain open.

Validation: 2,974 passed / 2 skipped with optional backends/serializers/RDF/
Polars/docs and independent reasoning oracles. Docs drift, 8/8 examples,
executable publication guide, Sphinx with warnings as errors, roadmap and diff
checks passed. Cross-backend checks cover one-commit intervals and pinned old
reads, exact claim/commit metadata, idempotent restaging, vanished intervals,
stale-computation laundering, target CAS and empty-state ABA, malformed target
validity, altered evidence/output, bounded no-write and late cancellation,
writer exclusion through commit, historical reentrant movement, pre/post-visible
commit interruption and explicit nontransactional orphan recovery.

### Durable-session consumer continuation (#356)

Final local validation: **3,044 passed / 2 skipped** (47.35s), with backends,
serializers, RDF, Polars, docs, bloom and independent ontology requirements.
Documentation drift, all 8 executable examples, Sphinx warnings-as-errors,
roadmap consistency and diff whitespace gates passed. This includes authenticated
cross-process restarts and exact-state contention/fault regressions.

Baseline `134378c` includes #359/#355; all exact-head and post-merge gates passed.
Branch `feature/re-356-session-admission`, worktree `re-356-session-admission`.
Connect existing v1 files to typed-point source admission with exact caller
generation AND session-state pins. Explicit historical input admission permits
advancement of old sessions; the new computed session must always authenticate
against latest source under the database writer guard before persistence.

Persistence audit: horizon-only comparisons miss same-horizon state changes;
double reads without mutual exclusion leave a lost-update window, and a PID-only
temporary filename can collide between threads. Cooperating writers now share
the existing cross-process lock primitive, use unique temporary files and exact
state CAS, retain v1 bytes/digests, and actually attempt directory fsync where
supported. The legacy resume path gains exact-state write comparison but is not
silently advertised as source-authenticated. No direct filesystem/hardlink/
hostile-path or universal network-filesystem guarantee is implied.

Source admission remains separate from internal checksum/index consistency and
from independent closure soundness. No new result cache is invented;
source-independent specialization needs no source invalidation. #357 is the
remaining existing inferred-artifact consumer before #248 acceptance review.

### Existing inferred-artifact consumer (#357)

Baseline `458f039` integrates #360/#356; all 13 exact-head checks and post-merge
Ontology `40145400390` / readiness `40145400411` passed. #356 is closed.
Branch/worktree `feature/re-357-inferred-admission` / `re-357-inferred-admission`.

Connect only the existing inferred sampler consumer to typed-point admission:
exact caller shared generation and session-state pins, independently admitted
source (including independent versions), distinct canonical projection selection,
and versioned complete source/program/catalog/scope/profile/representation/state
metadata. Keep legacy partial-mapping builder behavior explicitly unverified.

Hydration independently reconstructs the full expected artifact and compares
every byte/file (metadata, completion, topology, lookup, CSR and policy arrays),
not merely stored identity/checksums. Self-consistent modified arrays cannot
pass. This expensive complete-serialization comparison may require explicit
rebuild for future encoding changes; no silent migration or fast-cache claim.
New-path staging, cooperating locks, source recheck, cancellation and rename
failure preserve existing artifacts. No source-authentication-as-closure-proof
claim, new sampler algorithm, automatic executor change or publication.

Focused validation initially 115 passed, including backend parity, generation
pin mismatch matrix for build AND hydration, cross-process restart, projection
selection, alternative evidence, rewritten-checksum tampering, cardinality/byte
bounds, competing writers and cancellation/fault cleanup. Full validation and
original #248 acceptance reconciliation follow before closure.

Final validation at `f6475ea`: **3,159 passed / 2 skipped** with all optional
backends/serializers/RDF/Polars/docs/bloom and independent ontology requirements.
Docs drift, all 8 examples, executable Sphinx guide, Sphinx `-W`, roadmap and
diff checks passed. Authenticated inferred tests: 115 passed across the three
backends, plus all legacy-consumer regressions in the full suite.

### Original generation-admission acceptance review (#248)

Branch/worktree `feature/re-248-acceptance` / `re-248-acceptance`, from integration
`6b51545` (#361 exact-head green and merged; post-merge watcher active).
Reconciled the original acceptance rather than treating child closures as proof:
all identity dimensions, historical/latest semantics, inward-only core,
pre-write mismatch evidence, and existing point/current/range/publication/durable/
inferred consumers now have an explicit evidence map in
`planning/reasoning-followthrough/generation-contract.md`.

Cache audit distinguishes source-independent specialized programs and pure
logical segment memoization from durable source-bound state; low-level catalog
pins and GraphDB decode integrity are not silently promoted to authentication.
No generic persistent closure cache was found or invented. Legacy entrypoints
retain explicit compatibility limits. #255, #194 and broader phase acceptance
are not closed by this reconciliation. Runtime code unchanged; final review and
post-merge gates are still required before closing #248.

Reconciliation validation: **3,159 passed / 2 skipped** (61.41s), with all
optional backends/serializers/RDF/Polars/docs/bloom and ontology requirements;
docs drift, 8/8 examples, Sphinx `-W`, roadmap and whitespace checks passed.
All 13 referenced acceptance-test paths were checked to exist. Runtime unchanged.

### Explicit application pipeline intermediate budget (#255)

Branch/worktree `feature/re-255-execution-budget` / `re-255-execution-budget`,
from integration `a57f166` (#362 acceptance reconciliation exact-head green and
merged; post-merge watcher active). Refreshed issue/PR ownership before starting.

#362 post-merge gates passed: Ontology `40146769957` / readiness `40146769959`;
#248 closed against the explicit original acceptance map, not child count.

Reproduced the measured adoption gap: the prototype's existing configurable
intermediate-row bound was silently fixed at 100,000 by the real dispatcher.
Expose explicit `polars_max_intermediate_rows` through both dispatcher and
authenticated current-graph consumer, preserving defaults. Reject malformed or
irrelevant options before input/projection; never auto-select or recode. Keep
physical budget outside shared logical identity. Retain native `PolarsRun` in
successful outcomes with chosen cap/observed counters, separate from unchanged
common stage telemetry and indexed candidate-accounting units.

Initial focused validation: 101 passed, including >100K complete reference parity,
same logical generation across physical budgets, independent cumulative/fact
limits, and invalid/irrelevant option rejection before reads. No performance or
hard RSS claim; original benchmark failures stay recorded. Broader typed/common
pipeline integration and #194 native historical/concurrent-source admission
remain open, not presumed delivered by configuring one physical bound.

Final physical-budget validation: **3,176 passed / 2 skipped** (57.08s), with
optional backends/serializers/RDF/Polars/docs/bloom and ontology requirements.
Docs drift, 8/8 examples, executable executor guide, Sphinx `-W`, roadmap and
whitespace checks passed. Default cap and indexed executor selection unchanged.

### Positive typed application pipeline (#255)

Branch/worktree `feature/re-255-typed-pipeline` / `re-255-typed-pipeline`, based
on integration `694b5dc` (#363 exact-head green and merged; post-merge Ontology
`40165662298` / readiness `40165662268` passed). Owner/open-PR audit refreshed.
Connect the existing GenericEngine to central ExecutorRequest selection, exact
shared generation admission and common StageTelemetry under a distinct typed
executor identity. Binary defaults stay unchanged; unsupported typed proof,
SCC/Polars and negative/aggregate/closed-scope combinations fail closed rather
than recode or silently weaken semantics.

Actual application consumer independently re-admits exact caller source/state
pins, retains independent active assertion versions, executes only complete base
input, compares fresh complete closure to supplied session state, and rechecks
source/decoder under the writer guard. Internally consistent forged closure is
not trusted as input. No supports/proofs/publication/durable writes claimed;
same-engine recomputation is not independent verification. Original typed-point
source policy remains positive entity claims; literal/n-ary derived values stay
typed, with arbitrary typed source values available only through the raw API.

Initial focused suite: 250 passed before expanded fault/negative-mode tests.
Full validation and executable docs follow. Broader #255/#194 acceptance remains
open pending its own capability/measurement reconciliation.

Final typed-pipeline validation: **3,251 passed / 2 skipped** (54.26s) with all
optional backend/serializer/RDF/Polars/docs/bloom and ontology requirements.
Docs drift, 8/8 examples, both executable typed examples, Sphinx `-W`, roadmap
and whitespace gates passed. Focused compatibility/integration set: 264 passed.
Typed generation admission explicitly rejects current/range source identities,
not just mismatched profile/dictionary strings. Point/session digests unchanged.

### Prior ordered follow-through

Native GitHub links established: #338 (identity), #339 (input bounds), #340
(evidence) are children of #255. #341 (pinned frontier) is a child of #249 and
blocked by all three fixes. #342 (measured crossover) is a child of #194 and
blocked by #341; it also coordinates #255 and RE-26/#187.
Documentation correction #347 is a separate child of #248: range staging
freshness does not authenticate the caller's derivation, and provenance tokens
are currently claim metadata, not commit metadata.

Dispatcher fixes merged via #343/#344/#345, each on its own issue-numbered
branch/worktree. Combined local validation: 2729 passed, one optional fault
injection skip; docs/examples/Sphinx/roadmap gates passed. Source compatibility
checks do not authenticate caller facts, and proofs remain bounded witnesses.

The #341 implementation uses an explicit storage-neutral tabled scheduler over
binary `Program`/`Atom`, reusing reference indexed lookup/unification. Its claim
adapter preserves one authenticated temporal view. This is intentionally a
different API from n-ary magic-set evaluation; no implicit fragment downgrade.
Its scheduler work counters include repeated rule/prefix visits and cannot be
compared directly with full-closure attempted-unification counters.

### 1. Trustworthy executor dispatch (#255, #248)

Create separate issue-numbered fix branches/worktrees for identity preflight,
bounded/cancellable input consumption, and exact evidence preservation.

- Compare the actual program, context, semantic profile, dictionary and selected
  executor with generation/admission/plan identities before consuming input.
- Preserve `Evidence` through witness-capable engines. Closure-only conversion
  must be explicit and must not imply proof/source authentication.
- Consume at most `max_input_facts + 1` inputs, checking cancellation between
  bounded units; invalid configurations must consume none.
- Regressions: unrelated plans/programs, mismatched context/profile/dictionary,
  admission versus dispatch, duplicate evidence, proof parity, oversized input,
  cancellation and single-pass generators. Keep reference defaults unchanged.

### 2. Pinned frontier-demand evaluation (#249, #248)

Deliver an opt-in positive-entity demand API interleaving binding discovery,
indexed extraction and evaluation under one authenticated read view. Keep core
and compiler storage-independent. Start with a complete documented fragment,
not initial-seed filtering masquerading as recursive demand execution.

- Preserve asserted inputs for headed predicates and exact evidence references.
- Invert logical aliases to storage predicates; follow newly discovered bindings.
- Deduplicate reads/evidence, enforce cumulative read/work/output budgets and
  cancellation, and terminate on a finite monotone request/fact fixed point.
- Reject unsupported combinations before reads (or expose a separately selected
  complete predicate-read strategy; never silently return partial answers).
- Require full-closure answer parity for recursion, cycles, diamonds, repeated
  variables, constants, disconnected joins, aliases and headed assertions.
- Compare scanned/decoded rows and whole-pipeline timings with the existing
  predicate-selective baseline; do not claim universal speedups.

### 3. Measured executor/native optimization (#194, #255, RE-26)

Implemented measurement cut #342 / PR #349 in `re-342-pipeline-crossover`,
branch `feature/re-342-pipeline-crossover`. Clean measured source `a1cfceb`:
six final reports, 126 parity-verified results plus 18 bounded Polars outcomes;
see `docs/reasoning-frontier-benchmarks.rst`. Unexpected failures are not
successful samples. Decision: no automatic executor/native adoption. Repeated
authentication outweighs frontier row pruning on these fixtures; prepared
execution ties reference; native primed gains do not offset first-read/RSS and
restricted-fixture admission costs. Production bounds/defaults unchanged.

After correctness and a real demand consumer, benchmark the actual pipeline:
reference indexed, prepared indexed, explicitly selected Polars where supported,
and existing decoded/locator native extraction. Native decoding already exists;
its outstanding work is broader measurement, not a new decoder rewrite.

- Record exact revision, runtime/dependencies, cold/warm conditions, variable
  identifiers/values, 100K+ input rows, sparse/dense/selective workloads, conversion,
  extraction, fixpoint and total costs, RSS, output parity and work counters.
- Keep proof and closure modes separate; never equate different work counters.
- Adopt an optimization only with parity and reproducible crossover evidence.
  Retain explicit opt-in selection and no bespoke runtime by default.
- If no candidate wins, record a measured no-adoption decision rather than
  manufacturing an optimization or claiming native/scale certification.

## Workflow and exit gates

Create native GitHub parent/sub-issue and blocked-by links as well as textual
dependencies. Each fix/feature gets a dedicated issue-indexed worktree/branch;
create worktrees when implementation starts, not as empty future reservations.
Check issue ownership/activity first. Preserve unrelated worktrees and branches.

For each coherent change: focused regressions, applicable full-suite/backend
tests, docs drift/examples/Sphinx and roadmap validation; record exact evidence
and limitations. Open integration-target PRs, merge only exact-head green with
combined-base validation, then monitor post-merge CI automatically. Close issues
only against their acceptance evidence. No main merge, version bump or publication
is part of this implementation plan.

Alternatives (universal identity migration, sampler alignment, tutorials and a
release) remain useful but do not precede repairing the reproduced dispatcher
defects. Demand pushdown is the next feature because it makes selective ontology
queries practical without requiring another runtime.

### Tracking sanitation (#156)

Dedicated `feature/re-156-tracking-sanitation` on integration `2ed6ade` (#368).
No competing implementation; unrelated work preserved.

Sanitation corrects stale GitHub/roadmap display without closing genuine gates
by child count: close completed RE-E1 #157, keep RE-E2 closed, keep RE-E3/E4
open with explicit evidence gate #370 (sub-issue of #159) and RE-E5 sequentially
blocked by #160, keep #200 open with D2-D6 disposition #371, remove stale #194
blockers from closed #249/#254/#255, and correct the published `v0.13.0rc1`
release record. Offline snapshot `planning/reasoning-engine/tracking-status.json`
plus `check_tracking.py` (`--github` drift check) pins expected states and native
sub-issue/blocked-by edges; `tracking-audit.md` records evidence and remaining
checklists. Roadmap feature DAG and source hashes unchanged; no new engine,
main merge, tag or release authorized.
