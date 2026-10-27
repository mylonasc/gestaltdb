"""RWS-07 transition probabilities on multigraphs and filtered adjacency."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, WalkPolicy, WalkSchema


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "parallel", external_node_ids=["a", "b", "c"],
        external_edge_ids=["ab1", "ab2", "ab3", "ac"], external_relation_ids=["R"],
        src_int=[0] * 4, dst_int=[1, 1, 1, 2], rel_int=[0] * 4, edge_weights=[1, 1, 1, 4],
    )


@pytest.mark.parametrize("policy,expected", [
    (WalkPolicy(), .25),
    (WalkPolicy(transition_unit="neighbor"), .5),
    (WalkPolicy(transition_unit="neighbor", strategy="weighted", neighbor_weight_aggregation="sum"), 4/7),
    (WalkPolicy(transition_unit="neighbor", strategy="weighted", neighbor_weight_aggregation="mean"), .8),
    (WalkPolicy(transition_unit="neighbor", strategy="weighted", neighbor_weight_aggregation="max"), .8),
    (WalkPolicy(node2vec=True), .25),
    (WalkPolicy(node2vec=True, weighted_first_step=True), 4/7),
    (WalkPolicy(transition_unit="neighbor", schema=WalkSchema(node_types=None)), .5),
])
def test_parallel_edge_neighbor_and_weighted_first_step_probabilities(snapshot, policy, expected):
    walks = SamplerEngine(snapshot, seed=7).sample_policy_walks([0] * 5000, 1, policy=policy)
    assert abs(np.mean(walks.node_ids[:, 1] == 2) - expected) < .025
    # Metadata always identifies an actual edge to the selected neighbor.
    np.testing.assert_array_equal(snapshot.dst_int[walks.edge_indices[:, 0]], walks.node_ids[:, 1])


def test_eligible_vs_unfiltered_bias_adjacency(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "filtered", external_node_ids=["a", "b", "c"],
        external_edge_ids=["ab", "ba", "bc", "ac"], external_relation_ids=["R", "S"],
        src_int=[0, 1, 1, 0], dst_int=[1, 0, 2, 2], rel_int=[0, 0, 0, 1],
    )
    ratios = []
    for scope in ("snapshot", "eligible"):
        result = SamplerEngine(snapshot, seed=7).sample_policy_walks(
            [0] * 4000, 2, relations=[0], policy=WalkPolicy(node2vec=True, inout_factor=10, bias_adjacency=scope),
        )
        ratios.append(np.mean(result.node_ids[:, 2] == 2))
    assert abs(ratios[0] - .5) < .025
    assert abs(ratios[1] - 1/11) < .025


def test_policy_node2vec_matches_legacy(snapshot):
    for direction in ("out", "any"):
        expected = SamplerEngine(snapshot, seed=4).sample_node2vec_walks([0] * 20, 8, direction=direction,
                                                                      return_factor=2, inout_factor=.5)
        actual = SamplerEngine(snapshot, seed=4).sample_policy_walks([0] * 20, 8, direction=direction,
            policy=WalkPolicy(node2vec=True, return_factor=2, inout_factor=.5))
        np.testing.assert_array_equal(actual.node_ids, expected.node_ids)
        np.testing.assert_array_equal(actual.edge_indices, expected.edge_indices)


@pytest.mark.parametrize("options", [
    {"transition_unit": "bad"}, {"neighbor_weight_aggregation": "bad"},
    {"return_factor": 0}, {"inout_factor": float("nan")}, {"node2vec": 1},
    {"weighted_first_step": True}, {"bias_adjacency": "bad"},
    {"bias_adjacency": "eligible", "schema": WalkSchema(metapath=[0])},
])
def test_transition_validation(options):
    with pytest.raises(ValueError):
        WalkPolicy(**options)
