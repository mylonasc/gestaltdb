from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(slots=True)
class PathsConfig:
    project_root: Path = Path(__file__).resolve().parents[2]
    data_root: Path = project_root / "data" / "tarkg_gnn"
    util_repo_dir: Path = data_root / "drug-target-interaction-gnns"
    tarkg_cache_dir: Path = data_root / "cache"
    parquet_cache_dir: Path = tarkg_cache_dir / "parquet"
    db_path: Path = data_root / "gestaltdb_tarkg_subset_rocksdb"
    artifacts_dir: Path = data_root / "artifacts"
    selected_nodes_path: Path = artifacts_dir / "nodes_selected.parquet"
    selected_edges_path: Path = artifacts_dir / "edges_selected.parquet"
    node_map_path: Path = artifacts_dir / "node_map.parquet"
    relation_map_path: Path = artifacts_dir / "relation_map.parquet"
    edge_arrays_path: Path = artifacts_dir / "edge_arrays.npz"
    sampler_snapshot_path: Path = artifacts_dir / "sampler_snapshot"
    metadata_path: Path = artifacts_dir / "metadata.json"


@dataclass(slots=True)
class SubsetConfig:
    target_edges: int = 14_000_000
    target_tolerance: float = 0.08
    min_node_source_nodes: int = 10_000
    max_node_sources: int | None = 8
    min_relation_edges: int = 50_000
    max_relations: int | None = None
    allowed_node_sources: tuple[str, ...] = ()
    excluded_node_sources: tuple[str, ...] = ()
    allowed_relations: tuple[str, ...] = ()
    excluded_relations: tuple[str, ...] = ()
    force_download: bool = False
    max_download_workers: int = 4


@dataclass(slots=True)
class IngestionConfig:
    reset_db: bool = False
    chunk_size: int = 250_000
    write_buffer_size: int = 256 * 1024 * 1024
    parallelism: int = 8
    max_background_jobs: int = 8
    progress: bool = True


@dataclass(slots=True)
class SamplerConfig:
    batch_size: int = 64
    negatives_per_positive: int = 8
    hop1_fanout: int = 12
    hop2_fanout: int = 8
    negative_candidate_fanout: int = 24
    bfs_direction: Literal["out", "in", "any"] = "any"
    hard_negative_head_probability: float = 0.5
    same_endpoint_type_negatives: bool = True
    engine_negative_source: Literal["random", "context_relation_neighbors", "non_visited_relation_neighbors"] = "context_relation_neighbors"
    max_negative_retries: int = 64
    seed: int = 13
    use_array_backend: bool = True
    backend: Literal["graphdb", "array", "engine"] = "array"


@dataclass(slots=True)
class ModelConfig:
    node_id_embedding_dim: int = 64
    node_type_embedding_dim: int = 32
    relation_embedding_dim: int = 64
    hidden_dim: int = 256
    mp_steps: int = 3
    dropout: float = 0.0
    aggregation_function: str = "mean"
    temperature: float = 0.1


@dataclass(slots=True)
class TrainingConfig:
    steps: int = 1_000
    learning_rate: float = 1e-4
    log_every: int = 20
    prefetch: int = 2
    smoke_test: bool = False


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    subset: SubsetConfig = field(default_factory=SubsetConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    sampler: SamplerConfig = field(default_factory=SamplerConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)


def make_config(smoke_test: bool = False) -> AppConfig:
    cfg = AppConfig()
    if smoke_test:
        cfg.subset.target_edges = 50_000
        cfg.subset.min_relation_edges = 1_000
        cfg.ingestion.chunk_size = 10_000
        cfg.sampler.batch_size = 4
        cfg.sampler.negatives_per_positive = 2
        cfg.sampler.hop1_fanout = 3
        cfg.sampler.hop2_fanout = 2
        cfg.model.hidden_dim = 64
        cfg.model.mp_steps = 1
        cfg.training.steps = 2
        cfg.training.log_every = 1
        cfg.training.smoke_test = True
    return cfg
