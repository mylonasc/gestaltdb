"""Array-native sampler engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from ..temporal import TemporalContext
from .batch import SampledSubgraphBatch
from .negatives import HardNegativeConfig
from .snapshot import SamplerSnapshot


@dataclass(frozen=True)
class NeighborSamplingSpec:
    """Sampling policy for one layer of neighborhood expansion."""

    fanout: int
    direction: str = "out"
    relations: Sequence[int] | None = None
    replace: bool = False
    strategy: str = "uniform"
    balanced: bool = False

    def __post_init__(self):
        _validate_count(self.fanout, "fanout")
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if self.strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if not isinstance(self.balanced, bool):
            raise ValueError("balanced must be a boolean")
        if self.relations is not None:
            relations = _integer_array(self.relations, "relations")
            object.__setattr__(self, "relations", tuple(int(rel) for rel in relations))


@dataclass(frozen=True)
class ExternalNeighborSamplingSpec:
    """Sampling policy using external relation IDs for one layer."""

    fanout: int
    direction: str = "out"
    relations: Sequence[object] | None = None
    replace: bool = False
    strategy: str = "uniform"
    balanced: bool = False

    def __post_init__(self):
        _validate_count(self.fanout, "fanout")
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if self.strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if not isinstance(self.balanced, bool):
            raise ValueError("balanced must be a boolean")
        if self.relations is not None:
            if isinstance(self.relations, (str, bytes)):
                raise TypeError("relations must be a sequence, not a scalar string")
            normalized = np.asarray(list(self.relations), dtype=str).tolist()
            object.__setattr__(self, "relations", tuple(normalized))


@dataclass(slots=True)
class SampledNeighbors:
    """Batched neighbor-sampling result.

    Attributes:
        input_nodes: Input compact node IDs, one per requested seed node.
        edge_indices: Sampled global compact edge IDs concatenated across all
            input nodes.
        neighbor_nodes: Neighbor compact node IDs aligned with ``edge_indices``.
        offsets: Prefix-sum offsets into ``edge_indices`` and ``neighbor_nodes``.
            Samples for ``input_nodes[i]`` are in ``offsets[i]:offsets[i + 1]``.
        edge_version_ids: Sampled edge-version IDs for temporal snapshots.
        valid_from_us: Inclusive valid starts aligned with sampled edges.
        valid_to_us: Exclusive valid ends, with zero for open intervals.
        valid_to_open: Open-end masks aligned with sampled edges.

    Examples:
        Iterate over sampled neighbors for each input node::

            result = engine.sample_neighbors([0, 1], fanout=5)
            for row, node in enumerate(result.input_nodes):
                start, end = result.offsets[row], result.offsets[row + 1]
                neighbors = result.neighbor_nodes[start:end]
    """

    input_nodes: np.ndarray
    edge_indices: np.ndarray
    neighbor_nodes: np.ndarray
    offsets: np.ndarray
    edge_version_ids: np.ndarray | None = None
    valid_from_us: np.ndarray | None = None
    valid_to_us: np.ndarray | None = None
    valid_to_open: np.ndarray | None = None

    def to_external(self, snapshot: SamplerSnapshot) -> dict[str, np.ndarray]:
        """Convert compact node and edge IDs to external IDs."""
        return {
            "input_nodes": _external_id_array(self.input_nodes, snapshot.external_node_ids),
            "edge_ids": _external_id_array(self.edge_indices, snapshot.external_edge_ids),
            "neighbor_nodes": _external_id_array(self.neighbor_nodes, snapshot.external_node_ids),
            "offsets": self.offsets,
        }


@dataclass(slots=True)
class LayeredSampleBatch:
    """Neighbor samples with one result block preserved per requested layer."""

    seed_nodes: np.ndarray
    layers: tuple[SampledNeighbors, ...]

    def to_external(self, snapshot: SamplerSnapshot) -> dict[str, object]:
        """Convert compact IDs in every layer to external IDs."""
        return {
            "seed_nodes": _external_id_array(self.seed_nodes, snapshot.external_node_ids),
            "layers": tuple(layer.to_external(snapshot) for layer in self.layers),
        }


@dataclass(slots=True)
class RandomWalkBatch:
    """Fixed-width random walks over compact snapshot IDs.

    ``node_ids`` has shape ``(walks, walk_length + 1)``. ``edge_indices`` and
    ``relation_ids`` have shape ``(walks, walk_length)``. Unused positions and
    restart transitions use ``-1`` edge and relation IDs. ``lengths`` counts
    populated transitions, including restart transitions.
    """

    node_ids: np.ndarray
    edge_indices: np.ndarray
    relation_ids: np.ndarray
    lengths: np.ndarray

    def to_numpy(self) -> dict[str, np.ndarray]:
        """Return the walk arrays without copying them."""
        return {
            "node_ids": self.node_ids,
            "edge_indices": self.edge_indices,
            "relation_ids": self.relation_ids,
            "lengths": self.lengths,
        }

    def to_external(self, snapshot: SamplerSnapshot) -> dict[str, np.ndarray]:
        """Convert compact walk IDs to external IDs, preserving ``None`` gaps."""
        return {
            "node_ids": _external_id_array(self.node_ids, snapshot.external_node_ids),
            "edge_ids": _external_id_array(self.edge_indices, snapshot.external_edge_ids),
            "relation_ids": _external_id_array(self.relation_ids, snapshot.external_relation_ids),
            "lengths": self.lengths,
        }


def split_seed(seed: int | None, num_workers: int) -> tuple[int, ...]:
    """Derive one deterministic worker seed per training worker.

    Uses ``SeedSequence.spawn`` so sibling workers hold statistically
    independent streams while the whole split reproduces from ``(seed,
    num_workers)``. Workers run in separate processes and cannot share
    ``Generator`` objects, so each worker rebuilds its engine from its own
    integer seed (see ``SamplerEngine.worker_engine``). ``seed=None`` draws
    fresh entropy: workers stay independent but the split does not
    reproduce — pass an explicit seed for deterministic training.
    """
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("seed must be an integer or None")
    if isinstance(num_workers, bool) or not isinstance(num_workers, int) or num_workers < 1:
        raise ValueError("num_workers must be a positive integer")
    return tuple(int(child.generate_state(1)[0]) for child in np.random.SeedSequence(seed).spawn(num_workers))


class SamplerEngine:
    """High-throughput sampler over a static ``SamplerSnapshot``.

    The engine samples compact integer arrays instead of materializing GraphDB
    ``Node``/``Edge`` objects. It supports direction-aware traversal,
    relation-aware traversal, heterogeneous layers, first-order random walks,
    multihop subgraph sampling, exact positive triple checks, and configurable
    hard-negative generation.

    Args:
        snapshot: Loaded sampler snapshot.
        seed: Optional NumPy RNG seed for reproducible sampling.

    Examples:
        Load a snapshot and sample two-hop neighborhoods around seed edges::

            engine = SamplerEngine.load("data/sampler", mode="memmap", seed=7)
            batch = engine.sample_subgraph([10, 11], fanouts=[15, 10])
            arrays = batch.to_numpy()
    """

    def __init__(self, snapshot: SamplerSnapshot, *, seed: int | None = None):
        self.snapshot = snapshot
        self.seed = seed
        if snapshot.edge_weights.size == 0 and snapshot.num_edges:
            snapshot.edge_weights = np.ones(snapshot.num_edges, dtype=np.float64)
        else:
            snapshot.edge_weights = self._validated_weights(snapshot.edge_weights, snapshot.num_edges, "snapshot.edge_weights")
        self.rng = np.random.default_rng(seed)
        self._positive_codes = self._build_positive_codes()
        self._positive_history_groups = self._build_positive_history_groups()

    @classmethod
    def load(cls, path: str | Path, *, mode: str = "ram", seed: int | None = None, **_kwargs) -> "SamplerEngine":
        """Load a sampler engine from a snapshot path.

        Args:
            path: Snapshot directory created by ``SamplerSnapshot``.
            mode: ``"ram"`` eagerly loads arrays; ``"memmap"`` memory maps them.
            seed: Optional RNG seed.
            **_kwargs: Reserved for future cache/prefetch options.

        Returns:
            Initialized ``SamplerEngine``.

        Raises:
            ValueError: If ``mode`` is not ``"ram"`` or ``"memmap"``.

        Examples:
            Load a RAM-backed engine::

                engine = SamplerEngine.load("snapshot", mode="ram", seed=13)
        """
        if mode not in {"ram", "memmap"}:
            raise ValueError("mode must be 'ram' or 'memmap'")
        return cls(SamplerSnapshot.load(path, mmap=mode == "memmap"), seed=seed)

    def worker_engine(self, worker_id: int, *, num_workers: int) -> "SamplerEngine":
        """Build a sibling engine holding this worker's RNG substream.

        The snapshot object is shared, not copied; only the RNG stream is
        independent and deterministic per ``(seed, worker_id, num_workers)``.
        Requires the engine to have been constructed with an integer seed
        (or ``None`` for entropy-derived independence without reproducibility).
        """
        if isinstance(worker_id, bool) or not isinstance(worker_id, int):
            raise ValueError("worker_id must be an integer")
        if isinstance(num_workers, bool) or not isinstance(num_workers, int) or num_workers < 1:
            raise ValueError("num_workers must be a positive integer")
        if not 0 <= worker_id < num_workers:
            raise ValueError("worker_id must satisfy 0 <= worker_id < num_workers")
        return type(self)(self.snapshot, seed=split_seed(self.seed, num_workers)[worker_id])

    def sample_neighbors(
        self,
        nodes: Sequence[int] | np.ndarray,
        fanout: int,
        *,
        direction: str = "out",
        relations: Sequence[int] | np.ndarray | None = None,
        replace: bool = False,
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
        causal_policy: str = "none",
        strategy: str = "uniform",
        balanced: bool = False,
    ) -> SampledNeighbors:
        """Sample neighbors for each input node.

        Args:
            nodes: Compact node IDs to sample from.
            fanout: Maximum number of edges sampled per node.
            direction: ``"out"`` for source-to-target, ``"in"`` for
                target-to-source, or ``"any"`` for incident traversal.
            relations: Optional compact relation IDs to restrict traversal.
            replace: Whether to sample with replacement.
            temporal: Optional point or finite-window validity filter. Requires a
                temporal format-v2 snapshot.
            window_policy: ``"overlap"`` selects intervals intersecting a
                window; ``"contained"`` selects intervals fully inside it.
            causal_policy: Accepted for API consistency. It constrains valid
                starts across hops in multihop and subgraph sampling.
            strategy: ``"uniform"`` or ``"weighted"``. Weighted sampling uses
                the snapshot's edge weights.
            balanced: When true, split ``fanout`` into deterministic equal
                shares across the relation IDs present for each input node.
                Groups smaller than their share return fewer edges when
                ``replace=False``; unused quota is not borrowed from other
                relations. Default uniform allocation is unchanged.

        Returns:
            ``SampledNeighbors`` with concatenated edge and neighbor arrays.

        Raises:
            ValueError: If ``fanout`` is negative or ``direction`` is invalid.

        Examples:
            Relation-aware neighbor sampling::

                sampled = engine.sample_neighbors(
                    [drug_id], fanout=10, direction="out", relations=[binds_id]
                )
        """
        self._validate_sampling_options(
            fanout=fanout, direction=direction, temporal=temporal,
            window_policy=window_policy, causal_policy=causal_policy,
        )
        return self._sample_neighbors(
            nodes, fanout, direction=direction, relations=relations, replace=replace,
            temporal=temporal, window_policy=window_policy, causal_policy=causal_policy,
            strategy=strategy, balanced=balanced,
        )

    def _sample_neighbors(
        self,
        nodes,
        fanout,
        *,
        direction,
        relations,
        replace=False,
        temporal=None,
        window_policy="overlap",
        causal_policy="none",
        causal_starts=None,
        strategy="uniform",
        balanced=False,
    ) -> SampledNeighbors:
        if strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if not isinstance(balanced, bool):
            raise ValueError("balanced must be a boolean")
        input_nodes = self._validated_ids(nodes, self.snapshot.num_nodes, "nodes")
        if causal_starts is not None and len(causal_starts) != input_nodes.size:
            raise ValueError("causal_starts must align with input nodes")
        offsets = [0]
        sampled_edges: list[np.ndarray] = []
        sampled_neighbors: list[np.ndarray] = []
        relation_filter = None
        if relations is not None:
            relation_filter = set(self._validated_ids(relations, self.snapshot.num_relations, "relations").tolist())

        for row, node in enumerate(input_nodes.tolist()):
            candidates = self._candidate_edges(node, direction, relation_filter, temporal, window_policy)
            if causal_starts is not None and causal_starts[row] is not None and candidates.size:
                starts = self.snapshot.valid_from_us[candidates]
                if causal_policy == "nondecreasing":
                    candidates = candidates[starts >= causal_starts[row]]
                elif causal_policy == "nonincreasing":
                    candidates = candidates[starts <= causal_starts[row]]
            weights = self.snapshot.edge_weights[candidates] if strategy == "weighted" else None
            if balanced:
                chosen = self._choose_balanced(
                    candidates,
                    fanout,
                    replace=replace,
                    weights=weights,
                    groups=self.snapshot.rel_int[candidates],
                )
            else:
                chosen = self._choose_candidates(candidates, fanout, replace=replace, weights=weights)
            sampled_edges.append(chosen.astype(np.int64, copy=False))
            sampled_neighbors.append(self._neighbors_for_edges(node, chosen, direction))
            offsets.append(offsets[-1] + int(chosen.size))

        edge_indices = np.concatenate(sampled_edges) if sampled_edges else np.empty(0, dtype=np.int64)
        neighbor_nodes = np.concatenate(sampled_neighbors) if sampled_neighbors else np.empty(0, dtype=np.int64)
        temporal_arrays = self._temporal_arrays(edge_indices)
        return SampledNeighbors(
            input_nodes, edge_indices, neighbor_nodes, np.asarray(offsets, dtype=np.int64),
            *temporal_arrays,
        )

    def sample_neighbors_external(
        self,
        nodes: Sequence[object],
        fanout: int,
        *,
        direction: str = "out",
        relations: Sequence[object] | None = None,
        replace: bool = False,
        strategy: str = "uniform",
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
        causal_policy: str = "none",
        balanced: bool = False,
    ) -> SampledNeighbors:
        """Sample neighbors using external node and relation IDs as input."""
        compact_nodes = self.snapshot.node_ints(nodes)
        compact_relations = None if relations is None else self.snapshot.relation_ints(relations)
        return self.sample_neighbors(
            compact_nodes,
            fanout,
            direction=direction,
            relations=compact_relations,
            replace=replace,
            strategy=strategy,
            temporal=temporal,
            window_policy=window_policy,
            causal_policy=causal_policy,
            balanced=balanced,
        )

    def sample_nodes(
        self,
        count: int,
        *,
        node_types: Sequence[int] | np.ndarray | None = None,
        replace: bool = False,
        strategy: str = "uniform",
        weights: Sequence[float] | np.ndarray | None = None,
        balanced: bool = False,
    ) -> np.ndarray:
        """Sample compact node IDs, optionally restricted by node type.

        Args:
            count: Maximum number of node IDs to return.
            node_types: Optional compact node type IDs to restrict candidates.
            replace: Whether to sample with replacement.
            strategy: ``"uniform"``, ``"weighted"``, or ``"degree"``.
            weights: Per-node weights for ``strategy="weighted"``.
            balanced: When true, split ``count`` into deterministic equal
                shares across the distinct node type IDs present in the
                candidate set. Groups smaller than their share return fewer
                nodes when ``replace=False``; unused quota is not borrowed
                from other types. Untyped nodes (compact type ``-1``) form
                their own group when no ``node_types`` filter excludes them.
        """
        if not isinstance(balanced, bool):
            raise ValueError("balanced must be a boolean")
        candidates = np.arange(self.snapshot.num_nodes, dtype=np.int64)
        if node_types is not None:
            types = self._validated_nonnegative_ids(node_types, "node_types")
            candidates = candidates[np.isin(self.snapshot.node_type_ids, types)]
        if strategy == "degree":
            if weights is not None:
                raise ValueError("weights cannot be combined with strategy='degree'")
            weights_array = np.diff(self.snapshot.incident.indptr).astype(np.float64)[candidates]
        elif strategy == "weighted":
            if weights is None:
                raise ValueError("weights are required for strategy='weighted'")
            all_weights = self._validated_weights(weights, self.snapshot.num_nodes, "weights")
            weights_array = all_weights[candidates]
        elif strategy == "uniform":
            if weights is not None:
                raise ValueError("weights require strategy='weighted'")
            weights_array = None
        else:
            raise ValueError("strategy must be 'uniform', 'weighted', or 'degree'")
        if balanced:
            return self._choose_balanced(
                candidates,
                count,
                replace=replace,
                weights=weights_array,
                groups=self.snapshot.node_type_ids[candidates],
            )
        return self._choose_candidates(candidates, count, replace=replace, weights=weights_array)

    def sample_edges(
        self,
        count: int,
        *,
        relations: Sequence[int] | np.ndarray | None = None,
        replace: bool = False,
        strategy: str = "uniform",
        weights: Sequence[float] | np.ndarray | None = None,
        balanced: bool = False,
    ) -> np.ndarray:
        """Sample compact edge IDs, optionally restricted by relation.

        Args:
            count: Maximum number of edge IDs to return.
            relations: Optional compact relation IDs to restrict candidates.
            replace: Whether to sample with replacement.
            strategy: ``"uniform"`` or ``"weighted"``.
            weights: Per-edge weights for ``strategy="weighted"``.
            balanced: When true, split ``count`` into deterministic equal
                shares across the distinct relation IDs present in the
                candidate set. Groups smaller than their share return fewer
                edges when ``replace=False``; unused quota is not borrowed
                from other relations.
        """
        if not isinstance(balanced, bool):
            raise ValueError("balanced must be a boolean")
        candidates = np.arange(self.snapshot.num_edges, dtype=np.int64)
        if relations is not None:
            relation_ids = self._validated_ids(relations, self.snapshot.num_relations, "relations")
            candidates = candidates[np.isin(self.snapshot.rel_int, relation_ids)]
        if strategy == "weighted":
            all_weights = self.snapshot.edge_weights if weights is None else self._validated_weights(weights, self.snapshot.num_edges, "weights")
            candidate_weights = all_weights[candidates]
        elif strategy == "uniform":
            if weights is not None:
                raise ValueError("weights require strategy='weighted'")
            candidate_weights = None
        else:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if balanced:
            return self._choose_balanced(
                candidates,
                count,
                replace=replace,
                weights=candidate_weights,
                groups=self.snapshot.rel_int[candidates],
            )
        return self._choose_candidates(candidates, count, replace=replace, weights=candidate_weights)

    def sample_layers(
        self,
        seed_nodes: Sequence[int] | np.ndarray,
        layers: Sequence[NeighborSamplingSpec],
        *,
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
    ) -> LayeredSampleBatch:
        """Sample a heterogeneous neighborhood while preserving layer blocks."""
        seeds = self._validated_ids(seed_nodes, self.snapshot.num_nodes, "seed_nodes")
        self._validate_sampling_options(
            fanout=0, direction="out", temporal=temporal,
            window_policy=window_policy, causal_policy="none",
        )
        frontier = seeds
        sampled_layers: list[SampledNeighbors] = []
        for layer in layers:
            if not isinstance(layer, NeighborSamplingSpec):
                raise TypeError("layers must contain NeighborSamplingSpec values")
            sampled = self.sample_neighbors(
                frontier,
                layer.fanout,
                direction=layer.direction,
                relations=layer.relations,
                replace=layer.replace,
                strategy=layer.strategy,
                balanced=layer.balanced,
                temporal=temporal,
                window_policy=window_policy,
            )
            sampled_layers.append(sampled)
            frontier = np.unique(sampled.neighbor_nodes)
        return LayeredSampleBatch(seed_nodes=seeds, layers=tuple(sampled_layers))

    def sample_layers_external(
        self,
        seed_nodes: Sequence[object],
        layers: Sequence[ExternalNeighborSamplingSpec],
        *,
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
    ) -> LayeredSampleBatch:
        """Sample heterogeneous layers using external node and relation IDs."""
        compact_layers: list[NeighborSamplingSpec] = []
        for layer in layers:
            if not isinstance(layer, ExternalNeighborSamplingSpec):
                raise TypeError("layers must contain ExternalNeighborSamplingSpec values")
            relations = None if layer.relations is None else self.snapshot.relation_ints(layer.relations)
            compact_layers.append(
                NeighborSamplingSpec(
                    fanout=layer.fanout,
                    direction=layer.direction,
                    relations=relations,
                    replace=layer.replace,
                    strategy=layer.strategy,
                    balanced=layer.balanced,
                )
            )
        return self.sample_layers(
            self.snapshot.node_ints(seed_nodes), compact_layers,
            temporal=temporal, window_policy=window_policy,
        )

    def sample_random_walks(
        self,
        start_nodes: Sequence[int] | np.ndarray,
        walk_length: int,
        *,
        direction: str = "out",
        relations: Sequence[int] | np.ndarray | None = None,
        metapath: Sequence[int] | np.ndarray | None = None,
        restart_probability: float = 0.0,
        strategy: str = "uniform",
        dead_end: str = "terminate",
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
    ) -> RandomWalkBatch:
        """Sample fixed-width first-order random walks.

        A metapath is repeated cyclically and advances only when an edge is
        traversed. Random restart and ``dead_end="restart"`` transitions move to
        the walk's start node and are represented by ``-1`` edge/relation IDs.
        ``relations`` and ``metapath`` are mutually exclusive.
        """
        _validate_count(walk_length, "walk_length")
        self._validate_sampling_options(
            fanout=0, direction=direction, temporal=temporal,
            window_policy=window_policy, causal_policy="none",
        )
        if strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if dead_end not in {"terminate", "restart"}:
            raise ValueError("dead_end must be 'terminate' or 'restart'")
        if isinstance(restart_probability, (bool, np.bool_)) or not isinstance(restart_probability, (int, float, np.integer, np.floating)):
            raise ValueError("restart_probability must be a number between 0 and 1")
        restart_probability = float(restart_probability)
        if not np.isfinite(restart_probability) or not 0.0 <= restart_probability <= 1.0:
            raise ValueError("restart_probability must be between 0 and 1")
        if relations is not None and metapath is not None:
            raise ValueError("relations and metapath are mutually exclusive")

        starts = self._validated_ids(start_nodes, self.snapshot.num_nodes, "start_nodes")
        relation_filter = None
        if relations is not None:
            relation_filter = set(self._validated_ids(relations, self.snapshot.num_relations, "relations").tolist())
        metapath_ids = None
        if metapath is not None:
            metapath_ids = self._validated_ids(metapath, self.snapshot.num_relations, "metapath")
            if metapath_ids.size == 0:
                raise ValueError("metapath must contain at least one relation")

        node_ids = np.full((starts.size, walk_length + 1), -1, dtype=np.int64)
        edge_indices = np.full((starts.size, walk_length), -1, dtype=np.int64)
        relation_ids = np.full((starts.size, walk_length), -1, dtype=np.int64)
        lengths = np.zeros(starts.size, dtype=np.int64)
        if starts.size:
            node_ids[:, 0] = starts

        for row, start in enumerate(starts.tolist()):
            current = int(start)
            traversed_edges = 0
            for step in range(walk_length):
                if restart_probability > 0 and self.rng.random() < restart_probability:
                    current = int(start)
                    node_ids[row, step + 1] = current
                    lengths[row] = step + 1
                    continue

                step_relations = relation_filter
                if metapath_ids is not None:
                    step_relations = {int(metapath_ids[traversed_edges % metapath_ids.size])}
                candidates = self._candidate_edges(current, direction, step_relations, temporal, window_policy)
                if candidates.size == 0:
                    if dead_end == "restart":
                        current = int(start)
                        node_ids[row, step + 1] = current
                        lengths[row] = step + 1
                        continue
                    break

                weights = self.snapshot.edge_weights[candidates] if strategy == "weighted" else None
                chosen = self._choose_candidates(candidates, 1, replace=False, weights=weights)
                edge = int(chosen[0])
                current = int(self._neighbors_for_edges(current, chosen, direction)[0])
                edge_indices[row, step] = edge
                relation_ids[row, step] = int(self.snapshot.rel_int[edge])
                node_ids[row, step + 1] = current
                lengths[row] = step + 1
                traversed_edges += 1

        return RandomWalkBatch(node_ids, edge_indices, relation_ids, lengths)

    def sample_random_walks_external(
        self,
        start_nodes: Sequence[object],
        walk_length: int,
        *,
        direction: str = "out",
        relations: Sequence[object] | None = None,
        metapath: Sequence[object] | None = None,
        restart_probability: float = 0.0,
        strategy: str = "uniform",
        dead_end: str = "terminate",
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
    ) -> RandomWalkBatch:
        """Sample random walks using external node and relation IDs as input."""
        compact_relations = None if relations is None else self.snapshot.relation_ints(relations)
        compact_metapath = None if metapath is None else self.snapshot.relation_ints(metapath)
        return self.sample_random_walks(
            self.snapshot.node_ints(start_nodes),
            walk_length,
            direction=direction,
            relations=compact_relations,
            metapath=compact_metapath,
            restart_probability=restart_probability,
            strategy=strategy,
            dead_end=dead_end,
            temporal=temporal,
            window_policy=window_policy,
        )

    def sample_multihop(
        self,
        seeds: Sequence[int] | np.ndarray,
        fanouts: Sequence[int],
        *,
        direction: str = "any",
        relations: Sequence[int] | np.ndarray | None = None,
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
        causal_policy: str = "none",
    ) -> SampledSubgraphBatch:
        """Sample a merged multihop subgraph around seed nodes.

        Args:
            seeds: Compact node IDs used as the initial frontier.
            fanouts: Fanout per hop. ``[15, 10]`` performs two hops.
            direction: Traversal direction for all hops.
            relations: Optional relation filter applied to all hops.
            temporal: Optional point or finite-window validity filter.
            window_policy: Window matching policy, ``"overlap"`` or
                ``"contained"``.
            causal_policy: Valid-start policy across each sampled path:
                ``"none"``, ``"nondecreasing"``, or ``"nonincreasing"``.

        Returns:
            ``SampledSubgraphBatch`` containing one merged sampled graph.

        Examples:
            Sample a two-hop undirected context::

                batch = engine.sample_multihop([u, v], [15, 10], direction="any")
        """
        for fanout in fanouts:
            self._validate_sampling_options(
                fanout=fanout, direction=direction, temporal=temporal,
                window_policy=window_policy, causal_policy=causal_policy,
            )
        return self._sample_multihop(
            seeds, fanouts, direction=direction, relations=relations, temporal=temporal,
            window_policy=window_policy, causal_policy=causal_policy,
        )

    def _sample_multihop(
        self, seeds, fanouts, *, direction, relations, temporal,
        window_policy, causal_policy, initial_states=None,
    ) -> SampledSubgraphBatch:
        seed_values = self._validated_ids(seeds, self.snapshot.num_nodes, "seeds").tolist()
        if relations is not None:
            relations = self._validated_ids(relations, self.snapshot.num_relations, "relations")
        nodes = {int(node) for node in seed_values}
        edges: set[int] = set()
        self._validate_sampling_options(
            fanout=0, direction=direction, temporal=temporal,
            window_policy=window_policy, causal_policy=causal_policy,
        )
        if causal_policy == "none":
            frontier = np.asarray(sorted(nodes), dtype=np.int64)
            for fanout in fanouts:
                sampled = self._sample_neighbors(
                    frontier, fanout, direction=direction, relations=relations,
                    temporal=temporal, window_policy=window_policy,
                )
                edges.update(int(edge) for edge in sampled.edge_indices.tolist())
                next_frontier = sorted({
                    int(node) for node in sampled.neighbor_nodes.tolist()
                    if int(node) not in nodes
                })
                nodes.update(next_frontier)
                frontier = np.asarray(next_frontier, dtype=np.int64)
                if frontier.size == 0:
                    break
            return self._batch_from_edges(
                nodes, edges, positives=np.empty((0, 3), dtype=np.int64),
                negatives=np.empty((0, 3), dtype=np.int64),
            )

        states = initial_states or [(node, None) for node in sorted(nodes)]
        for fanout in fanouts:
            if not states:
                break
            frontier = np.asarray([state[0] for state in states], dtype=np.int64)
            sampled = self._sample_neighbors(
                frontier, fanout, direction=direction, relations=relations,
                temporal=temporal, window_policy=window_policy, causal_policy=causal_policy,
                causal_starts=[state[1] for state in states] if causal_policy != "none" else None,
            )
            edges.update(int(edge) for edge in sampled.edge_indices.tolist())
            next_states = []
            for row in range(frontier.size):
                start, end = sampled.offsets[row:row + 2]
                for position in range(int(start), int(end)):
                    node = int(sampled.neighbor_nodes[position])
                    edge = int(sampled.edge_indices[position])
                    next_states.append((node, int(self.snapshot.valid_from_us[edge])))
            nodes.update(state[0] for state in next_states)
            states = list(dict.fromkeys(next_states))
        return self._batch_from_edges(nodes, edges, positives=np.empty((0, 3), dtype=np.int64), negatives=np.empty((0, 3), dtype=np.int64))

    def sample_subgraph(
        self,
        seed_edges: Sequence[int] | np.ndarray,
        fanouts: Sequence[int],
        *,
        direction: str = "any",
        relations: Sequence[int] | np.ndarray | None = None,
        negative_config: HardNegativeConfig | None = None,
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
        causal_policy: str = "none",
    ) -> SampledSubgraphBatch:
        """Sample a merged training subgraph around positive seed edges.

        Args:
            seed_edges: Compact edge IDs treated as positive triples.
            fanouts: Fanout per hop around the positive edge endpoints.
            direction: Traversal direction for context expansion.
            relations: Optional relation filter for context expansion.
            negative_config: Optional hard-negative configuration. When provided,
                negatives are returned as ``(positives, negatives_per_positive,
                3)`` local triples.
            temporal: Optional point or finite-window validity filter applied to
                seed and context edges.
            window_policy: Window matching policy, ``"overlap"`` or
                ``"contained"``.
            causal_policy: Valid-start policy propagated from each seed edge
                through context hops.

        Returns:
            ``SampledSubgraphBatch`` with local node IDs in positive and negative
            triples.

        Examples:
            Sample positives with hard negatives::

                batch = engine.sample_subgraph(
                    seed_edges,
                    fanouts=[12, 8],
                    negative_config=HardNegativeConfig(negatives_per_positive=8),
                )
        """
        self._validate_sampling_options(
            fanout=0, direction=direction, temporal=temporal,
            window_policy=window_policy, causal_policy=causal_policy,
        )
        for fanout in fanouts:
            self._validate_sampling_options(
                fanout=fanout, direction=direction, temporal=temporal,
                window_policy=window_policy, causal_policy=causal_policy,
            )
        seed_edges = self._validated_ids(seed_edges, self.snapshot.num_edges, "seed_edges")
        if temporal is not None:
            seed_edges = self._filter_temporal_edges(seed_edges, temporal, window_policy)
        seed_nodes = set(self.snapshot.src_int[seed_edges].astype(np.int64).tolist())
        seed_nodes.update(self.snapshot.dst_int[seed_edges].astype(np.int64).tolist())
        initial_states = None
        if causal_policy != "none":
            initial_states = []
            for edge in seed_edges.tolist():
                start = int(self.snapshot.valid_from_us[edge])
                initial_states.extend([(int(self.snapshot.src_int[edge]), start), (int(self.snapshot.dst_int[edge]), start)])
            initial_states = list(dict.fromkeys(initial_states))
        context_temporal = temporal
        if (
            context_temporal is None and negative_config is not None
            and self.snapshot.temporal and not negative_config.allow_future_candidates
            and seed_edges.size
        ):
            cutoff = int(np.min(self.snapshot.valid_from_us[seed_edges]))
            if negative_config.temporal_candidate_window_days is None:
                context_temporal = TemporalContext.as_of(cutoff)
            else:
                width = self._candidate_window_us(negative_config)
                context_temporal = TemporalContext.during(cutoff - width, cutoff + 1)
        batch = self._sample_multihop(
            sorted(seed_nodes), fanouts, direction=direction, relations=relations,
            temporal=context_temporal, window_policy=window_policy, causal_policy=causal_policy,
            initial_states=initial_states,
        )
        edge_set = set(batch.edge_ids_global.astype(np.int64).tolist())
        edge_set.update(seed_edges.astype(np.int64).tolist())
        positives = np.stack(
            [self.snapshot.src_int[seed_edges], self.snapshot.rel_int[seed_edges], self.snapshot.dst_int[seed_edges]],
            axis=1,
        ) if seed_edges.size else np.empty((0, 3), dtype=np.int64)
        nodes = set(batch.node_ids_global.astype(np.int64).tolist())
        nodes.update(positives[:, 0].astype(np.int64).tolist() if positives.size else [])
        nodes.update(positives[:, 2].astype(np.int64).tolist() if positives.size else [])
        positive_times = None
        diagnostics = None
        if self.snapshot.temporal and seed_edges.size:
            if temporal is not None and temporal.instant is not None:
                positive_times = np.full(seed_edges.size, temporal.instant.epoch_microseconds, dtype=np.int64)
            else:
                positive_times = self.snapshot.valid_from_us[seed_edges].astype(np.int64, copy=True)
        if negative_config is not None:
            negatives, diagnostics = self.sample_hard_negatives(
                positives, nodes, negative_config, positive_times_us=positive_times,
                temporal=temporal, return_diagnostics=True,
            )
        else:
            negatives = np.empty((0, 3), dtype=np.int64)
        if negatives.size:
            nodes.update(negatives[..., 0].astype(np.int64).ravel().tolist())
            nodes.update(negatives[..., 2].astype(np.int64).ravel().tolist())
        negative_times = None if positive_times is None or negative_config is None else np.repeat(
            positive_times[:, None], negative_config.negatives_per_positive, axis=1
        )
        return self._batch_from_edges(
            nodes, edge_set, positives=positives, negatives=negatives,
            positive_times=positive_times, negative_times=negative_times,
            negative_diagnostics=diagnostics,
        )

    def sample_subgraphs(self, *args, **kwargs) -> SampledSubgraphBatch:
        """Batched alias for ``sample_subgraph``."""
        return self.sample_subgraph(*args, **kwargs)

    def sample_subgraph_external(
        self,
        seed_edges: Sequence[object],
        fanouts: Sequence[int],
        *,
        direction: str = "any",
        relations: Sequence[object] | None = None,
        negative_config: HardNegativeConfig | None = None,
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
        causal_policy: str = "none",
    ) -> SampledSubgraphBatch:
        """Sample a training subgraph using external seed edge IDs."""
        compact_relations = None if relations is None else self.snapshot.relation_ints(relations)
        return self.sample_subgraph(
            self.snapshot.edge_ints(seed_edges),
            fanouts,
            direction=direction,
            relations=compact_relations,
            negative_config=negative_config,
            temporal=temporal,
            window_policy=window_policy,
            causal_policy=causal_policy,
        )

    def is_positive(
        self, src: int, rel: int, dst: int, *, temporal: TemporalContext | None = None,
        policy: str = "any_time", window_policy: str = "overlap",
    ) -> bool:
        """Return whether a triple is a known positive in the snapshot.

        Args:
            src: Compact source node ID.
            rel: Compact relation ID.
            dst: Compact target node ID.

        Returns:
            ``True`` if ``(src, rel, dst)`` exists in the snapshot positives.
        """
        self._validated_ids([src], self.snapshot.num_nodes, "src")
        self._validated_ids([rel], self.snapshot.num_relations, "rel")
        self._validated_ids([dst], self.snapshot.num_nodes, "dst")
        if policy not in {"any_time", "at_positive_time", "window"}:
            raise ValueError("policy must be 'any_time', 'at_positive_time', or 'window'")
        if window_policy not in {"overlap", "contained"}:
            raise ValueError("window_policy must be 'overlap' or 'contained'")
        code = int(self._pack_triple(src, rel, dst))
        if policy == "any_time":
            return code in self._positive_codes
        if temporal is None:
            raise ValueError("temporal positive membership requires a temporal context")
        if not self.snapshot.temporal:
            raise ValueError("temporal positive membership requires a format-v2 temporal snapshot")
        group = self._positive_history_groups.get(code)
        if group is None:
            return False
        start, end = self.snapshot.positive_history_indptr[group:group + 2]
        edges = self.snapshot.positive_history_edge_indices[int(start):int(end)]
        if policy == "at_positive_time":
            if temporal.instant is None:
                raise ValueError("at_positive_time requires an instant temporal context")
            instant = temporal.instant.epoch_microseconds
            return bool(np.any(
                (self.snapshot.valid_from_us[edges] <= instant)
                & (self.snapshot.valid_to_open[edges] | (instant < self.snapshot.valid_to_us[edges]))
            ))
        if temporal.interval is None:
            raise ValueError("window positive policy requires a window temporal context")
        window_start = temporal.interval.start.epoch_microseconds
        window_end = temporal.interval.end.epoch_microseconds
        if window_policy == "overlap":
            return bool(np.any(
                (self.snapshot.valid_from_us[edges] < window_end)
                & (self.snapshot.valid_to_open[edges] | (window_start < self.snapshot.valid_to_us[edges]))
            ))
        return bool(np.any(
            (window_start <= self.snapshot.valid_from_us[edges])
            & ~self.snapshot.valid_to_open[edges]
            & (self.snapshot.valid_to_us[edges] <= window_end)
        ))

    def sample_hard_negatives(
        self,
        positive_triples: Sequence[Sequence[int]] | np.ndarray,
        context_nodes: Iterable[int] | None = None,
        config: HardNegativeConfig | None = None,
        *,
        positive_times_us: Sequence[int] | np.ndarray | None = None,
        temporal: TemporalContext | None = None,
        return_diagnostics: bool = False,
    ) -> np.ndarray | tuple[np.ndarray, dict[str, int]]:
        """Generate grouped hard negatives for positive triples.

        Args:
            positive_triples: Array-like positive triples shaped ``(n, 3)`` using
                compact global IDs.
            context_nodes: Optional compact node IDs from the sampled context.
                Context-aware negative sources draw candidates from this set.
            config: Negative sampling configuration. Defaults to
                ``HardNegativeConfig()``.
            positive_times_us: Optional example instants aligned with positives.
                Required by example-time policies and by leakage-safe candidate
                filtering unless ``temporal`` provides the needed context.
            temporal: Optional point/window used by temporal positive rejection.
            return_diagnostics: Return ``(negatives, counters)`` when true.

        Returns:
            Array shaped ``(num_positives, negatives_per_positive, 3)`` using
            compact global IDs, optionally paired with rejection counters.

        Raises:
            ValueError: If ``positive_triples`` is not shaped ``(n, 3)``.
            RuntimeError: If a valid non-positive negative cannot be found within
                the configured retry budget.
        """
        config = config or HardNegativeConfig()
        raw_positives = np.asarray(positive_triples)
        if raw_positives.ndim != 2 or raw_positives.shape[1] != 3:
            raise ValueError("positive_triples must have shape (n, 3)")
        positives = _integer_array(raw_positives.reshape(-1), "positive_triples").reshape(raw_positives.shape)
        self._validated_ids(positives[:, 0], self.snapshot.num_nodes, "positive sources")
        self._validated_ids(positives[:, 1], self.snapshot.num_relations, "positive relations")
        self._validated_ids(positives[:, 2], self.snapshot.num_nodes, "positive destinations")
        diagnostics = {
            "attempted": 0, "accepted": 0, "rejected_self_loop": 0,
            "rejected_duplicate": 0, "rejected_type": 0,
            "rejected_unavailable": 0, "rejected_positive": 0, "repeated": 0,
        }
        if positives.size == 0 or config.negatives_per_positive == 0:
            empty = np.empty((positives.shape[0] if positives.ndim else 0, 0, 3), dtype=np.int64)
            return (empty, diagnostics) if return_diagnostics else empty
        context = (
            set(self._validated_ids(list(context_nodes), self.snapshot.num_nodes, "context_nodes").tolist())
            if context_nodes is not None else set()
        )
        times = None if positive_times_us is None else np.asarray(positive_times_us, dtype=np.int64)
        if times is not None and times.shape != (positives.shape[0],):
            raise ValueError("positive_times_us must align with positive_triples")
        if (
            self.snapshot.temporal and not config.allow_future_candidates
            and times is None and temporal is None
        ):
            raise ValueError(
                "temporal hard negatives require aligned positive_times_us or a temporal context "
                "when future candidates are disabled"
            )
        needs_time = config.temporal_positive_policy != "any_time" or config.temporal_candidate_window_days is not None
        if needs_time and not self.snapshot.temporal:
            raise ValueError("temporal hard negatives require a format-v2 temporal snapshot")
        window_context_suffices = (
            config.temporal_positive_policy == "window"
            and config.temporal_candidate_window_days is None
            and temporal is not None and temporal.interval is not None
        )
        if needs_time and times is None and (temporal is None or temporal.instant is None) and not window_context_suffices:
            raise ValueError("temporal hard negatives require aligned positive_times_us or an instant context")
        if times is None and temporal is not None and temporal.instant is not None:
            times = np.full(positives.shape[0], temporal.instant.epoch_microseconds, dtype=np.int64)
        result = np.empty((positives.shape[0], config.negatives_per_positive, 3), dtype=np.int64)
        candidate_cache: dict[tuple[int, int, int, bool, int | None], list[int]] = {}

        for row_idx, (src, rel, dst) in enumerate(positives.tolist()):
            group_seen: set[tuple[int, int, int]] = set()
            for neg_idx in range(config.negatives_per_positive):
                result[row_idx, neg_idx] = self._one_negative(
                    int(src), int(rel), int(dst), context, config, group_seen,
                    candidate_cache, None if times is None else int(times[row_idx]), temporal,
                    diagnostics,
                )
        return (result, diagnostics) if return_diagnostics else result

    def _candidate_edges(
        self, node: int, direction: str, relation_filter: set[int] | None,
        temporal: TemporalContext | None = None, window_policy: str = "overlap",
    ) -> np.ndarray:
        if temporal is not None:
            relations = range(self.snapshot.num_relations) if relation_filter is None else sorted(relation_filter)
            query_time = temporal.instant
            if temporal.interval is not None:
                query_time = temporal.interval.end.epoch_microseconds - 1
            ranges = []
            directions = ("out", "in") if direction == "any" else (direction,)
            for candidate_direction in directions:
                for relation in relations:
                    if 0 <= relation < self.snapshot.num_relations:
                        ranges.append(self.snapshot.temporal_candidates(
                            node, relation, query_time, direction=candidate_direction,
                        ))
            candidates = np.concatenate(ranges) if ranges else np.empty(0, dtype=np.int64)
            if direction == "any" and candidates.size:
                _, first_positions = np.unique(candidates, return_index=True)
                candidates = candidates[np.sort(first_positions)]
            return self._filter_temporal_edges(candidates, temporal, window_policy)
        if relation_filter is not None and direction in {"out", "in"}:
            relation_adj = self.snapshot.relation_out if direction == "out" else self.snapshot.relation_in
            ranges = []
            rel_count = max(1, self.snapshot.num_relations)
            for rel in sorted(relation_filter):
                if rel < 0 or rel >= self.snapshot.num_relations:
                    continue
                key = int(node) * rel_count + int(rel)
                ranges.append(relation_adj.edge_range(key))
            return np.concatenate(ranges) if ranges else np.empty(0, dtype=np.int64)

        if direction == "out":
            candidates = self.snapshot.out.edge_range(node)
        elif direction == "in":
            candidates = self.snapshot.in_.edge_range(node)
        else:
            candidates = self.snapshot.incident.edge_range(node)
            if candidates.size:
                _, first_positions = np.unique(candidates, return_index=True)
                candidates = candidates[np.sort(first_positions)]
        if relation_filter is None or candidates.size == 0:
            return candidates
        mask = np.isin(self.snapshot.rel_int[candidates], np.fromiter(relation_filter, dtype=np.int64))
        return candidates[mask]

    def _filter_temporal_edges(self, edges, temporal: TemporalContext, window_policy: str) -> np.ndarray:
        edges = np.asarray(edges, dtype=np.int64)
        if edges.size == 0:
            return edges
        starts = self.snapshot.valid_from_us[edges]
        ends = self.snapshot.valid_to_us[edges]
        open_ends = self.snapshot.valid_to_open[edges]
        if temporal.instant is not None:
            instant = temporal.instant.epoch_microseconds
            mask = (starts <= instant) & (open_ends | (instant < ends))
        else:
            window_start = temporal.interval.start.epoch_microseconds
            window_end = temporal.interval.end.epoch_microseconds
            if window_policy == "overlap":
                mask = (starts < window_end) & (open_ends | (window_start < ends))
            else:
                mask = (window_start <= starts) & ~open_ends & (ends <= window_end)
        return edges[mask]

    def _validate_sampling_options(self, *, fanout, direction, temporal, window_policy, causal_policy):
        _validate_count(fanout, "fanout")
        if direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if temporal is not None and not isinstance(temporal, TemporalContext):
            raise TypeError("temporal must be a TemporalContext")
        if window_policy not in {"overlap", "contained"}:
            raise ValueError("window_policy must be 'overlap' or 'contained'")
        if causal_policy not in {"none", "nondecreasing", "nonincreasing"}:
            raise ValueError("causal_policy must be 'none', 'nondecreasing', or 'nonincreasing'")
        if (temporal is not None or causal_policy != "none") and not self.snapshot.temporal:
            raise ValueError("temporal sampling requires a format-v2 temporal snapshot")

    def _temporal_arrays(self, edge_indices):
        if not self.snapshot.temporal:
            return (None, None, None, None)
        return (
            self.snapshot.edge_version_ids[edge_indices],
            self.snapshot.valid_from_us[edge_indices],
            self.snapshot.valid_to_us[edge_indices],
            self.snapshot.valid_to_open[edge_indices],
        )

    def _one_negative(
        self,
        src: int,
        rel: int,
        dst: int,
        context_nodes: set[int],
        config: HardNegativeConfig,
        group_seen: set[tuple[int, int, int]],
        candidate_cache: dict[tuple[int, int, int, bool, int | None], list[int]],
        positive_time: int | None,
        temporal: TemporalContext | None,
        diagnostics: dict[str, int],
    ) -> tuple[int, int, int]:
        first_direction = bool(self.rng.random() < config.head_probability)
        for corrupt_head in (first_direction, not first_direction):
            cache_key = (src, rel, dst, corrupt_head, positive_time)
            if cache_key not in candidate_cache:
                candidate_cache[cache_key] = self._negative_candidates(
                    src, rel, dst, context_nodes, corrupt_head, config, positive_time,
                    temporal,
                )
            candidates = candidate_cache[cache_key]
            for cand in candidates:
                candidate = (int(cand), rel, dst) if corrupt_head else (src, rel, int(cand))
                rejection = self._negative_rejection(
                    candidate, src, dst, corrupt_head, config, group_seen,
                    positive_time, temporal,
                )
                diagnostics["attempted"] += 1
                if rejection is None:
                    group_seen.add(candidate)
                    diagnostics["accepted"] += 1
                    return candidate
                diagnostics[rejection] += 1
            fallback = np.arange(self.snapshot.num_nodes, dtype=np.int64)
            self.rng.shuffle(fallback)
            for cand in fallback[:config.max_retries].tolist():
                candidate = (cand, rel, dst) if corrupt_head else (src, rel, cand)
                rejection = self._negative_rejection(
                    candidate, src, dst, corrupt_head, config, group_seen,
                    positive_time, temporal,
                )
                diagnostics["attempted"] += 1
                if rejection is None:
                    group_seen.add(candidate)
                    diagnostics["accepted"] += 1
                    return candidate
                diagnostics[rejection] += 1
        if config.exhaustion_policy == "repeat" and group_seen:
            diagnostics["repeated"] += 1
            diagnostics["accepted"] += 1
            return sorted(group_seen)[0]
        raise RuntimeError("failed to sample a hard negative that is not a known positive")

    def _negative_candidates(self, src: int, rel: int, dst: int, context_nodes: set[int], corrupt_head: bool, config: HardNegativeConfig, positive_time: int | None, query_temporal: TemporalContext | None) -> list[int]:
        if config.source == "random" or not context_nodes:
            return []
        if config.source not in {"non_visited_relation_neighbors", "context_relation_neighbors"}:
            raise ValueError("negative source must be 'random', 'non_visited_relation_neighbors', or 'context_relation_neighbors'")
        candidates: set[int] = set()
        direction = "in" if corrupt_head else "out"
        temporal = self._candidate_context(config, positive_time)
        if temporal is None and positive_time is None and not config.allow_future_candidates:
            temporal = query_temporal
        if config.candidate_fanout is not None:
            sampled = self.sample_neighbors(
                np.fromiter(sorted(context_nodes), dtype=np.int64),
                config.candidate_fanout,
                direction=direction,
                relations=[rel],
                temporal=temporal,
            )
            candidates.update(sampled.neighbor_nodes.astype(np.int64).tolist())
        else:
            for node in sorted(context_nodes):
                edge_indices = self._candidate_edges(node, direction, {rel}, temporal)
                if edge_indices.size == 0:
                    continue
                candidates.update(self._neighbors_for_edges(node, edge_indices, direction).astype(np.int64).tolist())
        if config.source == "non_visited_relation_neighbors":
            candidates.difference_update(context_nodes)
        candidates.discard(src)
        candidates.discard(dst)
        ordered = sorted(candidates)
        self.rng.shuffle(ordered)
        return ordered

    def _negative_rejection(
        self,
        candidate: tuple[int, int, int],
        positive_src: int,
        positive_dst: int,
        corrupt_head: bool,
        config: HardNegativeConfig,
        group_seen: set[tuple[int, int, int]],
        positive_time: int | None,
        temporal: TemporalContext | None,
    ) -> str | None:
        src, rel, dst = candidate
        if src == dst:
            return "rejected_self_loop"
        if candidate in group_seen:
            return "rejected_duplicate"
        if config.same_endpoint_type:
            original = positive_src if corrupt_head else positive_dst
            replacement = src if corrupt_head else dst
            if self.snapshot.node_type_ids[replacement] != self.snapshot.node_type_ids[original]:
                return "rejected_type"
        if config.relation_endpoint_types:
            expected_type = self.snapshot.relation_src_type_ids[rel] if corrupt_head else self.snapshot.relation_dst_type_ids[rel]
            replacement = src if corrupt_head else dst
            if expected_type >= 0 and self.snapshot.node_type_ids[replacement] != expected_type:
                return "rejected_type"
        replacement = src if corrupt_head else dst
        candidate_context = self._candidate_context(config, positive_time)
        if candidate_context is None and positive_time is None and not config.allow_future_candidates:
            candidate_context = temporal
        if candidate_context is not None and not self._node_available(replacement, candidate_context):
            return "rejected_unavailable"
        if config.reject_known_positives:
            positive_context = self._positive_context(config, positive_time, temporal)
            if self.is_positive(
                src, rel, dst, temporal=positive_context,
                policy=config.temporal_positive_policy,
                window_policy=config.temporal_positive_window_policy,
            ):
                return "rejected_positive"
        return None

    def _neighbors_for_edges(self, node: int, edge_indices: np.ndarray, direction: str) -> np.ndarray:
        if edge_indices.size == 0:
            return np.empty(0, dtype=np.int64)
        if direction == "out":
            return self.snapshot.dst_int[edge_indices].astype(np.int64, copy=False)
        if direction == "in":
            return self.snapshot.src_int[edge_indices].astype(np.int64, copy=False)
        src = self.snapshot.src_int[edge_indices]
        dst = self.snapshot.dst_int[edge_indices]
        return np.where(src == int(node), dst, src).astype(np.int64, copy=False)

    @staticmethod
    def _candidate_window_us(config: HardNegativeConfig) -> int:
        return int(config.temporal_candidate_window_days * 86_400_000_000)

    def _candidate_context(
        self, config: HardNegativeConfig, positive_time: int | None
    ) -> TemporalContext | None:
        if positive_time is None or config.allow_future_candidates:
            return None
        if config.temporal_candidate_window_days is None:
            return TemporalContext.as_of(positive_time)
        width = self._candidate_window_us(config)
        return TemporalContext.during(positive_time - width, positive_time + 1)

    def _positive_context(
        self,
        config: HardNegativeConfig,
        positive_time: int | None,
        temporal: TemporalContext | None,
    ) -> TemporalContext | None:
        if config.temporal_positive_policy == "any_time":
            return None
        if config.temporal_positive_policy == "at_positive_time":
            return TemporalContext.as_of(positive_time)
        if temporal is not None and temporal.interval is not None:
            return temporal
        if config.temporal_candidate_window_days is None:
            raise ValueError("window positive policy requires a temporal window or temporal_candidate_window_days")
        width = self._candidate_window_us(config)
        return TemporalContext.during(positive_time - width, positive_time + 1)

    def _node_available(self, node: int, temporal: TemporalContext | None) -> bool:
        if temporal is None:
            return True
        start = int(self.snapshot.node_history_indptr[node])
        end = int(self.snapshot.node_history_indptr[node + 1])
        if start == end:
            return False
        starts = self.snapshot.node_valid_from_us[start:end]
        ends = self.snapshot.node_valid_to_us[start:end]
        open_ends = self.snapshot.node_valid_to_open[start:end]
        if temporal.instant is not None:
            instant = temporal.instant.epoch_microseconds
            return bool(np.any((starts <= instant) & (open_ends | (instant < ends))))
        window_start = temporal.interval.start.epoch_microseconds
        window_end = temporal.interval.end.epoch_microseconds
        return bool(np.any((starts < window_end) & (open_ends | (window_start < ends))))

    def _choose_balanced(
        self,
        candidates: np.ndarray,
        count: int,
        *,
        replace: bool,
        weights: np.ndarray | None,
        groups: np.ndarray,
    ) -> np.ndarray:
        """Choose a deterministic equal share from each group.

        Groups are visited in sorted compact-ID order. When the requested
        ``count`` does not divide evenly, earlier groups receive one extra
        selection. A group smaller than its share returns all its candidates
        when ``replace=False``; unused quota is intentionally not redistributed
        so the quota contract stays explicit and reproducible.
        """
        _validate_count(count, "count")
        if count == 0 or candidates.size == 0:
            return np.empty(0, dtype=np.int64)
        if groups.shape != candidates.shape:
            raise ValueError("balanced groups must align with candidates")
        unique_groups = np.unique(groups)
        base, extra = divmod(count, int(unique_groups.size))
        selections: list[np.ndarray] = []
        for index, group in enumerate(unique_groups):
            quota = base + (1 if index < extra else 0)
            if quota == 0:
                continue
            mask = groups == group
            group_candidates = candidates[mask]
            group_weights = None if weights is None else weights[mask]
            if group_weights is not None and float(np.max(group_weights)) <= 0:
                continue
            selections.append(
                self._choose_candidates(
                    group_candidates, quota, replace=replace, weights=group_weights
                )
            )
        if not selections:
            return self._choose_candidates(
                candidates, count, replace=replace, weights=weights
            )
        return np.concatenate(selections).astype(np.int64, copy=False)

    def _choose_candidates(self, candidates: np.ndarray, count: int, *, replace: bool, weights: np.ndarray | None) -> np.ndarray:
        _validate_count(count, "count")
        if count == 0 or candidates.size == 0:
            return np.empty(0, dtype=np.int64)
        probabilities = None
        if weights is not None:
            maximum = float(np.max(weights))
            if maximum <= 0:
                raise ValueError("sampling weights must contain a positive value in the candidate set")
            positive = weights > 0
            candidates = candidates[positive]
            weights = weights[positive]
            scaled_weights = weights / float(np.max(weights))
            probabilities = scaled_weights / float(np.sum(scaled_weights))
        if not replace and candidates.size <= count:
            return candidates.astype(np.int64, copy=True)
        positions = self.rng.choice(candidates.size, size=count, replace=replace, p=probabilities)
        return candidates[positions].astype(np.int64, copy=False)

    @staticmethod
    def _validated_ids(values, upper_bound: int, name: str) -> np.ndarray:
        ids = _integer_array(values, name)
        if ids.size and (ids.min() < 0 or ids.max() >= upper_bound):
            raise ValueError(f"{name} contain IDs outside [0, {upper_bound})")
        return ids

    @staticmethod
    def _validated_nonnegative_ids(values, name: str) -> np.ndarray:
        ids = _integer_array(values, name)
        if ids.size and ids.min() < 0:
            raise ValueError(f"{name} must contain one-dimensional non-negative IDs")
        return ids

    @staticmethod
    def _validated_weights(values, expected_size: int, name: str) -> np.ndarray:
        weights = np.asarray(values, dtype=np.float64)
        if weights.ndim != 1 or weights.size != expected_size:
            raise ValueError(f"{name} length must be {expected_size}")
        if not np.all(np.isfinite(weights)) or np.any(weights < 0):
            raise ValueError(f"{name} must be finite and non-negative")
        return weights

    def _batch_from_edges(
        self, nodes: Iterable[int], edges: Iterable[int], *, positives: np.ndarray,
        negatives: np.ndarray, positive_times: np.ndarray | None = None,
        negative_times: np.ndarray | None = None,
        negative_diagnostics: dict[str, int] | None = None,
    ) -> SampledSubgraphBatch:
        ordered_nodes = np.asarray(sorted(set(int(node) for node in nodes)), dtype=np.int64)
        ordered_edges = np.asarray(sorted(set(int(edge) for edge in edges)), dtype=np.int64)
        local_src = np.searchsorted(ordered_nodes, self.snapshot.src_int[ordered_edges]) if ordered_edges.size else np.empty(0, dtype=np.int64)
        local_dst = np.searchsorted(ordered_nodes, self.snapshot.dst_int[ordered_edges]) if ordered_edges.size else np.empty(0, dtype=np.int64)

        def localize_triples(triples: np.ndarray) -> np.ndarray:
            if triples.size == 0:
                return np.empty(triples.shape, dtype=np.int64)
            localized = np.array(triples, dtype=np.int64, copy=True)
            localized[..., 0] = np.searchsorted(ordered_nodes, triples[..., 0])
            localized[..., 2] = np.searchsorted(ordered_nodes, triples[..., 2])
            return localized

        return SampledSubgraphBatch(
            node_ids_global=ordered_nodes,
            node_type_ids=self.snapshot.node_type_ids[ordered_nodes] if ordered_nodes.size else np.empty(0, dtype=np.int64),
            senders=local_src.astype(np.int64, copy=False),
            receivers=local_dst.astype(np.int64, copy=False),
            edge_ids_global=ordered_edges,
            edge_relation_ids=self.snapshot.rel_int[ordered_edges] if ordered_edges.size else np.empty(0, dtype=np.int64),
            positives=localize_triples(positives),
            negatives=localize_triples(negatives),
            edge_version_ids=self.snapshot.edge_version_ids[ordered_edges] if self.snapshot.temporal else None,
            valid_from_us=self.snapshot.valid_from_us[ordered_edges] if self.snapshot.temporal else None,
            valid_to_us=self.snapshot.valid_to_us[ordered_edges] if self.snapshot.temporal else None,
            valid_to_open=self.snapshot.valid_to_open[ordered_edges] if self.snapshot.temporal else None,
            positive_time_us=positive_times,
            negative_time_us=negative_times,
            negative_diagnostics=negative_diagnostics,
        )

    def _pack_triple(self, src: int, rel: int, dst: int) -> np.uint64:
        num_relations = max(1, self.snapshot.num_relations)
        num_nodes = max(1, self.snapshot.num_nodes)
        return np.uint64((int(src) * num_relations + int(rel)) * num_nodes + int(dst))

    def _build_positive_codes(self) -> set[int]:
        triples = self.snapshot.positive_triples
        if triples.size == 0:
            return set()
        max_code = (self.snapshot.num_nodes * max(1, self.snapshot.num_relations) * self.snapshot.num_nodes) - 1
        if max_code > np.iinfo(np.uint64).max:
            raise OverflowError("positive triple packing exceeds uint64")
        codes = [int(self._pack_triple(src, rel, dst)) for src, rel, dst in triples.tolist()]
        return set(codes)

    def _build_positive_history_groups(self) -> dict[int, int]:
        if not self.snapshot.temporal:
            return {}
        return {
            int(self._pack_triple(src, rel, dst)): index
            for index, (src, rel, dst) in enumerate(
                self.snapshot.positive_history_triples.tolist()
            )
        }

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


def _external_id_array(compact_ids: np.ndarray, external_ids: np.ndarray) -> np.ndarray:
    compact_ids = np.asarray(compact_ids, dtype=np.int64)
    result = np.full(compact_ids.shape, None, dtype=object)
    valid = compact_ids >= 0
    if np.any(valid):
        selected = compact_ids[valid]
        if selected.max() >= external_ids.size:
            raise ValueError("compact IDs are outside the snapshot ID range")
        result[valid] = external_ids[selected]
    return result
