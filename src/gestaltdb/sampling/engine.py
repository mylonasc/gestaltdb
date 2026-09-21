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
        temporal: TemporalContext | None = None,
        window_policy: str = "overlap",
        causal_policy: str = "none",
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
    ) -> SampledNeighbors:
        input_nodes = np.asarray(nodes, dtype=np.int64)
        if causal_starts is not None and len(causal_starts) != input_nodes.size:
            raise ValueError("causal_starts must align with input nodes")
        offsets = [0]
        sampled_edges: list[np.ndarray] = []
        sampled_neighbors: list[np.ndarray] = []
        relation_filter = None if relations is None else set(np.asarray(relations, dtype=np.int64).tolist())

        for row, node in enumerate(input_nodes.tolist()):
            candidates = self._candidate_edges(node, direction, relation_filter, temporal, window_policy)
            if causal_starts is not None and causal_starts[row] is not None and candidates.size:
                starts = self.snapshot.valid_from_us[candidates]
                if causal_policy == "nondecreasing":
                    candidates = candidates[starts >= causal_starts[row]]
                elif causal_policy == "nonincreasing":
                    candidates = candidates[starts <= causal_starts[row]]
            if fanout == 0 or candidates.size == 0:
                chosen = np.empty(0, dtype=np.int64)
            elif replace:
                positions = self.rng.integers(candidates.size, size=fanout)
                chosen = candidates[positions]
            elif candidates.size <= fanout:
                chosen = candidates
            else:
                positions = self.rng.choice(candidates.size, size=fanout, replace=False)
                chosen = candidates[positions]
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
        seed_values = np.asarray(seeds, dtype=np.int64).tolist()
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
                next_frontier = [
                    int(node) for node in sampled.neighbor_nodes.tolist()
                    if int(node) not in nodes
                ]
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
        seed_edges = np.asarray(seed_edges, dtype=np.int64)
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
        batch = self._sample_multihop(
            sorted(seed_nodes), fanouts, direction=direction, relations=relations,
            temporal=temporal, window_policy=window_policy, causal_policy=causal_policy,
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
        positives = np.asarray(positive_triples, dtype=np.int64)
        if positives.size == 0 or config.negatives_per_positive == 0:
            return np.empty((positives.shape[0] if positives.ndim else 0, 0, 3), dtype=np.int64)
        if positives.ndim != 2 or positives.shape[1] != 3:
            raise ValueError("positive_triples must have shape (n, 3)")
        context = set(int(node) for node in context_nodes) if context_nodes is not None else set()
        result = np.empty((positives.shape[0], config.negatives_per_positive, 3), dtype=np.int64)
        candidate_cache: dict[tuple[int, bool], list[int]] = {}

        for row_idx, (src, rel, dst) in enumerate(positives.tolist()):
            group_seen: set[tuple[int, int, int]] = set()
            for neg_idx in range(config.negatives_per_positive):
                result[row_idx, neg_idx] = self._one_negative(int(src), int(rel), int(dst), context, config, group_seen, candidate_cache)
        return result

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
            return self._filter_temporal_edges(candidates, temporal, window_policy)
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
        if fanout < 0:
            raise ValueError("fanout must be non-negative")
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
        candidate_cache: dict[tuple[int, bool], list[int]],
    ) -> tuple[int, int, int]:
        first_direction = bool(self.rng.random() < config.head_probability)
        for corrupt_head in (first_direction, not first_direction):
            cache_key = (rel, corrupt_head)
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
            edge_version_ids=self.snapshot.edge_version_ids[ordered_edges] if self.snapshot.temporal else None,
            valid_from_us=self.snapshot.valid_from_us[ordered_edges] if self.snapshot.temporal else None,
            valid_to_us=self.snapshot.valid_to_us[ordered_edges] if self.snapshot.temporal else None,
            valid_to_open=self.snapshot.valid_to_open[ordered_edges] if self.snapshot.temporal else None,
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
