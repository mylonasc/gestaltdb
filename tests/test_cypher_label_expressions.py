"""Label expression coverage for the GestaltDB Cypher engine ([cypher-14]).

Covers ``:A|B`` alternatives and ``:!A`` negation on node patterns, alone
and combined with conjunctions, properties, traversals, and clauses.
"""

from gestaltdb.cypher import execute
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _label_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["X"], properties={"v": 1}))
    graph.put_node(Node(node_id="b", labels=["Y"], properties={"v": 2}))
    graph.put_node(Node(node_id="c", labels=["X", "Y"], properties={"v": 3}))
    graph.put_node(Node(node_id="d", labels=["Z"], properties={"v": 4}))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    return graph


def _ids(query, parameters=None):
    return [record["n.id"] for record in execute(_label_graph(), query, parameters=parameters).records]


def test_label_alternatives():
    assert _ids("MATCH (n:X|Y) RETURN n.id") == ["a", "b", "c"]


def test_label_negation():
    assert _ids("MATCH (n:!X) RETURN n.id") == ["b", "d"]
    assert _ids("MATCH (n:!X:!Y) RETURN n.id") == ["d"]


def test_label_conjunction_still_requires_all():
    assert _ids("MATCH (n:X:Y) RETURN n.id") == ["c"]


def test_mixed_alternatives_of_conjunctions():
    assert _ids("MATCH (n:X:Y|Z) RETURN n.id") == ["c", "d"]
    assert _ids("MATCH (n:!X|Y) RETURN n.id") == ["b", "c", "d"]


def test_label_expressions_combine_with_properties():
    assert _ids("MATCH (n:X|Y {v: 2}) RETURN n.id") == ["b"]
    assert _ids("MATCH (n:!Z) WHERE n.v >= 2 RETURN n.id") == ["b", "c"]


def test_label_expressions_on_traversal_endpoints():
    graph = _label_graph()

    assert execute(graph, "MATCH (a:X)-[:T]->(b:Y) RETURN b.id").records == [{"b.id": "b"}]
    assert execute(graph, "MATCH (a)-[:T]->(b:!Y) RETURN b.id").records == []
    assert execute(graph, "MATCH (a:Z)-[:T]->(b) RETURN b.id").records == []


def test_label_expressions_with_clauses():
    graph = _label_graph()

    assert execute(
        graph, "MATCH (n:X|Y) OPTIONAL MATCH (n)-[:T]->(m) RETURN n.id, m.id"
    ).records == [
        {"n.id": "a", "m.id": "b"},
        {"n.id": "b", "m.id": None},
        {"n.id": "c", "m.id": None},
    ]
    assert execute(graph, "MATCH (n:!Z) RETURN count(*) AS total").records == [{"total": 3}]
