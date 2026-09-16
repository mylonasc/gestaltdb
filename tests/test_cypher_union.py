"""UNION [ALL] coverage for the GestaltDB Cypher engine ([cypher-09]).

Covers set combination of independently planned branches: deduplication,
concatenation, mixed operators with left-fold semantics, per-branch
modifiers, column compatibility errors, and branch-local features.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _union_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 30}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"age": 40}))
    graph.put_node(Node(node_id="c", labels=["Team"], properties={"age": 30}))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "KNOWS"}))
    return graph


def test_union_deduplicates_across_branches():
    records = execute(
        _union_graph(),
        "MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id "
        "UNION MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id",
    ).records

    assert records == [{"id": "a"}]


def test_union_all_concatenates_without_dedup():
    records = execute(
        _union_graph(),
        "MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id "
        "UNION ALL MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id",
    ).records

    assert records == [{"id": "a"}, {"id": "a"}]


def test_union_combines_disjoint_branches_in_order():
    records = execute(
        _union_graph(),
        "MATCH (n:Person) WHERE n.age = 40 RETURN n.id AS id "
        "UNION MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id",
    ).records

    assert records == [{"id": "b"}, {"id": "a"}]


def test_mixed_union_operators_fold_left():
    graph = _union_graph()
    first = "MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id"
    second = "MATCH (n:Person) WHERE n.age = 30 RETURN n.id AS id"

    assert execute(graph, f"{first} UNION ALL {second} UNION {second}").records == [{"id": "a"}]
    assert execute(graph, f"{first} UNION {second} UNION ALL {second}").records == [
        {"id": "a"}, {"id": "a"}
    ]


def test_branch_modifiers_apply_within_branches():
    records = execute(
        _union_graph(),
        "MATCH (n:Person) RETURN n.id AS id ORDER BY id DESC LIMIT 1 "
        "UNION ALL MATCH (n:Person) RETURN n.id AS id ORDER BY id LIMIT 1",
    ).records

    assert records == [{"id": "b"}, {"id": "a"}]


def test_union_column_mismatch_is_rejected():
    graph = _union_graph()

    with pytest.raises(ValueError, match="UNION branches must return the same columns"):
        execute(graph, "MATCH (n:Person) RETURN n.id AS id UNION MATCH (n:Person) RETURN n.age AS age")
    with pytest.raises(ValueError, match="UNION branches must return the same columns"):
        execute(graph, "MATCH (n:Person) RETURN n.id AS id UNION MATCH (n:Person) RETURN n.id AS id, n.age AS age")
    with pytest.raises(ValueError, match="UNION branches must return the same columns"):
        execute(graph, "MATCH (n:Person) RETURN n.age AS a, n.id AS i UNION MATCH (n:Person) RETURN n.id AS i, n.age AS a")


def test_union_branches_support_full_read_queries():
    graph = _union_graph()

    assert execute(
        graph,
        "MATCH (n:Person) OPTIONAL MATCH (n)-[:KNOWS]->(m) RETURN n.id AS id, m.id AS friend "
        "UNION MATCH (t:Team) RETURN t.id AS id, t.id AS friend",
    ).records == [
        {"id": "a", "friend": "b"},
        {"id": "b", "friend": None},
        {"id": "c", "friend": "c"},
    ]
    assert execute(
        graph,
        "MATCH (n:Person) WITH n AS p RETURN p.id AS id "
        "UNION ALL UNWIND ['x'] AS id RETURN id",
    ).records == [{"id": "a"}, {"id": "b"}, {"id": "x"}]
    assert execute(
        graph,
        "MATCH (n:Person) RETURN count(*) AS total UNION MATCH (t:Team) RETURN count(*) AS total",
    ).records == [{"total": 2}, {"total": 1}]


def test_union_with_empty_branch_and_parameters():
    graph = _union_graph()

    assert execute(
        graph, "MATCH (n:Missing) RETURN n.id AS id UNION MATCH (n:Person) RETURN n.id AS id"
    ).records == [{"id": "a"}, {"id": "b"}]
    assert execute(
        graph,
        "MATCH (n:Person) WHERE n.age = $age RETURN n.id AS id UNION MATCH (n:Team) RETURN n.id AS id",
        parameters={"age": 30},
    ).records == [{"id": "a"}, {"id": "c"}]


def test_legacy_parse_rejects_union():
    with pytest.raises(ValueError, match="use parse_ast"):
        parse("MATCH (n) RETURN n AS id UNION MATCH (m) RETURN m AS id")
