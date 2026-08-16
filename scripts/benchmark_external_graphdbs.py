#!/usr/bin/env python3
"""Benchmark GestaltDB against Neo4j, Memgraph, ArcadeDB, and Apache AGE.

Neo4j, Memgraph, and Apache AGE are managed as disposable Docker containers by default.
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
import re
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


def node_rows(start: int, end: int) -> list[dict[str, object]]:
    return [{"id": f"n{index}", "group": index % 128} for index in range(start, end)]


def edge_rows(start: int, end: int, nodes: int, graph_shape: str = "synthetic") -> list[dict[str, object]]:
    rows = []
    for index in range(start, end):
        edge_id, source, target, edge_type = edge_parts(index, nodes, graph_shape)
        rows.append({"id": edge_id, "source": source, "target": target, "type": edge_type, "weight": index % 1000})
    return rows


def edge_rows_by_type(start: int, end: int, nodes: int, graph_shape: str = "synthetic") -> dict[str, list[dict[str, object]]]:
    grouped = {edge_type: [] for edge_type in EDGE_TYPES}
    for row in edge_rows(start, end, nodes, graph_shape):
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
    if engine not in {"neo4j", "memgraph", "age"} or args.no_containers:
        yield {"container_name": "", "container_image": ""}
        return
    if not docker_available():
        raise RuntimeError("docker executable not found")

    suffix = uuid.uuid4().hex[:10]
    age_load_host_dir = None
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
    elif engine == "memgraph":
        image = args.memgraph_image
        name = f"gestaltdb-bench-memgraph-{suffix}"
        run_args = ["run", "-d", "--rm", "--name", name, "-p", f"{args.memgraph_bolt_port}:7687", image]
    else:
        image = args.age_image
        name = f"gestaltdb-bench-age-{suffix}"
        age_load_host_dir = Path(tempfile.mkdtemp(prefix="age_load_", dir=args.tmp_dir)).resolve()
        os.chmod(age_load_host_dir, 0o755)
        args._age_load_host_dir = age_load_host_dir
        args._age_load_container_dir = "age_load"
        run_args = [
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            f"{args.age_port}:5432",
            "-v",
            f"{age_load_host_dir}:/tmp/age/age_load:ro",
            "-e",
            f"POSTGRES_USER={args.age_user}",
            "-e",
            f"POSTGRES_PASSWORD={args.age_password}",
            "-e",
            f"POSTGRES_DB={args.age_database}",
            image,
        ]

    result = docker_run(run_args, timeout=args.container_start_timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to start {engine} container")
    try:
        yield {"container_name": name, "container_image": image}
    finally:
        if not args.keep_containers:
            docker_run(["stop", name], timeout=60)
        if age_load_host_dir is not None:
            shutil.rmtree(age_load_host_dir, ignore_errors=True)
            args._age_load_host_dir = None
            args._age_load_container_dir = None


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
        "graph_shape": workload_graph_shape(workload, args),
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
        JSONSerializer(),
    )


def run_gestaltdb(workload: str, args: argparse.Namespace, *, transactional: bool = False) -> dict[str, object]:
    engine_name = "gestaltdb-rocksdb-transactional" if transactional else "gestaltdb-rocksdb"
    row = base_row(engine_name, workload, args)
    if importlib.util.find_spec("pyrex") is None:
        row.update({"status": "skipped", "skip_reason": "missing pyrex-rocksdb"})
        return row
    if workload != "rocksdb_compaction" and importlib.util.find_spec("pyarrow") is None:
        row.update({"status": "skipped", "skip_reason": "missing pyarrow"})
        return row
    if workload != "rocksdb_compaction" and importlib.util.find_spec("polars") is None:
        row.update({"status": "skipped", "skip_reason": "missing polars"})
        return row

    pa = None
    if workload != "rocksdb_compaction":
        import pyarrow as pa

    path = Path(tempfile.mkdtemp(prefix="gestaltdb_external_bench_", dir=args.tmp_dir))
    graph = None
    try:
        graph = open_gestaltdb(path, args, transactional=transactional)
        graph_shape = workload_graph_shape(workload, args)

        if workload == "rocksdb_compaction":
            row["ingest_seconds"] = 0.0
            result_count, row["query_seconds"] = seconds(lambda: gestaltdb_compaction(graph.store, args))
            row["result_count"] = result_count
            row["actual_nodes"] = 0
            row["actual_edges"] = 0
            row["count_seconds"] = 0.0
            row["count_status"] = "not_applicable"
            row["db_bytes"] = disk_usage(path)
            add_rates(row)
            return row

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
                rows = edge_rows(start, end, args.nodes, graph_shape)
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
        graph, row["reopen_seconds"] = seconds(lambda: open_gestaltdb(path, args, transactional=transactional))
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
    if workload in {"ingest", "columnar_ingest"}:
        return args.nodes + args.edges
    if workload == "neighbors":
        return sum(len(gestaltdb_query_neighbors(graph, seed, "RelA")) for seed in seeds)
    if workload == "star_traversal":
        query = "MATCH (a {id: 'n0'})-[:RelA]->(n) RETURN n.id AS id"
        return sum(len(graph.query(query)) for _ in range(args.iterations))
    if workload == "sample_neighbors":
        rng = random.Random(args.seed)
        return sum(reservoir_count(gestaltdb_query_neighbors(graph, seed, "RelA"), args.sample_size, rng) for seed in seeds)
    if workload == "typed_path":
        return exact_typed_path_query_gestaltdb(graph, seeds, args.path_fanout_limit)
    if workload == "deep_typed_query":
        return deep_typed_query_gestaltdb(graph, seeds, args.path_fanout_limit)
    if workload == "bfs_depth":
        return typed_bfs_gestaltdb(graph, "n0", args.depth, args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def gestaltdb_query_neighbors(graph: GraphDB, seed: str, edge_type: str):
    query = f"MATCH (a {{id: {cypher_string(seed)}}})-[:{edge_type}]->(n) RETURN n.id AS id"
    return graph.query(query)


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


def typed_bfs_gestaltdb(graph: GraphDB, start_node: str, depth: int, limit: int) -> int:
    visited = set()
    queue = deque([(start_node, 0)])
    while queue and len(visited) < limit:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if current_depth >= depth:
            continue
        for edge_type in EDGE_TYPES:
            for record in gestaltdb_query_neighbors(graph, current, edge_type):
                neighbor = str(record["id"])
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def exact_typed_path_query_gestaltdb(graph: GraphDB, seeds: list[str], path_fanout_limit: int) -> int:
    total = 0
    for seed in seeds:
        query = (
            f"MATCH (a {{id: {cypher_string(seed)}}})-[:RelA]->(b)-[:RelB]->(n) "
            f"RETURN n.id AS id LIMIT {path_fanout_limit}"
        )
        total += len(graph.query(query))
    return total


def deep_typed_query_gestaltdb(graph: GraphDB, seeds: list[str], path_fanout_limit: int) -> int:
    total = 0
    for seed in seeds:
        query = (
            f"MATCH (a {{id: {cypher_string(seed)}}})-[:RelA]->(b)-[:RelB]->(c)-[:RelC]->(d)-[:RelA]->(n) "
            f"RETURN n.id AS id LIMIT {path_fanout_limit}"
        )
        total += len(graph.query(query))
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
    if workload == "rocksdb_compaction":
        row.update({"status": "skipped", "skip_reason": "not applicable: raw RocksDB LSM overwrite workload"})
        add_rates(row)
        return row
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
            _, row["ingest_seconds"] = seconds(lambda: ingest_cypher(session, workload, args))
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


def ingest_cypher(session, workload: str, args: argparse.Namespace) -> None:
    graph_shape = workload_graph_shape(workload, args)
    for start, end in chunks(args.nodes, args.batch_size):
        session.run(
            "UNWIND $rows AS row CREATE (:Node {id: row.id, group: row.group})",
            rows=node_rows(start, end),
        ).consume()
    for start, end in chunks(args.edges, args.batch_size):
        grouped_rows = edge_rows_by_type(start, end, args.nodes, graph_shape)
        for edge_type, rows in grouped_rows.items():
            if not rows:
                continue
            session.run(
                f"UNWIND $rows AS row MATCH (a:Node {{id: row.source}}), (b:Node {{id: row.target}}) "
                f"CREATE (a)-[:{edge_type} {{id: row.id, weight: row.weight}}]->(b)",
                rows=rows,
            ).consume()


def run_cypher_workload(session, workload: str, args: argparse.Namespace) -> int:
    if workload in {"ingest", "columnar_ingest"}:
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
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            rows = session.run("MATCH (:Node {id: $seed})-[:RelA]->(n:Node) RETURN n.id AS id", seed="n0")
            total += sum(1 for _ in rows)
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
    if workload == "deep_typed_query":
        return deep_typed_query_cypher(session, seeds, args.path_fanout_limit)
    if workload == "bfs_depth":
        return typed_bfs_cypher(session, "n0", args.depth, args.bfs_limit)
    raise ValueError(f"unknown workload: {workload}")


def deep_typed_query_cypher(session, seeds: list[str], path_fanout_limit: int) -> int:
    total = 0
    query = (
        "MATCH (:Node {id: $seed})-[:RelA]->(:Node)-[:RelB]->(:Node)-[:RelC]->(:Node)-[:RelA]->(n:Node) "
        "RETURN n.id AS id LIMIT $limit"
    )
    for seed in seeds:
        total += len(session.run(query, seed=seed, limit=path_fanout_limit).data())
    return total


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
    if workload == "rocksdb_compaction":
        row.update({"status": "skipped", "skip_reason": "not applicable: raw RocksDB LSM overwrite workload"})
        add_rates(row)
        return row
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
        _, row["ingest_seconds"] = seconds(lambda: ingest_arcadedb(db, workload, args))
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


def ingest_arcadedb(db, workload: str, args: argparse.Namespace) -> None:
    rid_lookup: dict[str, str] = {}
    graph_shape = workload_graph_shape(workload, args)
    with db.graph_batch(batch_size=args.batch_size, expected_edge_count=args.edges, bidirectional=False, commit_every=args.batch_size, use_wal=False) as batch:
        for start, end in chunks(args.nodes, args.batch_size):
            rows = node_rows(start, end)
            rids = batch.create_vertices("Node", rows)
            rid_lookup.update((str(row["id"]), rid) for row, rid in zip(rows, rids))
        for start, end in chunks(args.edges, args.batch_size):
            for row in edge_rows(start, end, args.nodes, graph_shape):
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


def cypher_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def run_arcadedb_workload(db, workload: str, args: argparse.Namespace) -> int:
    if workload in {"ingest", "columnar_ingest"}:
        return args.nodes + args.edges
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            total += arcade_count(db.query("sql", "SELECT expand(out('RelA')) FROM Node WHERE id = ?", "n0"))
        return total
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
    if workload == "deep_typed_query":
        total = 0
        for seed in seed_ids(args):
            query = (
                f"MATCH {{type: Node, where: (id = {sql_string(seed)})}}"
                ".out('RelA'){}.out('RelB'){}.out('RelC'){}.out('RelA'){as: n} "
                f"RETURN n.id AS id LIMIT {args.path_fanout_limit}"
            )
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


def age_dsn(args: argparse.Namespace) -> str:
    if args.age_dsn:
        return args.age_dsn
    return f"host={args.age_host} port={args.age_port} dbname={args.age_database} user={args.age_user} password={args.age_password}"


def age_display_uri(args: argparse.Namespace) -> str:
    if args.age_dsn:
        return args.age_dsn.replace(args.age_password, "****") if args.age_password else args.age_dsn
    return f"postgresql://{args.age_user}:****@{args.age_host}:{args.age_port}/{args.age_database}"


def wait_for_age(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("psycopg") is None:
        raise RuntimeError("missing psycopg Python package")
    import psycopg

    deadline = time.monotonic() + args.container_wait_timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(age_dsn(args), autocommit=True) as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"timed out waiting for Apache AGE PostgreSQL service: {last_error}")


def age_graph_name(args: argparse.Namespace) -> str:
    graph = args.age_graph
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", graph):
        raise ValueError("--age-graph must be a SQL identifier")
    return graph


def age_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def age_csv_value(value: object) -> object:
    return value


def age_cypher_sql(graph: str, query: str, columns: str = "value agtype") -> str:
    if "$$" in query:
        raise ValueError("AGE Cypher query cannot contain $$")
    return f"SELECT * FROM cypher('{graph}', $${query}$$) AS ({columns})"


def age_execute(conn, graph: str, query: str, columns: str = "value agtype"):
    return list(conn.execute(age_cypher_sql(graph, query, columns)))


def age_execute_command(conn, graph: str, query: str) -> None:
    age_execute(conn, graph, query)


def age_int(value: object) -> int:
    if isinstance(value, int):
        return value
    match = re.search(r"-?\d+", str(value))
    return int(match.group(0)) if match else 0


def age_text(value: object) -> str:
    text = str(value)
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return text


def run_age(workload: str, args: argparse.Namespace) -> dict[str, object]:
    row = base_row("apache-age", workload, args)
    if workload == "rocksdb_compaction":
        row.update({"status": "skipped", "skip_reason": "not applicable: raw RocksDB LSM overwrite workload"})
        add_rates(row)
        return row
    if importlib.util.find_spec("psycopg") is None:
        row.update({"status": "skipped", "skip_reason": "missing psycopg Python package"})
        return row
    import psycopg

    row["uri"] = age_display_uri(args)
    conn = None
    try:
        wait_for_age(args)
        conn = psycopg.connect(age_dsn(args), autocommit=True)
        setup_age(conn, args)
        _, row["ingest_seconds"] = seconds(lambda: ingest_age(conn, workload, args))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_age(conn, args))
        row["count_status"] = count_status(row, args)
        result_count, row["query_seconds"] = seconds(lambda: run_age_workload(conn, workload, args))
        row["result_count"] = result_count
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    finally:
        if conn is not None:
            conn.close()
    add_rates(row)
    return row


def setup_age(conn, args: argparse.Namespace) -> None:
    graph = age_graph_name(args)
    conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    conn.execute('SET search_path = ag_catalog, "$user", public')
    try:
        conn.execute(f"SELECT * FROM ag_catalog.drop_graph('{graph}', true)")
    except Exception:
        pass
    conn.execute(f"SELECT * FROM ag_catalog.create_graph('{graph}')")
    for label_func, label in [("create_vlabel", "Node"), ("create_elabel", "RelA"), ("create_elabel", "RelB"), ("create_elabel", "RelC")]:
        try:
            conn.execute(f"SELECT * FROM ag_catalog.{label_func}('{graph}', '{label}')")
        except Exception:
            pass


def ingest_age(conn, workload: str, args: argparse.Namespace) -> None:
    graph = age_graph_name(args)
    graph_shape = workload_graph_shape(workload, args)
    host_dir = getattr(args, "_age_load_host_dir", None)
    container_dir = getattr(args, "_age_load_container_dir", None)
    if not host_dir or not container_dir:
        raise RuntimeError("AGE bulk loading requires the managed Docker container so CSV files are visible to PostgreSQL")
    host_dir = Path(host_dir)

    nodes_path = host_dir / "nodes.csv"
    with nodes_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "node_id", "group"])
        for index in range(args.nodes):
            writer.writerow([index + 1, age_csv_value(f"n{index}"), index % 128])
    conn.execute(f"SELECT * FROM ag_catalog.load_labels_from_file('{graph}', 'Node', '{container_dir}/nodes.csv')")

    edge_paths = {edge_type: host_dir / f"{edge_type}.csv" for edge_type in EDGE_TYPES}
    handles = []
    writers = {}
    try:
        for edge_type, path in edge_paths.items():
            handle = path.open("w", newline="", encoding="utf-8")
            handles.append(handle)
            writer = csv.writer(handle)
            writer.writerow(["start_id", "start_vertex_type", "end_id", "end_vertex_type", "edge_id", "weight"])
            writers[edge_type] = writer
        for row in edge_rows(0, args.edges, args.nodes, graph_shape):
            edge_type = str(row["type"])
            source_index = int(str(row["source"])[1:]) + 1
            target_index = int(str(row["target"])[1:]) + 1
            writers[edge_type].writerow([source_index, "Node", target_index, "Node", age_csv_value(str(row["id"])), row["weight"]])
    finally:
        for handle in handles:
            handle.close()
    for edge_type in EDGE_TYPES:
        conn.execute(f"SELECT * FROM ag_catalog.load_edges_from_file('{graph}', '{edge_type}', '{container_dir}/{edge_type}.csv')")


def run_age_workload(conn, workload: str, args: argparse.Namespace) -> int:
    if workload in {"ingest", "columnar_ingest"}:
        return args.nodes + args.edges
    seeds = seed_ids(args)
    graph = age_graph_name(args)
    if workload == "neighbors":
        total = 0
        for seed in seeds:
            rows = age_execute(conn, graph, f"MATCH (:Node {{node_id: {age_string(seed)}}})-[:RelA]->(n:Node) RETURN count(n)", "count agtype")
            total += age_int(rows[0][0]) if rows else 0
        return total
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            total += len(age_execute(conn, graph, "MATCH (:Node {node_id: 'n0'})-[:RelA]->(n:Node) RETURN n.node_id", "id agtype"))
        return total
    if workload == "sample_neighbors":
        total = 0
        rng = random.Random(args.seed)
        for seed in seeds:
            rows = age_execute(conn, graph, f"MATCH (:Node {{node_id: {age_string(seed)}}})-[:RelA]->(n:Node) RETURN n.node_id", "id agtype")
            total += reservoir_count(rows, args.sample_size, rng)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seeds:
            query = f"MATCH (:Node {{node_id: {age_string(seed)}}})-[:RelA]->(:Node)-[:RelB]->(n:Node) RETURN n.node_id LIMIT {args.path_fanout_limit}"
            total += len(age_execute(conn, graph, query, "id agtype"))
        return total
    if workload == "deep_typed_query":
        total = 0
        for seed in seeds:
            query = (
                f"MATCH (:Node {{node_id: {age_string(seed)}}})-[:RelA]->(:Node)-[:RelB]->(:Node)-[:RelC]->(:Node)-[:RelA]->(n:Node) "
                f"RETURN n.node_id LIMIT {args.path_fanout_limit}"
            )
            total += len(age_execute(conn, graph, query, "id agtype"))
        return total
    if workload == "bfs_depth":
        return typed_bfs_age(conn, "n0", args.depth, args.bfs_limit, args)
    raise ValueError(f"unknown workload: {workload}")


def age_neighbors(conn, node_id: str, edge_type: str, args: argparse.Namespace) -> list[str]:
    graph = age_graph_name(args)
    rows = age_execute(conn, graph, f"MATCH (:Node {{node_id: {age_string(node_id)}}})-[:{edge_type}]->(n:Node) RETURN n.node_id", "id agtype")
    return [age_text(row[0]) for row in rows]


def typed_bfs_age(conn, start_node: str, depth: int, limit: int, args: argparse.Namespace) -> int:
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
            for neighbor in age_neighbors(conn, current, edge_type, args):
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def count_age(conn, args: argparse.Namespace) -> tuple[int, int]:
    graph = age_graph_name(args)
    node_rows_count = age_execute(conn, graph, "MATCH (n:Node) RETURN count(n)", "count agtype")
    edge_rows_count = age_execute(conn, graph, "MATCH ()-[r]->() RETURN count(r)", "count agtype")
    nodes = age_int(node_rows_count[0][0]) if node_rows_count else 0
    edges = age_int(edge_rows_count[0][0]) if edge_rows_count else 0
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
    parser = argparse.ArgumentParser(description="Benchmark GestaltDB against Neo4j, Memgraph, ArcadeDB, and Apache AGE")
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=["gestaltdb", "gestaltdb-tx", "neo4j", "memgraph", "arcadedb", "age"],
        default=["gestaltdb", "gestaltdb-tx", "neo4j", "memgraph", "arcadedb", "age"],
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=["ingest", "columnar_ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path", "deep_typed_query", "rocksdb_compaction"],
        default=["ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path", "deep_typed_query"],
    )
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
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/external_graphdbs"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    parser.add_argument("--no-containers", action="store_true", help="Use configured external database URIs without starting Docker containers")
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
    parser.add_argument("--age-image", default="apache/age:latest")
    parser.add_argument("--age-dsn", default="", help="psycopg connection string for an existing PostgreSQL/AGE service")
    parser.add_argument("--age-host", default="localhost")
    parser.add_argument("--age-port", type=int, default=15432)
    parser.add_argument("--age-database", default="postgres")
    parser.add_argument("--age-user", default="postgres")
    parser.add_argument("--age-password", default="gestaltdb-bench-password")
    parser.add_argument("--age-graph", default="gestaltdb_bench")
    parser.add_argument("--age-batch-size", type=int, default=500)
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=int, default=10)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    parser.add_argument("--compaction-keys", type=int, default=50_000)
    parser.add_argument("--compaction-passes", type=int, default=4)
    parser.add_argument("--compaction-value-size", type=int, default=1024)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = []
    runners = {
        "gestaltdb": lambda workload, args: run_gestaltdb(workload, args),
        "gestaltdb-tx": lambda workload, args: run_gestaltdb(workload, args, transactional=True),
        "neo4j": lambda workload, args: run_with_container("neo4j", workload, args, run_bolt_engine),
        "memgraph": lambda workload, args: run_with_container("memgraph", workload, args, run_bolt_engine),
        "arcadedb": lambda workload, args: run_arcadedb(workload, args),
        "age": lambda workload, args: run_with_container("age", workload, args, lambda _engine, workload, args: run_age(workload, args)),
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
