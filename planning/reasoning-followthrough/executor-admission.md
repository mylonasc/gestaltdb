# Application executor admission — #255

Status: original #255 acceptance reconciled and issue closed after #365 at
`4a2c393`, 12 exact-head green checks and post-merge Ontology `40168451602` /
readiness `40168451655`. Shared admission is in
`reasoning/generation.py` (`admit`; see `docs/reasoning-admission.rst`); binary
defaults remain unchanged. #248's acceptance reconciliation
is merged via #362 and #248 is closed after green post-merge checks.
Benchmark crossovers in #194 remain the gate for enabling new physical paths.

Implemented: `reasoning/adapters/execution.py` connects the decision
to real engines through one opt-in entry: verified `ExecutorRequest`, optional
generation admission with exact dictionary/mode/executor checks, dispatch to
the decided engine (indexed reference default; prepared-plan and Polars only
on explicit caller-supplied plan/context plus available runtime), uniform
`StageTelemetry`, no automatic switching, no dictionary recoding. Existing
engine call sites are unchanged; typed/maintenance/temporal paths keep their
adapters. Broader pipeline adoption remains acceptance-reviewed rather than
implied by one dispatcher or a benchmark result.

Positive typed application integration now uses the same central
`ExecutorRequest` selector and common stage telemetry, with a distinct
`typed-indexed-python-v1` identity and exact `typed-values-v1` admission. The raw
`execute_typed_closure` owns bounded single-pass TypedFact buffering and preserves
literal/n-ary values without binary conversion. The real
`typed_execution.execute_authenticated_session` consumer independently verifies
the complete active source ledger under exact caller generation/state pins,
recomputes from only active base input, compares complete closure, retains every
independent input version and rechecks source/decoder under the writer guard.
Negation/aggregate/closed-scope programs and typed witness/SCC/Polars requests
remain explicitly rejected here, not silently routed to weaker semantics.
See `docs/reasoning-typed-execution.rst`; no supports/proof/publication guarantee
is inferred from matching closure or source authentication.

Physical-budget continuation: the dispatcher and authenticated current-graph
consumer now expose explicit `polars_max_intermediate_rows`, default unchanged
at 100,000. The option is rejected for indexed requests before input/projection
reads, remains outside logical identity, and does not replace cumulative
candidate/fact budgets. Successful `ExecutionOutcome.polars_run` retains native
metrics including chosen cap and conservative peak/candidate accounting, separate
from unchanged common stage telemetry. The >100K-row regression checks full
reference parity, not adoption or a memory guarantee. The #255 continuation adds
48 clean-source parity-verified 100K observations through the real dispatcher,
using a caller-owned 1M cap; full timing/RSS/native accounting boundaries are in
`docs/reasoning-frontier-benchmarks.rst`. Indexed remains default. General native
historical/concurrent-source admission remains separate #194 work.

| Requested mode | Initial admitted implementation |
|---|---|
| Binary positive closure | Existing indexed reference; Polars only on explicit opt-in |
| Binary witness | Indexed engines with the existing witness contract |
| Typed n-ary closure/filter | Explicit typed dispatcher / Generic indexed engine; authenticated session base-input consumer |
| Closed-scope negation | Explicit stratified engine |
| Complete maintenance supports | Maintenance path; not a bounded witness trace |
| Temporal closure | Generation-bound change-segment path |
| Unsupported combination | Explicit error or explicitly requested complete rebuild |

The planner authenticates the source through adapters, then validates profile,
dictionary and generation compatibility before consuming facts. Conversion
ownership, candidate-accounting units and proof guarantees travel with the plan.
No workload-size heuristic may silently weaken requested semantics.

Acceptance scenarios use the same pinned source, goal and evidence across
reference and optional modes; compare decoded results and semantic guarantees,
then full-pipeline latency/RSS. Measure startup/conversion separately. Enable a
new physical path only after the benchmark shows a useful end-to-end crossover;
do not add a bespoke native runtime merely to fill out this table.

## Original acceptance reconciliation

This is acceptance against the original issue, not a count of merged children.

| Original requirement | Delivered implementation / evidence |
|---|---|
| Profile/mode capability admission | `executors.py:select_executor`, `generation.py:admit`; explicit binary closure/witness and distinct typed closure rows; tests in `test_reasoning_executors.py`, `test_reasoning_admission.py`, `test_reasoning_typed_execution.py` |
| Conversion ownership | `adapters/execution.py` bounds single-pass materialization; prepared context is supplied independently; Polars owns explicit dictionary/columnar conversion and decoded output; typed facts preserve strict literal/n-ary values without binary recoding |
| Dictionary/context checks | Actual program/context/profile/representation/executor checked before input; exact generation binding; `test_reasoning_execution.py` covers unrelated-plan/program/context and executor/mode mismatches, duplicate evidence and bounded generators |
| Common stage telemetry | `StageTelemetry` shared by binary reference, prepared, explicit Polars and typed execution; native `PolarsRun` remains separate, physical caps do not change logical identity |
| Real application integration fixtures | `adapters/graph_execution.py` authenticates complete current projection before dispatch; `adapters/typed_execution.py` independently admits complete temporal point-session base versions, recomputes full closure and compares supplied state; `test_reasoning_graph_execution.py` and `test_reasoning_typed_execution.py` include pin mismatches, forgery, cancellation and movement |
| Indexed Python reference; measured supported optional closure | Original #342/#349 reports plus 48 #255 100K results at clean `b87ef1c`; exact evidence and analytical full closure parity, all conversion charged, separate native metrics; original failures preserved |
| Reject unsupported proof/maintenance combinations | Polars closure-only; no typed proof/SCC/Polars recoding, no implicit aggregate/negative execution or maintenance/range fallback; dedicated engines remain separate |
| Standalone boundaries, exact evidence, backend capabilities, limits | Core imports stay inward-only (`test_reasoning_contracts.py`); point/current/range identities remain distinct; independent version IDs retained, optional runtime probed, physical/input/candidate/fact limits and cooperative cancellation fail closed |

The measured improvement supports explicit bounded opt-in, not universal speed,
memory or automatic selection claims. Session prerequisites are delivered by
the authenticated point/session contract, not by trusting caller checksums.
No new native runtime, full-OWL claim, main merge or release is part of #255.
General native extraction (#194), authentication amortization and broader
scale/durability/concurrency gates of #156 remain open independently.
