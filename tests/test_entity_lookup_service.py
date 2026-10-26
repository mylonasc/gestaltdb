"""Entity lookups compose index/node/edge readers without GraphDB redispatch."""

import pytest

from gestaltdb.current import StorageSession
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.lookups import EntityLookup
from tests.test_current_node_repository import _graph, forbidden


@pytest.fixture(params=["lmdb", "leveldb", "pyrex"])
def graph(tmp_path, request):
    graph = _graph(tmp_path, request.param)
    try:
        yield graph
    finally:
        graph.close()


def _populate(graph):
    graph.put_node(Node("a", labels=["Person"], properties={"age": 30}))
    graph.put_node(Node("b", labels=["Person"], properties={"age": 40}))
    graph.put_node(Node("c", labels=["Org"], properties={"age": 30}))
    graph.put_edge(Edge("ab", "a", "b", {"type": "T", "score": 1}))
    graph.put_edge(Edge("bc", "b", "c", {"type": "T", "score": 5}))
    graph.create_node_property_index("age")
    graph.create_edge_property_index("score")


def _lookup(graph):
    return EntityLookup(StorageSession(graph.store, graph.serializer))


def test_standalone_lookup_never_redispatches(graph, monkeypatch):
    _populate(graph)
    lookup = _lookup(graph)
    for name in ("__init__", "transaction", "get_node", "get_edge",
                 "nodes_by_label", "nodes_by_property", "edges_by_type",
                 "edges_by_property", "range_query_nodes"):
        monkeypatch.setattr(GraphDB, name, forbidden)
    assert {node.get_id for node in lookup.nodes_by_label("Person")} == {"a", "b"}
    assert lookup.count_nodes_by_label("Person") == 2
    assert {node.get_id for node in lookup.nodes_by_property("age", 30)} == {"a", "c"}
    assert lookup.count_nodes_by_property("age", 30) == 2
    assert [node.get_id for node in lookup.nodes_by_label_property("Person", "age", 40)] == ["b"]
    assert lookup.count_nodes_by_label_property("Person", "age", 40) == 1
    assert {node.get_id for node in lookup.nodes_by_property_range("age", 35, 45)} == {"b"}
    assert lookup.count_nodes_by_property_range("age", 35, 45) == 1
    assert {node.get_id for node in
            lookup.nodes_by_label_property_range("Person", "age", 20, 35)} == {"a"}
    assert lookup.count_nodes_by_label_property_range("Person", "age", 20, 35) == 1
    # `range_query_nodes` keeps the legacy stub's exact key format: it iterates
    # the store's raw range with hand-built keys rather than encoded indexes.
    seen = {}

    class RangeStore:
        def range_iter(self, start_key, end_key):
            seen["keys"] = (start_key, end_key)
            yield b"IDX:N:age:30:a", b""

    lookup._session.store = RangeStore()
    lookup._session.store.get_node = lambda node_id: graph.store.get_node(
        node_id.encode("utf-8"))
    try:
        assert [node.get_id for node in lookup.range_query_nodes("age", 30, 30)] == ["a"]
    finally:
        lookup._session.store = graph.store
    assert seen["keys"][0] == b"IDX:N:age:30:"
    assert seen["keys"][1] == "IDX:N:age:30:\xff".encode("utf-8")
    assert {edge.get_id for edge in lookup.edges_by_type("T")} == {"ab", "bc"}
    assert lookup.count_edges_by_type("T") == 2
    assert [edge.get_id for edge in lookup.edges_by_property("score", 5)] == ["bc"]
    assert lookup.count_edges_by_property("score", 5) == 1
    assert [edge.get_id for edge in lookup.edges_by_type_property("T", "score", 1)] == ["ab"]
    assert lookup.count_edges_by_type_property("T", "score", 1) == 1
    assert {edge.get_id for edge in lookup.edges_by_property_range("score", 0, 3)} == {"ab"}
    assert lookup.count_edges_by_property_range("score", 0, 3) == 1
    assert [edge.get_id for edge in
            lookup.edges_by_type_property_range("T", "score", 4, 6)] == ["bc"]
    assert lookup.count_edges_by_type_property_range("T", "score", 4, 6) == 1
    for mutating in ("put", "delete", "create_node_property_index",
                     "rebuild_node_indexes", "commit_versions"):
        assert not hasattr(lookup, mutating)


def test_lookup_transaction_and_snapshot_scopes(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "lmdb")
    try:
        _populate(graph)
        live = graph.lookup
        with pytest.raises(ValueError, match="abort"):
            with graph.transaction() as tx:
                assert tx.lookup is not live
                tx.put_node(Node("d", labels=["Person"], properties={"age": 50}))
                assert {node.get_id for node in tx.lookup.nodes_by_label("Person")} == {"a", "b", "d"}
                assert {node.get_id for node in live.nodes_by_label("Person")} == {"a", "b"}
                raise ValueError("abort")
        assert {node.get_id for node in live.nodes_by_label("Person")} == {"a", "b"}
        with graph.current_read_view() as view:
            assert {node.get_id for node in view.lookup.nodes_by_label("Person")} == {"a", "b"}
            graph.put_node(Node("d", labels=["Person"], properties={"age": 50}))
            monkeypatch.setattr(graph.store, "get_node", forbidden)
            assert {node.get_id for node in view.lookup.nodes_by_label("Person")} == {"a", "b"}
            assert not hasattr(view.lookup, "create_node_property_index")
    finally:
        graph.close()
