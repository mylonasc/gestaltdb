# Application executor admission — #255

Status: executable admission milestone implemented in
`reasoning/generation.py` (`admit`; see `docs/reasoning-admission.rst`); existing
`reasoning/executors.py` selection is unchanged. Depends on #248 (merged);
benchmark crossovers in #194 remain the gate for enabling new physical paths.

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
