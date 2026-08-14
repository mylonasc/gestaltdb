#!/usr/bin/env python3
"""Run GestaltDB backend benchmarks across sizes, cores, and ingest modes.

The runner is intentionally storage-backed: each benchmark writes to an on-disk
temporary database, closes it, reopens it, then runs traversal workloads. This
does not eliminate the OS page cache, but it avoids measuring only Python object
state kept alive after ingestion.
"""

from __future__ import annotations

import argparse
import csv
from collections import deque
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import platform
import random
import shutil
import sys
import tempfile
import time
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore, PyRexStore
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.serializers import (
    JSONSerializer,
    MessagePackSerializer,
    PickleSerializer,
    ProtobufSerializer,
)


EDGE_TYPES = ("rel-a", "rel-b", "rel-c")
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


def seconds(func):
    """Return ``(result, elapsed_seconds)`` for ``func``."""
    started = time.perf_counter()
    result = func()
    return result, time.perf_counter() - started


def serializer_factory(name: str):
    if name == "pickle":
        return PickleSerializer()
    if name == "msgpack":
        return MessagePackSerializer()
    if name == "json":
        return JSONSerializer()
    if name == "protobuf":
        return ProtobufSerializer()
    raise ValueError(f"unknown serializer: {name}")


def open_graph(path: Path, backend: str, serializer_name: str, rocksdb_options: dict[str, object]) -> GraphDB:
    serializer = serializer_factory(serializer_name)
    if backend == "leveldb":
        return GraphDB(LevelDBStore(path=str(path)), serializer)
    if backend == "rocksdb":
        return GraphDB(PyRexStore(path=str(path), **rocksdb_options), serializer)
    raise ValueError(f"unknown backend: {backend}")


def chunks(total: int, chunk_size: int) -> Iterable[tuple[int, int]]:
    for start in range(0, total, chunk_size):
        yield start, min(start + chunk_size, total)


def make_node(index: int) -> Node:
    return Node(node_id=f"n{index}", labels=(f"L{index % 8}",), properties={"group": index % 128})


def edge_parts(index: int, nodes: int) -> tuple[str, str, str, str]:
    source = f"n{index % nodes}"
    target = f"n{(index * 9973 + 1) % nodes}"
    edge_type = EDGE_TYPES[index % len(EDGE_TYPES)]
    return f"e{index}", source, target, edge_type


def make_edge(index: int, nodes: int) -> Edge:
    edge_id, source, target, edge_type = edge_parts(index, nodes)
    return Edge(edge_id=edge_id, source=source, target=target, properties={"type": edge_type, "weight": index % 1000})


def pyarrow_array(values):
    try:
        import pyarrow as pa
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for --ingestion-modes arrow") from exc
    return pa.array(values)


def polars_frame(data: dict[str, list[object]]):
    try:
        import polars as pl
    except ImportError as exc:
        raise RuntimeError("polars is required for --ingestion-modes polars") from exc
    return pl.DataFrame(data)


def ingest_object(graph: GraphDB, nodes: int, edges: int, chunk_size: int) -> dict[str, float]:
    def write_nodes():
        for start, end in chunks(nodes, chunk_size):
            graph.put_nodes([make_node(index) for index in range(start, end)])

    def write_edges():
        for start, end in chunks(edges, chunk_size):
            graph.put_edges_bulk([make_edge(index, nodes) for index in range(start, end)], check_existing=False)

    _, node_seconds = seconds(write_nodes)
    _, edge_seconds = seconds(write_edges)
    return {
        "serialization_seconds": 0.0,
        "column_build_seconds": 0.0,
        "node_ingest_seconds": node_seconds,
        "edge_ingest_seconds": edge_seconds,
    }


def ingest_columnar(graph: GraphDB, nodes: int, edges: int, chunk_size: int, mode: str) -> dict[str, float]:
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
            edge_id, source, target, edge_type = edge_parts(index, nodes)
            edge_ids.append(edge_id)
            sources.append(source)
            targets.append(target)
            edge_types.append(edge_type)
        edge_values, serialize_seconds = seconds(
            lambda start=start, end=end: [graph.serialize_edge_value(make_edge(index, nodes)) for index in range(start, end)]
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


def typed_bfs(graph: GraphDB, start_node: str, *, limit: int, edge_types: tuple[str, ...] = EDGE_TYPES) -> int:
    """BFS over typed adjacency records so all ingestion modes are comparable."""
    visited = set()
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


def run_traversals(graph: GraphDB, nodes: int, args) -> dict[str, float | int]:
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


def disk_usage(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for root, _, files in os.walk(path):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except FileNotFoundError:
                pass
    return total


@contextmanager
def cpu_affinity(cores: int):
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        yield
        return
    original = os.sched_getaffinity(0)
    selected = set(sorted(original)[:cores])
    os.sched_setaffinity(0, selected)
    try:
        yield
    finally:
        os.sched_setaffinity(0, original)


def set_thread_env(cores: int) -> None:
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "POLARS_MAX_THREADS"):
        os.environ[name] = str(cores)


def rocksdb_configs(selected: list[str], cores: int) -> list[tuple[str, dict[str, object]]]:
    configs = {
        "default": {},
        "parallel": {"parallelism": cores, "max_background_jobs": cores},
        "parallel-buffer64mb-bloom10": {
            "parallelism": cores,
            "max_background_jobs": cores,
            "write_buffer_size": 64 * 1024 * 1024,
            "bloom_bits_per_key": 10,
        },
        "parallel-buffer64mb-bloom10-nowal": {
            "parallelism": cores,
            "max_background_jobs": cores,
            "write_buffer_size": 64 * 1024 * 1024,
            "bloom_bits_per_key": 10,
            "disable_wal": True,
        },
    }
    return [(name, configs[name]) for name in selected]


def validate_dependencies(backend: str, ingestion_mode: str, serializer: str) -> str | None:
    if backend == "leveldb" and importlib.util.find_spec("plyvel") is None:
        return "missing plyvel"
    if backend == "rocksdb" and importlib.util.find_spec("pyrex") is None:
        return "missing pyrex-rocksdb"
    if ingestion_mode == "arrow" and importlib.util.find_spec("pyarrow") is None:
        return "missing pyarrow"
    if ingestion_mode == "polars" and importlib.util.find_spec("polars") is None:
        return "missing polars"
    if serializer == "msgpack" and importlib.util.find_spec("msgpack") is None:
        return "missing msgpack"
    if serializer == "protobuf" and importlib.util.find_spec("google.protobuf") is None:
        return "missing protobuf"
    if serializer == "json" and ingestion_mode == "object":
        return "json cannot serialize legacy adjacency bytes written by object ingestion"
    return None


def base_result(args, backend: str, config_name: str, cores: int, nodes: int, ingestion_mode: str) -> dict[str, object]:
    semantics = "full_object_indexes_legacy_adjacency" if ingestion_mode == "object" else "append_only_typed_adjacency_only"
    return {
        "status": "ok",
        "skip_reason": "",
        "backend": backend,
        "backend_config": config_name,
        "cores": cores,
        "nodes": nodes,
        "edges": nodes,
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


def run_one(args, backend: str, config_name: str, rocksdb_options: dict[str, object], cores: int, nodes: int, ingestion_mode: str):
    result = base_result(args, backend, config_name, cores, nodes, ingestion_mode)
    skip_reason = validate_dependencies(backend, ingestion_mode, args.serializer)
    if skip_reason:
        result.update({"status": "skipped", "skip_reason": skip_reason})
        return result

    set_thread_env(cores)
    db_path = Path(tempfile.mkdtemp(prefix=f"gestaltdb_{backend}_{ingestion_mode}_{nodes}_", dir=args.tmp_dir))
    graph = None
    try:
        with cpu_affinity(cores):
            graph = open_graph(db_path, backend, args.serializer, rocksdb_options)
            result["native_columnar"] = bool(getattr(graph.store, "has_native_columnar_ingestion", lambda: False)())
            if ingestion_mode == "object":
                result.update(ingest_object(graph, nodes, nodes, args.chunk_size))
            else:
                result.update(ingest_columnar(graph, nodes, nodes, args.chunk_size, ingestion_mode))
            graph.close()
            graph = None

            def reopen():
                return open_graph(db_path, backend, args.serializer, rocksdb_options)

            graph, reopen_seconds = seconds(reopen)
            result["reopen_seconds"] = reopen_seconds
            result.update(run_traversals(graph, nodes, args))
            result["db_bytes"] = disk_usage(db_path)
    except Exception as exc:  # Keep long matrix runs moving and record failures.
        result.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if graph is not None:
            graph.close()
        if not args.keep_dbs:
            shutil.rmtree(db_path, ignore_errors=True)

    add_rates(result, nodes)
    return result


def add_rates(result: dict[str, object], nodes: int) -> None:
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
        numerator = result.get(numerator_key) if numerator_key else nodes
        result[rate_key] = float(numerator) / elapsed if isinstance(numerator, (int, float)) else ""


def write_result(output_dir: Path, result: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "matrix_results.jsonl"
    csv_path = output_dir / "matrix_results.csv"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GestaltDB benchmark matrix")
    parser.add_argument("--backends", nargs="+", choices=["leveldb", "rocksdb"], default=["leveldb", "rocksdb"])
    parser.add_argument("--sizes", nargs="+", type=int, default=[10_000, 100_000, 1_000_000])
    parser.add_argument("--cores", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--ingestion-modes", nargs="+", choices=["object", "arrow", "polars"], default=["object", "arrow", "polars"])
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
        choices=["default", "parallel", "parallel-buffer64mb-bloom10", "parallel-buffer64mb-bloom10-nowal"],
        default=["default", "parallel", "parallel-buffer64mb-bloom10"],
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for cores in args.cores:
        for nodes in args.sizes:
            for backend in args.backends:
                configs = [("default", {})] if backend == "leveldb" else rocksdb_configs(args.rocksdb_configs, cores)
                for config_name, rocksdb_options in configs:
                    for ingestion_mode in args.ingestion_modes:
                        label = f"backend={backend} config={config_name} cores={cores} nodes={nodes} mode={ingestion_mode}"
                        print(f"Running {label}", flush=True)
                        result = run_one(args, backend, config_name, rocksdb_options, cores, nodes, ingestion_mode)
                        write_result(args.output_dir, result)
                        status = result["status"]
                        edge_rate = result.get("edge_ingest_rate", "")
                        print(f"Finished {label} status={status} edge_rate={edge_rate}", flush=True)


if __name__ == "__main__":
    main()
