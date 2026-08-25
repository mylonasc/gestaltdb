from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

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
                source_db={
                    "path": cfg.paths.db_path,
                    "path_type": "relative_to_snapshot",
                },
                source_artifacts={
                    "edge_arrays": cfg.paths.edge_arrays_path,
                    "node_map": cfg.paths.node_map_path,
                    "node_info": cfg.paths.node_info_path,
                    "node_mappings": cfg.paths.node_mappings_path,
                    "relation_map": cfg.paths.relation_map_path,
                    "edge_mappings": cfg.paths.edge_mappings_path,
                },
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
    return SamplerEngineKGNeighborhoodSampler(cfg)
