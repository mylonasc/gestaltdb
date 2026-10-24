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

Native GitHub links established: #338 (identity), #339 (input bounds), #340
(evidence) are children of #255. #341 (pinned frontier) is a child of #249 and
blocked by all three fixes. #342 (measured crossover) is a child of #194 and
blocked by #341; it also coordinates #255 and RE-26/#187.

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
