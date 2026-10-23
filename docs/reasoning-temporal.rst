:orphan:

Complete time-range closure with scoped equality
============================================================

``gestaltdb.reasoning.temporal_closure.evaluate_range`` never samples one
instant: all validity bounds become cut points, the timeline splits into
elementary segments with uniform active inputs, and each segment evaluates
under the ordinary engine. A derived fact holds exactly on the union of
segments where it derives — the union of all support intersections — with
exact per-interval evidence. Point queries agree with independent point
evaluation by construction.

.. code-block:: python

   from gestaltdb.reasoning import Interval, IntervalSet, TemporalFact, evaluate_range
   from gestaltdb.reasoning.model import ReasoningLimits

   facts = [TemporalFact(TypedFact("edge", ["a", "b"]), IntervalSet([Interval(0, 10)])),
            TemporalFact(TypedFact("edge", ["b", "c"]), IntervalSet([Interval(5, 15)]))]
   closure = evaluate_range(program, facts, limits=ReasoningLimits())
   assert closure.answers(TypedFact("reach", ["a", "c"])).intervals == (Interval(5, 10),)
   assert set(closure.at(7)) == set(
       GenericEngine().evaluate(program, [f.fact for f in facts if 7 in f.valid],
                                limits=ReasoningLimits()).facts)

Time-scoped equality
----------------------------------------

``canonicalize_segments`` rebuilds canonical representatives at every
change point under per-segment scopes, so entities equal during one
interval never merge globally. Time-varying links are separate
evaluations per segment, never one global union. Temporal negation,
aggregates and closed scopes fail closed; per-point stratified
evaluation is the documented explicit fallback.

Measured cost
----------------------------------------

A 20-edge chain with sliding 10-wide windows (29 segments, 935
derivations with complete per-interval supports) evaluates in ~150ms
single-threaded. Segment count bounds the work: ``max_segments``,
per-segment engine limits and ``max_supports`` fail closed instead of
degrading. The 25-case seeded suite checks point/range agreement over
randomized topologies and windows.
