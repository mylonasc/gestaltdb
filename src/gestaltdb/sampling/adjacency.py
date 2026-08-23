"""Compact adjacency structures for array-native sampling."""

from __future__ import annotations

from dataclasses import dataclass

from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class CSRAdjacency:
    """Compressed sparse row adjacency over edge indices."""

    indptr: np.ndarray
    edge_indices: np.ndarray

    def edge_range(self, node: int) -> np.ndarray:
        start = int(self.indptr[node])
        end = int(self.indptr[node + 1])
        return self.edge_indices[start:end]


def build_csr(keys: Sequence[int] | np.ndarray, edge_indices: Sequence[int] | np.ndarray, size: int) -> CSRAdjacency:
    """Build CSR arrays sorted by integer key."""
    keys = np.asarray(keys, dtype=np.int64)
    edge_indices = np.asarray(edge_indices, dtype=np.int64)
    if keys.size == 0:
        return CSRAdjacency(np.zeros(size + 1, dtype=np.int64), np.empty(0, dtype=np.int64))
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    sorted_edges = edge_indices[order]
    counts = np.bincount(sorted_keys, minlength=size)
    indptr = np.empty(size + 1, dtype=np.int64)
    indptr[0] = 0
    np.cumsum(counts, out=indptr[1:])
    return CSRAdjacency(indptr, sorted_edges)
