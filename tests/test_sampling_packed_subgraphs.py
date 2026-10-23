"""Sampling roadmap (#106): packed multi-component subgraph batches."""

import numpy as np
import pytest

from gestaltdb.sampling import HardNegativeConfig, SamplerEngine, SamplerSnapshot


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=[f"n{index}" for index in range(6)],
        external_edge_ids=[f"e{index}" for index in range(5)],
        external_relation_ids=["R"],
        src_int=[0, 1, 2, 3, 4],
        dst_int=[1, 2, 3, 4, 5],
        rel_int=[0, 0, 0, 0, 0],
    )


def test_packed_components_are_self_contained(snapshot):
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_packed_subgraphs([[0], [3, 4]], fanouts=[2])

    assert batch.graph_node_offsets.tolist() == [0, batch.graph_node_offsets[1], batch.n_nodes]
    assert batch.graph_edge_offsets.tolist() == [0, batch.graph_edge_offsets[1], batch.n_edges]
    assert len(batch.graph_node_offsets) == 3
    for component in range(2):
        node_start = int(batch.graph_node_offsets[component])
        node_end = int(batch.graph_node_offsets[component + 1])
        edge_start = int(batch.graph_edge_offsets[component])
        edge_end = int(batch.graph_edge_offsets[component + 1])
        frame = set(range(node_start, node_end))
        assert set(batch.senders[edge_start:edge_end].tolist()) <= frame
        assert set(batch.receivers[edge_start:edge_end].tolist()) <= frame
        component_nodes = set(batch.node_ids_global[node_start:node_end].tolist())
        for edge in batch.edge_ids_global[edge_start:edge_end].tolist():
            assert int(snapshot.src_int[edge]) in component_nodes
            assert int(snapshot.dst_int[edge]) in component_nodes


def test_packed_positive_rows_follow_group_order(snapshot):
    batch = SamplerEngine(snapshot, seed=23).sample_packed_subgraphs(
        [[0], [3, 4]], fanouts=[2]
    )

    known = set(map(tuple, snapshot.positive_triples.tolist()))
    assert batch.positives.shape == (3, 3)
    node_end = int(batch.graph_node_offsets[1])
    assert batch.positives[0, 0] < node_end
    assert batch.positives[0, 2] < node_end
    assert (batch.positives[1:, 0] >= node_end).all()
    assert (batch.positives[1:, 2] >= node_end).all()
    for triple in batch.positives.tolist():
        global_triple = (
            int(batch.node_ids_global[triple[0]]),
            int(triple[1]),
            int(batch.node_ids_global[triple[2]]),
        )
        assert global_triple in known


def test_packed_matches_standalone_components(snapshot):
    kwargs = {"fanouts": [2], "negative_config": HardNegativeConfig(negatives_per_positive=1)}
    packed = SamplerEngine(snapshot, seed=5).sample_packed_subgraphs([[0], [4]], **kwargs)
    first = SamplerEngine(snapshot, seed=5).sample_subgraph([0], **kwargs)

    node_end = int(packed.graph_node_offsets[1])
    edge_end = int(packed.graph_edge_offsets[1])
    assert packed.node_ids_global[:node_end].tolist() == first.node_ids_global.tolist()
    assert packed.edge_ids_global[:edge_end].tolist() == first.edge_ids_global.tolist()
    assert packed.positives.tolist()[: len(first.positives)] == first.positives.tolist()
    assert packed.negative_diagnostics["accepted"] >= first.negative_diagnostics["accepted"]


def test_packed_is_deterministic_for_seed(snapshot):
    kwargs = {"fanouts": [2]}
    first = SamplerEngine(snapshot, seed=7).sample_packed_subgraphs([[0, 1], [2]], **kwargs)
    second = SamplerEngine(snapshot, seed=7).sample_packed_subgraphs([[0, 1], [2]], **kwargs)

    assert first.node_ids_global.tolist() == second.node_ids_global.tolist()
    assert first.edge_ids_global.tolist() == second.edge_ids_global.tolist()
    assert first.graph_node_offsets.tolist() == second.graph_node_offsets.tolist()
    assert first.graph_edge_offsets.tolist() == second.graph_edge_offsets.tolist()


def test_packed_handles_empty_groups(snapshot):
    batch = SamplerEngine(snapshot, seed=11).sample_packed_subgraphs([[], [0]], fanouts=[2])

    assert batch.graph_node_offsets.tolist()[0] == 0
    assert batch.graph_edge_offsets.tolist()[0] == 0
    assert len(batch.graph_node_offsets) == 3
    assert batch.n_edges > 0
    empty = SamplerEngine(snapshot, seed=13).sample_packed_subgraphs([], fanouts=[2])
    assert empty.n_nodes == 0
    assert empty.n_edges == 0
    assert empty.graph_node_offsets.tolist() == [0]
    assert empty.graph_edge_offsets.tolist() == [0]


def test_packed_rejects_invalid_groups(snapshot):
    engine = SamplerEngine(snapshot, seed=17)
    with pytest.raises(ValueError, match="seed_edges"):
        engine.sample_packed_subgraphs([[99]], fanouts=[2])


def test_packed_sums_diagnostics(snapshot):
    config = HardNegativeConfig(negatives_per_positive=2)
    batch = SamplerEngine(snapshot, seed=19).sample_packed_subgraphs(
        [[0], [1]], fanouts=[1], negative_config=config
    )

    assert batch.negative_diagnostics is not None
    assert batch.negative_diagnostics["accepted"] == 4
    assert batch.negatives.shape == (2, 2, 3)
