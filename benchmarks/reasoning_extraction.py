"""RE-05 batched extraction measurements; extraction only, no inference claims.

Run ``python -m benchmarks.reasoning_extraction --output <path>``. Compares the
existing single-pass claim iterator with the bounded batched source on LMDB and
LevelDB, recording rows/s, batch counts, verification counts and peak RSS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time


def _peak_rss():
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def run_case(backend, rows, predicates, batch_rows, temp_root):
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
    from gestaltdb.reasoning.adapters.claims import (
        ClaimMapping,
        ClaimSelection,
        PinnedClaimSource,
    )
    from gestaltdb.reasoning.extraction import ExtractionRequest

    metrics: dict[str, float] = {}
    start = time.perf_counter()
    report: dict = {"backend": backend, "rows": rows, "predicates": predicates,
                    "batch_rows": batch_rows}
    with tempfile.TemporaryDirectory(prefix="re05-", dir=temp_root) as directory:
        options = {"map_size": 512 * 1024 * 1024} if backend == "lmdb" else {}
        graph = GraphDB.create(Path(directory) / "db", backend=backend, serializer="json",
                               backend_options=options)
        try:
            begin = time.perf_counter()
            for index in range(rows):
                graph.epistemic.assert_claim(subject=f"n{index}", predicate=f"p{index % predicates}",
                                   object=f"m{index}", agent="registry",
                                   polarity="positive", valid=(0, 100))
            metrics["load_s"] = time.perf_counter() - begin
            with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
                begin = time.perf_counter()
                with PinnedClaimSource(view, world="default", ontology_revision="rev-1",
                                       selection=ClaimSelection(), mapping=ClaimMapping()) as source:
                    single = list(source.iter_evidence())
                metrics["single_pass_s"] = time.perf_counter() - begin
                begin = time.perf_counter()
                request = ExtractionRequest(world="default", batch_rows=batch_rows,
                                            max_rows=rows + 1)
                with PinnedBatchedClaimSource(view, request=request, ontology_revision="rev-1",
                                              selection=ClaimSelection(),
                                              mapping=ClaimMapping()) as source:
                    batches = list(source.iter_batches())
                    profile = source.profile
                metrics["batched_s"] = time.perf_counter() - begin
                assert sum(len(b.evidence) for b in batches) == len(single) == rows
                assert sorted((e for b in batches for e in b.evidence), key=lambda e: e.fact) == sorted(single, key=lambda e: e.fact)
                report["batches"] = profile.batches
                report["verifications"] = profile.verifications
                report["pushdown"] = profile.pushdown
                report["capability"] = {
                    "backend": source.capability.backend,
                    "snapshot_kind": source.capability.snapshot_kind,
                    "pinned_snapshot": source.capability.pinned_snapshot,
                    "native_arrow_scan": source.capability.native_arrow_scan,
                }
                report["limit_hit"] = None
                try:
                    ove = ExtractionRequest(world="default", batch_rows=batch_rows, max_rows=rows - 1)
                    with PinnedBatchedClaimSource(view, request=ove, ontology_revision="rev-1",
                                                  selection=ClaimSelection(),
                                                  mapping=ClaimMapping()) as source:
                        list(source.iter_batches())
                except Exception as exc:  # noqa: BLE001 - record the bound, whatever its type
                    report["limit_hit"] = type(exc).__name__
                if report["limit_hit"] not in ("ReasoningLimitError",):
                    raise ValueError(f"row budget did not fail closed: {report['limit_hit']}")
        finally:
            graph.close()
    metrics["pipeline_s"] = time.perf_counter() - start
    report["status"] = "ok"
    report["metrics"] = metrics
    report["rows_per_s_single"] = rows / metrics["single_pass_s"] if metrics["single_pass_s"] > 0 else 0
    report["rows_per_s_batched"] = rows / metrics["batched_s"] if metrics["batched_s"] > 0 else 0
    report["rss_peak_bytes"] = _peak_rss()
    return report


def run_native_case(rows, batch_rows, temp_root):
    """Raw Python-iterator vs native Arrow scan on one throwaway PyRex database."""
    pyrex = __import__("pyrex")
    from gestaltdb.reasoning.adapters.native import NativeKeyScan

    report: dict = {"backend": "pyrex-native", "rows": rows, "batch_rows": batch_rows}
    metrics: dict[str, float] = {}
    with tempfile.TemporaryDirectory(prefix="re05-native-", dir=temp_root) as directory:
        db = pyrex.PyRocksDB(str(Path(directory) / "db"))
        try:
            keys = [f"n:{index:07d}".encode() for index in range(rows)]
            values = [f"v{index}".encode() for index in range(rows)]
            begin = time.perf_counter()
            for key, value in zip(keys, values):
                db.put(key, value)
            metrics["load_s"] = time.perf_counter() - begin
            begin = time.perf_counter()
            iterator = db.new_iterator()
            iterator.seek(b"")
            collected = 0
            while iterator.valid():
                collected += 1
                _ = iterator.key(), iterator.value()
                iterator.next()
            iterator.check_status()
            metrics["python_iter_s"] = time.perf_counter() - begin
            assert collected == rows
            begin = time.perf_counter()
            batches = list(NativeKeyScan(db, batch_rows=batch_rows, max_rows=rows + 1))
            metrics["native_scan_s"] = time.perf_counter() - begin
            assert sum(len(b.rows) for b in batches) == rows
            assert batches[-1].complete
            report["batches"] = len(batches)
            report["status"] = "ok"
        finally:
            db.close()
    metrics["pipeline_s"] = metrics["load_s"] + metrics["python_iter_s"] + metrics["native_scan_s"]
    report["metrics"] = metrics
    report["rows_per_s_python"] = rows / metrics["python_iter_s"] if metrics["python_iter_s"] > 0 else 0
    report["rows_per_s_native"] = rows / metrics["native_scan_s"] if metrics["native_scan_s"] > 0 else 0
    report["rss_peak_bytes"] = _peak_rss()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--predicates", type=int, default=4)
    parser.add_argument("--batch-rows", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--temp-root", type=Path, default=Path("/tmp/opencode"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.rows <= 100_000 or not 1 <= args.predicates <= 100:
        parser.error("rows must be 1..100000; predicates 1..100")
    if not 1 <= args.batch_rows <= 100_000 or args.repeats < 1:
        parser.error("batch_rows must be 1..100000; repeats positive")
    if not args.temp_root.is_dir():
        parser.error("temp root must exist")
    root = Path(__file__).resolve().parents[1]
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    report: dict = {"schema_version": 1, "python": sys.version, "platform": platform.platform(),
                    "git_revision": revision, "working_tree_dirty": dirty, "complete": False, "rows": [],
                    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    backends = []
    for backend, module in (("lmdb", "lmdb"), ("leveldb", "plyvel")):
        try:
            __import__(module)
            backends.append(backend)
        except ImportError:
            report["rows"].append({"backend": backend, "status": "unavailable"})
    env_threads = {"OMP_NUM_THREADS": "1", "POLARS_MAX_THREADS": "1"}
    for backend in backends:
        for _ in range(args.repeats):
            try:
                row = run_case(backend, args.rows, args.predicates, args.batch_rows,
                               str(args.temp_root))
            except Exception as exc:  # noqa: BLE001 - benchmark must record, not raise
                row = {"backend": backend, "status": "error",
                       "reason": f"{type(exc).__name__}: {exc}"}
            report["rows"].append({**row, "threads": env_threads})
            if args.output is not None:
                args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for _ in range(args.repeats):
        try:
            pyarrow_spec = __import__("importlib.util", fromlist=["find_spec"]).find_spec("pyarrow")
            pyrex_spec = __import__("importlib.util", fromlist=["find_spec"]).find_spec("pyrex")
            if pyarrow_spec is None or pyrex_spec is None:
                raise ImportError("native scan benchmark requires pyrex and pyarrow")
            row = run_native_case(args.rows, args.batch_rows, str(args.temp_root))
        except ImportError as exc:
            row = {"backend": "pyrex-native", "status": "unavailable", "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 - benchmark must record, not raise
            row = {"backend": "pyrex-native", "status": "error",
                   "reason": f"{type(exc).__name__}: {exc}"}
        report["rows"].append({**row, "threads": env_threads})
        if args.output is not None:
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report["complete"] = True
    if args.output is not None:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    native = [r for r in report["rows"] if r.get("backend") == "pyrex-native"]
    passed = all(r.get("status") == "ok" for r in report["rows"] if r.get("backend") in backends)
    passed = passed and all(r.get("status") in ("ok", "unavailable") for r in native)
    print(json.dumps({"passed": passed, "rows": len(report["rows"]),
                      "output": None if args.output is None else str(args.output)}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
