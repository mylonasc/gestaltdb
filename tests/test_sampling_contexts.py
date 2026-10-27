"""RWS-02 context windows, restart boundaries and bounded pair emission."""

import numpy as np
import pytest

from gestaltdb.sampling import RandomWalkBatch, iter_context_pairs


def walk(nodes=(0, 1, 2, 3), edges=(0, 1, 2), length=3):
    return RandomWalkBatch(np.array([nodes]), np.array([edges]), np.array([edges]), np.array([length]))


def collect(source, **options):
    batches = list(iter_context_pairs(source, **options))
    return {key: np.concatenate([b.to_numpy()[key] for b in batches])
            for key in ("center_nodes", "context_nodes", "distances", "weights")} if batches else {}


def test_fixed_windows_and_inverse_weights():
    result = collect(walk(), window_size=2, distance_weighting="inverse", batch_size=3)
    assert list(zip(result["center_nodes"], result["context_nodes"])) == [
        (0, 1), (0, 2), (1, 0), (1, 2), (1, 3), (2, 0), (2, 1), (2, 3), (3, 1), (3, 2)
    ]
    np.testing.assert_array_equal(result["weights"], 1 / result["distances"])


def test_padding_restart_boundaries_and_separate_rows():
    source = walk(nodes=(0, 1, 2, -1), edges=(0, -1, -1), length=2)
    pairs = collect([source, walk(nodes=(3, 4, 5, 6))], window_size=10)
    observed = set(zip(pairs["center_nodes"], pairs["context_nodes"]))
    assert (0, 1) in observed and (1, 2) not in observed
    assert all((a < 3) == (b < 3) for a, b in observed)
    crossing = collect(source, window_size=10, cross_restarts=True)
    assert (1, 2) in set(zip(crossing["center_nodes"], crossing["context_nodes"]))


def test_dynamic_windows_and_subsampling_replay_across_batches():
    source = [walk() for _ in range(300)]
    first = collect(source, window_size=3, dynamic_window=True, frequencies=[100, 2, 2, 2],
                    subsample_threshold=0.01, seed=4, batch_size=1)
    second = collect(source, window_size=3, dynamic_window=True, frequencies=[100, 2, 2, 2],
                     subsample_threshold=0.01, seed=4, batch_size=100)
    for key in first:
        np.testing.assert_array_equal(first[key], second[key])
    assert np.count_nonzero(first["center_nodes"] == 0) < np.count_nonzero(first["center_nodes"] == 1)


def test_empty_singletons_zero_frequency_and_bounded_emission():
    assert list(iter_context_pairs([])) == []
    assert collect(walk(length=0)) == {}
    assert len(collect(walk(), frequencies=[0, 0, 0, 0], subsample_threshold=.1)["weights"]) == 12
    assert [len(b.weights) for b in iter_context_pairs(walk(), batch_size=5)] == [5, 5, 2]


@pytest.mark.parametrize("options", [
    {"window_size": 0}, {"window_size": True}, {"batch_size": 0},
    {"dynamic_window": 1}, {"cross_restarts": 1}, {"distance_weighting": "bad"},
    {"frequencies": [-1]}, {"frequencies": [float("inf")]},
    {"subsample_threshold": .1}, {"subsample_threshold": float("nan")},
])
def test_eager_validation(options):
    with pytest.raises(ValueError):
        iter_context_pairs([], **options)


def test_late_validation_of_malformed_batches():
    with pytest.raises(ValueError, match="lengths"):
        collect(walk(length=9))
    with pytest.raises(ValueError, match="invalid IDs"):
        collect(walk(nodes=(0, -1, 2, 3)))
    with pytest.raises(ValueError, match="frequencies"):
        collect(walk(), frequencies=[1, 2])
    malformed = walk()
    malformed.relation_ids[0, 0] = -1
    with pytest.raises(ValueError, match="markers"):
        collect(malformed)
