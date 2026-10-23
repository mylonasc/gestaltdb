"""Isolated #194 measurement matrix; native KV experiments are NOT claims adapters."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import itertools
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

from .reasoning_pipeline import run_once, run_worker


def quantiles(samples):
    """Never aggregate failed/partial or differently configured measurements."""
    if not samples or any(row.get("status") != "ok" for row in samples):
        return {}
    names = set(samples[0]["metrics"])
    if any(set(row["metrics"]) != names for row in samples):
        raise ValueError("incompatible sample metrics")
    summary = {}
    for name in sorted(names):
        values = sorted(row["metrics"][name] for row in samples)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("invalid timing")
        summary[name] = {"p50": statistics.median(values),
                         "p95": values[math.ceil(.95 * len(values)) - 1], "n": len(values)}
    return summary


def run_native(rows, batch_rows, selection, payload, warmup, progress_path=None):
    """Synthetic immutable JSON KV rows: iterator vs copied Arrow/decoded handoff.

    This deliberately does not pretend that a raw snapshot authenticates claims,
    or that iter_decoded_evidence's post-decode filters are storage pushdown.
    """
    import pyrex
    from gestaltdb.reasoning.adapters.native import (
        DecodedRow, NativeKeyScan, iter_decoded_evidence, iter_locator_keys,
    )
    from .reasoning_extraction import _peak_rss

    metrics = {}

    def note(phase, seconds):
        metrics[phase] = seconds
        if progress_path is not None:
            with progress_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"phase": phase, "s": seconds}) + "\n")

    with tempfile.TemporaryDirectory(prefix="reasoning-native-") as directory:
        db = pyrex.PyRocksDB(str(Path(directory) / "db"))
        snapshot = None
        try:
            begin = time.perf_counter()
            for index in range(rows):
                predicate = "memberOf" if index % 4 == 0 else "other"
                size = 32 if payload == "fixed" else 32 + (index * 131) % 4065
                record = [f"n{index}", predicate, f"g{index % 8}", f"v{index}", f"e{index}", "x" * size]
                db.put(f"row:{index:09d}".encode(), json.dumps(record).encode())
            note("load_s", time.perf_counter() - begin)
            snapshot = db.create_snapshot()

            def decoded(key, value):
                record = json.loads(value)
                return DecodedRow(*record[:5])

            def selected(row):
                return ((selection == "all" or row.predicate == "memberOf")
                        and (selection != "subject" or row.subject == "n0"))

            def consume():
                with NativeKeyScan(db, snapshot=snapshot, prefix=b"row:",
                                   batch_rows=batch_rows, max_rows=rows) as scan:
                    return [row for batch in iter_decoded_evidence(
                        scan, decoded, predicate=None if selection == "all" else "memberOf",
                        bindings={"subject": "n0"} if selection == "subject" else None,
                        visibility="synthetic-snapshot-only-not-claim-provenance", max_rows=rows)
                            for row in batch.rows]

            if warmup:
                begin = time.perf_counter()
                consume()
                note("warmup_s", time.perf_counter() - begin)
            begin = time.perf_counter()
            actual = consume()
            note("native_copy_decode_filter_s", time.perf_counter() - begin)
            # No writes occur after snapshot creation. Reference iteration runs
            # after the measured native path so it cannot prime that path.
            begin = time.perf_counter()
            iterator = db.new_iterator()
            expected, raw_bytes, count = [], 0, 0
            iterator.seek(b"row:")
            while iterator.valid() and iterator.key().startswith(b"row:"):
                key, value = iterator.key(), iterator.value()
                count += 1
                raw_bytes += len(key) + len(value)
                row = decoded(key, value)
                if selected(row):
                    expected.append(row)
                iterator.next()
            iterator.check_status()
            del iterator
            note("python_copy_decode_filter_s", time.perf_counter() - begin)
            if actual != expected or count != rows or not snapshot.is_valid():
                raise ValueError("native exact-row parity/snapshot check failed")
            begin = time.perf_counter()
            with NativeKeyScan(db, snapshot=snapshot, prefix=b"row:",
                               batch_rows=batch_rows, max_rows=rows) as scan:
                locators = sum(1 for _ in iter_locator_keys(scan, max_rows=rows))
            note("native_locator_s", time.perf_counter() - begin)
            if locators != rows:
                raise ValueError("native locator cardinality mismatch")
            return {"status": "ok", "metrics": metrics, "accepted": len(actual), "scanned": rows,
                    "evidence_digest": hashlib.sha256(repr(actual).encode()).hexdigest(),
                    "raw_key_value_bytes": raw_bytes, "rss_peak_bytes": _peak_rss(),
                    "mode": "synthetic KV only; no authenticated claims or inference",
                    "pushdown": "row prefix only; predicate/subject are post-decode filters",
                    "cache_state": "primed native scan" if warmup else "first native scan after load",
                    "locator_note": "copies values too; measured after decoded/reference scans, warm"}
        finally:
            if snapshot is not None:
                snapshot.release()
            db.close()


def worker(case, batch_rows, progress_path=None):
    if progress_path is not None:
        progress_path.write_text(json.dumps({"event": "worker_started", "case": case}) + "\n")
    modules = {"lmdb": "lmdb", "leveldb": "plyvel", "pyrex": "pyrex", "pyrex-native": "pyrex"}
    # Missing optional extras are explicit unavailability, never a fallback.
    try:
        __import__(modules[case["backend"]])
        if case["backend"] == "pyrex-native":
            __import__("pyarrow")
            from gestaltdb.reasoning.adapters.native import _at_least, MIN_NATIVE_SCAN_VERSION
            if not _at_least(version("pyrex-rocksdb"), MIN_NATIVE_SCAN_VERSION):
                return {"status": "unavailable", "reason": "native runtime below version gate"}
    except (ImportError, PackageNotFoundError):
        return {"status": "unavailable", "reason": "required optional runtime is not installed"}
    options = {"selection": case["selection"], "payload": case["payload"],
               "warmup": case["cache"] == "primed"}
    if case["backend"] == "pyrex-native":
        return run_native(case["rows"], batch_rows, progress_path=progress_path, **options)
    return run_once(case["rows"], case["backend"], batch_rows, progress_path=progress_path, **options)


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".pending")
    staging.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    staging.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--backends", nargs="+", choices=("lmdb", "leveldb", "pyrex", "pyrex-native"), default=["lmdb", "pyrex"])
    parser.add_argument("--selections", nargs="+", choices=("all", "predicate", "subject"), default=["all"])
    parser.add_argument("--payloads", nargs="+", choices=("fixed", "variable"), default=["fixed"])
    parser.add_argument("--caches", nargs="+", choices=("first", "primed"), default=["first"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--batch-rows", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-case", help=argparse.SUPPRESS)
    parser.add_argument("--progress", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (any(not 1 <= size <= 1_000_000 for size in args.sizes) or args.repeats < 1
            or not 1 <= args.batch_rows <= 100_000 or not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error("invalid size, repeats, batch rows or timeout")
    if args.worker_case:
        print(json.dumps(worker(json.loads(args.worker_case), args.batch_rows, args.progress)))
        return
    if args.output is None:
        parser.error("--output is required")
    root = Path(__file__).resolve().parents[1]
    packages = {}
    for package in ("lmdb", "plyvel", "pyrex-rocksdb", "pyarrow", "numpy"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    source_hashes = {name: hashlib.sha256((root / "benchmarks" / name).read_bytes()).hexdigest()
                     for name in ("reasoning_matrix.py", "reasoning_pipeline.py")}
    report = {"schema_version": 1, "complete": False, "cases": [],
              "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
              "python": sys.version, "platform": platform.platform(), "packages": packages,
              "source_sha256": source_hashes, "batch_rows": args.batch_rows, "repeats": args.repeats,
              "timeout_s": args.timeout, "cache_note": "OS cache uncontrolled; no privileged cache eviction",
              "rss_note": "whole worker high-water mark, including load and reference verification; not engine-only"}
    write_report(args.output, report)
    failed = False
    for values in itertools.product(args.sizes, args.backends, args.selections, args.payloads, args.caches):
        case = dict(zip(("rows", "backend", "selection", "payload", "cache"), values))
        entry = {"configuration": case, "samples": [], "summary": {}}
        report["cases"].append(entry)
        for repeat in range(args.repeats):
            command = [sys.executable, "-m", "benchmarks.reasoning_matrix", "--worker-case",
                       json.dumps(case), "--batch-rows", str(args.batch_rows)]
            with tempfile.TemporaryDirectory(prefix="reasoning-progress-") as directory:
                progress = Path(directory) / "phases.jsonl"
                sample = run_worker(command + ["--progress", str(progress)], args.timeout)
                if sample["status"] not in ("ok", "unavailable") and progress.exists():
                    sample["progress"] = progress.read_text()[-4000:]
            entry["samples"].append(sample)
            print(json.dumps({"case": case, "repeat": repeat + 1, "status": sample["status"]}), flush=True)
            write_report(args.output, report)
            if sample["status"] != "ok":
                failed = failed or sample["status"] != "unavailable"
                break
        entry["summary"] = quantiles(entry["samples"])
        write_report(args.output, report)
    report["all_available"] = all(entry["samples"][0]["status"] != "unavailable" for entry in report["cases"])
    report["complete"] = not failed and report["all_available"]
    write_report(args.output, report)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
