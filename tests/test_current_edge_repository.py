"""Edge services own current writes and bind exact transaction/snapshot scopes."""

import pytest

from gestaltdb.current import EdgeRepository, StorageSession
from gestaltdb.graphdb import Edge, GraphDB
from tests.test_current_node_repository import _graph, forbidden


@pytest.fixture(params=["lmdb", "leveldb", "pyrex"])
def graph(tmp_path, request):
    graph = _graph(tmp_path, request.param)
    try:
        yield graph
    finally:
        graph.close()


def test_standalone_edge_service_never_redispatches(graph, monkeypatch):
    graph.create_edge_property_index("score")
    repository = EdgeRepository(StorageSession(graph.store, graph.serializer))
    for name in ("__init__", "transaction", "put_edge", "get_edge", "delete_edge",
                 "put_edges_bulk"):
        monkeypatch.setattr(GraphDB, name, forbidden)
    repository.put(Edge("e", "a", "b", {"type": "Old", "score": 1}))
    repository.put_many([Edge("e", "c", "d", {"type": "New", "score": 2})])
    assert repository.get(b"e").source == "c"
    assert set(repository.keys()) == {b"e"}
    assert graph.count_typed_adjacency("a", "Old") == 0
    assert len(list(graph.iter_typed_adjacency("c", "New"))) == 1
    assert list(graph.iter_edge_ids_by_property("score", 1)) == []
    assert list(graph.iter_edge_ids_by_property("score", 2)) == [b"e"]
    repository.update(b"e", {"score": 3}, lambda old, new: {**old, **new})
    assert repository.get(b"e").properties["score"] == 3
    repository.delete(b"e")
    assert repository.get(b"e") is None
    assert list(graph.iter_typed_adjacency("c", "New")) == []
    assert not hasattr(repository.reader, "put")
    assert not hasattr(repository, "commit_versions")


def test_facade_edge_calls_do_not_construct_child_graphs(graph, monkeypatch):
    monkeypatch.setattr(GraphDB, "__init__", forbidden)
    monkeypatch.setattr(GraphDB, "transaction", forbidden)
    graph.put_edge(Edge("e", "a", "b"))
    graph.put_edges_bulk([Edge("other", "a", "c")], check_existing=False)
    assert graph.get_edge(b"e").target == "b"
    graph.delete_edge(b"e")
    assert graph.get_edge(b"e") is None


def test_handle_repository_updates_cached_counts_and_preserves_history(graph):
    repository = graph.edges
    assert graph.edges is repository
    repository.put(Edge("e", "a", "b", {"type": "Old"}))
    historical = graph.temporal.put_edge_version(
        Edge("e", "a", "b", {"type": "Historical"}), valid=(0, 10))
    assert graph.count_typed_adjacency("a", "Old") == 1
    repository.put(Edge("e", "c", "d", {"type": "New"}), update_adjacency=False)
    assert graph.count_typed_adjacency("a", "Old") == 0
    assert graph.count_typed_adjacency("c", "New") == 1
    repository.delete(b"e")
    assert graph.count_typed_adjacency("c", "New") == 0
    assert graph.temporal.get_edge_as_of("e", valid_time=5).version_id == historical.version_id


@pytest.mark.parametrize("backend", ["lmdb", "pyrex"])
def test_bulk_failure_rolls_back_edge_adjacency_and_indexes(tmp_path, backend, monkeypatch):
    graph = _graph(tmp_path, backend, transactional=backend == "pyrex")
    try:
        graph.create_edge_property_index("score")
        repository = graph.edges
        repository.put(Edge("e", "a", "b", {"type": "Old", "score": 1}))
        assert graph.count_typed_adjacency("a", "Old") == 1
        original = repository._session.entity_serializer.serialize

        def broken(edge, kind):
            if kind == "Edge" and edge.get_id == "bad":
                raise ValueError("injected serialization failure")
            return original(edge, kind)

        monkeypatch.setattr(repository._session.entity_serializer, "serialize", broken)
        with pytest.raises(ValueError, match="injected"):
            repository.put_many([Edge("e", "c", "d", {"type": "New", "score": 2}),
                                 Edge("bad", "c", "d")])
        assert repository.get(b"e").source == "a"
        assert repository.get(b"bad") is None
        assert graph.count_typed_adjacency("a", "Old") == 1
        assert graph.count_typed_adjacency("c", "New") == 0
        assert list(graph.iter_edge_ids_by_property("score", 1)) == [b"e"]
        assert list(graph.iter_edge_ids_by_property("score", 2)) == []
    finally:
        graph.close()


def test_edge_transaction_and_snapshot_scopes(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "lmdb")
    try:
        graph.edges.put(Edge("e", "a", "b", {"type": "T"}))
        live = graph.edges
        with pytest.raises(ValueError, match="abort"):
            with graph.transaction() as tx:
                assert tx.edges is not live
                assert tx.edges._session.store is tx.store
                assert tx.edges._session.transaction_bound
                tx.edges.put(Edge("e", "c", "d", {"type": "T"}))
                assert live.get(b"e").source == "a"
                raise ValueError("abort")
        assert live.get(b"e").source == "a"
        with graph.current_read_view() as view:
            assert view._graph.edges is not live
            assert view.edges._session.store is view.store
            graph.edges.put(Edge("e", "c", "d", {"type": "T"}))
            monkeypatch.setattr(graph.store, "get_edge", forbidden)
            assert view.edges.get(b"e").source == "a"
            assert not hasattr(view.edges, "delete")
    finally:
        graph.close()


def test_reassigning_store_rebinds_edge_repository(tmp_path):
    graph = _graph(tmp_path / "one", "lmdb")
    other = _graph(tmp_path / "two", "lmdb")
    original_store = graph.store
    try:
        graph.edges.put(Edge("e", "a", "b"))
        other.edges.put(Edge("e", "c", "d"))
        first = graph.edges
        graph.store = other.store
        assert graph.edges is not first
        assert graph.edges._session.store is other.store
        assert graph.edges.get(b"e").source == "c"
        assert first.get(b"e").source == "a"
    finally:
        graph.store = original_store
        graph.close()
        other.close()
