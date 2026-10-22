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
