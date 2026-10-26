"""Traversal services own adjacency reads/sampling and bind exact scopes."""

import random

import pytest

from gestaltdb.current import EdgeRepository, NodeRepository, StorageSession
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.traversal import AdjacencyReader, TraversalService
from tests.test_current_node_repository import _graph, forbidden


@pytest.fixture(params=["lmdb", "leveldb", "pyrex"])
def graph(tmp_path, request):
    graph = _graph(tmp_path, request.param)
    try:
        yield graph
    finally:
        graph.close()


def _populate(graph):
    nodes = NodeRepository(graph.nodes._session)
    edges = EdgeRepository(graph.nodes._session)
    for node_id in ("a", "b", "c"):
        nodes.put(Node(node_id))
    edges.put(Edge("ab", "a", "b", {"type": "T", "score": 1}))
    edges.put(Edge("ac", "a", "c", {"type": "U", "score": 2}))


def test_standalone_traversal_never_redispatches(graph, monkeypatch):
    _populate(graph)
    traversal = TraversalService(StorageSession(graph.store, graph.serializer))
    for name in ("__init__", "transaction", "put_edge", "get_typed_adjacency",
                 "count_typed_adjacency", "sample_nodes", "sample_neighbors",
                 "rebuild_typed_adjacency"):
        monkeypatch.setattr(GraphDB, name, forbidden)
    assert [record["neighbor_id"] for record in traversal.iter_typed_adjacency("a", "T")] == [b"b"]
    assert traversal.get_typed_adjacency("a", "U")[0]["target_id"] == b"c"
    assert traversal.count_typed_adjacency("a", "T") == 1
    assert traversal.count_typed_adjacency("b", "T", direction="in") == 1
    assert traversal.neighbors_by_edge_type("a", "U") == [b"c"]
    assert traversal.edges_by_edge_type("a", "U") == [b"ac"]
    assert traversal.sample_neighbors("a", "T", sample_size=5, rng=random.Random(1))[0]["edge_id"] == b"ab"
    assert set(traversal.sample_nodes(3, rng=random.Random(1))) == {"a", "b", "c"}
    paths = traversal.sample_typed_paths(
        ["a"], [{"edge_type": "T", "direction": "out", "sample_size": 1}])
    assert paths[0]["path"][0]["neighbor_id"] == b"b"
    subgraph = traversal.sample_typed_subgraph(
        ["a"], [{"edge_type": "T", "direction": "out", "sample_size": 1}])
    assert subgraph["nodes"][b"a"].get_id == "a"
    assert subgraph["edges"][b"ab"].target == "b"
    assert not hasattr(traversal.reader, "sample_nodes")
    assert not hasattr(traversal.reader, "rebuild_typed_adjacency")
    assert isinstance(traversal.reader, AdjacencyReader)


def test_count_cache_uses_scope_and_invalidates_after_write(graph):
    _populate(graph)
    assert graph.traversal.count_typed_adjacency("a", "T") == 1
    assert graph.traversal.count_typed_adjacency("a", "T") == 1
    graph.edges.put(Edge("ac", "a", "b", {"type": "T"}))
    assert graph.traversal.count_typed_adjacency("a", "T") == 2
    assert graph.traversal.count_typed_adjacency("a", "U") == 0
    graph.edges.delete(b"ac")
    assert graph.traversal.count_typed_adjacency("a", "T") == 1


def test_sample_nodes_label_scan_still_fails_closed_on_stale_index(graph):
    _populate(graph)
    from gestaltdb._graph import index_maintenance
    from gestaltdb.indexes import STALE_KEY

    index_maintenance.mark_indexes_stale(graph.store, STALE_KEY, "node_label")
    with pytest.raises(RuntimeError, match="stale"):
        graph.traversal.sample_nodes(1, label="Missing")


def test_rebuild_typed_adjacency_without_facade(graph, monkeypatch):
    _populate(graph)
    from gestaltdb._graph import adjacency

    edge = graph.edges.get(b"ab")
    adjacency.delete_typed_adjacency_for_edge(graph.store, {}, edge)
    assert graph.traversal.neighbors_by_edge_type("a", "T") == []
    monkeypatch.setattr(GraphDB, "__init__", forbidden)
    monkeypatch.setattr(GraphDB, "transaction", forbidden)
    assert graph.traversal.rebuild_typed_adjacency() == 2
    assert graph.traversal.neighbors_by_edge_type("a", "T") == [b"b"]


def test_traversal_transaction_and_snapshot_scopes(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "lmdb")
    try:
        _populate(graph)
        live = graph.traversal
        with pytest.raises(ValueError, match="abort"):
            with graph.transaction() as tx:
                assert tx.traversal is not live
                assert tx.traversal._session.store is tx.store
                tx.edges.put(Edge("ad", "a", "d", {"type": "T"}))
                assert tx.traversal.neighbors_by_edge_type("a", "T") == [b"b", b"d"]
                assert live.neighbors_by_edge_type("a", "T") == [b"b"]
                raise ValueError("abort")
        assert live.neighbors_by_edge_type("a", "T") == [b"b"]
        with graph.current_read_view() as view:
            assert view.traversal is not live
            assert view.traversal._session.store is view.store
            graph.edges.put(Edge("ad", "a", "d", {"type": "T"}))
            monkeypatch.setattr(graph.store, "iter_typed_adjacency", forbidden)
            assert view.traversal.neighbors_by_edge_type("a", "T") == [b"b"]
            assert not hasattr(view.traversal, "sample_nodes")
            assert not hasattr(view.traversal, "rebuild_typed_adjacency")
    finally:
        graph.close()
