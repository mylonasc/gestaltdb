from __future__ import annotations

import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import polars as pl

SRC_PATH = Path(__file__).resolve().parents[2] / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gestaltdb.sampling import HardNegativeConfig, SamplerEngine, SamplerSnapshot

from .config import AppConfig


@dataclass(slots=True)
class SampledBatch:
    node_ids: np.ndarray
    node_global_ids: np.ndarray
    node_type_ids: np.ndarray
    senders: np.ndarray
    receivers: np.ndarray
    edge_rel_ids: np.ndarray
    positive_src: np.ndarray
    positive_dst: np.ndarray
    positive_rel: np.ndarray
    negative_src: np.ndarray
    negative_dst: np.ndarray
    negative_rel: np.ndarray


def _decode(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class KGNeighborhoodSampler:
    def __init__(self, cfg: AppConfig, graphdb):
        self.cfg = cfg
        self.graphdb = graphdb
        arrays = np.load(cfg.paths.edge_arrays_path, allow_pickle=True)
        self.src = arrays["src"].astype(np.int64)
        self.dst = arrays["dst"].astype(np.int64)
        self.rel = arrays["rel"].astype(np.int64)
        self.node1_type = arrays["node1_type"].astype(str)
        self.node2_type = arrays["node2_type"].astype(str)
        node_map = pl.read_parquet(cfg.paths.node_map_path).sort("node_int")
        rel_map = pl.read_parquet(cfg.paths.relation_map_path).sort("rel_int")
        self.int_to_node = node_map["node_id"].to_list()
        self.node_to_int = {node_id: idx for idx, node_id in enumerate(self.int_to_node)}
        self.node_kinds = node_map["kind"].to_list()
        kinds = sorted({kind for kind in self.node_kinds if kind is not None})
        self.kind_to_int = {kind: idx for idx, kind in enumerate(kinds)}
        self.node_kind_int = np.array([self.kind_to_int.get(kind, 0) for kind in self.node_kinds], dtype=np.int64)
        self.int_to_rel = rel_map["edge_type"].to_list()
        self.rel_to_int = {rel: idx for idx, rel in enumerate(self.int_to_rel)}
        self.positive_triples = set(zip(self.src.tolist(), self.rel.tolist(), self.dst.tolist()))
        self.by_node: dict[int, list[int]] = defaultdict(list)
        for idx, (s, d) in enumerate(zip(self.src, self.dst)):
            self.by_node[int(s)].append(idx)
            self.by_node[int(d)].append(idx)
        self.rng = random.Random(cfg.sampler.seed)

    @property
    def num_nodes(self) -> int:
        return len(self.int_to_node)

    @property
    def num_relations(self) -> int:
        return len(self.int_to_rel)

    @property
    def num_node_types(self) -> int:
        return max(1, len(self.kind_to_int))

    def _sample_typed_records(self, node_int: int, fanout: int) -> list[dict[str, bytes]]:
        node_id = self.int_to_node[node_int]
        records: list[dict[str, bytes]] = []
        rels = self.rng.sample(self.int_to_rel, min(len(self.int_to_rel), max(1, fanout)))
        per_rel = max(1, fanout // max(1, len(rels)))
        for rel in rels:
            records.extend(
                self.graphdb.sample_neighbors(
                    node_id,
                    rel,
                    direction=self.cfg.sampler.bfs_direction,
                    sample_size=per_rel,
                    rng=self.rng,
                )
            )
        return records

    def _two_hop_context(self, seeds: Iterable[int]) -> tuple[set[int], set[tuple[int, int, int]]]:
        visited_nodes = {int(seed) for seed in seeds}
        visited_edges: set[tuple[int, int, int]] = set()
        frontier = list(visited_nodes)
        for hop, fanout in enumerate((self.cfg.sampler.hop1_fanout, self.cfg.sampler.hop2_fanout)):
            next_frontier: list[int] = []
            for node_int in frontier:
                for rec in self._sample_typed_records(node_int, fanout):
                    neighbor_id = _decode(rec["neighbor_id"])
                    if neighbor_id not in self.node_to_int:
                        continue
                    nb_int = self.node_to_int[neighbor_id]
                    src_id = _decode(rec["source_id"])
                    dst_id = _decode(rec["target_id"])
                    rel_id = _decode(rec.get("edge_type", rec.get("type", b"")))
                    src_int = self.node_to_int.get(src_id)
                    dst_int = self.node_to_int.get(dst_id)
                    rel_int = self.rel_to_int.get(rel_id)
                    if src_int is not None and dst_int is not None and rel_int is not None:
                        visited_edges.add((src_int, rel_int, dst_int))
                    if nb_int not in visited_nodes:
                        visited_nodes.add(nb_int)
                        next_frontier.append(nb_int)
            frontier = next_frontier
            if not frontier and hop == 0:
                break
        return visited_nodes, visited_edges

    def _non_visited_relation_neighbors(self, visited_nodes: set[int]) -> list[int]:
        candidates: set[int] = set()
        for node_int in list(visited_nodes):
            for rec in self._sample_typed_records(node_int, self.cfg.sampler.negative_candidate_fanout):
                neighbor_id = _decode(rec["neighbor_id"])
                neighbor_int = self.node_to_int.get(neighbor_id)
                if neighbor_int is not None and neighbor_int not in visited_nodes:
                    candidates.add(neighbor_int)
        return list(candidates)

    def _hard_negative(self, src: int, rel: int, dst: int, src_type: str, dst_type: str, candidate_nodes: list[int]) -> tuple[int, int, int]:
        corrupt_head = self.rng.random() < self.cfg.sampler.hard_negative_head_probability
        local_candidates = list(candidate_nodes)
        self.rng.shuffle(local_candidates)
        for _ in range(self.cfg.sampler.max_negative_retries):
            if local_candidates:
                cand = local_candidates.pop()
            else:
                cand = self.rng.randrange(self.num_nodes)
            if corrupt_head:
                candidate = (cand, rel, dst)
                if self.cfg.sampler.same_endpoint_type_negatives and self.node_kinds[cand] != src_type:
                    continue
            else:
                candidate = (src, rel, cand)
                if self.cfg.sampler.same_endpoint_type_negatives and self.node_kinds[cand] != dst_type:
                    continue
            if candidate not in self.positive_triples:
                return candidate
        for _ in range(self.cfg.sampler.max_negative_retries * 4):
            cand = self.rng.randrange(self.num_nodes)
            candidate = (cand, rel, dst) if corrupt_head else (src, rel, cand)
            if candidate not in self.positive_triples:
                return candidate
        raise RuntimeError("failed to sample a hard negative that is not a known positive")

    def sample_batch(self) -> SampledBatch:
        edge_indices = [self.rng.randrange(len(self.src)) for _ in range(self.cfg.sampler.batch_size)]
        graph_nodes: set[int] = set()
        graph_edges: set[tuple[int, int, int]] = set()
        positives: list[tuple[int, int, int]] = []
        negatives: list[list[tuple[int, int, int]]] = []
        for edge_idx in edge_indices:
            s, d, r = int(self.src[edge_idx]), int(self.dst[edge_idx]), int(self.rel[edge_idx])
            positives.append((s, r, d))
            visited_nodes, visited_edges = self._two_hop_context((s, d))
            negative_candidates = self._non_visited_relation_neighbors(visited_nodes)
            graph_nodes.update(visited_nodes)
            graph_edges.update(visited_edges)
            graph_edges.add((s, r, d))
            negs = [
                self._hard_negative(s, r, d, self.node1_type[edge_idx], self.node2_type[edge_idx], negative_candidates)
                for _ in range(self.cfg.sampler.negatives_per_positive)
            ]
            negatives.append(negs)
            for ns, nr, nd in negs:
                graph_nodes.add(ns)
                graph_nodes.add(nd)
                graph_edges.add((ns, nr, nd))

        ordered_nodes = np.array(sorted(graph_nodes), dtype=np.int64)
        local = {node: idx for idx, node in enumerate(ordered_nodes.tolist())}
        ordered_edges = sorted(graph_edges)
        return SampledBatch(
            node_ids=np.arange(len(ordered_nodes), dtype=np.int64),
            node_global_ids=ordered_nodes,
            node_type_ids=self.node_kind_int[ordered_nodes],
            senders=np.array([local[s] for s, _, _ in ordered_edges], dtype=np.int64),
            receivers=np.array([local[d] for _, _, d in ordered_edges], dtype=np.int64),
            edge_rel_ids=np.array([r for _, r, _ in ordered_edges], dtype=np.int64),
            positive_src=np.array([local[s] for s, _, _ in positives], dtype=np.int64),
            positive_dst=np.array([local[d] for _, _, d in positives], dtype=np.int64),
            positive_rel=np.array([r for _, r, _ in positives], dtype=np.int64),
            negative_src=np.array([[local[s] for s, _, _ in row] for row in negatives], dtype=np.int64),
            negative_dst=np.array([[local[d] for _, _, d in row] for row in negatives], dtype=np.int64),
            negative_rel=np.array([[r for _, r, _ in row] for row in negatives], dtype=np.int64),
        )


class ArrayKGNeighborhoodSampler:
    """High-throughput sampler over compact edge arrays.

    GestaltDB is still used to prepare and persist the selected KG. Training uses
    this array backend to avoid thousands of small random KV lookups per batch.
    """

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        arrays = np.load(cfg.paths.edge_arrays_path, allow_pickle=True)
        self.src = arrays["src"].astype(np.int64)
        self.dst = arrays["dst"].astype(np.int64)
        self.rel = arrays["rel"].astype(np.int64)
        self.node1_type = arrays["node1_type"].astype(str)
        self.node2_type = arrays["node2_type"].astype(str)
        node_map = pl.read_parquet(cfg.paths.node_map_path).sort("node_int")
        rel_map = pl.read_parquet(cfg.paths.relation_map_path).sort("rel_int")
        self.node_kinds = np.asarray(node_map["kind"].to_list(), dtype=object)
        kinds = sorted({kind for kind in self.node_kinds.tolist() if kind is not None})
        self.kind_to_int = {kind: idx for idx, kind in enumerate(kinds)}
        self.node_kind_int = np.array([self.kind_to_int.get(kind, 0) for kind in self.node_kinds], dtype=np.int64)
        self.num_edges = len(self.src)
        self._num_nodes = len(node_map)
        self._num_relations = len(rel_map)
        self.rng = np.random.default_rng(cfg.sampler.seed)
        self.positive_codes = set(self._pack_triples(self.src, self.rel, self.dst).tolist())
        self._build_incident_csr()

    def _build_incident_csr(self) -> None:
        incident_nodes = np.concatenate([self.src, self.dst])
        incident_edges = np.concatenate([np.arange(self.num_edges, dtype=np.int64), np.arange(self.num_edges, dtype=np.int64)])
        order = np.argsort(incident_nodes, kind="stable")
        incident_nodes = incident_nodes[order]
        self.incident_edges = incident_edges[order]
        counts = np.bincount(incident_nodes, minlength=self._num_nodes)
        self.indptr = np.empty(self._num_nodes + 1, dtype=np.int64)
        self.indptr[0] = 0
        np.cumsum(counts, out=self.indptr[1:])

    @property
    def num_nodes(self) -> int:
        return self._num_nodes

    @property
    def num_relations(self) -> int:
        return self._num_relations

    @property
    def num_node_types(self) -> int:
        return max(1, len(self.kind_to_int))

    def _pack_triples(self, src, rel, dst):
        return (np.asarray(src, dtype=np.int64) * self._num_relations + np.asarray(rel, dtype=np.int64)) * self._num_nodes + np.asarray(dst, dtype=np.int64)

    def _incident_sample(self, node: int, fanout: int) -> np.ndarray:
        start, end = int(self.indptr[node]), int(self.indptr[node + 1])
        degree = end - start
        if degree <= 0:
            return np.empty(0, dtype=np.int64)
        if degree <= fanout:
            return self.incident_edges[start:end]
        positions = self.rng.choice(degree, size=fanout, replace=False)
        return self.incident_edges[start + positions]

    def _edge_neighbor(self, node: int, edge_idx: np.ndarray) -> np.ndarray:
        src = self.src[edge_idx]
        dst = self.dst[edge_idx]
        return np.where(src == node, dst, src)

    def _two_hop_context(self, seeds: tuple[int, int]) -> tuple[set[int], set[tuple[int, int, int]]]:
        visited_nodes = {int(seeds[0]), int(seeds[1])}
        visited_edges: set[tuple[int, int, int]] = set()
        frontier = np.fromiter(visited_nodes, dtype=np.int64)
        for hop, fanout in enumerate((self.cfg.sampler.hop1_fanout, self.cfg.sampler.hop2_fanout)):
            next_nodes: list[int] = []
            for node in frontier.tolist():
                edge_idx = self._incident_sample(node, fanout)
                if edge_idx.size == 0:
                    continue
                neighbors = self._edge_neighbor(node, edge_idx)
                for edge in edge_idx.tolist():
                    visited_edges.add((int(self.src[edge]), int(self.rel[edge]), int(self.dst[edge])))
                for nb in neighbors.tolist():
                    if nb not in visited_nodes:
                        visited_nodes.add(int(nb))
                        next_nodes.append(int(nb))
            frontier = np.asarray(next_nodes, dtype=np.int64)
            if frontier.size == 0 and hop == 0:
                break
        return visited_nodes, visited_edges

    def _non_visited_relation_neighbors(self, visited_nodes: set[int]) -> list[int]:
        candidates: set[int] = set()
        for node in visited_nodes:
            edge_idx = self._incident_sample(node, self.cfg.sampler.negative_candidate_fanout)
            if edge_idx.size == 0:
                continue
            neighbors = self._edge_neighbor(node, edge_idx)
            for nb in neighbors.tolist():
                if nb not in visited_nodes:
                    candidates.add(int(nb))
        return list(candidates)

    def _is_positive(self, src: int, rel: int, dst: int) -> bool:
        return int((src * self._num_relations + rel) * self._num_nodes + dst) in self.positive_codes

    def _hard_negative(self, src: int, rel: int, dst: int, src_type: str, dst_type: str, candidate_nodes: list[int]) -> tuple[int, int, int]:
        corrupt_head = bool(self.rng.random() < self.cfg.sampler.hard_negative_head_probability)
        if candidate_nodes:
            self.rng.shuffle(candidate_nodes)
        for _ in range(self.cfg.sampler.max_negative_retries):
            cand = candidate_nodes.pop() if candidate_nodes else int(self.rng.integers(self._num_nodes))
            if corrupt_head:
                if self.cfg.sampler.same_endpoint_type_negatives and self.node_kinds[cand] != src_type:
                    continue
                if not self._is_positive(cand, rel, dst):
                    return cand, rel, dst
            else:
                if self.cfg.sampler.same_endpoint_type_negatives and self.node_kinds[cand] != dst_type:
                    continue
                if not self._is_positive(src, rel, cand):
                    return src, rel, cand
        for _ in range(self.cfg.sampler.max_negative_retries * 4):
            cand = int(self.rng.integers(self._num_nodes))
            candidate = (cand, rel, dst) if corrupt_head else (src, rel, cand)
            if not self._is_positive(*candidate):
                return candidate
        raise RuntimeError("failed to sample a hard negative that is not a known positive")

    def sample_batch(self) -> SampledBatch:
        edge_indices = self.rng.integers(self.num_edges, size=self.cfg.sampler.batch_size, dtype=np.int64)
        graph_nodes: set[int] = set()
        graph_edges: set[tuple[int, int, int]] = set()
        positives: list[tuple[int, int, int]] = []
        negatives: list[list[tuple[int, int, int]]] = []
        for edge_idx in edge_indices.tolist():
            s, d, r = int(self.src[edge_idx]), int(self.dst[edge_idx]), int(self.rel[edge_idx])
            positives.append((s, r, d))
            visited_nodes, visited_edges = self._two_hop_context((s, d))
            negative_candidates = self._non_visited_relation_neighbors(visited_nodes)
            graph_nodes.update(visited_nodes)
            graph_edges.update(visited_edges)
            graph_edges.add((s, r, d))
            negs = [
                self._hard_negative(s, r, d, self.node1_type[edge_idx], self.node2_type[edge_idx], negative_candidates)
                for _ in range(self.cfg.sampler.negatives_per_positive)
            ]
            negatives.append(negs)
            for ns, nr, nd in negs:
                graph_nodes.add(ns)
                graph_nodes.add(nd)
                graph_edges.add((ns, nr, nd))

        ordered_nodes = np.fromiter(graph_nodes, dtype=np.int64)
        ordered_nodes.sort()
        edge_array = np.asarray(list(graph_edges), dtype=np.int64)
        if edge_array.size == 0:
            edge_array = np.empty((0, 3), dtype=np.int64)
        senders = np.searchsorted(ordered_nodes, edge_array[:, 0])
        receivers = np.searchsorted(ordered_nodes, edge_array[:, 2])
        pos_array = np.asarray(positives, dtype=np.int64)
        neg_array = np.asarray(negatives, dtype=np.int64)
        return SampledBatch(
            node_ids=np.arange(len(ordered_nodes), dtype=np.int64),
            node_global_ids=ordered_nodes,
            node_type_ids=self.node_kind_int[ordered_nodes],
            senders=senders.astype(np.int64),
            receivers=receivers.astype(np.int64),
            edge_rel_ids=edge_array[:, 1].astype(np.int64),
            positive_src=np.searchsorted(ordered_nodes, pos_array[:, 0]).astype(np.int64),
            positive_dst=np.searchsorted(ordered_nodes, pos_array[:, 2]).astype(np.int64),
            positive_rel=pos_array[:, 1].astype(np.int64),
            negative_src=np.searchsorted(ordered_nodes, neg_array[:, :, 0]).astype(np.int64),
            negative_dst=np.searchsorted(ordered_nodes, neg_array[:, :, 2]).astype(np.int64),
            negative_rel=neg_array[:, :, 1].astype(np.int64),
        )


class SamplerEngineKGNeighborhoodSampler:
    """TarKG sampler backed by GestaltDB's generic ``SamplerEngine``.

    The example-specific responsibility of this class is loading TarKG artifacts
    from ``edge_arrays.npz`` and parquet mapping files. Generic snapshot
    construction, type encoding, relation endpoint constraint derivation, CSR
    construction, and positive membership setup are delegated to
    ``SamplerSnapshot.from_edge_arrays``.
    """

    def __init__(self, cfg: AppConfig, *, mode: str = "ram"):
        self.cfg = cfg
        self.snapshot = self._load_or_build_snapshot(mode=mode)
        self.engine = SamplerEngine(self.snapshot, seed=cfg.sampler.seed)
        self.rng = np.random.default_rng(cfg.sampler.seed)

    @property
    def num_nodes(self) -> int:
        return self.snapshot.num_nodes

    @property
    def num_relations(self) -> int:
        return self.snapshot.num_relations

    @property
    def num_node_types(self) -> int:
        return max(1, int(self.snapshot.metadata.get("node_type_count", 1)))

    def _load_or_build_snapshot(self, *, mode: str):
        """Load or build the generic sampler snapshot for local TarKG artifacts.

        Args:
            mode: ``"ram"`` loads arrays eagerly and ``"memmap"`` memory maps
                persisted snapshot arrays.

        Returns:
            A ``SamplerSnapshot`` built from the example's local TarKG artifacts.

        Examples:
            The constructor calls this automatically::

                sampler = SamplerEngineKGNeighborhoodSampler(cfg)
        """
        path = self.cfg.paths.sampler_snapshot_path
        metadata_path = path / "metadata.json"
        if not metadata_path.exists():
            path.mkdir(parents=True, exist_ok=True)
            arrays = np.load(self.cfg.paths.edge_arrays_path, allow_pickle=True)
            node_map = pl.read_parquet(self.cfg.paths.node_map_path).sort("node_int")
            rel_map = pl.read_parquet(self.cfg.paths.relation_map_path).sort("rel_int")
            SamplerSnapshot.from_edge_arrays(
                path,
                external_node_ids=node_map["node_id"].to_list(),
                external_edge_ids=arrays["edge_id"].astype(str),
                external_relation_ids=rel_map["edge_type"].to_list(),
                src_int=arrays["src"].astype(np.int64),
                dst_int=arrays["dst"].astype(np.int64),
                rel_int=arrays["rel"].astype(np.int64),
                node_type_values=node_map["kind"].to_list(),
                edge_src_type_values=arrays["node1_type"].astype(str),
                edge_dst_type_values=arrays["node2_type"].astype(str),
                metadata={"source": "example/gnn_kg edge_arrays.npz"},
            )
        return SamplerSnapshot.load(path, mmap=mode == "memmap")

    def sample_batch(self) -> SampledBatch:
        edge_indices = self.rng.integers(self.snapshot.num_edges, size=self.cfg.sampler.batch_size, dtype=np.int64)
        batch = self.engine.sample_subgraph(
            edge_indices,
            fanouts=[self.cfg.sampler.hop1_fanout, self.cfg.sampler.hop2_fanout],
            direction=self.cfg.sampler.bfs_direction,
            negative_config=HardNegativeConfig(
                negatives_per_positive=self.cfg.sampler.negatives_per_positive,
                source=self.cfg.sampler.engine_negative_source,
                reject_known_positives=True,
                same_endpoint_type=self.cfg.sampler.same_endpoint_type_negatives,
                relation_endpoint_types=True,
                candidate_fanout=self.cfg.sampler.negative_candidate_fanout,
                head_probability=self.cfg.sampler.hard_negative_head_probability,
                max_retries=self.cfg.sampler.max_negative_retries,
            ),
        )
        return SampledBatch(
            node_ids=np.arange(batch.n_nodes, dtype=np.int64),
            node_global_ids=batch.node_ids_global,
            node_type_ids=batch.node_type_ids,
            senders=batch.senders,
            receivers=batch.receivers,
            edge_rel_ids=batch.edge_relation_ids,
            positive_src=batch.positives[:, 0].astype(np.int64),
            positive_dst=batch.positives[:, 2].astype(np.int64),
            positive_rel=batch.positives[:, 1].astype(np.int64),
            negative_src=batch.negatives[:, :, 0].astype(np.int64),
            negative_dst=batch.negatives[:, :, 2].astype(np.int64),
            negative_rel=batch.negatives[:, :, 1].astype(np.int64),
        )


def make_sampler(cfg: AppConfig, graphdb=None):
    backend = cfg.sampler.backend if hasattr(cfg.sampler, "backend") else ("array" if cfg.sampler.use_array_backend else "graphdb")
    if backend == "engine":
        return SamplerEngineKGNeighborhoodSampler(cfg)
    if backend == "array":
        return ArrayKGNeighborhoodSampler(cfg)
    if graphdb is None:
        raise ValueError("graphdb is required when use_array_backend=False")
    return KGNeighborhoodSampler(cfg, graphdb)
