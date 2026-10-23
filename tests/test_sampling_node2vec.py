"""Sampling roadmap (#106): node2vec second-order biased walks."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot


@pytest.fixture
def snapshot(tmp_path):
    src, dst, edges = [], [], []
    for index in range(11):
        src.extend([index, index + 1])
        dst.extend([index + 1, index])
        edges.extend([f"e{index}f", f"e{index}b"])
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=[f"n{index}" for index in range(13)],
        external_edge_ids=edges,
        external_relation_ids=["R"],
        src_int=src,
        dst_int=dst,
        rel_int=[0] * len(src),
    )


def assert_valid_walks(snapshot, batch, direction):
    assert batch.node_ids.shape[1] == batch.edge_indices.shape[1] + 1
    for row in range(batch.node_ids.shape[0]):
        for step in range(int(batch.lengths[row])):
            edge = int(batch.edge_indices[row, step])
            assert edge != -1
            current = int(batch.node_ids[row, step])
            nxt = int(batch.node_ids[row, step + 1])
            assert nxt != -1
            if direction == "out":
                assert int(snapshot.src_int[edge]) == current
                assert int(snapshot.dst_int[edge]) == nxt
            elif direction == "in":
                assert int(snapshot.src_int[edge]) == nxt
                assert int(snapshot.dst_int[edge]) == current
            else:
                endpoints = {
                    int(snapshot.src_int[edge]),
                    int(snapshot.dst_int[edge]),
                }
                assert current in endpoints and nxt in endpoints


def test_node2vec_walks_are_valid(snapshot):
    engine = SamplerEngine(snapshot, seed=3)
    for direction in ("out", "in", "any"):
        batch = engine.sample_node2vec_walks(
            [5], 6, return_factor=2.0, inout_factor=0.5, direction=direction
        )
        assert_valid_walks(snapshot, batch, direction)


def test_node2vec_is_deterministic_for_seed(snapshot):
    kwargs = {
        "start_nodes": [0, 5, 10],
        "walk_length": 5,
        "return_factor": 0.5,
        "inout_factor": 2.0,
        "direction": "out",
    }
    first = SamplerEngine(snapshot, seed=11).sample_node2vec_walks(**kwargs)
    second = SamplerEngine(snapshot, seed=11).sample_node2vec_walks(**kwargs)
    third = SamplerEngine(snapshot, seed=12).sample_node2vec_walks(**kwargs)

    assert first.to_numpy()["node_ids"].tolist() == second.to_numpy()["node_ids"].tolist()
    assert first.to_numpy()["node_ids"].tolist() != third.to_numpy()["node_ids"].tolist()


def test_node2vec_bias_pushes_outward_or_holds_local(snapshot):
    outward = SamplerEngine(snapshot, seed=13).sample_node2vec_walks(
        [5] * 200, 8, return_factor=1.0, inout_factor=0.25, direction="any"
    )
    local = SamplerEngine(snapshot, seed=13).sample_node2vec_walks(
        [5] * 200, 8, return_factor=1.0, inout_factor=4.0, direction="any"
    )

    def mean_displacement(batch):
        final = batch.node_ids[np.arange(batch.node_ids.shape[0]), batch.lengths]
        return float(np.abs(final - 5).mean())

    assert mean_displacement(outward) > mean_displacement(local) + 0.5


def test_node2vec_high_return_factor_avoids_immediate_return(snapshot):
    engine = SamplerEngine(snapshot, seed=17)
    batch = engine.sample_node2vec_walks(
        [5] * 200, 2, return_factor=100.0, inout_factor=1.0, direction="any"
    )
    returns = sum(
        1
        for row in range(batch.node_ids.shape[0])
        if int(batch.lengths[row]) == 2
        and int(batch.node_ids[row, 2]) == 5
    )
    assert returns < 40


def test_node2vec_dead_ends(snapshot):
    engine = SamplerEngine(snapshot, seed=19)
    terminated = engine.sample_node2vec_walks([12], 3)
    assert terminated.lengths.tolist() == [0]
    assert terminated.node_ids[0, 0] == 12
    assert (terminated.node_ids[0, 1:] == -1).all()

    restarted = engine.sample_node2vec_walks([12], 3, dead_end="restart")
    assert restarted.lengths.tolist() == [3]
    assert (restarted.node_ids[0] == 12).all()
    assert (restarted.edge_indices[0] == -1).all()


def test_node2vec_external_variant(snapshot):
    engine = SamplerEngine(snapshot, seed=23)
    batch = engine.sample_node2vec_walks_external(["n5"], 4, direction="out")
    assert batch.lengths.tolist() == [4]
    assert_valid_walks(snapshot, batch, "out")


def test_node2vec_validates_options(snapshot):
    engine = SamplerEngine(snapshot, seed=29)
    with pytest.raises(ValueError, match="return_factor"):
        engine.sample_node2vec_walks([0], 2, return_factor=0.0)
    with pytest.raises(ValueError, match="inout_factor"):
        engine.sample_node2vec_walks([0], 2, inout_factor=-1.0)
    with pytest.raises(ValueError, match="return_factor"):
        engine.sample_node2vec_walks([0], 2, return_factor=float("inf"))
    with pytest.raises(ValueError, match="inout_factor"):
        engine.sample_node2vec_walks([0], 2, inout_factor="1.0")
    with pytest.raises(ValueError, match="return_factor"):
        engine.sample_node2vec_walks([0], 2, return_factor=True)
    with pytest.raises(ValueError, match="direction"):
        engine.sample_node2vec_walks([0], 2, direction="sideways")
    with pytest.raises(ValueError, match="dead_end"):
        engine.sample_node2vec_walks([0], 2, dead_end="loop")
