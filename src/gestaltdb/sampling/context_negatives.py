"""Co-occurrence-objective negatives, separate from KG edge negatives (RWS-03)."""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from .contexts import _count, _frequencies
from .snapshot import SamplerSnapshot


class ContextNegativeSampler:
    """Draw compact context IDs from caller counts raised to an exponent.

    Counts may be empirical corpus visits or snapshot degrees. All-zero counts
    fall back to uniform; otherwise zero-weight nodes are outside the support.
    Exponent zero gives uniform support, including zero-count nodes. IDs must
    belong to the exact dictionary used to construct the counts. Conditioning
    on exclusions costs O(nodes) per row plus output, with no rejection loops.
    No graph-edge checks are applied: a missing edge is not a negative context.
    """

    def __init__(self, frequencies, *, exponent: float = 0.75, seed: int | None = None):
        counts = _frequencies(frequencies, "frequencies")
        if (isinstance(exponent, (bool, np.bool_))
                or not isinstance(exponent, (int, float, np.integer, np.floating))
                or not np.isfinite(exponent) or exponent < 0):
            raise ValueError("exponent must be finite and non-negative")
        if exponent == 0 or (counts.size and counts.max() == 0):
            weights = np.ones(counts.size)
        else:
            weights = np.zeros(counts.size)
            if counts.size:
                positive = counts > 0
                log_counts = np.log(counts[positive])
                with np.errstate(over="ignore", under="ignore"):
                    weights[positive] = np.exp(exponent * (log_counts - log_counts.max()))
        self.probabilities = weights / weights.sum() if weights.size else weights
        self.probabilities.flags.writeable = False
        self.rng = np.random.default_rng(seed)

    @classmethod
    def from_snapshot(cls, snapshot: SamplerSnapshot, *, exponent: float = 0.75,
                      seed: int | None = None) -> ContextNegativeSampler:
        """Use incident degree (self-loops twice), not corpus visit counts."""
        return cls(np.diff(snapshot.incident.indptr), exponent=exponent, seed=seed)

    def _ids(self, values, name):
        ids = np.asarray(values)
        if (ids.ndim != 1 or (ids.size and ids.dtype.kind not in "iu")
                or (ids.size and (ids.min() < 0 or ids.max() >= self.probabilities.size))):
            raise ValueError(f"{name} must be one-dimensional valid compact node IDs")
        return ids.astype(np.int64, copy=False)

    def sample(
        self, centers: Sequence[int] | np.ndarray, *,
        positive_contexts: Sequence[int] | np.ndarray | None = None,
        negatives_per_positive: int = 5, exclude_center: bool = True,
        exclude_context: bool = True,
        excluded_contexts: Mapping[int, Sequence[int]] | None = None,
        unique: bool = False, exhaustion: str = "raise",
    ) -> np.ndarray:
        """Return ``(centers, negatives_per_positive)`` compact context IDs.

        Exclude centers, aligned positive contexts, and/or caller-provided sets
        of observed contexts. ``unique=True`` samples without replacement per
        row. Insufficient support raises before any draws; ``repeat`` instead
        samples the feasible unique set and repeats it cyclically. Empty support
        always raises for nonzero requests. Replay uses identical seed, inputs
        and call order. No cross-call batching invariance is promised.
        """
        count = _count(negatives_per_positive, "negatives_per_positive")
        for name, value in (("exclude_center", exclude_center), ("exclude_context", exclude_context),
                            ("unique", unique)):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        if exhaustion not in {"raise", "repeat"}:
            raise ValueError("exhaustion must be 'raise' or 'repeat'")
        centers = self._ids(centers, "centers")
        contexts = None if positive_contexts is None else self._ids(positive_contexts, "positive_contexts")
        if contexts is not None and contexts.shape != centers.shape:
            raise ValueError("positive_contexts must align with centers")
        excluded = {}
        for center, values in (excluded_contexts or {}).items():
            self._ids([center], "excluded_contexts center")
            excluded[int(center)] = self._ids(values, "excluded_contexts values")

        def eligible(row, center):
            weights = self.probabilities.copy()
            if exclude_center:
                weights[center] = 0
            if exclude_context and contexts is not None:
                weights[contexts[row]] = 0
            weights[excluded.get(int(center), [])] = 0
            return weights

        if count:
            for row, center in enumerate(centers):
                support = np.count_nonzero(eligible(row, center))
                if support == 0 or (unique and count > support and exhaustion == "raise"):
                    raise ValueError("negative context support exhausted by exclusions/uniqueness")
        result = np.empty((centers.size, count), dtype=np.int64)
        if count == 0:
            return result
        for row, center in enumerate(centers):
            weights = eligible(row, center)
            support = np.count_nonzero(weights)
            draw_count = min(count, support) if unique else count
            chosen = self.rng.choice(weights.size, size=draw_count, replace=not unique,
                                     p=weights / weights.sum())
            result[row] = np.resize(chosen, count)
        return result
