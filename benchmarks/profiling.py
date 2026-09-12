"""Profile GestaltDB end-to-end columnar ingestion paths.

The script separates payload serialization, node ingestion, and edge ingestion so
profiles can show whether bottlenecks are in serialization, Python-side column
normalization/key construction, index maintenance, or backend writes.
"""

from __future__ import annotations

import argparse
import cProfile
import csv
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import pstats
import random
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from gestaltdb.graphdb import Edge, GraphDB, GraphEntityDictSerializer, Node
from gestaltdb.kvstores import LevelDBStore, PyRexStore
from gestaltdb.serializers import JSONSerializer, PickleSerializer
from benchmarks.common.datasets import BIO_EDGE_TYPES
from benchmarks.common.reporting import ResultWriter
from benchmarks.common.storage import has_module
from benchmarks.common.timing import seconds


@dataclass(frozen=True)
class Dataset:
    node_ids: list[str]
    edge_ids: list[str]
    sources: list[str]
    targets: list[str]
    edge_types: list[str]
    node_kinds: list[str]
    node_groups: list[int]
    edge_weights: list[int]
    nodes: list[Node]
    edges: list[Edge]


def profile_phase(profile_path: Path, func: Callable[[], Any]) -> tuple[Any, float]:
    """Execute ``func()`` under cProfile and save stats."""
    profiler = cProfile.Profile()
    started = time.perf_counter()
    result = profiler.runcall(func)
    elapsed = time.perf_counter() - started
    profiler.dump_stats(str(profile_path))
    return result, elapsed


def write_profile_text(profile_path: Path, text_path: Path, limit: int) -> None:
    """Format and write human-readable pstats output."""
    with text_path.open("w", encoding="utf-8") as handle:
        stats = pstats.Stats(str(profile_path), stream=handle)
        stats.strip_dirs().sort_stats("cumulative").print_stats(limit)
        handle.write("\n--- internal time ---\n")
        stats.sort_stats("tottime").print_stats(limit)


def make_dataset(num_nodes: int, num_edges: int, seed: int) -> Dataset:
    """Generate deterministic synthetic dataset for profiling."""
    rng = random.Random(seed)
    node_ids = [f"n{index}" for index in range(num_nodes)]
    node_kinds = ["entity"] * num_nodes
    node_groups = [index % 10 for index in range(num_nodes)]
    nodes = [
        Node(node_id=node_id, labels=["Entity"], properties={"kind": kind, "group": group})
        for node_id, kind, group in zip(node_ids, node_kinds, node_groups)
    ]

    edge_ids = [f"e{index}" for index in range(num_edges)]
    sources = [f"n{rng.randrange(num_nodes)}" for _ in range(num_edges)]
    targets = [f"n{rng.randrange(num_nodes)}" for _ in range(num_edges)]
    edge_types = [BIO_EDGE_TYPES[index % len(BIO_EDGE_TYPES)] for index in range(num_edges)]
    edge_weights = [index % 100 for index in range(num_edges)]
    edges = [
        Edge(edge_id=edge_id, source=source, target=target, properties={"type": edge_type, "weight": weight})
        for edge_id, source, target, edge_type, weight in zip(edge_ids, sources, targets, edge_types, edge_weights)
    ]

    return Dataset(
        node_ids=node_ids,
        edge_ids=edge_ids,
        sources=sources,
        targets=targets,
        edge_types=edge_types,
        node_kinds=node_kinds,
        node_groups=node_groups,
        edge_weights=edge_weights,
        nodes=nodes,
        edges=edges,
    )


def serialize_python_entities(dataset: Dataset, serializer_cls: Any) -> dict[str, Any]:
    """Serialize entities using pure Python serializer."""
    serializer = GraphEntityDictSerializer(serializer_cls())
    return {
        "node_ids": dataset.node_ids,
        "node_values": [serializer.serialize(node, "Node") for node in dataset.nodes],
        "edge_ids": dataset.edge_ids,
        "sources": dataset.sources,
        "targets": dataset.targets,
        "edge_types": dataset.edge_types,
        "edge_values": [serializer.serialize(edge, "Edge") for edge in dataset.edges],
    }


def serialize_json_entities_with_polars(dataset: Dataset) -> dict[str, Any]:
    """Serialize entities into JSON payloads using vectorized Polars struct json encoding."""
    import polars as pl
    import pyarrow as pa

    node_df = pl.DataFrame(
        {
            "node_id": dataset.node_ids,
            "labels": [["Entity"] for _ in dataset.node_ids],
            "kind": dataset.node_kinds,
            "group": dataset.node_groups,
        }
    )
    edge_df = pl.DataFrame(
        {
            "edge_id": dataset.edge_ids,
            "source": dataset.sources,
            "target": dataset.targets,
            "edge_type": dataset.edge_types,
            "weight": dataset.edge_weights,
        }
    )

    node_values = node_df.select(
        pl.struct(
            [
                pl.col("node_id").alias("id"),
                pl.struct(["kind", "group"]).alias("properties"),
                pl.col("labels"),
            ]
        ).struct.json_encode().alias("node_value")
    )["node_value"].to_arrow().cast(pa.binary())

    edge_values = edge_df.select(
        pl.struct(
            [
                pl.col("edge_id").alias("id"),
                pl.col("source"),
                pl.col("target"),
                pl.struct([pl.col("edge_type").alias("type"), pl.col("weight")]).alias("properties"),
            ]
        ).struct.json_encode().alias("edge_value")
    )["edge_value"].to_arrow().cast(pa.binary())

    return {
        "node_ids": node_df["node_id"].to_arrow(),
        "node_values": node_values,
        "edge_ids": edge_df["edge_id"].to_arrow(),
        "sources": edge_df["source"].to_arrow(),
        "targets": edge_df["target"].to_arrow(),
        "edge_types": edge_df["edge_type"].to_arrow(),
        "edge_values": edge_values,
    }


def open_graph(case: dict[str, Any], path: Path, args: argparse.Namespace) -> GraphDB:
    """Open graph instance for profile case."""
    serializer_cls = case["serializer_cls"]
    if case["backend"] == "leveldb":
        return GraphDB(LevelDBStore(path=str(path)), serializer_cls())
    if case["backend"] == "rocksdb":
        return GraphDB(
            PyRexStore(
                path=str(path),
                parallelism=args.rocksdb_parallelism,
                max_background_jobs=args.rocksdb_max_background_jobs,
                write_buffer_size=args.rocksdb_write_buffer_size,
                bloom_bits_per_key=args.rocksdb_bloom_bits,
                disable_wal=args.rocksdb_disable_wal,
            ),
            serializer_cls(),
        )
    raise ValueError(f"unknown backend: {case['backend']}")


def benchmark_cases() -> dict[str, dict[str, Any]]:
    """Define profiling comparison cases."""
    return {
        "leveldb-pickle-python": {
            "backend": "leveldb",
            "serializer": "pickle",
            "serializer_cls": PickleSerializer,
            "serialization_path": "python objects + PickleSerializer",
            "serialize": lambda dataset: serialize_python_entities(dataset, PickleSerializer),
            "requires": ["plyvel"],
        },
        "rocksdb-pickle-python": {
            "backend": "rocksdb",
            "serializer": "pickle",
            "serializer_cls": PickleSerializer,
            "serialization_path": "python objects + PickleSerializer",
            "serialize": lambda dataset: serialize_python_entities(dataset, PickleSerializer),
            "requires": ["pyrex"],
        },
        "rocksdb-json-python": {
            "backend": "rocksdb",
            "serializer": "json",
            "serializer_cls": JSONSerializer,
            "serialization_path": "python objects + JSONSerializer",
            "serialize": lambda dataset: serialize_python_entities(dataset, JSONSerializer),
            "requires": ["pyrex"],
        },
        "rocksdb-json-polars": {
            "backend": "rocksdb",
            "serializer": "json",
            "serializer_cls": JSONSerializer,
            "serialization_path": "Polars struct.json_encode + Arrow binary payloads",
            "serialize": serialize_json_entities_with_polars,
            "requires": ["pyrex", "polars", "pyarrow"],
        },
    }


def case_available(case: dict[str, Any]) -> tuple[bool, str]:
    """Check whether all dependencies for a profile case exist."""
    for package in case["requires"]:
        if not has_module(str(package)):
            return False, f"missing dependency: {package}"
    return True, ""


def run_case(
    case_name: str,
    case: dict[str, Any],
    dataset: Dataset,
    output_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Execute profiling run for one ingestion case."""
    available, reason = case_available(case)
    row: dict[str, Any] = {
        "case": case_name,
        "backend": case["backend"],
        "serializer": case["serializer"],
        "serialization_path": case["serialization_path"],
        "nodes": args.nodes,
        "edges": args.edges,
        "batch_size": args.batch_size,
        "index_mode": args.index_mode,
        "node_append_only": args.node_append_only,
        "rebuild_deferred": args.rebuild_deferred,
        "node_index_properties": ",".join(args.node_index_property),
        "edge_index_properties": ",".join(args.edge_index_property),
    }
    if not available:
        row.update({"status": "skipped", "skip_reason": reason})
        return row

    case_dir = output_dir / case_name
    case_dir.mkdir(parents=True, exist_ok=True)

    payloads, serialization_seconds = profile_phase(
        case_dir / "serialization.prof",
        lambda: case["serialize"](dataset),
    )
    write_profile_text(case_dir / "serialization.prof", case_dir / "serialization.txt", args.profile_lines)

    path = Path(tempfile.mkdtemp(prefix=f"gestaltdb_profile_{case_name}_"))
    graph = open_graph(case, path, args)
    try:
        for property_name in args.node_index_property:
            graph.create_node_property_index(property_name)
        for property_name in args.edge_index_property:
            graph.create_edge_property_index(property_name)
        native_columnar = bool(getattr(graph.store, "has_native_columnar_ingestion", lambda: False)())
        _, node_ingest_seconds = profile_phase(
            case_dir / "node_ingestion.prof",
            lambda: graph.ingest_nodes_arrow(
                payloads["node_ids"],
                payloads["node_values"],
                chunk_size=args.batch_size,
                append_only=args.node_append_only,
                index_mode=args.index_mode,
            ),
        )
        write_profile_text(case_dir / "node_ingestion.prof", case_dir / "node_ingestion.txt", args.profile_lines)

        _, edge_ingest_seconds = profile_phase(
            case_dir / "edge_ingestion.prof",
            lambda: graph.ingest_edges_arrow(
                payloads["edge_ids"],
                payloads["sources"],
                payloads["targets"],
                payloads["edge_types"],
                payloads["edge_values"],
                chunk_size=args.batch_size,
                append_only=True,
                index_mode=args.index_mode,
            ),
        )
        write_profile_text(case_dir / "edge_ingestion.prof", case_dir / "edge_ingestion.txt", args.profile_lines)

        rebuild_seconds = 0.0
        if args.index_mode == "defer" and args.rebuild_deferred:
            _, rebuild_seconds = profile_phase(
                case_dir / "rebuild_indexes.prof",
                graph.rebuild_deferred_indexes,
            )
            write_profile_text(case_dir / "rebuild_indexes.prof", case_dir / "rebuild_indexes.txt", args.profile_lines)

        total_ingest_seconds = node_ingest_seconds + edge_ingest_seconds + rebuild_seconds
        total_seconds = serialization_seconds + total_ingest_seconds

        row.update(
            {
                "status": "ok",
                "native_columnar": native_columnar,
                "serialization_seconds": serialization_seconds,
                "node_ingest_seconds": node_ingest_seconds,
                "edge_ingest_seconds": edge_ingest_seconds,
                "rebuild_seconds": rebuild_seconds,
                "total_ingest_seconds": total_ingest_seconds,
                "total_seconds": total_seconds,
                "node_rate": args.nodes / node_ingest_seconds if node_ingest_seconds > 0 else 0,
                "edge_rate": args.edges / edge_ingest_seconds if edge_ingest_seconds > 0 else 0,
                "total_ingest_rate": args.edges / total_ingest_seconds if total_ingest_seconds > 0 else 0,
            }
        )
    finally:
        graph.close()
        shutil.rmtree(path, ignore_errors=True)
    return row


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build argument parser for profiling benchmark."""
    parser = subparser or argparse.ArgumentParser(description="Profile GestaltDB ingestion paths")
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--edges", type=int, default=50_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--profile-lines", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/ingestion_profiles"))
    parser.add_argument("--cases", nargs="+", choices=list(benchmark_cases().keys()), default=list(benchmark_cases().keys()))
    parser.add_argument("--index-mode", choices=["maintain", "defer"], default="maintain")
    parser.add_argument("--rebuild-deferred", action="store_true")
    parser.add_argument("--node-append-only", action="store_true")
    parser.add_argument("--node-index-property", action="append", default=["kind", "group"])
    parser.add_argument("--edge-index-property", action="append", default=["weight"])
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-max-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=float, default=10.0)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset = make_dataset(args.nodes, args.edges, args.seed)
    cases = benchmark_cases()

    results: list[dict[str, Any]] = []
    for case_name in args.cases:
        print(f"Running profile case: {case_name}")
        row = run_case(case_name, cases[case_name], dataset, args.output_dir, args)
        results.append(row)
        if row.get("status") == "ok":
            print(
                f"  serialize={row['serialization_seconds']:.3f}s | "
                f"node_ingest={row['node_ingest_seconds']:.3f}s | "
                f"edge_ingest={row['edge_ingest_seconds']:.3f}s | "
                f"total={row['total_seconds']:.3f}s"
            )
        else:
            print(f"  status={row.get('status')} ({row.get('skip_reason')})")

    summary_json = args.output_dir / "summary.json"
    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Summary written to {summary_json}")


if __name__ == "__main__":
    main()
