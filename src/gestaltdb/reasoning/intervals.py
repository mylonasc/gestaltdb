"""Storage-neutral interval-set algebra with support-segment contracts.

All bounds are integer microseconds at instant precision — the same epoch
scale as temporal instants — or ``None`` for an unbounded end. Every
interval is half-open ``[start, end)``; ``[t, t)`` is empty and
unrepresentable, so touching intervals ``[a, b)`` and ``[b, c)`` tile
without overlap or gap. Nothing here touches storage, backends or temporal
records; callers map epoch values in and out explicitly.

``IntervalSet`` is the truth-coalescing view: normalized sorted disjoint
intervals with union, intersection and difference. ``SupportSegment`` is the
support-history view: an interval plus its support identity, coalesced only
within one support. Disjoint intervals are never replaced by a min/max
envelope in either view. Polars/native interval operators stay deferred;
this scalar prototype is the semantics they must match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _bound(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntervalError(f"{name} must be an integer microsecond or None")
    return value


class IntervalError(ValueError):
    """Base error for invalid interval values or operations."""


@dataclass(frozen=True)
class Interval:
    """One half-open ``[start, end)`` span; ``None`` is an unbounded end."""

    start: int | None
    end: int | None

    def __post_init__(self) -> None:
        start = _bound(self.start, "start")
        end = _bound(self.end, "end")
        if start is not None and end is not None and start >= end:
            raise IntervalError("half-open intervals require start < end; [t, t) is empty")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def contains(self, point: int) -> bool:
        """Half-open membership with instant precision."""
        if isinstance(point, bool) or not isinstance(point, int):
            raise IntervalError("point must be an integer microsecond")
        return (self.start is None or self.start <= point) and (self.end is None or point < self.end)

    def overlaps(self, other: Interval) -> bool:
        if not isinstance(other, Interval):
            raise IntervalError("other must be an Interval")
        return self._lo() < other._hi() and other._lo() < self._hi()

    def touches(self, other: Interval) -> bool:
        """Abutting without overlap: a finite end equals the other's start."""
        if not isinstance(other, Interval):
            raise IntervalError("other must be an Interval")
        return ((self.end is not None and self.end == other.start)
                or (other.end is not None and other.end == self.start))

    def _lo(self):
        return float("-inf") if self.start is None else self.start

    def _hi(self):
        return float("inf") if self.end is None else self.end

    def intersect(self, other: Interval) -> Interval | None:
        if not isinstance(other, Interval):
            raise IntervalError("other must be an Interval")
        start = self.start if other.start is None or (self.start is not None and self.start >= other.start) else other.start
        end = self.end if other.end is None or (self.end is not None and (other.end is None or self.end <= other.end)) else other.end
        if start is not None and end is not None and start >= end:
            return None
        return Interval(start, end)

    def difference(self, other: Interval) -> tuple[Interval, ...]:
        """Carve ``other`` out, returning zero, one or two disjoint pieces."""
        if not isinstance(other, Interval):
            raise IntervalError("other must be an Interval")
        cut = self.intersect(other)
        if cut is None:
            return (self,)
        pieces = []
        if (self.start is None, self.start) != (cut.start is None, cut.start) and (
                self.start is None or cut.start is None or self.start < cut.start):
            pieces.append(Interval(self.start, cut.start))
        if (self.end is None, self.end) != (cut.end is None, cut.end) and (
                self.end is None or cut.end is None or cut.end < self.end):
            pieces.append(Interval(cut.end, self.end))
        return tuple(pieces)

    @property
    def digest(self) -> str:
        import hashlib
        import json

        return "sha256:" + hashlib.sha256(json.dumps(
            ["gestaltdb-interval-v1", self.start, self.end],
            ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IntervalSet:
    """Normalized sorted disjoint union; touching intervals are coalesced."""

    intervals: tuple[Interval, ...] = ()
    max_intervals: int = 10_000

    def __post_init__(self) -> None:
        if isinstance(self.max_intervals, bool) or not isinstance(self.max_intervals, int) or self.max_intervals < 1:
            raise IntervalError("max_intervals must be a positive integer")
        if not isinstance(self.intervals, (tuple, list)):
            raise IntervalError("intervals must be a tuple or list")
        if not all(isinstance(item, Interval) for item in self.intervals):
            raise IntervalError("intervals must contain only Interval values")
        merged = _normalize(list(self.intervals))
        if len(merged) > self.max_intervals:
            raise IntervalError(f"interval union exceeded max_intervals={self.max_intervals}")
        object.__setattr__(self, "intervals", tuple(merged))

    def __contains__(self, point: object) -> bool:
        return isinstance(point, int) and not isinstance(point, bool) and any(
            item.contains(point) for item in self.intervals)

    def __len__(self) -> int:
        return len(self.intervals)

    @property
    def digest(self) -> str:
        import hashlib
        import json

        return "sha256:" + hashlib.sha256(json.dumps(
            ["gestaltdb-interval-set-v1", [[item.start, item.end] for item in self.intervals]],
            ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def union(self, other: IntervalSet | Interval) -> IntervalSet:
        return IntervalSet((*self.intervals, *(_as_tuple(other))), max_intervals=self.max_intervals)

    def intersection(self, other: IntervalSet | Interval) -> IntervalSet:
        right = _as_tuple(other)
        parts: list[Interval] = []
        skip = 0
        for left in self.intervals:
            while skip < len(right) and _end_at_or_before_start(right[skip].end, left.start):
                skip += 1
            index = skip
            while index < len(right) and _start_before_end(right[index].start, left.end):
                cut = left.intersect(right[index])
                if cut is not None:
                    parts.append(cut)
                index += 1
        return IntervalSet(tuple(parts), max_intervals=self.max_intervals)

    def difference(self, other: IntervalSet | Interval) -> IntervalSet:
        cuts = _as_tuple(other)
        out: list[Interval] = []
        skip = 0
        for item in self.intervals:
            while skip < len(cuts) and _end_at_or_before_start(cuts[skip].end, item.start):
                skip += 1
            current = item.start
            index = skip
            while index < len(cuts) and _start_before_end(cuts[index].start, item.end):
                if _start_after(cuts[index].start, current):
                    out.append(Interval(current, cuts[index].start))
                current = _advance_past(current, cuts[index].end)
                if current is _EXHAUSTED:
                    break
                index += 1
            if current is not _EXHAUSTED and _start_before_end(current, item.end):
                out.append(Interval(current, item.end))
        return IntervalSet(tuple(out), max_intervals=self.max_intervals)


def _as_tuple(value: IntervalSet | Interval) -> tuple[Interval, ...]:
    if isinstance(value, Interval):
        return (value,)
    if isinstance(value, IntervalSet):
        return value.intervals
    raise IntervalError("operand must be an Interval or IntervalSet")


def _normalize(items: list[Interval]) -> list[Interval]:
    ordered = sorted(items, key=lambda item: (item.start is not None, item.start, item.end is None, item.end))
    merged: list[Interval] = []
    for item in ordered:
        if merged and (merged[-1].overlaps(item) or merged[-1].touches(item)):
            last = merged.pop()
            merged.append(Interval(last.start, _max_end(last.end, item.end)))
        else:
            merged.append(item)
    return merged


_EXHAUSTED: object = object()


def _max_end(first: int | None, second: int | None) -> int | None:
    if first is None or second is None:
        return None
    return first if first >= second else second


def _end_at_or_before_start(end: int | None, start: int | None) -> bool:
    """An end bound (+inf when None) at or before a start bound (-inf when None)."""
    if end is None or start is None:
        return False
    return end <= start


def _start_before_end(start: int | None, end: int | None) -> bool:
    """A start bound (-inf when None) strictly before an end bound (+inf when None)."""
    if start is None or end is None:
        return True
    return start < end


def _start_after(value: int | None, current: int | None) -> bool:
    """A start bound strictly after the current sweep position."""
    if value is None:
        return False
    if current is None:
        return True
    return value > current


def _advance_past(current: int | None, end: int | None):
    """Move the sweep position past a cut end; exhausts on unbounded ends."""
    if end is None:
        return _EXHAUSTED
    if current is None or end > current:
        return end
    return current


@dataclass(frozen=True)
class SupportSegment:
    """One support-history segment: an interval plus its support identity."""

    interval: Interval
    support: str

    def __post_init__(self) -> None:
        if not isinstance(self.interval, Interval):
            raise IntervalError("interval must be an Interval")
        if not isinstance(self.support, str) or not self.support:
            raise IntervalError("support must be a non-empty string")


def coalesce_segments(segments: Iterable[SupportSegment],
                      *, max_segments: int = 10_000) -> tuple[SupportSegment, ...]:
    """Merge overlapping/touching segments only within one support.

    Segments from different supports are never merged, and disjoint
    same-support intervals are never replaced by a min/max envelope: gaps
    are preserved exactly.
    """
    if isinstance(max_segments, bool) or not isinstance(max_segments, int) or max_segments < 1:
        raise IntervalError("max_segments must be a positive integer")
    try:
        items = list(segments)
    except TypeError as exc:
        raise IntervalError("segments must be iterable") from exc
    if not all(isinstance(item, SupportSegment) for item in items):
        raise IntervalError("segments must contain only SupportSegment values")
    grouped: dict[str, list[Interval]] = {}
    for item in items:
        grouped.setdefault(item.support, []).append(item.interval)
    out = []
    for support in sorted(grouped):
        for interval in _normalize(grouped[support]):
            out.append(SupportSegment(interval, support))
    if len(out) > max_segments:
        raise IntervalError(f"segment coalescing exceeded max_segments={max_segments}")
    return tuple(out)
