"""Sampling roadmap (#106): bipartite message-passing layer blocks."""

import numpy as np
import pytest

from gestaltdb.sampling import NeighborSamplingSpec, SamplerEngine, SamplerSnapshot


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=["a", "b", "c", "d"],
        external_edge_ids=["ab", "ac", "bd", "cd"],
        external_relation_ids=["R", "S"],
        src_int=[0, 0, 1, 2],
        dst_int=[1, 2, 3, 3],
        rel_int=[0, 0, 1, 1],
    )


def test_blocks_recover_sampled_pairs(snapshot):
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_layers(
        [0],
        [
            NeighborSamplingSpec(fanout=2, direction="out"),
            NeighborSamplingSpec(fanout=2, direction="out"),
        ],
    )
    blocks = batch.to_blocks(snapshot)

    assert len(blocks) == 2
    for block, layer in zip(blocks, batch.layers):
        assert block["edge_index"].shape == (2, layer.edge_indices.size)
        assert block["edge_ids"].tolist() == layer.edge_indices.tolist()
        widths = np.diff(layer.offsets)
        row_of = np.repeat(np.arange(layer.input_nodes.size), widths)
        for column in range(layer.edge_indices.size):
            assert block["src_nodes"][block["edge_index"][0, column]] == (
                layer.neighbor_nodes[column]
            )
            assert block["dst_nodes"][block["edge_index"][1, column]] == (
                layer.input_nodes[row_of[column]]
            )
        assert (
            block["relation_ids"].tolist()
            == snapshot.rel_int[layer.edge_indices].tolist()
        )
        assert block["src_nodes"].tolist() == sorted(
            set(layer.neighbor_nodes.tolist())
        )
        assert block["dst_nodes"].tolist() == sorted(
            set(layer.input_nodes.tolist())
        )


def test_blocks_preserve_duplicate_draws(snapshot):
    engine = SamplerEngine(snapshot, seed=5)
    batch = engine.sample_layers(
        [0], [NeighborSamplingSpec(fanout=4, direction="out", replace=True)]
    )
    block = batch.to_blocks(snapshot)[0]
    layer = batch.layers[0]

    assert block["edge_index"].shape[1] == layer.edge_indices.size
    assert layer.edge_indices.size == 4


def test_blocks_handle_empty_layers(snapshot):
    engine = SamplerEngine(snapshot, seed=7)
    batch = engine.sample_layers(
        [3], [NeighborSamplingSpec(fanout=2, direction="out")]
    )
    block = batch.to_blocks(snapshot)[0]

    assert block["edge_index"].shape == (2, 0)
    assert block["edge_ids"].size == 0
    assert block["relation_ids"].size == 0
    assert block["dst_nodes"].tolist() == [3]


def test_blocks_are_deterministic_for_seed(snapshot):
    kwargs = {"seed_nodes": [0], "layers": [NeighborSamplingSpec(fanout=2)]}
    first = SamplerEngine(snapshot, seed=11).sample_layers(**kwargs).to_blocks(
        snapshot
    )
    second = SamplerEngine(snapshot, seed=11).sample_layers(**kwargs).to_blocks(
        snapshot
    )

    for left, right in zip(first, second):
        for key in ("src_nodes", "dst_nodes", "edge_index", "edge_ids", "relation_ids"):
            assert left[key].tolist() == right[key].tolist()


def test_blocks_external_maps_ids(snapshot):
    engine = SamplerEngine(snapshot, seed=13)
    batch = engine.sample_layers([0], [NeighborSamplingSpec(fanout=2)])
    block = batch.to_blocks_external(snapshot)[0]
    compact = batch.to_blocks(snapshot)[0]

    assert block["edge_index"].tolist() == compact["edge_index"].tolist()
    assert block["src_nodes"].tolist() == [
        snapshot.external_node_ids[node] for node in compact["src_nodes"]
    ]
    assert block["dst_nodes"].tolist() == [
        snapshot.external_node_ids[node] for node in compact["dst_nodes"]
    ]


def test_blocks_reject_misaligned_offsets(snapshot):
    from gestaltdb.sampling.engine import LayeredSampleBatch, SampledNeighbors

    bad = LayeredSampleBatch(
        seed_nodes=np.asarray([0]),
        layers=(
            SampledNeighbors(
                np.asarray([0]),
                np.asarray([0]),
                np.asarray([1]),
                np.asarray([0, 0]),
            ),
        ),
    )
    with pytest.raises(ValueError, match="offsets"):
        bad.to_blocks(snapshot)
