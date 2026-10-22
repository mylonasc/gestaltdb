"""#106 follow-up: deterministic worker RNG stream splitting."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, split_seed


def _snapshot(tmp_path):
    return SamplerSnapshot.from_arrays(
        tmp_path / "rng-snap",
        external_node_ids=["a", "b", "c"],
        node_type_ids=[-1, -1, -1],
        external_edge_ids=["e1", "e2"],
        external_relation_ids=["r"],
        src_int=[0, 1],
        dst_int=[1, 2],
        rel_int=[0, 0],
    )


def test_split_seed_is_deterministic_distinct_and_validated():
    assert split_seed(7, 4) == split_seed(7, 4)
    children = split_seed(7, 4)
    assert len(children) == 4 and len(set(children)) == 4
    assert all(isinstance(child, int) for child in children)
    assert split_seed(None, 2)  # entropy-derived: independent, not reproducible
    with pytest.raises(ValueError, match="positive integer"):
        split_seed(7, 0)
    with pytest.raises(ValueError, match="positive integer"):
        split_seed(7, True)
    with pytest.raises(ValueError, match="integer or None"):
        split_seed("7", 2)


def test_worker_streams_are_independent_and_reproducible(tmp_path):
    snapshot = _snapshot(tmp_path)
    first = [SamplerEngine(snapshot, seed=seed).rng.random() for seed in split_seed(7, 4)]
    assert len(set(first)) == 4
    again = [SamplerEngine(snapshot, seed=seed).rng.random() for seed in split_seed(7, 4)]
    assert first == again


def test_worker_engine_samples_reproducibly_and_shares_snapshot(tmp_path):
    snapshot = _snapshot(tmp_path)
    parent = SamplerEngine(snapshot, seed=11)
    worker = parent.worker_engine(2, num_workers=4)
    assert worker.snapshot is snapshot
    assert worker.seed == split_seed(11, 4)[2]
    assert len({parent.worker_engine(i, num_workers=4).seed for i in range(4)}) == 4

    first = parent.worker_engine(0, num_workers=4).sample_neighbors([0], fanout=10, direction="out")
    second = parent.worker_engine(0, num_workers=4).sample_neighbors([0], fanout=10, direction="out")
    assert first.neighbor_nodes.tolist() == second.neighbor_nodes.tolist()


def test_worker_engine_validates_ids_and_seed_kind(tmp_path):
    parent = SamplerEngine(_snapshot(tmp_path), seed=7)
    with pytest.raises(ValueError, match="worker_id"):
        parent.worker_engine(4, num_workers=4)
    with pytest.raises(ValueError, match="worker_id"):
        parent.worker_engine(-1, num_workers=4)
    with pytest.raises(ValueError, match="positive integer"):
        parent.worker_engine(0, num_workers=0)
    unseeded = SamplerEngine(parent.snapshot, seed=np.random.SeedSequence(1))
    with pytest.raises(ValueError, match="integer or None"):
        unseeded.worker_engine(0, num_workers=2)


def test_unseeded_parent_still_splits_independently(tmp_path):
    parent = SamplerEngine(_snapshot(tmp_path), seed=None)
    workers = [parent.worker_engine(i, num_workers=2) for i in (0, 1)]
    assert all(isinstance(worker.seed, int) for worker in workers)
    for worker in workers:
        worker.sample_neighbors([0], fanout=10, direction="out")
