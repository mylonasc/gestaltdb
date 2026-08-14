"""Small benchmark suite for GestaltDB ingestion and typed sampling.

Run examples:

    python benchmarks.py --backend lmdb --nodes 10000 --edges 50000
    python benchmarks.py --backend leveldb --nodes 10000 --edges 50000
    python benchmarks.py --backend rocksdb --nodes 10000 --edges 50000 --rocksdb-bloom-bits 10

The benchmark intentionally uses the public API. It is designed to catch large
performance regressions and compare ingestion modes, not to be a full profiler.
"""

from __future__ import annotations

import argparse
import random
import shutil
import tempfile
import time

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.serializers import PickleSerializer


def timed(label, func):
    """Run a benchmark function and print elapsed time."""
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    print(f"{label}: {elapsed:.4f}s")
    return result, elapsed


def open_graph(args, path):
    """Open a graph for the selected benchmark backend."""
    if args.backend == "lmdb":
        return GraphDB(LMDBStore(path=path, map_size=2**34), PickleSerializer())
    if args.backend == "leveldb":
        return GraphDB(LevelDBStore(path=path), PickleSerializer())
    if args.backend == "rocksdb":
        return GraphDB(
            PyRexStore(
                path=path,
                parallelism=args.rocksdb_parallelism,
                max_background_jobs=args.rocksdb_max_background_jobs,
                write_buffer_size=args.rocksdb_write_buffer_size,
                bloom_bits_per_key=args.rocksdb_bloom_bits,
                disable_wal=args.rocksdb_disable_wal,
            ),
            PickleSerializer(),
        )
    raise ValueError(f"unknown backend: {args.backend}")


def make_edges(num_nodes, num_edges, seed):
    """Create deterministic typed edges for ingestion benchmarks."""
    rng = random.Random(seed)
    edge_types = ["drug-to-protein", "protein-to-disease", "drug-to-disease"]
    edges = []
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


def chunks(items, chunk_size):
    """Yield fixed-size chunks from a sequence."""
    for start in range(0, len(items), chunk_size):
        yield items[start:start + chunk_size]


def run_benchmark(args):
    """Run ingestion and sampling benchmarks."""
    path = tempfile.mkdtemp(prefix=f"gestaltdb_{args.backend}_benchmark_")
    graph = open_graph(args, path)
    try:
        nodes = [Node(node_id=f"n{index}", properties={"group": index % 10}) for index in range(args.nodes)]
        edges = make_edges(args.nodes, args.edges, args.seed)

        _, node_time = timed(
            f"put_nodes ({args.nodes})",
            lambda: graph.put_nodes(nodes),
        )

        def insert_edges():
            for edge_chunk in chunks(edges, args.batch_size):
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

        print(f"node_insert_rate: {args.nodes / node_time:,.0f} nodes/s")
        print(f"edge_insert_rate: {args.edges / edge_time:,.0f} edges/s")
        print(f"neighbor_sample_rate: {len(sample_nodes) / neighbor_time:,.0f} seeds/s")
        print(f"typed_path_sample_rate: {len(sample_nodes) / path_time:,.0f} seeds/s")
    finally:
        graph.close()
        shutil.rmtree(path, ignore_errors=True)


def main():
    """Parse arguments and run benchmarks."""
    parser = argparse.ArgumentParser(description="Run GestaltDB ingestion and sampling benchmarks")
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
    run_benchmark(parser.parse_args())


if __name__ == "__main__":
    main()
