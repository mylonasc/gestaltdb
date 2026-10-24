"""First-class importance-bias policies for neighborhood sampling.

Uniform draws undersample hubs on scale-free graphs. These policies derive
per-edge weights from graph structure, temporal recency, or caller-supplied
confidence scores; persistence goes through ``SamplerSnapshot.set_edge_weights``
and selection through the existing ``strategy="weighted"`` draws. Scale-free
convergence/step-time benchmarks remain explicitly open work (see the
PropEnh-06 tracker): what is specified and tested here is the mechanism —
policies, validation, persistence, and statistical contracts — plus a
bias-shift measurement on synthetic graphs, not a training-speed claim.
"""

from __future__ import annotations

import numpy as np

IMPORTANCE_POLICIES = ("uniform", "degree", "recency", "confidence")


def _as_int_array(values, name: str, size: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.int64)
    if array.ndim != 1 or array.size != size:
        raise ValueError(f"{name} length must match the number of edges")
    return array


def derive_edge_weights(src_int, dst_int, *, policy: str,
                        num_nodes: int | None = None,
                        valid_from_us=None,
                        reference_time_us: int | None = None,
                        half_life_us: float | None = None,
                        scores=None) -> np.ndarray:
    """Derive finite non-negative per-edge weights under a named policy.

    Args:
        src_int: Source compact node ID per edge.
        dst_int: Target compact node ID per edge.
        policy: One of ``"uniform"``, ``"degree"``, ``"recency"``,
            ``"confidence"``.
        num_nodes: Node count for degree bins. Defaults to one past the
            largest endpoint ID.
        valid_from_us: Inclusive valid-time starts (``"recency"`` only).
        reference_time_us: Time the recency decay is measured against.
            Defaults to the newest edge start. Edges starting after the
            reference fail closed: weights must never leak future knowledge.
        half_life_us: Positive finite recency half-life (``"recency"`` only).
        scores: Caller-supplied per-edge confidence scores (``"confidence"``
            only). Must be finite, non-negative, with positive total mass.

    Returns:
        Float64 weights with unit mean (uniform and degree policies) or the
        raw decay/score values otherwise. Unit-mean normalization keeps
        derived weights on the same scale as the ``1.0`` default, so
        swapping policies does not rescale downstream estimators.

    Raises:
        ValueError: On unknown policies, shape mismatches, non-positive
            half-lives, future-dated edges, or zero-mass scores.
    """
    src = _as_int_array(src_int, "src_int", len(np.asarray(dst_int)))
    dst = _as_int_array(dst_int, "dst_int", src.size)
    if not isinstance(policy, str) or policy not in IMPORTANCE_POLICIES:
        raise ValueError(f"policy must be one of {list(IMPORTANCE_POLICIES)}")
    if src.size and (src.min() < 0 or dst.min() < 0):
        raise ValueError("compact node IDs must be non-negative")
    if policy == "uniform":
        return np.ones(src.size, dtype=np.float64)
    if policy == "degree":
        if num_nodes is None:
            num_nodes = int(max(src.max(), dst.max()) + 1) if src.size else 0
        if isinstance(num_nodes, bool) or not isinstance(num_nodes, int) or num_nodes < 0:
            raise ValueError("num_nodes must be a non-negative integer")
        if src.size and (src.max() >= num_nodes or dst.max() >= num_nodes):
            raise ValueError("compact node IDs exceed num_nodes")
        degree = np.bincount(src, minlength=num_nodes) + np.bincount(dst, minlength=num_nodes)
        weights = (degree[src] + degree[dst]).astype(np.float64)
        mean = weights.mean() if weights.size else 1.0
        return weights / mean if mean > 0 else np.ones(src.size, dtype=np.float64)
    if policy == "recency":
        if valid_from_us is None or half_life_us is None:
            raise ValueError("recency requires valid_from_us and half_life_us")
        starts = _as_int_array(valid_from_us, "valid_from_us", src.size)
        if not isinstance(half_life_us, (int, float)) or isinstance(half_life_us, bool) \
                or not np.isfinite(half_life_us) or half_life_us <= 0:
            raise ValueError("half_life_us must be a positive finite number")
        reference = int(starts.max()) if reference_time_us is None else reference_time_us
        if isinstance(reference, bool) or not isinstance(reference, int):
            raise ValueError("reference_time_us must be an integer or None")
        ages = reference - starts
        if np.any(ages < 0):
            raise ValueError("recency edges must not start after the reference time")
        return np.exp(-ages.astype(np.float64) / float(half_life_us))
    weights = np.asarray(scores, dtype=np.float64)
    if weights.ndim != 1 or weights.size != src.size:
        raise ValueError("scores length must match the number of edges")
    if not np.all(np.isfinite(weights)) or np.any(weights < 0):
        raise ValueError("scores must be finite and non-negative")
    if not weights.size or not weights.sum() > 0:
        raise ValueError("confidence scores must carry positive total mass")
    return weights / weights.mean()
