"""Small reproducible directory-byte/lookup comparison; not an RSS benchmark.

Run: uv run python -m benchmarks.sparse_adjacency
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time

import numpy as np

from gestaltdb.sampling.adjacency import build_csr, build_sparse_csr


def measure(nodes=2000, relations=128, edges=5000, lookups=5000, repeats=5):
    rng = np.random.default_rng(427)
    keys = rng.integers(0, nodes, edges, dtype=np.int64) * relations + rng.integers(0, relations, edges, dtype=np.int64)
    indices = np.arange(edges, dtype=np.int64)
    queries = rng.choice(keys, lookups) if edges else np.zeros(lookups, dtype=np.int64)
    # Half the queries are uniform-space draws (usually absent on this graph).
    queries[::2] = rng.integers(0, nodes * relations, len(queries[::2]))
    rows = []
    reference = None
    for label, build in (("dense", build_csr), ("sparse", build_sparse_csr)):
        start = time.perf_counter()
        adjacency = build(keys, indices, nodes * relations)
        build_seconds = time.perf_counter() - start
        values = [adjacency.edge_range(int(key)).tolist() for key in queries]
        if reference is None:
            reference = values
        else:
            assert reference == values, "candidate ordering differs"
        durations = []
        checksum = 0
        for _ in range(repeats):
            start = time.perf_counter()
            checksum = sum(adjacency.edge_range(int(key)).size for key in queries)
            durations.append(time.perf_counter() - start)
        sample_durations = []
        for _ in range(repeats):
            draws = np.random.default_rng(55)
            start = time.perf_counter()
            for key in queries:
                candidates = adjacency.edge_range(int(key))
                if candidates.size:
                    draws.choice(candidates, min(2, candidates.size), replace=False)
            sample_durations.append(time.perf_counter() - start)
        rows.append(dict(encoding=label, directory_bytes=adjacency.indptr.nbytes + (
            0 if adjacency.row_keys is None else adjacency.row_keys.nbytes),
            edge_index_bytes=adjacency.edge_indices.nbytes, build_seconds=build_seconds,
            median_lookup_seconds=statistics.median(durations), candidate_count=checksum,
            median_lookup_plus_fanout_seconds=statistics.median(sample_durations)))
    return dict(python=platform.python_version(), numpy=np.__version__, seed=427, nodes=nodes,
                relations=relations, edges=edges, lookups=lookups, repeats=repeats, results=rows,
                scope="In-memory adjacency microbenchmark; excludes snapshot loading, validation, RSS and engine positive sets.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=2000)
    parser.add_argument("--relations", type=int, default=128)
    parser.add_argument("--edges", type=int, default=5000)
    parser.add_argument("--lookups", type=int, default=5000)
    args = parser.parse_args()
    if args.nodes < 1 or args.relations < 1 or args.edges < 0 or args.lookups < 1:
        parser.error("nodes, relations and lookups must be positive; edges non-negative")
    print(json.dumps(measure(**vars(args)), indent=2))
