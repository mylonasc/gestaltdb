"""Deterministic temporal, sampling, rule, and modal benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time
import tracemalloc
from typing import Sequence

from gestaltdb.graphdb import Edge, Node
from gestaltdb.sampling import SamplerEngine
from gestaltdb.temporal import TemporalContext
from gestaltdb.versioning import EdgeVersionWrite, NodeVersionWrite

from .common.storage import disk_usage, open_gestaltdb, validate_matrix_dependencies


def _elapsed(callable_obj):
    started = time.perf_counter()
    result = callable_obj()
    return result, time.perf_counter() - started


def run_benchmark(args: argparse.Namespace) -> dict[str, object]:
    """Run one deterministic generated workload and return its metrics."""
    if args.nodes < 2 or args.edges < 1 or args.queries < 0 or args.interval_versions < 0:
        raise ValueError(
            "nodes must be at least 2, edges at least 1, and queries/interval-versions non-negative"
        )
    dependency_error = validate_matrix_dependencies(args.backend, "object", args.serializer)
    if dependency_error == "json cannot serialize legacy adjacency bytes written by object ingestion":
        dependency_error = None
    if dependency_error is not None:
        return {"status": "skipped", "skip_reason": dependency_error}
    workdir = Path(tempfile.mkdtemp(prefix="gestaltdb_temporal_"))
    database_path = workdir / "database"
    snapshot_path = workdir / "snapshot"
    graph = open_gestaltdb(database_path, backend=args.backend, serializer=args.serializer)
    tracemalloc.start()
    try:
        node_writes = [
            NodeVersionWrite.assertion(Node(f"n{index}", labels=["Entity"]), (0, None))
            for index in range(args.nodes)
        ]
        edge_writes = [
            EdgeVersionWrite.assertion(
                Edge(
                    f"e{index}",
                    f"n{index % args.nodes}",
                    f"n{(index + 1) % args.nodes}",
                    {"type": f"R{index % 3}"},
                ),
                (index % 10, None),
            )
            for index in range(args.edges)
        ]
        interval_writes = [
            NodeVersionWrite.assertion(
                Node("interval-probe", labels=["IntervalProbe"]),
                (index, index + 1),
            )
            for index in range(args.interval_versions)
        ]
        payload_bytes = sum(
            len(graph.entity_serializer.serialize(write.node, "Node"))
            for write in node_writes
        ) + sum(
            len(graph.entity_serializer.serialize(write.edge, "Edge"))
            for write in edge_writes
        )
        commit, write_seconds = _elapsed(
            lambda: graph.commit_versions(node_writes + edge_writes + interval_writes)
        )

        interval_candidates_decoded = 0
        if interval_writes:
            original_lookup = graph._get_temporal_version_at

            def counted_lookup(locator):
                nonlocal interval_candidates_decoded
                interval_candidates_decoded += 1
                return original_lookup(locator)

            graph._get_temporal_version_at = counted_lookup
            try:
                graph.get_node_as_of(
                    "interval-probe", valid_time=args.interval_versions - 1
                )
            finally:
                graph._get_temporal_version_at = original_lookup

        query_latencies = []
        for index in range(args.queries):
            _, elapsed = _elapsed(
                lambda index=index: graph.get_edge_as_of(
                    f"e{index % args.edges}", valid_time=20
                )
            )
            query_latencies.append(elapsed)

        snapshot, snapshot_seconds = _elapsed(
            lambda: graph.build_sampler_snapshot(
                snapshot_path, temporal=True, time_bucket="none"
            )
        )
        engine = SamplerEngine(snapshot, seed=args.seed)
        seed_nodes = list(range(min(args.sample_seeds, args.nodes)))
        sampled, sampling_seconds = _elapsed(
            lambda: engine.sample_neighbors(
                seed_nodes, args.fanout, temporal=TemporalContext.as_of(20)
            )
        )

        graph.assert_claim(
            subject="n0", predicate="P", object="n1", polarity="positive",
            agent="source:benchmark", world="verified", valid=(0, None),
        )
        graph.create_rule("p-implies-q", when=[("?x", "P", "?y")], then=("?x", "Q", "?y"))
        rule_result, rule_seconds = _elapsed(
            lambda: graph.run_rules(
                as_of=20, world="verified", max_iterations=10,
                max_derivations=100, max_justifications=100,
            )
        )
        graph.assert_world_accessibility(
            agent="benchmark", from_world="actual", to_world="verified",
            kind="knowledge", valid=(0, None),
        )
        modal_result, modal_seconds = _elapsed(
            lambda: graph.entails(
                "benchmark", {"subject": "n0", "predicate": "Q", "object": "n1"},
                "KNOWS", world="actual", valid_time=20, max_depth=4, max_states=100,
            )
        )
        _, peak_memory = tracemalloc.get_traced_memory()
        storage_bytes = disk_usage(database_path)
        ordered_latencies = sorted(query_latencies)
        p50 = ordered_latencies[len(ordered_latencies) // 2] if ordered_latencies else 0.0
        p95_index = min(len(ordered_latencies) - 1, int(len(ordered_latencies) * 0.95))
        p95 = ordered_latencies[p95_index] if ordered_latencies else 0.0
        result = {
            "status": "ok",
            "backend": args.backend,
            "serializer": args.serializer,
            "seed": args.seed,
            "nodes": args.nodes,
            "edges": args.edges,
            "versions_written": len(commit.versions),
            "write_seconds": write_seconds,
            "versions_per_second": len(commit.versions) / write_seconds if write_seconds else 0.0,
            "query_count": args.queries,
            "query_p50_seconds": p50,
            "query_p95_seconds": p95,
            "interval_versions": args.interval_versions,
            "interval_candidates_decoded": interval_candidates_decoded,
            "snapshot_seconds": snapshot_seconds,
            "sample_seconds": sampling_seconds,
            "sampled_edges": int(sampled.edge_indices.size),
            "rule_seconds": rule_seconds,
            "derived_claims": rule_result.derived_count,
            "modal_seconds": modal_seconds,
            "modal_status": modal_result.status.value,
            "peak_traced_memory_bytes": peak_memory,
            "storage_bytes": storage_bytes,
            "estimated_payload_bytes": payload_bytes,
            "storage_amplification": storage_bytes / payload_bytes,
        }
        if args.keep:
            result["workdir"] = str(workdir)
        return result
    finally:
        tracemalloc.stop()
        graph.close()
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = subparser or argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["leveldb", "rocksdb", "lmdb"], default="leveldb")
    parser.add_argument("--serializer", choices=["pickle", "json", "msgpack", "protobuf"], default="json")
    parser.add_argument("--nodes", type=int, default=1_000)
    parser.add_argument("--edges", type=int, default=5_000)
    parser.add_argument("--queries", type=int, default=1_000)
    parser.add_argument("--interval-versions", type=int, default=0)
    parser.add_argument("--sample-seeds", type=int, default=100)
    parser.add_argument("--fanout", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = run_benchmark(args)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
