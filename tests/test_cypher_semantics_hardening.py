"""Three-valued logic, coercion, and ordering matrix ([cypher-20])."""

import pytest

from gestaltdb.cypher import execute
from gestaltdb.cypher_errors import CypherSemanticError
from gestaltdb.cypher_expr import _cypher_equals
from gestaltdb.graphdb import Node

from tests.test_cypher import FakeCypherGraph


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("true AND null", None),
        ("false AND null", False),
        ("false OR null", None),
        ("true OR null", True),
        ("true XOR null", None),
        ("NOT null", None),
        ("null IN []", False),
        ("1 IN [2, null]", None),
        ("1 IN [null, 1]", True),
        ("[1, null] = [1, null]", None),
        ("[1, null] = [2, null]", False),
        ("{a: null} = {a: null}", None),
        ("1 = 1.0", True),
        ("true = 1", False),
    ],
)
def test_three_valued_logic_matrix(expression, expected):
    records = execute(
        FakeCypherGraph(), f"UNWIND [0] AS seed RETURN {expression} AS value"
    ).records
    assert records == [{"value": expected}]


@pytest.mark.parametrize(
    "query",
    [
        "UNWIND [0] AS x WITH x WHERE 1 RETURN x",
        "UNWIND [0] AS x RETURN CASE WHEN 1 THEN 'yes' END AS value",
        "UNWIND [0] AS x RETURN [v IN [1] WHERE 1 | v] AS value",
        "UNWIND [0] AS x RETURN all(v IN [1] WHERE 1) AS value",
    ],
)
def test_predicate_contexts_require_booleans(query):
    with pytest.raises(TypeError, match="Expected boolean expression"):
        execute(FakeCypherGraph(), query)


@pytest.mark.parametrize(
    "expression",
    ["true < 2", "toBoolean(1.5)", "toInteger([])", "toFloat(true)"],
)
def test_mixed_comparisons_and_unsupported_coercions_raise(expression):
    with pytest.raises(TypeError):
        execute(FakeCypherGraph(), f"UNWIND [0] AS x RETURN {expression} AS value")


def test_failed_string_conversion_remains_null():
    records = execute(
        FakeCypherGraph(), "UNWIND [0] AS x RETURN toInteger('nope') AS value"
    ).records
    assert records == [{"value": None}]


def test_order_by_null_placement_and_stable_ties():
    graph = FakeCypherGraph()
    asc = execute(
        graph,
        "UNWIND [{id: 'a', k: 1}, {id: 'b', k: null}, {id: 'c', k: 1}, "
        "{id: 'd', k: null}] AS x RETURN x.id AS id, x.k AS k ORDER BY k",
    ).records
    desc = execute(
        graph,
        "UNWIND [{id: 'a', k: 1}, {id: 'b', k: null}, {id: 'c', k: 1}, "
        "{id: 'd', k: null}] AS x RETURN x.id AS id, x.k AS k ORDER BY k DESC",
    ).records
    assert [row["id"] for row in asc] == ["a", "c", "b", "d"]
    assert [row["id"] for row in desc] == ["b", "d", "a", "c"]


def test_order_by_uses_total_type_order_and_projected_alias_values():
    records = execute(
        FakeCypherGraph(),
        "UNWIND [{}, [], 'text', false, 1, null] AS value RETURN value ORDER BY value",
    ).records
    assert [row["value"] for row in records] == [{}, [], "text", False, 1, None]

    random_rows = execute(
        FakeCypherGraph(),
        "UNWIND [1, 2, 3, 4, 5] AS x RETURN rand() AS value ORDER BY value",
    ).records
    values = [row["value"] for row in random_rows]
    assert values == sorted(values)


def test_graph_entities_compare_by_kind_and_stable_id():
    assert _cypher_equals(Node("a"), Node("a")) is True
    assert _cypher_equals(Node("a"), Node("b")) is False


@pytest.mark.parametrize(
    ("expression", "message"),
    [("date()", "Unsupported temporal function: date"), ("point({x: 1})", "Unsupported spatial function: point")],
)
def test_temporal_and_spatial_stubs_have_typed_errors(expression, message):
    with pytest.raises(CypherSemanticError, match=message):
        execute(FakeCypherGraph(), f"UNWIND [0] AS x RETURN {expression} AS value")
