"""Versioned sparse relation directories within authenticated v2 snapshots."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ._arrays import IntArray
from .adjacency import CSRAdjacency

ENCODING = "sparse-rows-v1"
NAMES = ("relation_out", "relation_in")
TEMPORAL_NAMES = ("temporal_out", "temporal_in")


def sparse_encoding(metadata: dict) -> bool:
    encoding = metadata.get("relation_adjacency_encoding")
    if encoding is None:
        return False
    if encoding != ENCODING or metadata.get("format_version") != 2:
        raise ValueError("unsupported relation adjacency encoding")
    return True


def load_relation_adjacency(name: str, load_array: Callable[[str], IntArray], *, sparse: bool) -> CSRAdjacency:
    return CSRAdjacency(load_array(f"{name}_indptr"), load_array(f"{name}_edge_indices"),
                        load_array(f"{name}_row_keys") if sparse else None)


def sparse_arrays(name: str, adjacency: CSRAdjacency) -> dict[str, IntArray]:
    """Persist sparse rows, including when rewriting a legacy dense snapshot."""
    rows = adjacency.row_keys
    indptr = adjacency.indptr
    if rows is None:
        rows = np.flatnonzero(indptr[1:] > indptr[:-1]).astype(np.int64)
        indptr = np.concatenate((indptr[rows], indptr[-1:]))
    return {f"{name}_row_keys": rows, f"{name}_indptr": indptr,
            f"{name}_edge_indices": adjacency.edge_indices}
