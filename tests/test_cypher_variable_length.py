"""Variable-length path coverage for the GestaltDB Cypher engine ([cypher-12]).

Covers bound shapes, breadth-first order, rel-list bindings, zero-length
matches, trail semantics, directions, labels, rel maps, and bounds
validation.
"""

import pytest

from gestaltdb.cypher import execute
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _chain_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    for node_id in ("a", "b", "c", "d"):
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="b", target="c", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e3", source="a", target="d", properties={"type": "T"}))
    return graph


def test_variable_length_breadth_first_order():
    records = execute(
        _chain_graph(), 'MATCH (x {id: "a"})-[:T*1..2]->(y) RETURN y.id'
    ).records

    assert records == [{"y.id": "b"}, {"y.id": "d"}, {"y.id": "c"}]


def test_variable_length_binds_relationship_lists():
    records = execute(
        _chain_graph(), 'MATCH (x {id: "a"})-[r:T*1..2]->(y) RETURN y.id, size(r) AS depth'
    ).records

    assert records == [
        {"y.id": "b", "depth": 1},
        {"y.id": "d", "depth": 1},
        {"y.id": "c", "depth": 2},
    ]


def test_variable_length_exact_and_open_bounds():
    graph = _chain_graph()

    assert execute(graph, 'MATCH (x {id: "a"})-[:T*2]->(y) RETURN y.id').records == [
        {"y.id": "c"}
    ]
    assert execute(graph, 'MATCH (x {id: "a"})-[:T*..1]->(y) RETURN y.id').records == [
        {"y.id": "b"}, {"y.id": "d"}
    ]
    assert execute(graph, 'MATCH (x {id: "a"})-[:T*2..]->(y) RETURN y.id').records == [
        {"y.id": "c"}
    ]
    assert execute(graph, 'MATCH (x {id: "a"})-[:T*]->(y) RETURN y.id').records == [
        {"y.id": "b"}, {"y.id": "d"}, {"y.id": "c"}
    ]


def test_zero_length_matches_source_itself():
    records = execute(
        _chain_graph(), 'MATCH (x {id: "a"})-[:T*0..1]->(y) RETURN y.id'
    ).records

    assert records == [{"y.id": "a"}, {"y.id": "b"}, {"y.id": "d"}]


def test_variable_length_respects_trail_semantics():
    graph = FakeCypherGraph()
    for node_id in ("a", "b"):
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="b", target="a", properties={"type": "T"}))

    records = execute(graph, 'MATCH (x {id: "a"})-[:T*1..3]->(y) RETURN y.id').records

    assert records == [{"y.id": "b"}, {"y.id": "a"}]


def test_variable_length_directions_and_labels():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b", labels=["Target"]))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))

    assert execute(graph, 'MATCH (x {id: "b"})<-[:T*1..2]-(y) RETURN y.id').records == [
        {"y.id": "a"}
    ]
    assert execute(graph, 'MATCH (x {id: "a"})-[:T*1..2]->(y:Target) RETURN y.id').records == [
        {"y.id": "b"}
    ]
    assert execute(graph, 'MATCH (x {id: "a"})-[:T*1..2]->(y:Missing) RETURN y.id').records == []


def test_variable_length_with_rel_maps_and_counts():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_node(Node(node_id="c"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T", "score": 1}))
    graph.put_edge(Edge(edge_id="e2", source="b", target="c", properties={"type": "T", "score": 0}))

    assert execute(
        graph, 'MATCH (x {id: "a"})-[r:T*1..2 {score: 1}]->(y) RETURN y.id'
    ).records == [{"y.id": "b"}]
    assert execute(
        graph, 'MATCH (x {id: "a"})-[:T*1..2]->(y) RETURN count(*) AS total'
    ).records == [{"total": 2}]


def test_variable_length_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="Invalid variable-length bounds"):
        execute(_chain_graph(), 'MATCH (x {id: "a"})-[:T*3..1]->(y) RETURN y.id')
