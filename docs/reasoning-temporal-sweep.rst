:orphan:

Bounded temporal event-sweep prototype
=====================================

``gestaltdb.reasoning.temporal_sweep.active_segments`` accepts ``TemporalFact``
values and returns immutable ``ActiveSegment`` records. Each contains an
interval, active input-row ordinals and deduplicated logical facts. Overlapping
rows asserting the same fact remain separate until their own validity ends.
Unbounded intervals, half-open boundary ties and finite empty gaps preserve the
segment boundaries of ``change_segments``.

Row ordinals identify membership within this exact input order only. They are
not durable claim-version IDs or authenticated evidence locators. Limits bound
input rows, endpoint events, segments and total emitted row memberships;
over-budget or cancelled runs return no partial output. Emitting dense active
sets still costs output-sized memory and work.

This is a standalone scheduler with seeded scalar-membership oracle tests.
``evaluate_range`` and ``update_range`` do not yet use it. Wiring it into
temporal closure depends on program-bound reuse and representative measurements;
no production temporal speedup or persistent-evidence guarantee is claimed.
