"""Relationship property map coverage for the GestaltDB Cypher engine ([cypher-11]).

Covers inline ``-[r:T {prop: value}]->`` predicates with literal and
parameter values: filtering in both expansion paths, anonymous forms,
multi-entry maps, interaction with ``WHERE``/indexes/``OPTIONAL MATCH``,
and literal validation.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _relmap_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_node(Node(node_id="c"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T", "score": 0.9, "kind": "x"}))
    graph.put_edge(Edge(edge_id="e2", source="a", target="c", properties={"type": "T", "score": 0.5, "kind": "y"}))
    graph.put_edge(Edge(edge_id="e3", source="b", target="c", properties={"type": "U", "score": 0.9}))
    return graph


def test_relationship_map_filters_by_literal():
    records = execute(
        _relmap_graph(), "MATCH (a)-[r:T {score: 0.9}]->(b) RETURN r.id, b.id"
    ).records

    assert records == [{"r.id": "e1", "b.id": "b"}]


def test_relationship_map_accepts_parameters():
    records = execute(
        _relmap_graph(), "MATCH (a)-[r:T {score: $score}]->(b) RETURN r.id", parameters={"score": 0.5}
    ).records

    assert records == [{"r.id": "e2"}]


def test_relationship_map_supports_multiple_entries():
    records = execute(
        _relmap_graph(), "MATCH (a)-[r:T {score: 0.9, kind: 'x'}]->(b) RETURN r.id"
    ).records

    assert records == [{"r.id": "e1"}]
    assert execute(
        _relmap_graph(), "MATCH (a)-[r:T {score: 0.9, kind: 'y'}]->(b) RETURN r.id"
    ).records == []


def test_relationship_map_without_variable_or_type():
    graph = _relmap_graph()

    assert execute(graph, "MATCH (a)-[:T {score: 0.5}]->(b) RETURN b.id").records == [
        {"b.id": "c"}
    ]
    assert execute(graph, "MATCH (a)-[{score: 0.9}]->(b) RETURN b.id").records == [
        {"b.id": "b"}, {"b.id": "c"}
    ]


def test_relationship_map_combines_with_where_and_labels():
    graph = _relmap_graph()

    assert execute(
        graph, "MATCH (a)-[r:T {score: 0.9}]->(b) WHERE r.kind = 'x' RETURN r.id"
    ).records == [{"r.id": "e1"}]
    assert execute(
        graph, "MATCH (a)-[r:T]->(b) WHERE r.score = 0.9 AND r.kind = 'y' RETURN r.id"
    ).records == []


def test_relationship_map_in_chained_and_optional_match():
    graph = _relmap_graph()

    assert execute(
        graph, "MATCH (a) MATCH (a)-[r:T {score: 0.9}]->(b) RETURN a.id, b.id"
    ).records == [{"a.id": "a", "b.id": "b"}]
    assert execute(
        graph, "MATCH (n) OPTIONAL MATCH (n)-[r:T {score: 0.1}]->(m) RETURN n.id, r.id"
    ).records == [
        {"n.id": "a", "r.id": None},
        {"n.id": "b", "r.id": None},
        {"n.id": "c", "r.id": None},
    ]


def test_relationship_map_rejects_non_literal_values():
    with pytest.raises(ValueError, match="Invalid Cypher literal"):
        execute(_relmap_graph(), "MATCH (a)-[r:T {score: b.score}]->(c) RETURN r.id")


def test_legacy_parse_represents_relmap_as_generalized_pattern():
    parsed = parse("MATCH (a)-[r:T {score: 1}]->(b) RETURN r.id")

    assert parsed.clauses[0].hops[0].properties == (("score", 1),)
