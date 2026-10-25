# Application executor admission — #255

Status: executable admission milestone implemented in
`reasoning/generation.py` (`admit`; see `docs/reasoning-admission.rst`); existing
`reasoning/executors.py` selection is unchanged. #248's acceptance reconciliation
is merged via #362 and #248 is closed after green post-merge checks.
Benchmark crossovers in #194 remain the gate for enabling new physical paths.

Implemented: `reasoning/adapters/execution.py` connects the decision
to real engines through one opt-in entry: verified `ExecutorRequest`, optional
generation admission with exact dictionary/mode/executor checks, dispatch to
the decided engine (indexed reference default; prepared-plan and Polars only
on explicit caller-supplied plan/context plus available runtime), uniform
`StageTelemetry`, no automatic switching, no dictionary recoding. Existing
engine call sites are unchanged; typed/maintenance/temporal paths keep their
adapters. Benchmark crossover and broader pipeline adoption remain open.

Physical-budget continuation: the dispatcher and authenticated current-graph
consumer now expose explicit `polars_max_intermediate_rows`, default unchanged
at 100,000. The option is rejected for indexed requests before input/projection
reads, remains outside logical identity, and does not replace cumulative
candidate/fact budgets. Successful `ExecutionOutcome.polars_run` retains native
metrics including chosen cap and conservative peak/candidate accounting, separate
from unchanged common stage telemetry. The >100K-row regression checks full
reference parity, not adoption or a new speed/memory claim. Larger measured
pipelines, typed/common capability integration and general native historical/
concurrent-source admission remain distinct #255/#194 work.

| Requested mode | Initial admitted implementation |
|---|---|
| Binary positive closure | Existing indexed reference; Polars only on explicit opt-in |
| Binary witness | Indexed engines with the existing witness contract |
| Typed n-ary closure/filter | Generic indexed engine |
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
