# Inferred query and immutable sampler acceptance workflow — #256

Status: executable scenario milestone implemented in
`tests/test_reasoning_inferred_consumers.py` (see
`docs/reasoning-inferred-consumers.rst`): session query, caller-driven
publication into a managed world, correct/retract transitions with
fresh-recomputation parity and pinned-reader stability. Sampler-artifact
slice implemented in `reasoning/adapters/inferred_sampler.py`
(`tests/test_reasoning_inferred_sampler.py`): explicit inferred-node/edge
selection with generation provenance, mismatch/overwrite fail-closed, old
artifacts stable across generations. Blocked-by notes: #248 and
#251 merged (durable sessions included); #249 planner merged (binding/pushdown still open); #254 sweep
prototype merged.

## End-to-end fixture

Create a disposable graph with two sourced assertions, a subproperty chain and
disjoint valid-time supports. Pin a source/rule generation; answer an explicit
inferred goal and publish to a separate managed world. Build an immutable sampler
artifact from an explicitly selected inferred view, retaining generation and
external/compact ID provenance. Filtering seeds alone must not filter every hop.

Correct one assertion, retract one of two independent versions, then retract the
final version. After each transition compare queried facts, validity intervals,
evidence and inferred neighbor sets with fresh recomputation. Old pinned readers
and sampler artifacts remain unchanged; new artifacts carry the new generation.

Test system-time replay and future-edge/node-availability exclusion. Preserve
documented distinct-endpoint versus path/edge multiplicity, weight and fanout
semantics. Reject a generation/view mismatch before snapshot files are published.
Keep graph handles/read views open through extraction and close them explicitly;
snapshot arrays must not retain a live database handle.
