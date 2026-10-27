"""RWS-04: hard avoidance is distinct from soft node2vec bias."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, WalkPolicy


@pytest.fixture
def engine(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot", external_node_ids=["a", "b", "c"],
        external_edge_ids=["ab", "ba", "ba2", "bc", "cb", "aa"],
        external_relation_ids=["R"], src_int=[0, 1, 1, 1, 2, 0],
        dst_int=[1, 0, 0, 2, 1, 0], rel_int=[0] * 6,
    )
    return SamplerEngine(snapshot, seed=7)


def test_non_backtracking_excludes_all_parallel_returns(engine):
    batch = engine.sample_policy_walks([0] * 100, 8, policy=WalkPolicy(non_backtracking=True, avoid_recent=1))
    for nodes, length in zip(batch.node_ids, batch.lengths):
        assert nodes[:int(length) + 1].tolist() == [0, 1, 2]


@pytest.mark.parametrize("action", ["terminate", "restart", "relax"])
def test_avoidance_exhaustion_is_explicit(engine, action):
    batch = engine.sample_policy_walks([0], 6, policy=WalkPolicy(avoid_recent=3, avoidance_exhaustion=action))
    if action == "terminate":
        assert batch.lengths.tolist() == [2]
    elif action == "restart":
        assert batch.node_ids.tolist() == [[0, 1, 2, 0, 1, 2, 0]]
        assert batch.edge_indices[0, 2] == batch.edge_indices[0, 5] == -1
    else:
        assert batch.lengths.tolist() == [6]
        assert np.all(batch.edge_indices >= 0)


def test_relax_does_not_relax_relation_filter(engine):
    batch = engine.sample_policy_walks([0], 3, relations=[],
                                      policy=WalkPolicy(avoid_recent=1, avoidance_exhaustion="relax"))
    assert batch.lengths.tolist() == [0]


def test_random_restart_resets_history_and_preserves_markers(engine):
    batch = engine.sample_policy_walks([0], 3, restart_probability=1,
                                      policy=WalkPolicy(avoid_recent=4))
    assert batch.node_ids.tolist() == [[0, 0, 0, 0]]
    assert batch.edge_indices.tolist() == [[-1, -1, -1]]


def test_default_policy_matches_legacy_and_streaming(engine):
    reference = SamplerEngine(engine.snapshot, seed=7).sample_random_walks([0, 1, 2], 8)
    batches = list(engine.iter_random_walks([0, 1, 2], 8, walk_type="policy", batch_size=1))
    for key in reference.to_numpy():
        np.testing.assert_array_equal(np.concatenate([b.to_numpy()[key] for b in batches]),
                                      reference.to_numpy()[key])


@pytest.mark.parametrize("options", [
    {"non_backtracking": 1}, {"avoid_recent": -1}, {"avoid_recent": True},
    {"avoidance_exhaustion": "silent"}, {"strategy": "unknown"},
])
def test_invalid_policies(options):
    with pytest.raises(ValueError):
        WalkPolicy(**options)
