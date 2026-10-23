"""PropEnh-06 (#119): importance-bias weight policies and persistence."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerSnapshot, derive_edge_weights


def test_uniform_and_degree_policies():
    src = [0, 0, 0, 1]
    dst = [1, 2, 3, 2]
    assert derive_edge_weights(src, dst, policy="uniform").tolist() == [1.0] * 4
    weights = derive_edge_weights(src, dst, policy="degree")
    # endpoint appearances: 0->3, 1->2, 2->2, 3->1; edge sums 5,5,4,4 mean 4.5
    assert weights.tolist() == pytest.approx([5 / 4.5, 5 / 4.5, 4 / 4.5, 4 / 4.5])
    assert float(weights.mean()) == pytest.approx(1.0)


def test_degree_policy_validates_inputs():
    with pytest.raises(ValueError, match="one of"):
        derive_edge_weights([0], [1], policy="pagerank")
    with pytest.raises(ValueError, match="non-negative"):
        derive_edge_weights([-1], [0], policy="degree")
    with pytest.raises(ValueError, match="exceed num_nodes"):
        derive_edge_weights([0], [5], policy="degree", num_nodes=3)
    with pytest.raises(ValueError, match="non-negative integer"):
        derive_edge_weights([0], [1], policy="degree", num_nodes=-1)
    assert derive_edge_weights([], [], policy="degree").tolist() == []


def test_recency_policy_decays_and_rejects_future_edges():
    src, dst = [0, 0, 1], [1, 2, 2]
    starts = [100, 200, 300]
    weights = derive_edge_weights(src, dst, policy="recency",
                                  valid_from_us=starts, half_life_us=100.0)
    assert weights.tolist() == pytest.approx(
        [np.exp(-2.0), np.exp(-1.0), np.exp(0.0)])
    with pytest.raises(ValueError, match="half_life"):
        derive_edge_weights(src, dst, policy="recency",
                            valid_from_us=starts, half_life_us=0.0)
    with pytest.raises(ValueError, match="valid_from_us and half_life_us"):
        derive_edge_weights(src, dst, policy="recency", valid_from_us=starts)
    with pytest.raises(ValueError, match="must not start after the reference"):
        derive_edge_weights(src, dst, policy="recency", valid_from_us=starts,
                            half_life_us=100.0, reference_time_us=250)
    with pytest.raises(ValueError, match="reference_time_us"):
        derive_edge_weights(src, dst, policy="recency", valid_from_us=starts,
                            half_life_us=100.0, reference_time_us="now")


def test_confidence_policy_normalizes_and_validates_mass():
    weights = derive_edge_weights([0, 1], [1, 2], policy="confidence", scores=[0.5, 1.5])
    assert weights.tolist() == pytest.approx([0.5, 1.5])
    with pytest.raises(ValueError, match="positive total mass"):
        derive_edge_weights([0, 1], [1, 2], policy="confidence", scores=[0.0, 0.0])
    with pytest.raises(ValueError, match="finite and non-negative"):
        derive_edge_weights([0, 1], [1, 2], policy="confidence", scores=[1.0, -1.0])
    with pytest.raises(ValueError, match="scores length"):
        derive_edge_weights([0, 1], [1, 2], policy="confidence", scores=[1.0])
    with pytest.raises(ValueError, match="positive total mass"):
        derive_edge_weights([0], [1], policy="confidence", scores=[0.0])


def test_set_edge_weights_persists_and_validates(tmp_path):
    snapshot = SamplerSnapshot.from_arrays(
        tmp_path / "weighted", external_node_ids=["a", "b", "c"],
        node_type_ids=[-1, -1, -1], external_edge_ids=["e1", "e2"],
        external_relation_ids=["r"], src_int=[0, 1], dst_int=[1, 2], rel_int=[0, 0])
    weights = derive_edge_weights([0, 1], [1, 2], policy="degree")
    summary = snapshot.set_edge_weights(weights, source="policy:degree")
    assert summary["edge_weight_source"] == "policy:degree"
    assert summary["snapshot_id"] == snapshot.metadata["snapshot_id"]
    assert snapshot.metadata["edge_weight_source"] == "policy:degree"
    assert snapshot.edge_weights.tolist() == pytest.approx(weights.tolist())

    reloaded = SamplerSnapshot.load(tmp_path / "weighted")
    assert reloaded.edge_weights.tolist() == pytest.approx(weights.tolist())
    assert reloaded.metadata["edge_weight_source"] == "policy:degree"

    with pytest.raises(ValueError, match="length must match"):
        snapshot.set_edge_weights([1.0], source="policy:degree")
    with pytest.raises(ValueError, match="finite and non-negative"):
        snapshot.set_edge_weights([1.0, -1.0], source="policy:degree")
    with pytest.raises(ValueError, match="non-empty provenance label"):
        snapshot.set_edge_weights(weights, source="")


def test_degree_bias_lifts_hub_inclusion_on_synthetic_scale_free_graph(tmp_path):
    rng = np.random.default_rng(11)
    src: list[int] = [0]
    dst: list[int] = [1]
    for node in range(2, 60):
        probs = np.bincount(
            [endpoint for edge in range(len(src)) for endpoint in (src[edge], dst[edge])],
            minlength=node)
        probs = probs / probs.sum()
        for target in rng.choice(node, size=2, replace=False, p=probs).tolist():
            src.append(node)
            dst.append(int(target))
    assert len(src) == len(dst) == 117
    snapshot = SamplerSnapshot.from_arrays(
        tmp_path / "scalefree", external_node_ids=[f"n{i}" for i in range(60)],
        node_type_ids=[-1] * 60, external_edge_ids=[f"e{i}" for i in range(117)],
        external_relation_ids=["links"], src_int=src, dst_int=dst, rel_int=[0] * 117)
    hub = int(np.bincount(src + dst).argmax())

    from gestaltdb.sampling import SamplerEngine
    uniform_hits = sum(
        hub in SamplerEngine(snapshot, seed=seed).sample_neighbors(
            [0], fanout=4, direction="any").neighbor_nodes.tolist()
        for seed in range(30))
    snapshot.set_edge_weights(
        derive_edge_weights(src, dst, policy="degree"), source="policy:degree")
    biased_hits = sum(
        hub in SamplerEngine(snapshot, seed=seed).sample_neighbors(
            [0], fanout=4, direction="any", strategy="weighted").neighbor_nodes.tolist()
        for seed in range(30))
    assert biased_hits >= uniform_hits


def test_weighted_draws_skew_toward_high_weight_edges(tmp_path):
    snapshot = SamplerSnapshot.from_arrays(
        tmp_path / "skew", external_node_ids=[f"n{i}" for i in range(10)],
        node_type_ids=[-1] * 10, external_edge_ids=[f"e{i}" for i in range(9)],
        external_relation_ids=["r"], src_int=[0] * 9, dst_int=list(range(1, 10)),
        rel_int=[0] * 9, edge_weights=[9.0] + [0.125] * 8)

    from gestaltdb.sampling import SamplerEngine
    uniform_engine = SamplerEngine(snapshot, seed=3)
    uniform_hits = sum(
        uniform_engine.sample_neighbors(
            [0], fanout=1, direction="out").neighbor_nodes.tolist() == [1]
        for _ in range(200))
    weighted_engine = SamplerEngine(snapshot, seed=3)
    weighted_hits = sum(
        weighted_engine.sample_neighbors(
            [0], fanout=1, direction="out", strategy="weighted").neighbor_nodes.tolist() == [1]
        for _ in range(200))
    assert uniform_hits < 60 < weighted_hits
