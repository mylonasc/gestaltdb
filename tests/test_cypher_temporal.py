"""Cypher temporal value construction and expression semantics."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from gestaltdb import (
    TemporalDate,
    TemporalDuration,
    TemporalInstant,
    TemporalLocalDateTime,
    TemporalLocalTime,
    TemporalTime,
)
from gestaltdb.cypher import execute
from tests.test_cypher import FakeCypherGraph


def _query(expression: str, *, parameters=None):
    query = f"UNWIND [1] AS seed RETURN {expression} AS value"
    return execute(FakeCypherGraph(), query, parameters=parameters).records[0]["value"]


@pytest.mark.parametrize(
    ("expression", "kind"),
    [
        ("date('2024-02-29')", TemporalDate),
        ("localtime('03:04:05.123456')", TemporalLocalTime),
        ("time('03:04:05+02:30')", TemporalTime),
        ("datetime('1969-12-31T23:59:59.999999Z')", TemporalInstant),
        ("localdatetime('2024-02-29T03:04:05')", TemporalLocalDateTime),
        ("duration('P2DT3H4M5.000006S')", TemporalDuration),
    ],
)
def test_temporal_string_constructors_return_typed_values(expression, kind):
    assert isinstance(_query(expression), kind)


def test_temporal_map_constructors_and_components():
    assert _query("date({year: 2024, month: 2, day: 29}).ordinalDay") == 60
    assert _query("localtime({hour: 3, minute: 4, second: 5, microsecond: 6}).microsecond") == 6
    assert _query("time({hour: 3, offset: '+02:30'}).offsetSeconds") == 9000
    assert _query("datetime({year: 2024, month: 1, day: 2, offset: '+02:00'}).hour") == 22
    assert _query("localdatetime({year: 2024, month: 1, day: 2, hour: 3}).hour") == 3
    assert _query("duration({days: 1, seconds: 2}).microseconds") == 86_402_000_000


@pytest.mark.parametrize("name", ["date", "time", "localtime", "datetime", "localdatetime", "duration"])
def test_temporal_constructor_null_propagates(name):
    assert _query(f"{name}(null)") is None


@pytest.mark.parametrize(
    "expression",
    [
        "date('2023-02-29')",
        "time('12:00')",
        "localtime('25:00')",
        "datetime('2024-01-01T00:00:00')",
        "localdatetime('2024-01-01T00:00:00Z')",
        "duration('P1M')",
        "date({year: 2024, month: 1, day: 1, hour: 2})",
        "time({hour: 12})",
        "datetime({year: 2024, month: 1, day: 1})",
    ],
)
def test_invalid_temporal_input_fails_deterministically(expression):
    with pytest.raises(ValueError, match="Invalid value"):
        _query(expression)


def test_python_temporal_parameters_are_normalized_recursively():
    assert _query("$value = date('2024-01-02')", parameters={"value": date(2024, 1, 2)}) is True
    assert isinstance(_query("$value", parameters={"value": datetime(2024, 1, 2, 3)}), TemporalLocalDateTime)
    assert isinstance(
        _query("$value", parameters={"value": datetime(2024, 1, 2, tzinfo=timezone.utc)}),
        TemporalInstant,
    )
    assert _query("$value[0]", parameters={"value": [timedelta(days=2)]}) == TemporalDuration(172_800_000_000)
    with pytest.raises(ValueError, match="whole minutes"):
        _query("$value", parameters={"value": time(1, tzinfo=timezone(timedelta(seconds=30)))})
    with pytest.raises(ValueError, match="whole minutes"):
        TemporalTime(0, 30)


def test_temporal_equality_comparison_and_arithmetic():
    assert _query("time('12:00:00+01:00') = time('11:00:00Z')") is True
    assert _query("date('2024-01-01') < date('2024-01-02')") is True
    assert _query("date('2024-01-01') + duration('P2D')") == TemporalDate(date(2024, 1, 3))
    assert _query("date('2024-01-03') - date('2024-01-01')") == TemporalDuration(172_800_000_000)
    assert _query("localtime('23:00:00') + duration('PT2H')") == TemporalLocalTime(3_600_000_000)
    assert _query("-duration('PT1S')") == TemporalDuration(-1_000_000)


def test_mixed_temporal_categories_cannot_be_relationally_compared():
    with pytest.raises(TypeError, match="Cannot compare"):
        _query("date('2024-01-01') < localdatetime('2024-01-01T00:00:00')")


def test_temporal_values_support_distinct_ordering_and_stringification():
    records = execute(
        FakeCypherGraph(),
        "UNWIND [date('2024-01-02'), date('2024-01-01'), date('2024-01-02')] AS d "
        "RETURN DISTINCT d ORDER BY d",
    ).records
    assert [row["d"].value for row in records] == [date(2024, 1, 1), date(2024, 1, 2)]
    assert _query("toString(datetime('2024-01-02T03:04:05+02:00'))") == "2024-01-02T01:04:05.000000Z"


def test_temporal_values_survive_aggregates_grouping_case_and_union():
    graph = FakeCypherGraph()
    records = execute(
        graph,
        "UNWIND [time('12:00:00+01:00'), time('09:00:00Z')] AS t "
        "RETURN min(t) AS first, max(t) AS last",
    ).records
    assert records == [{"first": TemporalTime(32_400_000_000, 0), "last": TemporalTime(43_200_000_000, 3600)}]
    grouped = execute(
        graph,
        "UNWIND [date('2024-01-01'), date('2024-01-01')] AS d RETURN d, count(*) AS total",
    ).records
    assert grouped == [{"d": TemporalDate(date(2024, 1, 1)), "total": 2}]
    assert _query("CASE WHEN true THEN date('2024-01-01') ELSE null END") == TemporalDate(date(2024, 1, 1))
    unioned = execute(
        graph,
        "UNWIND [1] AS x RETURN date('2024-01-01') AS d "
        "UNION UNWIND [1] AS x RETURN date('2024-01-01') AS d",
    ).records
    assert unioned == [{"d": TemporalDate(date(2024, 1, 1))}]


def test_temporal_graph_property_writes_are_rejected_recursively():
    graph = FakeCypherGraph()
    with pytest.raises(TypeError, match="cannot be persisted"):
        execute(graph, "CREATE (n {when: date('2024-01-01')}) RETURN n")
    with pytest.raises(TypeError, match="cannot be persisted"):
        execute(graph, "CREATE (n {values: [duration('PT1S')]}) RETURN n")
