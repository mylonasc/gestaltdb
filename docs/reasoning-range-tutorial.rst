Update and inspect interval-valued conclusions
===============================================

Start with :doc:`reasoning-temporal` for interval intersection, union, change
segments and point/range agreement. Each example below defines its own program
and facts and runs with the base install. No database/source identity, backend,
serializer or publication permission is created by these computations.

Absence applies only where its blocker is inactive
---------------------------------------------------

Alice is a candidate on ``[0, 10)`` and banned on ``[5, 10)``. With ``banned``
declared complete for the supplied inputs, eligibility holds on ``[0, 5)``.
Moving the ban's start to ``3`` shrinks eligibility to ``[0, 3)``; the updated
closure must agree with fresh evaluation, not just with a few sampled points.

.. code-block:: python

   import warnings
   from gestaltdb.reasoning import (
       GenericProgram, GenericRule, Interval, IntervalSet, ReasoningLimits,
       TemporalFact, TemporalProofTruncatedWarning, TypedAtom, TypedFact,
       Variable, evaluate_range, update_range, verify_closure,
   )

   person = Variable("person")
   program = GenericProgram([GenericRule(
       "eligible", [TypedAtom("candidate", [person])], [], TypedAtom("eligible", [person]),
       negated=[TypedAtom("banned", [person])],
   )], closed=["banned"])
   candidate = TemporalFact(TypedFact("candidate", ["alice"]), IntervalSet([Interval(0, 10)]))
   ban = TemporalFact(TypedFact("banned", ["alice"]), IntervalSet([Interval(5, 10)]))
   base = [candidate, ban]
   goal = TypedFact("eligible", ["alice"])
   closure = evaluate_range(program, base, limits=ReasoningLimits())
   assert closure.answers(goal).intervals == (Interval(0, 5),)
   assert closure.evidence(goal)[0].absence[0].scope == ("banned",)
   changed = [candidate, TemporalFact(ban.fact, IntervalSet([Interval(3, 10)]))]
   updated = update_range(program, closure, changed, limits=ReasoningLimits())
   fresh = evaluate_range(program, changed, limits=ReasoningLimits())
   assert updated.closure.conclusions == fresh.conclusions
   assert updated.closure.answers(goal).intervals == (Interval(0, 3),)
   assert not verify_closure(program, updated.closure, limits=ReasoningLimits()).proofs_truncated

   with warnings.catch_warnings(record=True) as caught:
       warnings.simplefilter("always")
       partial = evaluate_range(program, base, limits=ReasoningLimits(max_proof_nodes=1))
   assert any(isinstance(item.message, TemporalProofTruncatedWarning) for item in caught)
   assert partial.answers(goal) == closure.answers(goal)
   assert partial.proofs_truncated and partial.missing_evidence()

Scoped absence is not a negative claim or a declaration that every unrecorded
predicate is false. The range evaluator checks supported stratified absence on
each completed segment, with its scoped evidence. This differs from the stored
claim-rule service's conservative overlap rule (:doc:`stored-rules`).

Logical results and retained explanations are separate. The warning above says
evidence is incomplete, not that eligibility vanished. ``answers`` and ``at``
remain logical answers; ``derivations``/``evidence`` can be partial. A truncated
old closure is rematerialized rather than admitted as a reusable proof cache.
Malformed input, cancellation and logical-work bounds still fail without a
successful partial closure (:doc:`reasoning-ranges`).

Aggregate the rows active on each segment
------------------------------------------

One member is present on ``[0, 10)`` and another joins on ``[5, 10)``. The team
size is one then two. A count does not extend beyond the members' validity or
invent a zero-count group outside the populated interval.

.. code-block:: python

   from gestaltdb.reasoning import (
       AggregateRule, Aggregation, GenericProgram, Interval, IntervalSet, Literal,
       ReasoningLimits, TemporalFact, TypedAtom, TypedFact, Variable, evaluate_range, verify_closure,
   )

   team, member, count = Variable("team"), Variable("member"), Variable("count")
   program = GenericProgram([], aggregates=[AggregateRule(
       "team-count", [TypedAtom("member", [team, member])], [team],
       [Aggregation("count", member, count)], TypedAtom("team_size", [team, count]),
   )])
   base = [TemporalFact(TypedFact("member", ["lab", "alice"]), IntervalSet([Interval(0, 10)])),
           TemporalFact(TypedFact("member", ["lab", "bob"]), IntervalSet([Interval(5, 10)]))]
   closure = evaluate_range(program, base, limits=ReasoningLimits())
   one = TypedFact("team_size", ["lab", Literal("integer", 1)])
   two = TypedFact("team_size", ["lab", Literal("integer", 2)])
   assert closure.answers(one).intervals == (Interval(0, 5),)
   assert closure.answers(two).intervals == (Interval(5, 10),)
   assert not any(fact.predicate == "team_size" for fact in closure.at(10))
   assert len(closure.evidence(two)[0].aggregates[0].operands) == 2
   assert not verify_closure(program, closure, limits=ReasoningLimits()).proofs_truncated

Only the supported nonrecursive aggregate stratum is evaluated. Aggregate heads
remain sinks and cannot feed another aggregate body or recursive rule. Range
evaluation does not widen the typed engine's accepted fragment. Its aggregate
evidence records logical operands for a segment, not authenticated database
versions or an independently recomputed arithmetic certificate.

Scope equality to one segment
-------------------------------

Representatives from an :term:`equality` policy are not globally stable aliases.
This separate helper takes caller-selected active fact sets for two segments.
The explicit functional-property policy equates the two mailbox values in the
first segment; that evidence is absent in the second. It does not rewrite either
the input facts or database nodes.

.. code-block:: python

   from gestaltdb.reasoning import TypedFact, canonicalize_segments
   from gestaltdb.reasoning.equality import FunctionalProperty

   segments = [
       [TypedFact("mailbox", ["alice", "address-a"]), TypedFact("mailbox", ["alice", "address-b"])],
       [TypedFact("mailbox", ["alice", "address-a"])],
   ]
   mappings = canonicalize_segments(segments, [FunctionalProperty("mailbox")], scope_prefix="example")
   assert mappings[0].representative("address-b") == "address-a"
   assert mappings[1].representative("address-b") == "address-b"
   assert mappings[0].scope != mappings[1].scope

The policy is caller-selected, not automatic real-world identity resolution.
Rebuild scoped mappings when support or the selected horizon changes. The helper
does not acquire database snapshots, automatically rewrite range closure, or
make equality valid beyond the fact set supplied for that segment. See
:doc:`reasoning-equality` for conflicts and representative semantics.

Computation, admission and publication are separate
---------------------------------------------------

.. list-table:: Do not infer authority from a successful calculation
   :header-rows: 1

   * - Stage
     - Provides
     - Does not provide
   * - ``evaluate_range`` / ``update_range``
     - Closure over explicit program/base validity
     - Database authentication or write authority
   * - ``verify_closure``
     - Re-segmentation, per-segment reevaluation and evidence conformance
     - Independent source authentication or real-world truth certification
   * - Authenticated range admission
     - Explicit finite source/range/program identities, extraction and recomputation
     - A universal publication permit or a cheap cache hit
   * - Bound range publication
     - Computation-owned guarded source re-admission, freshness/target checks and interval-batch commit
     - Unrestricted publication of caller-derived state or automatic mutation of current graph edges

Follow :doc:`reasoning-range-admission` and
:doc:`reasoning-bound-range-publication` only when a database workflow requires
those contracts. Raw caller-derived ``stage_range`` remains a distinct operation;
capturing a staging horizon does not authenticate the computation that preceded
it (:doc:`reasoning-ranges`). Compare incremental and full evaluation on your
workload rather than assuming updates always reuse most segments.
