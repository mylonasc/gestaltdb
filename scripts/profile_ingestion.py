#!/usr/bin/env python3
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
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gestaltdb.graphdb import Edge, GraphDB, GraphEntityDictSerializer, Node
from gestaltdb.kvstores import LevelDBStore, PyRexStore
from gestaltdb.serializers import JSONSerializer, PickleSerializer


EDGE_TYPES = ("drug-to-protein", "protein-to-disease", "drug-to-disease")


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


def seconds(func: Callable[[], object]) -> tuple[object, float]:
    started = time.perf_counter()
    result = func()
    return result, time.perf_counter() - started


def profile_phase(profile_path: Path, func: Callable[[], object]) -> tuple[object, float]:
    profiler = cProfile.Profile()
    started = time.perf_counter()
    result = profiler.runcall(func)
    elapsed = time.perf_counter() - started
    profiler.dump_stats(str(profile_path))
    return result, elapsed


def write_profile_text(profile_path: Path, text_path: Path, limit: int) -> None:
    with text_path.open("w", encoding="utf-8") as handle:
        stats = pstats.Stats(str(profile_path), stream=handle)
        stats.strip_dirs().sort_stats("cumulative").print_stats(limit)
        handle.write("\n--- internal time ---\n")
        stats.sort_stats("tottime").print_stats(limit)


def make_dataset(num_nodes: int, num_edges: int, seed: int) -> Dataset:
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
    edge_types = [EDGE_TYPES[index % len(EDGE_TYPES)] for index in range(num_edges)]
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


def serialize_python_entities(dataset: Dataset, serializer_cls) -> dict[str, object]:
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


def serialize_json_entities_with_polars(dataset: Dataset) -> dict[str, object]:
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


def open_graph(case: dict[str, object], path: Path, args: argparse.Namespace) -> GraphDB:
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


def validate_ingestion(graph: GraphDB, dataset: Dataset) -> None:
    assert graph.store.get_node(dataset.node_ids[0].encode("utf-8")) is not None
    assert graph.store.get_edge(dataset.edge_ids[0].encode("utf-8")) is not None
    assert graph.neighbors_by_edge_type(dataset.sources[0], dataset.edge_types[0], direction="out")


def benchmark_cases() -> dict[str, dict[str, object]]:
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


def case_available(case: dict[str, object]) -> tuple[bool, str]:
    for package in case["requires"]:
        if importlib.util.find_spec(str(package)) is None:
            return False, f"missing dependency: {package}"
    return True, ""


def run_case(case_name: str, case: dict[str, object], dataset: Dataset, output_dir: Path, args: argparse.Namespace) -> dict[str, object]:
    available, reason = case_available(case)
    row: dict[str, object] = {
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
                append_only=True,
                chunk_size=args.batch_size,
                index_mode=args.index_mode,
            ),
        )
        write_profile_text(case_dir / "edge_ingestion.prof", case_dir / "edge_ingestion.txt", args.profile_lines)
        rebuild_seconds = 0.0
        rebuild_result = {}
        if args.rebuild_deferred:
            rebuild_result, rebuild_seconds = seconds(graph.rebuild_deferred_indexes)
        validate_ingestion(graph, dataset)
    finally:
        graph.close()
        shutil.rmtree(path, ignore_errors=True)

    total_seconds = serialization_seconds + node_ingest_seconds + edge_ingest_seconds + rebuild_seconds
    row.update(
        {
            "status": "ok",
            "skip_reason": "",
            "native_columnar": native_columnar,
            "serialization_seconds": serialization_seconds,
            "node_ingest_seconds": node_ingest_seconds,
            "edge_ingest_seconds": edge_ingest_seconds,
            "rebuild_seconds": rebuild_seconds,
            "rebuild_result": rebuild_result,
            "total_seconds": total_seconds,
            "serialization_share": serialization_seconds / total_seconds if total_seconds else None,
            "node_ingest_share": node_ingest_seconds / total_seconds if total_seconds else None,
            "edge_ingest_share": edge_ingest_seconds / total_seconds if total_seconds else None,
            "edge_rate_total": args.edges / total_seconds if total_seconds else None,
            "profile_dir": str(case_dir),
        }
    )
    return row


def write_rows(output_dir: Path, rows: list[dict[str, object]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = [
        "status",
        "skip_reason",
        "case",
        "backend",
        "serializer",
        "serialization_path",
        "native_columnar",
        "nodes",
        "edges",
        "batch_size",
        "index_mode",
        "node_append_only",
        "rebuild_deferred",
        "node_index_properties",
        "edge_index_properties",
        "serialization_seconds",
        "node_ingest_seconds",
        "edge_ingest_seconds",
        "rebuild_seconds",
        "total_seconds",
        "serialization_share",
        "node_ingest_share",
        "edge_ingest_share",
        "edge_rate_total",
        "profile_dir",
    ]
    with (output_dir / "timings.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    (output_dir / "timings.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def warm_optional_imports(selected: list[str], cases: dict[str, dict[str, object]]) -> None:
    """Import optional libraries before cProfile starts measuring phases."""
    required = {package for case_name in selected for package in cases[case_name]["requires"]}
    for package in sorted(required):
        if importlib.util.find_spec(package) is not None:
            __import__(package)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile GestaltDB ingestion phases")
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--edges", type=int, default=50_000)
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--index-mode", choices=["maintain", "defer"], default="maintain")
    parser.add_argument("--node-append-only", action="store_true", help="skip existing-node index deletion for known-new nodes")
    parser.add_argument("--rebuild-deferred", action="store_true", help="time graph.rebuild_deferred_indexes() after ingestion")
    parser.add_argument("--node-index-property", action="append", default=[], help="node property index to register before ingestion")
    parser.add_argument("--edge-index-property", action="append", default=[], help="edge property index to register before ingestion")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--case", action="append", choices=sorted(benchmark_cases()), help="case to run; may be repeated")
    parser.add_argument("--output-dir", default="benchmark_results/ingestion_profiles")
    parser.add_argument("--profile-lines", type=int, default=30)
    parser.add_argument("--rocksdb-parallelism", type=int, default=None)
    parser.add_argument("--rocksdb-max-background-jobs", type=int, default=None)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=None)
    parser.add_argument("--rocksdb-bloom-bits", type=float, default=None)
    parser.add_argument("--rocksdb-disable-wal", action="store_true", default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = args.case or list(benchmark_cases())
    cases = benchmark_cases()
    warm_optional_imports(selected, cases)

    print(f"building dataset nodes={args.nodes:,} edges={args.edges:,}")
    dataset, dataset_seconds = seconds(lambda: make_dataset(args.nodes, args.edges, args.seed))
    print(f"dataset build: {dataset_seconds:.4f}s (not included in profile totals)")

    rows = []
    for case_name in selected:
        print(f"profiling {case_name}...")
        row = run_case(case_name, cases[case_name], dataset, output_dir, args)
        rows.append(row)
        if row["status"] == "ok":
            print(
                f"  total={row['total_seconds']:.4f}s serialization={row['serialization_seconds']:.4f}s "
                f"nodes={row['node_ingest_seconds']:.4f}s edges={row['edge_ingest_seconds']:.4f}s "
                f"rebuild={row.get('rebuild_seconds', 0.0):.4f}s"
            )
        else:
            print(f"  skipped: {row['skip_reason']}")

    write_rows(output_dir, rows)
    print(f"wrote profiles and timings under {output_dir}")


if __name__ == "__main__":
    main()
