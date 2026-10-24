# Temporal active-set event scheduler — #254

Status: scheduler wired into range evaluation. Integration depended on #245
(done) and #194 measurements (bounded matrix merged; native comparison remains
separate and untouched). Program identity is enforced by the existing
generation digest and program-bound reuse contracts, not by a new cache.

Implemented in `reasoning/temporal_sweep.py`; see
`docs/reasoning-temporal-sweep.rst`. `evaluate_range` and `update_range` schedule
active inputs through the sweep instead of scanning every input at every segment
(row-to-fact mapping preserves overlapping multiplicity; `verify_closure` keeps
independently re-deriving segmentation through its own scan path). Measured on a
fragmented draw: 800 events + 1,957 memberships replace 79,600 scan checks.
The prototype retains row ordinals rather than inventing evidence IDs and
bounds total emitted row memberships as well as rows, events and segments.

Build events `(bound, logical_fact, signed_count)` from half-open intervals.
Initialize unbounded-start intervals before finite events. At a boundary process
all ending and starting events before yielding the segment beginning there;
maintain counts, not a Boolean set, for overlapping assertions of the same fact.
Remove a fact from the active set only when its count reaches zero.

Preserve existing segment IDs and exact source-evidence associations. Caching
uses the authenticated generation plus program/profile and active evidence, not
just an unordered set of logical facts. Finite gaps remain gaps.

## Independent oracle cases

For every generated segment compare the sweep to scalar `point in item.valid`:
touching boundaries, nested intervals, same-fact overlaps, duplicate intervals,
unbounded starts/ends, negative timestamps, empty gaps, corrections and concurrent
version supports. Compare range answers and supports, not only derived fact sets.
Measure event processing separately from unavoidable output-sized work and support
discovery. Bound event count, segments and output before claiming memory behavior.
