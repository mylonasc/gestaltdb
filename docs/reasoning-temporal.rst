:orphan:

Complete time-range closure with scoped equality
============================================================

Before the algorithm
---------------------

Ordinary :term:`closure` says which facts follow. Temporal closure also says
**when** they follow. Premises used together overlap by interval intersection;
independent alternative supports contribute an interval union. A **segment** is
an interval between change points where the active inputs stay constant.
``[a, b)`` includes its start and excludes its end. See :doc:`glossary` for
:term:`valid time`, :term:`system time`, :term:`support` and :term:`witness`, and
:doc:`reasoning-introduction` for the facts/rules foundation. Historical database
selection remains a separate workflow (:doc:`temporal-versioning`).

Evaluation and bounds
----------------------

``gestaltdb.reasoning.temporal_closure.evaluate_range`` never samples one
instant: all validity bounds become cut points, the timeline splits into
elementary segments with uniform active inputs, and each segment evaluates
under the ordinary engine. A derived fact holds exactly on the union of
segments where it derives — the union of all support intersections — with
exact per-interval evidence. Point queries agree with independent point
evaluation by construction. Active inputs are scheduled once with an
interval event sweep (``gestaltdb.reasoning.temporal_sweep``) instead of
scanning every input at every segment: each validity bound contributes one
add/remove event pair, segments tile exactly the change points, and
overlapping assertions keep their multiplicity. A fragmented draw (400
inputs, 199 segments) performs 800 events plus 1,957 scheduled memberships
instead of 79,600 scan checks (~40x fewer membership operations); sweep
bounds (``max_sweep_rows``/``max_sweep_events``/``max_sweep_memberships``)
fail closed instead of degrading. The independent ``verify_closure`` gate
still re-derives segmentation and point-evaluates through its own scan
path, so scheduled closures keep proving segment identity.

**Standalone runnable example.** The base install suffices. Two edges overlap
only on ``[5, 10)``, so their two-hop reachability holds exactly on that interval.

.. code-block:: python

   from gestaltdb.reasoning import (
       GenericEngine, GenericProgram, GenericRule, Interval, IntervalSet,
       ReasoningLimits, TemporalFact, TypedAtom, TypedFact, Variable, evaluate_range,
   )

   x, y, z = Variable("x"), Variable("y"), Variable("z")
   program = GenericProgram([
       GenericRule("direct", [TypedAtom("edge", [x, y])], (), TypedAtom("reach", [x, y])),
       GenericRule("extend", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])],
                   (), TypedAtom("reach", [x, z])),
   ])
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
evaluations per segment, never one global union. Stratified negation over
declared closed scopes and nonrecursive aggregates evaluate per segment
with per-interval absence and aggregate evidence; gaps stay gaps and
empty groups infer no zero counts (see :doc:`reasoning-ranges`).

Measured cost
----------------------------------------

A 20-edge chain with sliding 10-wide windows (29 segments, 935
derivations with complete per-interval supports) evaluates in ~150ms
single-threaded. Segment count bounds the work: ``max_segments``,
per-segment engine limits and ``max_supports`` fail closed instead of
degrading. The 25-case seeded suite checks point/range agreement over
randomized topologies and windows.
