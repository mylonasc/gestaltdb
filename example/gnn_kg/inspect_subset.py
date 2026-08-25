from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from example.gnn_kg.config import make_config
else:
    from .config import make_config


def main() -> None:
    cfg = make_config()
    if not cfg.paths.selected_edges_path.exists():
        raise SystemExit(f"missing {cfg.paths.selected_edges_path}; run prepare_tarkg.py first")
    metadata = json.loads(cfg.paths.metadata_path.read_text(encoding="utf-8")) if cfg.paths.metadata_path.exists() else {}
    edges = pl.scan_parquet(cfg.paths.selected_edges_path)
    nodes = pl.scan_parquet(cfg.paths.selected_nodes_path)
    print(json.dumps(metadata, indent=2))
    print("\nTop relations:")
    print(edges.group_by("edge_type").len().sort("len", descending=True).head(25).collect())
    print("\nTop node sources:")
    print(nodes.group_by("node_source").len().sort("len", descending=True).head(25).collect())


if __name__ == "__main__":
    main()
