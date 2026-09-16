"""CREATE/SET/REMOVE coverage for the GestaltDB Cypher engine ([cypher-15]).

Covers node and path creation (fresh IDs, explicit IDs, bound reuse),
copy-on-write updates, label add/remove, map merge/replace, null-safe
no-ops, validation errors, read-after-write visibility, and per-query
atomicity on transactional backends.
"""

import importlib.util

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.serializers import PickleSerializer
from tests.test_cypher import FakeCypherGraph


def _write_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 30}))
    return graph


def test_create_node_with_generated_id():
    graph = _write_graph()

    records = execute(graph, "CREATE (n:Person {age: 40}) RETURN n.id").records

    assert len(records) == 1
    created_id = records[0]["n.id"]
    assert isinstance(created_id, str) and created_id != "a"
    assert graph.get_node(created_id.encode()).properties == {"age": 40}


def test_create_node_with_explicit_id():
    graph = _write_graph()

    assert execute(graph, 'CREATE (n:Person {id: "b", age: 40}) RETURN n.id').records == [
        {"n.id": "b"}
    ]
    assert b"b" in graph.labels.get("Person", [])


def test_create_path_reuses_bound_nodes():
    graph = _write_graph()

    records = execute(
        graph, 'MATCH (a {id: "a"}) CREATE (a)-[r:KNOWS {since: 2020}]->(m:Person {id: "b"}) RETURN m.id'
    ).records

    assert records == [{"m.id": "b"}]
    created_edges = [edge for edge in graph.edges.values() if edge.source == "a"]
    assert len(created_edges) == 1
    assert created_edges[0].properties == {"type": "KNOWS", "since": 2020}
    assert created_edges[0].target == "b"


def test_create_merges_labels_and_properties_into_bound_nodes():
    graph = _write_graph()

    execute(graph, 'MATCH (a {id: "a"}) CREATE (a:Employee {age: 31}) RETURN a.id')

    node = graph.get_node(b"a")
    assert set(node.labels) == {"Person", "Employee"}
    assert node.properties["age"] == 31


def test_create_rejects_untyped_and_variable_length_rels():
    graph = _write_graph()

    with pytest.raises(ValueError, match="exactly one type"):
        execute(graph, "CREATE (a)-[r]->(b) RETURN r")
    with pytest.raises(ValueError, match="variable-length"):
        execute(graph, "CREATE (a)-[:T*1..2]->(b) RETURN b")


def test_create_rejects_bound_relationship_variables():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))

    with pytest.raises(ValueError, match="already defined"):
        execute(graph, "MATCH ()-[r:T]->() CREATE (x)-[r:U]->(y) RETURN x")


def test_set_property_label_merge_replace():
    graph = _write_graph()

    assert execute(graph, 'MATCH (n {id: "a"}) SET n.age = 31 RETURN n.age').records == [
        {"n.age": 31}
    ]
    assert graph.get_node(b"a").properties["age"] == 31

    execute(graph, 'MATCH (n {id: "a"}) SET n:Employee RETURN n')
    assert "Employee" in graph.get_node(b"a").labels

    execute(graph, 'MATCH (n {id: "a"}) SET n += {city: "Athens"} RETURN n.age').records
    assert graph.get_node(b"a").properties == {"age": 31, "city": "Athens"}

    execute(graph, 'MATCH (n {id: "a"}) SET n = {age: 32} RETURN n.age').records
    assert graph.get_node(b"a").properties == {"age": 32}


def test_set_edge_properties():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))

    assert execute(graph, "MATCH ()-[r:T]->() SET r.score = 0.5 RETURN r.score").records == [
        {"r.score": 0.5}
    ]
    assert graph.get_edge(b"e1").properties == {"type": "T", "score": 0.5}


def test_remove_property_and_labels():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person", "Employee"], properties={"age": 30}))

    execute(graph, 'MATCH (n {id: "a"}) REMOVE n.age RETURN n.id')
    assert graph.get_node(b"a").properties == {}
    execute(graph, 'MATCH (n {id: "a"}) REMOVE n:Person, n.age RETURN n.id')
    assert list(graph.get_node(b"a").labels) == ["Employee"]


def test_set_remove_on_null_are_noops():
    graph = _write_graph()

    assert execute(
        graph,
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:Missing]->(m) SET m.age = 1 REMOVE m:Person RETURN n.id",
    ).records == [{"n.id": "a"}]
    assert graph.get_node(b"a").properties == {"age": 30}


def test_write_validation_errors():
    graph = _write_graph()

    with pytest.raises(ValueError, match="unbound variable"):
        execute(graph, "SET n.age = 1 RETURN n")
    with pytest.raises(ValueError, match="requires a node"):
        execute(graph, "MATCH ()-[r:T]->() SET r:Person RETURN r")
    with pytest.raises(ValueError, match="requires a node"):
        execute(graph, "MATCH ()-[r:T]->() REMOVE r:Person RETURN r")
    with pytest.raises(ValueError, match="Unsupported Cypher query"):
        execute(graph, "MATCH (n) RETURN n CREATE (m)")
    with pytest.raises(ValueError, match="UNION branches must be read-only"):
        execute(graph, "MATCH (n) RETURN n AS id UNION CREATE (m) RETURN m AS id")
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("CREATE (n) RETURN n")


def test_read_after_write_sees_new_data():
    graph = _write_graph()

    records = execute(
        graph, 'CREATE (n:Person {id: "b"}) MATCH (m:Person) RETURN m.id ORDER BY m.id'
    ).records

    assert records == [{"m.id": "a"}, {"m.id": "b"}]


def test_writes_inside_subquery():
    graph = _write_graph()

    records = execute(graph, 'CALL { CREATE (n:Person {id: "b"}) RETURN n } RETURN n.id').records

    assert records == [{"n.id": "b"}]
    assert graph.get_node(b"b") is not None


@pytest.mark.skipif(importlib.util.find_spec("lmdb") is None, reason="lmdb not installed")
def test_write_query_is_atomic_on_transactional_backend(tmp_path):
    graph = GraphDB(LMDBStore(path=str(tmp_path / "tx")), PickleSerializer())
    try:
        graph.put_node(Node(node_id="a", labels=["Person"]))

        with pytest.raises(TypeError, match="expects two strings"):
            execute(
                graph,
                'MATCH (a {id: "a"}) CREATE (a)-[:T]->(b:Person) SET b.age = 1 + $bad RETURN b',
                parameters={"bad": "boom"},
            )

        assert graph.get_node(b"a") is not None
        assert [node.get_id for node in graph.nodes_by_label("Person")] == ["a"]
    finally:
        graph.close()
