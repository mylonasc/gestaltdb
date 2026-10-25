"""Capability boundaries, not merely relocated GraphDB implementations."""

import pytest

from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.history import TemporalHistory


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb")
    try:
        yield graph
    finally:
        graph.close()


def forbidden(*args, **kwargs):
    raise AssertionError("historical consumer called the mutable graph facade")


def test_standalone_history_needs_no_graph_facade_or_writes(graph, monkeypatch):
    version = graph.put_node_version(Node("n", properties={"value": 1}), valid=(0, 10))
    history = TemporalHistory.from_store(graph.store, graph.serializer)
    monkeypatch.setattr(graph.store, "put_metadata", forbidden)
    monkeypatch.setattr(GraphDB, "get_node_as_of", forbidden)
    monkeypatch.setattr(GraphDB, "get_temporal_commit", forbidden)
    assert history.get_node_as_of("n", valid_time=5).version_id == version.version_id
    assert history.get_node_version(version.version_id).node.properties == {"value": 1}
    assert list(history.iter_node_versions("n"))[0].version_id == version.version_id
    assert history.get_node_as_of("n", valid_time=10) is None
    assert not hasattr(history, "put_node")
    assert not hasattr(history, "commit_versions")
    assert not hasattr(history, "query")


def test_read_view_queries_do_not_round_trip_through_graphdb(graph, monkeypatch):
    version = graph.put_node_version(Node("n"), valid=(0, 10))
    with graph.read_view(valid_time=5) as view:
        monkeypatch.setattr(GraphDB, "get_node_as_of", forbidden)
        monkeypatch.setattr(GraphDB, "iter_nodes_as_of", forbidden)
        monkeypatch.setattr(GraphDB, "iter_node_versions", forbidden)
        monkeypatch.setattr(GraphDB, "get_node_version", forbidden)
        monkeypatch.setattr(GraphDB, "iter_claims_as_of", forbidden)
        assert view.get_node_as_of("n").version_id == version.version_id
        assert len(list(view.iter_nodes_as_of())) == 1
        assert len(list(view.iter_node_versions())) == 1
        assert view.get_node_version(version.version_id).version_id == version.version_id
        assert list(view.iter_claims_as_of()) == []


def test_history_binds_transaction_store_and_detaches_results(graph):
    first = graph.put_node_version(Node("n", properties={"value": 1}), valid=(0, 10))
    with graph.transaction() as tx:
        history = tx.history
        assert history._reader.store is tx.store
        assert history._reader.store is not graph.store
        tx.correct_node_version(Node("n", properties={"value": 2}),
            supersedes_version_id=first.version_id)
        assert history.get_node_as_of("n", valid_time=5).node.properties["value"] == 2
        assert graph.history.get_node_as_of("n", valid_time=5).node.properties["value"] == 1
        result = history.get_node_as_of("n", valid_time=5)
        result.node.properties["value"] = 999
        assert history.get_node_as_of("n", valid_time=5).node.properties["value"] == 2


def test_snapshot_history_reads_only_its_bound_store(graph, monkeypatch):
    version = graph.put_node_version(Node("n"), valid=(0, 10))
    with graph.current_read_view() as view:
        history = view._graph.history
        assert history._reader.store is view._graph.store
        assert history._reader.store is not graph.store
        monkeypatch.setattr(graph.store, "get_metadata", forbidden)
        assert history.get_node_as_of("n", valid_time=5).version_id == version.version_id
