from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from statistics import mean

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from example.gnn_kg.config import make_config
    from example.gnn_kg.sampler import make_sampler
else:
    from .config import make_config
    from .sampler import make_sampler


def open_graphdb(cfg):
    src_path = cfg.paths.project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.kvstores import PyRexStore
    from gestaltdb.serializers import JSONSerializer

    return GraphDB(PyRexStore(path=str(cfg.paths.db_path)), JSONSerializer())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batches", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--graphdb-sampler", action="store_true")
    parser.add_argument("--backend", choices=["array", "engine", "graphdb", "all"], default=None)
    parser.add_argument("--engine-negative-source", choices=["random", "context_relation_neighbors", "non_visited_relation_neighbors"], default=None)
    args = parser.parse_args()
    cfg = make_config(smoke_test=args.smoke_test)
    if args.batch_size is not None:
        cfg.sampler.batch_size = args.batch_size
    if args.graphdb_sampler:
        args.backend = "graphdb"
    if args.engine_negative_source is not None:
        cfg.sampler.engine_negative_source = args.engine_negative_source
    backends = ["graphdb", "array", "engine"] if args.backend == "all" else [args.backend or cfg.sampler.backend]

    results = []
    for backend in backends:
        cfg.sampler.backend = backend
        cfg.sampler.use_array_backend = backend != "graphdb"
        graphdb = open_graphdb(cfg) if backend == "graphdb" else None
        try:
            sampler = make_sampler(cfg, graphdb)
            # Exclude one warmup batch so snapshot page cache and RNG setup do not dominate small runs.
            sampler.sample_batch()
            t0 = time.perf_counter()
            nodes = 0
            edges = 0
            batch_seconds = []
            for _ in range(args.batches):
                batch_t0 = time.perf_counter()
                batch = sampler.sample_batch()
                batch_seconds.append(time.perf_counter() - batch_t0)
                nodes += len(batch.node_ids)
                edges += len(batch.edge_rel_ids)
            elapsed = time.perf_counter() - t0
            result = {
                "backend": backend,
                "seconds": elapsed,
                "batches_per_s": args.batches / elapsed,
                "avg_batch_ms": mean(batch_seconds) * 1000,
                "avg_nodes": nodes / args.batches,
                "avg_edges": edges / args.batches,
            }
            results.append(result)
            print(
                f"backend={backend} batches={args.batches} seconds={elapsed:.3f} "
                f"batches_per_s={result['batches_per_s']:.3f} avg_batch_ms={result['avg_batch_ms']:.2f}"
            )
            print(f"backend={backend} avg_nodes={result['avg_nodes']:.1f} avg_edges={result['avg_edges']:.1f}")
        finally:
            if graphdb is not None:
                graphdb.close()

    if len(results) > 1:
        baseline = next((result for result in results if result["backend"] == "graphdb"), results[0])
        for result in results:
            if result is baseline:
                continue
            print(f"speedup {result['backend']} vs {baseline['backend']}: {result['batches_per_s'] / baseline['batches_per_s']:.2f}x")


if __name__ == "__main__":
    main()
