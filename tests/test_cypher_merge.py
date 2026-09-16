"""MERGE coverage for the GestaltDB Cypher engine ([cypher-17]).

Covers match-or-create per row, idempotency, ``ON CREATE``/``ON MATCH``
actions, bound-variable reuse, partial-pattern creation, and validation.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _merge_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 30}))
    return graph


def _node_ids(graph):
    return sorted(node_id.decode() for node_id in graph.nodes)


def test_merge_creates_missing_node():
    graph = _merge_graph()

    assert execute(graph, 'MERGE (n:Person {id: "b"}) RETURN n.id').records == [{"n.id": "b"}]
    assert _node_ids(graph) == ["a", "b"]


def test_merge_matches_existing_node_without_duplicating():
    graph = _merge_graph()

    assert execute(graph, 'MERGE (n:Person {id: "a"}) RETURN n.id').records == [{"n.id": "a"}]
    assert _node_ids(graph) == ["a"]


def test_merge_is_idempotent():
    graph = _merge_graph()

    for _ in range(2):
        assert execute(graph, 'MERGE (n:Person {id: "b", age: 40}) RETURN n.id').records == [
            {"n.id": "b"}
        ]
    assert _node_ids(graph) == ["a", "b"]


def test_merge_on_create_and_on_match_actions():
    graph = _merge_graph()

    assert execute(
        graph,
        'MERGE (n:Person {id: "a"}) ON CREATE SET n.created = true ON MATCH SET n.seen = true '
        "RETURN n.created, n.seen",
    ).records == [{"n.created": None, "n.seen": True}]
    assert execute(
        graph,
        'MERGE (n:Person {id: "b"}) ON CREATE SET n.created = true ON MATCH SET n.seen = true '
        "RETURN n.created, n.seen",
    ).records == [{"n.created": True, "n.seen": None}]


def test_merge_path_reuses_bound_nodes_and_creates_edges():
    graph = _merge_graph()

    assert execute(
        graph, 'MATCH (a {id: "a"}) MERGE (a)-[r:KNOWS]->(b:Person {id: "b"}) RETURN b.id'
    ).records == [{"b.id": "b"}]
    assert execute(
        graph, 'MATCH (a {id: "a"}) MERGE (a)-[r:KNOWS]->(b:Person {id: "b"}) RETURN count(*) AS total'
    ).records == [{"total": 1}]
    assert len(graph.edges) == 1


def test_merge_with_parameters():
    graph = _merge_graph()

    assert execute(
        graph, "MERGE (n:Person {id: $id}) RETURN n.id", parameters={"id": "b"}
    ).records == [{"n.id": "b"}]


def test_merge_validation_errors():
    graph = _merge_graph()

    with pytest.raises(ValueError, match="already defined"):
        execute(graph, "MATCH ()-[r:T]->() MERGE (a)-[r:U]->(b) RETURN a")
    with pytest.raises(ValueError, match="exactly one type"):
        execute(graph, "MERGE (a)-[r]->(b) RETURN r")
    with pytest.raises(ValueError, match="variable-length"):
        execute(graph, "MERGE (a)-[:T*1..2]->(b) RETURN b")
    with pytest.raises(ValueError, match="UNION branches must be read-only"):
        execute(graph, "MATCH (n) RETURN n AS id UNION MERGE (m) RETURN m AS id")
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("MERGE (n) RETURN n")
