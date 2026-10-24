"""Sampling roadmap (#106): degree-aware hard-negative fallback."""

import numpy as np
import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.sampling import HardNegativeConfig, SamplerEngine, SamplerSnapshot
from gestaltdb.serializers import PickleSerializer


@pytest.fixture
def snapshot(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        graph.put_nodes(
            [Node("c"), Node("a"), Node("b")]
            + [Node(f"l{index}") for index in range(50)]
        )
        graph.put_edges_bulk(
            [
                Edge(f"c{index}", "c", f"l{index}", properties={"type": "R"})
                for index in range(50)
            ]
            + [Edge("ab", "a", "b", properties={"type": "R"})]
        )
        yield graph.build_sampler_snapshot(tmp_path / "snapshot")
    finally:
        graph.close()


def test_degree_distribution_biases_fallback_to_high_degree_nodes(snapshot):
    relation = snapshot.relation_int("R")
    triples = np.tile(
        np.asarray(
            [[snapshot.node_int("a"), relation, snapshot.node_int("b")]],
            dtype=np.int64,
        ),
        (100, 1),
    )
    degree = SamplerEngine(snapshot, seed=3).sample_hard_negatives(
        triples,
        config=HardNegativeConfig(
            negatives_per_positive=1,
            head_probability=1.0,
            max_retries=10,
            candidate_distribution="degree",
            degree_exponent=4.0,
        ),
    )
    uniform = SamplerEngine(snapshot, seed=3).sample_hard_negatives(
        triples,
        config=HardNegativeConfig(
            negatives_per_positive=1,
            head_probability=1.0,
            max_retries=10,
            candidate_distribution="uniform",
        ),
    )
    center = snapshot.node_int("c")
    degree_hits = sum(
        center in (int(negative[0]), int(negative[2]))
        for negative in degree.reshape(-1, 3)
    )
    uniform_hits = sum(
        center in (int(negative[0]), int(negative[2]))
        for negative in uniform.reshape(-1, 3)
    )

    assert degree_hits >= 80
    assert degree_hits > uniform_hits


def test_degree_distribution_is_deterministic_for_seed(snapshot):
    relation = snapshot.relation_int("R")
    triples = np.asarray(
        [[snapshot.node_int("a"), relation, snapshot.node_int("b")]],
        dtype=np.int64,
    )
    config = HardNegativeConfig(
        negatives_per_positive=10,
        candidate_distribution="degree",
        degree_exponent=1.5,
    )
    first = SamplerEngine(snapshot, seed=5).sample_hard_negatives(
        triples, config=config
    )
    second = SamplerEngine(snapshot, seed=5).sample_hard_negatives(
        triples, config=config
    )

    assert first.tolist() == second.tolist()


def test_degree_distribution_falls_back_to_uniform_for_zero_degrees(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=["n0", "n1", "n2"],
        external_edge_ids=[],
        external_relation_ids=["R"],
        src_int=[],
        dst_int=[],
        rel_int=[],
    )
    triples = np.asarray([[0, 0, 1]], dtype=np.int64)
    negatives = SamplerEngine(snapshot, seed=7).sample_hard_negatives(
        triples,
        config=HardNegativeConfig(
            candidate_distribution="degree",
            degree_exponent=0.75,
            head_probability=1.0,
        ),
    )

    assert tuple(negatives[0, 0].tolist()) in {(2, 0, 1), (0, 0, 2)}


def test_degree_distribution_fails_closed_on_non_finite_weights(snapshot):
    relation = snapshot.relation_int("R")
    triples = np.asarray(
        [[snapshot.node_int("a"), relation, snapshot.node_int("b")]],
        dtype=np.int64,
    )

    with pytest.raises(ValueError, match="non-finite"):
        SamplerEngine(snapshot, seed=11).sample_hard_negatives(
            triples,
            config=HardNegativeConfig(
                candidate_distribution="degree",
                degree_exponent=10_000.0,
            ),
        )


def test_degree_distribution_config_validation():
    with pytest.raises(ValueError, match="candidate_distribution"):
        HardNegativeConfig(candidate_distribution="power")
    with pytest.raises(ValueError, match="non-negative"):
        HardNegativeConfig(degree_exponent=-0.1)
    with pytest.raises(TypeError, match="degree_exponent"):
        HardNegativeConfig(degree_exponent="0.5")
    with pytest.raises(TypeError, match="degree_exponent"):
        HardNegativeConfig(degree_exponent=True)
