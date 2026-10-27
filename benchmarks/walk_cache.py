"""Benchmark-only transition-distribution prototype; NOT a runtime API (RWS-08)."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np

from gestaltdb.sampling.validation import _validate_count


class WalkTransitionCache:
    """Cache edge-choice distributions, not walk results or source authentication.

    Keys include the exact ordered candidate IDs and effective float64 weight
    bytes. Filters, state, chronology and changing weights cannot reuse a plan
    unless they produce identical numeric inputs. A cache binds to one snapshot
    object at a time and clears entries on rebinding. Bounded entries AND key/
    array payload bytes limit retained data, not total Python overhead or RSS.
    Neighbor-group choices bypass this cache. Use a separate cache per worker;
    the cache and shared engine RNG are not thread-safe.
    """

    def __init__(self, *, max_entries: int = 1024, max_bytes: int = 4 * 1024 * 1024):
        _validate_count(max_entries, "max_entries")
        _validate_count(max_bytes, "max_bytes")
        self.max_entries, self.max_bytes = int(max_entries), int(max_bytes)
        self._plans = OrderedDict()
        self._source = None
        self._bytes = self._hits = self._misses = self._evictions = self._bypassed = 0

    def clear(self):
        """Release entries and snapshot binding; cumulative counters remain."""
        self._plans.clear()
        self._source = None
        self._bytes = 0

    def stats(self) -> dict[str, int]:
        """Return cumulative hit/miss/eviction counters and current payload size."""
        return dict(hits=self._hits, misses=self._misses, evictions=self._evictions,
                    bypassed=self._bypassed, entries=len(self._plans), payload_bytes=self._bytes)

    def choose(self, engine, candidates, weights) -> int:
        """Choose one aligned position with the reference kernel's RNG contract."""
        if self._source is not engine.snapshot:
            self.clear()
            self._source = engine.snapshot
        raw_weights = None if weights is None else np.asarray(weights, dtype=np.float64)
        key = (candidates.astype(np.int64, copy=False).tobytes(),
               None if raw_weights is None else raw_weights.tobytes())
        plan = self._plans.get(key)
        if plan is not None:
            self._hits += 1
            self._plans.move_to_end(key)
            positions, probabilities, _ = plan
        else:
            self._misses += 1
            positions = np.arange(candidates.size, dtype=np.int64)
            probabilities = None
            if raw_weights is not None:
                if (raw_weights.shape != candidates.shape or not np.all(np.isfinite(raw_weights))
                        or np.any(raw_weights < 0) or not np.any(raw_weights > 0)):
                    raise ValueError("sampling weights must be finite, non-negative and contain a positive value")
                positive = raw_weights > 0
                positions = positions[positive]
                scaled = raw_weights[positive] / float(np.max(raw_weights[positive]))
                probabilities = scaled / float(np.sum(scaled))
            if not positions.size:
                raise ValueError("transition cache requires nonempty candidates")
            size = len(key[0]) + (len(key[1]) if key[1] is not None else 0) + positions.nbytes
            size += probabilities.nbytes if probabilities is not None else 0
            if self.max_entries and size <= self.max_bytes:
                while self._plans and (len(self._plans) >= self.max_entries or self._bytes + size > self.max_bytes):
                    _, (_, _, old_size) = self._plans.popitem(last=False)
                    self._bytes -= old_size
                    self._evictions += 1
                self._plans[key] = positions, probabilities, size
                self._bytes += size
            else:
                self._bypassed += 1
        if positions.size == 1:
            return int(positions[0])
        chosen = engine.rng.choice(positions.size, size=1, replace=False, p=probabilities)
        return int(positions[int(chosen[0])])


@contextmanager
def cache_transitions(cache):
    """Install the prototype only inside a single-threaded benchmark/test scope."""
    from gestaltdb.sampling import walk_policy

    reference = walk_policy.choose_transition

    def choose(engine, candidates, endpoints, weights, policy):
        if policy.transition_unit == "edge":
            return cache.choose(engine, candidates, weights)
        cache._bypassed += 1
        return reference(engine, candidates, endpoints, weights, policy)

    with patch.object(walk_policy, "choose_transition", new=choose):
        yield
