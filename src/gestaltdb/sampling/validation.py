"""Integer argument validation shared by sampler kernels."""

from __future__ import annotations

import numpy as np


def _integer_array(values, name: str) -> np.ndarray:
    ids = np.asarray(values)
    if ids.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if ids.size and (np.issubdtype(ids.dtype, np.bool_) or not np.issubdtype(ids.dtype, np.integer)):
        raise ValueError(f"{name} must contain integer IDs")
    return ids.astype(np.int64, copy=False)


def _validate_count(value, name: str) -> None:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
