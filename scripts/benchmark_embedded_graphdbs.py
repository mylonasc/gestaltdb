#!/usr/bin/env python3
"""Benchmark embedded Python graph databases with optional dependencies.

The runner compares GestaltDB with LatticeDB and LadybugDB on deterministic
property-graph shapes. Third-party engines are optional: missing packages emit
``status=skipped`` rows so local and CI runs remain useful.

LadybugDB support is intentionally skip-only until its public Python package and
API are confirmed.
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
import random
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


def seconds(func):
    started = time.perf_counter()
    result = func()
    return result, time.perf_counter() - started


def chunks(total: int, chunk_size: int) -> Iterable[tuple[int, int]]:
    for start in range(0, total, chunk_size):
        yield start, min(start + chunk_size, total)


def workload_graph_shape(workload: str, args: argparse.Namespace) -> str:
    if args.graph_shape != "auto":
        return args.graph_shape
    if workload == "star_traversal":
        return "star"
    if workload == "typed_path":
        return "typed_path"
    return "synthetic"


def edge_parts(index: int, nodes: int, graph_shape: str = "synthetic") -> tuple[str, str, str, str]:
    if graph_shape == "star":
        target = 1 + (index % max(1, nodes - 1))
        return f"e{index}", "n0", f"n{target}", "RelA"
    if graph_shape == "typed_path":
        source = f"n{index % nodes}"
        target = f"n{(index % nodes + 1) % nodes}"
        edge_type = EDGE_TYPES[index % 2]
        return f"e{index}", source, target, edge_type
    source = f"n{index % nodes}"
    target = f"n{(index * 9973 + 1) % nodes}"
    edge_type = EDGE_TYPES[index % len(EDGE_TYPES)]
    return f"e{index}", source, target, edge_type


def seed_ids(args: argparse.Namespace) -> list[str]:
    return [f"n{index}" for index in range(min(args.iterations, args.nodes))]


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


def reservoir_count(records: Iterable[object], sample_size: int, rng: random.Random) -> int:
    sample = []
    seen = 0
    for record in records:
        seen += 1
        if len(sample) < sample_size:
            sample.append(record)
            continue
        replacement_idx = rng.randrange(seen)
        if replacement_idx < sample_size:
            sample[replacement_idx] = record
    return len(sample)


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
        "graph_shape": workload_graph_shape(workload, args),
        "sample_size": args.sample_size,
        "depth": args.depth,
        "bfs_limit": args.bfs_limit,
        "ingest_seconds": "",
        "query_seconds": "",
        "reopen_seconds": "",
        "count_seconds": "",
        "total_seconds": "",
        "nodes_per_second": "",
        "edges_per_second": "",
        "queries_per_second": "",
        "result_count": "",
        "actual_nodes": "",
        "actual_edges": "",
        "count_status": "",
        "db_bytes": "",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def add_rates(row: dict[str, object]) -> None:
    ingest_seconds = row.get("ingest_seconds")
    query_seconds = row.get("query_seconds")
    total_seconds = 0.0
    if isinstance(ingest_seconds, (int, float)):
        total_seconds += ingest_seconds
        if ingest_seconds > 0:
            row["nodes_per_second"] = row["nodes"] / ingest_seconds
            row["edges_per_second"] = row["edges"] / ingest_seconds
    if isinstance(query_seconds, (int, float)):
        total_seconds += query_seconds
        if query_seconds > 0:
            row["queries_per_second"] = row["iterations"] / query_seconds
    if total_seconds > 0:
        row["total_seconds"] = total_seconds


def count_status(row: dict[str, object], args: argparse.Namespace) -> str:
    if row.get("actual_nodes") == args.nodes and row.get("actual_edges") == args.edges:
        return "ok"
    return "mismatch"


def run_gestaltdb(workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row("gestaltdb-rocksdb", workload, args)
    if importlib.util.find_spec("pyrex") is None:
        row.update({"status": "skipped", "skip_reason": "missing pyrex-rocksdb"})
        return row

    path = Path(tempfile.mkdtemp(prefix="gestaltdb_embedded_bench_", dir=args.tmp_dir))
    graph = None
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
        row["count_status"] = count_status(row, args)
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


def count_gestaltdb(graph: GraphDB) -> tuple[int, int]:
    return graph.count_nodes_by_label("Node"), sum(graph.count_edges_by_type(edge_type) for edge_type in EDGE_TYPES)


def ingest_gestaltdb(graph: GraphDB, workload: str, args: argparse.Namespace) -> None:
    graph_shape = workload_graph_shape(workload, args)
    for start, end in chunks(args.nodes, args.batch_size):
        graph.put_nodes([Node(node_id=f"n{index}", labels=("Node",), properties={"id": f"n{index}", "group": index % 128}) for index in range(start, end)])
    for start, end in chunks(args.edges, args.batch_size):
        graph.put_edges_bulk(
            [
                Edge(edge_id=edge_id, source=source, target=target, properties={"type": edge_type, "weight": index % 1000})
                for index, (edge_id, source, target, edge_type) in ((index, edge_parts(index, args.nodes, graph_shape)) for index in range(start, end))
            ],
            check_existing=False,
        )


def run_gestaltdb_workload(graph: GraphDB, workload: str, args: argparse.Namespace) -> int:
    if workload == "ingest":
        return args.nodes + args.edges
    if workload == "star_traversal":
        total = 0
        seed = graph.node_key_to_bytes("n0")
        for _ in range(args.iterations):
            total += sum(1 for _ in graph.iter_typed_adjacency(seed, "RelA", direction="out"))
        return total
    if workload == "neighbors":
        total = 0
        for seed in seed_ids(args):
            total += sum(1 for _ in graph.iter_typed_adjacency(graph.node_key_to_bytes(seed), "RelA", direction="out"))
        return total
    if workload == "sample_neighbors":
        total = 0
        rng = random.Random(args.seed)
        for seed in seed_ids(args):
            records = graph.iter_typed_adjacency(graph.node_key_to_bytes(seed), "RelA", direction="out")
            total += reservoir_count(records, args.sample_size, rng)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seed_ids(args):
            first = graph.iter_typed_adjacency(graph.node_key_to_bytes(seed), "RelA", direction="out")
            for edge in first:
                total += sum(1 for _ in graph.iter_typed_adjacency(edge["neighbor_id"], "RelB", direction="out"))
                if total >= args.path_fanout_limit * len(seed_ids(args)):
                    break
        return total
    if workload == "bfs_depth":
        return typed_bfs_gestaltdb(graph, "n0", args.depth, args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def typed_bfs_gestaltdb(graph: GraphDB, start_node: str, depth: int, limit: int) -> int:
    visited = set()
    queue = deque([(graph.node_key_to_bytes(start_node), 0)])
    while queue and len(visited) <= limit:
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
    return min(max(0, len(visited) - 1), limit)


def run_latticedb(workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row("latticedb", workload, args)
    if importlib.util.find_spec("latticedb") is None:
        row.update({"status": "skipped", "skip_reason": "missing latticedb"})
        return row

    from latticedb import Database

    path = Path(tempfile.mkdtemp(prefix="latticedb_embedded_bench_", dir=args.tmp_dir)) / "graph.lattice"
    db = None
    node_ids: list[int] = []
    try:
        db = Database(path, create=True, cache_size_mb=args.latticedb_cache_size_mb, enable_wal=not args.latticedb_disable_wal, enable_adjacency_cache=args.latticedb_adjacency_cache)
        db.open()
        _, row["ingest_seconds"] = seconds(lambda: node_ids.extend(ingest_latticedb(db, workload, args)))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_latticedb(db))
        row["count_status"] = count_status(row, args)
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


def ingest_latticedb(db, workload: str, args: argparse.Namespace) -> list[int]:
    graph_shape = workload_graph_shape(workload, args)
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
                _, source, target, edge_type = edge_parts(index, args.nodes, graph_shape)
                source_id = node_ids[int(source[1:])]
                target_id = node_ids[int(target[1:])]
                txn.create_edge(source_id, target_id, edge_type, properties={"weight": index % 1000})
            txn.commit()
    return node_ids


def count_latticedb(db) -> tuple[int, int]:
    with db.read() as txn:
        node_ids = txn.get_nodes_by_label("Node")
        edge_count = sum(len(txn.get_outgoing_edges(node_id)) for node_id in node_ids)
    return len(node_ids), edge_count


def run_latticedb_workload(db, node_ids: list[int], workload: str, args: argparse.Namespace) -> int:
    if workload == "ingest":
        return args.nodes + args.edges
    with db.read() as txn:
        if workload == "star_traversal":
            total = 0
            for _ in range(args.iterations):
                total += len(txn.get_outgoing_edges_by_type(node_ids[0], "RelA"))
            return total
        if workload == "neighbors":
            return sum(len(txn.get_outgoing_edges_by_type(node_ids[index], "RelA")) for index in range(min(args.iterations, args.nodes)))
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


def typed_bfs_latticedb(txn, start_node: int, depth: int, limit: int) -> int:
    visited = set()
    queue = deque([(start_node, 0)])
    while queue and len(visited) <= limit:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current_depth >= depth:
            continue
        for edge_type in EDGE_TYPES:
            for edge in txn.get_outgoing_edges_by_type(current, edge_type):
                if edge.target_id not in visited:
                    queue.append((edge.target_id, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def run_ladybugdb(workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row("ladybugdb", workload, args)
    if importlib.util.find_spec("ladybug") is None:
        row.update({"status": "skipped", "skip_reason": "missing ladybug Python package"})
        return row
    import ladybug as lb

    path = Path(tempfile.mkdtemp(prefix="ladybugdb_embedded_bench_", dir=args.tmp_dir))
    db = None
    conn = None
    try:
        db = lb.Database(str(path / "graph.lbdb"), buffer_pool_size=args.ladybugdb_buffer_pool_size, max_num_threads=args.ladybugdb_threads)
        conn = lb.Connection(db, num_threads=args.ladybugdb_threads)
        setup_ladybugdb(conn)
        _, row["ingest_seconds"] = seconds(lambda: ingest_ladybugdb(conn, path, workload, args))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_ladybugdb(conn))
        row["count_status"] = count_status(row, args)
        row["result_count"], row["query_seconds"] = seconds(lambda: run_ladybugdb_workload(conn, workload, args))
        row["db_bytes"] = disk_usage(path)
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if conn is not None:
            conn.close()
        if db is not None:
            db.close()
        if not args.keep_dbs:
            shutil.rmtree(path, ignore_errors=True)
    add_rates(row)
    return row


def setup_ladybugdb(conn) -> None:
    conn.execute("CREATE NODE TABLE Node(id STRING PRIMARY KEY, node_group INT64)")
    for edge_type in EDGE_TYPES:
        conn.execute(f"CREATE REL TABLE {edge_type}(FROM Node TO Node, edge_id STRING, weight INT64)")


def ingest_ladybugdb(conn, path: Path, workload: str, args: argparse.Namespace) -> None:
    graph_shape = workload_graph_shape(workload, args)
    nodes_path = path / "nodes.csv"
    with nodes_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "node_group"])
        for index in range(args.nodes):
            writer.writerow([f"n{index}", index % 128])
    conn.execute(f'COPY Node FROM "{nodes_path}" (HEADER=true)')

    edge_paths = {edge_type: path / f"{edge_type}.csv" for edge_type in EDGE_TYPES}
    handles = []
    writers = {}
    try:
        for edge_type, edge_path in edge_paths.items():
            handle = edge_path.open("w", newline="", encoding="utf-8")
            handles.append(handle)
            writer = csv.writer(handle)
            writer.writerow(["from", "to", "edge_id", "weight"])
            writers[edge_type] = writer
        for index in range(args.edges):
            edge_id, source, target, edge_type = edge_parts(index, args.nodes, graph_shape)
            writers[edge_type].writerow([source, target, edge_id, index % 1000])
    finally:
        for handle in handles:
            handle.close()
    for edge_type, edge_path in edge_paths.items():
        conn.execute(f'COPY {edge_type} FROM "{edge_path}" (HEADER=true)')


def ladybug_scalar(conn, query: str) -> int:
    rows = list(conn.execute(query))
    if not rows or not rows[0]:
        return 0
    return int(rows[0][0])


def count_ladybugdb(conn) -> tuple[int, int]:
    return ladybug_scalar(conn, "MATCH (n:Node) RETURN count(n)"), ladybug_scalar(conn, "MATCH ()-[r]->() RETURN count(r)")


def run_ladybugdb_workload(conn, workload: str, args: argparse.Namespace) -> int:
    if workload == "ingest":
        return args.nodes + args.edges
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            total += ladybug_scalar(conn, 'MATCH (:Node {id: "n0"})-[:RelA]->(m) RETURN count(m)')
        return total
    if workload == "neighbors":
        total = 0
        for seed in seed_ids(args):
            total += ladybug_scalar(conn, f'MATCH (:Node {{id: "{seed}"}})-[:RelA]->(m) RETURN count(m)')
        return total
    if workload == "sample_neighbors":
        total = 0
        rng = random.Random(args.seed)
        for seed in seed_ids(args):
            rows = conn.execute(f'MATCH (:Node {{id: "{seed}"}})-[:RelA]->(m) RETURN m.id')
            total += reservoir_count(rows, args.sample_size, rng)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seed_ids(args):
            rows = conn.execute(f'MATCH (:Node {{id: "{seed}"}})-[:RelA]->()-[:RelB]->(n) RETURN n.id LIMIT {args.path_fanout_limit}')
            total += sum(1 for _ in rows)
        return total
    if workload == "bfs_depth":
        return typed_bfs_ladybugdb(conn, "n0", args.depth, args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def ladybug_neighbors(conn, node_id: str, edge_type: str) -> list[str]:
    rows = conn.execute(f'MATCH (:Node {{id: "{node_id}"}})-[:{edge_type}]->(m) RETURN m.id')
    return [str(row[0]) for row in rows if row]


def typed_bfs_ladybugdb(conn, start_node: str, depth: int, limit: int) -> int:
    visited = set()
    queue = deque([(start_node, 0)])
    while queue and len(visited) <= limit:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current_depth >= depth:
            continue
        for edge_type in EDGE_TYPES:
            for neighbor in ladybug_neighbors(conn, current, edge_type):
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def write_row(output_dir: Path, row: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "embedded_graphdbs_results.jsonl"
    csv_path = output_dir / "embedded_graphdbs_results.csv"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def numeric_values(rows: list[dict[str, object]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]


def mean_std(rows: list[dict[str, object]], key: str) -> tuple[float | str, float | str]:
    values = numeric_values(rows, key)
    if not values:
        return "", ""
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def write_summary(output_dir: Path, rows: list[dict[str, object]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row.get("engine", "")), str(row.get("workload", ""))), []).append(row)
    jsonl_path = output_dir / "embedded_graphdbs_summary.jsonl"
    csv_path = output_dir / "embedded_graphdbs_summary.csv"
    with jsonl_path.open("w", encoding="utf-8") as jsonl, csv_path.open("w", newline="", encoding="utf-8") as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for (engine, workload), group in sorted(grouped.items()):
            ok_rows = [row for row in group if row.get("status") == "ok"]
            first = (ok_rows or group)[0]
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
                "sample_size": first.get("sample_size", ""),
                "depth": first.get("depth", ""),
                "skip_reason": "" if ok_rows else first.get("skip_reason", ""),
            }
            for key in ("ingest_seconds", "query_seconds", "reopen_seconds", "count_seconds", "total_seconds", "nodes_per_second", "edges_per_second", "queries_per_second", "result_count", "actual_nodes", "actual_edges", "db_bytes"):
                mean, std = mean_std(ok_rows, key)
                summary[f"{key}_mean"] = mean
                summary[f"{key}_std"] = std
            jsonl.write(json.dumps(summary, sort_keys=True) + "\n")
            writer.writerow(summary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark embedded graph databases against deterministic graph workloads")
    parser.add_argument("--engines", nargs="+", choices=["gestaltdb", "latticedb", "ladybugdb"], default=["gestaltdb", "latticedb", "ladybugdb"])
    parser.add_argument("--workloads", nargs="+", choices=["ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path"], default=["ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path"])
    parser.add_argument("--graph-shape", choices=["auto", "synthetic", "star", "typed_path"], default="auto")
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--edges", type=int, default=50_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--bfs-limit", type=int, default=100_000)
    parser.add_argument("--path-fanout-limit", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/embedded_graphdbs"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=int, default=10)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    parser.add_argument("--latticedb-cache-size-mb", type=int, default=100)
    parser.add_argument("--latticedb-disable-wal", action="store_true")
    parser.add_argument("--latticedb-adjacency-cache", action="store_true")
    parser.add_argument("--ladybugdb-buffer-pool-size", type=int, default=0)
    parser.add_argument("--ladybugdb-threads", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runners = {
        "gestaltdb": run_gestaltdb,
        "latticedb": run_latticedb,
        "ladybugdb": run_ladybugdb,
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
