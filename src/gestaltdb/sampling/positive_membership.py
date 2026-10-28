"""Exact positive membership over sorted triples, without packed Python sets.

Temporal snapshots already persist sorted unique history triples whose row
numbers identify interval groups. Current non-temporal snapshots persist the
same lexicographic key shape. Legacy non-temporal snapshots build it once in RAM.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ._arrays import IntArray

if TYPE_CHECKING:
    from .snapshot import SamplerSnapshot

ENCODING = "lexicographic-triples-v1"
ARTIFACT = "positive_membership_triples"


def has_membership_encoding(metadata: dict[str, object]) -> bool:
    encoding = metadata.get("positive_membership_encoding")
    if encoding is None:
        return False
    if encoding != ENCODING or metadata.get("format_version") != 2:
        raise ValueError("unsupported positive membership encoding")
    if metadata.get("temporal") and metadata.get("temporal_history_indexes") is not True:
        raise ValueError("positive membership requires temporal history indexes")
    return True


def build_membership(triples: IntArray) -> IntArray:
    """Canonical unique lexicographic keys; build/legacy admission allocation."""
    return np.unique(triples, axis=0)


def build_positive_history_index(src: IntArray, rel: IntArray, dst: IntArray, valid_from_us: IntArray) -> tuple[IntArray, IntArray, IntArray]:
    """Canonical triple groups with stable valid-start/edge-row order."""
    triples = np.stack([src, rel, dst], axis=1) if len(src) else np.empty((0, 3), dtype=np.int64)
    if not len(triples):
        return triples, np.zeros(1, dtype=np.int64), np.empty(0, dtype=np.int64)
    edge_indices = np.arange(len(triples), dtype=np.int64)
    order = np.lexsort((edge_indices, valid_from_us, dst, rel, src))
    ordered = triples[order]
    starts = np.empty(len(ordered), dtype=np.bool_)
    starts[0] = True
    starts[1:] = np.any(ordered[1:] != ordered[:-1], axis=1)
    group_starts = np.flatnonzero(starts).astype(np.int64)
    indptr = np.concatenate([group_starts, np.asarray([len(ordered)], dtype=np.int64)])
    return ordered[group_starts], indptr, edge_indices[order]


def validate_membership(triples: IntArray | None, expected: IntArray) -> None:
    if (triples is None or triples.dtype != np.dtype("int64")
            or triples.ndim != 2 or triples.shape[1] != 3):
        raise ValueError("invalid positive membership schema")
    if not np.array_equal(triples, build_membership(expected)):
        raise ValueError("positive membership does not match edge triples")


class PositiveMembership:
    """Borrow exact sorted triple keys, including memmaps, without copying.

    Binary search touches O(log U) rows per lookup. Scalar tuple comparisons
    avoid packing overflow and per-lookup contiguous copies of strided columns.
    Construction trusts the snapshot loader's validation, not arbitrary data.
    """

    def __init__(self, triples: IntArray) -> None:
        self.triples = triples

    def find(self, src: int, rel: int, dst: int) -> int | None:
        key = (int(src), int(rel), int(dst))
        left, right = 0, self.triples.shape[0]
        while left < right:
            mid = (left + right) // 2
            value = (int(self.triples[mid, 0]), int(self.triples[mid, 1]), int(self.triples[mid, 2]))
            if value < key:
                left = mid + 1
            elif value > key:
                right = mid
            else:
                return mid
        return None


def snapshot_membership(snapshot: SamplerSnapshot) -> PositiveMembership:
    if snapshot.temporal:
        triples = snapshot.positive_history_triples
        if triples is None:
            raise ValueError("temporal positive membership requires history triples")
    else:
        triples = snapshot.positive_membership_triples
        if triples is None:
            if has_membership_encoding(snapshot.metadata):
                raise ValueError("encoded snapshot lacks positive membership triples")
            triples = build_membership(snapshot.positive_triples)
    return PositiveMembership(triples)
