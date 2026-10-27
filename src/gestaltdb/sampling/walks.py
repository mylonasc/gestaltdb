"""Walk batches and bounded corpus iteration (RWS-01)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterator, Sequence, cast

import numpy as np

from .snapshot import SamplerSnapshot
from ._arrays import Array, IntArray, StringArray

if TYPE_CHECKING:
    from .engine import SamplerEngine


@dataclass(slots=True)
class RandomWalkBatch:
    """Fixed-width random walks over compact snapshot IDs.

    ``node_ids`` has shape ``(walks, walk_length + 1)``. ``edge_indices`` and
    ``relation_ids`` have shape ``(walks, walk_length)``. Unused positions and
    restart transitions use ``-1`` edge and relation IDs. ``lengths`` counts
    populated transitions, including restart transitions.
    """

    node_ids: IntArray
    edge_indices: IntArray
    relation_ids: IntArray
    lengths: IntArray

    def to_numpy(self) -> dict[str, IntArray]:
        """Return the walk arrays without copying them."""
        return {
            "node_ids": self.node_ids,
            "edge_indices": self.edge_indices,
            "relation_ids": self.relation_ids,
            "lengths": self.lengths,
        }

    def to_external(self, snapshot: SamplerSnapshot) -> dict[str, Array]:
        """Convert compact walk IDs to external IDs, preserving ``None`` gaps."""
        return {
            "node_ids": _external_id_array(self.node_ids, snapshot.external_node_ids),
            "edge_ids": _external_id_array(self.edge_indices, snapshot.external_edge_ids),
            "relation_ids": _external_id_array(self.relation_ids, snapshot.external_relation_ids),
            "lengths": self.lengths,
        }


def _external_id_array(compact_ids: IntArray, external_ids: StringArray) -> Array:
    compact_ids = np.asarray(compact_ids, dtype=np.int64)
    result = np.full(compact_ids.shape, None, dtype=object)
    valid = compact_ids >= 0
    if np.any(valid):
        selected = compact_ids[valid]
        if cast(Callable[[], np.int64], selected.max)() >= external_ids.size:
            raise ValueError("compact IDs are outside the snapshot ID range")
        result[valid] = external_ids[selected]
    return result


def iter_walk_batches(
    engine: SamplerEngine,
    start_nodes: Sequence[int] | np.ndarray | None,
    walk_length: int,
    *,
    walks_per_node: int,
    batch_size: int,
    shuffle: bool,
    walk_type: str,
    **walk_options,
) -> Iterator[RandomWalkBatch]:
    """Validate eagerly, then iterate rounds without materializing the corpus."""
    for name, value in (("walks_per_node", walks_per_node), ("batch_size", batch_size)):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"{name} must be an integer")
        if value < (1 if name == "batch_size" else 0):
            raise ValueError(f"{name} must be {'positive' if name == 'batch_size' else 'non-negative'}")
    if not isinstance(shuffle, bool):
        raise ValueError("shuffle must be a boolean")
    kernels = {"random": engine.sample_random_walks, "node2vec": engine.sample_node2vec_walks,
               "policy": engine.sample_policy_walks}
    if walk_type not in kernels:
        raise ValueError("walk_type must be 'random', 'node2vec' or 'policy'")
    sample = kernels[walk_type]
    # Reuse kernel validation even for empty cohorts/zero rounds. This draws no RNG.
    sample([], walk_length, **walk_options)
    seeds = None if start_nodes is None else engine._validated_ids(
        start_nodes, engine.snapshot.num_nodes, "start_nodes"
    ).copy()
    size = engine.snapshot.num_nodes if seeds is None else seeds.size

    def batches() -> Iterator[RandomWalkBatch]:
        if size == 0:
            return
        for _ in range(walks_per_node):
            order = engine.rng.permutation(size) if shuffle else None
            for offset in range(0, size, batch_size):
                stop = min(offset + batch_size, size)
                positions = np.arange(offset, stop) if order is None else order[offset:stop]
                starts = positions if seeds is None else seeds[positions]
                yield sample(starts, walk_length, **walk_options)

    return batches()
