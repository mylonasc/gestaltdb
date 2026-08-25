from __future__ import annotations

import argparse
import json
import shutil
import subprocess as sp
import sys
import time
from pathlib import Path

import numpy as np

try:
    import polars as pl
except ImportError as exc:  # pragma: no cover - dependency guard
    raise SystemExit("Install polars and pyarrow before running prepare_tarkg.py") from exc

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from example.gnn_kg.config import AppConfig, make_config
else:
    from .config import AppConfig, make_config


UTIL_REPO_URL = "https://github.com/mylonasc/drug-target-interaction-gnns.git"


def ensure_local_imports(cfg: AppConfig) -> None:
    src_path = cfg.paths.project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def ensure_tarkg_utils(cfg: AppConfig) -> None:
    cfg.paths.data_root.mkdir(parents=True, exist_ok=True)
    if not cfg.paths.util_repo_dir.exists():
        sp.check_call(["git", "clone", "--depth", "1", UTIL_REPO_URL, str(cfg.paths.util_repo_dir)])
    dataset_path = cfg.paths.util_repo_dir / "dataset"
    if str(dataset_path) not in sys.path:
        sys.path.insert(0, str(dataset_path))


def load_tarkg_lazy(cfg: AppConfig):
    ensure_tarkg_utils(cfg)
    from tar_kg.tarkg_loader import TarKGLoader

    loader = TarKGLoader(
        cache_dir=cfg.paths.tarkg_cache_dir,
        parquet_cache_dir=cfg.paths.parquet_cache_dir,
        max_workers=cfg.subset.max_download_workers,
        show_progress=True,
    )
    if cfg.subset.force_download:
        loader.download(
            kg=["TarKG_nodes.csv", "TarKG_nodes_mapping.csv"],
            entities=False,
            relations=["edges", "edges_mapping"],
            features=False,
            force=True,
        )
    return loader.load(
        download=True,
        use_parquet_cache=True,
        kg=["TarKG_nodes.csv", "TarKG_nodes_mapping.csv"],
        entities=False,
        relations=["edges", "edges_mapping"],
        features=False,
        lazy=True,
    )


def make_base_frames(data: dict[str, pl.LazyFrame]) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    nodes_raw = data["TarKG_nodes"]
    edges_raw = data["TarKG_edges"]
    node_df = nodes_raw.select(
        pl.col("unify_id").alias("node_id"),
        pl.concat_list("kind").alias("labels"),
        pl.col("kind"),
        pl.col("dbid"),
        pl.col("db_source"),
        pl.col("name"),
        pl.col("source").alias("node_source"),
    ).unique(subset=["node_id"], keep="first")
    edge_df = edges_raw.select(
        pl.col("index").cast(pl.Utf8).alias("edge_id"),
        pl.col("node1").alias("source"),
        pl.col("node2").alias("target"),
        pl.col("relation").alias("edge_type"),
        pl.col("node1_type"),
        pl.col("node2_type"),
    )
    return node_df, edge_df


def _available_columns(frame: pl.LazyFrame, columns: list[str]) -> list[str]:
    schema_names = set(frame.collect_schema().names())
    return [column for column in columns if column in schema_names]


def _node_mapping_frame(data: dict[str, pl.LazyFrame], selected_nodes: pl.LazyFrame) -> pl.LazyFrame | None:
    mapping = data.get("TarKG_nodes_mapping")
    if mapping is None:
        return None
    selected_ids = selected_nodes.select("node_id").unique()
    columns = _available_columns(
        mapping,
        ["index", "unify_id", "kind", "kgid", "dbid", "db_source", "name", "stru", "std_inchi", "source", "kg_index", "kg"],
    )
    return (
        mapping.select(columns)
        .rename({"unify_id": "node_id"})
        .join(selected_ids, on="node_id", how="semi")
    )


def _edge_mapping_frame(data: dict[str, pl.LazyFrame], selected_edges: pl.LazyFrame) -> pl.LazyFrame | None:
    mapping = data.get("TarKG_edges_mapping")
    if mapping is None:
        return None
    selected_ids = selected_edges.select("edge_id").unique()
    columns = _available_columns(
        mapping,
        ["index", "node1", "node1_type", "relation", "node2", "node2_type", "db_source", "kg_index", "kg", "change"],
    )
    return (
        mapping.select(columns)
        .with_columns(pl.col("index").cast(pl.Utf8).alias("edge_id"))
        .join(selected_ids, on="edge_id", how="semi")
    )


def _node_mapping_summary(node_mappings: pl.LazyFrame | None) -> pl.LazyFrame | None:
    if node_mappings is None:
        return None
    return node_mappings.group_by("node_id").agg(
        pl.len().alias("mapping_count"),
        pl.col("name").drop_nulls().first().alias("mapping_name"),
        pl.col("dbid").drop_nulls().first().alias("mapping_dbid"),
        pl.col("db_source").drop_nulls().first().alias("mapping_db_source"),
        pl.col("source").drop_nulls().first().alias("mapping_node_source"),
        pl.col("kg").drop_nulls().unique().sort().alias("source_kgs"),
        pl.col("kgid").drop_nulls().unique().sort().head(20).alias("source_kgids"),
    )


def _enrich_selected_nodes(selected_nodes: pl.LazyFrame, node_mappings: pl.LazyFrame | None) -> pl.LazyFrame:
    summary = _node_mapping_summary(node_mappings)
    if summary is None:
        return selected_nodes.with_columns(
            pl.lit(0, dtype=pl.UInt32).alias("mapping_count"),
            pl.lit([], dtype=pl.List(pl.Utf8)).alias("source_kgs"),
            pl.lit([], dtype=pl.List(pl.Utf8)).alias("source_kgids"),
        )
    return (
        selected_nodes.join(summary, on="node_id", how="left")
        .with_columns(
            pl.coalesce([pl.col("name"), pl.col("mapping_name")]).alias("name"),
            pl.coalesce([pl.col("dbid"), pl.col("mapping_dbid")]).alias("dbid"),
            pl.coalesce([pl.col("db_source"), pl.col("mapping_db_source")]).alias("db_source"),
            pl.coalesce([pl.col("node_source"), pl.col("mapping_node_source")]).alias("node_source"),
            pl.col("mapping_count").fill_null(0).cast(pl.UInt32).alias("mapping_count"),
            pl.col("source_kgs").fill_null([]),
            pl.col("source_kgids").fill_null([]),
        )
        .drop(["mapping_name", "mapping_dbid", "mapping_db_source", "mapping_node_source"])
    )


def _edge_mapping_summary(edge_mappings: pl.LazyFrame | None) -> pl.LazyFrame | None:
    if edge_mappings is None:
        return None
    return edge_mappings.group_by("edge_id").agg(
        pl.len().alias("mapping_count"),
        pl.col("db_source").drop_nulls().first().alias("mapping_db_source"),
        pl.col("kg").drop_nulls().unique().sort().alias("source_kgs"),
        pl.col("kg_index").drop_nulls().unique().sort().head(20).alias("source_kg_indices"),
        pl.col("change").drop_nulls().unique().sort().alias("mapping_changes"),
    )


def _enrich_selected_edges(selected_edges: pl.LazyFrame, edge_mappings: pl.LazyFrame | None) -> pl.LazyFrame:
    summary = _edge_mapping_summary(edge_mappings)
    if summary is None:
        return selected_edges.with_columns(
            pl.lit(0, dtype=pl.UInt32).alias("mapping_count"),
            pl.lit([], dtype=pl.List(pl.Utf8)).alias("source_kgs"),
            pl.lit([], dtype=pl.List(pl.Utf8)).alias("source_kg_indices"),
            pl.lit([], dtype=pl.List(pl.Int64)).alias("mapping_changes"),
        )
    return (
        selected_edges.join(summary, on="edge_id", how="left")
        .with_columns(
            pl.col("mapping_count").fill_null(0).cast(pl.UInt32).alias("mapping_count"),
            pl.col("mapping_db_source").alias("db_source"),
            pl.col("source_kgs").fill_null([]),
            pl.col("source_kg_indices").fill_null([]),
            pl.col("mapping_changes").fill_null([]),
        )
        .drop("mapping_db_source")
    )


def select_relations(edges: pl.LazyFrame, cfg: AppConfig) -> list[str]:
    subset = cfg.subset
    if subset.allowed_relations:
        return list(subset.allowed_relations)
    rel_counts = (
        edges.group_by("edge_type")
        .len()
        .filter(pl.col("len") >= subset.min_relation_edges)
        .filter(~pl.col("edge_type").is_in(list(subset.excluded_relations)))
        .sort("len", descending=True)
        .collect()
    )
    selected: list[str] = []
    total = 0
    for row in rel_counts.iter_rows(named=True):
        if subset.max_relations is not None and len(selected) >= subset.max_relations:
            break
        selected.append(row["edge_type"])
        total += row["len"]
        if total >= subset.target_edges:
            break
    return selected


def select_node_sources(nodes: pl.LazyFrame, cfg: AppConfig) -> list[str]:
    subset = cfg.subset
    if subset.allowed_node_sources:
        return list(subset.allowed_node_sources)
    counts = (
        nodes.filter(pl.col("node_source").is_not_null())
        .group_by("node_source")
        .len()
        .filter(pl.col("len") >= subset.min_node_source_nodes)
        .filter(~pl.col("node_source").is_in(list(subset.excluded_node_sources)))
        .sort("len", descending=True)
        .collect()
    )
    selected: list[str] = []
    for row in counts.iter_rows(named=True):
        if subset.max_node_sources is not None and len(selected) >= subset.max_node_sources:
            break
        selected.append(row["node_source"])
    return selected


def build_selected_frames(cfg: AppConfig, dry_run: bool = False) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame | None, pl.LazyFrame | None, dict[str, object]]:
    data = load_tarkg_lazy(cfg)
    node_df, edge_df = make_base_frames(data)
    selected_node_sources = select_node_sources(node_df, cfg)
    if selected_node_sources:
        node_df = node_df.filter(pl.col("node_source").is_in(selected_node_sources))
    if cfg.subset.excluded_node_sources:
        node_df = node_df.filter(~pl.col("node_source").is_in(list(cfg.subset.excluded_node_sources)))

    node_ids = node_df.select("node_id").unique()
    valid_edges = edge_df.join(node_ids, left_on="source", right_on="node_id", how="semi").join(
        node_ids, left_on="target", right_on="node_id", how="semi"
    )
    selected_relations = select_relations(valid_edges, cfg)
    selected_edges = valid_edges.filter(pl.col("edge_type").is_in(selected_relations))

    edge_count = selected_edges.select(pl.len()).collect().item()
    selected_node_ids = pl.concat(
        [selected_edges.select(pl.col("source").alias("node_id")), selected_edges.select(pl.col("target").alias("node_id"))]
    ).unique()
    selected_nodes_base = node_df.join(selected_node_ids, on="node_id", how="semi")
    node_mappings = _node_mapping_frame(data, selected_nodes_base)
    edge_mappings = _edge_mapping_frame(data, selected_edges)
    selected_nodes = _enrich_selected_nodes(selected_nodes_base, node_mappings)
    selected_edges = _enrich_selected_edges(selected_edges, edge_mappings)
    node_count = selected_nodes.select(pl.len()).collect().item()
    metadata = {
        "target_edges": cfg.subset.target_edges,
        "selected_edges": edge_count,
        "selected_nodes": node_count,
        "selected_node_sources": selected_node_sources,
        "selected_relations": selected_relations,
        "node_mappings": None if node_mappings is None else node_mappings.select(pl.len()).collect().item(),
        "edge_mappings": None if edge_mappings is None else edge_mappings.select(pl.len()).collect().item(),
    }
    if dry_run:
        print(json.dumps(metadata, indent=2))
    return selected_nodes, selected_edges, node_mappings, edge_mappings, metadata


def materialize_artifacts(
    cfg: AppConfig,
    selected_nodes: pl.LazyFrame,
    selected_edges: pl.LazyFrame,
    node_mappings: pl.LazyFrame | None,
    edge_mappings: pl.LazyFrame | None,
    metadata: dict[str, object],
) -> None:
    cfg.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    cfg.paths.selected_nodes_path.unlink(missing_ok=True)
    cfg.paths.selected_edges_path.unlink(missing_ok=True)
    cfg.paths.node_info_path.unlink(missing_ok=True)
    cfg.paths.node_mappings_path.unlink(missing_ok=True)
    cfg.paths.edge_mappings_path.unlink(missing_ok=True)
    selected_nodes.sink_parquet(cfg.paths.selected_nodes_path)
    selected_edges.sink_parquet(cfg.paths.selected_edges_path)
    selected_nodes.sink_parquet(cfg.paths.node_info_path)
    if node_mappings is not None:
        node_mappings.sink_parquet(cfg.paths.node_mappings_path)
    if edge_mappings is not None:
        edge_mappings.sink_parquet(cfg.paths.edge_mappings_path)

    nodes = pl.scan_parquet(cfg.paths.selected_nodes_path)
    edges = pl.scan_parquet(cfg.paths.selected_edges_path)
    node_map_columns = [
        "node_id",
        "kind",
        "name",
        "dbid",
        "db_source",
        "node_source",
        "mapping_count",
        "source_kgs",
        "source_kgids",
    ]
    node_map = nodes.select(_available_columns(nodes, node_map_columns)).with_row_index("node_int")
    rel_map = edges.group_by("edge_type").agg(pl.len().alias("edge_count")).sort("edge_type").with_row_index("rel_int")

    node_map.sink_parquet(cfg.paths.node_map_path)
    rel_map.sink_parquet(cfg.paths.relation_map_path)
    edge_ints = (
        edges.join(node_map.select("node_id", "node_int"), left_on="source", right_on="node_id")
        .rename({"node_int": "src_int"})
        .join(node_map.select("node_id", "node_int"), left_on="target", right_on="node_id")
        .rename({"node_int": "dst_int"})
        .join(rel_map, on="edge_type")
        .select("edge_id", "src_int", "dst_int", "rel_int", "node1_type", "node2_type")
        .collect()
    )
    np.savez_compressed(
        cfg.paths.edge_arrays_path,
        src=edge_ints["src_int"].to_numpy().astype(np.int64),
        dst=edge_ints["dst_int"].to_numpy().astype(np.int64),
        rel=edge_ints["rel_int"].to_numpy().astype(np.int64),
        edge_id=edge_ints["edge_id"].to_numpy(),
        node1_type=edge_ints["node1_type"].to_numpy(),
        node2_type=edge_ints["node2_type"].to_numpy(),
    )
    metadata.update({"num_relations": rel_map.select(pl.len()).collect().item()})
    cfg.paths.metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def ingest_gestaltdb(cfg: AppConfig) -> None:
    ensure_local_imports(cfg)
    from gestaltdb import IndexMaintenanceMode
    from gestaltdb.graphdb import GraphDB

    if cfg.ingestion.reset_db and cfg.paths.db_path.exists():
        shutil.rmtree(cfg.paths.db_path)
    graphdb = GraphDB.create(
        cfg.paths.db_path,
        backend="pyrex",
        serializer="json",
        backend_options={
            "disable_wal": True,
            "write_buffer_size": cfg.ingestion.write_buffer_size,
            "parallelism": cfg.ingestion.parallelism,
            "max_background_jobs": cfg.ingestion.max_background_jobs,
        },
    )
    try:
        nodes = pl.scan_parquet(cfg.paths.selected_nodes_path)
        edges = pl.scan_parquet(cfg.paths.selected_edges_path)
        t0 = time.perf_counter()
        n_nodes = graphdb.ingest_nodes_polars_entities(
            nodes,
            node_id="node_id",
            labels="labels",
            property_columns=[
                "kind",
                "name",
                "dbid",
                "db_source",
                "node_source",
                "mapping_count",
                "source_kgs",
                "source_kgids",
            ],
            append_only=True,
            index_mode=IndexMaintenanceMode.DEFER,
            chunk_size=cfg.ingestion.chunk_size,
            progress=cfg.ingestion.progress,
        )
        n_edges = graphdb.ingest_edges_polars_entities(
            edges,
            edge_id="edge_id",
            source="source",
            target="target",
            edge_type="edge_type",
            property_columns=[
                "node1_type",
                "node2_type",
                "db_source",
                "mapping_count",
                "source_kgs",
                "source_kg_indices",
                "mapping_changes",
            ],
            append_only=True,
            index_mode=IndexMaintenanceMode.DEFER,
            chunk_size=cfg.ingestion.chunk_size,
            progress=cfg.ingestion.progress,
        )
        print(f"ingested {n_nodes:,} nodes and {n_edges:,} edges in {time.perf_counter() - t0:.1f}s")
    finally:
        graphdb.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args()
    cfg = make_config(smoke_test=args.smoke_test)
    cfg.ingestion.reset_db = args.reset_db
    selected_nodes, selected_edges, node_mappings, edge_mappings, metadata = build_selected_frames(cfg, dry_run=args.dry_run)
    if args.dry_run:
        return
    materialize_artifacts(cfg, selected_nodes, selected_edges, node_mappings, edge_mappings, metadata)
    if not args.skip_ingest:
        ingest_gestaltdb(cfg)


if __name__ == "__main__":
    main()
