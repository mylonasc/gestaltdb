"""Small exact-membership startup/lookup comparison, not full-process RSS.

Run: uv run python -m benchmarks.positive_membership
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np

from gestaltdb.sampling.positive_membership import PositiveMembership, build_membership


def measure(nodes=2000, relations=16, edges=10000, lookups=1000, repeats=3):
    rng = np.random.default_rng(429)
    triples = np.column_stack((rng.integers(0, nodes, edges), rng.integers(0, relations, edges),
                               rng.integers(0, nodes, edges))).astype(np.int64)
    queries = triples[rng.integers(0, edges, lookups)].copy()
    queries[::2, 2] = rng.integers(0, nodes, len(queries[::2]))
    def pack(row):
        src, rel, dst = map(int, row)
        return (src * relations + rel) * nodes + dst
    tracemalloc.start()
    start = time.perf_counter()
    codes = set([pack(row) for row in triples.tolist()])
    set_startup = time.perf_counter() - start
    _, set_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    set_bytes = sys.getsizeof(codes) + sum(sys.getsizeof(code) for code in codes)
    start = time.perf_counter()
    keys = build_membership(triples)
    build_seconds = time.perf_counter() - start
    rows = []
    reference = [pack(row) in codes for row in queries]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "keys.npy"
        np.save(path, keys)
        mapped = np.load(path, mmap_mode="r", allow_pickle=False)
        tracemalloc.start()
        start = time.perf_counter()
        index = PositiveMembership(mapped)
        mapped_startup = time.perf_counter() - start
        _, mapped_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert index.triples is mapped
        assert [index.find(*row) is not None for row in queries] == reference
        for label, lookup in (("python-packed-set", lambda row: pack(row) in codes),
                              ("mapped-sorted-triples", lambda row: index.find(*row) is not None)):
            durations = []
            for _ in range(repeats):
                start = time.perf_counter()
                hits = sum(lookup(row) for row in queries)
                durations.append(time.perf_counter() - start)
            rows.append(dict(encoding=label, median_lookup_seconds=statistics.median(durations), hits=hits))
    return dict(python=platform.python_version(), numpy=np.__version__, seed=429, nodes=nodes,
                relations=relations, edges=edges, unique_triples=len(keys), lookups=lookups, repeats=repeats,
                python_set_retained_bytes=set_bytes, mapped_array_payload_bytes=keys.nbytes,
                python_set_startup_seconds=set_startup, mapped_index_startup_seconds=mapped_startup,
                python_set_startup_tracemalloc_peak_bytes=set_peak,
                mapped_index_startup_tracemalloc_peak_bytes=mapped_peak, offline_key_build_seconds=build_seconds,
                results=rows, scope="Membership component only; excludes snapshot/engine load, authentication, page cache and RSS. Tracemalloc peaks are not RSS. Set retained bytes and mapped file payload are different memory categories.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=2000)
    parser.add_argument("--relations", type=int, default=16)
    parser.add_argument("--edges", type=int, default=10000)
    parser.add_argument("--lookups", type=int, default=1000)
    args = parser.parse_args()
    if min(vars(args).values()) < 1:
        parser.error("all counts must be positive")
    print(json.dumps(measure(**vars(args)), indent=2))
