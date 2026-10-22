:orphan:

Storage-neutral interval sets and support segments
============================================================

``gestaltdb.reasoning.intervals`` is the scalar interval prototype for
temporal reasoning: half-open ``[start, end)`` spans over integer
microseconds (instant precision, the temporal epoch scale) with ``None``
for unbounded ends. Empty ``[t, t)`` spans are unrepresentable and fail
closed. Nothing here touches storage or temporal records; callers map
epoch values in and out explicitly, and future Polars/native operators
must match these semantics.

.. code-block:: python

   from gestaltdb.reasoning import Interval, IntervalSet, SupportSegment, coalesce_segments

   truth = IntervalSet([Interval(0, 5), Interval(3, 9), Interval(20, 30)])
   assert truth.intervals == (Interval(0, 9), Interval(20, 30))
   assert truth.difference(Interval(4, 21)).intervals == (
       Interval(0, 4), Interval(21, 30))
   segments = coalesce_segments([SupportSegment(Interval(0, 5), "rule-a"),
                                 SupportSegment(Interval(3, 9), "rule-b")])
   assert len(segments) == 2  # different supports never merge

Two views, one rule
----------------------------------------

``IntervalSet`` is the truth-coalescing view: normalized sorted disjoint
intervals where union, intersection and difference tile exactly —
touching intervals merge, gaps survive, and disjoint spans are never
replaced by a min/max envelope. ``SupportSegment`` is the support-history
view: an interval plus its support identity, coalesced only within one
support. A point's covering supports partition its context without path
enumeration. Bounds (``max_intervals``, ``max_segments``) fail closed
instead of truncating.

Measured cost
----------------------------------------

5,000-interval Sweep workloads complete in milliseconds single-threaded:
union ~8ms, difference ~5ms, intersection ~3ms, support coalescing ~3ms —
all linear two-pointer sweeps with bounded memory. The 300-case seeded
property suite checks union/intersection/difference against an independent
point-set oracle over gaps, touching boundaries, unbounded ends and
carved corrections.
