from datetime import datetime, timedelta, timezone

import pytest

from gestaltdb.graphdb import Edge, GraphDB, TimeIndexedEdge
from gestaltdb.serializers import PickleSerializer
from gestaltdb.versioning import TemporalCorruptionError, TemporalVersionError


def _legacy_edge(when, *, revision):
    return TimeIndexedEdge(
        when,
        edge_id="employment",
        source="alice",
        target="acme",
        properties={"type": "WORKS_FOR", "revision": revision},
    )


def test_time_indexed_edge_migration_is_idempotent_and_preserves_current_records(graph_db):
    if not isinstance(graph_db.serializer, PickleSerializer):
        pytest.skip("legacy TimeIndexedEdge records require PickleSerializer")
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    first = _legacy_edge(start, revision=1)
    second = _legacy_edge(start + timedelta(days=30), revision=2)
    current = Edge("ordinary", "alice", "acme", {"type": "CURRENT"})
    graph_db.put_edge(first, update_adjacency=False)
    graph_db.put_edge(second, update_adjacency=False)
    graph_db.put_edge(current, update_adjacency=False)

    migrated = graph_db.migrate_time_indexed_edges(metadata={"operator": "test"})

    assert len(migrated) == 2
    assert migrated[0].valid.start.epoch_microseconds < migrated[1].valid.start.epoch_microseconds
    assert migrated[0].valid.end == migrated[1].valid.start
    assert migrated[1].valid.end is None
    assert migrated[0].edge.properties["revision"] == 1
    assert migrated[1].edge.properties["revision"] == 2
    assert graph_db.get_edge(current.get_id_bytes).to_dict() == current.to_dict()
    assert graph_db.migrate_time_indexed_edges() == ()

    assert graph_db.migrate_time_indexed_edges(delete_legacy=True) == ()
    assert graph_db.get_edge(first.get_id_bytes) is None
    assert graph_db.get_edge(second.get_id_bytes) is None
    assert graph_db.get_edge(current.get_id_bytes) is not None
    assert len(list(graph_db.iter_edge_versions("employment"))) == 2


def test_time_indexed_edge_migration_rejects_changed_input_after_publication(graph_db):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    original = _legacy_edge(start, revision=1)
    graph_db.put_edge(original, update_adjacency=False)
    graph_db.migrate_time_indexed_edges()
    graph_db.put_edge(_legacy_edge(start, revision=2), update_adjacency=False)

    with pytest.raises(TemporalVersionError, match="changed after migration"):
        graph_db.migrate_time_indexed_edges()

    assert len(list(graph_db.iter_edge_versions("employment"))) == 1


def test_time_indexed_edge_migration_rejects_new_logical_id_after_publication(graph_db):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    graph_db.put_edge(_legacy_edge(start, revision=1), update_adjacency=False)
    graph_db.migrate_time_indexed_edges()
    graph_db.put_edge(
        TimeIndexedEdge(
            start,
            edge_id="new-employment",
            source="bob",
            target="acme",
            properties={"type": "WORKS_FOR"},
        ),
        update_adjacency=False,
    )

    with pytest.raises(TemporalVersionError, match="new logical IDs after migration"):
        graph_db.migrate_time_indexed_edges()

    assert len(list(graph_db.iter_temporal_commits())) == 1


def test_time_indexed_edge_migration_fails_closed_on_mismatched_key(graph_db):
    edge = _legacy_edge(datetime(2024, 1, 1, tzinfo=timezone.utc), revision=1)
    graph_db.store.put_edge(b"wrong-key", graph_db.entity_serializer.serialize(edge, "Edge"))

    with pytest.raises(TemporalCorruptionError, match="key does not match"):
        graph_db.migrate_time_indexed_edges()

    assert list(graph_db.iter_temporal_commits()) == []


def test_time_indexed_edge_migration_fails_closed_on_undecodable_record(graph_db):
    graph_db.store.put_edge(b"corrupt", b"not-a-pickle")

    with pytest.raises(TemporalCorruptionError, match="cannot decode"):
        graph_db.migrate_time_indexed_edges()

    assert list(graph_db.iter_temporal_commits()) == []


def test_time_indexed_edge_cleanup_can_resume_after_interruption(graph_db, monkeypatch):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    first = _legacy_edge(start, revision=1)
    second = _legacy_edge(start + timedelta(days=30), revision=2)
    graph_db.put_edge(first, update_adjacency=False)
    graph_db.put_edge(second, update_adjacency=False)
    original_delete = graph_db.delete_edge
    attempts = 0

    def interrupted_delete(raw_key):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("interrupted cleanup")
        original_delete(raw_key)

    monkeypatch.setattr(graph_db, "delete_edge", interrupted_delete)
    with pytest.raises(RuntimeError, match="interrupted cleanup"):
        graph_db.migrate_time_indexed_edges(delete_legacy=True)

    assert graph_db.get_edge(first.get_id_bytes) is None
    assert graph_db.get_edge(second.get_id_bytes) is not None
    monkeypatch.setattr(graph_db, "delete_edge", original_delete)
    assert graph_db.migrate_time_indexed_edges(delete_legacy=True) == ()
    assert graph_db.get_edge(second.get_id_bytes) is None
    assert len(list(graph_db.iter_edge_versions("employment"))) == 2
