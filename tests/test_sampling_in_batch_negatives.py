"""Sampling roadmap (#106): in-batch hard negatives."""

import numpy as np
import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.sampling import HardNegativeConfig, SamplerEngine
from gestaltdb.serializers import PickleSerializer


@pytest.fixture
def snapshot(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        graph.put_nodes([
            Node("h1", properties={"kind": "head"}),
            Node("h2", properties={"kind": "head"}),
            Node("h3", properties={"kind": "other"}),
            Node("h4", properties={"kind": "other"}),
            Node("t1", properties={"kind": "tail"}),
            Node("t2", properties={"kind": "tail"}),
            Node("t3", properties={"kind": "tail"}),
            Node("t4", properties={"kind": "tail"}),
        ])
        graph.put_edges_bulk([
            Edge("e1", "h1", "t1", properties={"type": "R"}),
            Edge("e2", "h2", "t2", properties={"type": "R"}),
            Edge("e3", "h3", "t3", properties={"type": "R"}),
            Edge("e4", "h4", "t4", properties={"type": "R"}),
        ])
        yield graph.build_sampler_snapshot(tmp_path / "snapshot")
    finally:
        graph.close()


def positives(snapshot):
    relation = snapshot.relation_int("R")
    return np.asarray([
        [snapshot.node_int("h1"), relation, snapshot.node_int("t1")],
        [snapshot.node_int("h2"), relation, snapshot.node_int("t2")],
        [snapshot.node_int("h3"), relation, snapshot.node_int("t3")],
        [snapshot.node_int("h4"), relation, snapshot.node_int("t4")],
    ], dtype=np.int64)


def test_in_batch_head_negatives_use_other_positive_heads(snapshot):
    triples = positives(snapshot)
    config = HardNegativeConfig(
        negatives_per_positive=1, in_batch=True, head_probability=1.0
    )
    negatives, diagnostics = SamplerEngine(snapshot, seed=3).sample_hard_negatives(
        triples, config=config, return_diagnostics=True
    )
    heads = {snapshot.node_int(node) for node in ("h1", "h2", "h3", "h4")}
    known = set(map(tuple, triples.tolist()))

    assert diagnostics["accepted_in_batch"] == len(triples)
    for row, positive in enumerate(triples.tolist()):
        negative = tuple(negatives[row, 0].tolist())
        assert negative[1] == positive[1]
        assert negative[0] in heads
        assert negative[0] != positive[0]
        assert negative not in known


def test_in_batch_tail_negatives_use_other_positive_tails(snapshot):
    triples = positives(snapshot)
    config = HardNegativeConfig(
        negatives_per_positive=1, in_batch=True, head_probability=0.0
    )
    negatives, diagnostics = SamplerEngine(snapshot, seed=5).sample_hard_negatives(
        triples, config=config, return_diagnostics=True
    )
    tails = {snapshot.node_int(node) for node in ("t1", "t2", "t3", "t4")}
    known = set(map(tuple, triples.tolist()))

    assert diagnostics["accepted_in_batch"] == len(triples)
    for row, positive in enumerate(triples.tolist()):
        negative = tuple(negatives[row, 0].tolist())
        assert negative[1] == positive[1]
        assert negative[2] in tails
        assert negative[2] != positive[2]
        assert negative not in known


def test_in_batch_respects_endpoint_type_constraints(snapshot):
    triples = positives(snapshot)
    config = HardNegativeConfig(
        negatives_per_positive=1,
        in_batch=True,
        head_probability=1.0,
        same_endpoint_type=True,
    )
    negatives, diagnostics = SamplerEngine(snapshot, seed=7).sample_hard_negatives(
        triples, config=config, return_diagnostics=True
    )

    assert diagnostics["accepted_in_batch"] == len(triples)
    for row, positive in enumerate(triples.tolist()):
        negative = negatives[row, 0].tolist()
        assert snapshot.node_type_ids[negative[0]] == snapshot.node_type_ids[positive[0]]


def test_in_batch_falls_back_when_no_peer_exists(snapshot):
    relation = snapshot.relation_int("R")
    triples = np.asarray([[
        snapshot.node_int("h1"), relation, snapshot.node_int("t1")
    ]], dtype=np.int64)
    config = HardNegativeConfig(
        negatives_per_positive=1, in_batch=True, head_probability=1.0
    )
    negatives = SamplerEngine(snapshot, seed=11).sample_hard_negatives(
        triples, config=config
    )

    assert negatives.shape == (1, 1, 3)
    assert tuple(negatives[0, 0].tolist()) != tuple(triples[0].tolist())


def test_in_batch_is_deterministic_for_seed(snapshot):
    triples = positives(snapshot)
    config = HardNegativeConfig(
        negatives_per_positive=2, in_batch=True, head_probability=0.5
    )
    first = SamplerEngine(snapshot, seed=13).sample_hard_negatives(
        triples, config=config
    )
    second = SamplerEngine(snapshot, seed=13).sample_hard_negatives(
        triples, config=config
    )

    assert first.tolist() == second.tolist()


def test_in_batch_config_validates_boolean():
    with pytest.raises(ValueError, match="in_batch"):
        HardNegativeConfig(in_batch="yes")
