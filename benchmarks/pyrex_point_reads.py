"""Warmed PyRex native, adapter and decoded reads; run in fresh processes.

Example: python -m benchmarks.pyrex_point_reads --cpu 2 > reads.json
Compare published wheels in isolated environments, alternating version order.
This is not a cold-cache, concurrent-throughput or query benchmark.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import statistics
import tempfile
import time

from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.kvstores import PyRexStore
from gestaltdb.serializers import JSONSerializer


def measure(*, nodes=5000, reads=40000, warmup=1000, trials=7, cpu=None):
    """Return raw samples and summaries; verify all read layers before timing."""
    if nodes < 1 or reads < 1 or warmup < 0 or trials < 1:
        raise ValueError("nodes, reads and trials must be positive; warmup non-negative")
    original_affinity = None
    if cpu is not None:
        original_affinity = os.sched_getaffinity(0)
        os.sched_setaffinity(0, {cpu})
    try:
        return _measure(nodes, reads, warmup, trials)
    finally:
        if original_affinity is not None:
            os.sched_setaffinity(0, original_affinity)


def _measure(nodes, reads, warmup, trials):
    import pyrex

    report = {
        "pyrex_version": importlib.metadata.version("pyrex-rocksdb"),
        "pyrex_module": pyrex.__file__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "nodes": nodes, "reads": reads, "warmup": warmup, "trials": trials,
        "serializer": "json", "payload_characters": 100,
        "scope": "single-thread warmed hits; no flush or cold-cache claim",
        "results": {},
    }
    with tempfile.TemporaryDirectory(prefix="gestaltdb-point-reads-") as directory:
        for transactional in (False, True):
            mode = "transactional" if transactional else "default"
            store = PyRexStore(path=f"{directory}/{mode}", transactional=transactional)
            graph = GraphDB(store, JSONSerializer())
            try:
                keys = [f"n{i:015d}".encode() for i in range(nodes)]
                graph.put_nodes([
                    Node(key.decode(), labels=["Entity"], properties={"payload": "x" * 100})
                    for key in keys
                ])
                # Intentional white-box isolation of the identical stored node payload.
                raw_keys = [store._key(b"N", key) for key in keys]
                operations = {
                    "native_get": (store.db.get, raw_keys),
                    "store_get_node": (store.get_node, keys),
                    "graph_get_node": (graph.get_node, keys),
                }
                for key, raw_key in zip(keys, raw_keys):
                    assert store.db.get(raw_key) == store.get_node(key)
                    assert graph.get_node(key).properties == {"payload": "x" * 100}
                samples = {name: [] for name in operations}
                names = list(operations)
                for trial in range(trials):
                    # Rotate ordering so one layer is not always measured first.
                    for name in names[trial % len(names):] + names[:trial % len(names)]:
                        operation, operation_keys = operations[name]
                        for index in range(warmup):
                            operation(operation_keys[index % nodes])
                        started = time.perf_counter_ns()
                        for index in range(reads):
                            operation(operation_keys[index % nodes])
                        samples[name].append((time.perf_counter_ns() - started) / reads / 1000)
                report["results"][mode] = {
                    name: {
                        "samples_us": values,
                        "median_us": statistics.median(values),
                        "min_us": min(values), "max_us": max(values),
                    }
                    for name, values in samples.items()
                }
            finally:
                graph.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=5000)
    parser.add_argument("--reads", type=int, default=40000)
    parser.add_argument("--warmup", type=int, default=1000)
    parser.add_argument("--trials", type=int, default=7)
    parser.add_argument("--cpu", type=int)
    args = parser.parse_args()
    print(json.dumps(measure(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
