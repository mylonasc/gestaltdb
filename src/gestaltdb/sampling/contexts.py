"""Framework-independent Skip-gram context pairs from walk batches (RWS-02)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

import numpy as np

from .walks import RandomWalkBatch


def _count(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return int(value)


def _frequencies(values: Iterable[float] | np.ndarray, name: str) -> np.ndarray:
    frequencies = np.array(values, dtype=np.float64, copy=True)
    if frequencies.ndim != 1 or not np.all(np.isfinite(frequencies)) or np.any(frequencies < 0):
        raise ValueError(f"{name} must be one-dimensional, finite and non-negative")
    return frequencies


@dataclass(slots=True)
class ContextPairBatch:
    """Aligned compact center/context IDs, positive distances and pair weights.

    IDs are compact and belong to the walks' snapshot; convert at application
    boundaries. Batches are bounded by ``batch_size``; see ``iter_context_pairs``
    and ``docs/random-walks.rst`` RWS-02 for windowing and subsampling.
    """

    center_nodes: np.ndarray
    context_nodes: np.ndarray
    distances: np.ndarray
    weights: np.ndarray

    def to_numpy(self) -> dict[str, np.ndarray]:
        """Return arrays without copying; IDs belong to the walks' snapshot."""
        return {name: getattr(self, name) for name in (
            "center_nodes", "context_nodes", "distances", "weights"
        )}


def _segments(batch: RandomWalkBatch, cross_restarts: bool) -> Iterator[np.ndarray]:
    nodes, edges, relations, lengths = (
        batch.node_ids, batch.edge_indices, batch.relation_ids, batch.lengths
    )
    for name, values in (("node_ids", nodes), ("edge_indices", edges),
                         ("relation_ids", relations), ("lengths", lengths)):
        if not isinstance(values, np.ndarray) or values.dtype.kind not in "iu":
            raise ValueError(f"{name} must be an integer array")
    if (nodes.ndim != 2 or edges.ndim != 2 or lengths.shape != (nodes.shape[0],)
            or edges.shape != (nodes.shape[0], nodes.shape[1] - 1)
            or relations.shape != edges.shape):
        raise ValueError("walk batch shapes do not align")
    if np.any(lengths < 0) or np.any(lengths > edges.shape[1]):
        raise ValueError("walk lengths exceed populated transition bounds")
    for row, length in enumerate(lengths):
        length = int(length)
        prefix = nodes[row, :length + 1]
        if (np.any(prefix < 0) or np.any(edges[row, :length] < -1)
                or np.any(relations[row, :length] < -1)):
            raise ValueError("populated walk positions contain invalid IDs")
        if not np.array_equal(edges[row, :length] == -1, relations[row, :length] == -1):
            raise ValueError("restart edge/relation markers must agree")
        boundaries = [] if cross_restarts else (np.flatnonzero(edges[row, :length] == -1) + 1).tolist()
        start = 0
        for stop in (*boundaries, length + 1):
            yield prefix[start:stop]
            start = stop


def iter_context_pairs(
    walks: RandomWalkBatch | Iterable[RandomWalkBatch],
    *,
    window_size: int = 5,
    dynamic_window: bool = False,
    distance_weighting: str = "uniform",
    cross_restarts: bool = False,
    frequencies: Iterable[float] | np.ndarray | None = None,
    subsample_threshold: float = 0.0,
    batch_size: int = 4096,
    seed: int | None = None,
) -> Iterator[ContextPairBatch]:
    """Yield bounded Skip-gram pair batches, never joining separate walk rows.

    Dynamic windows draw a radius uniformly from 1 through ``window_size`` per
    retained center. ``inverse`` weighting emits 1/distance; ``uniform`` emits
    one. Restart jumps split sequences by default, before optional subsampling.
    Subsampling uses caller-supplied counts/frequencies and Word2Vec retention
    min(1, sqrt(t/f) + t/f), with normalized frequency f. Zero-frequency tokens
    are retained. Distances/windows apply to the sequence AFTER subsampling.
    An explicit seed reproduces results across output batch sizes, provided
    input walks/ordering are unchanged. Parameters validate eagerly; incoming
    walk arrays validate lazily. Memory: O(pair batch size + one walk row +
    caller frequency vector), excluding the upstream walk batch.
    """
    window_size = _count(window_size, "window_size", minimum=1)
    batch_size = _count(batch_size, "batch_size", minimum=1)
    if not isinstance(dynamic_window, bool) or not isinstance(cross_restarts, bool):
        raise ValueError("dynamic_window and cross_restarts must be booleans")
    if distance_weighting not in {"uniform", "inverse"}:
        raise ValueError("distance_weighting must be 'uniform' or 'inverse'")
    if (isinstance(subsample_threshold, (bool, np.bool_))
            or not isinstance(subsample_threshold, (int, float, np.integer, np.floating))
            or not np.isfinite(subsample_threshold) or subsample_threshold < 0):
        raise ValueError("subsample_threshold must be finite and non-negative")
    keep = None
    if frequencies is not None:
        counts = _frequencies(frequencies, "frequencies")
        if counts.size and counts.max() > 0 and subsample_threshold > 0:
            normalized = counts / counts.max()
            normalized /= normalized.sum()
            keep = np.ones(counts.size)
            positive = normalized > subsample_threshold
            ratio = subsample_threshold / normalized[positive]
            keep[positive] = np.minimum(1, np.sqrt(ratio) + ratio)
        else:
            keep = np.ones(counts.size)
    elif subsample_threshold > 0:
        raise ValueError("subsampling requires frequencies")
    rng = np.random.default_rng(seed)
    source = (walks,) if isinstance(walks, RandomWalkBatch) else walks

    def batches() -> Iterator[ContextPairBatch]:
        centers: list[int] = []
        contexts: list[int] = []
        distances: list[int] = []
        weights: list[float] = []

        def pack() -> ContextPairBatch:
            return ContextPairBatch(
                np.asarray(centers, dtype=np.int64), np.asarray(contexts, dtype=np.int64),
                np.asarray(distances, dtype=np.int64), np.asarray(weights, dtype=np.float64),
            )

        for batch in source:
            for segment in _segments(batch, cross_restarts):
                if keep is not None:
                    if segment.size and segment.max() >= keep.size:
                        raise ValueError("walk node ID is outside frequencies")
                    if subsample_threshold > 0:
                        segment = segment[rng.random(segment.size) < keep[segment]]
                for index, center in enumerate(segment):
                    radius = int(rng.integers(1, window_size + 1)) if dynamic_window else window_size
                    for other in range(max(0, index - radius), min(segment.size, index + radius + 1)):
                        if other == index:
                            continue
                        distance = abs(other - index)
                        centers.append(int(center))
                        contexts.append(int(segment[other]))
                        distances.append(distance)
                        weights.append(1.0 / distance if distance_weighting == "inverse" else 1.0)
                        if len(centers) == batch_size:
                            yield pack()
                            centers, contexts, distances, weights = [], [], [], []
        if centers:
            yield pack()

    return batches()
