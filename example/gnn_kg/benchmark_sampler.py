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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batches", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument(
        "--engine-negative-source",
        choices=["random", "context_relation_neighbors", "non_visited_relation_neighbors"],
        default=None,
    )
    args = parser.parse_args()
    cfg = make_config(smoke_test=args.smoke_test)
    if args.batch_size is not None:
        cfg.sampler.batch_size = args.batch_size
    if args.engine_negative_source is not None:
        cfg.sampler.engine_negative_source = args.engine_negative_source

    sampler = make_sampler(cfg)
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
    print(
        f"backend=engine batches={args.batches} seconds={elapsed:.3f} "
        f"batches_per_s={args.batches / elapsed:.3f} avg_batch_ms={mean(batch_seconds) * 1000:.2f}"
    )
    print(f"backend=engine avg_nodes={nodes / args.batches:.1f} avg_edges={edges / args.batches:.1f}")


if __name__ == "__main__":
    main()
