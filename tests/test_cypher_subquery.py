"""CALL subquery coverage for the GestaltDB Cypher engine ([cypher-10]).

Covers correlated execution (per-row inner plans, RETURN exports, empty
results dropping rows), shadowing, nesting, WITH-import patterns,
parameters, and interaction with the other read clauses.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _subquery_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 30}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"age": 40}))
    graph.put_node(Node(node_id="t", labels=["Team"], properties={"name": "core"}))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "KNOWS"}))
    return graph


def test_call_subquery_as_first_clause():
    records = execute(_subquery_graph(), "CALL { MATCH (n:Person) RETURN n } RETURN n.id").records

    assert records == [{"n.id": "a"}, {"n.id": "b"}]


def test_call_subquery_correlates_on_outer_variable():
    records = execute(
        _subquery_graph(),
        "MATCH (n:Person) WHERE n.age = 30 CALL { MATCH (n)-->(m) RETURN m } RETURN n.id, m.id",
    ).records

    assert records == [{"n.id": "a", "m.id": "b"}]


def test_call_subquery_exports_only_returned_variables():
    graph = _subquery_graph()

    assert execute(
        graph, "MATCH (n:Person) CALL { MATCH (m:Person) WHERE m.age = 40 RETURN m AS o } RETURN n.id, o.id"
    ).records == [{"n.id": "a", "o.id": "b"}, {"n.id": "b", "o.id": "b"}]
    with pytest.raises(ValueError, match="unbound variable"):
        execute(graph, "MATCH (n:Person) CALL { MATCH (n)-->(m) RETURN n } RETURN m.id")


def test_empty_subquery_result_drops_outer_rows():
    records = execute(
        _subquery_graph(),
        "MATCH (n:Person) CALL { MATCH (n)-->(m:Team) RETURN m } RETURN n.id",
    ).records

    assert records == []


def test_subquery_shadowing_prefers_inner_values():
    records = execute(
        _subquery_graph(),
        "MATCH (n:Person) WHERE n.age = 30 CALL { MATCH (m:Person) WHERE m.age = 40 RETURN m AS n } RETURN n.id",
    ).records

    assert records == [{"n.id": "b"}]


def test_subquery_with_with_import_pattern():
    records = execute(
        _subquery_graph(),
        "MATCH (n:Person) WITH n AS p CALL { MATCH (p)-->(m) RETURN m } RETURN p.id, m.id",
    ).records

    assert records == [{"p.id": "a", "m.id": "b"}]


def test_nested_call_subqueries():
    records = execute(
        _subquery_graph(),
        "CALL { CALL { MATCH (n:Person) WHERE n.age = 30 RETURN n } RETURN n } RETURN n.id",
    ).records

    assert records == [{"n.id": "a"}]


def test_subquery_supports_read_clauses_and_aggregation():
    graph = _subquery_graph()

    assert execute(
        graph,
        "MATCH (n:Person) CALL { MATCH (n) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN m } RETURN n.id, m.id",
    ).records == [
        {"n.id": "a", "m.id": "b"},
        {"n.id": "b", "m.id": None},
    ]
    assert execute(
        graph, "MATCH (n:Person) CALL { UNWIND [1, 2] AS x RETURN x } RETURN n.id, x"
    ).records == [
        {"n.id": "a", "x": 1},
        {"n.id": "a", "x": 2},
        {"n.id": "b", "x": 1},
        {"n.id": "b", "x": 2},
    ]
    assert execute(
        graph, "CALL { MATCH (n:Person) RETURN count(*) AS total } RETURN total"
    ).records == [{"total": 2}]


def test_subquery_with_parameters():
    records = execute(
        _subquery_graph(),
        "MATCH (n:Person) CALL { MATCH (m:Person) WHERE m.age = $age RETURN m } RETURN n.id, m.id",
        parameters={"age": 40},
    ).records

    assert records == [{"n.id": "a", "m.id": "b"}, {"n.id": "b", "m.id": "b"}]


def test_subquery_with_post_filter():
    records = execute(
        _subquery_graph(),
        "MATCH (n:Person) CALL { MATCH (m:Person) RETURN m } WHERE m.age > 30 RETURN n.id, m.id",
    ).records

    assert records == [{"n.id": "a", "m.id": "b"}, {"n.id": "b", "m.id": "b"}]


def test_legacy_parse_rejects_subqueries():
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("CALL { MATCH (n) RETURN n } RETURN n")


def test_parameters_everywhere():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 30, "name": "Ada"}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"age": 40, "name": "Bob"}))

    assert execute(graph, "MATCH (n:Person) RETURN n.id ORDER BY n.id SKIP $s LIMIT $l",
                   parameters={"s": 1, "l": 1}).records == [{"n.id": "b"}]
    assert execute(graph, "MATCH (n) WHERE n.name =~ $re RETURN n.id",
                   parameters={"re": "A.*"}).records == [{"n.id": "a"}]
    assert execute(graph, "MATCH (n) WHERE n.age IN $ages RETURN n.id",
                   parameters={"ages": [30]}).records == [{"n.id": "a"}]
    assert execute(graph, "MATCH (n) WHERE n.age IN [$a, $b] RETURN n.id",
                   parameters={"a": 30, "b": 40}).records == [{"n.id": "a"}, {"n.id": "b"}]
    assert execute(graph, "UNWIND $items AS x RETURN x",
                   parameters={"items": [1, 2]}).records == [{"x": 1}, {"x": 2}]
