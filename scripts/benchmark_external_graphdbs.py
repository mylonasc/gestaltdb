#!/usr/bin/env python3
"""Benchmark GestaltDB against Neo4j, Memgraph, and ArcadeDB.

Neo4j and Memgraph are managed as disposable Docker containers by default.
ArcadeDB uses the optional embedded Python package, matching the existing
ArcadeDB benchmark in this repository.
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
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import PyRexStore
from gestaltdb.serializers import JSONSerializer


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
    "container_image",
    "container_name",
    "uri",
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


def edge_parts(index: int, nodes: int) -> tuple[str, str, str, str]:
    source = f"n{index % nodes}"
    target = f"n{(index * 9973 + 1) % nodes}"
    edge_type = EDGE_TYPES[index % len(EDGE_TYPES)]
    return f"e{index}", source, target, edge_type


def node_rows(start: int, end: int) -> list[dict[str, object]]:
    return [{"id": f"n{index}", "group": index % 128} for index in range(start, end)]


def edge_rows(start: int, end: int, nodes: int) -> list[dict[str, object]]:
    rows = []
    for index in range(start, end):
        edge_id, source, target, edge_type = edge_parts(index, nodes)
        rows.append({"id": edge_id, "source": source, "target": target, "type": edge_type, "weight": index % 1000})
    return rows


def edge_rows_by_type(start: int, end: int, nodes: int) -> dict[str, list[dict[str, object]]]:
    grouped = {edge_type: [] for edge_type in EDGE_TYPES}
    for row in edge_rows(start, end, nodes):
        grouped[str(row["type"])].append(row)
    return grouped


def seed_ids(args: argparse.Namespace) -> list[str]:
    count = min(args.iterations, args.nodes)
    return [f"n{index}" for index in range(count)]


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


def reservoir_count(records: Iterable[object], sample_size: int, rng) -> int:
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


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], text=True, capture_output=True, timeout=timeout, check=False)


@contextmanager
def managed_container(engine: str, args: argparse.Namespace):
    if engine not in {"neo4j", "memgraph"} or args.no_containers:
        yield {"container_name": "", "container_image": ""}
        return
    if not docker_available():
        raise RuntimeError("docker executable not found")

    suffix = uuid.uuid4().hex[:10]
    if engine == "neo4j":
        image = args.neo4j_image
        name = f"gestaltdb-bench-neo4j-{suffix}"
        run_args = [
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            f"{args.neo4j_bolt_port}:7687",
            "-e",
            f"NEO4J_AUTH={args.neo4j_user}/{args.neo4j_password}",
            "-e",
            "NEO4J_dbms_memory_heap_initial__size=512m",
            "-e",
            "NEO4J_dbms_memory_heap_max__size=2G",
            image,
        ]
    else:
        image = args.memgraph_image
        name = f"gestaltdb-bench-memgraph-{suffix}"
        run_args = ["run", "-d", "--rm", "--name", name, "-p", f"{args.memgraph_bolt_port}:7687", image]

    result = docker_run(run_args, timeout=args.container_start_timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to start {engine} container")
    try:
        yield {"container_name": name, "container_image": image}
    finally:
        if not args.keep_containers:
            docker_run(["stop", name], timeout=60)


def wait_for_bolt(uri: str, auth: tuple[str, str] | None, timeout_seconds: int) -> None:
    if importlib.util.find_spec("neo4j") is None:
        raise RuntimeError("missing neo4j Python package")
    from neo4j import GraphDatabase

    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            driver = GraphDatabase.driver(uri, auth=auth)
            with driver.session() as session:
                session.run("RETURN 1 AS ok").consume()
            driver.close()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"timed out waiting for {uri}: {last_error}")


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
        "sample_size": args.sample_size,
        "depth": args.depth,
        "bfs_limit": args.bfs_limit,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def add_rates(row: dict[str, object]) -> None:
    ingest_seconds = row.get("ingest_seconds")
    query_seconds = row.get("query_seconds")
    if isinstance(ingest_seconds, (int, float)) and ingest_seconds > 0:
        row["nodes_per_second"] = row["nodes"] / ingest_seconds
        row["edges_per_second"] = row["edges"] / ingest_seconds
    if isinstance(query_seconds, (int, float)) and query_seconds > 0:
        row["queries_per_second"] = row["iterations"] / query_seconds
    row["total_seconds"] = float(row.get("ingest_seconds") or 0.0) + float(row.get("query_seconds") or 0.0)


def open_gestaltdb(path: Path, args: argparse.Namespace) -> GraphDB:
    return GraphDB(
        PyRexStore(
            path=str(path),
            parallelism=args.rocksdb_parallelism,
            max_background_jobs=args.rocksdb_background_jobs,
            write_buffer_size=args.rocksdb_write_buffer_size,
            bloom_bits_per_key=args.rocksdb_bloom_bits,
            disable_wal=args.rocksdb_disable_wal,
        ),
        JSONSerializer(),
    )


def run_gestaltdb(workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row("gestaltdb-rocksdb", workload, args)
    if importlib.util.find_spec("pyrex") is None:
        row.update({"status": "skipped", "skip_reason": "missing pyrex-rocksdb"})
        return row
    if importlib.util.find_spec("pyarrow") is None:
        row.update({"status": "skipped", "skip_reason": "missing pyarrow"})
        return row
    if importlib.util.find_spec("polars") is None:
        row.update({"status": "skipped", "skip_reason": "missing polars"})
        return row

    import pyarrow as pa

    path = Path(tempfile.mkdtemp(prefix="gestaltdb_external_bench_", dir=args.tmp_dir))
    graph = None
    try:
        graph = open_gestaltdb(path, args)

        def ingest() -> None:
            for start, end in chunks(args.nodes, args.batch_size):
                rows = node_rows(start, end)
                graph.ingest_nodes_arrow_entities(
                    pa.array([item["id"] for item in rows]),
                    labels=pa.array([["Node"] for _ in rows]),
                    properties={"group": pa.array([item["group"] for item in rows])},
                    append_only=True,
                    chunk_size=args.batch_size,
                    index_mode=IndexMaintenanceMode.DEFER.value,
                )
            for start, end in chunks(args.edges, args.batch_size):
                rows = edge_rows(start, end, args.nodes)
                graph.ingest_edges_arrow_entities(
                    pa.array([item["id"] for item in rows]),
                    pa.array([item["source"] for item in rows]),
                    pa.array([item["target"] for item in rows]),
                    pa.array([item["type"] for item in rows]),
                    properties={"weight": pa.array([item["weight"] for item in rows])},
                    append_only=True,
                    chunk_size=args.batch_size,
                    index_mode=IndexMaintenanceMode.DEFER.value,
                )

        _, row["ingest_seconds"] = seconds(ingest)
        graph.close()
        graph = None
        graph, row["reopen_seconds"] = seconds(lambda: open_gestaltdb(path, args))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_gestaltdb(graph))
        row["count_status"] = count_status(row, args)
        result_count, row["query_seconds"] = seconds(lambda: run_gestaltdb_workload(graph, workload, args))
        row["result_count"] = result_count
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


def run_gestaltdb_workload(graph: GraphDB, workload: str, args: argparse.Namespace) -> int:
    seeds = seed_ids(args)
    if workload == "neighbors":
        return sum(len(graph.neighbors_by_edge_type(seed, "RelA", direction="out")) for seed in seeds)
    if workload == "sample_neighbors":
        rng = random.Random(args.seed)
        return sum(len(graph.sample_neighbors(seed, "RelA", sample_size=args.sample_size, rng=rng)) for seed in seeds)
    if workload == "typed_path":
        return exact_typed_path_gestaltdb(graph, seeds, args.path_fanout_limit)
    if workload == "bfs_depth":
        return typed_bfs_gestaltdb(graph, "n0", args.depth, args.bfs_limit)
    if workload == "ingest":
        return args.nodes + args.edges
    raise ValueError(f"unknown workload: {workload}")


def typed_bfs_gestaltdb(graph: GraphDB, start_node: str, depth: int, limit: int) -> int:
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
    return min(max(0, len(visited) - 1), limit)


def exact_typed_path_gestaltdb(graph: GraphDB, seeds: list[str], path_fanout_limit: int) -> int:
    total = 0
    for seed in seeds:
        frontier = [graph.node_key_to_bytes(seed)]
        for edge_type in ("RelA", "RelB"):
            next_frontier = []
            for node_id in frontier:
                for record in graph.iter_typed_adjacency(node_id, edge_type, direction="out"):
                    next_frontier.append(record["neighbor_id"])
                    if len(next_frontier) >= path_fanout_limit:
                        break
                if len(next_frontier) >= path_fanout_limit:
                    break
            frontier = next_frontier
            if not frontier:
                break
        total += len(frontier[:path_fanout_limit])
    return total


def count_gestaltdb(graph: GraphDB) -> tuple[int, int]:
    return sum(1 for _ in graph.store.get_node_keys_generator()), sum(1 for _ in graph.store.get_edge_keys_generator())


def count_status(row: dict[str, object], args: argparse.Namespace) -> str:
    return "ok" if row.get("actual_nodes") == args.nodes and row.get("actual_edges") == args.edges else "mismatch"


def cypher_driver(uri: str, auth: tuple[str, str] | None):
    from neo4j import GraphDatabase

    return GraphDatabase.driver(uri, auth=auth)


def run_bolt_engine(engine: str, workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row(engine, workload, args)
    if importlib.util.find_spec("neo4j") is None:
        row.update({"status": "skipped", "skip_reason": "missing neo4j Python package"})
        return row

    uri = args.neo4j_uri if engine == "neo4j" else args.memgraph_uri
    auth = (args.neo4j_user, args.neo4j_password) if engine == "neo4j" else None
    row["uri"] = uri
    try:
        wait_for_bolt(uri, auth, args.container_wait_timeout)
        driver = cypher_driver(uri, auth)
        with driver.session() as session:
            setup_cypher_graph(session, engine)
            _, row["ingest_seconds"] = seconds(lambda: ingest_cypher(session, args))
            (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_cypher(session))
            row["count_status"] = count_status(row, args)
            result_count, row["query_seconds"] = seconds(lambda: run_cypher_workload(session, workload, args))
            row["result_count"] = result_count
        driver.close()
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    add_rates(row)
    return row


def setup_cypher_graph(session, engine: str) -> None:
    session.run("MATCH (n) DETACH DELETE n").consume()
    if engine == "neo4j":
        session.run("CREATE INDEX node_id IF NOT EXISTS FOR (n:Node) ON (n.id)").consume()
    else:
        try:
            session.run("CREATE INDEX ON :Node(id)").consume()
        except Exception:
            pass


def ingest_cypher(session, args: argparse.Namespace) -> None:
    for start, end in chunks(args.nodes, args.batch_size):
        session.run(
            "UNWIND $rows AS row CREATE (:Node {id: row.id, group: row.group})",
            rows=node_rows(start, end),
        ).consume()
    for start, end in chunks(args.edges, args.batch_size):
        grouped_rows = edge_rows_by_type(start, end, args.nodes)
        for edge_type, rows in grouped_rows.items():
            if not rows:
                continue
            session.run(
                f"UNWIND $rows AS row MATCH (a:Node {{id: row.source}}), (b:Node {{id: row.target}}) "
                f"CREATE (a)-[:{edge_type} {{id: row.id, weight: row.weight}}]->(b)",
                rows=rows,
            ).consume()


def run_cypher_workload(session, workload: str, args: argparse.Namespace) -> int:
    if workload == "ingest":
        return args.nodes + args.edges
    seeds = seed_ids(args)
    if workload == "neighbors":
        total = 0
        for seed in seeds:
            record = session.run(
                "MATCH (:Node {id: $seed})-[:RelA]->(n:Node) RETURN count(n) AS count",
                seed=seed,
            ).single()
            total += int(record["count"] if record else 0)
        return total
    if workload == "sample_neighbors":
        total = 0
        rng = random.Random(args.seed)
        for seed in seeds:
            rows = session.run(
                "MATCH (:Node {id: $seed})-[:RelA]->(n:Node) RETURN n.id AS id",
                seed=seed,
            )
            total += reservoir_count(rows, args.sample_size, rng)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seeds:
            rows = session.run(
                "MATCH (:Node {id: $seed})-[:RelA]->(:Node)-[:RelB]->(n:Node) RETURN n.id AS id LIMIT $limit",
                seed=seed,
                limit=args.path_fanout_limit,
            ).data()
            total += len(rows)
        return total
    if workload == "bfs_depth":
        return typed_bfs_cypher(session, "n0", args.depth, args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def cypher_neighbors(session, node_id: str, edge_type: str) -> list[str]:
    return [
        str(row["id"])
        for row in session.run(
            f"MATCH (:Node {{id: $node_id}})-[:{edge_type}]->(n:Node) RETURN n.id AS id",
            node_id=node_id,
        )
    ]


def typed_bfs_cypher(session, start_node: str, depth: int, limit: int) -> int:
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
            for neighbor in cypher_neighbors(session, current, edge_type):
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def count_cypher(session) -> tuple[int, int]:
    node_record = session.run("MATCH (n:Node) RETURN count(n) AS count").single()
    edge_record = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()
    return int(node_record["count"] if node_record else 0), int(edge_record["count"] if edge_record else 0)


def run_arcadedb(workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row("arcadedb-embedded", workload, args)
    if importlib.util.find_spec("arcadedb_embedded") is None:
        row.update({"status": "skipped", "skip_reason": "missing arcadedb-embedded"})
        return row
    import arcadedb_embedded as arcadedb

    path = Path(tempfile.mkdtemp(prefix="arcadedb_external_bench_", dir=args.tmp_dir))
    db = None
    try:
        kwargs = {"jvm_kwargs": {"heap_size": args.arcadedb_heap_size}} if args.arcadedb_heap_size else {}
        db = arcadedb.create_database(str(path), **kwargs)
        setup_arcadedb(db)
        _, row["ingest_seconds"] = seconds(lambda: ingest_arcadedb(db, args))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_arcadedb(db))
        row["count_status"] = count_status(row, args)
        result_count, row["query_seconds"] = seconds(lambda: run_arcadedb_workload(db, workload, args))
        row["result_count"] = result_count
        row["db_bytes"] = disk_usage(path)
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if db is not None:
            db.close()
        if not args.keep_dbs:
            shutil.rmtree(path, ignore_errors=True)
    add_rates(row)
    return row


def setup_arcadedb(db) -> None:
    for command in ("CREATE VERTEX TYPE Node", "CREATE EDGE TYPE RelA", "CREATE EDGE TYPE RelB", "CREATE EDGE TYPE RelC", "CREATE PROPERTY Node.id STRING"):
        try:
            db.command("sql", command)
        except Exception:
            pass


def ingest_arcadedb(db, args: argparse.Namespace) -> None:
    rid_lookup: dict[str, str] = {}
    with db.graph_batch(batch_size=args.batch_size, expected_edge_count=args.edges, bidirectional=False, commit_every=args.batch_size, use_wal=False) as batch:
        for start, end in chunks(args.nodes, args.batch_size):
            rows = node_rows(start, end)
            rids = batch.create_vertices("Node", rows)
            rid_lookup.update((str(row["id"]), rid) for row, rid in zip(rows, rids))
        for start, end in chunks(args.edges, args.batch_size):
            for row in edge_rows(start, end, args.nodes):
                batch.new_edge(rid_lookup[str(row["source"])], str(row["type"]), rid_lookup[str(row["target"])], id=row["id"], weight=row["weight"])
    db.command("sql", "CREATE INDEX ON Node (id) UNIQUE_HASH")


def arcade_count(result) -> int:
    rows = result.to_list() if hasattr(result, "to_list") else list(result)
    if len(rows) == 1 and isinstance(rows[0], dict):
        for key in ("count", "degree", "c"):
            if key in rows[0]:
                return int(rows[0][key])
    return len(rows)


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_arcadedb_workload(db, workload: str, args: argparse.Namespace) -> int:
    if workload == "ingest":
        return args.nodes + args.edges
    if workload == "neighbors":
        total = 0
        for seed in seed_ids(args):
            total += arcade_count(db.query("sql", "SELECT out('RelA').size() AS degree FROM Node WHERE id = ?", seed))
        return total
    if workload == "sample_neighbors":
        total = 0
        rng = random.Random(args.seed)
        for seed in seed_ids(args):
            query = f"MATCH {{type: Node, where: (id = {sql_string(seed)})}}.out('RelA'){{as: n}} RETURN n"
            rows = db.query("sql", query).to_list()
            total += reservoir_count(rows, args.sample_size, rng)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seed_ids(args):
            query = f"MATCH {{type: Node, where: (id = {sql_string(seed)})}}.out('RelA'){{}}.out('RelB'){{as: n}} RETURN n LIMIT {args.path_fanout_limit}"
            total += arcade_count(db.query("sql", query))
        return total
    if workload == "bfs_depth":
        return typed_bfs_arcadedb(db, "n0", args.depth, args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def arcade_neighbors(db, node_id: str, edge_type: str) -> list[str]:
    query = f"MATCH {{type: Node, where: (id = {sql_string(node_id)})}}.out('{edge_type}'){{as: n}} RETURN n.id AS id"
    rows = db.query("sql", query).to_list()
    return [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id") is not None]


def typed_bfs_arcadedb(db, start_node: str, depth: int, limit: int) -> int:
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
            for neighbor in arcade_neighbors(db, current, edge_type):
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def count_arcadedb(db) -> tuple[int, int]:
    nodes = arcade_count(db.query("sql", "SELECT count(*) AS count FROM Node"))
    edges = 0
    for edge_type in EDGE_TYPES:
        edges += arcade_count(db.query("sql", f"SELECT count(*) AS count FROM {edge_type}"))
    return nodes, edges


def write_row(output_dir: Path, row: dict[str, object]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "external_graphdbs_results.jsonl"
    csv_path = output_dir / "external_graphdbs_results.csv"
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
    jsonl_path = output_dir / "external_graphdbs_summary.jsonl"
    csv_path = output_dir / "external_graphdbs_summary.csv"
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


def run_with_container(engine: str, workload: str, args: argparse.Namespace, runner: Callable[[str, str, argparse.Namespace], dict[str, object]]) -> dict[str, object]:
    try:
        with managed_container(engine, args) as metadata:
            row = runner(engine, workload, args)
            row.update(metadata)
            return row
    except Exception as exc:
        row = base_row(engine, workload, args)
        row.update({"status": "skipped", "skip_reason": f"container unavailable: {type(exc).__name__}: {exc}"})
        add_rates(row)
        return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark GestaltDB against Neo4j, Memgraph, and ArcadeDB")
    parser.add_argument("--engines", nargs="+", choices=["gestaltdb", "neo4j", "memgraph", "arcadedb"], default=["gestaltdb", "neo4j", "memgraph", "arcadedb"])
    parser.add_argument("--workloads", nargs="+", choices=["ingest", "neighbors", "sample_neighbors", "bfs_depth", "typed_path"], default=["ingest", "neighbors", "sample_neighbors", "bfs_depth", "typed_path"])
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
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/external_graphdbs"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    parser.add_argument("--no-containers", action="store_true", help="Use configured Bolt URIs without starting Docker containers")
    parser.add_argument("--keep-containers", action="store_true")
    parser.add_argument("--container-start-timeout", type=int, default=120)
    parser.add_argument("--container-wait-timeout", type=int, default=90)
    parser.add_argument("--neo4j-image", default="neo4j:5-community")
    parser.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    parser.add_argument("--neo4j-bolt-port", type=int, default=7687)
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="gestaltdb-bench-password")
    parser.add_argument("--memgraph-image", default="memgraph/memgraph:latest")
    parser.add_argument("--memgraph-uri", default="bolt://localhost:7688")
    parser.add_argument("--memgraph-bolt-port", type=int, default=7688)
    parser.add_argument("--arcadedb-heap-size", default="2g")
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=int, default=10)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = []
    runners = {
        "gestaltdb": lambda workload, args: run_gestaltdb(workload, args),
        "neo4j": lambda workload, args: run_with_container("neo4j", workload, args, run_bolt_engine),
        "memgraph": lambda workload, args: run_with_container("memgraph", workload, args, run_bolt_engine),
        "arcadedb": lambda workload, args: run_arcadedb(workload, args),
    }
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
