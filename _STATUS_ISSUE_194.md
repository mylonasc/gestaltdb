# Issue #194 — working status (2026-11-01)

- Issue: https://github.com/mylonasc/gestaltdb/issues/194
- Draft PR: https://github.com/mylonasc/gestaltdb/pull/450
- Branch: `feature/194-native-locator-benchmarks`
- Worktree: `/tmp/opencode/gestaltdb-194-native-locator-benchmarks`
- Implementation commit: `4c3d1259f71b452eb21d386a78bb5206d7b6cdd6`
- Base: `origin/main` at `3403530` when work started.

This is a snapshot, not a completed measurement or issue-closure declaration.
The separate implementation-roadmap/workbook sanitation work is ongoing. Leave
those files alone pending its merge; reconciliation notes are retained here and
in the conversation rather than editing the workbook concurrently.

## What remains to close the issue

The current issue body supersedes the old 2K-only status and integration-only
target. Production historical locator admission is already implemented. The
remaining work is to measure the **actual** `PinnedNativeLocatorClaimSource` on
named historical/selective and nonselective workloads, charging complete source
authentication and history point materialization, recording exact output parity,
source/runtime versions, time and RSS, then explicitly adopt, defer or reject it.

Earlier raw scans, assertion-only benchmark locators and authenticated full-record
results must remain separate. They are not measurements of this adapter. There
is no need to invent support/publication benchmarks or weaken integrity checks to
close the current bounded scope. A process restart is not an OS-cold-cache test.

## Implemented and pushed

`benchmarks/reasoning_locator_pipeline.py` compares three separately named sources:

1. `ordinary`: `PinnedBatchedClaimSource`.
2. `native-full-record`: `PinnedNativeClaimSource`.
3. `native-authenticated-locator`: `PinnedNativeLocatorClaimSource`.

The fixture uses variable bounded entity IDs and compressible provenance padding.
It writes assertions over `[0, 100)`, corrections for every fifth assertion over
`[20, 80)`, and retractions for every seventh over `[40, 60)`. Selections are all
claims, one predicate (one quarter of assertions), and one subject. Supported
valid-time probes are 10, 30, 50, 70 and 90. The subject fixture is retracted at 50
and restored outside the retraction interval; its correction has a distinct exact
version ID.

Exact fact/version evidence is checked against an independent oracle assembled
from write receipts and the fixture's validity rules, not from the adapter output.
Fixture loading and extraction run in separate processes. Timed extraction
includes view/source authentication, native transfer, history materialization,
canonical conformance, source closure and final source verification. Reports retain
native work profiles, runtime/source/hash metadata, first/primed labels and
whole-worker peak RSS. Failed workers retain diagnostics without success quantiles.
Inference, support discovery and publication are not measured. Production defaults
and integrity checks are unchanged.

Tests live in `tests/test_reasoning_locator_pipeline.py`; reproduction commands
and accounting boundaries are in `docs/reasoning-native-locator-claims.rst`.

## Evidence so far

Local report paths below are outside Git and are **not committed artifacts** yet.

### Completed 1K smoke

Report: `/tmp/opencode/reasoning-194-locator-1k-v2.json`.

- 1,000 assertions, 1,343 total historical versions.
- All three methods, all three selections, valid time 50, first/primed extraction,
  one repeat: **18/18 exact-evidence parity samples**; `complete=true`.
- Recorded source: `3403530`, `dirty=true` because the new harness had not yet
  been committed. This is not clean-source release evidence.
- Runtime: GestaltDB 0.16.0, Python 3.13, pyrex-rocksdb 0.7.1, PyArrow 24.0.0.
- Locator extraction was slower than ordinary extraction in this smoke. One
  observation per case is not a stable latency distribution or adoption result.

An earlier wider 1K smoke was interrupted by the outer command's 120-second
deadline, leaving an incomplete report at
`/tmp/opencode/reasoning-194-locator-smoke.json`. Do not reinterpret that as a
specific adapter's worker timeout or a completed matrix.

### In-progress clean-source 10K gate

Report: `/tmp/opencode/reasoning-194-locator-10k-gate.json`.

- Source `4c3d125`, `dirty=false` at launch; same runtime versions as the smoke.
- Fixture loaded successfully: 10,000 assertions, 13,429 historical versions;
  load took approximately 66.96 seconds, outside extraction timing.
- Requested: all three methods/selections, times 30 and 50, first/primed,
  one repeat, 600-second per-worker limit (36 planned samples).
- Latest inspected snapshot: **12 successful parity samples**, `complete=false`;
  all/predicate cases at time 30 completed for first/primed reads. Remaining
  cases were not yet established complete. No completed-matrix quantiles exist.

First-read observations at valid time 30 (single samples, not medians):

| Selection | Source | Authenticated pipeline (s) | Worker peak RSS (MiB) |
|---|---|---:|---:|
| all | ordinary | 24.145 | 128.1 |
| all | native full-record | 119.752 | 207.3 |
| all | authenticated locator | 81.399 | 198.3 |
| predicate | ordinary | 23.853 | 124.2 |
| predicate | native full-record | 120.207 | 191.7 |
| predicate | authenticated locator | 78.032 | 196.9 |

RSS is whole extraction-worker high-water, including validation and any warmup,
not incremental query-only memory. Worker wall time also includes startup and,
for primed cases, the untimed priming pass; do not compare that directly with
the timed authenticated-pipeline metric. OS caches and host contention are
uncontrolled; tests, a small diagnostic profile and the later 100K pilot have
also used this host during the ongoing work. These are pilot observations, not
an isolated backend ranking or a general native-speedup claim.

### In-progress 100K pilot

Launched command:

```sh
uv run --extra rocksdb --extra arrow python -m benchmarks.reasoning_locator_pipeline --rows 100000 --methods ordinary --selections subject --times 30 --caches first --repeats 1 --timeout 900 --output /tmp/opencode/reasoning-194-locator-100k-ordinary-subject-gate.json
```

Source was clean `4c3d125` at launch. No report had been written at the latest
inspection, so fixture completion, extraction time, parity and RSS are **not yet
established**. This is only the ordinary-source gate, not the native locator
comparison. Actual native/full-record and nonselective 100K measurements remain
to be completed or explicitly retained as bounded failures.

## Diagnostic finding, not a production fix

A separate 1K single-subject authenticated-locator cProfile run recorded about
1.86 million raw backend reads in 4.62 profiled seconds. Profile:
`/tmp/opencode/reasoning-194-locator-subject-profile.pstats`.

Inspection shows mutable-store cached commits still revalidate record envelopes;
commits containing supersession links also require target validation. Repeated
authentication is therefore a plausible major cost even when the requested
subject is highly selective. This small profile does not prove the attribution
of a future 100K timeout or establish asymptotic complexity. Do not skip commit,
supersession, complete-catalog or canonical-conformance checks to improve numbers.

## Validation and outstanding CI defect

- Full local suite with backend/serializer/Arrow/Polars/RDF/NetworkX/docs extras:
  **4,148 passed, 41 skipped**.
- Ruff, strict typing ratchet and module-size gate passed.
- Documentation drift and Sphinx `-W` passed.
- Documentation examples: 7 passed, 1 skipped because Polars was absent in that
  particular example-test environment.
- PR #450 full-suite, installed-artifact, ontology, build, optional-integration
  and CodeQL jobs passed at the inspected head.
- **Two minimal-install CI jobs failed** on the new CLI timeout test. Report
  setup calls `importlib.metadata.version` for optional runtime packages without
  handling missing distributions, so a base install exits before writing its
  expected timeout report. This is a real harness portability bug, not a source
  extraction failure. Fix optional-dependency metadata handling and rerun those
  jobs; do not claim the PR is fully green. Benchmark source files have not been
  changed during the clean-source runs to avoid mixing harness revisions.

## Provisional decision and next actions

**No automatic native adoption is justified by the evidence so far.** Keep the
ordinary source as the default and native sources explicit. The final
adopt/defer/reject decision is pending the 100K comparison review, not yet a
completed issue acceptance result. Current pilot results favor deferring adoption,
not deleting or declaring the authenticated locator semantically incorrect.

1. Preserve completed/incomplete gate reports and their exact source/runtime
   boundaries; do not promote partial matrices to complete evidence.
2. Finish the ordinary 100K gate, then measure actual locator/full-record sources
   on historical selective and nonselective workloads with appropriate bounds.
   Record timeouts as failures, not successful latency/RSS/parity measurements.
3. Decide whether repeated runs are justified by the gate; retain time/RSS,
   parity and native profiles for successful samples and bounded diagnostics for
   failures. Record an explicit, workload-qualified adopt/defer/reject decision
   in the adapter documentation with committed evidence.
4. Fix minimal-install report metadata handling, test it without optional extras,
   and obtain green exact-head checks before making PR #450 ready.
5. After the separate roadmap/workbook update merges, reconcile its current
   wording with this bounded #194 acceptance and measured decision. Do not
   overwrite concurrent workbook work or expand acceptance from older comments.

Issue #194 remains **open** and PR #450 remains **draft**. No merge, release,
automatic source adoption or issue closure has been performed.
