"""Canonical temporal values shared by future graph and sampling APIs.

This module defines storage-independent temporal semantics. It does not make
``GraphDB`` records or sampler snapshots temporal by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time as time_type, timedelta, timezone
from numbers import Integral
from typing import Optional, Tuple, Union


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECONDS_PER_SECOND = 1_000_000
_MICROSECONDS_PER_DAY = 86_400 * _MICROSECONDS_PER_SECOND
_SORTABLE_OFFSET = 1 << 63
_MIN_SORTABLE_VALUE = -(1 << 63)
_MAX_SORTABLE_VALUE = (1 << 63) - 1
_ISO_INSTANT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|z|[+-]\d{2}:\d{2})$"
)


def datetime_to_epoch_microseconds(value: datetime) -> int:
    """Normalize an aware datetime to exact UTC epoch microseconds."""
    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (
        delta.days * _MICROSECONDS_PER_DAY
        + delta.seconds * _MICROSECONDS_PER_SECOND
        + delta.microseconds
    )


def epoch_microseconds_to_datetime(value: int) -> datetime:
    """Convert epoch microseconds to a timezone-aware UTC datetime."""
    microseconds = _coerce_epoch_microseconds(value)
    try:
        return _EPOCH + timedelta(microseconds=microseconds)
    except OverflowError as exc:
        raise ValueError("epoch microseconds are outside the datetime range") from exc


def _coerce_epoch_microseconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError("epoch microseconds must be an integer")
    return int(value)


@dataclass(frozen=True, order=True)
class TemporalInstant:
    """An exact UTC instant represented as signed epoch microseconds."""

    epoch_microseconds: int

    def __post_init__(self) -> None:
        value = _coerce_epoch_microseconds(self.epoch_microseconds)
        epoch_microseconds_to_datetime(value)
        object.__setattr__(self, "epoch_microseconds", value)

    @classmethod
    def from_datetime(cls, value: datetime) -> "TemporalInstant":
        return cls(datetime_to_epoch_microseconds(value))

    @classmethod
    def from_value(cls, value: "TemporalInput") -> "TemporalInstant":
        return as_temporal_instant(value)

    @classmethod
    def parse(cls, value: str) -> "TemporalInstant":
        """Parse an extended ISO-8601 datetime with an explicit UTC offset."""
        if not isinstance(value, str):
            raise TypeError("temporal instant text must be a string")
        if _ISO_INSTANT_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid ISO-8601 temporal instant")
        normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("invalid ISO-8601 temporal instant") from exc
        return cls.from_datetime(parsed)

    @classmethod
    def decode_sortable(cls, value: bytes) -> "TemporalInstant":
        """Decode the eight-byte chronological encoding from ``encode_sortable``."""
        if not isinstance(value, bytes):
            raise TypeError("sortable temporal encoding must be bytes")
        if len(value) != 8:
            raise ValueError("sortable temporal encoding must contain exactly 8 bytes")
        return cls(int.from_bytes(value, byteorder="big", signed=False) - _SORTABLE_OFFSET)

    def to_datetime(self) -> datetime:
        return epoch_microseconds_to_datetime(self.epoch_microseconds)

    def encode_sortable(self) -> bytes:
        """Encode the instant so lexicographic byte order is chronological."""
        if not _MIN_SORTABLE_VALUE <= self.epoch_microseconds <= _MAX_SORTABLE_VALUE:
            raise ValueError("epoch microseconds cannot be represented in 8 bytes")
        return (self.epoch_microseconds + _SORTABLE_OFFSET).to_bytes(
            8, byteorder="big", signed=False
        )


TemporalInput = Union[TemporalInstant, datetime, int]


def as_temporal_instant(value: TemporalInput) -> TemporalInstant:
    """Normalize an instant, aware datetime, or epoch-microsecond integer."""
    if isinstance(value, TemporalInstant):
        return value
    if isinstance(value, datetime):
        return TemporalInstant.from_datetime(value)
    return TemporalInstant(_coerce_epoch_microseconds(value))


@dataclass(frozen=True)
class TemporalInterval:
    """A non-empty half-open interval ``[start, end)``.

    ``end=None`` denotes positive infinity.
    """

    start: TemporalInstant
    end: Optional[TemporalInstant] = None

    def __post_init__(self) -> None:
        start = as_temporal_instant(self.start)
        end = None if self.end is None else as_temporal_instant(self.end)
        if end is not None and end <= start:
            raise ValueError("interval end must be greater than start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @classmethod
    def from_values(
        cls, start: TemporalInput, end: Optional[TemporalInput] = None
    ) -> "TemporalInterval":
        return cls(as_temporal_instant(start), None if end is None else as_temporal_instant(end))

    @classmethod
    def parse(cls, start: str, end: Optional[str] = None) -> "TemporalInterval":
        return cls(
            TemporalInstant.parse(start),
            None if end is None else TemporalInstant.parse(end),
        )

    def contains(self, value: TemporalInput) -> bool:
        instant = as_temporal_instant(value)
        return self.start <= instant and (self.end is None or instant < self.end)

    def overlaps(self, other: "TemporalInterval") -> bool:
        if not isinstance(other, TemporalInterval):
            raise TypeError("other must be a TemporalInterval")
        return (other.end is None or self.start < other.end) and (
            self.end is None or other.start < self.end
        )

    def intersection(self, other: "TemporalInterval") -> Optional["TemporalInterval"]:
        """Return the non-empty intersection, or ``None`` for disjoint intervals."""
        if not self.overlaps(other):
            return None
        start = max(self.start, other.start)
        if self.end is None:
            end = other.end
        elif other.end is None:
            end = self.end
        else:
            end = min(self.end, other.end)
        return TemporalInterval(start, end)


TemporalIntervalInput = Union[
    TemporalInterval, Tuple[TemporalInput, Optional[TemporalInput]]
]


def as_temporal_interval(value: TemporalIntervalInput) -> TemporalInterval:
    """Normalize an interval or a ``(start, end)`` pair."""
    if isinstance(value, TemporalInterval):
        return value
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError("temporal interval must be a TemporalInterval or a two-item tuple")
    return TemporalInterval.from_values(value[0], value[1])


@dataclass(frozen=True)
class TemporalContext:
    """A point-in-time or finite-window temporal selection."""

    instant: Optional[TemporalInstant] = None
    interval: Optional[TemporalInterval] = None

    def __post_init__(self) -> None:
        if (self.instant is None) == (self.interval is None):
            raise ValueError("temporal context requires exactly one instant or interval")
        if self.instant is not None:
            object.__setattr__(self, "instant", as_temporal_instant(self.instant))
        if self.interval is not None:
            interval = as_temporal_interval(self.interval)
            if interval.end is None:
                raise ValueError("temporal context windows require a finite end")
            object.__setattr__(self, "interval", interval)

    @classmethod
    def as_of(cls, value: TemporalInput) -> "TemporalContext":
        return cls(instant=as_temporal_instant(value))

    @classmethod
    def during(cls, start: TemporalInput, end: TemporalInput) -> "TemporalContext":
        return cls(interval=TemporalInterval.from_values(start, end))

    @property
    def is_as_of(self) -> bool:
        return self.instant is not None

    @property
    def is_window(self) -> bool:
        return self.interval is not None

    def matches(self, value: Union[TemporalInterval, TemporalInput]) -> bool:
        """Test a point event or validity interval against this selection."""
        if isinstance(value, TemporalInterval):
            if self.instant is not None:
                return value.contains(self.instant)
            return value.overlaps(self.interval)  # type: ignore[arg-type]
        instant = as_temporal_instant(value)
        if self.instant is not None:
            return instant == self.instant
        return self.interval.contains(instant)  # type: ignore[union-attr]


def as_temporal_context(
    value: Union[TemporalContext, TemporalInput]
) -> TemporalContext:
    """Normalize a context or adapt an instant-like value to an as-of context."""
    if isinstance(value, TemporalContext):
        return value
    return TemporalContext.as_of(value)


@dataclass(frozen=True, order=True)
class TemporalDate:
    """A calendar date without time or timezone."""

    value: date_type

    def __post_init__(self) -> None:
        if isinstance(self.value, datetime) or not isinstance(self.value, date_type):
            raise TypeError("TemporalDate requires a date")


@dataclass(frozen=True, order=True)
class TemporalLocalTime:
    """A timezone-free time represented as microseconds since midnight."""

    microseconds: int

    def __post_init__(self) -> None:
        value = _coerce_epoch_microseconds(self.microseconds)
        if not 0 <= value < _MICROSECONDS_PER_DAY:
            raise ValueError("local time must be within one day")
        object.__setattr__(self, "microseconds", value)

    @classmethod
    def from_time(cls, value: time_type) -> "TemporalLocalTime":
        if not isinstance(value, time_type) or value.tzinfo is not None:
            raise ValueError("local time requires a timezone-free time")
        return cls(
            ((value.hour * 60 + value.minute) * 60 + value.second) * _MICROSECONDS_PER_SECOND
            + value.microsecond
        )


@dataclass(frozen=True, eq=False)
class TemporalTime:
    """A fixed-offset time of day."""

    local_microseconds: int
    offset_seconds: int

    def __post_init__(self) -> None:
        local = TemporalLocalTime(self.local_microseconds).microseconds
        offset = _coerce_epoch_microseconds(self.offset_seconds)
        if not -86_399 <= offset <= 86_399:
            raise ValueError("time offset must be less than 24 hours")
        if offset % 60:
            raise ValueError("time offset must use whole minutes")
        object.__setattr__(self, "local_microseconds", local)
        object.__setattr__(self, "offset_seconds", offset)

    @property
    def utc_microseconds(self) -> int:
        return (self.local_microseconds - self.offset_seconds * _MICROSECONDS_PER_SECOND) % _MICROSECONDS_PER_DAY

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TemporalTime):
            return NotImplemented
        return self.utc_microseconds == other.utc_microseconds

    def __hash__(self) -> int:
        return hash((TemporalTime, self.utc_microseconds))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TemporalTime):
            return NotImplemented
        return self.utc_microseconds < other.utc_microseconds


@dataclass(frozen=True, order=True)
class TemporalLocalDateTime:
    """A timezone-free local date and time."""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime) or self.value.tzinfo is not None:
            raise ValueError("local datetime requires a timezone-free datetime")


@dataclass(frozen=True, order=True)
class TemporalDuration:
    """An exact signed duration represented as total microseconds."""

    total_microseconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_microseconds", _coerce_epoch_microseconds(self.total_microseconds))

    @classmethod
    def from_timedelta(cls, value: timedelta) -> "TemporalDuration":
        if not isinstance(value, timedelta):
            raise TypeError("TemporalDuration requires a timedelta")
        return cls(
            value.days * _MICROSECONDS_PER_DAY
            + value.seconds * _MICROSECONDS_PER_SECOND
            + value.microseconds
        )

    def to_timedelta(self) -> timedelta:
        return timedelta(microseconds=self.total_microseconds)


__all__ = [
    "TemporalContext",
    "TemporalDate",
    "TemporalDuration",
    "TemporalInstant",
    "TemporalInterval",
    "TemporalLocalDateTime",
    "TemporalLocalTime",
    "TemporalTime",
    "as_temporal_context",
    "as_temporal_instant",
    "as_temporal_interval",
    "datetime_to_epoch_microseconds",
    "epoch_microseconds_to_datetime",
]
