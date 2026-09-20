from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from gestaltdb import TemporalContext as RootTemporalContext
from gestaltdb import TemporalInstant as RootTemporalInstant
from gestaltdb import TemporalInterval as RootTemporalInterval
from gestaltdb.temporal import (
    TemporalContext,
    TemporalInstant,
    TemporalInterval,
    as_temporal_context,
    as_temporal_instant,
    as_temporal_interval,
    datetime_to_epoch_microseconds,
    epoch_microseconds_to_datetime,
)


def test_datetime_conversion_is_exact_and_normalizes_to_utc():
    source = datetime(2025, 1, 2, 3, 4, 5, 678901, tzinfo=timezone(timedelta(hours=2)))
    expected = datetime(2025, 1, 2, 1, 4, 5, 678901, tzinfo=timezone.utc)

    value = datetime_to_epoch_microseconds(source)

    assert epoch_microseconds_to_datetime(value) == expected
    assert TemporalInstant.from_datetime(source).to_datetime() == expected


def test_pre_epoch_instants_round_trip_and_sortable_encoding_orders_chronologically():
    instants = [
        TemporalInstant.from_datetime(datetime(1900, 1, 1, tzinfo=timezone.utc)),
        TemporalInstant(0),
        TemporalInstant.from_datetime(datetime(2100, 1, 1, tzinfo=timezone.utc)),
    ]

    encodings = [instant.encode_sortable() for instant in instants]

    assert instants[0].epoch_microseconds < 0
    assert encodings == sorted(encodings)
    assert [TemporalInstant.decode_sortable(value) for value in encodings] == instants


def test_temporal_instant_parses_iso_offsets_and_z_suffix():
    with_z = TemporalInstant.parse("2025-01-01T00:00:00.123456Z")
    with_offset = TemporalInstant.parse("2025-01-01T02:00:00.123456+02:00")

    assert with_z == with_offset


@pytest.mark.parametrize(
    "value",
    [
        "2025-01-01",
        "2025-01-01T00:00:00",
        "20250101T000000Z",
        "2025-W01-3T00:00:00Z",
        "2025-01-01T00:00:00.1234567Z",
    ],
)
def test_temporal_instant_parse_rejects_ambiguous_or_lossy_formats(value):
    with pytest.raises(ValueError, match="ISO-8601"):
        TemporalInstant.parse(value)


@pytest.mark.parametrize(
    "value,exception",
    [
        (datetime(2025, 1, 1), ValueError),
        (True, TypeError),
        (1.5, TypeError),
        ("2025-01-01", TypeError),
    ],
)
def test_temporal_instant_rejects_ambiguous_values(value, exception):
    with pytest.raises(exception):
        as_temporal_instant(value)


def test_temporal_instant_accepts_integral_values_and_is_immutable():
    instant = as_temporal_instant(np.int64(42))

    assert instant == TemporalInstant(42)
    assert as_temporal_instant(instant) is instant
    with pytest.raises(FrozenInstanceError):
        instant.epoch_microseconds = 43


def test_temporal_interval_uses_half_open_boundaries():
    interval = TemporalInterval.from_values(10, 20)

    assert interval.contains(10)
    assert interval.contains(19)
    assert not interval.contains(20)
    assert TemporalInterval.from_values(10).contains(1_000_000)


@pytest.mark.parametrize("start,end", [(10, 10), (20, 10)])
def test_temporal_interval_rejects_empty_or_reversed_ranges(start, end):
    with pytest.raises(ValueError, match="greater than start"):
        TemporalInterval.from_values(start, end)


def test_temporal_interval_overlap_and_intersection():
    first = TemporalInterval.from_values(10, 20)
    overlapping = TemporalInterval.from_values(15, 30)
    adjacent = TemporalInterval.from_values(20, 30)
    open_ended = TemporalInterval.from_values(25)

    assert first.overlaps(overlapping)
    assert first.intersection(overlapping) == TemporalInterval.from_values(15, 20)
    assert not first.overlaps(adjacent)
    assert first.intersection(adjacent) is None
    assert overlapping.overlaps(open_ended)


def test_temporal_context_validates_selection_kind():
    with pytest.raises(ValueError, match="exactly one"):
        TemporalContext()
    with pytest.raises(ValueError, match="exactly one"):
        TemporalContext(TemporalInstant(1), TemporalInterval.from_values(1, 2))
    with pytest.raises(ValueError, match="finite end"):
        TemporalContext(interval=TemporalInterval.from_values(1))


def test_temporal_context_matches_points_and_intervals():
    as_of = TemporalContext.as_of(20)
    window = TemporalContext.during(20, 30)

    assert as_of.is_as_of and not as_of.is_window
    assert as_of.matches(TemporalInstant(20))
    assert as_of.matches(TemporalInterval.from_values(10, 21))
    assert not as_of.matches(TemporalInterval.from_values(10, 20))
    assert window.is_window and not window.is_as_of
    assert window.matches(TemporalInstant(20))
    assert window.matches(20)
    assert not window.matches(TemporalInstant(30))
    assert window.matches(TemporalInterval.from_values(10, 21))
    assert not window.matches(TemporalInterval.from_values(10, 20))


def test_temporal_adapters_and_public_exports():
    interval = TemporalInterval.from_values(1, 2)
    context = TemporalContext.as_of(1)

    assert as_temporal_interval(interval) is interval
    assert as_temporal_interval((1, 2)) == interval
    assert as_temporal_context(context) is context
    assert as_temporal_context(1) == context
    assert RootTemporalInstant is TemporalInstant
    assert RootTemporalInterval is TemporalInterval
    assert RootTemporalContext is TemporalContext
