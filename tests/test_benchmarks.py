"""Tests for the GestaltDB benchmarking suite and modular components."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import tempfile

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.serializers import PickleSerializer

from benchmarks.common.datasets import (
    BIO_EDGE_TYPES,
    DEFAULT_EDGE_TYPES,
    NAMED_EDGE_TYPES,
    chunk_items,
    chunks,
    edge_parts,
    edge_rows,
    edge_rows_by_type,
    make_edge,
    make_node,
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
from benchmarks.common.storage import (
    disk_usage,
    file_stats,
    has_module,
    has_plyvel,
    has_pyrex,
    open_gestaltdb,
    rocksdb_configs,
    serializer_factory,
    validate_matrix_dependencies,
)
from benchmarks.common.timing import (
    cpu_affinity,
    reservoir_count,
    seconds,
    set_thread_env,
    timed,
)
from benchmarks.common.workloads import (
    count_gestaltdb,
    gestaltdb_bfs,
    gestaltdb_neighbors,
    gestaltdb_sample_neighbors,
    gestaltdb_star_traversal,
    gestaltdb_typed_path,
)
from benchmarks import cli, quick, temporal


def test_timing_seconds():
    result, elapsed = seconds(lambda: 42)
    assert result == 42
    assert elapsed >= 0.0


def test_timing_timed(capsys):
    result, elapsed = timed("test_op", lambda: "ok")
    assert result == "ok"
    assert elapsed >= 0.0
    out = capsys.readouterr().out
    assert "test_op:" in out


def test_reservoir_count():
    rng = random.Random(42)
    items = list(range(100))
    sample_size = reservoir_count(items, 5, rng)
    assert sample_size == 5

    small_items = [1, 2]
    assert reservoir_count(small_items, 5, rng) == 2


def test_cpu_affinity_and_thread_env():
    with cpu_affinity(1):
        pass
    set_thread_env(2)
    assert os.environ.get("OMP_NUM_THREADS") == "2"
    assert os.environ.get("POLARS_MAX_THREADS") == "2"


def test_datasets_chunks():
    slices = list(chunks(10, 3))
    assert slices == [(0, 3), (3, 6), (6, 9), (9, 10)]

    items = [1, 2, 3, 4, 5]
    assert list(chunk_items(items, 2)) == [[1, 2], [3, 4], [5]]


def test_datasets_workload_graph_shape():
    assert workload_graph_shape("star_traversal") == "star"
    assert workload_graph_shape("typed_path") == "typed_path"
    assert workload_graph_shape("neighbors") == "synthetic"
    assert workload_graph_shape("neighbors", "custom") == "custom"


def test_datasets_edge_parts():
    # Synthetic
    eid, src, dst, et = edge_parts(0, 10, "synthetic")
    assert eid == "e0"
    assert src == "n0"
    assert dst == "n1"
    assert et in NAMED_EDGE_TYPES

    # Star
    eid, src, dst, et = edge_parts(0, 10, "star")
    assert src == "n0"
    assert dst == "n1"

    # Typed path
    eid, src, dst, et = edge_parts(0, 10, "typed_path")
    assert src == "n0"
    assert dst == "n1"

    # Layered
    eid, src, dst, et = edge_parts(0, 10, "layered")
    assert src == "n0"


def test_datasets_node_and_edge_generators():
    node = make_node(5, label="Person", properties={"age": 30})
    assert node.get_id == "n5"
    assert "Person" in node.labels
    assert node.properties["age"] == 30

    edge = make_edge(3, 10)
    assert edge.get_id == "e3"
    assert "type" in edge.properties
    assert "weight" in edge.properties

    seeds = seed_ids(5, 10)
    assert seeds == ["n0", "n1", "n2", "n3", "n4"]

    nrows = node_rows(0, 2)
    assert len(nrows) == 2
    assert nrows[0]["id"] == "n0"

    erows = edge_rows(0, 3, 10)
    assert len(erows) == 3
    assert "type" in erows[0]
    assert "edge_type" in erows[0]

    by_type = edge_rows_by_type(0, 6, 10)
    assert isinstance(by_type, dict)


def test_storage_serializer_factory():
    assert serializer_factory("pickle").__class__.__name__ == "PickleSerializer"
    assert serializer_factory("json").__class__.__name__ == "JSONSerializer"
    with pytest.raises(ValueError, match="unknown serializer"):
        serializer_factory("invalid_name")


def test_storage_file_and_disk_stats(tmp_path):
    f1 = tmp_path / "test.sst"
    f1.write_bytes(b"12345")
    f2 = tmp_path / "test.log"
    f2.write_bytes(b"123")

    stats = file_stats(tmp_path)
    assert stats["sst_files"] == 1
    assert stats["sst_bytes"] == 5
    assert stats["log_files"] == 1
    assert stats["log_bytes"] == 3
    assert stats["total_files"] == 2

    usage = disk_usage(tmp_path)
    assert usage >= 8


def test_storage_rocksdb_configs():
    cfgs = rocksdb_configs(["default", "parallel"], cores=4)
    assert len(cfgs) == 2
    assert cfgs[0][0] == "default"
    assert cfgs[1][0] == "parallel"
    assert cfgs[1][1]["parallelism"] == 4


def test_storage_dependency_validators():
    assert has_module("os") is True
    assert has_module("non_existent_module_xyz") is False
    reason = validate_matrix_dependencies("leveldb", "object", "pickle")
    if not has_plyvel():
        assert reason == "missing plyvel"


def test_reporting_base_row_and_rates():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=100)
    parser.add_argument("--edges", type=int, default=200)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args([])

    row = base_row("test_engine", "test_workload", args)
    assert row["engine"] == "test_engine"
    assert row["workload"] == "test_workload"
    assert row["nodes"] == 100

    row["ingest_seconds"] = 0.5
    row["query_seconds"] = 0.1
    add_rates(row)
    assert row["nodes_per_second"] == 200.0
    assert row["edges_per_second"] == 400.0
    assert row["queries_per_second"] == 100.0

    row["actual_nodes"] = 100
    row["actual_edges"] = 200
    assert count_status(row, 100, 200) == "ok"
    assert count_status(row, 99, 200) == "mismatch"


def test_reporting_summarize_and_writer(tmp_path):
    rows = [
        {"engine": "E1", "workload": "W1", "status": "ok", "ingest_seconds": 1.0, "nodes": 10},
        {"engine": "E1", "workload": "W1", "status": "ok", "ingest_seconds": 2.0, "nodes": 10},
    ]
    summary = summarize_rows(rows)
    assert len(summary) == 1
    assert summary[0]["runs"] == 2
    assert summary[0]["ingest_seconds_mean"] == 1.5
    assert summary[0]["ingest_seconds_std"] > 0

    writer = ResultWriter(tmp_path, csv_filename="out.csv", jsonl_filename="out.jsonl")
    writer.write_rows(rows)
    assert (tmp_path / "out.csv").exists()
    assert (tmp_path / "out.jsonl").exists()


def test_workloads_against_graphdb(tmp_path):
    if not has_plyvel():
        pytest.skip("plyvel not installed")
    graph = open_gestaltdb(tmp_path / "g", backend="leveldb", serializer="pickle")
    try:
        nodes = [make_node(i) for i in range(10)]
        edges = [make_edge(i, 10, graph_shape="star") for i in range(15)]
        graph.put_nodes(nodes)
        graph.put_edges_bulk(edges)

        n_count, e_count = count_gestaltdb(graph)
        assert n_count == 10
        assert e_count == 15

        bfs_visited = gestaltdb_bfs(graph, "n0", depth=2)
        assert bfs_visited > 0

        star_count = gestaltdb_star_traversal(graph, seed_id="n0")
        assert star_count > 0

        nbr_count = gestaltdb_neighbors(graph, ["n0"])
        assert nbr_count > 0

        sampled = gestaltdb_sample_neighbors(graph, ["n0"], sample_size=3)
        assert sampled <= 3
    finally:
        graph.close()


def test_quick_benchmark_smoke(tmp_path):
    if not has_plyvel():
        pytest.skip("plyvel not installed")
    args = quick.build_parser().parse_args([
        "--backend", "leveldb",
        "--nodes", "20",
        "--edges", "40",
        "--batch-size", "10",
        "--samples", "10",
        "--sample-size", "2",
    ])
    quick.run_benchmark(args)


def test_temporal_benchmark_smoke():
    args = temporal.build_parser().parse_args([
        "--backend", "leveldb",
        "--serializer", "json",
        "--nodes", "20",
        "--edges", "40",
        "--queries", "10",
        "--interval-versions", "25",
        "--sample-seeds", "5",
        "--fanout", "2",
        "--seed", "7",
    ])
    result = temporal.run_benchmark(args)
    if result["status"] == "skipped":
        pytest.skip(result["skip_reason"])
    assert result["versions_written"] == 85
    assert result["interval_candidates_decoded"] == 1
    assert result["query_count"] == 10
    assert result["sampled_edges"] > 0
    assert result["derived_claims"] == 1
    assert result["modal_status"] == "supported"
    assert result["peak_traced_memory_bytes"] > 0
    assert result["storage_amplification"] > 0


def test_cli_help(capsys):
    cli.main(["--help"])
    out = capsys.readouterr().out
    assert "GestaltDB Benchmarking Suite" in out
    assert "quick" in out
    assert "matrix" in out
    assert "compaction" in out
    assert "external" in out
