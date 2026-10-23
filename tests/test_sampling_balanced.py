"""Sampling roadmap (#106): balanced per-type and per-relation quotas."""

from collections import Counter

import pytest

from gestaltdb.sampling import (
    ExternalNeighborSamplingSpec,
    NeighborSamplingSpec,
    SamplerEngine,
    SamplerSnapshot,
)


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "balanced",
        external_node_ids=["hub", "a1", "a2", "a3", "b1", "b2", "g1"],
        external_edge_ids=["e-a1", "e-a2", "e-a3", "e-b1", "e-b2", "e-g1"],
        external_relation_ids=["alpha_edge", "beta_edge", "gamma_edge"],
        src_int=[0, 0, 0, 0, 0, 0],
        dst_int=[1, 2, 3, 4, 5, 6],
        rel_int=[0, 0, 0, 1, 1, 2],
        node_type_ids=[0, 1, 1, 1, 2, 2, 3],
        edge_weights=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    )


def relation_counts(snapshot, sampled):
    return Counter(snapshot.rel_int[sampled.edge_indices].tolist())


def test_balanced_neighbors_split_fanout_across_present_relations(snapshot):
    engine = SamplerEngine(snapshot, seed=7)

    one_each = engine.sample_neighbors([0], fanout=3, balanced=True)
    assert relation_counts(snapshot, one_each) == {0: 1, 1: 1, 2: 1}
    assert one_each.offsets.tolist() == [0, 3]

    two_each = engine.sample_neighbors([0], fanout=6, balanced=True)
    # gamma has only one edge and does not borrow from alpha or beta.
    assert relation_counts(snapshot, two_each) == {0: 2, 1: 2, 2: 1}
    assert two_each.offsets.tolist() == [0, 5]

    first_two_groups = engine.sample_neighbors([0], fanout=2, balanced=True)
    assert relation_counts(snapshot, first_two_groups) == {0: 1, 1: 1}


def test_balanced_neighbors_with_replacement_fill_every_quota(snapshot):
    sampled = SamplerEngine(snapshot, seed=3).sample_neighbors(
        [0], fanout=6, replace=True, balanced=True
    )
    assert relation_counts(snapshot, sampled) == {0: 2, 1: 2, 2: 2}
    assert sampled.edge_indices.size == 6


def test_balanced_neighbors_respect_the_relation_filter(snapshot):
    sampled = SamplerEngine(snapshot, seed=11).sample_neighbors(
        [0], fanout=2, relations=[0, 2], balanced=True
    )
    assert relation_counts(snapshot, sampled) == {0: 1, 2: 1}


def test_balanced_nodes_split_across_present_types(snapshot):
    engine = SamplerEngine(snapshot, seed=5)

    sampled = engine.sample_nodes(3, balanced=True)
    assert Counter(snapshot.node_type_ids[sampled].tolist()) == {0: 1, 1: 1, 2: 1}

    filtered = engine.sample_nodes(6, node_types=[1, 2, 3], balanced=True)
    # gamma has one node and does not borrow from alpha or beta.
    assert Counter(snapshot.node_type_ids[filtered].tolist()) == {1: 2, 2: 2, 3: 1}

    replaced = engine.sample_nodes(
        6, node_types=[1, 2, 3], replace=True, balanced=True
    )
    assert Counter(snapshot.node_type_ids[replaced].tolist()) == {1: 2, 2: 2, 3: 2}


def test_balanced_edges_split_across_present_relations(snapshot):
    engine = SamplerEngine(snapshot, seed=13)

    assert Counter(snapshot.rel_int[engine.sample_edges(3, balanced=True)].tolist()) == {
        0: 1, 1: 1, 2: 1}
    assert Counter(snapshot.rel_int[engine.sample_edges(6, balanced=True)].tolist()) == {
        0: 2, 1: 2, 2: 1}


def test_balanced_layer_specs_forward_the_flag(snapshot):
    engine = SamplerEngine(snapshot, seed=17)
    layer = engine.sample_layers(
        [0], [NeighborSamplingSpec(fanout=3, balanced=True)]
    ).layers[0]
    assert relation_counts(snapshot, layer) == {0: 1, 1: 1, 2: 1}

    external = engine.sample_layers_external(
        ["hub"],
        [ExternalNeighborSamplingSpec(
            fanout=3,
            relations=["alpha_edge", "beta_edge", "gamma_edge"],
            balanced=True,
        )],
    ).layers[0]
    assert relation_counts(snapshot, external) == {0: 1, 1: 1, 2: 1}


def test_balanced_weighted_groups_without_positive_weight_are_skipped(snapshot):
    snapshot.edge_weights[-1] = 0.0
    sampled = SamplerEngine(snapshot, seed=19).sample_edges(
        3, strategy="weighted", balanced=True
    )
    assert Counter(snapshot.rel_int[sampled].tolist()) == {0: 1, 1: 1}


def test_balanced_rejects_non_boolean_flags(snapshot):
    engine = SamplerEngine(snapshot, seed=23)
    with pytest.raises(ValueError, match="boolean"):
        engine.sample_nodes(1, balanced="yes")
    with pytest.raises(ValueError, match="boolean"):
        engine.sample_edges(1, balanced=1)
    with pytest.raises(ValueError, match="boolean"):
        engine.sample_neighbors([0], 1, balanced="yes")
    with pytest.raises(ValueError, match="boolean"):
        NeighborSamplingSpec(fanout=1, balanced="yes")
    with pytest.raises(ValueError, match="boolean"):
        ExternalNeighborSamplingSpec(fanout=1, balanced=1)
