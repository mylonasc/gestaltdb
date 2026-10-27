"""RWS-01: bounded random-walk corpus iteration."""

import numpy as np
import pytest

from gestaltdb.sampling import RandomWalkBatch, SamplerEngine, SamplerSnapshot
from gestaltdb.sampling.engine import RandomWalkBatch as LegacyRandomWalkBatch


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=["a", "b", "c", "isolated"],
        external_edge_ids=["ab", "ac", "ba", "ca"],
        external_relation_ids=["R"],
        src_int=[0, 0, 1, 2], dst_int=[1, 2, 0, 0], rel_int=[0, 0, 0, 0],
        edge_weights=[1, 3, 1, 1],
    )


def arrays(batches):
    return {
        key: np.concatenate([batch.to_numpy()[key] for batch in batches])
        for key in ("node_ids", "edge_indices", "relation_ids", "lengths")
    }


def test_rounds_order_duplicates_and_partial_batches(snapshot):
    batches = list(SamplerEngine(snapshot, seed=7).iter_random_walks(
        [2, 0, 2], 3, walks_per_node=2, batch_size=2,
    ))
    assert [len(batch.lengths) for batch in batches] == [2, 1, 2, 1]
    assert arrays(batches)["node_ids"][:, 0].tolist() == [2, 0, 2, 2, 0, 2]
    assert RandomWalkBatch is LegacyRandomWalkBatch
    assert batches[0].to_external(snapshot)["node_ids"][0, 0] == "c"


@pytest.mark.parametrize("shuffle", [False, True])
@pytest.mark.parametrize("walk_type", ["random", "node2vec"])
def test_replay_batch_size_invariance_and_ram_memmap(snapshot, shuffle, walk_type):
    results = []
    for mode, batch_size in (("ram", 1), ("memmap", 3), ("ram", 20)):
        engine = SamplerEngine.load(snapshot.path, mode=mode, seed=19)
        batches = list(engine.iter_random_walks(
            walk_length=8, walks_per_node=3, batch_size=batch_size,
            shuffle=shuffle, walk_type=walk_type, dead_end="restart",
        ))
        results.append(arrays(batches))
        for round_starts in results[-1]["node_ids"][:, 0].reshape(3, 4):
            assert sorted(round_starts.tolist()) == [0, 1, 2, 3]
    for result in results[1:]:
        for key in result:
            np.testing.assert_array_equal(result[key], results[0][key])


@pytest.mark.parametrize("options", [
    {"strategy": "weighted"}, {"restart_probability": 0.4}, {"metapath": [0]},
    {"relations": [0], "direction": "in"},
    {"walk_type": "node2vec", "return_factor": 2.0, "inout_factor": 0.5},
])
def test_stream_matches_existing_kernel(snapshot, options):
    options = dict(options)
    kind = options.pop("walk_type", "random")
    streamed = arrays(list(SamplerEngine(snapshot, seed=5).iter_random_walks(
        [0, 1, 2, 3], 6, batch_size=2, walk_type=kind, **options,
    )))
    reference = SamplerEngine(snapshot, seed=5)
    sample = reference.sample_random_walks if kind == "random" else reference.sample_node2vec_walks
    expected = sample([0, 1, 2, 3], 6, **options).to_numpy()
    for key in expected:
        np.testing.assert_array_equal(streamed[key], expected[key])


def test_validation_is_eager_even_without_work(snapshot):
    engine = SamplerEngine(snapshot)
    for options, message in [
        ({"batch_size": 0}, "batch_size"),
        ({"batch_size": True}, "batch_size"),
        ({"walks_per_node": -1}, "walks_per_node"),
        ({"walks_per_node": 1.5}, "walks_per_node"),
        ({"shuffle": 1}, "shuffle"),
        ({"walk_type": "invalid"}, "walk_type"),
        ({"walk_length": -1}, "walk_length"),
        ({"direction": "sideways"}, "direction"),
        ({"relations": [99]}, "relations"),
        ({"start_nodes": [99]}, "start_nodes"),
        ({"start_nodes": [[0]]}, "one-dimensional"),
        ({"restart_probability": 2}, "restart_probability"),
    ]:
        with pytest.raises(ValueError, match=message):
            engine.iter_random_walks(**options)
    with pytest.raises(TypeError):
        engine.iter_random_walks([], unknown_option=True)
    with pytest.raises(ValueError, match="return_factor"):
        engine.iter_random_walks([], walks_per_node=0, walk_type="node2vec", return_factor=0)


def test_empty_cohorts_zero_rounds_and_zero_length(snapshot):
    engine = SamplerEngine(snapshot, seed=2)
    assert list(engine.iter_random_walks([])) == []
    assert list(engine.iter_random_walks(walks_per_node=0)) == []
    batches = list(engine.iter_random_walks(walk_length=0, batch_size=3))
    assert arrays(batches)["node_ids"].tolist() == [[0], [1], [2], [3]]
    assert all(batch.edge_indices.shape[1] == 0 for batch in batches)


def test_lazy_generation_copies_seed_cohort_and_bounds_kernel_batches(snapshot, monkeypatch):
    engine = SamplerEngine(snapshot, seed=9)
    sample = engine.sample_random_walks
    calls = []

    def record(starts, length, **options):
        calls.append(len(starts))
        return sample(starts, length, **options)

    monkeypatch.setattr(engine, "sample_random_walks", record)
    seeds = np.array([0, 1, 2, 3])
    stream = engine.iter_random_walks(seeds, 2, walks_per_node=10**9, batch_size=2)
    seeds[:] = 3
    assert calls == [0]  # Only empty validation; no walk generation yet.
    first = next(stream)
    assert calls == [0, 2]
    assert first.node_ids[:, 0].tolist() == [0, 1]
    next(stream)
    assert calls == [0, 2, 2]
    stream.close()


def test_all_node_unshuffled_seed_allocation_is_bounded(snapshot, monkeypatch):
    original = np.arange
    allocations = []

    def record(start, stop, *args, **kwargs):
        allocations.append(stop - start)
        return original(start, stop, *args, **kwargs)

    engine = SamplerEngine(snapshot)
    monkeypatch.setattr("gestaltdb.sampling.walks.np.arange", record)
    stream = engine.iter_random_walks(walk_length=0, batch_size=2)
    for batch in stream:
        assert len(batch.lengths) <= 2
    assert allocations == [2, 2]


def test_worker_streams_replay_without_changing_parent_rng(snapshot):
    parent = SamplerEngine(snapshot, seed=11)
    first = arrays(list(parent.worker_engine(0, num_workers=2).iter_random_walks(shuffle=True)))
    second = arrays(list(parent.worker_engine(0, num_workers=2).iter_random_walks(shuffle=True)))
    for key in first:
        np.testing.assert_array_equal(first[key], second[key])
    np.testing.assert_array_equal(
        parent.sample_random_walks([0], 10).node_ids,
        SamplerEngine(snapshot, seed=11).sample_random_walks([0], 10).node_ids,
    )


def test_empty_snapshot_yields_no_batches(tmp_path):
    empty = SamplerSnapshot.from_edge_arrays(
        tmp_path / "empty", external_node_ids=[], external_edge_ids=[],
        external_relation_ids=[], src_int=[], dst_int=[], rel_int=[],
    )
    engine = SamplerEngine(empty, seed=3)
    assert list(engine.iter_random_walks(shuffle=True)) == []
    assert engine.rng.random() == np.random.default_rng(3).random()


@pytest.mark.parametrize("walk_type", ["random", "node2vec"])
@pytest.mark.parametrize("window_policy", ["overlap", "contained"])
def test_temporal_options_retain_kernel_semantics(tmp_path, walk_type, window_policy):
    pytest.importorskip("lmdb")
    from gestaltdb import TemporalContext
    from gestaltdb.graphdb import Edge, GraphDB
    from gestaltdb.kvstores import LMDBStore
    from gestaltdb.serializers import PickleSerializer

    graph = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        graph.temporal.put_edge_version(Edge("ab", "a", "b", {"type": "R"}), valid=(0, 10))
        graph.temporal.put_edge_version(Edge("ba", "b", "a", {"type": "R"}), valid=(10, 20))
        snapshot = graph.build_sampler_snapshot(tmp_path / "temporal", temporal=True)
        options = {"temporal": TemporalContext.during(5, 15), "window_policy": window_policy}
        results = arrays(list(SamplerEngine(snapshot, seed=3).iter_random_walks(
            walk_length=4, batch_size=1, walk_type=walk_type, **options,
        )))
        reference = SamplerEngine(snapshot, seed=3)
        sample = reference.sample_random_walks if walk_type == "random" else reference.sample_node2vec_walks
        expected = sample(np.arange(snapshot.num_nodes), 4, **options).to_numpy()
        for key in expected:
            np.testing.assert_array_equal(results[key], expected[key])
    finally:
        graph.close()
