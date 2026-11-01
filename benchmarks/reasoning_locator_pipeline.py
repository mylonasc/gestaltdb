"""Actual authenticated historical locator comparisons for #194.

No benchmark-only decoder, automatic adoption, or OS-cold-cache claim.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time

from .reasoning_frontier_pipeline import call_worker, checkpoint, digest, entity


METHODS = ("ordinary", "native-full-record", "native-authenticated-locator")
SELECTIONS = ("all", "predicate", "subject")
TIMES = (10, 30, 50, 70, 90)


def selected(index, selection):
    return selection == "all" or (index % 4 == 0 and (selection != "subject" or index == 0))


def fixture(path, rows, history_rows=None):
    """Build history and independent expected winner IDs from write receipts."""
    from gestaltdb.epistemic import Claim
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.versioning import ClaimVersionWrite

    history_rows = rows if history_rows is None else history_rows
    if not 1 <= history_rows <= rows:
        raise ValueError("history_rows must be between 1 and rows")
    begin = time.perf_counter()
    graph = GraphDB.create(path, backend="pyrex", serializer="json")
    expected = {str(t): {s: [] for s in SELECTIONS} for t in TIMES}
    versions = 0
    try:
        for start in range(0, rows, 1000):
            claims = [Claim(entity(i), "edge" if i % 4 == 0 else "other", f"target{i}",
                            "positive", "benchmark", provenance={"payload": "p" * (32 + i % 257)})
                      for i in range(start, min(start + 1000, rows))]
            assertions = graph.temporal.commit_versions([
                ClaimVersionWrite.assertion(c, (0, 100)) for c in claims]).versions
            corrections = {}
            pending = [ClaimVersionWrite.correction(replace(c, confidence=.8),
                       supersedes_version_id=v.version_id, valid=(20, 80))
                       for i, (c, v) in enumerate(zip(claims, assertions), start)
                       if i < history_rows and i % 5 == 0]
            if pending:
                corrections = {v.logical_id: v for v in graph.temporal.commit_versions(pending).versions}
            pending = [ClaimVersionWrite.retraction(v.logical_id, valid=(40, 60),
                       supersedes_version_id=corrections.get(v.logical_id, v).version_id)
                       for i, v in enumerate(assertions, start) if i < history_rows and i % 7 == 0]
            if pending:
                graph.temporal.commit_versions(pending)
            versions += len(assertions) + len(corrections) + len(pending)
            for i, v in enumerate(assertions, start):
                for t in TIMES:
                    if i < history_rows and i % 7 == 0 and 40 <= t < 60:
                        continue
                    winner = corrections.get(v.logical_id, v) if 20 <= t < 80 else v
                    record = (v.claim.subject, v.claim.predicate, v.claim.object, winner.version_id)
                    for selection in SELECTIONS:
                        if selected(i, selection):
                            expected[str(t)][selection].append(record)
        return {"status": "ok", "rows": rows, "history_rows": history_rows, "versions": versions,
                "load_s": time.perf_counter() - begin,
                "expected": {t: {s: {"digest": digest(items), "rows": len(items)}
                                    for s, items in groups.items()} for t, groups in expected.items()}}
    finally:
        graph.close()


def worker(path, rows, method, selection, valid_time, cache):
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
    from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, ClaimSourceLimits
    from gestaltdb.reasoning.adapters.native_claims import NativeClaimLimits, PinnedNativeClaimSource
    from gestaltdb.reasoning.adapters.native_locator_claims import NativeLocatorLimits, PinnedNativeLocatorClaimSource
    from gestaltdb.reasoning.extraction import ExtractionRequest

    if method not in METHODS or selection not in SELECTIONS or valid_time not in TIMES or cache not in ("first", "warm"):
        raise ValueError("unknown benchmark configuration")
    manifest = json.loads((Path(path).parent / "fixture.json").read_text())
    if manifest["rows"] != rows:
        raise ValueError("fixture size mismatch")
    checkpoint("reopen")
    begin = time.perf_counter()
    graph = GraphDB.open(path)
    reopen_s = time.perf_counter() - begin
    request = ExtractionRequest("default", max_rows=rows + 1,
                                predicates=None if selection == "all" else ("edge",),
                                subject=entity(0) if selection == "subject" else None)
    source_type = {"ordinary": PinnedBatchedClaimSource, "native-full-record": PinnedNativeClaimSource,
                   "native-authenticated-locator": PinnedNativeLocatorClaimSource}[method]
    options = {}
    if method != "ordinary":
        limits_type = NativeLocatorLimits if method == "native-authenticated-locator" else NativeClaimLimits
        options["native_limits"] = limits_type(max_raw_rows=manifest["versions"] * 5 + 1,
                                               max_logical_claims=rows + 1)
        if method == "native-authenticated-locator":
            options["native_limits"] = replace(options["native_limits"],
                                                max_authenticated_records=manifest["versions"] + 1)

    def measure():
        metrics = {}
        checkpoint("read-view")
        begin = time.perf_counter()
        pipeline_begin = begin
        with graph.temporal.open_read_view(graph, manifest=graph.manifest,
                database_id=graph.database_id, valid_time=valid_time) as view:
            metrics["view_open_s"] = time.perf_counter() - begin
            checkpoint("authenticated-extraction")
            begin = time.perf_counter()
            with source_type(view, request=request, ontology_revision="locator-benchmark-v1",
                    selection=ClaimSelection(), mapping=ClaimMapping(), limits=ClaimSourceLimits(rows + 1),
                    **options) as source:
                evidence = [e for batch in source.iter_batches() for e in batch.evidence]
                profile = asdict(source.profile)
                native_profile = asdict(source.native_profile) if method != "ordinary" else None
            metrics["source_open_extract_close_verify_s"] = time.perf_counter() - begin
            checkpoint("post-verification")
            begin = time.perf_counter()
            view.provenance.verify_source(graph)
            metrics["post_verify_s"] = time.perf_counter() - begin
        metrics["authenticated_pipeline_s"] = time.perf_counter() - pipeline_begin
        return metrics, evidence, profile, native_profile

    try:
        if cache == "warm":
            measure()
        metrics, evidence, profile, native_profile = measure()
        metrics["reopen_s"] = reopen_s
        metrics["total_pipeline_s"] = reopen_s + metrics["authenticated_pipeline_s"]
        checkpoint("parity-validation")
        evidence_hash = digest((e.fact.subject, e.fact.predicate, e.fact.object, e.reference_id) for e in evidence)
        expected = manifest["expected"][str(valid_time)][selection]
        if evidence_hash != expected["digest"] or len(evidence) != expected["rows"]:
            raise ValueError("exact historical fact/version evidence parity failed")
        return {"status": "ok", "metrics": metrics, "evidence_digest": evidence_hash,
                "evidence_rows": len(evidence), "profile": profile, "native_profile": native_profile,
                "source_contract": method, "valid_time": valid_time, "selection": selection,
                "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                                  (1 if sys.platform == "darwin" else 1024),
                "rss_boundary": "whole extraction worker including warmup/parity; fixture load in separate worker",
                "cache_boundary": "reopened handle; OS cache uncontrolled, not OS-cold; warm primes same method",
                "scope": "extraction and authentication; no inference/support/publication measurement"}
    finally:
        graph.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--history-rows", type=int,
                        help="apply corrections/retractions only to this many initial assertions; default all")
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--selections", nargs="+", choices=SELECTIONS, default=list(SELECTIONS))
    parser.add_argument("--times", nargs="+", type=int, choices=TIMES, default=[50])
    parser.add_argument("--caches", nargs="+", choices=("first", "warm"), default=["first", "warm"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--worker", choices=("load", *METHODS))
    args = parser.parse_args()
    if not 1 <= args.rows <= 1_000_000 or args.repeats < 1 or not 0 < args.timeout < math.inf:
        parser.error("positive finite timeout/repeats and rows <=1000000 required")
    if args.history_rows is not None and not 1 <= args.history_rows <= args.rows:
        parser.error("history-rows must be between 1 and rows")
    if args.worker:
        if args.fixture is None:
            parser.error("worker requires --fixture")
        result = fixture(args.fixture, args.rows, args.history_rows) if args.worker == "load" else worker(
            args.fixture, args.rows, args.worker, args.selections[0], args.times[0], args.caches[0])
        print(json.dumps(result))
        return
    root = Path(__file__).resolve().parents[1]
    report = {"schema_version": 1, "configuration": {k: v for k, v in vars(args).items()
               if k not in ("output", "fixture", "worker")}, "samples": [], "complete": False,
              "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "shared_harness_sha256": hashlib.sha256(
                  Path(__file__).with_name("reasoning_frontier_pipeline.py").read_bytes()).hexdigest(),
              "python": sys.version, "platform": platform.platform(), "dependencies": {},
              "decision": "pending measurement review; no automatic native adoption",
              "os_cache": "uncontrolled; process restart is not OS-cold"}
    for package in ("gestaltdb", "pyrex-rocksdb", "pyarrow"):
        try:
            report["dependencies"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["dependencies"][package] = None

    def save():
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")

    with tempfile.TemporaryDirectory(prefix="re194-locator-",
            dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None) as directory:
        path = Path(directory) / "db"
        common = [sys.executable, "-m", "benchmarks.reasoning_locator_pipeline",
                  "--rows", str(args.rows), "--fixture", str(path)]
        if args.history_rows is not None:
            common += ["--history-rows", str(args.history_rows)]
        loaded = call_worker([*common, "--worker", "load"], args.timeout)
        report["fixture"] = loaded
        save()
        if loaded["status"] != "ok":
            raise SystemExit("fixture failed; diagnostics saved")
        (path.parent / "fixture.json").write_text(json.dumps(loaded))
        for repeat in range(args.repeats):
            for t in args.times:
                for selection in args.selections:
                    for cache in args.caches:
                        for method in args.methods:
                            row = call_worker([*common, "--worker", method, "--selections", selection,
                                               "--times", str(t), "--caches", cache], args.timeout)
                            row.update(repeat=repeat, method=method, selection=selection, valid_time=t, cache=cache)
                            report["samples"].append(row)
                            save()
                            if row["status"] != "ok":
                                raise SystemExit("worker failed; incomplete report retained without quantiles")
        report["complete"] = report["all_parity_verified"] = True
        groups = {}
        for row in report["samples"]:
            key = f'{row["method"]}/{row["selection"]}/{row["valid_time"]}/{row["cache"]}'
            groups.setdefault(key, []).append(row)
        report["summary"] = {key: {metric: {"p50": statistics.median(values),
            "p95": values[math.ceil(.95 * len(values)) - 1]} for metric in (*group[0]["metrics"], "rss_peak_bytes")
            for values in [sorted(r["rss_peak_bytes"] if metric == "rss_peak_bytes" else r["metrics"][metric]
                                  for r in group)]} for key, group in groups.items()}
        save()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
