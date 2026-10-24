:orphan:

Bounded temporal event-sweep prototype
======================================

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

This scheduler backs range evaluation: ``evaluate_range`` and
``update_range`` schedule active inputs through it instead of scanning every
input at every segment, with row-to-fact mapping preserving overlapping
multiplicity and ``verify_closure`` independently re-checking segment
identity through its own scan path. Segment-memoized reuse keys in
``update_range`` are unchanged. No persistent-evidence or publication
guarantee is added; limits still fail closed without partial output.
