import random

import pytest

from gestaltdb.graphdb import Edge, Node
from gestaltdb.sampling import AsyncBatchFeeder, HardNegativeConfig, NeighborSamplingSpec, SamplerEngine, SamplerSnapshot, SamplingHop, SamplingPattern
from gestaltdb.sampling import as_sampling_pattern

from .conftest import blocked_import, populate_typed_graph


class _FakeSamplerStore:
    def __init__(self, nodes, edges):
        self.nodes = {node.get_id_bytes: node for node in nodes}
        self.edges = {edge.get_id_bytes: edge for edge in edges}

    def get_node_keys_generator(self):
        return iter(self.nodes.keys())

    def get_edge_keys_generator(self):
        return iter(self.edges.keys())


class _FakeSamplerGraph:
    def __init__(self):
        nodes = [
            Node(node_id="drug-1", properties={"kind": "drug"}),
            Node(node_id="drug-2", properties={"kind": "drug"}),
            Node(node_id="protein-1", properties={"kind": "protein"}),
            Node(node_id="protein-2", properties={"kind": "protein"}),
            Node(node_id="disease-1", properties={"kind": "disease"}),
        ]
        edges = [
            Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"}),
            Edge(edge_id="d1-p2", source="drug-1", target="protein-2", properties={"type": "drug-to-protein"}),
            Edge(edge_id="p1-dis1", source="protein-1", target="disease-1", properties={"type": "protein-to-disease"}),
        ]
        self.store = _FakeSamplerStore(nodes, edges)

    def get_node(self, node_key):
        return self.store.nodes.get(node_key)

    def get_edge(self, edge_key):
        return self.store.edges.get(edge_key)

    def key_to_string(self, key):
        return key.decode("utf-8") if isinstance(key, bytes) else key

    def build_sampler_snapshot(self, output_path, **kwargs):
        from gestaltdb.sampling import SamplerSnapshot

        return SamplerSnapshot.build(self, output_path, **kwargs)


def test_sampling_hop_round_trips_dict_config():
    hop = SamplingHop.from_dict({"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2})

    assert hop.edge_type == "drug-to-protein"
    assert hop.direction == "out"
    assert hop.sample_size == 2
    assert hop.to_dict() == {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}


def test_sampling_pattern_normalizes_dicts_and_hops():
    pattern = SamplingPattern([
        SamplingHop("drug-to-protein", sample_size=2),
        {"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1},
    ])

    assert len(pattern) == 2
    assert pattern.hops[0].edge_type == "drug-to-protein"
    assert pattern.hops[1].sample_size == 1


def test_sampling_pattern_from_dicts_and_to_dicts_round_trip():
    pattern = SamplingPattern.from_dicts([
        {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2},
    ])

    assert pattern.to_dicts() == [{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}]
    assert as_sampling_pattern(pattern) is pattern


def test_sampling_hop_validates_values():
    with pytest.raises(ValueError, match="direction"):
        SamplingHop("drug-to-protein", direction="sideways")
    with pytest.raises(ValueError, match="sample_size"):
        SamplingHop("drug-to-protein", sample_size=0)


def test_sampler_snapshot_and_engine_without_optional_backend(tmp_path):
    graph = _FakeSamplerGraph()

    snapshot = graph.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine.load(snapshot.path, seed=7)
    node_id = snapshot.external_node_ids.tolist().index("drug-1")
    rel_id = snapshot.external_relation_ids.tolist().index("drug-to-protein")

    sample = engine.sample_neighbors([node_id], fanout=10, direction="out", relations=[rel_id])
    neighbor_names = set(snapshot.external_node_ids[sample.neighbor_nodes].tolist())
    seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")
    batch = engine.sample_subgraph([seed_edge], fanouts=[2], direction="any")

    assert snapshot.num_nodes == 5
    assert snapshot.num_edges == 3
    assert neighbor_names == {"protein-1", "protein-2"}
    assert engine.is_positive(node_id, rel_id, snapshot.external_node_ids.tolist().index("protein-1"))
    assert batch.positives.shape == (1, 3)
    assert batch.n_edges >= 1


def test_sampler_snapshot_from_edge_arrays_derives_type_constraints(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "array_snapshot",
        external_node_ids=["drug-1", "drug-2", "protein-1"],
        external_edge_ids=["d1-p1", "d2-p1"],
        external_relation_ids=["drug-to-protein"],
        src_int=[0, 1],
        dst_int=[2, 2],
        rel_int=[0, 0],
        node_type_values=["drug", "drug", "protein"],
        edge_src_type_values=["drug", "drug"],
        edge_dst_type_values=["protein", "protein"],
    )

    assert snapshot.num_nodes == 3
    assert snapshot.num_edges == 2
    assert snapshot.relation_src_type_ids.tolist() == [snapshot.node_type_ids[0]]
    assert snapshot.relation_dst_type_ids.tolist() == [snapshot.node_type_ids[2]]
    assert snapshot.metadata["node_type_values"] == {"0": "drug", "1": "protein"}


def test_sampler_snapshot_persists_edge_weights(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "weighted_snapshot",
        external_node_ids=["source", "left", "right"],
        external_edge_ids=["left-edge", "right-edge"],
        external_relation_ids=["links"],
        src_int=[0, 0],
        dst_int=[1, 2],
        rel_int=[0, 0],
        edge_weights=[0.0, 4.0],
    )

    loaded = SamplerSnapshot.load(snapshot.path, mmap=True)

    assert loaded.edge_weights.tolist() == [0.0, 4.0]

    (snapshot.path / "edge_weights.npy").unlink()
    legacy = SamplerSnapshot.load(snapshot.path)
    assert legacy.edge_weights.tolist() == [1.0, 1.0]


def test_sampler_engine_weighted_neighbors_use_snapshot_weights(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "weighted_snapshot",
        external_node_ids=["source", "left", "right"],
        external_edge_ids=["left-edge", "right-edge"],
        external_relation_ids=["links"],
        src_int=[0, 0],
        dst_int=[1, 2],
        rel_int=[0, 0],
        edge_weights=[0.0, 1.0],
    )
    engine = SamplerEngine(snapshot, seed=5)

    sampled = engine.sample_neighbors([0], fanout=1, strategy="weighted")

    assert sampled.edge_indices.tolist() == [1]
    assert sampled.neighbor_nodes.tolist() == [2]

    snapshot.edge_weights[:] = 0
    with pytest.raises(ValueError, match="positive value"):
        SamplerEngine(snapshot, seed=5).sample_neighbors([0], fanout=10, strategy="weighted")


def test_sampler_engine_normalizes_large_weights_without_overflow(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "weighted_snapshot",
        external_node_ids=["source", "left", "right"],
        external_edge_ids=["left-edge", "right-edge"],
        external_relation_ids=["links"],
        src_int=[0, 0],
        dst_int=[1, 2],
        rel_int=[0, 0],
        edge_weights=[1e308, 1e308],
    )

    sampled = SamplerEngine(snapshot, seed=5).sample_neighbors([0], fanout=1, strategy="weighted")

    assert sampled.edge_indices[0] in {0, 1}


def test_sampler_engine_samples_filtered_node_and_edge_seeds(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "seed_snapshot",
        external_node_ids=["drug-1", "drug-2", "protein-1", "disease-1"],
        external_edge_ids=["targets-1", "targets-2", "associated-1"],
        external_relation_ids=["targets", "associated"],
        src_int=[0, 1, 2],
        dst_int=[2, 2, 3],
        rel_int=[0, 0, 1],
        node_type_ids=[0, 0, 1, 2],
    )
    engine = SamplerEngine(snapshot, seed=7)

    nodes = engine.sample_nodes(10, node_types=[0])
    edges = engine.sample_edges(10, relations=[1])

    assert nodes.tolist() == [0, 1]
    assert edges.tolist() == [2]


def test_sampler_engine_preserves_heterogeneous_layer_blocks(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "layer_snapshot",
        external_node_ids=["drug", "protein", "disease"],
        external_edge_ids=["targets", "associated"],
        external_relation_ids=["targets", "associated"],
        src_int=[0, 1],
        dst_int=[1, 2],
        rel_int=[0, 1],
    )
    engine = SamplerEngine(snapshot, seed=11)

    batch = engine.sample_layers(
        [0, 0],
        [
            NeighborSamplingSpec(fanout=2, relations=[0]),
            NeighborSamplingSpec(fanout=2, relations=[1]),
        ],
    )

    assert batch.seed_nodes.tolist() == [0, 0]
    assert len(batch.layers) == 2
    assert batch.layers[0].input_nodes.tolist() == [0, 0]
    assert batch.layers[0].neighbor_nodes.tolist() == [1, 1]
    assert batch.layers[1].input_nodes.tolist() == [1]
    assert batch.layers[1].neighbor_nodes.tolist() == [2]


def test_sampler_engine_rejects_invalid_compact_ids(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=["source", "target"],
        external_edge_ids=["edge"],
        external_relation_ids=["rel"],
        src_int=[0],
        dst_int=[1],
        rel_int=[0],
    )
    engine = SamplerEngine(snapshot)

    with pytest.raises(ValueError, match="nodes"):
        engine.sample_neighbors([-1], fanout=1)
    with pytest.raises(ValueError, match="integer IDs"):
        engine.sample_neighbors([0.5], fanout=1)
    with pytest.raises(ValueError, match="seed_edges"):
        engine.sample_subgraph([1], fanouts=[1])
    with pytest.raises(ValueError, match="count must be an integer"):
        engine.sample_nodes(1.5)
    with pytest.raises(ValueError, match="integer IDs"):
        NeighborSamplingSpec(fanout=1, relations=[0.5])
    with pytest.raises(ValueError, match="integer IDs"):
        engine.sample_hard_negatives([[0.5, 0, 1]])


def test_sampler_snapshot_rejects_fractional_compact_ids(tmp_path):
    with pytest.raises(ValueError, match="src_int must contain integer IDs"):
        SamplerSnapshot.from_edge_arrays(
            tmp_path / "snapshot",
            external_node_ids=["source", "target"],
            external_edge_ids=["edge"],
            external_relation_ids=["rel"],
            src_int=[0.5],
            dst_int=[1],
            rel_int=[0],
        )


def test_sampler_engine_incident_self_loop_is_one_candidate(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "self_loop_snapshot",
        external_node_ids=["node"],
        external_edge_ids=["loop"],
        external_relation_ids=["rel"],
        src_int=[0],
        dst_int=[0],
        rel_int=[0],
    )

    sampled = SamplerEngine(snapshot, seed=3).sample_neighbors([0], fanout=2, direction="any")

    assert sampled.edge_indices.tolist() == [0]


def test_sampler_snapshot_from_edge_arrays_records_source_metadata(tmp_path):
    db_path = tmp_path / "db"
    db_path.mkdir()

    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "artifacts" / "array_snapshot",
        external_node_ids=["drug-1", "protein-1"],
        external_edge_ids=["d1-p1"],
        external_relation_ids=["binds"],
        src_int=[0],
        dst_int=[1],
        rel_int=[0],
        source_db={"path": db_path, "path_type": "relative_to_snapshot", "backend": "pyrex", "serializer": "json"},
        source_artifacts={"edge_arrays": tmp_path / "edge_arrays.npz"},
    )

    loaded = SamplerSnapshot.load(snapshot.path)

    assert loaded.metadata["source_db"]["path_type"] == "relative_to_snapshot"
    assert loaded.metadata["source_db"]["backend"] == "pyrex"
    assert loaded.source_graph_exists()
    assert loaded.metadata["source_artifacts"]["edge_arrays"] == "../../edge_arrays.npz"


def test_sampler_snapshot_without_source_metadata_still_loads(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "array_snapshot",
        external_node_ids=["n1", "n2"],
        external_edge_ids=["e1"],
        external_relation_ids=["rel"],
        src_int=[0],
        dst_int=[1],
        rel_int=[0],
    )

    loaded = SamplerSnapshot.load(snapshot.path)

    assert "source_db" not in loaded.metadata
    assert not loaded.source_graph_exists()
    with pytest.raises(ValueError, match="no source_db metadata"):
        loaded.open_source_graph()


def test_sampler_snapshot_inspection_helpers_hydrate_external_ids(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "array_snapshot",
        external_node_ids=["drug-1", "protein-1"],
        external_edge_ids=["d1-p1"],
        external_relation_ids=["binds"],
        src_int=[0],
        dst_int=[1],
        rel_int=[0],
    )
    engine = SamplerEngine(snapshot, seed=1)
    batch = engine.sample_subgraph([0], fanouts=[0])

    class HydrationGraph:
        def get_node(self, node_id):
            return Node(node_id=node_id.decode("utf-8"))

        def get_edge(self, edge_id):
            return Edge(edge_id=edge_id.decode("utf-8"), source="drug-1", target="protein-1")

    assert snapshot.external_node_id(0) == "drug-1"
    assert snapshot.external_edge_id(0) == "d1-p1"
    assert snapshot.external_relation_id(0) == "binds"
    assert snapshot.global_triple_to_external([0, 0, 1]) == ("drug-1", "binds", "protein-1")
    assert snapshot.local_triple_to_external(batch, batch.positives[0]) == ("drug-1", "binds", "protein-1")
    assert snapshot.get_node(HydrationGraph(), 0).get_id == "drug-1"
    assert snapshot.get_edge(HydrationGraph(), 0).get_id == "d1-p1"


def test_sampler_engine_memmap_loads_snapshot(tmp_path):
    graph = _FakeSamplerGraph()
    snapshot = graph.build_sampler_snapshot(tmp_path / "sampler")

    engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=11)
    node_id = engine.snapshot.external_node_ids.tolist().index("drug-1")
    rel_id = engine.snapshot.external_relation_ids.tolist().index("drug-to-protein")
    sample = engine.sample_neighbors([node_id], fanout=1, direction="out", relations=[rel_id])

    assert sample.edge_indices.shape == (1,)
    assert engine.snapshot.src_int.shape == snapshot.src_int.shape


def test_sampler_engine_hard_negatives_reject_known_positives(tmp_path):
    graph = _FakeSamplerGraph()
    snapshot = graph.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine(snapshot, seed=13)
    seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")

    batch = engine.sample_subgraph(
        [seed_edge],
        fanouts=[2],
        direction="any",
        negative_config=HardNegativeConfig(negatives_per_positive=3, source="random", reject_known_positives=True),
    )
    global_negatives = batch.node_ids_global[batch.negatives[..., [0, 2]]]

    assert batch.negatives.shape == (1, 3, 3)
    for neg_idx in range(batch.negatives.shape[1]):
        src = int(global_negatives[0, neg_idx, 0])
        rel = int(batch.negatives[0, neg_idx, 1])
        dst = int(global_negatives[0, neg_idx, 1])
        assert not engine.is_positive(src, rel, dst)


def test_sampler_engine_relation_endpoint_type_negatives(tmp_path):
    graph = _FakeSamplerGraph()
    snapshot = graph.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine(snapshot, seed=19)
    seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")
    rel_id = snapshot.external_relation_ids.tolist().index("drug-to-protein")

    batch = engine.sample_subgraph(
        [seed_edge],
        fanouts=[1],
        negative_config=HardNegativeConfig(
            negatives_per_positive=1,
            relation_endpoint_types=True,
            head_probability=1.0,
        ),
    )
    global_head = int(batch.node_ids_global[batch.negatives[0, 0, 0]])

    assert snapshot.relation_src_type_ids[rel_id] == snapshot.node_type_ids[global_head]


def test_sampled_batch_optional_adapter_dependency_errors(tmp_path):
    graph = _FakeSamplerGraph()
    snapshot = graph.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine(snapshot, seed=23)
    seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")
    batch = engine.sample_subgraph([seed_edge], fanouts=[1])

    with blocked_import("pyarrow"):
        with pytest.raises(ImportError, match="pyarrow"):
            batch.to_arrow()
    with blocked_import("tensorflow"):
        with pytest.raises(ImportError, match="tensorflow"):
            batch.to_tf_gnns()
    with blocked_import("torch"):
        with pytest.raises(ImportError, match="torch"):
            batch.to_pyg()


def test_async_batch_feeder_prefetches_batches(tmp_path):
    graph = _FakeSamplerGraph()
    snapshot = graph.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine(snapshot, seed=17)
    seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")

    with AsyncBatchFeeder(lambda: engine.sample_subgraph([seed_edge], fanouts=[1]), max_prefetch=1) as feeder:
        batch = feeder.get(timeout=2)

    assert batch.positives.shape == (1, 3)
    assert batch.n_nodes >= 2


def test_sample_neighbors_uses_typed_frontier(graph_db):
    populate_typed_graph(graph_db)

    sample = graph_db.sample_neighbors(
        "drug-1",
        "drug-to-protein",
        direction="out",
        sample_size=1,
        rng=random.Random(7),
    )

    assert len(sample) == 1
    assert sample[0]["edge_type"] == "drug-to-protein"
    assert sample[0]["neighbor_id"] in {b"protein-1", b"protein-2"}


def test_sample_neighbors_streams_typed_adjacency(graph_db):
    populate_typed_graph(graph_db)

    def fail_materialized_adjacency(*args, **kwargs):
        raise AssertionError("sample_neighbors should not materialize full typed adjacency")

    graph_db.get_typed_adjacency = fail_materialized_adjacency

    sample = graph_db.sample_neighbors(
        "drug-1",
        "drug-to-protein",
        direction="out",
        sample_size=1,
        rng=random.Random(7),
    )

    assert len(sample) == 1
    assert sample[0]["edge_type"] == "drug-to-protein"


def test_sample_typed_paths_respects_edge_type_sequence(graph_db):
    populate_typed_graph(graph_db)

    paths = graph_db.sample_typed_paths(
        ["drug-1", "drug-2"],
        SamplingPattern([
            SamplingHop("drug-to-protein", direction="out", sample_size=2),
            SamplingHop("protein-to-disease", direction="out", sample_size=1),
        ]),
        rng=random.Random(3),
    )

    assert paths
    for sampled_path in paths:
        assert len(sampled_path["path"]) == 2
        assert sampled_path["path"][0]["edge_type"] == "drug-to-protein"
        assert sampled_path["path"][1]["edge_type"] == "protein-to-disease"
        assert sampled_path["path"][0]["target_id"].startswith(b"protein-")
        assert sampled_path["path"][1]["target_id"].startswith(b"disease-")


def test_sample_typed_subgraph_materializes_sampled_records(graph_db):
    populate_typed_graph(graph_db)

    subgraph = graph_db.sample_typed_subgraph(
        ["drug-1"],
        [
            {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 1},
            {"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1},
        ],
        rng=random.Random(11),
    )

    assert b"drug-1" in subgraph["nodes"]
    assert subgraph["edges"]
    assert subgraph["paths"]
    assert all(node is not None for node in subgraph["nodes"].values())
    assert all(edge is not None for edge in subgraph["edges"].values())


def test_put_edges_bulk_append_only_skips_existing_edge_reads(graph_db):
    for node_id in ["drug-1", "protein-1"]:
        graph_db.put_node(Node(node_id=node_id))

    def fail_get_edge(*args, **kwargs):
        raise AssertionError("append-only ingestion should skip existing-edge reads")

    graph_db.get_edge = fail_get_edge

    graph_db.put_edges_bulk(
        [Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"})],
        check_existing=False,
    )

    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-1"]


def test_put_edges_bulk_uses_bulk_typed_adjacency_writer(graph_db):
    for node_id in ["drug-1", "protein-1"]:
        graph_db.put_node(Node(node_id=node_id))

    def fail_single_typed_adjacency(*args, **kwargs):
        raise AssertionError("put_edges_bulk should use put_typed_adjacency_bulk")

    graph_db.store.put_typed_adjacency = fail_single_typed_adjacency

    graph_db.put_edges_bulk(
        [Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"})],
        check_existing=False,
    )

    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-1"]


def test_build_sampler_snapshot_exports_compact_arrays(graph_db, tmp_path):
    populate_typed_graph(graph_db)

    snapshot = graph_db.build_sampler_snapshot(tmp_path / "sampler")

    assert snapshot.num_nodes == 8
    assert snapshot.num_edges == 7
    assert set(snapshot.external_relation_ids.tolist()) == {"drug-to-disease", "drug-to-protein", "protein-to-disease"}
    assert snapshot.out.indptr.shape == (snapshot.num_nodes + 1,)
    assert snapshot.in_.indptr.shape == (snapshot.num_nodes + 1,)
    assert snapshot.incident.indptr.shape == (snapshot.num_nodes + 1,)
    assert snapshot.positive_triples.shape == (7, 3)


def test_sampler_engine_samples_relation_aware_neighbors(graph_db, tmp_path):
    populate_typed_graph(graph_db)
    snapshot = graph_db.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine.load(snapshot.path, seed=7)
    node_id = snapshot.external_node_ids.tolist().index("drug-1")
    rel_id = snapshot.external_relation_ids.tolist().index("drug-to-protein")

    sample = engine.sample_neighbors([node_id], fanout=10, direction="out", relations=[rel_id])

    neighbor_names = set(snapshot.external_node_ids[sample.neighbor_nodes].tolist())

    assert sample.offsets.tolist() == [0, 2]
    assert neighbor_names == {"protein-1", "protein-2"}
    assert set(snapshot.rel_int[sample.edge_indices].tolist()) == {rel_id}


def test_sampler_engine_exact_positive_membership(graph_db, tmp_path):
    populate_typed_graph(graph_db)
    snapshot = graph_db.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine(snapshot)
    nodes = snapshot.external_node_ids.tolist()
    relations = snapshot.external_relation_ids.tolist()

    drug = nodes.index("drug-1")
    protein = nodes.index("protein-1")
    disease = nodes.index("disease-1")
    rel = relations.index("drug-to-protein")

    assert engine.is_positive(drug, rel, protein)
    assert not engine.is_positive(drug, rel, disease)


def test_sampler_engine_sample_subgraph_returns_local_batch(graph_db, tmp_path):
    populate_typed_graph(graph_db)
    snapshot = graph_db.build_sampler_snapshot(tmp_path / "sampler")
    engine = SamplerEngine(snapshot, seed=3)
    seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")

    batch = engine.sample_subgraph([seed_edge], fanouts=[2], direction="any")

    arrays = batch.to_numpy()

    assert batch.n_nodes >= 2
    assert batch.n_edges >= 1
    assert arrays["positives"].shape == (1, 3)
    assert arrays["senders"].shape == arrays["receivers"].shape
