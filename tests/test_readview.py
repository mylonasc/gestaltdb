"""Consistent temporal read views and source provenance."""

import importlib.util
import hashlib
import json
import shutil
import threading
import uuid

import numpy as np
import pytest

import gestaltdb.graphdb as graphdb_module
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.readview import ProvenanceMismatchError, ReadViewProvenance
from gestaltdb.sampling import SamplerSnapshot
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


def _populate_temporal_snapshot_history(graph):
    nodes = graph.commit_versions([
        NodeVersionWrite.assertion(Node("a", properties={"kind": "person"}), (-10, None), version_id="00000000-0000-0000-0000-000000000001"),
        NodeVersionWrite.assertion(Node("b", properties={"kind": "person"}), (-10, None), version_id="00000000-0000-0000-0000-000000000002"),
        EdgeVersionWrite.assertion(Edge("ab", "a", "b", properties={"type": "KNOWS"}), (-10, None), version_id="00000000-0000-0000-0000-000000000003"),
    ])
    asserted = nodes.versions[-1]
    corrected = graph.correct_edge_version(
        Edge("ab", "a", "b", properties={"type": "KNOWS", "revision": 2}),
        supersedes_version_id=asserted.version_id,
        version_id="00000000-0000-0000-0000-000000000004",
    )
    retracted = graph.retract_edge_version(
        "ab", valid=(20, 30), supersedes_version_id=corrected.version_id,
        version_id="00000000-0000-0000-0000-000000000005",
    )
    return nodes, corrected, retracted


def test_temporal_snapshot_materializes_horizon_intervals_and_time_indexes(memory_graph, tmp_path):
    first, corrected, _ = _populate_temporal_snapshot_history(memory_graph)
    early = memory_graph.build_sampler_snapshot(
        tmp_path / "early", temporal=True, system_time=first.system_time, time_bucket="none"
    )
    current = memory_graph.build_sampler_snapshot(
        tmp_path / "current", temporal=True, time_bucket="none"
    )

    assert early.edge_version_ids.tolist() == [first.versions[-1].version_id]
    assert early.valid_from_us.tolist() == [-10]
    assert early.valid_to_open.tolist() == [True]
    assert current.edge_version_ids.tolist() == [corrected.version_id, corrected.version_id]
    assert current.valid_from_us.tolist() == [-10, 30]
    assert current.valid_to_us.tolist() == [20, 0]
    assert current.valid_to_open.tolist() == [False, True]
    assert current.edge_commit_ids.tolist() == [corrected.commit_id, corrected.commit_id]
    relation = current.external_relation_ids.tolist().index("KNOWS")
    node = current.external_node_ids.tolist().index("a")
    candidates = current.temporal_candidates(node, relation, 15)
    exact = {
        index for index in range(current.num_edges)
        if current.valid_from_us[index] <= 15
        and (current.valid_to_open[index] or 15 < current.valid_to_us[index])
    }
    assert exact <= set(candidates.tolist())
    assert candidates.tolist() == [0]


@pytest.mark.parametrize("reserved", ["source_provenance", "temporal_encoding"])
def test_temporal_snapshot_rejects_authoritative_metadata_overrides(memory_graph, tmp_path, reserved):
    _populate_temporal_snapshot_history(memory_graph)
    output = tmp_path / reserved

    with pytest.raises(ValueError, match="cannot override"):
        memory_graph.build_sampler_snapshot(
            output,
            temporal=True,
            **{reserved: {}},
        )

    assert not output.exists()


def test_temporal_snapshot_hydrates_exact_edge_version(memory_graph, tmp_path):
    memory_graph.commit_versions([
        NodeVersionWrite.assertion(Node("a"), (0, 100)),
        NodeVersionWrite.assertion(Node("b"), (0, 100)),
        NodeVersionWrite.assertion(Node("c"), (0, 100)),
    ])
    asserted = memory_graph.put_edge_version(
        Edge("edge", "a", "b", properties={"type": "OLD"}), valid=(0, 100)
    )
    corrected = memory_graph.correct_edge_version(
        Edge("edge", "a", "c", properties={"type": "NEW"}),
        supersedes_version_id=asserted.version_id,
        valid=(20, 40),
    )
    snapshot = memory_graph.build_sampler_snapshot(tmp_path / "snapshot", temporal=True)
    corrected_row = snapshot.edge_version_ids.tolist().index(corrected.version_id)

    hydrated = snapshot.get_edge(memory_graph, corrected_row)

    assert hydrated.target == "c"
    assert hydrated.properties["type"] == "NEW"


def test_temporal_snapshot_ram_memmap_parity_and_deterministic_rebuild(memory_graph, tmp_path):
    _populate_temporal_snapshot_history(memory_graph)
    first = memory_graph.build_sampler_snapshot(tmp_path / "first", temporal=True, time_bucket="day")
    second = memory_graph.build_sampler_snapshot(tmp_path / "second", temporal=True, time_bucket="day")
    mapped = SamplerSnapshot.load(first.path, mmap=True)

    assert first.metadata["artifacts"] == second.metadata["artifacts"]
    for name in first.metadata["artifacts"]:
        assert np.array_equal(np.load(first.path / f"{name}.npy"), np.load(second.path / f"{name}.npy"))
    assert isinstance(mapped.valid_from_us, np.memmap)
    assert np.array_equal(mapped.edge_version_ids, first.edge_version_ids)
    assert np.array_equal(mapped.temporal_out.edge_indices, first.temporal_out.edge_indices)


def test_empty_temporal_snapshot_is_complete_and_loadable(memory_graph, tmp_path):
    snapshot = memory_graph.build_sampler_snapshot(tmp_path / "empty", temporal=True)

    assert snapshot.temporal
    assert snapshot.num_nodes == snapshot.num_edges == 0
    assert snapshot.temporal_out.indptr.tolist() == [0, 0]
    assert (snapshot.path / "completion.json").is_file()


def test_failed_snapshot_validation_does_not_publish_output(tmp_path):
    output = tmp_path / "invalid"

    with pytest.raises(ValueError, match="relation type arrays"):
        SamplerSnapshot.from_arrays(
            output,
            external_node_ids=["a", "b"],
            node_type_ids=[-1, -1],
            external_edge_ids=["ab"],
            external_relation_ids=["R"],
            src_int=[0],
            dst_int=[1],
            rel_int=[0],
            relation_src_type_ids=[],
            relation_dst_type_ids=[-1],
        )

    assert not output.exists()


def _resign_snapshot(snapshot_path, artifact_name):
    metadata_path = snapshot_path / "metadata.json"
    completion_path = snapshot_path / "completion.json"
    metadata = json.loads(metadata_path.read_text())
    artifact_path = snapshot_path / f"{artifact_name}.npy"
    metadata["artifacts"][artifact_name]["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    metadata_bytes = json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    metadata_path.write_bytes(metadata_bytes)
    catalog_bytes = json.dumps(
        metadata["artifacts"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    completion = json.loads(completion_path.read_text())
    completion["metadata_sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    completion["artifact_catalog_sha256"] = hashlib.sha256(catalog_bytes).hexdigest()
    completion_path.write_text(json.dumps(completion, sort_keys=True, separators=(",", ":")))


def test_temporal_snapshot_rejects_candidate_index_with_wrong_key_membership(memory_graph, tmp_path):
    _populate_temporal_snapshot_history(memory_graph)
    memory_graph.put_edge_version(
        Edge("ba", "b", "a", properties={"type": "KNOWS"}), valid=(-10, None)
    )
    snapshot = memory_graph.build_sampler_snapshot(tmp_path / "snapshot", temporal=True)
    index_path = snapshot.path / "temporal_out_edge_indices.npy"
    indices = np.load(index_path)
    indices[[0, -1]] = indices[[-1, 0]]
    np.save(index_path, indices, allow_pickle=False)
    _resign_snapshot(snapshot.path, "temporal_out_edge_indices")

    with pytest.raises(ValueError, match="temporal_out CSR membership"):
        SamplerSnapshot.load(snapshot.path)


@pytest.mark.parametrize("corruption", ["completion", "checksum", "provenance"])
def test_v2_snapshot_rejects_completion_checksum_and_provenance_corruption(memory_graph, tmp_path, corruption):
    _populate_temporal_snapshot_history(memory_graph)
    snapshot = memory_graph.build_sampler_snapshot(tmp_path / "snapshot", temporal=True)
    if corruption == "completion":
        (snapshot.path / "completion.json").unlink()
        expected = "incomplete"
    elif corruption == "checksum":
        with (snapshot.path / "src_int.npy").open("ab") as handle:
            handle.write(b"corrupt")
        expected = "checksum mismatch"
    else:
        metadata = json.loads((snapshot.path / "metadata.json").read_text())
        metadata["source_provenance"]["commit_horizon"] = 0
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        (snapshot.path / "metadata.json").write_bytes(encoded)
        completion = json.loads((snapshot.path / "completion.json").read_text())
        completion["metadata_sha256"] = hashlib.sha256(encoded).hexdigest()
        (snapshot.path / "completion.json").write_text(json.dumps(completion, sort_keys=True, separators=(",", ":")))
        expected = "token mismatch"
    with pytest.raises(ValueError, match=expected):
        SamplerSnapshot.load(snapshot.path)


def test_legacy_v1_snapshot_remains_loadable(memory_graph, tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "v2",
        external_node_ids=["a", "b"],
        external_edge_ids=["ab"],
        external_relation_ids=["KNOWS"],
        src_int=[0],
        dst_int=[1],
        rel_int=[0],
    )
    legacy = tmp_path / "v1"
    shutil.copytree(snapshot.path, legacy)
    metadata = json.loads((legacy / "metadata.json").read_text())
    metadata["format_version"] = 1
    metadata["temporal"] = True
    metadata.pop("artifacts")
    (legacy / "metadata.json").write_text(json.dumps(metadata))
    (legacy / "completion.json").unlink()

    loaded = SamplerSnapshot.load(legacy)

    assert loaded.metadata["format_version"] == 1
    assert not loaded.temporal
