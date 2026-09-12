"""Benchmark GestaltDB against Neo4j, Memgraph, ArcadeDB, and Apache AGE.

Neo4j, Memgraph, and Apache AGE are managed as disposable Docker containers by default.
ArcadeDB uses the optional embedded Python package.
"""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import csv
import importlib.util
import json
import os
from pathlib import Path
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Sequence
import uuid

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import PyRexStore
from gestaltdb.serializers import JSONSerializer
from benchmarks.common.datasets import (
    NAMED_EDGE_TYPES,
    chunks,
    edge_parts,
    edge_rows,
    edge_rows_by_type,
    node_rows,
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
    gestaltdb_deep_typed_query,
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
    "container_image",
    "container_name",
    "uri",
    "cypher_query_mode",
    "cypher_node_index_used",
    "age_property_index",
    "age_node_index_used",
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


def cypher_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def count_gestaltdb(graph: GraphDB) -> tuple[int, int]:
    return sum(1 for _ in graph.store.get_node_keys_generator()), sum(1 for _ in graph.store.get_edge_keys_generator())


def gestaltdb_query_neighbors(graph: GraphDB, seed: str, edge_type: str) -> Any:
    query = f"MATCH (a {{id: {cypher_string(seed)}}})-[:{edge_type}]->(n) RETURN n.id AS id"
    return graph.query(query)


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
        for edge_type in NAMED_EDGE_TYPES:
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
            f"MATCH (a {{id: {cypher_string(seed)}}})-[:RelA]->(b)-[:RelB]->(c)-[:RelC]->(n) "
            f"RETURN n.id AS id LIMIT {path_fanout_limit}"
        )
        total += len(graph.query(query))
    return total


def run_gestaltdb_workload(graph: GraphDB, workload: str, args: argparse.Namespace) -> int:
    seeds = seed_ids(args.iterations, args.nodes)
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


def run_gestaltdb(workload: str, args: argparse.Namespace, *, transactional: bool = False) -> dict[str, Any]:
    engine = "gestaltdb-rocksdb-transactional" if transactional else "gestaltdb-rocksdb"
    row = base_row(engine, workload, args)
    if not has_pyrex():
        row.update({"status": "skipped", "skip_reason": "missing pyrex-rocksdb"})
        return row

    path = Path(tempfile.mkdtemp(prefix=f"{engine}_{workload}_", dir=args.tmp_dir))
    graph: GraphDB | None = None
    try:
        graph = open_gestaltdb(path, args, transactional=transactional)
        shape = workload_graph_shape(workload, args.graph_shape)

        import pyarrow as pa

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
                rows = edge_rows(start, end, args.nodes, shape)
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
        row["count_status"] = count_status(row, args.nodes, args.edges)
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


def cypher_driver(uri: str, auth: tuple[str, str] | None):
    from neo4j import GraphDatabase

    return GraphDatabase.driver(uri, auth=auth)


def run_bolt_engine(engine: str, workload: str, args: argparse.Namespace) -> dict[str, Any]:
    row = base_row(engine, workload, args)
    if importlib.util.find_spec("neo4j") is None:
        row.update({"status": "skipped", "skip_reason": "missing neo4j Python package"})
        return row
    if not docker_available() and not args.no_containers:
        row.update({"status": "skipped", "skip_reason": "docker unavailable"})
        return row

    uri = f"bolt://localhost:{args.neo4j_bolt_port if engine == 'neo4j' else args.memgraph_bolt_port}"
    auth = (args.neo4j_user, args.neo4j_password) if engine == "neo4j" else None
    row["uri"] = uri
    row["cypher_query_mode"] = args.cypher_query_mode

    with managed_container(engine, args) as container_meta:
        row.update(container_meta)
        try:
            wait_for_bolt(uri, auth, args.container_wait_timeout)
            driver = cypher_driver(uri, auth)
            with driver.session() as session:
                setup_cypher_graph(session, engine)
                _, row["ingest_seconds"] = seconds(lambda: ingest_cypher(session, workload, args))
                (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_cypher(session))
                row["count_status"] = count_status(row, args.nodes, args.edges)
                row["cypher_node_index_used"] = cypher_node_index_used(session, engine)
                query_func = run_cypher_workload_batched if args.cypher_query_mode == "batched" else run_cypher_workload
                row["result_count"], row["query_seconds"] = seconds(lambda: query_func(session, workload, args))
            driver.close()
        except Exception as exc:
            row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    add_rates(row)
    return row


def setup_cypher_graph(session: Any, engine: str) -> None:
    try:
        session.run("MATCH (n) DETACH DELETE n").consume()
    except Exception:
        pass
    index_queries = [
        "CREATE INDEX node_id_idx IF NOT EXISTS FOR (n:Node) ON (n.id)",
    ] if engine == "neo4j" else [
        "CREATE INDEX ON :Node(id)",
    ]
    for q in index_queries:
        try:
            session.run(q).consume()
        except Exception:
            pass


def ingest_cypher(session: Any, workload: str, args: argparse.Namespace) -> None:
    shape = workload_graph_shape(workload, args.graph_shape)
    for start, end in chunks(args.nodes, args.batch_size):
        rows = node_rows(start, end)
        session.run("UNWIND $batch AS row CREATE (:Node {id: row.id, group: row.group})", batch=rows).consume()

    for start, end in chunks(args.edges, args.batch_size):
        by_type = edge_rows_by_type(start, end, args.nodes, shape, NAMED_EDGE_TYPES)
        for et, e_rows in by_type.items():
            if not e_rows:
                continue
            session.run(
                f"UNWIND $batch AS row MATCH (a:Node {{id: row.source}}), (b:Node {{id: row.target}}) CREATE (a)-[:{et} {{weight: row.weight}}]->(b)",
                batch=e_rows,
            ).consume()


def run_cypher_workload(session: Any, workload: str, args: argparse.Namespace) -> int:
    seeds = seed_ids(args.iterations, args.nodes)
    if workload in {"ingest", "columnar_ingest"}:
        return args.nodes + args.edges
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            rec = session.run("MATCH (:Node {id: 'n0'})-[:RelA]->(b) RETURN count(b) AS c").single()
            total += rec["c"]
        return total
    if workload == "neighbors":
        total = 0
        for seed in seeds:
            rec = session.run("MATCH (:Node {id: $seed})-[:RelA]->(b) RETURN count(b) AS c", seed=seed).single()
            total += rec["c"]
        return total
    if workload == "sample_neighbors":
        total = 0
        for seed in seeds:
            res = session.run(
                "MATCH (:Node {id: $seed})-[:RelA]->(b) RETURN b.id AS nid LIMIT $limit",
                seed=seed,
                limit=args.sample_size,
            )
            total += sum(1 for _ in res)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seeds:
            res = session.run(
                "MATCH (:Node {id: $seed})-[:RelA]->()-[:RelB]->(b) RETURN b.id AS nid LIMIT $limit",
                seed=seed,
                limit=args.path_fanout_limit,
            )
            total += sum(1 for _ in res)
        return total
    if workload == "deep_typed_query":
        total = 0
        for seed in seeds:
            res = session.run(
                "MATCH (:Node {id: $seed})-[:RelA]->()-[:RelB]->()-[:RelC]->(b) RETURN b.id AS nid LIMIT $limit",
                seed=seed,
                limit=args.path_fanout_limit,
            )
            total += sum(1 for _ in res)
        return total
    if workload == "bfs_depth":
        total = 0
        for _ in range(args.iterations):
            total += typed_bfs_cypher(session, "n0", args.depth, args.bfs_limit)
        return total
    raise ValueError(f"unsupported cypher workload: {workload}")


def run_cypher_workload_batched(session: Any, workload: str, args: argparse.Namespace) -> int:
    seeds = seed_ids(args.iterations, args.nodes)
    if workload in {"ingest", "columnar_ingest"}:
        return args.nodes + args.edges
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            rec = session.run("MATCH (:Node {id: 'n0'})-[:RelA]->(b) RETURN count(b) AS c").single()
            total += rec["c"]
        return total
    if workload == "neighbors":
        rec = session.run("UNWIND $seeds AS s MATCH (:Node {id: s})-[:RelA]->(b) RETURN count(b) AS c", seeds=seeds).single()
        return rec["c"]
    if workload == "sample_neighbors":
        total = 0
        for seed in seeds:
            res = session.run(
                "MATCH (:Node {id: $seed})-[:RelA]->(b) RETURN b.id AS nid LIMIT $limit",
                seed=seed,
                limit=args.sample_size,
            )
            total += sum(1 for _ in res)
        return total
    if workload == "typed_path":
        res = session.run(
            "UNWIND $seeds AS s MATCH (:Node {id: s})-[:RelA]->()-[:RelB]->(b) RETURN b.id AS nid LIMIT $limit",
            seeds=seeds,
            limit=args.path_fanout_limit * len(seeds),
        )
        return sum(1 for _ in res)
    if workload == "deep_typed_query":
        res = session.run(
            "UNWIND $seeds AS s MATCH (:Node {id: s})-[:RelA]->()-[:RelB]->()-[:RelC]->(b) RETURN b.id AS nid LIMIT $limit",
            seeds=seeds,
            limit=args.path_fanout_limit * len(seeds),
        )
        return sum(1 for _ in res)
    if workload == "bfs_depth":
        return typed_bfs_cypher_batched(session, "n0", args.depth, args.bfs_limit)
    return run_cypher_workload(session, workload, args)


def typed_bfs_cypher(session: Any, start_node: str, depth: int, limit: int) -> int:
    visited = set()
    frontier = [start_node]
    cur_depth = 0
    while frontier and cur_depth < depth and len(visited) < limit:
        next_frontier = []
        for nid in frontier:
            res = session.run("MATCH (:Node {id: $nid})-[:RelA|RelB|RelC]->(b) RETURN b.id AS target", nid=nid)
            for rec in res:
                tgt = rec["target"]
                if tgt not in visited:
                    visited.add(tgt)
                    next_frontier.append(tgt)
        frontier = next_frontier
        cur_depth += 1
    return len(visited)


def typed_bfs_cypher_batched(session: Any, start_node: str, depth: int, limit: int) -> int:
    visited = set()
    frontier = [start_node]
    cur_depth = 0
    while frontier and cur_depth < depth and len(visited) < limit:
        res = session.run("UNWIND $frontier AS nid MATCH (:Node {id: nid})-[:RelA|RelB|RelC]->(b) RETURN DISTINCT b.id AS target", frontier=frontier)
        next_frontier = [rec["target"] for rec in res if rec["target"] not in visited]
        visited.update(next_frontier)
        frontier = next_frontier
        cur_depth += 1
    return len(visited)


def cypher_node_index_used(session: Any, engine: str) -> bool:
    try:
        plan = session.run("EXPLAIN MATCH (n:Node {id: 'n0'}) RETURN n").consume().plan
        plan_str = json.dumps(plan or {})
        return "NodeIndex" in plan_str or "Index" in plan_str
    except Exception:
        return False


def count_cypher(session: Any) -> tuple[int, int]:
    rec_nodes = session.run("MATCH (n:Node) RETURN count(n) AS c").single()
    rec_edges = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()
    return rec_nodes["c"], rec_edges["c"]


def run_arcadedb(workload: str, args: argparse.Namespace) -> dict[str, Any]:
    row = base_row("arcadedb-embedded", workload, args)
    if importlib.util.find_spec("arcadedb_embedded") is None:
        row.update({"status": "skipped", "skip_reason": "missing arcadedb-embedded package"})
        return row
    import arcadedb_embedded

    path = Path(tempfile.mkdtemp(prefix="arcadedb_ext_bench_", dir=args.tmp_dir)) / "benchmark.arcadedb"
    db = None
    try:
        db = arcadedb_embedded.create_database(str(path), jvm_kwargs={"heap_size": args.arcadedb_heap_size})
        setup_arcadedb(db)
        _, row["ingest_seconds"] = seconds(lambda: ingest_arcadedb(db, workload, args))
        (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_arcadedb(db))
        row["count_status"] = count_status(row, args.nodes, args.edges)
        row["result_count"], row["query_seconds"] = seconds(lambda: run_arcadedb_workload(db, workload, args))
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


def ingest_arcadedb(db: Any, workload: str, args: argparse.Namespace) -> None:
    shape = workload_graph_shape(workload, args.graph_shape)
    rid_lookup: dict[str, str] = {}
    with db.graph_batch(
        batch_size=args.batch_size,
        expected_edge_count=args.edges,
        bidirectional=False,
        commit_every=args.batch_size,
        use_wal=False,
        parallel_flush=args.arcadedb_parallel > 1,
    ) as batch:
        for start, end in chunks(args.nodes, args.batch_size):
            rows = [{"id": f"n{index}"} for index in range(start, end)]
            node_ids = [r["id"] for r in rows]
            rids = batch.create_vertices("Node", rows)
            rid_lookup.update(zip(node_ids, rids))

        for start, end in chunks(args.edges, args.batch_size):
            e_rows = edge_rows(start, end, args.nodes, shape, NAMED_EDGE_TYPES)
            for r in e_rows:
                batch.new_edge(rid_lookup[r["source"]], r["edge_type"], rid_lookup[r["target"]], id=r["edge_id"])
    db.command("sql", "CREATE INDEX ON Node (id) UNIQUE_HASH")


def count_arcadedb(db: Any) -> tuple[int, int]:
    res_n = db.query("sql", "SELECT count(*) FROM Node").to_list()
    nodes = res_n[0]["count(*)"] if res_n else 0
    edges = sum(
        (db.query("sql", f"SELECT count(*) FROM {et}").to_list()[0]["count(*)"] if db.query("sql", f"SELECT count(*) FROM {et}").to_list() else 0)
        for et in NAMED_EDGE_TYPES
    )
    return nodes, edges


def run_arcadedb_workload(db: Any, workload: str, args: argparse.Namespace) -> int:
    seeds = seed_ids(args.iterations, args.nodes)
    if workload in {"ingest", "columnar_ingest"}:
        return args.nodes + args.edges
    if workload == "star_traversal":
        total = 0
        for _ in range(args.iterations):
            res = db.query("sql", "SELECT expand(out('RelA')) FROM Node WHERE id = 'n0'").to_list()
            total += len(res)
        return total
    if workload == "neighbors":
        total = 0
        for seed in seeds:
            res = db.query("sql", f"SELECT expand(out('RelA')) FROM Node WHERE id = '{seed}'").to_list()
            total += len(res)
        return total
    if workload == "sample_neighbors":
        total = 0
        for seed in seeds:
            res = db.query("sql", f"SELECT expand(out('RelA')) FROM Node WHERE id = '{seed}' LIMIT {args.sample_size}").to_list()
            total += len(res)
        return total
    if workload == "typed_path":
        total = 0
        for seed in seeds:
            res = db.query("sql", f"MATCH {{type: Node, where: (id = '{seed}')}}.out('RelA'){{}}.out('RelB'){{as: n}} RETURN n LIMIT {args.path_fanout_limit}").to_list()
            total += len(res)
        return total
    if workload == "deep_typed_query":
        total = 0
        for seed in seeds:
            query = (
                f"MATCH {{type: Node, where: (id = '{seed}')}}.out('RelA'){{}}.out('RelB'){{}}"
                f".out('RelC'){{as: n}} RETURN n LIMIT {args.path_fanout_limit}"
            )
            total += len(db.query("sql", query).to_list())
        return total
    if workload == "bfs_depth":
        query = f"MATCH {{type: Node, where: (id = 'n0')}}.out('RelA'){{as: n, while: ($depth < {args.depth}), where: ($depth > 0)}} RETURN n LIMIT {args.bfs_limit}"
        return len(db.query("sql", query).to_list())
    raise ValueError(f"unsupported ArcadeDB query workload: {workload}")


def run_age(workload: str, args: argparse.Namespace) -> dict[str, Any]:
    row = base_row("apache-age", workload, args)
    if importlib.util.find_spec("psycopg") is None:
        row.update({"status": "skipped", "skip_reason": "missing psycopg Python package"})
        return row
    if not docker_available() and not args.no_containers:
        row.update({"status": "skipped", "skip_reason": "docker unavailable"})
        return row

    try:
        with managed_container("age", args) as container_meta:
            row.update(container_meta)
            import psycopg

            conn_info = f"host=localhost port={args.age_port} user={args.age_user} password={args.age_password} dbname={args.age_database}"
            # Wait for postgres
            deadline = time.monotonic() + args.container_wait_timeout
            conn = None
            while time.monotonic() < deadline:
                try:
                    conn = psycopg.connect(conn_info, autocommit=True)
                    break
                except Exception:
                    time.sleep(1)
            if conn is None:
                raise RuntimeError("timed out connecting to Apache AGE")

            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS age;")
                cur.execute("LOAD 'age';")
                cur.execute("SET search_path = ag_catalog, '$user', public;")
                try:
                    cur.execute("SELECT drop_graph('bench', true);")
                except Exception:
                    pass
                cur.execute("SELECT create_graph('bench');")
                _, row["ingest_seconds"] = seconds(lambda: ingest_age(cur, workload, args))
                if args.age_require_index:
                    cur.execute("CREATE INDEX IF NOT EXISTS node_props_gin ON bench.\"Node\" USING gin (properties);")
                (row["actual_nodes"], row["actual_edges"]), row["count_seconds"] = seconds(lambda: count_age(cur))
                row["count_status"] = count_status(row, args.nodes, args.edges)
                row["result_count"], row["query_seconds"] = seconds(lambda: run_age_workload(cur, workload, args))
            conn.close()
    except Exception as exc:
        row.update({"status": "failed", "skip_reason": f"{type(exc).__name__}: {exc}"})
    add_rates(row)
    return row


def ingest_age(cur: Any, workload: str, args: argparse.Namespace) -> None:
    shape = workload_graph_shape(workload, args.graph_shape)
    for start, end in chunks(args.nodes, args.batch_size):
        for i in range(start, end):
            cur.execute(
                "SELECT * FROM cypher('bench', $$ CREATE (:Node {id: %s, \"group\": %s}) $$) as (v agtype);",
                (f"n{i}", i % 128),
            )
    for start, end in chunks(args.edges, args.batch_size):
        e_rows = edge_rows(start, end, args.nodes, shape, NAMED_EDGE_TYPES)
        for r in e_rows:
            cur.execute(
                f"SELECT * FROM cypher('bench', $$ MATCH (a:Node {{id: %s}}), (b:Node {{id: %s}}) CREATE (a)-[:{r['edge_type']} {{weight: %s}}]->(b) $$) as (e agtype);",
                (r["source"], r["target"], r["weight"]),
            )


def count_age(cur: Any) -> tuple[int, int]:
    cur.execute("SELECT * FROM cypher('bench', $$ MATCH (n:Node) RETURN count(n) $$) as (c agtype);")
    nodes = int(cur.fetchone()[0])
    cur.execute("SELECT * FROM cypher('bench', $$ MATCH ()-[r]->() RETURN count(r) $$) as (c agtype);")
    edges = int(cur.fetchone()[0])
    return nodes, edges


def run_age_workload(cur: Any, workload: str, args: argparse.Namespace) -> int:
    seeds = seed_ids(args.iterations, args.nodes)
    if workload in {"ingest", "columnar_ingest"}:
        return args.nodes + args.edges
    if workload == "star_traversal":
        cur.execute("SELECT * FROM cypher('bench', $$ MATCH (:Node {id: 'n0'})-[:RelA]->(b) RETURN count(b) $$) as (c agtype);")
        return int(cur.fetchone()[0])
    if workload == "neighbors":
        total = 0
        for s in seeds:
            cur.execute(
                "SELECT * FROM cypher('bench', $$ MATCH (:Node {id: %s})-[:RelA]->(b) RETURN count(b) $$) as (c agtype);",
                (s,),
            )
            total += int(cur.fetchone()[0])
        return total
    if workload == "typed_path":
        total = 0
        for s in seeds:
            cur.execute(
                f"SELECT * FROM cypher('bench', $$ MATCH (:Node {{id: %s}})-[:RelA]->()-[:RelB]->(b) RETURN count(b) $$) as (c agtype);",
                (s,),
            )
            total += int(cur.fetchone()[0])
        return total
    raise ValueError(f"unsupported age query workload: {workload}")


def run_engine_workload(engine: str, workload: str, args: argparse.Namespace) -> dict[str, Any]:
    if engine == "gestaltdb":
        return run_gestaltdb(workload, args, transactional=False)
    if engine == "gestaltdb-tx":
        return run_gestaltdb(workload, args, transactional=True)
    if engine in {"neo4j", "memgraph"}:
        return run_bolt_engine(engine, workload, args)
    if engine == "arcadedb":
        return run_arcadedb(workload, args)
    if engine == "age":
        return run_age(workload, args)
    raise ValueError(f"unknown engine: {engine}")


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = subparser or argparse.ArgumentParser(
        description="Benchmark GestaltDB against Neo4j, Memgraph, ArcadeDB, and Apache AGE"
    )
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=["gestaltdb", "gestaltdb-tx", "neo4j", "memgraph", "arcadedb", "age"],
        default=["gestaltdb", "gestaltdb-tx", "neo4j", "memgraph", "arcadedb", "age"],
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=["columnar_ingest", "ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path", "deep_typed_query"],
        default=["columnar_ingest", "neighbors", "sample_neighbors", "star_traversal", "bfs_depth", "typed_path", "deep_typed_query"],
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
    parser.add_argument("--cypher-query-mode", choices=["single", "batched"], default="batched")
    parser.add_argument("--age-require-index", action="store_true", default=True)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/external_graphdbs"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    parser.add_argument("--no-containers", action="store_true")
    parser.add_argument("--keep-containers", action="store_true")
    parser.add_argument("--container-start-timeout", type=int, default=120)
    parser.add_argument("--container-wait-timeout", type=int, default=60)
    # GestaltDB options
    parser.add_argument("--rocksdb-parallelism", type=int, default=4)
    parser.add_argument("--rocksdb-background-jobs", type=int, default=4)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--rocksdb-bloom-bits", type=float, default=10)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    # Neo4j options
    parser.add_argument("--neo4j-image", default="neo4j:5.23.0-community")
    parser.add_argument("--neo4j-bolt-port", type=int, default=7687)
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="gestaltdb_test")
    # Memgraph options
    parser.add_argument("--memgraph-image", default="memgraph/memgraph:2.18.0")
    parser.add_argument("--memgraph-bolt-port", type=int, default=7688)
    # ArcadeDB options
    parser.add_argument("--arcadedb-heap-size", default="4g")
    parser.add_argument("--arcadedb-parallel", type=int, default=4)
    # Apache AGE options
    parser.add_argument("--age-image", default="apache/age:PG16-v1.5.0")
    parser.add_argument("--age-port", type=int, default=5433)
    parser.add_argument("--age-user", default="postgres")
    parser.add_argument("--age-password", default="gestaltdb_test")
    parser.add_argument("--age-database", default="postgres")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="external_graphdbs_raw.csv",
        jsonl_filename="external_graphdbs_raw.jsonl",
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
                print(f"Finished {label} status={row.get('status')}", flush=True)

    summary = summarize_rows(all_rows)
    summary_writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="external_graphdbs_summary.csv",
        jsonl_filename="external_graphdbs_summary.jsonl",
        fieldnames=SUMMARY_FIELDS,
    )
    summary_writer.write_rows(summary)


if __name__ == "__main__":
    main()
