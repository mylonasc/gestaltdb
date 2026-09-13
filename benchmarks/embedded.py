"""Benchmark embedded Python graph databases with optional dependencies.

The runner compares GestaltDB with LatticeDB and LadybugDB on deterministic
property-graph shapes. Third-party engines are optional: missing packages emit
``status=skipped`` rows so local and CI runs remain useful.
"""

from __future__ import annotations

import argparse
from collections import deque
import csv
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
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import PyRexStore
from gestaltdb.serializers import MessagePackSerializer
from benchmarks.common.datasets import (
    NAMED_EDGE_TYPES,
    chunks,
    edge_parts,
    seed_ids,
    workload_graph_shape,
)
from benchmarks.common.reporting import (
    ResultWriter,
    add_rates,
    base_row,
    count_status,
    summarize_rows,
)
from benchmarks.common.storage import disk_usage, has_pyrex
from benchmarks.common.timing import reservoir_count, seconds
from benchmarks.common.workloads import (
    count_gestaltdb,
    gestaltdb_bfs,
    gestaltdb_neighbors,
    gestaltdb_sample_neighbors,
    gestaltdb_star_traversal,
    gestaltdb_typed_path,
)

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
    "sample_size",
    "depth",
    "bfs_limit",
    "ingest_seconds",
    "query_seconds",
    "reopen_seconds",
    "count_seconds",
    "total_seconds",
    "nodes_per_second",
    "edges_per_second",
    "queries_per_second",
    "result_count",
    "actual_nodes",
    "actual_edges",
    "count_status",
    "db_bytes",
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
    "sample_size",
    "depth",
    "ingest_seconds_mean",
    "ingest_seconds_std",
    "query_seconds_mean",
    "query_seconds_std",
    "reopen_seconds_mean",
    "reopen_seconds_std",
    "count_seconds_mean",
    "count_seconds_std",
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
    "actual_nodes_mean",
    "actual_nodes_std",
    "actual_edges_mean",
    "actual_edges_std",
    "db_bytes_mean",
    "db_bytes_std",
    "skip_reason",
]


def run_gestaltdb(workload: str, args: argparse.Namespace) -> dict[str, Any]:
    """Execute GestaltDB embedded benchmark run."""
    row = base_row("gestaltdb-rocksdb", workload, args)
    if not has_pyrex():
        row.update({"status": "skipped", "skip_reason": "missing pyrex-rocksdb"})
        return row

    path = Path(tempfile.mkdtemp(prefix="gestaltdb_embedded_bench_", dir=args.tmp_dir))
    graph: GraphDB | None = None
    try:
        graph = GraphDB(
            PyRexStore(
                path=str(path),
                parallelism=args.rocksdb_parallelism,
                max_background_jobs=args.rocksdb_background_jobs,
                write_buffer_size=args.rocksdb_write_buffer_size,
                bloom_bits_per_key=args.rocksdb_bloom_bits,
                disable_wal=args.rocksdb_disable_wal,
            ),
            MessagePackSerializer(),
        )
        _, row["ingest_seconds"] = seconds(lambda: ingest_gestaltdb(graph, workload, args))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_gestaltdb(graph))
        row["count_status"] = count_status(row, args.nodes, args.edges)
        row["result_count"], row["query_seconds"] = seconds(lambda: run_gestaltdb_workload(graph, workload, args))
        row["db_bytes"] = disk_usage(path)
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if graph is not None:
            graph.close()
        if not args.keep_dbs:
            shutil.rmtree(path, ignore_errors=True)
    add_rates(row)
    return row


def ingest_gestaltdb(graph: GraphDB, workload: str, args: argparse.Namespace) -> None:
    """Ingest synthetic nodes and edges into GestaltDB."""
    shape = workload_graph_shape(workload, args.graph_shape)
    for start, end in chunks(args.nodes, args.batch_size):
        graph.put_nodes([
            Node(node_id=f"n{index}", labels=("Node",), properties={"id": f"n{index}", "group": index % 128})
            for index in range(start, end)
        ])
    for start, end in chunks(args.edges, args.batch_size):
        graph.put_edges_bulk(
            [
                Edge(edge_id=edge_id, source=source, target=target, properties={"type": edge_type, "weight": index % 1000})
                for index, (edge_id, source, target, edge_type) in (
                    (index, edge_parts(index, args.nodes, shape)) for index in range(start, end)
                )
            ],
            check_existing=False,
        )


def run_gestaltdb_workload(graph: GraphDB, workload: str, args: argparse.Namespace) -> int:
    """Dispatch GestaltDB traversal workloads."""
    seeds = seed_ids(args.iterations, args.nodes)
    if workload == "ingest":
        return args.nodes + args.edges
    if workload == "star_traversal":
        return gestaltdb_star_traversal(graph, seed_id="n0", iterations=args.iterations)
    if workload == "neighbors":
        return gestaltdb_neighbors(graph, seeds)
    if workload == "sample_neighbors":
        return gestaltdb_sample_neighbors(graph, seeds, sample_size=args.sample_size, rng=random.Random(args.seed))
    if workload == "typed_path":
        return gestaltdb_typed_path(graph, seeds, fanout_limit=args.path_fanout_limit)
    if workload == "bfs_depth":
        return gestaltdb_bfs(graph, "n0", depth=args.depth, limit=args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def run_latticedb(workload: str, args: argparse.Namespace) -> dict[str, Any]:
    """Execute LatticeDB embedded benchmark run."""
    row = base_row("latticedb", workload, args)
    if importlib.util.find_spec("latticedb") is None:
        row.update({"status": "skipped", "skip_reason": "missing latticedb"})
        return row

    from latticedb import Database

    path = Path(tempfile.mkdtemp(prefix="latticedb_embedded_bench_", dir=args.tmp_dir)) / "graph.lattice"
    db = None
    node_ids: list[int] = []
    try:
        db = Database(
            path,
            create=True,
            cache_size_mb=args.latticedb_cache_size_mb,
            enable_wal=not args.latticedb_disable_wal,
            enable_adjacency_cache=args.latticedb_adjacency_cache,
        )
        db.open()
        _, row["ingest_seconds"] = seconds(lambda: node_ids.extend(ingest_latticedb(db, workload, args)))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_latticedb(db))
        row["count_status"] = count_status(row, args.nodes, args.edges)
        row["result_count"], row["query_seconds"] = seconds(lambda: run_latticedb_workload(db, node_ids, workload, args))
        row["db_bytes"] = path.stat().st_size if path.exists() else 0
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if db is not None:
            db.close()
        if not args.keep_dbs:
            shutil.rmtree(path.parent, ignore_errors=True)
    add_rates(row)
    return row


def ingest_latticedb(db: Any, workload: str, args: argparse.Namespace) -> list[int]:
    """Ingest synthetic nodes and edges into LatticeDB."""
    shape = workload_graph_shape(workload, args.graph_shape)
    node_ids: list[int] = []
    for start, end in chunks(args.nodes, args.batch_size):
        with db.write() as txn:
            for index in range(start, end):
                node = txn.create_node(labels=["Node"], properties={"id": f"n{index}", "group": index % 128})
                node_ids.append(node.id)
            txn.commit()
    for start, end in chunks(args.edges, args.batch_size):
        with db.write() as txn:
            for index in range(start, end):
                _, source, target, edge_type = edge_parts(index, args.nodes, shape)
                source_id = node_ids[int(source[1:])]
                target_id = node_ids[int(target[1:])]
                txn.create_edge(source_id, target_id, edge_type, properties={"weight": index % 1000})
            txn.commit()
    return node_ids


def count_latticedb(db: Any) -> tuple[int, int]:
    """Count nodes and edges in LatticeDB."""
    with db.read() as txn:
        node_ids = txn.get_nodes_by_label("Node")
        edge_count = sum(len(txn.get_outgoing_edges(node_id)) for node_id in node_ids)
    return len(node_ids), edge_count


def run_latticedb_workload(db: Any, node_ids: list[int], workload: str, args: argparse.Namespace) -> int:
    """Dispatch LatticeDB traversal workloads."""
    if workload == "ingest":
        return args.nodes + args.edges
    with db.read() as txn:
        if workload == "star_traversal":
            total = 0
            for _ in range(args.iterations):
                total += len(txn.get_outgoing_edges_by_type(node_ids[0], "RelA"))
            return total
        if workload == "neighbors":
            return sum(
                len(txn.get_outgoing_edges_by_type(node_ids[index], "RelA"))
                for index in range(min(args.iterations, args.nodes))
            )
        if workload == "sample_neighbors":
            total = 0
            rng = random.Random(args.seed)
            for index in range(min(args.iterations, args.nodes)):
                total += reservoir_count(txn.get_outgoing_edges_by_type(node_ids[index], "RelA"), args.sample_size, rng)
            return total
        if workload == "typed_path":
            total = 0
            for index in range(min(args.iterations, args.nodes)):
                for first in txn.get_outgoing_edges_by_type(node_ids[index], "RelA"):
                    total += len(txn.get_outgoing_edges_by_type(first.target_id, "RelB", limit=args.path_fanout_limit))
            return total
        if workload == "bfs_depth":
            return typed_bfs_latticedb(txn, node_ids[0], args.depth, args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def typed_bfs_latticedb(txn: Any, start_node: int, depth: int, limit: int) -> int:
    """BFS traversal over LatticeDB transactions."""
    visited: set[int] = set()
    queue = deque([(start_node, 0)])
    while queue and len(visited) <= limit:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current_depth >= depth:
            continue
        for edge_type in NAMED_EDGE_TYPES:
            for edge in txn.get_outgoing_edges_by_type(current, edge_type):
                if edge.target_id not in visited:
                    queue.append((edge.target_id, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def run_ladybugdb(workload: str, args: argparse.Namespace) -> dict[str, Any]:
    """Execute LadybugDB embedded benchmark run."""
    row = base_row("ladybugdb", workload, args)
    if importlib.util.find_spec("ladybug") is None:
        row.update({"status": "skipped", "skip_reason": "missing ladybug Python package"})
        return row
    import ladybug as lb

    path = Path(tempfile.mkdtemp(prefix="ladybugdb_embedded_bench_", dir=args.tmp_dir)) / "graph.ladybug"
    db = None
    conn = None
    try:
        db = lb.Database(str(path))
        conn = db.connect() if hasattr(db, "connect") else lb.Connection(db)
        _, row["ingest_seconds"] = seconds(lambda: ingest_ladybugdb(conn, workload, args, path.parent))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_ladybugdb(conn))
        row["count_status"] = count_status(row, args.nodes, args.edges)
        row["result_count"], row["query_seconds"] = seconds(lambda: run_ladybugdb_workload(conn, workload, args))
        row["db_bytes"] = disk_usage(path.parent)
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if conn is not None and hasattr(conn, "close"):
            conn.close()
        if db is not None:
            if hasattr(db, "close"):
                db.close()
            else:
                del db
        if not args.keep_dbs:
            shutil.rmtree(path.parent, ignore_errors=True)
    add_rates(row)
    return row


def ingest_ladybugdb(conn: Any, workload: str, args: argparse.Namespace, work_dir: Path) -> None:
    """Ingest data into LadybugDB using CSV files."""
    conn.execute("CREATE NODE TABLE Node(id STRING, node_group INT64, PRIMARY KEY (id));")
    for et in NAMED_EDGE_TYPES:
        conn.execute(f"CREATE REL TABLE {et}(FROM Node TO Node, weight INT64);")

    shape = workload_graph_shape(workload, args.graph_shape)
    nodes_csv = work_dir / "nodes.csv"
    with nodes_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for i in range(args.nodes):
            w.writerow([f"n{i}", i % 128])
    conn.execute(f"COPY Node FROM '{nodes_csv}';")

    edges_csv = work_dir / "edges.csv"
    with edges_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for i in range(args.edges):
            _, src, dst, et = edge_parts(i, args.nodes, shape)
            w.writerow([src, dst, et, i % 1000])

    for et in NAMED_EDGE_TYPES:
        et_csv = work_dir / f"edges_{et}.csv"
        with et_csv.open("w", newline="", encoding="utf-8") as f_out, edges_csv.open(encoding="utf-8") as f_in:
            w_out = csv.writer(f_out)
            r_in = csv.reader(f_in)
            for row in r_in:
                if row[2] == et:
                    w_out.writerow([row[0], row[1], row[3]])
        conn.execute(f"COPY {et} FROM '{et_csv}';")


def count_ladybugdb(conn: Any) -> tuple[int, int]:
    """Count nodes and edges in LadybugDB."""
    res_nodes = conn.execute("MATCH (n:Node) RETURN count(n);")
    node_count = res_nodes.get_next()[0]
    edge_count = 0
    for et in NAMED_EDGE_TYPES:
        res = conn.execute(f"MATCH ()-[r:{et}]->() RETURN count(r);")
        edge_count += res.get_next()[0]
    return node_count, edge_count


def run_ladybugdb_workload(conn: Any, workload: str, args: argparse.Namespace) -> int:
    """Dispatch LadybugDB query workloads."""
    if workload == "ingest":
        return args.nodes + args.edges
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            res = conn.execute("MATCH (a:Node {id: 'n0'})-[:RelA]->(b:Node) RETURN count(b);")
            total += res.get_next()[0]
        return total
    if workload == "neighbors":
        total = 0
        for seed in seed_ids(args.iterations, args.nodes):
            res = conn.execute(f"MATCH (a:Node {{id: '{seed}'}})-[:RelA]->(b:Node) RETURN count(b);")
            total += res.get_next()[0]
        return total
    if workload == "sample_neighbors":
        total = 0
        for seed in seed_ids(args.iterations, args.nodes):
            res = conn.execute(
                f"MATCH (a:Node {{id: '{seed}'}})-[:RelA]->(b:Node) RETURN b.id LIMIT {args.sample_size};"
            )
            total += res.get_num_tuples()
        return total
    if workload == "bfs_depth":
        total = 0
        for seed in seed_ids(args.iterations, args.nodes):
            total += bfs_ladybugdb(conn, seed, args.depth, args.bfs_limit)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seed_ids(args.iterations, args.nodes):
            res = conn.execute(f"MATCH (a:Node {{id: '{seed}'}})-[:RelA]->(b:Node)-[:RelB]->(c:Node) RETURN count(c);")
            total += res.get_next()[0]
        return total
    raise ValueError(f"workload {workload} not implemented for LadybugDB")


def bfs_ladybugdb(conn: Any, start_node: str, depth: int, limit: int) -> int:
    """BFS traversal over LadybugDB using typed outgoing-edge queries."""
    visited: set[str] = set()
    queue = deque([(start_node, 0)])
    while queue and len(visited) <= limit:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current_depth >= depth:
            continue
        for edge_type in NAMED_EDGE_TYPES:
            res = conn.execute(f"MATCH (a:Node {{id: '{current}'}})-[:{edge_type}]->(b:Node) RETURN b.id;")
            while res.has_next():
                neighbor = res.get_next()[0]
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def run_engine_workload(engine: str, workload: str, args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch benchmark run to requested engine."""
    if engine == "gestaltdb":
        return run_gestaltdb(workload, args)
    if engine == "latticedb":
        return run_latticedb(workload, args)
    if engine == "ladybugdb":
        return run_ladybugdb(workload, args)
    raise ValueError(f"unknown engine: {engine}")


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build or configure argument parser for embedded graph database benchmark."""
    parser = subparser or argparse.ArgumentParser(
        description="Benchmark embedded Python graph databases (GestaltDB, LatticeDB, LadybugDB)"
    )
    parser.add_argument("--engines", nargs="+", choices=["gestaltdb", "latticedb", "ladybugdb"], default=["gestaltdb", "latticedb"])
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=["ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path"],
        default=["ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path"],
    )
    parser.add_argument("--nodes", type=int, default=100_000)
    parser.add_argument("--edges", type=int, default=500_000)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--graph-shape", choices=["auto", "synthetic", "star", "typed_path"], default="auto")
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--bfs-limit", type=int, default=100_000)
    parser.add_argument("--path-fanout-limit", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/embedded_graphdbs"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    # Engine specific options
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=float, default=10)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    parser.add_argument("--latticedb-cache-size-mb", type=int, default=512)
    parser.add_argument("--latticedb-disable-wal", action="store_true")
    parser.add_argument("--latticedb-adjacency-cache", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="embedded_graphdbs_raw.csv",
        jsonl_filename="embedded_graphdbs_raw.jsonl",
        fieldnames=CSV_FIELDS,
    )
    all_rows: list[dict[str, Any]] = []
    for rep in range(1, args.repetitions + 1):
        setattr(args, "repetition", rep)
        for engine in args.engines:
            for workload in args.workloads:
                label = f"repetition={rep}/{args.repetitions} engine={engine} workload={workload} nodes={args.nodes} edges={args.edges}"
                print(f"Running {label}", flush=True)
                row = run_engine_workload(engine, workload, args)
                writer.write_row(row)
                all_rows.append(row)
                status = row.get("status")
                sec = row.get("total_seconds", "")
                print(f"Finished {label} status={status} total_seconds={sec}", flush=True)

    summary_rows = summarize_rows(all_rows)
    summary_writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="embedded_graphdbs_summary.csv",
        jsonl_filename="embedded_graphdbs_summary.jsonl",
        fieldnames=SUMMARY_FIELDS,
    )
    summary_writer.write_rows(summary_rows)


if __name__ == "__main__":
    main()
