"""RWS-03: conditional context negatives, not KG triple negatives."""

import numpy as np
import pytest

from gestaltdb.sampling import ContextNegativeSampler, SamplerSnapshot


def test_exclusions_uniqueness_and_repeat():
    sampler = ContextNegativeSampler([1, 1, 1, 1, 1], seed=3)
    draws = sampler.sample([0, 1], positive_contexts=[1, 2], negatives_per_positive=2,
                           excluded_contexts={0: [2]}, unique=True)
    assert set(draws[0]) == {3, 4}
    assert len(set(draws[1])) == 2 and not ({1, 2} & set(draws[1]))
    repeated = sampler.sample([0], excluded_contexts={0: [1, 2, 3]},
                              negatives_per_positive=4, unique=True, exhaustion="repeat")
    assert repeated.tolist() == [[4, 4, 4, 4]]


def test_distribution_and_reproducibility():
    first = ContextNegativeSampler([1, 4, 0], exponent=1, seed=7)
    second = ContextNegativeSampler([1, 4, 0], exponent=1, seed=7)
    options = dict(negatives_per_positive=20000, exclude_center=False)
    draws = first.sample([0], **options)
    np.testing.assert_array_equal(draws, second.sample([0], **options))
    assert abs(np.mean(draws == 1) - .8) < .015
    assert not np.any(draws == 2)
    np.testing.assert_allclose(ContextNegativeSampler([0, 0]).probabilities, [.5, .5])
    np.testing.assert_allclose(ContextNegativeSampler([0, 4], exponent=0).probabilities, [.5, .5])


def test_exhaustion_preflight_preserves_rng():
    sampler = ContextNegativeSampler([1, 1], seed=8)
    with pytest.raises(ValueError, match="exhausted"):
        sampler.sample([0, 1], positive_contexts=[0, 0], negatives_per_positive=2,
                       unique=True, exhaustion="repeat")
    np.testing.assert_array_equal(sampler.sample([0]), ContextNegativeSampler([1, 1], seed=8).sample([0]))
    with pytest.raises(ValueError, match="exhausted"):
        sampler.sample([0], unique=True, negatives_per_positive=2)


def test_empty_zero_requests_and_degree_factory(tmp_path):
    assert ContextNegativeSampler([]).sample([]).shape == (0, 5)
    assert ContextNegativeSampler([1]).sample([0], negatives_per_positive=0).shape == (1, 0)
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot", external_node_ids=["a", "b", "c"],
        external_edge_ids=["ab", "aa"], external_relation_ids=["R"],
        src_int=[0, 0], dst_int=[1, 0], rel_int=[0, 0],
    )
    np.testing.assert_allclose(ContextNegativeSampler.from_snapshot(snapshot, exponent=1).probabilities,
                               [3/4, 1/4, 0])


@pytest.mark.parametrize("frequencies,exponent", [([-1], 1), ([float("nan")], 1),
    ([[1]], 1), ([1], -1), ([1], float("inf")), ([1], True)])
def test_distribution_validation(frequencies, exponent):
    with pytest.raises(ValueError):
        ContextNegativeSampler(frequencies, exponent=exponent)


@pytest.mark.parametrize("centers,options", [
    ([2], {}), ([False], {}), ([[0]], {}), ([0], {"positive_contexts": []}),
    ([0], {"unique": 1}), ([0], {"negatives_per_positive": -1}),
    ([0], {"exhaustion": "silent"}), ([0], {"excluded_contexts": {0: [9]}}),
])
def test_sampling_validation(centers, options):
    with pytest.raises(ValueError):
        ContextNegativeSampler([1, 1]).sample(centers, **options)
