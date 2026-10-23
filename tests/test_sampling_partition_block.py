"""Sampling roadmap (#106): random partition blocks with exact normalization."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=[f"n{index}" for index in range(6)],
        external_edge_ids=[f"e{index}" for index in range(6)],
        external_relation_ids=["R"],
        src_int=[0, 1, 2, 3, 4, 0],
        dst_int=[1, 2, 3, 4, 5, 0],
        rel_int=[0, 0, 0, 0, 0, 0],
    )


def test_partition_block_is_deterministic_for_seed(snapshot):
    kwargs = {"max_edges": 20, "normalize": True}
    first = SamplerEngine(snapshot, seed=3).sample_partition_block(3, **kwargs)
    second = SamplerEngine(snapshot, seed=3).sample_partition_block(3, **kwargs)

    assert first.node_ids_global.tolist() == second.node_ids_global.tolist()
    assert first.edge_ids_global.tolist() == second.edge_ids_global.tolist()
    assert first.node_weights.tolist() == second.node_weights.tolist()
    assert first.edge_weights.tolist() == second.edge_weights.tolist()


def test_partition_block_normalize_matches_realized_sizes(snapshot):
    engine = SamplerEngine(snapshot, seed=5)
    batch = engine.sample_partition_block(2, max_edges=20, normalize=True)

    assert batch.node_weights.tolist() == [2.0] * batch.n_nodes
    chunk_nodes = batch.n_nodes
    assert 0 < chunk_nodes <= snapshot.num_nodes
    loops = (
        snapshot.src_int[batch.edge_ids_global]
        == snapshot.dst_int[batch.edge_ids_global]
    )
    all_pairs = 6 * 5 / 2
    same = chunk_nodes * (chunk_nodes - 1) / 2 + (
        (6 - chunk_nodes) * (6 - chunk_nodes - 1) / 2
    )
    pair_weight = all_pairs / same * 2
    for edge, is_loop, weight in zip(
        batch.edge_ids_global.tolist(), loops.tolist(), batch.edge_weights.tolist()
    ):
        assert weight == pytest.approx(2.0 if is_loop else pair_weight)


def test_partition_block_single_partition_covers_all_nodes(snapshot):
    batch = SamplerEngine(snapshot, seed=7).sample_partition_block(
        1, max_edges=20, normalize=True
    )

    assert batch.n_nodes == snapshot.num_nodes
    assert batch.n_edges == snapshot.num_edges
    assert batch.node_weights.tolist() == [1.0] * snapshot.num_nodes
    assert batch.edge_weights.tolist() == [1.0] * snapshot.num_edges


def test_partition_block_more_partitions_than_nodes(snapshot):
    batch = SamplerEngine(snapshot, seed=11).sample_partition_block(
        10, max_edges=20, normalize=True
    )

    assert batch.n_nodes <= 1
    assert batch.node_weights.tolist() == [10.0] * batch.n_nodes
    for weight in batch.edge_weights.tolist():
        assert weight == 10.0


def test_partition_block_rejects_invalid_options(snapshot):
    engine = SamplerEngine(snapshot, seed=13)
    with pytest.raises(ValueError, match="num_partitions"):
        engine.sample_partition_block(0, max_edges=10)
    with pytest.raises(ValueError, match="max_edges"):
        engine.sample_partition_block(2, max_edges=-1)
    with pytest.raises(ValueError, match="normalize"):
        engine.sample_partition_block(2, max_edges=10, normalize="yes")
    with pytest.raises(ValueError, match="exceeding max_edges=0"):
        engine.sample_partition_block(1, max_edges=0)
