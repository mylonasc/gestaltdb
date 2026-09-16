"""DELETE / DETACH DELETE coverage for the GestaltDB Cypher engine ([cypher-16]).

Covers relationship-existence checks, cascading detaches, edge deletion,
null-safe no-ops, mixed delete lists, validation, and per-query atomicity.
"""

import importlib.util

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.serializers import PickleSerializer
from tests.test_cypher import FakeCypherGraph


def _delete_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"]))
    graph.put_node(Node(node_id="b", labels=["Person"]))
    graph.put_node(Node(node_id="c", labels=["Person"]))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    return graph


def test_delete_edge():
    graph = _delete_graph()

    assert execute(graph, "MATCH ()-[r:T]->() DELETE r RETURN r.id").records == [
        {"r.id": "e1"}
    ]
    assert graph.get_edge(b"e1") is None
    assert graph.get_node(b"a") is not None


def test_delete_node_without_relationships():
    graph = _delete_graph()

    assert execute(graph, 'MATCH (n {id: "c"}) DELETE n RETURN n.id').records == [
        {"n.id": "c"}
    ]
    assert graph.get_node(b"c") is None


def test_delete_node_with_relationships_requires_detach():
    graph = _delete_graph()

    with pytest.raises(ValueError, match="use DETACH DELETE"):
        execute(graph, 'MATCH (n {id: "a"}) DELETE n RETURN n.id')
    assert graph.get_node(b"a") is not None


def test_detach_delete_cascades_incident_edges():
    graph = _delete_graph()

    assert execute(graph, 'MATCH (n {id: "a"}) DETACH DELETE n RETURN n.id').records == [
        {"n.id": "a"}
    ]
    assert graph.get_node(b"a") is None
    assert graph.get_edge(b"e1") is None
    assert graph.get_node(b"b") is not None


def test_delete_mixed_nodes_and_edges():
    graph = _delete_graph()

    records = execute(graph, "MATCH (a)-[r:T]->(b) DELETE a, r RETURN b.id").records

    assert records == [{"b.id": "b"}]
    assert graph.get_node(b"a") is None
    assert graph.get_edge(b"e1") is None


def test_delete_null_is_noop_and_validates_entities():
    graph = _delete_graph()

    assert execute(
        graph, "MATCH (n:Person) OPTIONAL MATCH (n)-[:Missing]->(m) DELETE m RETURN n.id"
    ).records == [{"n.id": "a"}, {"n.id": "b"}, {"n.id": "c"}]
    with pytest.raises(TypeError, match="DELETE expects a node or relationship"):
        execute(graph, "MATCH (n:Person) DELETE 1 RETURN n.id")
    with pytest.raises(ValueError, match="unbound variable"):
        execute(graph, "DELETE n RETURN n")


def test_delete_validation_errors():
    graph = _delete_graph()

    with pytest.raises(ValueError, match="UNION branches must be read-only"):
        execute(graph, "MATCH (n) RETURN n AS id UNION MATCH (m) DELETE m RETURN m AS id")
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("MATCH (n) DELETE n RETURN n")


@pytest.mark.skipif(importlib.util.find_spec("lmdb") is None, reason="lmdb not installed")
def test_delete_rolls_back_with_failed_followup(tmp_path):
    graph = GraphDB(LMDBStore(path=str(tmp_path / "del")), PickleSerializer())
    try:
        graph.put_node(Node(node_id="a", labels=["Person"]))
        graph.put_node(Node(node_id="b", labels=["Person"]))
        graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))

        with pytest.raises(TypeError):
            execute(
                graph,
                'MATCH (n {id: "b"}) DETACH DELETE n SET n.age = 1 + $bad RETURN n',
                parameters={"bad": "boom"},
            )

        assert graph.get_node(b"b") is not None
        assert graph.get_edge(b"e1") is not None
    finally:
        graph.close()
