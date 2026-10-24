Authenticated temporal-range evaluation and re-admission
================================================================

``gestaltdb.reasoning.adapters.range_generation`` connects positive typed range
closure to an authenticated temporal source prefix and shared generation
identity. It does not reinterpret a point token as evidence for a whole range.
The caller supplies a temporal ``GraphReadView`` anchored at the finite range's
start and keeps its source graph available throughout evaluation.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning.model import ReasoningLimits, Variable
   from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
   from gestaltdb.reasoning.adapters.claims import ClaimSelection, ClaimMapping
   from gestaltdb.reasoning.adapters.session_generation import catalog_for_typed_program
   from gestaltdb.reasoning.adapters.range_generation import evaluate_pinned_range, admit_range_result

   x, y = Variable("x"), Variable("y")
   program = GenericProgram([GenericRule("r", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y]))])
   catalog = catalog_for_typed_program(program)
   with TemporaryDirectory() as path:
       graph = GraphDB.create(path, backend="lmdb", serializer="json")
       try:
           graph.assert_claim(subject="a", predicate="edge", object="b", agent="example",
                              polarity="positive", valid=(10, 20))
           with graph.read_view(valid_time=0) as view:
               result = evaluate_pinned_range(view, program, catalog,
                   valid_start_us=0, valid_end_us=30, world="default",
                   selection=ClaimSelection(), mapping=ClaimMapping(), limits=ReasoningLimits())
               assert TypedFact("reach", ("a", "b")) in result.closure.at(10)
               assert result.closure.at(20) == ()
               checked = admit_range_result(view, result, program, catalog,
                   expected_generation=result.binding.identity, limits=ReasoningLimits())
               assert checked.points_checked == len(result.closure.segments)
           result.binding.verify_source(graph)
       finally:
           graph.close()

Source semantics and identity
-----------------------------

``TemporalRangeSourceIdentity`` binds database, commit horizon, authenticated
provenance token and explicit finite half-open ``[valid_start_us, valid_end_us)``
bounds. The provenance is anchored at the start; the range identity and its
digest domain are distinct from point and current-state identities. Existing
point/current generation digests remain unchanged. Program and catalog digests,
world, selection, mapping, ``positive-typed-range-v1`` profile and
``typed-values-v1`` representation complete the shared ``GenerationIdentity``.
Catalog content follows ``catalog_for_typed_program``; external versions and
ordinals remain part of identity, not independently authenticated rule records.

Evaluation enumerates **all historical claim validity boundaries** under the
exact pinned horizon, including corrections/retractions and boundaries from
other policies. At each elementary segment it uses canonical indexed point
claim selection under that same horizon. This preserves existing winner and
supersession semantics without treating an assertion's original validity as its
final active interval. Positive entity claims are then filtered by explicit
agent/source/derived policy and mapped, preserving unmapped predicates.

``RangeEvidence`` retains every independent active assertion version, logical
claim ID, mapped typed fact and exact clipped interval pieces. Parallel versions
are not collapsed merely because their logical fact is equal. ``TemporalFact``
rows feed the existing complete range evaluator. Gaps remain gaps and no
min/max envelope widens validity. ``extraction_segments`` tiles the requested
range, including empty portions; ``closure.segments`` follows active input
validity and can be smaller or empty.

Only the existing positive typed fragment is admitted. Database base facts are
binary entity claims; rule outputs may use supported typed arities. Temporal
negation, aggregates, closed scopes, equality canonicalization, literal-claim
ingestion and arbitrary executor selection are not silently enabled.

Authentication versus correctness
---------------------------------

Evaluation checks view/graph/store/decoder/provenance binding around source
work and verifies the authenticated prefix before/after extraction and after
evaluation. Matching ``expected_generation`` is checked before history or point
extraction, never as a substitute for authentication. The adapter uses canonical
database iterators and their normal integrity/index invariants; it does not
independently prove index completeness against hostile backend implementations.

``admit_range_result`` requires an exact caller generation pin, checks retained
size bounds, independently re-extracts all source evidence, and rejects any
version/fact/interval omission or change. It recomputes the complete closure and
compares the exact result, including derivations, then runs ``verify_closure``
and rechecks source identity. Equal goal facts alone do not admit missing or
fabricated supports. This is an intentionally expensive re-admission operation,
not a fast cache hit or an independent verification of the engine implementation.
Tests additionally compare against an independent fixture-grounding oracle.

Constructing identity, binding or result values manually proves nothing.
``binding.verify_source(graph)`` authenticates the original prefix and defaults
to rejecting any newer horizon; explicit ``freshness="historical"`` permits
that exact old prefix. This metadata/source check alone does not validate a
supplied closure: use re-admission for that. Old prefix evaluation can be
reopened explicitly with ``read_view(..., through_commit=...)``; it must not be
relabelled as latest state.

Bounds and publication boundary
------------------------------------

``RangeSourceLimits`` bounds commit prefix length, cumulative historical claim
versions, cumulative positive/entity/world candidates yielded across point
reads (before policy filtering), extraction segments, distinct evidence versions,
derived supports and interval-sweep memberships. History counts all policies;
candidate counts include repeated visits across segments. ``ReasoningLimits``
keeps the range evaluator's existing per-segment evaluation and cumulative
support-discovery accounting. Re-admission performs fresh evaluation plus the
conformance gate; their reasoning counters are not one shared global work meter.

Source authentication, whole commit decoding, point-query internal buffering,
KV/index scans and bytes/RSS/deadlines are outside these counters. Cancellation
is cooperative between calls, never inside a backend operation, hashing or a
blocked iterator. Failures return no successful partial result. The adapter
does not close the caller's view, write claims, repair data or select native
execution. The complete history/point strategy has no performance claim.

This API establishes authenticated range computation and re-admission, **not**
a compare-and-publish permit or durable artifact codec. Raw ``stage_range`` in
:doc:`reasoning-ranges` still receives caller-derived triples and authenticates
only its staging prefix. Multi-interval publication needs an explicit consumer
with source freshness and target CAS under a writer guard; independently
publishing one interval must not silently admit stale later intervals or claim
batch atomicity. Session/sampler/cache migrations remain separate consumers.
An explicit guarded interval-batch consumer is now available in
:doc:`reasoning-bound-range-publication`; the raw staging API remains unchanged.
