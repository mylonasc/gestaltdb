"""Explicit edge-validity traversal times for chronological walks (RWS-06)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..temporal import as_temporal_instant
from .validation import _validate_count
from .walks import RandomWalkBatch


@dataclass(frozen=True)
class TemporalWalkPolicy:
    """Chronology is separate from point/window eligibility filters.

    Interval selection chooses the earliest feasible microsecond forward, or
    latest backward, respecting the current clock and half-open validity.
    Valid-start selection instead treats each version start as its event time.
    Equality is allowed. Restart resets the clock to ``initial_time``.
    """

    order: str = "nondecreasing"
    time_selection: str = "interval"
    initial_time: object | None = None
    max_gap_us: int | None = None
    recency_half_life_us: float | None = None
    reference_time: object | None = None

    def __post_init__(self):
        if self.order not in {"nondecreasing", "nonincreasing"}:
            raise ValueError("order must be nondecreasing or nonincreasing")
        if self.time_selection not in {"interval", "valid_start"}:
            raise ValueError("time_selection must be interval or valid_start")
        for name in ("initial_time", "reference_time"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, as_temporal_instant(value).epoch_microseconds)
        if self.order == "nonincreasing" and self.time_selection == "interval" and self.initial_time is None:
            raise ValueError("backward interval selection requires initial_time")
        if self.max_gap_us is not None:
            _validate_count(self.max_gap_us, "max_gap_us")
        value = self.recency_half_life_us
        if value is not None:
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, float, np.integer, np.floating))
                    or not np.isfinite(value) or value <= 0):
                raise ValueError("recency_half_life_us must be positive and finite")
            if self.reference_time is None:
                raise ValueError("recency weighting requires reference_time")

    def candidates(self, snapshot, edges, clock, query):
        """Return a feasibility mask, chosen microseconds and proximity weights."""
        times = np.zeros(edges.size, dtype=np.int64)
        allowed = np.zeros(edges.size, dtype=bool)
        for index, edge in enumerate(edges):
            lower = int(snapshot.valid_from_us[edge])
            upper = None if snapshot.valid_to_open[edge] else int(snapshot.valid_to_us[edge])
            if query is not None:
                if query.instant is not None:
                    point = query.instant.epoch_microseconds
                    lower = max(lower, point)
                    upper = min(upper, point + 1) if upper is not None else point + 1
                else:
                    lower = max(lower, query.interval.start.epoch_microseconds)
                    end = query.interval.end.epoch_microseconds
                    upper = min(upper, end) if upper is not None else end
            if self.time_selection == "valid_start":
                chosen = int(snapshot.valid_from_us[edge])
            elif self.order == "nondecreasing":
                chosen = max(lower, clock) if clock is not None else lower
            else:
                chosen = min(clock, upper - 1) if upper is not None else clock
            if chosen < lower or (upper is not None and chosen >= upper):
                continue
            if clock is not None:
                if self.order == "nondecreasing" and chosen < clock:
                    continue
                if self.order == "nonincreasing" and chosen > clock:
                    continue
                if self.max_gap_us is not None and abs(chosen - clock) > self.max_gap_us:
                    continue
            times[index], allowed[index] = chosen, True
        weights = np.ones(edges.size)
        if self.recency_half_life_us is not None and np.any(allowed):
            distances = np.array([abs(int(time) - self.reference_time) for time in times[allowed]], dtype=float)
            # A common factor cancels; subtract minimum to retain a unit maximum.
            with np.errstate(over="ignore", under="ignore"):
                weights[allowed] = np.exp2(-(distances - distances.min()) / self.recency_half_life_us)
        return allowed, times, weights


@dataclass(slots=True)
class TemporalWalkBatch(RandomWalkBatch):
    """Policy walk output with aligned times, version IDs and copied provenance.

    The time mask is false for padding/restart jumps; zero is a valid epoch time.
    Source provenance is inherited from the loaded snapshot, not independently
    reauthenticated by sampling. Use snapshot.verify_source for source checks.
    """

    transition_times_us: np.ndarray
    transition_time_mask: np.ndarray
    edge_version_ids: np.ndarray
    source_provenance: dict | None

    def to_numpy(self) -> dict[str, np.ndarray]:
        result = RandomWalkBatch.to_numpy(self)
        result.update(transition_times_us=self.transition_times_us,
                      transition_time_mask=self.transition_time_mask,
                      edge_version_ids=self.edge_version_ids)
        return result

    def to_external(self, snapshot):
        result = RandomWalkBatch.to_external(self, snapshot)
        result.update(transition_times_us=self.transition_times_us,
                      transition_time_mask=self.transition_time_mask,
                      edge_version_ids=self.edge_version_ids)
        return result
