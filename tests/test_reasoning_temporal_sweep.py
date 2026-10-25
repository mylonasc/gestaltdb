"""Independent point-membership oracle for event-scheduled assertion rows."""

import random

import pytest

from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningLimitError
from gestaltdb.reasoning.temporal_closure import TemporalFact, change_segments
from gestaltdb.reasoning.temporal_sweep import active_segments
from gestaltdb.reasoning.typed import TypedFact


def record(name, *intervals):
    return TemporalFact(TypedFact("p", [name]), IntervalSet([Interval(*part) for part in intervals]))


def check_oracle(items):
    scheduled = active_segments(items)
    assert tuple(segment.interval for segment in scheduled) == change_segments(items)
    for segment in scheduled:
        span = segment.interval
        point = span.start if span.start is not None else (span.end - 1 if span.end is not None else 0)
        rows = tuple(row for row, item in enumerate(items) if point in item.valid)
        assert segment.rows == rows
        assert set(segment.facts) == {items[row].fact for row in rows}


def test_overlap_unbounded_boundary_ties_gaps_and_duplicates():
    check_oracle([record("a", (None, 0), (4, None)), record("a", (-2, 6)),
                  record("b", (0, 1)), record("b", (0, 1))])
    check_oracle([record("a", (0, 1)), record("a", (4, 5))])
    assert active_segments([record("a", (0, 1)), record("a", (4, 5))])[1].rows == ()
    check_oracle([record("a", (None, None))])
    assert active_segments([]) == ()


def test_seeded_scalar_parity():
    rng = random.Random(254)
    for _ in range(200):
        items = []
        for row in range(rng.randrange(1, 12)):
            start = rng.randrange(-10, 10)
            end = start + rng.randrange(1, 8)
            items.append(record(str(row % 3), (None if rng.random() < .1 else start,
                                               None if rng.random() < .1 else end)))
        check_oracle(items)


@pytest.mark.parametrize("limit", ["max_rows", "max_events", "max_segments", "max_memberships"])
def test_each_budget_rejects_without_returning_partial_output(limit):
    with pytest.raises(ReasoningLimitError, match=limit):
        active_segments([record("a", (0, 4)), record("b", (1, 3))], **{limit: 1})


def test_cancellation_is_not_a_partial_success():
    with pytest.raises(ReasoningCancelledError):
        active_segments([record("a", (0, 4))], cancel=lambda: True)


def chain_program():
    from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom
    from gestaltdb.reasoning.model import Variable
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
        GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                    TypedAtom("reach", [x, z])),
    ])


def test_scheduled_closure_passes_independent_scan_gate():
    from gestaltdb.reasoning.model import ReasoningLimits
    from gestaltdb.reasoning.temporal_closure import evaluate_range, verify_closure
    rng = random.Random(2541)
    for case in range(25):
        items = []
        for row in range(rng.randrange(1, 10)):
            start = rng.randrange(0, 8)
            end = start + rng.randrange(1, 6)
            items.append(TemporalFact(
                TypedFact("edge", [f"e{rng.randrange(4)}", f"e{rng.randrange(4)}"]),
                IntervalSet([Interval(None if rng.random() < .15 else start,
                                      None if rng.random() < .15 else end)])))
        closure = evaluate_range(chain_program(), items, limits=ReasoningLimits())
        # The independent gate re-derives segmentation and point-evaluates
        # through its own scan path: scheduled closures must verify clean.
        verification = verify_closure(chain_program(), closure, limits=ReasoningLimits())
        assert verification.segments == len(closure.segments)
        assert verification.derivations == len(closure.derivations)


def test_scheduled_active_sets_match_scan_with_multiplicity():
    from gestaltdb.reasoning.temporal_closure import _scheduled_active
    rng = random.Random(90210)
    scan_checks = sweep_memberships = 0
    for _ in range(30):
        items = []
        for row in range(rng.randrange(1, 15)):
            start = rng.randrange(0, 10)
            end = start + rng.randrange(1, 6)
            items.append(record(str(row % 3), (None if rng.random() < .15 else start,
                                               None if rng.random() < .15 else end)))
        scheduled = _scheduled_active(items, max_rows=100_000, max_events=200_000,
                                      max_segments=10_000, max_memberships=1_000_000)
        assert tuple(interval for interval, _ in scheduled) == change_segments(items)
        for interval, facts in scheduled:
            point = interval.start if interval.start is not None else (
                interval.end - 1 if interval.end is not None else 0)
            scan_checks += len(items)
            scanned = [item.fact for item in items if point in item.valid]
            assert sorted(scanned, key=lambda f: (f.predicate, f.values)) == sorted(
                facts, key=lambda f: (f.predicate, f.values))
            # One scheduled entry per active row: overlapping assertions keep
            # their multiplicity exactly like the scan.
            assert len(facts) == len(scanned)
            sweep_memberships += len(facts)
    assert sweep_memberships <= scan_checks


def test_scheduled_update_range_reuses_and_recomputes():
    from gestaltdb.reasoning.model import ReasoningLimits
    from gestaltdb.reasoning.temporal_closure import evaluate_range, update_range
    prog = chain_program()
    base = [TemporalFact(TypedFact("edge", ["a", "b"]), IntervalSet([Interval(0, 10)])),
            TemporalFact(TypedFact("edge", ["b", "c"]), IntervalSet([Interval(5, 15)]))]
    closure = evaluate_range(prog, base, limits=ReasoningLimits())
    assert closure.answers(TypedFact("reach", ["a", "c"])) == IntervalSet([Interval(5, 10)])
    # Unchanged base reuses every segment; identical evidence, no recompute.
    same = update_range(prog, closure, base, limits=ReasoningLimits())
    assert same.recomputed_segments == 0 and same.reused_segments == len(closure.segments)
    assert same.closure.answers(TypedFact("reach", ["a", "c"])) == IntervalSet([Interval(5, 10)])
    # Narrowed validity reuses segments by active-input key with exact evidence:
    # (5,8) and (8,10) hit the same active sets as before, so nothing recomputes.
    moved = [TemporalFact(TypedFact("edge", ["a", "b"]), IntervalSet([Interval(0, 10)])),
             TemporalFact(TypedFact("edge", ["b", "c"]), IntervalSet([Interval(5, 8)]))]
    updated = update_range(prog, closure, moved, limits=ReasoningLimits())
    assert (updated.reused_segments, updated.recomputed_segments) == (3, 0)
    assert updated.closure.answers(TypedFact("reach", ["a", "c"])) == IntervalSet([Interval(5, 8)])
    assert updated.closure == evaluate_range(prog, moved, limits=ReasoningLimits())
    # A genuinely new active combination recomputes only its own segments.
    grown = moved + [TemporalFact(TypedFact("edge", ["c", "d"]), IntervalSet([Interval(3, 6)]))]
    extended = update_range(prog, updated.closure, grown, limits=ReasoningLimits())
    assert extended.recomputed_segments > 0
    assert extended.closure.answers(TypedFact("reach", ["a", "d"])) == IntervalSet([Interval(5, 6)])
    assert extended.closure == evaluate_range(prog, grown, limits=ReasoningLimits())
