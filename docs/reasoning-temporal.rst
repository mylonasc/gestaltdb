:orphan:

.. _complete-time-range-closure-with-scoped-equality:

Reason over complete validity ranges
=====================================

Before the algorithm
---------------------

Ordinary :term:`closure` says which facts follow. Temporal closure also says
**when** they follow. Premises used together overlap by interval intersection;
independent alternative supports contribute an interval union. A **segment** is
an interval between change points where the active inputs stay constant.
``[a, b)`` includes its start and excludes its end. See :doc:`glossary` for
:term:`valid time`, :term:`system time`, :term:`support` and :term:`witness`, and
:doc:`reasoning-introduction` for the facts/rules foundation. Historical database
selection remains a separate workflow (:doc:`temporal-introduction`).

.. list-table:: Temporal storage is not temporal inference
   :header-rows: 1

   * - Task
     - Temporal storage
     - Standalone range reasoning
   * - Inputs
     - Recorded node/edge/claim versions, valid instant and system horizon
     - Explicit ``GenericProgram`` and ``TemporalFact`` values with validity sets
   * - Answer
     - Which historical version wins at that point?
     - On which complete intervals does each conclusion follow?
   * - Persistence
     - Version writes append history; historical reads do not run rules
     - Evaluation returns an in-memory result; source admission/publication are separate

Intersect premises without filling gaps
-----------------------------------------

The first edge below has two disjoint valid intervals; the second bridges part
of each. Both premises must be active together to derive two-hop reachability.
Do not turn their union into the envelope ``[5, 25)``: that would invent answers
in the gap. The numbers are integer UTC microseconds on a schematic timeline.

.. list-table:: Active facts and two-hop conclusion on elementary segments
   :header-rows: 1

   * - Fact
     - [0, 5)
     - [5, 10)
     - [10, 20)
     - [20, 25)
     - [25, 30)
   * - edge(a, b)
     - Yes
     - Yes
     - No
     - Yes
     - Yes
   * - edge(b, c)
     - No
     - Yes
     - Yes
     - Yes
     - No
   * - reach(a, c)
     - No
     - Yes
     - No
     - Yes
     - No

**Standalone runnable example, base install only.** No database, serializer or
solver is selected. ``TemporalFact`` pairs a typed fact with an ``IntervalSet``;
all rule variables, inputs and expected intervals are defined here.

.. code-block:: python

   from gestaltdb.reasoning import (
       GenericEngine, GenericProgram, GenericRule, Interval, IntervalSet,
       ReasoningLimits, TemporalFact, TypedAtom, TypedFact, Variable, evaluate_range,
       verify_closure,
   )

   x, y, z = Variable("x"), Variable("y"), Variable("z")
   program = GenericProgram([
       GenericRule("direct", [TypedAtom("edge", [x, y])], (), TypedAtom("reach", [x, y])),
       GenericRule("extend", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])],
                   (), TypedAtom("reach", [x, z])),
   ])
   facts = [TemporalFact(TypedFact("edge", ["a", "b"]),
                         IntervalSet([Interval(0, 10), Interval(20, 30)])),
            TemporalFact(TypedFact("edge", ["b", "c"]), IntervalSet([Interval(5, 25)]))]
   closure = evaluate_range(program, facts, limits=ReasoningLimits())
   goal = TypedFact("reach", ["a", "c"])
   assert closure.answers(goal).intervals == (Interval(5, 10), Interval(20, 25))
   assert {support.segment for support in closure.evidence(goal)} == {
       Interval(5, 10), Interval(20, 25),
   }
   for point in (0, 4, 5, 9, 10, 19, 20, 24, 25, 29, 30):
       active = [item.fact for item in facts if point in item.valid]
       fresh = GenericEngine().evaluate(program, active, limits=ReasoningLimits())
       assert set(closure.at(point)) == set(fresh.facts)
   report = verify_closure(program, closure, limits=ReasoningLimits())
   assert not report.proofs_truncated

``answers`` gives the normalized validity union; ``at`` returns the logical facts
at a point. ``evidence`` returns retained per-segment derivations, not a single
proof that applies throughout the min/max envelope. In this standalone API those
premises are logical facts, not authenticated historical database version IDs.

Why segment evaluation covers the range
----------------------------------------

Every validity start/end is a change point. Between adjacent change points the
active input set is constant, so evaluation covers that entire elementary
segment. The result unions only segments where a conclusion follows. A coarse
grid of sampled instants could miss short intervals or gaps; range evaluation
does not approximate the timeline with such a grid.

Active inputs use an event sweep instead of a complete input scan at each
segment. ``verify_closure`` reconstructs segmentation and reevaluates points
through its separate scan path. This checks conformance against the supplied
program/base facts; it is not independent source authentication, verification of
real-world truth, or permission to publish caller-supplied results. Physical
sweep details are in :doc:`reasoning-temporal-sweep`; reference measurements are
in :doc:`reasoning-conformance`.

Continue with scope, updates and storage
----------------------------------------

Use :doc:`reasoning-range-tutorial` for independently runnable absence, aggregation,
maintenance and equality-scope examples. Equality representatives belong to a
single selected segment, never a physical/global graph merge. Supported scoped
negation/aggregates run against completed per-segment inputs, not an implicit
closed-world policy for all time.

Budgets and evidence completeness
----------------------------------------

``max_segments``, ``max_supports``, sweep limits and per-segment engine limits
bound their documented work dimensions; they do not guarantee wall time or RSS.
Logical budget/cancellation failures do not yield successful partial closures.
Proof-node/edge budgets can instead truncate retained explanations while leaving
logical conclusions complete; check ``proofs_truncated`` and ``missing_evidence``.
Pure-positive support discovery and stratified first-witness evidence have
distinct contracts (:doc:`reasoning-ranges`).

For authenticated finite-range database input use :doc:`reasoning-range-admission`.
For computation-owned guarded persistence use :doc:`reasoning-bound-range-publication`.
These are separate stages with exact source/program/state and target checks, not
automatic consequences of ``evaluate_range``. :doc:`reasoning-conformance` explains
verification, and :doc:`reasoning-ranges` retains update and publication contracts.
