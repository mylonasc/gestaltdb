"""Path binding coverage for the GestaltDB Cypher engine ([cypher-14]).

Covers ``p = (a)-->(b)`` named patterns: path values (nodes + edges),
reuse downstream, fixed/variable/zero-length shapes, ``OPTIONAL MATCH``,
``length``/``nodes``/``relationships`` accessors, and validation.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.query_engine.cypher.ast import PathValue
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _path_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    for node_id in ("a", "b", "c"):
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="b", target="c", properties={"type": "T"}))
    return graph


def test_path_binding_binds_nodes_and_edges():
    records = execute(_path_graph(), "MATCH p = (a)-[r:T]->(b) RETURN p").records

    assert len(records) == 2
    assert [[node.get_id for node in record["p"].nodes] for record in records] == [
        ["a", "b"], ["b", "c"],
    ]
    assert [record["p"].length for record in records] == [1, 1]


def test_path_binding_variable_length_collects_trails():
    records = execute(_path_graph(), "MATCH p = (a)-[*1..2]->(b) RETURN p").records

    assert [[node.get_id for node in record["p"].nodes] for record in records] == [
        ["a", "b"], ["a", "b", "c"], ["b", "c"],
    ]
    assert [record["p"].length for record in records] == [1, 2, 1]


def test_path_binding_zero_hop_and_single_node():
    graph = _path_graph()

    assert execute(graph, 'MATCH p = (a {id: "a"}) RETURN length(p)').records == [
        {"length(p)": 0}
    ]
    assert execute(graph, 'MATCH p = (a {id: "a"})-[:T*0..1]->(b) RETURN length(p)').records == [
        {"length(p)": 0}, {"length(p)": 1}
    ]


def test_path_accessors():
    records = execute(
        _path_graph(), "MATCH p = (a)-[r:T]->(b) RETURN length(p) AS len, nodes(p) AS n, relationships(p) AS r ORDER BY len"
    ).records

    assert len(records) == 2
    assert [record["len"] for record in records] == [1, 1]
    assert [[node.get_id for node in record["n"]] for record in records] == [["a", "b"], ["b", "c"]]
    assert [[edge.get_id for edge in record["r"]] for record in records] == [["e1"], ["e2"]]


def test_path_accessor_errors():
    graph = _path_graph()

    with pytest.raises(TypeError, match=r"nodes\(\) expects a path"):
        execute(graph, "MATCH (n) RETURN nodes(n) AS v")
    with pytest.raises(TypeError, match=r"relationships\(\) expects a path"):
        execute(graph, "MATCH (n) RETURN relationships([1]) AS v")


def test_named_path_reusable_downstream():
    records = execute(
        _path_graph(), "MATCH p = (a)-[:T]->(b) MATCH (b)-[:T]->(c) RETURN p, c.id"
    ).records

    assert len(records) == 1
    assert [node.get_id for node in records[0]["p"].nodes] == ["a", "b"]
    assert records[0]["c.id"] == "c"


def test_named_path_in_optional_match():
    records = execute(
        _path_graph(), "MATCH (n) OPTIONAL MATCH p = (n)-[:T]->(m) RETURN n.id, p"
    ).records

    by_node = {record["n.id"]: record["p"] for record in records}
    assert [node.get_id for node in by_node["a"].nodes] == ["a", "b"]
    assert by_node["c"] is None


def test_named_path_with_where_and_aggregation():
    graph = _path_graph()

    assert execute(
        graph, "MATCH p = (a)-[r:T]->(b) WHERE r.id = 'e1' RETURN length(p) AS len"
    ).records == [{"len": 1}]
    assert execute(graph, "MATCH p = (a)-[:T]->(b) RETURN count(*) AS total").records == [
        {"total": 2}
    ]


def test_path_binding_validation():
    graph = _path_graph()

    with pytest.raises(ValueError, match="cannot be nested"):
        execute(graph, "MATCH p = q = (a)-->(b) RETURN p")
    parsed = parse("MATCH p = (a)-->(b) RETURN p")

    assert parsed.clauses[0].name == "p"
