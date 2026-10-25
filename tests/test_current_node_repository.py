"""Current-node services own backend scope, not GraphDB redispatch."""

import pytest

from gestaltdb.current import NodeRepository, StorageSession
from gestaltdb.graphdb import Edge, GraphDB, Node


def _graph(tmp_path, backend, *, transactional=False):
    pytest.importorskip({"lmdb": "lmdb", "leveldb": "plyvel", "pyrex": "pyrex"}[backend])
    options = {"transactional": True} if transactional else None
    return GraphDB.create(tmp_path / "db", backend=backend, backend_options=options)


@pytest.fixture(params=["lmdb", "leveldb", "pyrex"])
def graph(tmp_path, request):
    graph = _graph(tmp_path, request.param)
    try:
        yield graph
    finally:
        graph.close()


def forbidden(*args, **kwargs):
    raise AssertionError("repository redispatched through GraphDB")


def test_standalone_repository_reads_and_writes_without_facade(graph, monkeypatch):
    graph.create_node_property_index("name")
    repository = NodeRepository(StorageSession(graph.store, graph.serializer))
    monkeypatch.setattr(GraphDB, "__init__", forbidden)
    monkeypatch.setattr(GraphDB, "transaction", forbidden)
    monkeypatch.setattr(GraphDB, "put_node", forbidden)
    monkeypatch.setattr(GraphDB, "get_node", forbidden)
    repository.put(Node("a", labels=["Old"], properties={"name": "before"}))
    repository.put_many([Node("a", labels=["New"], properties={"name": "after"}), Node("b")])
    assert repository.get(b"a").properties == {"name": "after"}
    assert repository.get_many([b"a", b"missing"])[1] is None
    assert set(repository.keys()) == {b"a", b"b"}
    assert list(graph.iter_node_ids_by_label("Old")) == []
    assert list(graph.iter_node_ids_by_label("New")) == [b"a"]
    assert list(graph.iter_node_ids_by_property("name", "before")) == []
    assert list(graph.iter_node_ids_by_property("name", "after")) == [b"a"]
    repository.delete("a")
    assert repository.get(b"a") is None
    assert not hasattr(repository, "assert_claim")
    assert not hasattr(repository, "query")


def test_facade_node_calls_do_not_construct_transaction_graphs(graph, monkeypatch):
    monkeypatch.setattr(GraphDB, "__init__", forbidden)
    monkeypatch.setattr(GraphDB, "transaction", forbidden)
    graph.put_node(Node("a"))
    graph.put_nodes([Node("b")])
    assert graph.get_node(b"a").get_id == "a"
    graph.delete_node("a")
    assert graph.get_node(b"a") is None


def test_repository_delete_cascades_current_edges_only(graph):
    graph.put_node(Node("a"))
    graph.put_node(Node("b"))
    graph.put_edge(Edge("e", source="a", target="b", properties={"type": "T"}))
    historical = graph.put_node_version(Node("a"), valid=(0, 10))
    graph.nodes.delete("a")
    assert graph.get_edge(b"e") is None
    assert graph.count_typed_adjacency("b", "T", "in") == 0
    assert graph.history.get_node_as_of("a", valid_time=5).version_id == historical.version_id


@pytest.mark.parametrize("backend", ["lmdb", "pyrex"])
def test_repository_bulk_failure_rolls_back_records_and_indexes(tmp_path, backend, monkeypatch):
    graph = _graph(tmp_path, backend, transactional=backend == "pyrex")
    try:
        repository = graph.nodes
        repository.put(Node("a", labels=["Before"]))
        original = repository._session.entity_serializer.serialize

        def broken(node, kind):
            if node.get_id == "bad":
                raise ValueError("injected serialization failure")
            return original(node, kind)

        monkeypatch.setattr(repository._session.entity_serializer, "serialize", broken)
        with pytest.raises(ValueError, match="injected"):
            repository.put_many([Node("a", labels=["After"]), Node("bad")])
        assert repository.get(b"a").labels == ("Before",)
        assert list(graph.iter_node_ids_by_label("Before")) == [b"a"]
        assert list(graph.iter_node_ids_by_label("After")) == []
        assert repository.get(b"bad") is None
    finally:
        graph.close()


def test_transaction_children_copy_configuration_and_rollback(tmp_path):
    graph = _graph(tmp_path, "lmdb")
    try:
        graph.create_node_property_index("name")
        with pytest.raises(ValueError, match="abort"):
            with graph.transaction() as tx:
                repository = tx.nodes
                assert repository._session.store is tx.store
                assert repository._session.transaction_bound
                assert repository._session.indexed_node_properties is not graph.indexed_node_properties
                repository.put(Node("a", properties={"name": "Alice"}))
                assert graph.nodes.get(b"a") is None
                raise ValueError("abort")
        assert graph.nodes.get(b"a") is None
        assert list(graph.iter_node_ids_by_property("name", "Alice")) == []
    finally:
        graph.close()


def test_snapshot_view_preserves_cypher_facade_seam(tmp_path, monkeypatch):
    """View reads honor snapshot-copied facade seams (Cypher lazy-scan contract)."""
    graph = _graph(tmp_path, "lmdb")
    try:
        graph.nodes.put(Node("a"))
        with graph.current_read_view() as view:
            seen = []

            def fake_get_node(node_id):
                seen.append(node_id)
                return Node(node_id=node_id.decode("utf-8"))

            # Instance seams copy onto the snapshot child; the view must honor them.
            monkeypatch.setattr(view._graph, "get_node", fake_get_node)
            assert view.get_node(b"a").get_id == "a"
            assert seen == [b"a"]
            # The narrowed reader stays on the store and ignores facade seams.
            assert view.nodes.get(b"a").get_id == "a"
            assert seen == [b"a"]
    finally:
        graph.close()


def test_snapshot_repository_binds_snapshot_not_live_store(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "lmdb")
    try:
        graph.nodes.put(Node("a"))
        live_repository = graph.nodes
        assert graph.nodes is live_repository
        with graph.current_read_view() as view:
            repository = view._graph.nodes
            assert repository is not live_repository
            assert repository._session.store is view._graph.store
            assert repository._session.store is not graph.store
            monkeypatch.setattr(graph.store, "get_node", forbidden)
            assert repository.get(b"a").get_id == "a"
            assert view.get_node(b"a").get_id == "a"
            assert view.nodes.get(b"a").get_id == "a"
            assert not hasattr(view.nodes, "put")
            # The narrowed snapshot reader bypasses GraphDB methods entirely.
            monkeypatch.setattr(GraphDB, "get_node", forbidden)
            assert view.nodes.get(b"a").get_id == "a"
            assert repository.get(b"a").get_id == "a"
    finally:
        graph.close()
