"""Compact adjacency structures for array-native sampling."""

from __future__ import annotations

from dataclasses import dataclass

from typing import Sequence

import numpy as np
from ._arrays import IntArray


@dataclass(frozen=True)
class CSRAdjacency:
    """CSR edge rows; optional sorted ``row_keys`` omit empty logical rows.

    With row keys, indptr addresses populated rows, not logical node IDs.
    Use ``edge_range`` for representation-independent lookup.
    """

    indptr: IntArray
    edge_indices: IntArray
    row_keys: IntArray | None = None

    def edge_range(self, node: int) -> IntArray:
        if self.row_keys is not None:
            position = int(np.searchsorted(self.row_keys, node))
            if position == self.row_keys.size or self.row_keys[position] != node:
                return self.edge_indices[:0]
            node = position
        start = int(self.indptr[node])
        end = int(self.indptr[node + 1])
        return self.edge_indices[start:end]


def build_csr(keys: Sequence[int] | IntArray, edge_indices: Sequence[int] | IntArray, size: int) -> CSRAdjacency:
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


def build_sparse_csr(keys: IntArray, edge_indices: IntArray, size: int, *, starts: IntArray | None = None) -> CSRAdjacency:
    """Group populated keys only, preserving stable edge order (or time order)."""
    if keys.shape != edge_indices.shape or keys.ndim != 1:
        raise ValueError("sparse CSR keys and edges must be aligned vectors")
    if size < 0 or size > np.iinfo(np.int64).max:
        raise ValueError("relation adjacency key space exceeds int64")
    if np.any(keys < 0) or np.any(keys >= size):
        raise ValueError("sparse CSR key out of range")
    order = np.argsort(keys, kind="stable") if starts is None else np.lexsort((edge_indices, starts, keys))
    ordered = keys[order]
    rows, counts = np.unique(ordered, return_counts=True)
    indptr = np.empty(rows.size + 1, dtype=np.int64)
    indptr[0] = 0
    np.cumsum(counts, out=indptr[1:])
    return CSRAdjacency(indptr, edge_indices[order], rows)


def validate_csr(name: str, adjacency: CSRAdjacency | None, size: int, edge_count: int, *,
                 expected_keys: IntArray, expected_edge_indices: IntArray | None = None) -> None:
    """Validate bounds/membership using edge/row arrays, not sparse key space."""
    if adjacency is None or adjacency.indptr.dtype != np.dtype("int64") or adjacency.edge_indices.dtype != np.dtype("int64"):
        raise ValueError(f"invalid {name} CSR dtype")
    if expected_edge_indices is None:
        expected_edge_indices = np.arange(edge_count, dtype=np.int64)
    entries = int(expected_edge_indices.size)
    if expected_keys.shape != (entries,):
        raise ValueError(f"invalid expected {name} CSR keys")
    rows = adjacency.row_keys
    if rows is not None:
        if (rows.dtype != np.dtype("int64") or rows.ndim != 1
                or (rows.size and (rows[0] < 0 or rows[-1] >= size))
                or np.any(rows[1:] <= rows[:-1])):
            raise ValueError(f"invalid {name} CSR row keys")
        row_count = rows.size
    else:
        row_count = size
    if adjacency.indptr.shape != (row_count + 1,) or adjacency.edge_indices.shape != (entries,):
        raise ValueError(f"invalid {name} CSR shape")
    counts = np.diff(adjacency.indptr)
    if (adjacency.indptr[0] != 0 or adjacency.indptr[-1] != entries
            or np.any(counts < 0) or (rows is not None and np.any(counts == 0))):
        raise ValueError(f"invalid {name} CSR bounds")
    if np.any(adjacency.edge_indices < 0) or np.any(adjacency.edge_indices >= edge_count):
        raise ValueError(f"invalid {name} CSR edge index")
    actual_keys = np.repeat(np.arange(size, dtype=np.int64) if rows is None else rows, counts)
    if np.array_equal(expected_edge_indices, np.arange(edge_count, dtype=np.int64)):
        if (not np.array_equal(actual_keys, expected_keys[adjacency.edge_indices])
                or not np.array_equal(np.bincount(adjacency.edge_indices, minlength=edge_count),
                                      np.ones(edge_count, dtype=np.int64))):
            raise ValueError(f"invalid {name} CSR membership")
        return
    actual_order = np.lexsort((adjacency.edge_indices, actual_keys))
    expected_order = np.lexsort((expected_edge_indices, expected_keys))
    if not (np.array_equal(actual_keys[actual_order], expected_keys[expected_order])
            and np.array_equal(adjacency.edge_indices[actual_order], expected_edge_indices[expected_order])):
        raise ValueError(f"invalid {name} CSR membership")
