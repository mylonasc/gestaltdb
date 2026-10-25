"""Bounded event-sweep scheduler for temporal closure reuse.

Row ordinals preserve overlapping assertion membership within this exact input
tuple. They are not authenticated evidence IDs or durable version locators.
Finite gaps are retained as empty active sets, matching change_segments.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable

from .intervals import Interval
from .model import ReasoningError, ReasoningLimitError, check_cancelled
from .temporal_closure import TemporalFact
from .typed import TypedFact, _sort_key


@dataclass(frozen=True)
class ActiveSegment:
    interval: Interval
    rows: tuple[int, ...]
    facts: tuple[TypedFact, ...]


def active_segments(facts: Iterable[TemporalFact], *, max_rows: int = 100_000,
                    max_events: int = 200_000, max_segments: int = 10_000,
                    max_memberships: int = 1_000_000,
                    cancel: Callable[[], bool] | None = None) -> tuple[ActiveSegment, ...]:
    """Schedule exact active input rows with bounded events and output size.

    All output is returned atomically. End events precede starts at the same
    boundary, so row membership respects half-open validity. Multiple active
    rows supporting the same fact remain distinct until the last row expires.
    """
    for name, value in (("max_rows", max_rows), ("max_events", max_events),
                        ("max_segments", max_segments), ("max_memberships", max_memberships)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReasoningError(f"{name} must be a positive integer")
    items, active, events = [], set(), defaultdict(lambda: (set(), set()))
    below = above = False
    event_count = 0
    for row, item in enumerate(facts):
        check_cancelled(cancel)
        if row >= max_rows:
            raise ReasoningLimitError("sweep exceeded max_rows")
        if not isinstance(item, TemporalFact):
            raise ReasoningError("facts must contain TemporalFact values")
        items.append(item)
        for interval in item.valid.intervals:
            check_cancelled(cancel)
            event_count += 2
            if event_count > max_events:
                raise ReasoningLimitError("sweep exceeded max_events")
            if interval.start is None:
                active.add(row)
                below = True
            else:
                events[interval.start][1].add(row)
            if interval.end is None:
                above = True
            else:
                events[interval.end][0].add(row)
    cuts, output, memberships = sorted(events), [], 0

    def emit(start, end):
        nonlocal memberships
        check_cancelled(cancel)
        if len(output) >= max_segments:
            raise ReasoningLimitError("sweep exceeded max_segments")
        memberships += len(active)
        if memberships > max_memberships:
            raise ReasoningLimitError("sweep exceeded max_memberships")
        rows = tuple(sorted(active))
        output.append(ActiveSegment(Interval(start, end), rows,
                                    tuple(sorted({items[row].fact for row in rows}, key=_sort_key))))

    if not cuts:
        if below or above:
            emit(None, None)
    else:
        if below:
            emit(None, cuts[0])
        for index, cut in enumerate(cuts):
            ends, starts = events[cut]
            active.difference_update(ends)
            active.update(starts)
            if index + 1 < len(cuts):
                emit(cut, cuts[index + 1])
            elif above:
                emit(cut, None)
    check_cancelled(cancel)
    return tuple(output)
