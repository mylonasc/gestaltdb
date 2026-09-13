"""Run GestaltDB backend benchmarks across sizes, cores, and ingest modes.

The runner is intentionally storage-backed: each benchmark writes to an on-disk
temporary database, closes it, reopens it, then runs traversal workloads. This
does not eliminate the OS page cache, but it avoids measuring only Python object
state kept alive after ingestion.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import platform
import random
import shutil
import sys
import tempfile
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.sampling import SamplingHop, SamplingPattern
from benchmarks.common.datasets import (
    DEFAULT_EDGE_TYPES,
    chunks,
    edge_parts,
    make_edge,
    make_node,
    polars_frame,
    pyarrow_array,
)
from benchmarks.common.reporting import ResultWriter
from benchmarks.common.storage import (
    disk_usage,
    open_gestaltdb,
    rocksdb_configs,
    serializer_factory,
    validate_matrix_dependencies,
)
from benchmarks.common.timing import cpu_affinity, seconds, set_thread_env

CSV_FIELDS = [
    "status",
    "skip_reason",
    "backend",
    "backend_config",
    "cores",
    "nodes",
    "edges",
    "ingestion_mode",
    "ingestion_semantics",
    "serializer",
    "chunk_size",
    "samples",
    "sample_size",
    "bfs_limit",
    "serialization_seconds",
    "column_build_seconds",
    "node_ingest_seconds",
    "edge_ingest_seconds",
    "reopen_seconds",
    "bfs_seconds",
    "sampling_seconds",
    "typed_path_seconds",
    "node_ingest_rate",
    "edge_ingest_rate",
    "bfs_rate",
    "sampling_rate",
    "typed_path_rate",
    "bfs_visited",
    "sample_seeds",
    "db_bytes",
    "native_columnar",
]


def ingest_object(graph: GraphDB, nodes: int, edges: int, chunk_size: int) -> dict[str, float]:
    """Ingest nodes and edges as Python objects."""
    def write_nodes() -> None:
        for start, end in chunks(nodes, chunk_size):
            graph.put_nodes([make_node(index) for index in range(start, end)])

    def write_edges() -> None:
        for start, end in chunks(edges, chunk_size):
            graph.put_edges_bulk(
                [make_edge(index, nodes, edge_types=DEFAULT_EDGE_TYPES) for index in range(start, end)],
                check_existing=False,
            )

    _, node_seconds = seconds(write_nodes)
    _, edge_seconds = seconds(write_edges)
    return {
        "serialization_seconds": 0.0,
        "column_build_seconds": 0.0,
        "node_ingest_seconds": node_seconds,
        "edge_ingest_seconds": edge_seconds,
    }


def ingest_columnar(graph: GraphDB, nodes: int, edges: int, chunk_size: int, mode: str) -> dict[str, float]:
    """Ingest nodes and edges via PyArrow or Polars columnar payloads."""
    serialization_seconds = 0.0
    column_build_seconds = 0.0
    node_ingest_seconds = 0.0
    edge_ingest_seconds = 0.0

    for start, end in chunks(nodes, chunk_size):
        node_ids = [f"n{index}" for index in range(start, end)]
        node_values, serialize_seconds = seconds(
            lambda start=start, end=end: [graph.serialize_node_value(make_node(index)) for index in range(start, end)]
        )
        serialization_seconds += serialize_seconds
        if mode == "arrow":
            args, build_seconds = seconds(lambda: (pyarrow_array(node_ids), pyarrow_array(node_values)))
            column_build_seconds += build_seconds
            _, elapsed = seconds(lambda args=args: graph.ingest_nodes_arrow(*args, chunk_size=chunk_size))
        else:
            df, build_seconds = seconds(lambda: polars_frame({"node_id": node_ids, "node_value": node_values}))
            column_build_seconds += build_seconds
            _, elapsed = seconds(lambda df=df: graph.ingest_nodes_polars(df, chunk_size=chunk_size))
        node_ingest_seconds += elapsed

    for start, end in chunks(edges, chunk_size):
        edge_ids: list[str] = []
        sources: list[str] = []
        targets: list[str] = []
        edge_types: list[str] = []
        for index in range(start, end):
            edge_id, source, target, edge_type = edge_parts(index, nodes, edge_types=DEFAULT_EDGE_TYPES)
            edge_ids.append(edge_id)
            sources.append(source)
            targets.append(target)
            edge_types.append(edge_type)
        edge_values, serialize_seconds = seconds(
            lambda start=start, end=end: [
                graph.serialize_edge_value(make_edge(index, nodes, edge_types=DEFAULT_EDGE_TYPES))
                for index in range(start, end)
            ]
        )
        serialization_seconds += serialize_seconds
        if mode == "arrow":
            args, build_seconds = seconds(
                lambda: (
                    pyarrow_array(edge_ids),
                    pyarrow_array(sources),
                    pyarrow_array(targets),
                    pyarrow_array(edge_types),
                    pyarrow_array(edge_values),
                )
            )
            column_build_seconds += build_seconds
            _, elapsed = seconds(lambda args=args: graph.ingest_edges_arrow(*args, append_only=True, chunk_size=chunk_size))
        else:
            df, build_seconds = seconds(
                lambda: polars_frame(
                    {
                        "edge_id": edge_ids,
                        "source": sources,
                        "target": targets,
                        "edge_type": edge_types,
                        "edge_value": edge_values,
                    }
                )
            )
            column_build_seconds += build_seconds
            _, elapsed = seconds(lambda df=df: graph.ingest_edges_polars(df, append_only=True, chunk_size=chunk_size))
        edge_ingest_seconds += elapsed

    return {
        "serialization_seconds": serialization_seconds,
        "column_build_seconds": column_build_seconds,
        "node_ingest_seconds": node_ingest_seconds,
        "edge_ingest_seconds": edge_ingest_seconds,
    }


def typed_bfs(graph: GraphDB, start_node: str, *, limit: int, edge_types: Sequence[str] = DEFAULT_EDGE_TYPES) -> int:
    """BFS over typed adjacency records."""
    visited: set[bytes] = set()
    queue = deque([graph.node_key_to_bytes(start_node)])
    while queue and len(visited) < limit:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for edge_type in edge_types:
            for record in graph.iter_typed_adjacency(current, edge_type, direction="out"):
                neighbor = record["neighbor_id"]
                if neighbor not in visited:
                    queue.append(neighbor)
    return len(visited)


def run_traversals(graph: GraphDB, nodes: int, args: argparse.Namespace) -> dict[str, Any]:
    """Run traversal and sampling workloads against loaded database."""
    sample_count = min(args.samples, nodes)
    seed_ids = [f"n{index}" for index in range(sample_count)]
    pattern = SamplingPattern(
        [
            SamplingHop("rel-a", direction="out", sample_size=args.sample_size),
            SamplingHop("rel-b", direction="out", sample_size=args.sample_size),
        ]
    )

    bfs_visited, bfs_seconds = seconds(lambda: typed_bfs(graph, "n0", limit=min(args.bfs_limit, nodes)))
    _, sampling_seconds = seconds(
        lambda: [graph.sample_neighbors(seed_id, "rel-a", sample_size=args.sample_size) for seed_id in seed_ids]
    )
    _, typed_path_seconds = seconds(lambda: graph.sample_typed_paths(seed_ids, pattern, rng=random.Random(args.seed)))
    return {
        "bfs_visited": bfs_visited,
        "sample_seeds": sample_count,
        "bfs_seconds": bfs_seconds,
        "sampling_seconds": sampling_seconds,
        "typed_path_seconds": typed_path_seconds,
    }


def base_result(
    args: argparse.Namespace,
    backend: str,
    config_name: str,
    cores: int,
    nodes: int,
    edges: int,
    ingestion_mode: str,
) -> dict[str, Any]:
    """Initialize result row with metadata."""
    semantics = "full_object_indexes_legacy_adjacency" if ingestion_mode == "object" else "append_only_typed_adjacency_only"
    return {
        "status": "ok",
        "skip_reason": "",
        "backend": backend,
        "backend_config": config_name,
        "cores": cores,
        "nodes": nodes,
        "edges": edges,
        "ingestion_mode": ingestion_mode,
        "ingestion_semantics": semantics,
        "serializer": args.serializer,
        "chunk_size": args.chunk_size,
        "samples": args.samples,
        "sample_size": args.sample_size,
        "bfs_limit": args.bfs_limit,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def add_matrix_rates(result: dict[str, Any], nodes: int, edges: int) -> None:
    """Calculate and set throughput rates."""
    for key, numerator_key, rate_key in (
        ("node_ingest_seconds", None, "node_ingest_rate"),
        ("edge_ingest_seconds", None, "edge_ingest_rate"),
        ("bfs_seconds", "bfs_visited", "bfs_rate"),
        ("sampling_seconds", "sample_seeds", "sampling_rate"),
        ("typed_path_seconds", "sample_seeds", "typed_path_rate"),
    ):
        elapsed = result.get(key)
        if not isinstance(elapsed, (int, float)) or elapsed <= 0:
            result[rate_key] = ""
            continue
        numerator = result.get(numerator_key) if numerator_key else (edges if key == "edge_ingest_seconds" else nodes)
        result[rate_key] = float(numerator) / elapsed if isinstance(numerator, (int, float)) else ""


def run_one(
    args: argparse.Namespace,
    backend: str,
    config_name: str,
    rocksdb_options: dict[str, Any],
    cores: int,
    nodes: int,
    ingestion_mode: str,
) -> dict[str, Any]:
    """Execute one parameter point of the matrix benchmark."""
    edges = nodes * args.edge_multiplier
    result = base_result(args, backend, config_name, cores, nodes, edges, ingestion_mode)
    skip_reason = validate_matrix_dependencies(backend, ingestion_mode, args.serializer)
    if skip_reason:
        result.update({"status": "skipped", "skip_reason": skip_reason})
        return result

    set_thread_env(cores)
    db_path = Path(tempfile.mkdtemp(prefix=f"gestaltdb_{backend}_{ingestion_mode}_{nodes}_", dir=args.tmp_dir))
    graph: GraphDB | None = None
    try:
        with cpu_affinity(cores):
            graph = open_gestaltdb(db_path, backend=backend, serializer=args.serializer, rocksdb_options=rocksdb_options)
            result["native_columnar"] = bool(getattr(graph.store, "has_native_columnar_ingestion", lambda: False)())
            if ingestion_mode == "object":
                result.update(ingest_object(graph, nodes, edges, args.chunk_size))
            else:
                result.update(ingest_columnar(graph, nodes, edges, args.chunk_size, ingestion_mode))
            graph.close()
            graph = None

            def reopen() -> GraphDB:
                return open_gestaltdb(db_path, backend=backend, serializer=args.serializer, rocksdb_options=rocksdb_options)

            graph, reopen_seconds = seconds(reopen)
            result["reopen_seconds"] = reopen_seconds
            result.update(run_traversals(graph, nodes, args))
            result["db_bytes"] = disk_usage(db_path)
    except Exception as exc:
        result.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if graph is not None:
            graph.close()
        if not args.keep_dbs:
            shutil.rmtree(db_path, ignore_errors=True)

    add_matrix_rates(result, nodes, edges)
    return result


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build or configure argument parser for matrix benchmark."""
    parser = subparser or argparse.ArgumentParser(description="Run GestaltDB benchmark matrix across configurations")
    parser.add_argument("--backends", nargs="+", choices=["leveldb", "rocksdb"], default=["leveldb", "rocksdb"])
    parser.add_argument("--sizes", nargs="+", type=int, default=[10_000, 100_000, 1_000_000])
    parser.add_argument("--edge-multiplier", type=int, default=1)
    parser.add_argument("--cores", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument(
        "--ingestion-modes",
        nargs="+",
        choices=["object", "arrow", "polars"],
        default=["object", "arrow", "polars"],
    )
    parser.add_argument("--serializer", choices=["pickle", "msgpack", "json", "protobuf"], default="msgpack")
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--samples", type=int, default=1_000)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--bfs-limit", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    parser.add_argument(
        "--rocksdb-configs",
        nargs="+",
        choices=[
            "default",
            "transactional",
            "parallel",
            "parallel-transactional",
            "parallel-buffer64mb-bloom10",
            "parallel-buffer64mb-bloom10-transactional",
            "parallel-buffer64mb-bloom10-nowal",
        ],
        default=["default", "parallel", "parallel-buffer64mb-bloom10"],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point for matrix benchmark."""
    parser = build_parser()
    args = parser.parse_args(argv)
    writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="matrix_results.csv",
        jsonl_filename="matrix_results.jsonl",
        fieldnames=CSV_FIELDS,
    )
    for cores in args.cores:
        for nodes in args.sizes:
            for backend in args.backends:
                configs = [("default", {})] if backend == "leveldb" else rocksdb_configs(args.rocksdb_configs, cores)
                for config_name, rocksdb_options in configs:
                    for ingestion_mode in args.ingestion_modes:
                        label = f"backend={backend} config={config_name} cores={cores} nodes={nodes} mode={ingestion_mode}"
                        print(f"Running {label}", flush=True)
                        result = run_one(args, backend, config_name, rocksdb_options, cores, nodes, ingestion_mode)
                        writer.write_row(result)
                        status = result["status"]
                        edge_rate = result.get("edge_ingest_rate", "")
                        print(f"Finished {label} status={status} edge_rate={edge_rate}", flush=True)


if __name__ == "__main__":
    main()
