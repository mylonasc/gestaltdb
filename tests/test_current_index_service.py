"""Current index authority, DDL publication, freshness timing and scope binding."""

import pytest

from gestaltdb.current import EdgeRepository, NodeRepository, StorageSession
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.indexes import IndexService
from gestaltdb._graph import index_maintenance
from tests.test_current_node_repository import _graph, forbidden


@pytest.fixture(params=["lmdb", "leveldb", "pyrex"])
def graph(tmp_path, request):
    graph = _graph(tmp_path, request.param)
    try:
        yield graph
    finally:
        graph.close()


def test_index_service_standalone_no_facade_redispatch(graph, monkeypatch):
    session = StorageSession(graph.store, graph.serializer)
    nodes, edges = NodeRepository(session), EdgeRepository(session)
    nodes.put(Node("a", labels=["Person"], properties={"age": 42}))
    edges.put(Edge("e", "a", "b", {"type": "T", "score": 7}))
    indexes = IndexService(session)
    for name in ("__init__", "transaction", "create_node_property_index",
                 "create_edge_property_index", "rebuild_node_indexes", "rebuild_edge_indexes"):
        monkeypatch.setattr(GraphDB, name, forbidden)
    assert indexes.create_node_property_index("age") == 1
    assert indexes.create_edge_property_index("score") == 1
    assert list(indexes.iter_node_ids_by_label("Person")) == [b"a"]
    assert list(indexes.iter_node_ids_by_property("age", 42)) == [b"a"]
    assert list(indexes.iter_node_ids_by_label_property("Person", "age", 42)) == [b"a"]
    assert list(indexes.iter_node_ids_by_property_range("age", 40, 45)) == [b"a"]
    assert list(indexes.iter_node_ids_by_label_property_range("Person", "age", 40, 45)) == [b"a"]
    assert list(indexes.iter_edge_ids_by_type("T")) == [b"e"]
    assert list(indexes.iter_edge_ids_by_property("score", 7)) == [b"e"]
    assert list(indexes.iter_edge_ids_by_type_property("T", "score", 7)) == [b"e"]
    assert list(indexes.iter_edge_ids_by_property_range("score", 5, 10)) == [b"e"]
    assert list(indexes.iter_edge_ids_by_type_property_range("T", "score", 5, 10)) == [b"e"]
    assert indexes.statistics()["indexed_edge_properties"] == ("score",)
    assert not hasattr(indexes.reader, "create_node_property_index")


def test_failed_ddl_does_not_publish_live_configuration(graph, monkeypatch):
    graph.put_node(Node("a", properties={"age": 42}))
    graph.put_edge(Edge("e", "a", "b", {"score": 7}))
    original = graph.entity_serializer.deserialize

    def broken(*args, **kwargs):
        raise ValueError("injected decode failure")

    monkeypatch.setattr(graph.entity_serializer, "deserialize", broken)
    with pytest.raises(ValueError, match="injected"):
        graph.indexes.create_node_property_index("age")
    with pytest.raises(ValueError, match="injected"):
        graph.indexes.create_edge_property_index("score")
    assert "age" not in graph.indexed_node_properties
    assert "score" not in graph.indexed_edge_properties
    assert "age" not in index_maintenance.load_property_index_metadata(
        graph.store, b"schema:indexes:node_properties")
    monkeypatch.setattr(graph.entity_serializer, "deserialize", original)
    assert graph.indexes.create_node_property_index("age") == 1
    assert list(graph.indexes.iter_node_ids_by_property("age", 42)) == [b"a"]


def test_index_freshness_timing_is_preserved(graph):
    indexes = graph.indexes
    index_maintenance.mark_indexes_stale(graph.store, b"schema:indexes:stale", "node_property")
    lazy = indexes.iter_node_ids_by_property("age", 42)
    with pytest.raises(RuntimeError, match="stale"):
        next(lazy)
    with pytest.raises(RuntimeError, match="stale"):
        indexes.iter_node_ids_by_property_range("age", 0, 100)


def test_index_transaction_rollback_and_snapshot_scope(tmp_path):
    graph = _graph(tmp_path, "lmdb")
    try:
        graph.put_node(Node("a", labels=["Before"], properties={"age": 42}))
        with pytest.raises(ValueError, match="abort"):
            with graph.transaction() as tx:
                tx.indexes.create_node_property_index("age")
                assert "age" in tx.indexed_node_properties
                assert "age" not in graph.indexed_node_properties
                raise ValueError("abort")
        assert "age" not in graph.indexed_node_properties
        with graph.current_read_view() as view:
            assert view.indexes._session.store is view.store
            graph.indexes.create_node_property_index("age")
            graph.put_node(Node("b", labels=["After"], properties={"age": 42}))
            assert view.indexes.statistics()["indexed_node_properties"] == ()
            assert list(view.indexes.iter_node_ids_by_label("After")) == []
            assert not hasattr(view.indexes, "create_node_property_index")
        assert list(graph.indexes.iter_node_ids_by_property("age", 42)) == [b"a", b"b"]
    finally:
        graph.close()
