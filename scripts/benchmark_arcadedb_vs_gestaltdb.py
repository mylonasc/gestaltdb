#!/usr/bin/env python3
"""Compare gestaltdb/RocksDB with ArcadeDB graph workloads.

The suite intentionally includes workloads that stress different strengths:

* ``columnar_ingest`` uses gestaltdb's serialized Arrow column ingestion and
  ArcadeDB's embedded GraphBatch importer.
* ``star_traversal`` and ``bfs_depth`` favor native adjacency traversal.
* ``typed_path`` exercises repeated typed-edge expansion.
* ``rocksdb_compaction`` is a raw overwrite workload that targets RocksDB's LSM
  compaction behavior and is recorded as not applicable for ArcadeDB.

ArcadeDB is optional. If ``arcadedb-embedded`` is unavailable, ArcadeDB rows are
emitted with ``status=skipped`` so gestaltdb-only runs remain useful in CI and
local development.
"""

from __future__ import annotations

import argparse
import csv
from collections import deque
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import sys
import tempfile
import time
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import PyRexStore
from gestaltdb.serializers import MessagePackSerializer


EDGE_TYPES = ("RelA", "RelB", "RelC")
CSV_FIELDS = [
    "status",
    "skip_reason",
    "engine",
    "workload",
    "repetition",
    "nodes",
    "edges",
    "iterations",
    "batch_size",
    "graph_shape",
    "ingest_seconds",
    "query_seconds",
    "total_seconds",
    "nodes_per_second",
    "edges_per_second",
    "queries_per_second",
    "result_count",
    "db_bytes",
    "native_columnar",
    "arcadedb_path",
    "arcadedb_heap_size",
    "python",
    "platform",
]
SUMMARY_FIELDS = [
    "engine",
    "workload",
    "status",
    "runs",
    "nodes",
    "edges",
    "iterations",
    "batch_size",
    "graph_shape",
    "ingest_seconds_mean",
    "ingest_seconds_std",
    "query_seconds_mean",
    "query_seconds_std",
    "total_seconds_mean",
    "total_seconds_std",
    "nodes_per_second_mean",
    "nodes_per_second_std",
    "edges_per_second_mean",
    "edges_per_second_std",
    "queries_per_second_mean",
    "queries_per_second_std",
    "result_count_mean",
    "result_count_std",
    "db_bytes_mean",
    "db_bytes_std",
    "skip_reason",
]


def seconds(func):
    started = time.perf_counter()
    result = func()
    return result, time.perf_counter() - started


def chunks(total: int, chunk_size: int) -> Iterable[tuple[int, int]]:
    for start in range(0, total, chunk_size):
        yield start, min(start + chunk_size, total)


def graph_edges(shape: str, nodes: int, edges: int) -> Iterable[tuple[str, str, str, str]]:
    if shape == "star":
        for index in range(edges):
            target = 1 + (index % max(1, nodes - 1))
            yield f"e{index}", "n0", f"n{target}", "RelA"
        return

    if shape == "layered":
        for index in range(edges):
            source = index % nodes
            edge_type = EDGE_TYPES[index % len(EDGE_TYPES)]
            step = index % 31 + 1
            target = (source + step) % nodes
            yield f"e{index}", f"n{source}", f"n{target}", edge_type
        return

    if shape == "typed_path":
        for index in range(edges):
            source = index % nodes
            edge_type = EDGE_TYPES[index % 2]
            target = (source + 1) % nodes
            yield f"e{index}", f"n{source}", f"n{target}", edge_type
        return

    raise ValueError(f"unknown graph shape: {shape}")


def pyarrow_array(values):
    try:
        import pyarrow as pa
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for gestaltdb columnar ingestion") from exc
    return pa.array(values)


def open_gestaltdb(path: Path, args: argparse.Namespace, *, transactional: bool = False) -> GraphDB:
    return GraphDB(
        PyRexStore(
            path=str(path),
            parallelism=args.rocksdb_parallelism,
            max_background_jobs=args.rocksdb_background_jobs,
            write_buffer_size=args.rocksdb_write_buffer_size,
            bloom_bits_per_key=args.rocksdb_bloom_bits,
            disable_wal=args.rocksdb_disable_wal,
            transactional=transactional,
        ),
        MessagePackSerializer(),
    )


def ingest_gestaltdb_object(graph: GraphDB, shape: str, nodes: int, edges: int, batch_size: int) -> None:
    for start, end in chunks(nodes, batch_size):
        graph.put_nodes([Node(node_id=f"n{index}", labels=("Node",), properties={"id": f"n{index}"}) for index in range(start, end)])
    edge_rows = list(graph_edges(shape, nodes, edges))
    for start, end in chunks(len(edge_rows), batch_size):
        graph.put_edges_bulk(
            [
                Edge(edge_id=edge_id, source=source, target=target, properties={"type": edge_type})
                for edge_id, source, target, edge_type in edge_rows[start:end]
            ],
            check_existing=False,
        )


def ingest_gestaltdb_columnar(graph: GraphDB, shape: str, nodes: int, edges: int, batch_size: int) -> None:
    for start, end in chunks(nodes, batch_size):
        node_ids = [f"n{index}" for index in range(start, end)]
        node_values = [graph.serialize_node_value(Node(node_id=node_id, labels=("Node",), properties={"id": node_id})) for node_id in node_ids]
        graph.ingest_nodes_arrow(pyarrow_array(node_ids), pyarrow_array(node_values), chunk_size=batch_size)

    edge_rows = list(graph_edges(shape, nodes, edges))
    for start, end in chunks(len(edge_rows), batch_size):
        rows = edge_rows[start:end]
        edge_ids = [row[0] for row in rows]
        sources = [row[1] for row in rows]
        targets = [row[2] for row in rows]
        edge_types = [row[3] for row in rows]
        edge_values = [
            graph.serialize_edge_value(Edge(edge_id=edge_id, source=source, target=target, properties={"type": edge_type}))
            for edge_id, source, target, edge_type in rows
        ]
        graph.ingest_edges_arrow(
            pyarrow_array(edge_ids),
            pyarrow_array(sources),
            pyarrow_array(targets),
            pyarrow_array(edge_types),
            pyarrow_array(edge_values),
            append_only=True,
            chunk_size=batch_size,
        )


def typed_bfs(graph: GraphDB, start_node: str, depth: int, limit: int) -> int:
    visited = set()
    queue = deque([(graph.node_key_to_bytes(start_node), 0)])
    while queue and len(visited) < limit:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current_depth >= depth:
            continue
        for edge_type in EDGE_TYPES:
            for record in graph.iter_typed_adjacency(current, edge_type, direction="out"):
                neighbor = record["neighbor_id"]
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return len(visited)


def run_gestaltdb_query(graph: GraphDB, workload: str, args: argparse.Namespace) -> int:
    if workload == "star_traversal":
        count = 0
        for _ in range(args.iterations):
            count += len(graph.neighbors_by_edge_type("n0", "RelA", direction="out"))
        return count
    if workload == "bfs_depth":
        count = 0
        for _ in range(args.iterations):
            count += typed_bfs(graph, "n0", args.depth, args.bfs_limit)
        return count
    if workload == "typed_path":
        count = 0
        seeds = [f"n{index % args.nodes}" for index in range(args.iterations)]
        for seed in seeds:
            frontier = [graph.node_key_to_bytes(seed)]
            for edge_type in ("RelA", "RelB"):
                next_frontier = []
                for node_id in frontier:
                    next_frontier.extend(record["neighbor_id"] for record in graph.iter_typed_adjacency(node_id, edge_type, direction="out"))
                frontier = next_frontier[: args.path_fanout_limit]
            count += len(frontier)
        return count
    raise ValueError(f"unsupported gestaltdb query workload: {workload}")


def disk_usage(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except FileNotFoundError:
                pass
    return total


def setup_arcadedb(db) -> None:
    for command in (
        "CREATE VERTEX TYPE Node",
        "CREATE EDGE TYPE RelA",
        "CREATE EDGE TYPE RelB",
        "CREATE EDGE TYPE RelC",
        "CREATE PROPERTY Node.id STRING",
    ):
        try:
            db.command("sql", command)
        except Exception:
            pass


def ingest_arcadedb(db, shape: str, nodes: int, edges: int, batch_size: int, parallel: int) -> None:
    rid_lookup: dict[str, str] = {}
    with db.graph_batch(
        batch_size=batch_size,
        expected_edge_count=edges,
        bidirectional=False,
        commit_every=batch_size,
        use_wal=False,
        parallel_flush=parallel > 1,
    ) as batch:
        for start, end in chunks(nodes, batch_size):
            rows = [{"id": f"n{index}"} for index in range(start, end)]
            node_ids = [row["id"] for row in rows]
            rids = batch.create_vertices("Node", rows)
            rid_lookup.update(zip(node_ids, rids))

        for edge_id, source, target, edge_type in graph_edges(shape, nodes, edges):
            batch.new_edge(rid_lookup[source], edge_type, rid_lookup[target], id=edge_id)

    db.command("sql", "CREATE INDEX ON Node (id) UNIQUE_HASH")


def arcade_result_count(result) -> int:
    rows = result.to_list() if hasattr(result, "to_list") else list(result)
    if len(rows) == 1 and isinstance(rows[0], dict):
        for key in ("count", "degree", "size", "c"):
            value = rows[0].get(key)
            if isinstance(value, int):
                return value
            if value is not None:
                return int(value)
    return len(rows)


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_arcadedb_query(db, workload: str, args: argparse.Namespace) -> int:
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            total += arcade_result_count(db.query("sql", "SELECT expand(out('RelA')) FROM Node WHERE id = ?", "n0"))
        return total
    if workload == "bfs_depth":
        query = (
            f"MATCH {{type: Node, where: (id = {sql_string('n0')})}}.out('RelA')"
            f"{{as: n, while: ($depth < {args.depth}), where: ($depth > 0)}} RETURN n LIMIT {args.bfs_limit}"
        )
        total = 0
        for _ in range(args.iterations):
            total += arcade_result_count(db.query("sql", query))
        return total
    if workload == "typed_path":
        total = 0
        for index in range(args.iterations):
            node_id = f"n{index % args.nodes}"
            query = (
                f"MATCH {{type: Node, where: (id = {sql_string(node_id)})}}"
                f".out('RelA'){{}}.out('RelB'){{as: n}} RETURN n LIMIT {args.path_fanout_limit}"
            )
            total += arcade_result_count(db.query("sql", query))
        return total
    raise ValueError(f"unsupported ArcadeDB query workload: {workload}")


def base_row(engine: str, workload: str, args: argparse.Namespace) -> dict[str, object]:
    return {
        "status": "ok",
        "skip_reason": "",
        "engine": engine,
        "workload": workload,
        "repetition": "",
        "nodes": args.nodes,
        "edges": args.edges,
        "iterations": args.iterations,
        "batch_size": args.batch_size,
        "graph_shape": workload_shape(workload),
        "arcadedb_path": "",
        "arcadedb_heap_size": args.arcadedb_heap_size if engine == "arcadedb" else "",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def workload_shape(workload: str) -> str:
    if workload == "rocksdb_compaction":
        return "raw_kv"
    if workload == "star_traversal":
        return "star"
    if workload == "typed_path":
        return "typed_path"
    return "layered"


def add_rates(row: dict[str, object]) -> None:
    ingest_seconds = row.get("ingest_seconds")
    query_seconds = row.get("query_seconds")
    if isinstance(ingest_seconds, (int, float)) and ingest_seconds > 0:
        row["nodes_per_second"] = row["nodes"] / ingest_seconds
        row["edges_per_second"] = row["edges"] / ingest_seconds
    if isinstance(query_seconds, (int, float)) and query_seconds > 0:
        row["queries_per_second"] = row["iterations"] / query_seconds


def run_gestaltdb(workload: str, args: argparse.Namespace, *, transactional: bool = False) -> dict[str, object]:
    engine_name = "gestaltdb-rocksdb-transactional" if transactional else "gestaltdb-rocksdb"
    row = base_row(engine_name, workload, args)
    if importlib.util.find_spec("pyrex") is None:
        row.update({"status": "skipped", "skip_reason": "missing pyrex-rocksdb"})
        return row
    if workload == "columnar_ingest" and importlib.util.find_spec("pyarrow") is None:
        row.update({"status": "skipped", "skip_reason": "missing pyarrow"})
        return row

    path = Path(tempfile.mkdtemp(prefix=f"gestaltdb_arcade_compare_{workload}_", dir=args.tmp_dir))
    graph = None
    try:
        graph = open_gestaltdb(path, args, transactional=transactional)
        row["native_columnar"] = bool(getattr(graph.store, "has_native_columnar_ingestion", lambda: False)())
        shape = workload_shape(workload)
        if workload == "rocksdb_compaction":
            row["ingest_seconds"] = 0.0
            result_count, query_seconds = seconds(lambda: gestaltdb_compaction(graph.store, args))
            row["result_count"] = result_count
            row["query_seconds"] = query_seconds
        else:
            ingest = ingest_gestaltdb_columnar if workload == "columnar_ingest" else ingest_gestaltdb_object
            _, ingest_seconds = seconds(lambda: ingest(graph, shape, args.nodes, args.edges, args.batch_size))
            row["ingest_seconds"] = ingest_seconds
        if workload == "columnar_ingest":
            row["result_count"] = args.nodes + args.edges
            row["query_seconds"] = 0.0
        elif workload != "rocksdb_compaction":
            result_count, query_seconds = seconds(lambda: run_gestaltdb_query(graph, workload, args))
            row["result_count"] = result_count
            row["query_seconds"] = query_seconds
        row["db_bytes"] = disk_usage(path)
        row["total_seconds"] = row.get("ingest_seconds", 0.0) + row.get("query_seconds", 0.0)
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if graph is not None:
            graph.close()
        if not args.keep_dbs:
            shutil.rmtree(path, ignore_errors=True)
    add_rates(row)
    return row


def gestaltdb_compaction(store: PyRexStore, args: argparse.Namespace) -> int:
    value = b"x" * args.compaction_value_size
    total = 0
    for pass_index in range(args.compaction_passes):
        batch = store._pyrex.PyWriteBatch()
        for position in range(args.compaction_keys):
            index = ((position * 1_000_003) + (pass_index * 9_176)) % args.compaction_keys
            batch.put(store._key(b"C", f"k{index:012d}".encode("ascii")), value)
            total += 1
        store.db.write(batch, store.write_options)
    return total


def run_arcadedb(workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row("arcadedb", workload, args)
    if workload == "rocksdb_compaction":
        row.update({"status": "skipped", "skip_reason": "not applicable: raw RocksDB LSM overwrite workload"})
        return row
    if importlib.util.find_spec("arcadedb_embedded") is None:
        row.update({"status": "skipped", "skip_reason": "missing arcadedb-embedded"})
        return row

    import arcadedb_embedded as arcadedb

    path = Path(tempfile.mkdtemp(prefix=f"arcadedb_embedded_{workload}_", dir=args.tmp_dir))
    row["arcadedb_path"] = str(path)
    db = None
    try:
        kwargs = {"jvm_kwargs": {"heap_size": args.arcadedb_heap_size}} if args.arcadedb_heap_size else {}
        db = arcadedb.create_database(str(path), **kwargs)
        setup_arcadedb(db)
        shape = workload_shape(workload)
        _, ingest_seconds = seconds(lambda: ingest_arcadedb(db, shape, args.nodes, args.edges, args.batch_size, args.arcadedb_parallel))
        row["ingest_seconds"] = ingest_seconds
        if workload == "columnar_ingest":
            row["result_count"] = args.nodes + args.edges
            row["query_seconds"] = 0.0
        else:
            result_count, query_seconds = seconds(lambda: run_arcadedb_query(db, workload, args))
            row["result_count"] = result_count
            row["query_seconds"] = query_seconds
        row["db_bytes"] = disk_usage(path)
        row["total_seconds"] = row.get("ingest_seconds", 0.0) + row.get("query_seconds", 0.0)
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if db is not None:
            db.close()
        if not args.keep_dbs:
            shutil.rmtree(path, ignore_errors=True)
    add_rates(row)
    return row


def write_row(output_dir: Path, row: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "arcadedb_vs_gestaltdb_results.jsonl"
    csv_path = output_dir / "arcadedb_vs_gestaltdb_results.csv"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def numeric_values(rows: list[dict[str, object]], key: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def mean_std(rows: list[dict[str, object]], key: str) -> tuple[float | str, float | str]:
    values = numeric_values(rows, key)
    if not values:
        return "", ""
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, std


def summarize_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row.get("engine", "")), str(row.get("workload", ""))), []).append(row)

    summaries = []
    for (engine, workload), group in sorted(grouped.items()):
        ok_rows = [row for row in group if row.get("status") == "ok"]
        source_rows = ok_rows or group
        first = source_rows[0]
        summary = {
            "engine": engine,
            "workload": workload,
            "status": "ok" if ok_rows else str(first.get("status", "")),
            "runs": len(ok_rows),
            "nodes": first.get("nodes", ""),
            "edges": first.get("edges", ""),
            "iterations": first.get("iterations", ""),
            "batch_size": first.get("batch_size", ""),
            "graph_shape": first.get("graph_shape", ""),
            "skip_reason": "" if ok_rows else first.get("skip_reason", ""),
        }
        for key in (
            "ingest_seconds",
            "query_seconds",
            "total_seconds",
            "nodes_per_second",
            "edges_per_second",
            "queries_per_second",
            "result_count",
            "db_bytes",
        ):
            mean, std = mean_std(ok_rows, key)
            summary[f"{key}_mean"] = mean
            summary[f"{key}_std"] = std
        summaries.append(summary)
    return summaries


def write_summary(output_dir: Path, rows: list[dict[str, object]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = summarize_rows(rows)
    jsonl_path = output_dir / "arcadedb_vs_gestaltdb_summary.jsonl"
    csv_path = output_dir / "arcadedb_vs_gestaltdb_summary.csv"
    with jsonl_path.open("w", encoding="utf-8") as jsonl, csv_path.open("w", newline="", encoding="utf-8") as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in summaries:
            jsonl.write(json.dumps(row, sort_keys=True) + "\n")
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark gestaltdb/RocksDB against ArcadeDB graph workloads")
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=["gestaltdb", "gestaltdb-tx", "arcadedb"],
        default=["gestaltdb", "gestaltdb-tx", "arcadedb"],
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=["columnar_ingest", "star_traversal", "bfs_depth", "typed_path", "rocksdb_compaction"],
        default=["columnar_ingest", "star_traversal", "bfs_depth", "typed_path", "rocksdb_compaction"],
    )
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--edges", type=int, default=50_000)
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--bfs-limit", type=int, default=100_000)
    parser.add_argument("--path-fanout-limit", type=int, default=1_000)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/arcadedb_vs_gestaltdb"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=int, default=10)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    parser.add_argument("--compaction-keys", type=int, default=50_000)
    parser.add_argument("--compaction-passes", type=int, default=4)
    parser.add_argument("--compaction-value-size", type=int, default=1024)
    parser.add_argument("--arcadedb-heap-size", default="2g")
    parser.add_argument("--arcadedb-parallel", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runners = {
        "gestaltdb": run_gestaltdb,
        "gestaltdb-tx": lambda workload, args: run_gestaltdb(workload, args, transactional=True),
        "arcadedb": run_arcadedb,
    }
    rows = []
    for repetition in range(1, args.repetitions + 1):
        for workload in args.workloads:
            for engine in args.engines:
                label = f"repetition={repetition}/{args.repetitions} engine={engine} workload={workload} nodes={args.nodes} edges={args.edges}"
                print(f"Running {label}", flush=True)
                row = runners[engine](workload, args)
                row["repetition"] = repetition
                rows.append(row)
                write_row(args.output_dir, row)
                print(f"Finished {label} status={row['status']} total_seconds={row.get('total_seconds', '')}", flush=True)
    write_summary(args.output_dir, rows)


if __name__ == "__main__":
    main()
