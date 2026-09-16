"""UNWIND coverage for the GestaltDB Cypher engine ([cypher-08]).

Covers list-to-rows expansion: standalone and post-``MATCH``/``WITH``
placement, multiple ``UNWIND`` cross products, empty/``None`` inputs,
parameters, aggregation over unwound rows, and validation errors.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Node
from tests.test_cypher import FakeCypherGraph


def _unwind_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"tags": ["x", "y"]}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"tags": []}))
    return graph


def test_unwind_literal_list_as_first_clause():
    assert execute(FakeCypherGraph(), "UNWIND [1, 2] AS x RETURN x").records == [
        {"x": 1}, {"x": 2}
    ]


def test_unwind_after_match_expands_per_row():
    records = execute(
        _unwind_graph(), "MATCH (n:Person) UNWIND n.tags AS t RETURN n.id, t"
    ).records

    assert records == [{"n.id": "a", "t": "x"}, {"n.id": "a", "t": "y"}]


def test_unwind_empty_and_null_produce_no_rows():
    graph = _unwind_graph()

    assert execute(graph, "MATCH (n:Person) UNWIND [] AS x RETURN x").records == []
    assert execute(graph, "UNWIND null AS x RETURN x").records == []
    assert execute(graph, "UNWIND $items AS x RETURN x", parameters={"items": [1]}).records == [
        {"x": 1}
    ]


def test_unwind_non_list_raises_type_error():
    with pytest.raises(TypeError, match="UNWIND expects a list value"):
        execute(_unwind_graph(), "UNWIND 1 AS x RETURN x")


def test_multiple_unwinds_form_cross_product():
    records = execute(
        _unwind_graph(), "MATCH (n:Person) UNWIND [1] AS x UNWIND [10, 20] AS y RETURN n.id, x, y"
    ).records

    assert records == [
        {"n.id": "a", "x": 1, "y": 10},
        {"n.id": "a", "x": 1, "y": 20},
        {"n.id": "b", "x": 1, "y": 10},
        {"n.id": "b", "x": 1, "y": 20},
    ]


def test_unwind_after_with_and_before_match():
    graph = _unwind_graph()

    assert execute(
        graph, "MATCH (n:Person) UNWIND n.tags AS t WITH t AS u RETURN u"
    ).records == [{"u": "x"}, {"u": "y"}]
    assert execute(
        graph, "UNWIND ['a', 'c'] AS want MATCH (n) WHERE n.id = want RETURN n.id"
    ).records == [{"n.id": "a"}]


def test_unwind_after_projected_rows():
    records = execute(
        _unwind_graph(),
        "MATCH (n:Person) WITH n.id AS id, n.tags AS tags UNWIND tags AS t RETURN id, t",
    ).records

    assert records == [{"id": "a", "t": "x"}, {"id": "a", "t": "y"}]


def test_aggregation_over_unwound_rows():
    graph = _unwind_graph()

    assert execute(graph, "UNWIND [1, 2, 3] AS x RETURN count(*) AS total").records == [
        {"total": 3}
    ]
    assert execute(graph, "UNWIND [1, 2, 3] AS x RETURN sum(x) AS total").records == [
        {"total": 6}
    ]


def test_unwind_validates_scope_and_rejects_aggregates():
    graph = _unwind_graph()

    with pytest.raises(ValueError, match="unbound variable"):
        execute(graph, "UNWIND n.tags AS t RETURN t")
    with pytest.raises(ValueError, match="Aggregates cannot be used in UNWIND"):
        execute(graph, "MATCH (n:Person) UNWIND count(n.tags) AS t RETURN t")
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("UNWIND [1] AS x RETURN x")
