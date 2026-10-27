"""RWS-08: profile reference/policy walks and optional transition caching.

Run: uv run python -m benchmarks.random_walks --nodes 200 --edges 1200 --walks 400
Output is JSON. Timings include hashing streamed results and Python tracing;
tracemalloc is not RSS, and process peak RSS is cumulative, not per-case memory.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import cProfile
import hashlib
import json
import platform
import pstats
import resource
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

import numpy as np

from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, WalkPolicy
from .walk_cache import WalkTransitionCache, cache_transitions


def snapshot(root, nodes, edges, seed, workload):
    rng = np.random.default_rng(seed)
    src = rng.integers(nodes, size=edges)
    dst = rng.integers(nodes, size=edges)
    if workload == "hub":
        mask = rng.random(edges) < .7
        src[mask] = 0
        dst[rng.random(edges) < .4] = 0
    return SamplerSnapshot.from_edge_arrays(
        root, external_node_ids=[f"n{i}" for i in range(nodes)],
        external_edge_ids=[f"e{i}" for i in range(edges)], external_relation_ids=["R"],
        src_int=src, dst_int=dst, rel_int=np.zeros(edges, dtype=np.int64),
        edge_weights=rng.uniform(.1, 2, edges),
    )


def consume(engine, policy, starts, length, batch_size, walk_type="policy"):
    digest = hashlib.sha256()
    transitions = rows = output_bytes = 0
    options = dict(policy=policy) if walk_type == "policy" else (
        dict(strategy="weighted") if walk_type == "random" else {}
    )
    for batch in engine.iter_random_walks(
        starts, length, walk_type=walk_type, batch_size=batch_size, direction="any", **options,
    ):
        transitions += int(np.count_nonzero(batch.edge_indices >= 0))
        rows += len(batch.lengths)
        for array in batch.to_numpy().values():
            digest.update(array.tobytes())
            output_bytes += array.nbytes
    return dict(digest=digest.hexdigest(), transitions=transitions, walks=rows, output_bytes=output_bytes)


def run_case(source, starts, args, node2vec, cache=None, legacy=False):
    engine = SamplerEngine.load(source.path, mode=args.mode, seed=args.seed)
    options = dict(node2vec=node2vec, strategy="weighted")
    policy = WalkPolicy(**options)
    tracemalloc.start()
    start = perf_counter()
    with cache_transitions(cache) if cache is not None else nullcontext():
        result = consume(engine, policy, starts, args.length, args.batch_size,
                         ("node2vec" if node2vec else "random") if legacy else "policy")
    elapsed = perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result.update(seconds=elapsed, transitions_per_second=result["transitions"] / elapsed,
                  walks_per_second=result["walks"] / elapsed, python_peak_bytes=peak,
                  cache=None if cache is None else cache.stats())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=200)
    parser.add_argument("--edges", type=int, default=1200)
    parser.add_argument("--walks", type=int, default=400)
    parser.add_argument("--length", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--mode", choices=["ram", "memmap"], default="ram")
    parser.add_argument("--workload", choices=["uniform", "hub", "all"], default="all")
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--cache-entries", type=int, default=1024)
    parser.add_argument("--cache-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args(argv)
    if min(args.nodes, args.edges, args.walks, args.length, args.batch_size) < 1:
        parser.error("nodes, edges, walks, length and batch size must be positive")
    report = dict(python=platform.python_version(), numpy=np.__version__, parameters=vars(args), cases=[])
    with TemporaryDirectory() as root:
        for workload in (["uniform", "hub"] if args.workload == "all" else [args.workload]):
            source = snapshot(Path(root) / workload, args.nodes, args.edges, args.seed, workload)
            starts = np.arange(args.walks) % args.nodes
            for biased in (False, True):
                reference = run_case(source, starts, args, biased)
                legacy = run_case(source, starts, args, biased, legacy=True)
                if legacy["digest"] != reference["digest"]:
                    raise AssertionError("policy changed legacy seeded outputs for equivalent options")
                result = dict(workload=workload, node2vec=biased, reference=reference, legacy=legacy)
                if args.cache:
                    cache = WalkTransitionCache(max_entries=args.cache_entries, max_bytes=args.cache_bytes)
                    cached = run_case(source, starts, args, biased, cache)
                    if cached["digest"] != reference["digest"]:
                        raise AssertionError("cache changed seeded reference outputs")
                    result.update(cached=cached, reference_over_cached_seconds=reference["seconds"] / cached["seconds"])
                if args.profile:
                    profiler = cProfile.Profile()
                    profiler.runcall(consume, SamplerEngine(source, seed=args.seed),
                                     WalkPolicy(node2vec=biased, strategy="weighted"), starts,
                                     args.length, args.batch_size)
                    stats = pstats.Stats(profiler)
                    rows = sorted(stats.stats.items(), key=lambda row: row[1][3], reverse=True)[:15]
                    result["profile"] = [dict(function=f"{Path(key[0]).name}:{key[1]}:{key[2]}",
                                               calls=value[1], own_seconds=value[2], cumulative_seconds=value[3])
                                         for key, value in rows]
                report["cases"].append(result)
    report["process_peak_rss_kib_linux"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
