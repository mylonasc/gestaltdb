# Inferred query and immutable sampler acceptance workflow — #256

Status: initial integration design. Blocked by #248, #249, #251 and #254.
Coordinate #127/#128; no automatic Cypher rewrite or probabilistic calculus is
being introduced by this fixture.

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
