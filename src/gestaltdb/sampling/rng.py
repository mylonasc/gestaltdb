"""Deterministic worker RNG splitting shared by sampler APIs."""

from __future__ import annotations

import numpy as np


def split_seed(seed: int | None, num_workers: int) -> tuple[int, ...]:
    """Derive independent worker seeds reproducibly from (seed, worker count).

    ``None`` draws fresh entropy. Workers rebuild their own engines; generators
    are not shared between processes. This does not imply worker-count invariance.
    """
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("seed must be an integer or None")
    if isinstance(num_workers, bool) or not isinstance(num_workers, int) or num_workers < 1:
        raise ValueError("num_workers must be a positive integer")
    return tuple(int(child.generate_state(1)[0]) for child in np.random.SeedSequence(seed).spawn(num_workers))
