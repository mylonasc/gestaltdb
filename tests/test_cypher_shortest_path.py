"""Shortest-path coverage for the GestaltDB Cypher engine ([cypher-13]).

Covers ``MATCH SHORTEST [k]`` / ``ALL SHORTEST`` selectors and the
``shortestPath`` / ``allShortestPaths`` functions: minimal-length
semantics, limits, validation, and interaction with the read pipeline.
"""

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _shortest_graph() -> FakeCypherGraph:
    graph = FakeCypherGraph()
    for node_id in ("a", "b", "c", "d"):
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="b", target="c", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e3", source="a", target="c", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e4", source="a", target="d", properties={"type": "T"}))
    return graph


def test_match_shortest_returns_first_shortest_match():
    records = execute(
        _shortest_graph(), 'MATCH SHORTEST (x {id: "a"})-[*]->(y) RETURN y.id'
    ).records

    assert records == [{"y.id": "b"}]


def test_match_shortest_with_limit_returns_k_shortest():
    records = execute(
        _shortest_graph(), 'MATCH SHORTEST 2 (x {id: "a"})-[*]->(y) RETURN y.id'
    ).records

    assert records == [{"y.id": "b"}, {"y.id": "c"}]


def test_match_all_shortest_returns_minimal_level():
    records = execute(
        _shortest_graph(), 'MATCH ALL SHORTEST (x {id: "a"})-[*]->(y) RETURN y.id'
    ).records

    assert records == [{"y.id": "b"}, {"y.id": "c"}, {"y.id": "d"}]


def test_match_any_shortest_behaves_like_limit_one():
    records = execute(
        _shortest_graph(), 'MATCH ANY SHORTEST (x {id: "a"})-[*]->(y) RETURN y.id'
    ).records

    assert records == [{"y.id": "b"}]


def test_match_shortest_respects_bounds_and_no_match():
    graph = _shortest_graph()

    assert execute(
        graph, 'MATCH SHORTEST (x {id: "a"})-[*2..]->(y) RETURN y.id'
    ).records == [{"y.id": "c"}]
    assert execute(
        graph, 'MATCH SHORTEST (x {id: "d"})-[*]->(y) RETURN y.id'
    ).records == []
    assert execute(
        graph, 'MATCH ALL SHORTEST (x {id: "d"})-[*]->(y) RETURN y.id'
    ).records == []


def test_match_shortest_rejects_bad_selectors():
    graph = _shortest_graph()

    with pytest.raises(ValueError, match="SHORTEST limit must be a positive integer"):
        execute(graph, 'MATCH SHORTEST 0 (x {id: "a"})-[*]->(y) RETURN y.id')
    with pytest.raises(ValueError, match="SHORTEST selector must be"):
        execute(graph, 'MATCH SINGLE SHORTEST (x {id: "a"})-[*]->(y) RETURN y.id')


def test_shortest_path_function_returns_node_list():
    records = execute(
        _shortest_graph(),
        'MATCH (x {id: "a"}), (y {id: "c"}) RETURN shortestPath((x)-[*]->(y)) AS path',
    ).records

    assert len(records) == 1
    assert [node.get_id for node in records[0]["path"]] == ["a", "c"]


def test_shortest_path_function_no_match_returns_null():
    records = execute(
        _shortest_graph(),
        'MATCH (x {id: "d"}), (y {id: "a"}) RETURN shortestPath((x)-[*]->(y)) AS path',
    ).records

    assert records == [{"path": None}]


def test_all_shortest_paths_function_returns_path_lists():
    graph = FakeCypherGraph()
    for node_id in ("a", "b", "c"):
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="a", target="c", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e3", source="b", target="c", properties={"type": "T"}))

    records = execute(
        graph, 'MATCH (x {id: "a"}), (y {id: "c"}) RETURN allShortestPaths((x)-[*]->(y)) AS paths'
    ).records

    assert len(records) == 1
    assert [[node.get_id for node in path] for path in records[0]["paths"]] == [["a", "c"]]


def test_shortest_path_function_validates_shape():
    graph = _shortest_graph()

    with pytest.raises(ValueError, match="unbound variable"):
        execute(graph, 'MATCH (x {id: "a"}) RETURN shortestPath((x)-[*]->(y)) AS path')
    with pytest.raises(ValueError, match="bound variables"):
        execute(
            graph,
            'MATCH (x {id: "a"}), (y {id: "c"}) RETURN shortestPath((x)-[*]->()) AS path',
        )
    with pytest.raises(ValueError, match="single-hop"):
        execute(
            graph,
            'MATCH (x {id: "a"}), (y {id: "c"}) RETURN shortestPath((x)-[*]->()-[*]->(y)) AS path',
        )
    with pytest.raises(TypeError, match="endpoints must be nodes"):
        execute(
            graph,
            'MATCH (x {id: "a"}) WITH [1] AS y, x RETURN shortestPath((x)-[*]->(y)) AS path',
        )


def test_shortest_path_in_where_and_with_aggregation():
    graph = _shortest_graph()

    assert execute(
        graph,
        'MATCH (x {id: "a"}), (y) WHERE shortestPath((x)-[*]->(y)) IS NOT NULL RETURN y.id ORDER BY y.id',
    ).records == [{"y.id": "b"}, {"y.id": "c"}, {"y.id": "d"}]
    assert execute(
        graph,
        'MATCH (x {id: "a"}) MATCH SHORTEST (x)-[*]->(y) RETURN count(*) AS total',
    ).records == [{"total": 1}]


def test_legacy_parse_rejects_shortest_forms():
    with pytest.raises(ValueError, match="use parse_ast"):
        parse('MATCH SHORTEST (a)-[*]->(b) RETURN b')
    with pytest.raises(ValueError, match="use parse_ast"):
        parse('MATCH (a {id: "x"}), (b {id: "y"}) RETURN shortestPath((a)-[*]->(b))')
