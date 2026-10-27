"""RWS-05: finite-state alternatives and typed/metapath walks."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, WalkPolicy, WalkSchema, WalkTransition


@pytest.fixture
def engine(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot", external_node_ids=["a", "b", "c", "d"],
        external_edge_ids=["ab", "ac", "bd", "ca", "da"],
        external_relation_ids=["R", "S"], src_int=[0, 0, 1, 2, 3],
        dst_int=[1, 2, 3, 0, 0], rel_int=[0, 1, 1, 0, 0],
        node_type_ids=[0, 1, 1, 2],
    )
    return SamplerEngine(snapshot, seed=5)


def test_metapath_and_node_type_filter(engine):
    walks = engine.sample_policy_walks([0], 5, policy=WalkPolicy(schema=WalkSchema(metapath=[0, 1, 0])))
    assert walks.node_ids.tolist() == [[0, 1, 3, 0, 1, 3]]
    filtered = engine.sample_policy_walks([0], 3, policy=WalkPolicy(schema=WalkSchema(node_types=[1])))
    assert filtered.lengths.tolist() == [1]


def test_finite_state_directions_and_terminal_states(engine):
    schema = WalkSchema(transitions=[
        WalkTransition(0, 1, relation=0, source_types=[0], target_types=[1]),
        WalkTransition(1, 2, relation=0, direction="in", target_types=[0]),
    ])
    walks = engine.sample_policy_walks([0], 5, policy=WalkPolicy(schema=schema))
    assert walks.node_ids.tolist() == [[0, 1, 0, -1, -1, -1]]
    assert walks.lengths.tolist() == [2]


def test_metapath_and_state_reset_after_dead_end_restart(engine):
    for schema in (WalkSchema(metapath=[0, 1]), WalkSchema(transitions=[WalkTransition(0, 1, 0)])):
        walks = engine.sample_policy_walks([0], 6, dead_end="restart", policy=WalkPolicy(schema=schema))
        for step in np.flatnonzero(walks.edge_indices[0] == -1):
            assert walks.node_ids[0, step + 1] == 0
            if step + 1 < 6:
                assert walks.relation_ids[0, step + 1] == 0


def test_relation_and_transition_weights(engine):
    schema = WalkSchema(relation_weights={0: 0, 1: 1})
    walks = engine.sample_policy_walks([0] * 100, 1, policy=WalkPolicy(schema=schema))
    assert np.all(walks.node_ids[:, 1] == 2)
    schema = WalkSchema(transitions=[WalkTransition(0, 0, 0, weight=1), WalkTransition(0, 0, 1, weight=4)])
    draws = engine.sample_policy_walks([0] * 4000, 1, policy=WalkPolicy(schema=schema))
    assert abs(np.mean(draws.node_ids[:, 1] == 2) - .8) < .025


def test_zero_weight_arcs_and_global_filters_cannot_relax(engine):
    schema = WalkSchema(transitions=[WalkTransition(0, 0, relation=0, weight=0)])
    assert engine.sample_policy_walks([0], 1, policy=WalkPolicy(schema=schema)).lengths.tolist() == [0]
    schema = WalkSchema(metapath=[0])
    assert engine.sample_policy_walks([0], 1, relations=[1], policy=WalkPolicy(schema=schema)).lengths.tolist() == [0]


def test_unknown_types_relations_and_policy_bounds_fail_before_rng(engine):
    for schema in (WalkSchema(node_types=[99]), WalkSchema(metapath=[99]),
                   WalkSchema(transitions=[WalkTransition(0, 0, source_types=[99])])):
        with pytest.raises(ValueError):
            engine.sample_policy_walks([], 0, policy=WalkPolicy(schema=schema))
    for options in ({"max_states": 0}, {"max_states": 1, "initial_state": 1},
                    {"max_transitions": 0, "transitions": [WalkTransition(0, 0)]},
                    {"metapath": []}, {"metapath": [0], "transitions": [WalkTransition(0, 0)]},
                    {"relation_weights": {0: float("nan")}}):
        with pytest.raises(ValueError):
            WalkSchema(**options)


def test_schema_copies_caller_collections():
    weights = {0: 1}
    schema = WalkSchema(relation_weights=weights)
    weights[0] = 0
    assert schema.relation_weights[0] == 1
    with pytest.raises(TypeError):
        schema.relation_weights[0] = 0


def test_extreme_composing_weights_do_not_underflow_before_cancellation(engine):
    schema = WalkSchema(relation_weights={0: 1e-300, 1: 1e300}, transitions=[
        WalkTransition(0, 0, 0, weight=1e300), WalkTransition(0, 0, 1, weight=1e-300),
    ])
    batch = engine.sample_policy_walks([0] * 3000, 1, policy=WalkPolicy(schema=schema))
    assert abs(np.mean(batch.node_ids[:, 1] == 1) - .5) < .035
    engine.snapshot.edge_weights[:2] = [1e300, 1e-300]
    schema = WalkSchema(relation_weights={0: 1e-300, 1: 1e300})
    batch = engine.sample_policy_walks([0] * 3000, 1, policy=WalkPolicy(strategy="weighted", schema=schema))
    assert abs(np.mean(batch.node_ids[:, 1] == 1) - .5) < .035
