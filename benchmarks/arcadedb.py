"""Compare GestaltDB/RocksDB with embedded ArcadeDB graph workloads.

The suite includes workloads that stress different strengths:
- ``columnar_ingest``: serialized Arrow column ingestion vs ArcadeDB GraphBatch.
- ``star_traversal`` and ``bfs_depth``: native adjacency traversal.
- ``typed_path``: repeated typed-edge expansion.
- ``rocksdb_compaction``: raw overwrite workload targeting RocksDB's LSM compaction.

ArcadeDB is optional; if ``arcadedb-embedded`` is missing, rows emit ``status=skipped``.
"""

from __future__ import annotations

import argparse
from collections import deque
import importlib.util
import json
import os
from pathlib import Path
import platform
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
    pyarrow_array,
)
from benchmarks.common.reporting import (
    ResultWriter,
    add_rates,
    base_row,
    summarize_rows,
)
from benchmarks.common.storage import disk_usage, has_pyrex
from benchmarks.common.timing import seconds

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


def graph_edges(shape: str, nodes: int, edges: int) -> Iterable[tuple[str, str, str, str]]:
    """Yield edges for specified shape."""
    for i in range(edges):
        yield edge_parts(i, nodes, shape)


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


def ingest_gestaltdb_columnar(graph: GraphDB, shape: str, nodes: int, edges: int, batch_size: int) -> None:
    for start, end in chunks(nodes, batch_size):
        node_ids = [f"n{index}" for index in range(start, end)]
        node_values = [graph.serialize_node_value(Node(node_id=nid, labels=("Node",), properties={"id": nid})) for nid in node_ids]
        graph.ingest_nodes_arrow(pyarrow_array(node_ids), pyarrow_array(node_values), chunk_size=batch_size)

    edge_list = list(graph_edges(shape, nodes, edges))
    for start, end in chunks(len(edge_list), batch_size):
        rows = edge_list[start:end]
        edge_ids = [row[0] for row in rows]
        sources = [row[1] for row in rows]
        targets = [row[2] for row in rows]
        edge_types = [row[3] for row in rows]
        edge_values = [
            graph.serialize_edge_value(Edge(edge_id=eid, source=src, target=dst, properties={"type": et}))
            for eid, src, dst, et in rows
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


def typed_bfs_gestaltdb(graph: GraphDB, start_node: str, depth: int, limit: int) -> int:
    visited = set()
    queue = deque([(graph.node_key_to_bytes(start_node), 0)])
    while queue and len(visited) < limit:
        current, cur_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if cur_depth >= depth:
            continue
        for edge_type in NAMED_EDGE_TYPES:
            for record in graph.iter_typed_adjacency(current, edge_type, direction="out"):
                nbr = record["neighbor_id"]
                if nbr not in visited:
                    queue.append((nbr, cur_depth + 1))
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
            count += typed_bfs_gestaltdb(graph, "n0", args.depth, args.bfs_limit)
        return count
    if workload == "typed_path":
        count = 0
        seeds = [f"n{index % args.nodes}" for index in range(args.iterations)]
        for seed in seeds:
            frontier = [graph.node_key_to_bytes(seed)]
            for edge_type in ("RelA", "RelB"):
                next_frontier = []
                for node_id in frontier:
                    next_frontier.extend(r["neighbor_id"] for r in graph.iter_typed_adjacency(node_id, edge_type, direction="out"))
                frontier = next_frontier[: args.path_fanout_limit]
            count += len(frontier)
        return count
    raise ValueError(f"unsupported gestaltdb workload: {workload}")


def run_gestaltdb(workload: str, shape: str, args: argparse.Namespace, *, transactional: bool = False) -> dict[str, Any]:
    engine = "gestaltdb-rocksdb-transactional" if transactional else "gestaltdb-rocksdb"
    row = base_row(engine, workload, args, {"graph_shape": shape})
    if not has_pyrex():
        row.update({"status": "skipped", "skip_reason": "missing pyrex-rocksdb"})
        return row

    path = Path(tempfile.mkdtemp(prefix=f"{engine}_{workload}_", dir=args.tmp_dir))
    graph: GraphDB | None = None
    try:
        graph = open_gestaltdb(path, args, transactional=transactional)
        row["native_columnar"] = bool(getattr(graph.store, "has_native_columnar_ingestion", lambda: False)())
        _, row["ingest_seconds"] = seconds(lambda: ingest_gestaltdb_columnar(graph, shape, args.nodes, args.edges, args.batch_size))
        if workload != "columnar_ingest":
            row["result_count"], row["query_seconds"] = seconds(lambda: run_gestaltdb_query(graph, workload, args))
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


def setup_arcadedb(db: Any) -> None:
    for cmd in (
        "CREATE VERTEX TYPE Node",
        "CREATE EDGE TYPE RelA",
        "CREATE EDGE TYPE RelB",
        "CREATE EDGE TYPE RelC",
        "CREATE PROPERTY Node.id STRING",
    ):
        try:
            db.command("sql", cmd)
        except Exception:
            pass


def ingest_arcadedb(db: Any, shape: str, nodes: int, edges: int, batch_size: int, parallel: int) -> None:
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


def arcade_result_count(result: Any) -> int:
    rows = result.to_list() if hasattr(result, "to_list") else list(result)
    if len(rows) == 1 and isinstance(rows[0], dict):
        for key in ("count", "degree", "size", "c"):
            val = rows[0].get(key)
            if isinstance(val, int):
                return val
            if val is not None:
                return int(val)
    return len(rows)


def run_arcadedb_query(db: Any, workload: str, args: argparse.Namespace) -> int:
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            total += arcade_result_count(db.query("sql", "SELECT expand(out('RelA')) FROM Node WHERE id = ?", "n0"))
        return total
    if workload == "bfs_depth":
        query = (
            f"MATCH {{type: Node, where: (id = 'n0')}}.out('RelA')"
            f"{{as: n, while: ($depth < {args.depth}), where: ($depth > 0)}} RETURN n LIMIT {args.bfs_limit}"
        )
        total = 0
        for _ in range(args.iterations):
            total += arcade_result_count(db.query("sql", query))
        return total
    if workload == "typed_path":
        total = 0
        for index in range(args.iterations):
            nid = f"n{index % args.nodes}"
            query = f"MATCH {{type: Node, where: (id = '{nid}')}}.out('RelA'){{}}.out('RelB'){{as: n}} RETURN n LIMIT {args.path_fanout_limit}"
            total += arcade_result_count(db.query("sql", query))
        return total
    raise ValueError(f"unsupported ArcadeDB query workload: {workload}")


def run_arcadedb(workload: str, shape: str, args: argparse.Namespace) -> dict[str, Any]:
    row = base_row("arcadedb-embedded", workload, args, {"graph_shape": shape})
    if importlib.util.find_spec("arcadedb_embedded") is None:
        row.update({"status": "skipped", "skip_reason": "missing arcadedb-embedded package"})
        return row
    import arcadedb_embedded

    path = Path(tempfile.mkdtemp(prefix="arcadedb_bench_", dir=args.tmp_dir)) / "benchmark.arcadedb"
    db = None
    try:
        db = arcadedb_embedded.create_database(str(path), jvm_kwargs={"heap_size": args.arcadedb_heap_size})
        setup_arcadedb(db)
        _, row["ingest_seconds"] = seconds(lambda: ingest_arcadedb(db, shape, args.nodes, args.edges, args.batch_size, args.arcadedb_parallel))
        if workload != "columnar_ingest":
            row["result_count"], row["query_seconds"] = seconds(lambda: run_arcadedb_query(db, workload, args))
        row["db_bytes"] = disk_usage(path.parent)
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if db is not None:
            db.close()
        if not args.keep_dbs:
            shutil.rmtree(path.parent, ignore_errors=True)
    add_rates(row)
    return row


def run_workload(engine: str, workload: str, shape: str, args: argparse.Namespace) -> dict[str, Any]:
    if engine == "gestaltdb":
        return run_gestaltdb(workload, shape, args, transactional=False)
    if engine == "gestaltdb-tx":
        return run_gestaltdb(workload, shape, args, transactional=True)
    if engine == "arcadedb":
        return run_arcadedb(workload, shape, args)
    raise ValueError(f"unknown engine: {engine}")


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = subparser or argparse.ArgumentParser(description="Compare GestaltDB and ArcadeDB")
    parser.add_argument("--engines", nargs="+", choices=["gestaltdb", "gestaltdb-tx", "arcadedb"], default=["gestaltdb", "arcadedb"])
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=["columnar_ingest", "star_traversal", "bfs_depth", "typed_path"],
        default=["columnar_ingest", "star_traversal", "bfs_depth", "typed_path"],
    )
    parser.add_argument("--nodes", type=int, default=100_000)
    parser.add_argument("--edges", type=int, default=500_000)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--graph-shape", choices=["auto", "synthetic", "star", "layered", "typed_path"], default="auto")
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--bfs-limit", type=int, default=100_000)
    parser.add_argument("--path-fanout-limit", type=int, default=10_000)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/arcadedb_vs_gestaltdb"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=float, default=10)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    parser.add_argument("--arcadedb-heap-size", default="4g")
    parser.add_argument("--arcadedb-parallel", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="arcadedb_vs_gestaltdb_raw.csv",
        jsonl_filename="arcadedb_vs_gestaltdb_raw.jsonl",
        fieldnames=CSV_FIELDS,
    )
    all_rows: list[dict[str, Any]] = []
    for rep in range(1, args.repetitions + 1):
        setattr(args, "repetition", rep)
        for engine in args.engines:
            for workload in args.workloads:
                shape = "star" if workload == "star_traversal" else ("typed_path" if workload == "typed_path" else "layered")
                if args.graph_shape != "auto":
                    shape = args.graph_shape
                label = f"repetition={rep}/{args.repetitions} engine={engine} workload={workload} shape={shape}"
                print(f"Running {label}", flush=True)
                row = run_workload(engine, workload, shape, args)
                writer.write_row(row)
                all_rows.append(row)
                print(f"Finished {label} status={row.get('status')}", flush=True)

    summary = summarize_rows(all_rows)
    summary_writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="arcadedb_vs_gestaltdb_summary.csv",
        jsonl_filename="arcadedb_vs_gestaltdb_summary.jsonl",
        fieldnames=SUMMARY_FIELDS,
    )
    summary_writer.write_rows(summary)


if __name__ == "__main__":
    main()
