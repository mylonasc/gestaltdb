"""FOREACH coverage for the GestaltDB Cypher engine ([cypher-18]).

Covers write-only loops: creation and updates per element, computed
property values, null/empty inputs, nesting, scoping, entity refresh for
later clauses, and validation.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Node
from tests.test_cypher import FakeCypherGraph


def _foreach_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"]))
    return graph


def _node_ids(graph):
    return sorted(node_id.decode() for node_id in graph.nodes)


def test_foreach_create_per_element():
    graph = _foreach_graph()

    assert execute(graph, "FOREACH (x IN [1, 2] | CREATE (n:Person {id: x})) RETURN 1 AS ok").records == [
        {"ok": 1}
    ]
    assert _node_ids(graph) == ["1", "2", "a"]


def test_foreach_set_per_element_with_refresh():
    graph = _foreach_graph()

    records = execute(
        graph, "MATCH (n:Person) FOREACH (t IN [10, 20] | SET n.tag = t) RETURN n.tag"
    ).records

    assert records == [{"n.tag": 20}]
    assert graph.get_node(b"a").properties == {"tag": 20}


def test_foreach_null_and_empty_pass_rows_through():
    graph = _foreach_graph()

    assert execute(graph, "MATCH (n:Person) FOREACH (x IN [] | SET n.tag = 1) RETURN n.id").records == [
        {"n.id": "a"}
    ]
    assert execute(graph, "MATCH (n:Person) FOREACH (x IN null | SET n.tag = 1) RETURN n.id").records == [
        {"n.id": "a"}
    ]
    assert graph.get_node(b"a").properties == {}


def test_foreach_non_list_raises_type_error():
    with pytest.raises(TypeError, match="FOREACH expects a list value"):
        execute(_foreach_graph(), "FOREACH (x IN 1 | CREATE (n)) RETURN 1 AS ok")


def test_foreach_nested_loops():
    graph = _foreach_graph()

    execute(graph, "FOREACH (x IN [1, 2] | FOREACH (y IN [x] | CREATE (n:Person {id: y}))) RETURN 1 AS ok")

    assert _node_ids(graph) == ["1", "2", "a"]


def test_foreach_loop_variable_does_not_escape():
    assert execute(
        _foreach_graph(), "MATCH (n:Person) WITH n AS p FOREACH (q IN [1] | SET p.tag = 1) RETURN p.id"
    ).records == [{"p.id": "a"}]
    assert execute(
        _foreach_graph(),
        "MATCH (n:Person) WITH n AS p FOREACH (p IN [1, 2] | CREATE (m:Temp {id: p})) RETURN p.id",
    ).records == [{"p.id": "a"}]
    with pytest.raises(ValueError, match="unbound variable"):
        execute(_foreach_graph(), "FOREACH (z IN [1] | CREATE (n)) RETURN z")


def test_foreach_with_match_inside_and_delete():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))

    execute(graph, "FOREACH (x IN ['a'] | MATCH (n) WHERE n.id = x DELETE n) RETURN 1 AS ok")

    assert _node_ids(graph) == ["b"]


def test_foreach_validation_errors():
    graph = _foreach_graph()

    with pytest.raises(ValueError, match="unbound variable"):
        execute(graph, "FOREACH (x IN y | CREATE (n)) RETURN 1 AS ok")
    with pytest.raises(ValueError, match="Aggregates cannot be used in FOREACH"):
        execute(graph, "MATCH (n:Person) FOREACH (x IN [count(n)] | CREATE (m)) RETURN 1 AS ok")
    with pytest.raises(ValueError, match="UNION branches must be read-only"):
        execute(graph, "MATCH (n) RETURN n AS id UNION FOREACH (x IN [1] | CREATE (m)) RETURN m AS id")
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("FOREACH (x IN [1] | CREATE (n)) RETURN 1 AS ok")
