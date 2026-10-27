"""RWS-08 cache bounds, exact numeric identity and seeded reference parity."""

import numpy as np
import pytest

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, WalkPolicy, WalkSchema
from benchmarks.walk_cache import WalkTransitionCache, cache_transitions


def sample_cached(engine, starts, length, cache, **options):
    with cache_transitions(cache):
        return engine.sample_policy_walks(starts, length, **options)


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot", external_node_ids=["a", "b", "c"],
        external_edge_ids=["ab", "ac", "bc", "ba", "ca"], external_relation_ids=["R"],
        src_int=[0, 0, 1, 1, 2], dst_int=[1, 2, 2, 0, 0], rel_int=[0] * 5,
        edge_weights=[0, 3, 1, 2, 1],
    )


@pytest.mark.parametrize("policy", [
    WalkPolicy(), WalkPolicy(strategy="weighted"), WalkPolicy(node2vec=True, return_factor=2, inout_factor=.5),
    WalkPolicy(node2vec=True, weighted_first_step=True), WalkPolicy(avoid_recent=2),
    WalkPolicy(schema=WalkSchema(metapath=[0])), WalkPolicy(transition_unit="neighbor", strategy="weighted"),
])
def test_exact_reference_parity_and_bounded_storage(snapshot, policy):
    cache = WalkTransitionCache(max_entries=8, max_bytes=4096)
    expected = SamplerEngine(snapshot, seed=8).sample_policy_walks([0, 1, 2] * 10, 12, direction="any", policy=policy)
    actual = sample_cached(
        SamplerEngine(snapshot, seed=8), [0, 1, 2] * 10, 12, cache, direction="any", policy=policy,
    )
    for name in expected.to_numpy():
        np.testing.assert_array_equal(actual.to_numpy()[name], expected.to_numpy()[name])
    assert cache.stats()["entries"] <= 8
    assert cache.stats()["payload_bytes"] <= 4096
    assert cache.stats()["hits"] > 0 or cache.stats()["bypassed"] > 0


def test_weight_changes_and_rebinding_do_not_reuse_stale_plans(snapshot):
    cache = WalkTransitionCache()
    policy = WalkPolicy(strategy="weighted")
    first = sample_cached(SamplerEngine(snapshot, seed=1), [0], 1, cache, policy=policy)
    assert first.node_ids.tolist() == [[0, 2]]
    snapshot.edge_weights[:] = [3, 0, 1, 2, 1]
    second = sample_cached(SamplerEngine(snapshot, seed=1), [0], 1, cache, policy=policy)
    assert second.node_ids.tolist() == [[0, 1]]
    assert cache.stats()["misses"] == 2
    other = SamplerSnapshot.load(snapshot.path)
    sample_cached(SamplerEngine(other, seed=1), [0], 1, cache, policy=policy)
    assert cache.stats()["entries"] == 1


def test_eviction_zero_capacity_and_clear(snapshot):
    for cache in (WalkTransitionCache(max_entries=1), WalkTransitionCache(max_bytes=1),
                  WalkTransitionCache(max_entries=0)):
        sample_cached(SamplerEngine(snapshot, seed=1), [0, 1, 2] * 3, 4, cache, policy=WalkPolicy())
        assert cache.stats()["entries"] <= cache.max_entries
        assert cache.stats()["payload_bytes"] <= cache.max_bytes
        assert cache.stats()["evictions"] > 0 or cache.stats()["bypassed"] > 0
        cache.clear()
        assert cache.stats()["entries"] == cache.stats()["payload_bytes"] == 0


def test_bad_capacities():
    for options in ({"max_entries": -1}, {"max_bytes": True}):
        with pytest.raises(ValueError):
            WalkTransitionCache(**options)
