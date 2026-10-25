"""Warm decode caches must preserve visibility, isolation and snapshot scope."""

import copy

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.serializers import JSONSerializer
from gestaltdb.versioning import TemporalCorruptionError
from tests.test_temporal_as_of import _MetadataStore


@pytest.fixture(params=["memory", "lmdb"])
def graph(request, tmp_path):
    if request.param == "lmdb":
        pytest.importorskip("lmdb")
        db = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    else:
        db = GraphDB(_MetadataStore(), JSONSerializer())
    try:
        yield db
    finally:
        db.close()


def test_public_commit_and_version_payloads_are_detached(graph):
    node = graph.temporal.put_node_version(Node("a", properties={"nested": [1]}), valid=(0, 10))
    edge = graph.temporal.put_edge_version(Edge("e", "a", "a", properties={"nested": [2]}), valid=(0, 10))
    first = graph.temporal.get_temporal_commit(node.commit_id)
    first.versions[0].node.properties["nested"].append(9)
    graph.temporal.get_node_version(node.version_id).node.properties["nested"].append(8)
    assert graph.temporal.get_temporal_commit(node.commit_id).versions[0].node.properties == {"nested": [1]}
    graph.temporal.get_temporal_commit(edge.commit_id).versions[0].edge.properties["nested"].append(9)
    graph.temporal.get_edge_version(edge.version_id).edge.properties["nested"].append(8)
    assert graph.temporal.get_edge_version(edge.version_id).edge.properties == {"nested": [2]}


def test_warm_cache_rechecks_record_and_marker(graph):
    version = graph.temporal.put_node_version(Node("a"), valid=(0, 10))
    graph.temporal.get_temporal_commit(version.commit_id)
    key = graph.temporal.record_key(version.commit_id, 0)
    original = graph.store.get_metadata(key)
    graph.store.put_metadata(key, b"corrupted")
    with pytest.raises(TemporalCorruptionError, match="record"):
        graph.temporal.get_temporal_commit(version.commit_id)
    graph.store.put_metadata(key, original)
    graph.store.delete_metadata(graph.temporal.visible_key(version.commit_id))
    assert graph.temporal.get_temporal_commit(version.commit_id) is None


def test_cache_does_not_cross_store_objects(graph):
    version = graph.temporal.put_node_version(Node("a"), valid=(0, 10))
    graph.temporal.get_temporal_commit(version.commit_id)
    other = GraphDB(_MetadataStore(), JSONSerializer())
    other.temporal._commit_cache = graph.temporal._commit_cache
    try:
        assert other.temporal.get_temporal_commit(version.commit_id) is None
    finally:
        other.close()


def test_pinned_snapshot_and_live_store_do_not_share_admission(tmp_path):
    pytest.importorskip("lmdb")
    from gestaltdb._graph.scopes import configure_snapshot_child

    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        version = graph.temporal.put_node_version(Node("a"), valid=(0, 10))
        graph.temporal.get_temporal_commit(version.commit_id)
        with graph.store.read_snapshot() as snapshot_store:
            pinned = copy.copy(graph)
            pinned.store = snapshot_store
            configure_snapshot_child(graph, pinned)
            assert pinned.temporal.get_temporal_commit(version.commit_id)
            graph.store.put_metadata(graph.temporal.visible_key(version.commit_id), b"bad")
            assert pinned.temporal.get_temporal_commit(version.commit_id)
            with pytest.raises(TemporalCorruptionError, match="marker"):
                graph.temporal.get_temporal_commit(version.commit_id)
    finally:
        graph.close()


def test_cache_remains_bounded_across_store_scopes():
    graph = GraphDB(_MetadataStore(), JSONSerializer())
    try:
        version = graph.temporal.put_node_version(Node("a"), valid=(0, 10))
        for _ in range(270):
            graph.store = copy.copy(graph.store)
            assert graph.temporal.get_temporal_commit(version.commit_id)
            assert len(graph.temporal._commit_cache) <= 256
    finally:
        graph.close()
