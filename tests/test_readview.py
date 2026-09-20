"""Consistent temporal read views and source provenance."""

import importlib.util
import json
import threading
import uuid

import pytest

import gestaltdb.graphdb as graphdb_module
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.readview import ProvenanceMismatchError, ReadViewProvenance
from gestaltdb.temporal import TemporalInterval
from gestaltdb.versioning import EdgeVersionWrite, NodeVersionWrite
from tests.test_temporal_as_of import _MetadataStore


def _available_backend():
    for module, backend in (("lmdb", "lmdb"), ("plyvel", "leveldb"), ("pyrex", "pyrex")):
        if importlib.util.find_spec(module) is not None:
            return backend
    pytest.skip("no optional storage backend is installed")


@pytest.fixture
def managed_graph(tmp_path):
    path = tmp_path / "graph"
    graph = GraphDB.create(path, backend=_available_backend(), serializer="json")
    try:
        yield graph, path
    finally:
        graph.close()


@pytest.fixture
def memory_graph(tmp_path):
    graph = GraphDB(_MetadataStore(), graphdb_module.JSONSerializer())
    graph._backend_name = "leveldb"
    graph.save_manifest(tmp_path / "memory-graph")
    return graph


def _populate_history(graph):
    valid = TemporalInterval.parse("2024-01-01T00:00:00Z", None)
    first = graph.commit_versions(
        [
            NodeVersionWrite.assertion(Node("a", properties={"kind": "person", "name": "old"}), valid),
            NodeVersionWrite.assertion(Node("b", properties={"kind": "person"}), valid),
            EdgeVersionWrite.assertion(Edge("ab", "a", "b", properties={"type": "KNOWS"}), valid),
        ]
    )
    return first


def test_database_identity_is_stable_across_reopen(managed_graph):
    graph, path = managed_graph
    database_id = graph.database_id
    assert graph.manifest["database_id"] == database_id
    graph.close()

    reopened = GraphDB.open(path)
    try:
        assert reopened.database_id == database_id
        assert reopened.manifest["database_id"] == database_id
    finally:
        reopened.close()


def test_open_rejects_identity_metadata_conflicting_with_manifest(managed_graph):
    graph, path = managed_graph
    graph.store.put_metadata(graphdb_module._DATABASE_ID_METADATA_KEY, str(uuid.uuid4()).encode("ascii"))
    graph.close()
    with pytest.raises(ValueError, match="identity"):
        GraphDB.open(path)


def test_provenance_is_deterministic_json_and_detects_tampering(managed_graph):
    graph, _ = managed_graph
    _populate_history(graph)
    with graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        encoded = view.provenance.to_json()
        assert encoded == view.provenance.to_json()
        assert ReadViewProvenance.from_json(encoded) == view.provenance
        assert json.loads(encoded)["token"] == view.provenance.token

        tampered = view.provenance.to_dict()
        tampered["commit_horizon"] = 0
        with pytest.raises(ProvenanceMismatchError, match="token mismatch"):
            ReadViewProvenance.from_dict(tampered)


def test_read_view_keeps_captured_horizon(managed_graph):
    graph, _ = managed_graph
    first = _populate_history(graph)
    original = first.versions[0]
    with graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        graph.correct_node_version(
            Node("a", properties={"kind": "person", "name": "new"}),
            supersedes_version_id=original.version_id,
        )
        assert view.commit_horizon == first.commit_id
        assert view.get_node_as_of("a").node.properties["name"] == "old"
        assert graph.get_node_as_of("a", valid_time="2024-06-01T00:00:00Z").node.properties["name"] == "new"


def test_empty_history_read_view_has_authenticated_provenance(managed_graph):
    graph, _ = managed_graph
    with graph.read_view(valid_time="2024-01-01T00:00:00Z") as view:
        assert view.commit_horizon == 0
        assert view.provenance.commit_marker_sha256 is None
        view.verify_source()


def test_temporal_snapshot_uses_one_view_and_verified_hydration(managed_graph, tmp_path):
    graph, _ = managed_graph
    first = _populate_history(graph)
    original = first.versions[0]
    with graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        snapshot = view.build_sampler_snapshot(tmp_path / "snapshot")

    graph.correct_node_version(
        Node("a", properties={"kind": "person", "name": "new"}),
        supersedes_version_id=original.version_id,
    )
    assert snapshot.metadata["source_provenance"]["commit_horizon"] == first.commit_id
    assert sorted(snapshot.external_node_ids.tolist()) == ["a", "b"]
    assert snapshot.external_edge_ids.tolist() == ["ab"]
    a_index = snapshot.external_node_ids.tolist().index("a")
    assert snapshot.get_node(graph, a_index).properties["name"] == "old"
    snapshot.verify_source(graph)


def test_snapshot_rejects_replacement_source(managed_graph, tmp_path):
    graph, _ = managed_graph
    _populate_history(graph)
    with graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        snapshot = view.build_sampler_snapshot(tmp_path / "snapshot")

    replacement = GraphDB.create(tmp_path / "replacement", backend=_available_backend(), serializer="json")
    try:
        with pytest.raises(ProvenanceMismatchError, match="database identity mismatch"):
            snapshot.verify_source(replacement)
    finally:
        replacement.close()


def test_read_view_rejects_future_or_invalid_horizon(managed_graph):
    graph, _ = managed_graph
    _populate_history(graph)
    with pytest.raises(ValueError, match="newer than"):
        with graph.read_view(valid_time="2024-01-01T00:00:00Z", through_commit=2):
            pass
    with pytest.raises(ValueError, match="non-negative"):
        with graph.read_view(valid_time="2024-01-01T00:00:00Z", through_commit=-1):
            pass


def test_read_view_stops_before_gap_and_ignores_later_marker(memory_graph):
    _populate_history(memory_graph)
    second = memory_graph.put_node_version(
        Node("c", properties={"kind": "person"}),
        valid=TemporalInterval.parse("2024-01-01T00:00:00Z", None),
    )
    third = memory_graph.put_node_version(
        Node("d", properties={"kind": "person"}),
        valid=TemporalInterval.parse("2024-01-01T00:00:00Z", None),
    )
    marker_key = memory_graph._temporal_visible_key(second.commit_id)
    marker = memory_graph.store.metadata.pop(marker_key)

    with memory_graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        assert view.commit_horizon == 1
        with memory_graph.read_view(
            valid_time="2024-06-01T00:00:00Z",
            system_time=third.system_time,
        ) as system_view:
            assert system_view.commit_horizon == 1
        memory_graph.store.put_metadata(marker_key, marker)
        assert view.get_node_as_of("c") is None
    with memory_graph.read_view(valid_time="2024-06-01T00:00:00Z") as newer:
        assert newer.commit_horizon == 3
        assert newer.get_node_as_of("c") is not None


def test_read_view_core_guarantees_run_without_optional_backends(memory_graph, tmp_path):
    first = _populate_history(memory_graph)
    original = first.versions[0]
    with memory_graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        encoded = view.provenance.to_json()
        snapshot = view.build_sampler_snapshot(tmp_path / "memory-snapshot")
        memory_graph.correct_node_version(
            Node("a", properties={"kind": "person", "name": "new"}),
            supersedes_version_id=original.version_id,
        )
        assert ReadViewProvenance.from_json(encoded) == view.provenance
        assert view.get_node_as_of("a").node.properties["name"] == "old"
        assert snapshot.get_node(memory_graph, 0).properties["name"] == "old"


def test_visibility_digest_detects_lower_commit_marker_changes(memory_graph):
    _populate_history(memory_graph)
    with memory_graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        memory_graph.store.put_metadata(memory_graph._temporal_visible_key(1), b"x" * 32)
        with pytest.raises((ProvenanceMismatchError, graphdb_module.TemporalCorruptionError)):
            view.verify_source()


def test_identity_initialization_is_serialized_for_explicit_handles(tmp_path):
    store = _MetadataStore()
    path = tmp_path / "explicit"
    path.mkdir()
    graphs = [GraphDB(store, graphdb_module.JSONSerializer()) for _ in range(2)]
    for graph in graphs:
        graph._store_path = path
        graph._backend_name = "leveldb"
    identities = []
    threads = [threading.Thread(target=lambda graph=graph: identities.append(graph.database_id)) for graph in graphs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(identities)) == 1
    assert len(identities) == 2


def test_identity_reconciliation_rejects_conflicting_expected_value(memory_graph):
    with pytest.raises(ValueError, match="does not match"):
        memory_graph._initialize_database_id(str(uuid.uuid4()))
