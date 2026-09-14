"""Array-native sampler engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

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

    def __post_init__(self):
        _validate_count(self.fanout, "fanout")
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if self.strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if self.relations is not None:
            relations = _integer_array(self.relations, "relations")
            object.__setattr__(self, "relations", tuple(int(rel) for rel in relations))


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


@dataclass(slots=True)
class LayeredSampleBatch:
    """Neighbor samples with one result block preserved per requested layer."""

    seed_nodes: np.ndarray
    layers: tuple[SampledNeighbors, ...]


class SamplerEngine:
    """High-throughput sampler over a static ``SamplerSnapshot``.

    The engine samples compact integer arrays instead of materializing GraphDB
    ``Node``/``Edge`` objects. It supports direction-aware traversal,
    relation-aware traversal, multihop subgraph sampling, exact positive triple
    checks, and configurable hard-negative generation.

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
        if snapshot.edge_weights.size == 0 and snapshot.num_edges:
            snapshot.edge_weights = np.ones(snapshot.num_edges, dtype=np.float64)
        else:
            snapshot.edge_weights = self._validated_weights(snapshot.edge_weights, snapshot.num_edges, "snapshot.edge_weights")
        self.rng = np.random.default_rng(seed)
        self._positive_codes = self._build_positive_codes()

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

    def sample_neighbors(
        self,
        nodes: Sequence[int] | np.ndarray,
        fanout: int,
        *,
        direction: str = "out",
        relations: Sequence[int] | np.ndarray | None = None,
        replace: bool = False,
        strategy: str = "uniform",
    ) -> SampledNeighbors:
        """Sample neighbors for each input node.

        Args:
            nodes: Compact node IDs to sample from.
            fanout: Maximum number of edges sampled per node.
            direction: ``"out"`` for source-to-target, ``"in"`` for
                target-to-source, or ``"any"`` for incident traversal.
            relations: Optional compact relation IDs to restrict traversal.
            replace: Whether to sample with replacement.
            strategy: ``"uniform"`` or ``"weighted"``. Weighted sampling uses
                the snapshot's edge weights.

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
        _validate_count(fanout, "fanout")
        if direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        input_nodes = self._validated_ids(nodes, self.snapshot.num_nodes, "nodes")
        offsets = [0]
        sampled_edges: list[np.ndarray] = []
        sampled_neighbors: list[np.ndarray] = []
        relation_filter = None
        if relations is not None:
            relation_filter = set(self._validated_ids(relations, self.snapshot.num_relations, "relations").tolist())

        for node in input_nodes.tolist():
            candidates = self._candidate_edges(node, direction, relation_filter)
            weights = self.snapshot.edge_weights[candidates] if strategy == "weighted" else None
            chosen = self._choose_candidates(candidates, fanout, replace=replace, weights=weights)
            sampled_edges.append(chosen.astype(np.int64, copy=False))
            sampled_neighbors.append(self._neighbors_for_edges(node, chosen, direction))
            offsets.append(offsets[-1] + int(chosen.size))

        edge_indices = np.concatenate(sampled_edges) if sampled_edges else np.empty(0, dtype=np.int64)
        neighbor_nodes = np.concatenate(sampled_neighbors) if sampled_neighbors else np.empty(0, dtype=np.int64)
        return SampledNeighbors(input_nodes, edge_indices, neighbor_nodes, np.asarray(offsets, dtype=np.int64))

    def sample_nodes(
        self,
        count: int,
        *,
        node_types: Sequence[int] | np.ndarray | None = None,
        replace: bool = False,
        strategy: str = "uniform",
        weights: Sequence[float] | np.ndarray | None = None,
    ) -> np.ndarray:
        """Sample compact node IDs, optionally restricted by node type."""
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
        return self._choose_candidates(candidates, count, replace=replace, weights=weights_array)

    def sample_edges(
        self,
        count: int,
        *,
        relations: Sequence[int] | np.ndarray | None = None,
        replace: bool = False,
        strategy: str = "uniform",
        weights: Sequence[float] | np.ndarray | None = None,
    ) -> np.ndarray:
        """Sample compact edge IDs, optionally restricted by relation."""
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
        return self._choose_candidates(candidates, count, replace=replace, weights=candidate_weights)

    def sample_layers(
        self,
        seed_nodes: Sequence[int] | np.ndarray,
        layers: Sequence[NeighborSamplingSpec],
    ) -> LayeredSampleBatch:
        """Sample a heterogeneous neighborhood while preserving layer blocks."""
        seeds = self._validated_ids(seed_nodes, self.snapshot.num_nodes, "seed_nodes")
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
            )
            sampled_layers.append(sampled)
            frontier = np.unique(sampled.neighbor_nodes)
        return LayeredSampleBatch(seed_nodes=seeds, layers=tuple(sampled_layers))

    def sample_multihop(
        self,
        seeds: Sequence[int] | np.ndarray,
        fanouts: Sequence[int],
        *,
        direction: str = "any",
        relations: Sequence[int] | np.ndarray | None = None,
    ) -> SampledSubgraphBatch:
        """Sample a merged multihop subgraph around seed nodes.

        Args:
            seeds: Compact node IDs used as the initial frontier.
            fanouts: Fanout per hop. ``[15, 10]`` performs two hops.
            direction: Traversal direction for all hops.
            relations: Optional relation filter applied to all hops.

        Returns:
            ``SampledSubgraphBatch`` containing one merged sampled graph.

        Examples:
            Sample a two-hop undirected context::

                batch = engine.sample_multihop([u, v], [15, 10], direction="any")
        """
        seed_ids = self._validated_ids(seeds, self.snapshot.num_nodes, "seeds")
        nodes = {int(node) for node in seed_ids.tolist()}
        edges: set[int] = set()
        frontier = np.asarray(sorted(nodes), dtype=np.int64)
        for fanout in fanouts:
            sampled = self.sample_neighbors(frontier, fanout, direction=direction, relations=relations)
            edges.update(int(edge) for edge in sampled.edge_indices.tolist())
            next_frontier = sorted({int(node) for node in sampled.neighbor_nodes.tolist() if int(node) not in nodes})
            nodes.update(next_frontier)
            frontier = np.asarray(next_frontier, dtype=np.int64)
            if frontier.size == 0:
                break
        return self._batch_from_edges(nodes, edges, positives=np.empty((0, 3), dtype=np.int64), negatives=np.empty((0, 3), dtype=np.int64))

    def sample_subgraph(
        self,
        seed_edges: Sequence[int] | np.ndarray,
        fanouts: Sequence[int],
        *,
        direction: str = "any",
        relations: Sequence[int] | np.ndarray | None = None,
        negative_config: HardNegativeConfig | None = None,
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
        seed_edges = self._validated_ids(seed_edges, self.snapshot.num_edges, "seed_edges")
        seed_nodes = set(self.snapshot.src_int[seed_edges].astype(np.int64).tolist())
        seed_nodes.update(self.snapshot.dst_int[seed_edges].astype(np.int64).tolist())
        batch = self.sample_multihop(sorted(seed_nodes), fanouts, direction=direction, relations=relations)
        edge_set = set(batch.edge_ids_global.astype(np.int64).tolist())
        edge_set.update(seed_edges.astype(np.int64).tolist())
        positives = np.stack(
            [self.snapshot.src_int[seed_edges], self.snapshot.rel_int[seed_edges], self.snapshot.dst_int[seed_edges]],
            axis=1,
        ) if seed_edges.size else np.empty((0, 3), dtype=np.int64)
        nodes = set(batch.node_ids_global.astype(np.int64).tolist())
        nodes.update(positives[:, 0].astype(np.int64).tolist() if positives.size else [])
        nodes.update(positives[:, 2].astype(np.int64).tolist() if positives.size else [])
        negatives = self.sample_hard_negatives(positives, nodes, negative_config) if negative_config is not None else np.empty((0, 3), dtype=np.int64)
        if negatives.size:
            nodes.update(negatives[..., 0].astype(np.int64).ravel().tolist())
            nodes.update(negatives[..., 2].astype(np.int64).ravel().tolist())
        return self._batch_from_edges(nodes, edge_set, positives=positives, negatives=negatives)

    def sample_subgraphs(self, *args, **kwargs) -> SampledSubgraphBatch:
        """Batched alias for ``sample_subgraph``."""
        return self.sample_subgraph(*args, **kwargs)

    def is_positive(self, src: int, rel: int, dst: int) -> bool:
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
        return int(self._pack_triple(src, rel, dst)) in self._positive_codes

    def sample_hard_negatives(
        self,
        positive_triples: Sequence[Sequence[int]] | np.ndarray,
        context_nodes: Iterable[int] | None = None,
        config: HardNegativeConfig | None = None,
    ) -> np.ndarray:
        """Generate grouped hard negatives for positive triples.

        Args:
            positive_triples: Array-like positive triples shaped ``(n, 3)`` using
                compact global IDs.
            context_nodes: Optional compact node IDs from the sampled context.
                Context-aware negative sources draw candidates from this set.
            config: Negative sampling configuration. Defaults to
                ``HardNegativeConfig()``.

        Returns:
            Array shaped ``(num_positives, negatives_per_positive, 3)`` using
            compact global IDs.

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
        if positives.size == 0 or config.negatives_per_positive == 0:
            return np.empty((positives.shape[0], 0, 3), dtype=np.int64)
        context = (
            set(self._validated_ids(list(context_nodes), self.snapshot.num_nodes, "context_nodes").tolist())
            if context_nodes is not None
            else set()
        )
        result = np.empty((positives.shape[0], config.negatives_per_positive, 3), dtype=np.int64)
        candidate_cache: dict[tuple[int, int, int, bool], list[int]] = {}

        for row_idx, (src, rel, dst) in enumerate(positives.tolist()):
            group_seen: set[tuple[int, int, int]] = set()
            for neg_idx in range(config.negatives_per_positive):
                result[row_idx, neg_idx] = self._one_negative(int(src), int(rel), int(dst), context, config, group_seen, candidate_cache)
        return result

    def _candidate_edges(self, node: int, direction: str, relation_filter: set[int] | None) -> np.ndarray:
        if relation_filter is not None and direction in {"out", "in"}:
            relation_adj = self.snapshot.relation_out if direction == "out" else self.snapshot.relation_in
            ranges = []
            rel_count = max(1, self.snapshot.num_relations)
            for rel in relation_filter:
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

    def _one_negative(
        self,
        src: int,
        rel: int,
        dst: int,
        context_nodes: set[int],
        config: HardNegativeConfig,
        group_seen: set[tuple[int, int, int]],
        candidate_cache: dict[tuple[int, int, int, bool], list[int]],
    ) -> tuple[int, int, int]:
        first_direction = bool(self.rng.random() < config.head_probability)
        for corrupt_head in (first_direction, not first_direction):
            cache_key = (src, rel, dst, corrupt_head)
            if cache_key not in candidate_cache:
                candidate_cache[cache_key] = self._negative_candidates(src, rel, dst, context_nodes, corrupt_head, config)
            candidates = candidate_cache[cache_key]
            for cand in candidates:
                candidate = (int(cand), rel, dst) if corrupt_head else (src, rel, int(cand))
                if self._accept_negative(candidate, src, dst, corrupt_head, config, group_seen):
                    group_seen.add(candidate)
                    return candidate
            for _ in range(config.max_retries):
                cand = int(self.rng.integers(self.snapshot.num_nodes))
                candidate = (cand, rel, dst) if corrupt_head else (src, rel, cand)
                if self._accept_negative(candidate, src, dst, corrupt_head, config, group_seen):
                    group_seen.add(candidate)
                    return candidate
        raise RuntimeError("failed to sample a hard negative that is not a known positive")

    def _negative_candidates(self, src: int, rel: int, dst: int, context_nodes: set[int], corrupt_head: bool, config: HardNegativeConfig) -> list[int]:
        if config.source == "random" or not context_nodes:
            return []
        if config.source not in {"non_visited_relation_neighbors", "context_relation_neighbors"}:
            raise ValueError("negative source must be 'random', 'non_visited_relation_neighbors', or 'context_relation_neighbors'")
        candidates: set[int] = set()
        direction = "in" if corrupt_head else "out"
        if config.candidate_fanout is not None:
            sampled = self.sample_neighbors(
                np.fromiter(context_nodes, dtype=np.int64),
                config.candidate_fanout,
                direction=direction,
                relations=[rel],
            )
            candidates.update(sampled.neighbor_nodes.astype(np.int64).tolist())
        else:
            for node in context_nodes:
                edge_indices = self._candidate_edges(node, direction, {rel})
                if edge_indices.size == 0:
                    continue
                candidates.update(self._neighbors_for_edges(node, edge_indices, direction).astype(np.int64).tolist())
        if config.source == "non_visited_relation_neighbors":
            candidates.difference_update(context_nodes)
        candidates.discard(src)
        candidates.discard(dst)
        ordered = list(candidates)
        self.rng.shuffle(ordered)
        return ordered

    def _accept_negative(
        self,
        candidate: tuple[int, int, int],
        positive_src: int,
        positive_dst: int,
        corrupt_head: bool,
        config: HardNegativeConfig,
        group_seen: set[tuple[int, int, int]],
    ) -> bool:
        src, rel, dst = candidate
        if src == dst or candidate in group_seen:
            return False
        if config.same_endpoint_type:
            original = positive_src if corrupt_head else positive_dst
            replacement = src if corrupt_head else dst
            if self.snapshot.node_type_ids[replacement] != self.snapshot.node_type_ids[original]:
                return False
        if config.relation_endpoint_types:
            expected_type = self.snapshot.relation_src_type_ids[rel] if corrupt_head else self.snapshot.relation_dst_type_ids[rel]
            replacement = src if corrupt_head else dst
            if expected_type >= 0 and self.snapshot.node_type_ids[replacement] != expected_type:
                return False
        if config.reject_known_positives and self.is_positive(src, rel, dst):
            return False
        return True

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

    def _batch_from_edges(self, nodes: Iterable[int], edges: Iterable[int], *, positives: np.ndarray, negatives: np.ndarray) -> SampledSubgraphBatch:
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
