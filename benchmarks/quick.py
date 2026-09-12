"""Quick benchmark suite for GestaltDB ingestion and typed sampling.

Run examples:

    python -m benchmarks.quick --backend lmdb --nodes 10000 --edges 50000
    python -m benchmarks.quick --backend leveldb --nodes 10000 --edges 50000
    python -m benchmarks.quick --backend rocksdb --nodes 10000 --edges 50000 --rocksdb-bloom-bits 10
    python -m benchmarks.quick --backend rocksdb --nodes 10000 --edges 50000 --rocksdb-transactional

The benchmark intentionally uses the public API. It is designed to catch large
performance regressions and compare ingestion modes, not to be a full profiler.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import random
import shutil
import sys
import tempfile
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.sampling import SamplingHop, SamplingPattern
from benchmarks.common.datasets import BIO_EDGE_TYPES, chunk_items
from benchmarks.common.storage import open_gestaltdb
from benchmarks.common.timing import timed


def make_edges(num_nodes: int, num_edges: int, seed: int) -> list[Edge]:
    """Create deterministic typed edges for ingestion benchmarks."""
    rng = random.Random(seed)
    edge_types = BIO_EDGE_TYPES
    edges: list[Edge] = []
    for index in range(num_edges):
        source = f"n{rng.randrange(num_nodes)}"
        target = f"n{rng.randrange(num_nodes)}"
        edge_type = edge_types[index % len(edge_types)]
        edges.append(
            Edge(
                edge_id=f"e{index}",
                source=source,
                target=target,
                properties={"type": edge_type, "weight": index % 100},
            )
        )
    return edges


def run_benchmark(args: argparse.Namespace) -> None:
    """Run ingestion and sampling benchmarks."""
    path = tempfile.mkdtemp(prefix=f"gestaltdb_{args.backend}_benchmark_")
    rocksdb_options = {
        "parallelism": args.rocksdb_parallelism,
        "max_background_jobs": args.rocksdb_max_background_jobs,
        "write_buffer_size": args.rocksdb_write_buffer_size,
        "bloom_bits_per_key": args.rocksdb_bloom_bits,
        "disable_wal": args.rocksdb_disable_wal,
        "transactional": args.rocksdb_transactional,
    }
    # Filter None values
    rocksdb_options = {k: v for k, v in rocksdb_options.items() if v is not None}
    graph = open_gestaltdb(path, backend=args.backend, serializer="pickle", rocksdb_options=rocksdb_options)

    try:
        nodes = [Node(node_id=f"n{index}", properties={"group": index % 10}) for index in range(args.nodes)]
        edges = make_edges(args.nodes, args.edges, args.seed)

        _, node_time = timed(
            f"put_nodes ({args.nodes})",
            lambda: graph.put_nodes(nodes),
        )

        def insert_edges():
            for edge_chunk in chunk_items(edges, args.batch_size):
                graph.put_edges_bulk(edge_chunk, check_existing=not args.append_only)

        _, edge_time = timed(
            f"put_edges_bulk ({args.edges}, batch={args.batch_size}, append_only={args.append_only})",
            insert_edges,
        )

        sample_nodes = [f"n{index}" for index in range(min(args.samples, args.nodes))]
        pattern = SamplingPattern([
            SamplingHop("drug-to-protein", direction="out", sample_size=args.sample_size),
            SamplingHop("protein-to-disease", direction="out", sample_size=args.sample_size),
        ])

        _, neighbor_time = timed(
            f"sample_neighbors ({len(sample_nodes)})",
            lambda: [
                graph.sample_neighbors(node_id, "drug-to-protein", sample_size=args.sample_size)
                for node_id in sample_nodes
            ],
        )
        _, path_time = timed(
            f"sample_typed_paths ({len(sample_nodes)})",
            lambda: graph.sample_typed_paths(sample_nodes, pattern, rng=random.Random(args.seed)),
        )

        node_rate = args.nodes / node_time if node_time > 0 else 0
        edge_rate = args.edges / edge_time if edge_time > 0 else 0
        neighbor_rate = len(sample_nodes) / neighbor_time if neighbor_time > 0 else 0
        path_rate = len(sample_nodes) / path_time if path_time > 0 else 0

        print(f"node_insert_rate: {node_rate:,.0f} nodes/s")
        print(f"edge_insert_rate: {edge_rate:,.0f} edges/s")
        print(f"neighbor_sample_rate: {neighbor_rate:,.0f} seeds/s")
        print(f"typed_path_sample_rate: {path_rate:,.0f} seeds/s")
    finally:
        graph.close()
        shutil.rmtree(path, ignore_errors=True)


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build or configure argument parser for quick benchmark."""
    parser = subparser or argparse.ArgumentParser(description="Run GestaltDB ingestion and sampling microbenchmark")
    parser.add_argument("--backend", choices=["lmdb", "leveldb", "rocksdb"], default="lmdb")
    parser.add_argument("--nodes", type=int, default=10_000)
    parser.add_argument("--edges", type=int, default=50_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--samples", type=int, default=1_000)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--append-only", action="store_true", help="skip existing-edge reads during bulk ingestion")
    parser.add_argument("--rocksdb-parallelism", type=int, default=None)
    parser.add_argument("--rocksdb-max-background-jobs", type=int, default=None)
    parser.add_argument("--rocksdb-write-buffer-size", type=int, default=None)
    parser.add_argument("--rocksdb-bloom-bits", type=float, default=None)
    parser.add_argument("--rocksdb-disable-wal", action="store_true")
    parser.add_argument("--rocksdb-transactional", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    run_benchmark(args)


if __name__ == "__main__":
    main()
