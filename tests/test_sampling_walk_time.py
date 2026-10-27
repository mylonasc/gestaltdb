"""RWS-06 chronology, half-open intervals and aligned temporal evidence."""

import numpy as np
import pytest

from gestaltdb import TemporalContext
from gestaltdb.graphdb import Edge, GraphDB
from gestaltdb.kvstores import LMDBStore
from gestaltdb.sampling import SamplerEngine, TemporalWalkBatch, TemporalWalkPolicy, WalkPolicy
from gestaltdb.serializers import PickleSerializer


@pytest.fixture
def snapshot(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        for edge, valid in ((Edge("ab", "a", "b", {"type": "R"}), (5, 10)),
                            (Edge("bc", "b", "c", {"type": "R"}), (0, 20)),
                            (Edge("ca", "c", "a", {"type": "R"}), (10, None))):
            graph.temporal.put_edge_version(edge, valid=valid)
        yield graph.build_sampler_snapshot(tmp_path / "snapshot", temporal=True)
    finally:
        graph.close()


def sample(snapshot, chronology, length=3, **options):
    return SamplerEngine(snapshot, seed=3).sample_policy_walks(
        snapshot.node_ints(["a"]), length, policy=WalkPolicy(chronology=chronology), **options,
    )


def test_interval_selection_differs_from_valid_start_order(snapshot):
    interval = sample(snapshot, TemporalWalkPolicy())
    assert isinstance(interval, TemporalWalkBatch)
    assert interval.transition_times_us.tolist() == [[5, 5, 10]]
    assert interval.transition_time_mask.tolist() == [[True, True, True]]
    event = sample(snapshot, TemporalWalkPolicy(time_selection="valid_start"))
    assert event.lengths.tolist() == [1]
    assert event.edge_version_ids[0, 1] is None
    assert interval.source_provenance == snapshot.metadata["source_provenance"]
    assert interval.source_provenance is not snapshot.metadata["source_provenance"]
    assert interval.to_external(snapshot)["transition_times_us"].tolist() == [[5, 5, 10]]


def test_backward_open_ends_and_half_open_boundaries(snapshot):
    backwards = sample(snapshot, TemporalWalkPolicy(order="nonincreasing", initial_time=15))
    assert backwards.transition_times_us[0, :2].tolist() == [9, 9]
    assert backwards.lengths.tolist() == [2]
    assert sample(snapshot, TemporalWalkPolicy(initial_time=10)).lengths.tolist() == [0]
    opened = SamplerEngine(snapshot).sample_policy_walks(snapshot.node_ints(["c"]), 1,
        policy=WalkPolicy(chronology=TemporalWalkPolicy(order="nonincreasing", initial_time=100)))
    assert opened.transition_times_us.tolist() == [[100]]


def test_gap_filters_query_windows_and_restart_clock(snapshot):
    assert sample(snapshot, TemporalWalkPolicy(max_gap_us=1)).lengths.tolist() == [2]
    assert sample(snapshot, TemporalWalkPolicy(initial_time=0, max_gap_us=1)).lengths.tolist() == [0]
    window = sample(snapshot, TemporalWalkPolicy(), temporal=TemporalContext.during(6, 9))
    assert window.transition_times_us[0, :2].tolist() == [6, 6]
    assert window.lengths.tolist() == [2]
    event = sample(snapshot, TemporalWalkPolicy(time_selection="valid_start"), temporal=TemporalContext.during(6, 9))
    assert event.lengths.tolist() == [0]
    restarted = sample(snapshot, TemporalWalkPolicy(), length=5, dead_end="restart")
    assert restarted.transition_time_mask.tolist() == [[True, True, True, False, True]]
    assert restarted.transition_times_us.tolist() == [[5, 5, 10, 0, 5]]


def test_point_filter_and_memmap_parity(snapshot):
    options = dict(walk_length=3, policy=WalkPolicy(chronology=TemporalWalkPolicy()),
                   temporal=TemporalContext.as_of(7))
    first = SamplerEngine.load(snapshot.path, mode="ram", seed=3).sample_policy_walks([0], **options)
    second = SamplerEngine.load(snapshot.path, mode="memmap", seed=3).sample_policy_walks([0], **options)
    for name in first.to_numpy():
        np.testing.assert_array_equal(first.to_numpy()[name], second.to_numpy()[name])
    assert first.transition_times_us[0, :2].tolist() == [7, 7]


def test_recency_weights_favor_reference(snapshot):
    # Incoming ab at start 5 versus outgoing bc at start 0, both incident on b.
    policy = WalkPolicy(chronology=TemporalWalkPolicy(
        time_selection="valid_start", reference_time=5, recency_half_life_us=5,
    ))
    result = SamplerEngine(snapshot, seed=7).sample_policy_walks(
        [snapshot.node_int("b")] * 4000, 1, direction="any", policy=policy,
    )
    assert abs(np.mean(result.transition_times_us == 5) - 2/3) < .025


@pytest.mark.parametrize("options", [
    {"order": "bad"}, {"time_selection": "bad"}, {"order": "nonincreasing"},
    {"max_gap_us": -1}, {"max_gap_us": True}, {"recency_half_life_us": 0},
    {"recency_half_life_us": 1}, {"reference_time": True},
])
def test_temporal_policy_validation(options):
    with pytest.raises((ValueError, TypeError)):
        TemporalWalkPolicy(**options)


def test_cache_prototype_does_not_reuse_wrong_clock_or_filter(snapshot):
    from benchmarks.walk_cache import WalkTransitionCache, cache_transitions

    cache = WalkTransitionCache(max_entries=8, max_bytes=1024)
    for context in (None, TemporalContext.during(6, 9)):
        options = dict(policy=WalkPolicy(chronology=TemporalWalkPolicy()),
                       temporal=context, dead_end="restart")
        expected = SamplerEngine(snapshot, seed=8).sample_policy_walks([0, 1, 2], 8, **options)
        with cache_transitions(cache):
            actual = SamplerEngine(snapshot, seed=8).sample_policy_walks([0, 1, 2], 8, **options)
        for name in expected.to_numpy():
            np.testing.assert_array_equal(actual.to_numpy()[name], expected.to_numpy()[name])
    assert cache.stats()["payload_bytes"] <= 1024
