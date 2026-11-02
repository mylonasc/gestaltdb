"""Opt-in Python allocation diagnostic for temporal commit-cache reuse.

Measures tracemalloc around cold (decode) vs warm (reuse, no decode) reads
of the same commits. Native buffers and total RSS are excluded; this is not
an RSS guarantee or a decoded-heap bound.
"""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc


def main():
    sys.path.insert(0, str(Path.cwd()))
    from gestaltdb._graph.entities import Node
    from gestaltdb.graphdb import GraphDB

    with tempfile.TemporaryDirectory(dir="/tmp/opencode", prefix="hsa-cache-alloc-") as root:
        graph = GraphDB.create(Path(root) / "db", backend="pyrex", serializer="json")
        try:
            commits = 200
            for i in range(commits):
                graph.temporal.put_node_version(Node(f"n{i}", properties={"payload": "x" * 128}), valid=(0, None))
            # Cold: clear and decode everything.
            graph.temporal.invalidate_commit_cache()
            tracemalloc.start()
            try:
                for commit_id in range(1, commits + 1):
                    assert graph.temporal.get_commit_cached(commit_id) is not None
                _, cold_peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            # Warm: same commits, decodes must be reused.
            from gestaltdb._graph import temporal_reads as _reads
            calls = []
            original = _reads.TemporalReader.decode_version

            def counting(reader, payload):
                calls.append(payload)
                return original(reader, payload)

            _reads.TemporalReader.decode_version = counting
            try:
                tracemalloc.start()
                try:
                    for commit_id in range(1, commits + 1):
                        assert graph.temporal.get_commit_cached(commit_id) is not None
                    _, warm_peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
            finally:
                _reads.TemporalReader.decode_version = original
            print(json.dumps({
                "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "reader_sha256": hashlib.sha256(Path(
                    "src/gestaltdb/_graph/temporal_reads.py").read_bytes()).hexdigest(),
                "commits": commits,
                "cold_peak_python_bytes": cold_peak,
                "warm_peak_python_bytes": warm_peak,
                "warm_decode_calls": len(calls),
                "cache_entries": len(graph.temporal._commit_cache),
                "cache_bytes": _reads._commit_cache_bytes(graph.temporal._commit_cache),
                "boundary": "tracemalloc around get_commit_cached only; same process, "
                            "uncontrolled OS caches, native buffers excluded, not RSS or heap guarantee",
            }, indent=2))
        finally:
            graph.close()


if __name__ == "__main__":
    main()
