"""OPTIONAL MATCH coverage for the GestaltDB Cypher engine ([cypher-07]).

Covers left-outer-join semantics: null-filled rows, post-optional filtering,
chained optionals, comma patterns, interaction with later ``MATCH`` clauses,
aggregation over nulls, and relationship uniqueness scopes.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _optional_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 30}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"age": 40}))
    graph.put_node(Node(node_id="c", labels=["Person"], properties={"age": 50}))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "KNOWS"}))
    return graph


def test_optional_match_preserves_unmatched_rows_with_nulls():
    records = execute(
        _optional_graph(), "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN n.id, m.id"
    ).records

    assert records == [
        {"n.id": "a", "m.id": "b"},
        {"n.id": "b", "m.id": None},
        {"n.id": "c", "m.id": None},
    ]


def test_optional_match_binds_relationship_variable_or_null():
    records = execute(
        _optional_graph(), "MATCH (n:Person) OPTIONAL MATCH (n)-[r:KNOWS]->(m) RETURN n.id, r.id"
    ).records

    assert records == [
        {"n.id": "a", "r.id": "e1"},
        {"n.id": "b", "r.id": None},
        {"n.id": "c", "r.id": None},
    ]


def test_where_after_optional_match_filters_null_rows():
    graph = _optional_graph()

    assert execute(
        graph, "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) WHERE m.age > 35 RETURN n.id, m.id"
    ).records == [{"n.id": "a", "m.id": "b"}]
    assert execute(
        graph,
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) WHERE m.age > 35 OR m IS NULL RETURN n.id",
    ).records == [{"n.id": "a"}, {"n.id": "b"}, {"n.id": "c"}]


def test_chained_optional_matches_propagate_nulls():
    records = execute(
        _optional_graph(),
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) OPTIONAL MATCH (m)-[:KNOWS]->(k) "
        "RETURN n.id, m.id, k.id",
    ).records

    assert records == [
        {"n.id": "a", "m.id": "b", "k.id": None},
        {"n.id": "b", "m.id": None, "k.id": None},
        {"n.id": "c", "m.id": None, "k.id": None},
    ]


def test_optional_match_with_comma_patterns():
    records = execute(
        _optional_graph(),
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m), (m)-[:KNOWS]->(k) RETURN n.id, m.id, k.id",
    ).records

    assert records == [
        {"n.id": "a", "m.id": None, "k.id": None},
        {"n.id": "b", "m.id": None, "k.id": None},
        {"n.id": "c", "m.id": None, "k.id": None},
    ]


def test_optional_match_as_first_clause():
    graph = _optional_graph()

    assert execute(graph, "OPTIONAL MATCH (n:Person) RETURN n.id").records == [
        {"n.id": "a"}, {"n.id": "b"}, {"n.id": "c"}
    ]
    assert execute(graph, "OPTIONAL MATCH (n:Missing) RETURN n.id").records == [{"n.id": None}]


def test_optional_match_after_with():
    records = execute(
        _optional_graph(),
        "MATCH (n:Person) WITH n AS p OPTIONAL MATCH (p)-[:KNOWS]->(m) RETURN p.id, m.id",
    ).records

    assert records == [
        {"p.id": "a", "m.id": "b"},
        {"p.id": "b", "m.id": None},
        {"p.id": "c", "m.id": None},
    ]


def test_later_match_on_null_variable_yields_no_rows():
    records = execute(
        _optional_graph(),
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) MATCH (m)-[:KNOWS]->(k) RETURN n.id, k.id",
    ).records

    assert records == []


def test_later_match_on_bound_variable_survives_nulls():
    records = execute(
        _optional_graph(),
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) MATCH (n)-[:KNOWS]->(k) RETURN n.id, m.id, k.id",
    ).records

    assert records == [{"n.id": "a", "m.id": "b", "k.id": "b"}]


def test_disjoint_match_after_optional_keeps_null_rows():
    records = execute(
        _optional_graph(),
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) MATCH (d:Person {age: 50}) RETURN n.id, m.id, d.id",
    ).records

    assert records == [
        {"n.id": "a", "m.id": "b", "d.id": "c"},
        {"n.id": "b", "m.id": None, "d.id": "c"},
        {"n.id": "c", "m.id": None, "d.id": "c"},
    ]


def test_aggregation_over_optional_nulls():
    graph = _optional_graph()

    assert execute(
        graph, "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN count(*) AS total"
    ).records == [{"total": 3}]
    assert execute(
        graph, "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN count(m) AS total"
    ).records == [{"total": 1}]
    assert execute(
        graph,
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN n.id AS id, count(m) AS total ORDER BY id",
    ).records == [
        {"id": "a", "total": 1},
        {"id": "b", "total": 0},
        {"id": "c", "total": 0},
    ]


def test_with_carries_optional_nulls_for_null_filter():
    records = execute(
        _optional_graph(),
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) WITH n AS p, m AS q WHERE q IS NULL RETURN p.id",
    ).records

    assert records == [{"p.id": "b"}, {"p.id": "c"}]


def test_optional_match_uses_fresh_relationship_scope():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))

    assert execute(
        graph, "MATCH (a)-[r:T]->(b) OPTIONAL MATCH (a)-[s:T]->(b) RETURN r.id, s.id"
    ).records == [{"r.id": "e1", "s.id": "e1"}]


def test_legacy_parse_rejects_optional_match():
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("MATCH (n) OPTIONAL MATCH (n)-->(m) RETURN n")


def test_optional_match_validates_unbound_variables():
    with pytest.raises(ValueError, match="unbound variable"):
        execute(_optional_graph(), "MATCH (n:Person) OPTIONAL MATCH (n)-->(m) WHERE q.id = 'a' RETURN n.id")
