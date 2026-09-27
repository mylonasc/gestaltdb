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
from gestaltdb.sampling import HardNegativeConfig, SamplerEngine, SamplerSnapshot
from gestaltdb.temporal import TemporalContext, TemporalInterval
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
        with pytest.raises(graphdb_module.TemporalCorruptionError, match="checksum"):
            with memory_graph.read_view(
                valid_time="2024-06-01T00:00:00Z",
                system_time=third.system_time,
            ):
                pass
        memory_graph.store.put_metadata(marker_key, marker)
        assert view.get_node_as_of("c") is None
    with memory_graph.read_view(valid_time="2024-06-01T00:00:00Z") as newer:
        assert newer.commit_horizon == 3
        assert newer.get_node_as_of("c") is not None


def test_read_view_crosses_empty_abandoned_commit_reservation(memory_graph):
    _populate_history(memory_graph)
    abandoned = memory_graph.put_node_version(Node("abandoned"), valid=(0, None))
    memory_graph.store.metadata.pop(memory_graph._temporal_visible_key(abandoned.commit_id))
    memory_graph.store.metadata.pop(memory_graph._temporal_commit_key(abandoned.commit_id))
    later = memory_graph.put_node_version(Node("later"), valid=(0, None))

    with memory_graph.read_view(valid_time="2024-06-01T00:00:00Z") as view:
        assert view.commit_horizon == later.commit_id
        assert view.get_node_as_of("later") is not None

    view.provenance.verify_source(memory_graph)


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


def _build_runtime_temporal_snapshot(graph, path):
    graph.commit_versions([
        NodeVersionWrite.assertion(Node(node_id), (-20, None))
        for node_id in ["a", "b", "c", "d", "x"]
    ])
    for edge, valid in [
        (Edge("old", "a", "b", properties={"type": "R"}), (0, 10)),
        (Edge("new", "a", "c", properties={"type": "R"}), (10, 20)),
        (Edge("open", "a", "d", properties={"type": "S"}), (5, None)),
        (Edge("incoming", "x", "a", properties={"type": "R"}), (0, 20)),
        (Edge("forward", "b", "c", properties={"type": "R"}), (10, 30)),
        (Edge("backward", "b", "d", properties={"type": "R"}), (-5, 30)),
    ]:
        graph.put_edge_version(edge, valid=valid)
    return graph.build_sampler_snapshot(path, temporal=True, time_bucket="none")


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
    engine = SamplerEngine(current)
    a = current.external_node_ids.tolist().index("a")
    b = current.external_node_ids.tolist().index("b")
    assert engine.is_positive(a, relation, b, temporal=TemporalContext.as_of(19), policy="at_positive_time")
    assert not engine.is_positive(a, relation, b, temporal=TemporalContext.as_of(20), policy="at_positive_time")
    assert engine.is_positive(a, relation, b, temporal=TemporalContext.as_of(30), policy="at_positive_time")


def test_temporal_neighbor_sampling_composes_exact_filters(memory_graph, tmp_path):
    snapshot = _build_runtime_temporal_snapshot(memory_graph, tmp_path / "runtime")
    engine = SamplerEngine(snapshot, seed=7)
    node_ids = snapshot.external_node_ids.tolist()
    relations = snapshot.external_relation_ids.tolist()
    a = node_ids.index("a")
    b = node_ids.index("b")
    r = relations.index("R")
    s = relations.index("S")

    at_boundary = engine.sample_neighbors(
        [a], 10, direction="out", relations=[r], temporal=TemporalContext.as_of(10)
    )
    incoming = engine.sample_neighbors(
        [a], 10, direction="in", relations=[r], temporal=TemporalContext.as_of(10)
    )
    open_edge = engine.sample_neighbors(
        [a], 10, direction="out", relations=[s], temporal=TemporalContext.as_of(10)
    )
    pre_epoch = engine.sample_neighbors(
        [b], 10, direction="out", relations=[r], temporal=TemporalContext.as_of(-5)
    )
    empty = engine.sample_neighbors(
        [a], 10, direction="out", relations=[r], temporal=TemporalContext.as_of(100)
    )

    assert snapshot.external_edge_ids[at_boundary.edge_indices].tolist() == ["new"]
    assert snapshot.external_edge_ids[incoming.edge_indices].tolist() == ["incoming"]
    assert snapshot.external_edge_ids[open_edge.edge_indices].tolist() == ["open"]
    assert snapshot.external_edge_ids[pre_epoch.edge_indices].tolist() == ["backward"]
    assert empty.edge_indices.size == 0
    assert empty.edge_version_ids.size == 0
    assert at_boundary.edge_version_ids.tolist() == snapshot.edge_version_ids[at_boundary.edge_indices].tolist()
    assert at_boundary.valid_from_us.tolist() == [10]
    assert at_boundary.valid_to_us.tolist() == [20]
    assert at_boundary.valid_to_open.tolist() == [False]


def test_temporal_window_policies_filter_before_fanout(memory_graph, tmp_path):
    snapshot = _build_runtime_temporal_snapshot(memory_graph, tmp_path / "windows")
    a = snapshot.external_node_ids.tolist().index("a")
    relation = snapshot.external_relation_ids.tolist().index("R")
    window = TemporalContext.during(5, 20)

    overlap = SamplerEngine(snapshot, seed=3).sample_neighbors(
        [a], 10, direction="out", relations=[relation], temporal=window,
        window_policy="overlap",
    )
    contained = SamplerEngine(snapshot, seed=3).sample_neighbors(
        [a], 10, direction="out", relations=[relation], temporal=window,
        window_policy="contained",
    )
    contained_open = SamplerEngine(snapshot, seed=3).sample_neighbors(
        [a], 10, direction="out", relations=[snapshot.external_relation_ids.tolist().index("S")],
        temporal=window, window_policy="contained",
    )
    sampled = SamplerEngine(snapshot, seed=3).sample_neighbors(
        [a], 1, direction="out", relations=[relation],
        temporal=TemporalContext.as_of(10),
    )

    assert set(snapshot.external_edge_ids[overlap.edge_indices].tolist()) == {"old", "new"}
    assert snapshot.external_edge_ids[contained.edge_indices].tolist() == ["new"]
    assert contained_open.edge_indices.size == 0
    assert snapshot.external_edge_ids[sampled.edge_indices].tolist() == ["new"]


@pytest.mark.parametrize(
    ("causal_policy", "present", "absent"),
    [("nondecreasing", "forward", "backward"), ("nonincreasing", "backward", "forward")],
)
def test_temporal_multihop_enforces_causal_path_starts(
    memory_graph, tmp_path, causal_policy, present, absent
):
    snapshot = _build_runtime_temporal_snapshot(memory_graph, tmp_path / causal_policy)
    a = snapshot.external_node_ids.tolist().index("a")
    relation = snapshot.external_relation_ids.tolist().index("R")

    batch = SamplerEngine(snapshot, seed=5).sample_multihop(
        [a], [10, 10], direction="out", relations=[relation],
        temporal=TemporalContext.during(-20, 40), causal_policy=causal_policy,
    )
    sampled_ids = set(snapshot.external_edge_ids[batch.edge_ids_global].tolist())

    assert present in sampled_ids
    assert absent not in sampled_ids
    assert batch.edge_version_ids.tolist() == snapshot.edge_version_ids[batch.edge_ids_global].tolist()
    assert batch.to_numpy()["valid_from_us"].shape == batch.edge_ids_global.shape


def test_temporal_multihop_propagates_converged_causal_states(memory_graph, tmp_path):
    memory_graph.commit_versions([
        NodeVersionWrite.assertion(Node(node_id), (-10, None))
        for node_id in ["a", "b", "c", "d"]
    ])
    for edge_id, source, target, start in [
        ("a-b-late", "a", "b", 5),
        ("a-c", "a", "c", 1),
        ("c-b", "c", "b", 2),
        ("b-d", "b", "d", 3),
    ]:
        memory_graph.put_edge_version(
            Edge(edge_id, source, target, properties={"type": "R"}),
            valid=(start, 100),
        )
    snapshot = memory_graph.build_sampler_snapshot(
        tmp_path / "causal-convergence", temporal=True, time_bucket="none"
    )
    a = snapshot.external_node_ids.tolist().index("a")

    batch = SamplerEngine(snapshot, seed=1).sample_multihop(
        [a], [10, 10, 10], direction="out",
        temporal=TemporalContext.during(0, 100),
        causal_policy="nondecreasing",
    )

    assert set(snapshot.external_edge_ids[batch.edge_ids_global].tolist()) == {
        "a-b-late", "a-c", "c-b", "b-d",
    }


def test_temporal_subgraph_filters_seed_edges_and_preserves_alignment(memory_graph, tmp_path):
    snapshot = _build_runtime_temporal_snapshot(memory_graph, tmp_path / "subgraph")
    old = snapshot.external_edge_ids.tolist().index("old")
    new = snapshot.external_edge_ids.tolist().index("new")
    engine = SamplerEngine(snapshot, seed=9)

    excluded = engine.sample_subgraph([old], [], temporal=TemporalContext.as_of(10))
    included = engine.sample_subgraph([new], [0], temporal=TemporalContext.as_of(10))

    assert excluded.n_edges == 0
    assert excluded.positives.shape == (0, 3)
    assert included.edge_ids_global.tolist() == [new]
    assert included.edge_version_ids.tolist() == [snapshot.edge_version_ids[new]]
    assert included.valid_from_us.tolist() == [10]


def test_temporal_sampling_duplicate_versions_and_ram_memmap_parity(memory_graph, tmp_path):
    _, corrected, _ = _populate_temporal_snapshot_history(memory_graph)
    snapshot = memory_graph.build_sampler_snapshot(tmp_path / "history", temporal=True, time_bucket="day")
    node = snapshot.external_node_ids.tolist().index("a")
    relation = snapshot.external_relation_ids.tolist().index("KNOWS")
    context = TemporalContext.during(-20, 40)
    ram = SamplerEngine.load(snapshot.path, mode="ram", seed=17)
    mapped = SamplerEngine.load(snapshot.path, mode="memmap", seed=17)

    ram_sample = ram.sample_neighbors([node], 1, relations=[relation], temporal=context)
    mapped_sample = mapped.sample_neighbors([node], 1, relations=[relation], temporal=context)
    all_versions = SamplerEngine(snapshot).sample_neighbors(
        [node], 10, relations=[relation], temporal=context
    )

    assert np.array_equal(ram_sample.edge_indices, mapped_sample.edge_indices)
    assert np.array_equal(ram_sample.edge_version_ids, mapped_sample.edge_version_ids)
    assert all_versions.edge_version_ids.tolist() == [corrected.version_id, corrected.version_id]
    assert all_versions.valid_from_us.tolist() == [-10, 30]


def _build_temporal_negative_snapshot(graph, path):
    graph.commit_versions([
        NodeVersionWrite.assertion(Node(node_id), valid)
        for node_id, valid in [
            ("a", (0, None)), ("b", (0, None)), ("c", (0, None)),
            ("future", (100, None)),
        ]
    ])
    graph.put_edge_version(
        Edge("seed", "a", "b", properties={"type": "R"}), valid=(0, 50)
    )
    graph.put_edge_version(
        Edge("future-positive", "a", "c", properties={"type": "R"}), valid=(20, 50)
    )
    return graph.build_sampler_snapshot(path, temporal=True, time_bucket="none")


def test_temporal_positive_membership_uses_half_open_history(memory_graph, tmp_path):
    snapshot = _build_temporal_negative_snapshot(memory_graph, tmp_path / "membership")
    engine = SamplerEngine(snapshot)
    nodes = snapshot.external_node_ids.tolist()
    relation = snapshot.external_relation_ids.tolist().index("R")
    a, c = nodes.index("a"), nodes.index("c")

    assert engine.is_positive(a, relation, c)
    assert not engine.is_positive(
        a, relation, c, temporal=TemporalContext.as_of(19), policy="at_positive_time"
    )
    assert engine.is_positive(
        a, relation, c, temporal=TemporalContext.as_of(20), policy="at_positive_time"
    )
    assert not engine.is_positive(
        a, relation, c, temporal=TemporalContext.as_of(50), policy="at_positive_time"
    )
    assert engine.is_positive(
        a, relation, c, temporal=TemporalContext.during(10, 21), policy="window"
    )
    assert not engine.is_positive(
        a, relation, c, temporal=TemporalContext.during(10, 20), policy="window"
    )
    with pytest.raises(ValueError, match="temporal context"):
        engine.is_positive(a, relation, c, policy="at_positive_time")
    with pytest.raises(ValueError, match="policy"):
        engine.is_positive(a, relation, c, policy="eventually")


def test_window_hard_negatives_accept_query_context_without_example_times(memory_graph, tmp_path):
    snapshot = _build_temporal_negative_snapshot(memory_graph, tmp_path / "window-negatives")
    nodes = snapshot.external_node_ids.tolist()
    relation = snapshot.external_relation_ids.tolist().index("R")
    query = TemporalContext.during(10, 21)

    negatives = SamplerEngine(snapshot, seed=5).sample_hard_negatives(
        [[nodes.index("a"), relation, nodes.index("b")]],
        config=HardNegativeConfig(temporal_positive_policy="window"),
        temporal=query,
    )

    assert all(
        not SamplerEngine(snapshot).is_positive(*triple, temporal=query, policy="window")
        for triple in negatives.reshape(-1, 3)
    )


def test_temporal_hard_negatives_separate_future_positive_and_candidate_policies(memory_graph, tmp_path):
    snapshot = _build_temporal_negative_snapshot(memory_graph, tmp_path / "negatives")
    nodes = snapshot.external_node_ids.tolist()
    relation = snapshot.external_relation_ids.tolist().index("R")
    positive = np.array([[nodes.index("a"), relation, nodes.index("b")]], dtype=np.int64)
    config = HardNegativeConfig(
        negatives_per_positive=3,
        head_probability=0.0,
        temporal_positive_policy="at_positive_time",
        exhaustion_policy="repeat",
    )

    negatives, diagnostics = SamplerEngine(snapshot, seed=7).sample_hard_negatives(
        positive, config=config, positive_times_us=[10], return_diagnostics=True
    )

    assert all(nodes.index("c") in (int(triple[0]), int(triple[2])) for triple in negatives[0])
    assert nodes.index("future") not in negatives
    assert diagnostics["repeated"] == 1
    assert diagnostics["rejected_unavailable"] > 0
    any_time = SamplerEngine(snapshot, seed=7).sample_hard_negatives(
        positive,
        config=HardNegativeConfig(
            head_probability=0.0, temporal_positive_policy="any_time"
        ),
        positive_times_us=[10],
    )
    assert all(not SamplerEngine(snapshot).is_positive(*triple) for triple in any_time.reshape(-1, 3))


def test_temporal_hard_negatives_reject_ambiguous_candidate_time(memory_graph, tmp_path):
    snapshot = _build_temporal_negative_snapshot(memory_graph, tmp_path / "ambiguous-negatives")
    nodes = snapshot.external_node_ids.tolist()
    relation = snapshot.external_relation_ids.tolist().index("R")
    positive = [[nodes.index("a"), relation, nodes.index("b")]]

    with pytest.raises(ValueError, match="positive_times_us or a temporal context"):
        SamplerEngine(snapshot, seed=3).sample_hard_negatives(positive)

    negatives = SamplerEngine(snapshot, seed=3).sample_hard_negatives(
        positive, positive_times_us=[10]
    )

    assert nodes.index("future") not in negatives


def test_temporal_snapshot_does_not_resurrect_fully_retracted_node(memory_graph, tmp_path):
    memory_graph.commit_versions([
        NodeVersionWrite.assertion(Node("a"), (0, None)),
        NodeVersionWrite.assertion(Node("gone"), (0, 10)),
        EdgeVersionWrite.assertion(
            Edge("a-gone", "a", "gone", properties={"type": "R"}), (0, None)
        ),
    ])
    memory_graph.commit_versions([
        NodeVersionWrite.retraction("gone", valid=(0, 10)),
    ])
    snapshot = memory_graph.build_sampler_snapshot(
        tmp_path / "retracted-node", temporal=True, time_bucket="none"
    )
    gone = snapshot.external_node_ids.tolist().index("gone")

    assert snapshot.node_history_indptr[gone] == snapshot.node_history_indptr[gone + 1]
    assert not SamplerEngine(snapshot)._node_available(gone, TemporalContext.as_of(5))


def test_temporal_negative_batch_returns_aligned_times_and_diagnostics(memory_graph, tmp_path):
    snapshot = _build_temporal_negative_snapshot(memory_graph, tmp_path / "batch-negatives")
    seed = snapshot.external_edge_ids.tolist().index("seed")
    config = HardNegativeConfig(
        negatives_per_positive=2,
        head_probability=0.0,
        temporal_positive_policy="at_positive_time",
        exhaustion_policy="repeat",
    )

    batch = SamplerEngine(snapshot, seed=11).sample_subgraph(
        [seed], [], negative_config=config, temporal=TemporalContext.as_of(10)
    )

    assert batch.positive_time_us.tolist() == [10]
    assert batch.negative_time_us.tolist() == [[10, 10]]
    assert batch.to_numpy()["negative_time_us"].shape == (1, 2)
    assert batch.negative_diagnostics["accepted"] == 2


def test_temporal_negative_sampling_ram_memmap_parity(memory_graph, tmp_path):
    snapshot = _build_temporal_negative_snapshot(memory_graph, tmp_path / "negative-parity")
    nodes = snapshot.external_node_ids.tolist()
    relation = snapshot.external_relation_ids.tolist().index("R")
    positive = [[nodes.index("a"), relation, nodes.index("b")]]
    config = HardNegativeConfig(
        head_probability=0.0, temporal_positive_policy="at_positive_time"
    )

    ram = SamplerEngine.load(snapshot.path, mode="ram", seed=19).sample_hard_negatives(
        positive, config=config, positive_times_us=[10]
    )
    mapped = SamplerEngine.load(snapshot.path, mode="memmap", seed=19).sample_hard_negatives(
        positive, config=config, positive_times_us=[10]
    )

    assert np.array_equal(ram, mapped)


def test_temporal_sampling_validates_context_and_policies(memory_graph, tmp_path):
    snapshot = _build_runtime_temporal_snapshot(memory_graph, tmp_path / "validation")
    engine = SamplerEngine(snapshot)

    with pytest.raises(TypeError, match="TemporalContext"):
        engine.sample_neighbors([0], 1, temporal=10)
    with pytest.raises(ValueError, match="window_policy"):
        engine.sample_multihop([0], [], window_policy="covers")
    with pytest.raises(ValueError, match="causal_policy"):
        engine.sample_subgraph([], [], causal_policy="increasing")


def test_temporal_sampling_rejects_non_temporal_snapshot(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "static", external_node_ids=["a", "b"],
        external_edge_ids=["ab"], external_relation_ids=["R"],
        src_int=[0], dst_int=[1], rel_int=[0],
    )

    with pytest.raises(ValueError, match="temporal snapshot"):
        SamplerEngine(snapshot).sample_neighbors([0], 1, temporal=TemporalContext.as_of(0))


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


def test_pre_tkg09_temporal_v2_snapshot_derives_compatibility_indexes(memory_graph, tmp_path):
    snapshot = _build_runtime_temporal_snapshot(memory_graph, tmp_path / "legacy-v2")
    metadata_path = snapshot.path / "metadata.json"
    completion_path = snapshot.path / "completion.json"
    metadata = json.loads(metadata_path.read_text())
    history_arrays = {
        "positive_history_triples", "positive_history_indptr",
        "positive_history_edge_indices", "node_history_indptr",
        "node_valid_from_us", "node_valid_to_us", "node_valid_to_open",
    }
    metadata.pop("temporal_history_indexes")
    for name in history_arrays:
        metadata["artifacts"].pop(name)
        (snapshot.path / f"{name}.npy").unlink()
    metadata_bytes = json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    metadata_path.write_bytes(metadata_bytes)
    completion = json.loads(completion_path.read_text())
    completion["metadata_sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    catalog_bytes = json.dumps(
        metadata["artifacts"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    completion["artifact_catalog_sha256"] = hashlib.sha256(catalog_bytes).hexdigest()
    completion_path.write_text(json.dumps(completion, sort_keys=True, separators=(",", ":")))

    loaded = SamplerSnapshot.load(snapshot.path, mmap=True)

    assert loaded.positive_history_triples.shape[1] == 3
    assert loaded.node_history_indptr.shape == (loaded.num_nodes + 1,)


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


def test_temporal_snapshot_rejects_corrupt_positive_history_index(memory_graph, tmp_path):
    snapshot = _build_temporal_negative_snapshot(memory_graph, tmp_path / "positive-corruption")
    index_path = snapshot.path / "positive_history_edge_indices.npy"
    indices = np.load(index_path)
    indices[[0, -1]] = indices[[-1, 0]]
    np.save(index_path, indices, allow_pickle=False)
    _resign_snapshot(snapshot.path, "positive_history_edge_indices")

    with pytest.raises(ValueError, match="positive-history index is inconsistent"):
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
