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
from .reasoning_locator_instrumentation import instrument, stage_delta


METHODS = ("ordinary", "native-full-record", "native-authenticated-locator")
SELECTIONS = ("all", "predicate", "subject")
TIMES = (10, 30, 50, 70, 90)


def selected(index, selection):
    return selection == "all" or (index % 4 == 0 and (selection != "subject" or index == 0))


ATTRIBUTION_COUNTERS = (
    "metadata_reads", "metadata_writes", "index_scan_openings", "index_rows",
    "native_scan_openings", "native_batches", "native_rows", "native_prepare_calls",
    "commit_auth_calls", "decode_calls", "target_lookup_calls",
    "target_lookup_commit_visits", "target_lookup_horizon_sum",
    "distinct_target_version_ids", "validation_target_calls",
    "version_id_exists_calls", "checksum_calls", "checksum_marker_visits",
    "canonical_conformance_calls", "commit_calls", "sequence_reservations",
    "index_write_calls", "index_entries_written", "publication_calls",
    "stale_mark_calls", "commit_cache_entries_peak", "native_selected_records_peak",
)

ATTRIBUTION_TIMINGS = (
    "native_scan_s", "native_prepare_s", "commit_auth_s", "decode_s",
    "target_lookup_s", "validation_target_s", "version_id_exists_s",
    "checksum_s", "canonical_conformance_s", "commit_s", "sequence_reserve_s",
    "index_write_s", "publication_s", "stale_mark_s", "index_scan_s",
)


def _counter_summary(group):
    """Compact p50/p95 attribution for one measured configuration group."""
    totals = [row["instrumentation"]["total"] for row in group
              if row.get("instrumentation")]
    if not totals:
        return {"boundary": "instrumentation disabled"}
    result = {}
    for name in (*ATTRIBUTION_COUNTERS, *ATTRIBUTION_TIMINGS):
        values = sorted(total.get(name, 0) for total in totals)
        if values:
            result[name] = {"p50": statistics.median(values),
                            "p95": values[math.ceil(.95 * len(values)) - 1]}
    result["read_categories"] = {
        name: {"p50": statistics.median(values),
               "p95": values[math.ceil(.95 * len(values)) - 1]}
        for name, values in {
            category: sorted(total.get("metadata_reads_by_category", {}).get(category, 0)
                             for total in totals)
            for category in {category for total in totals
                             for category in total.get("metadata_reads_by_category", {})}
        }.items()
    }
    result["boundary"] = (
        "counts are per extraction stage (exclusive of fixture construction); "
        "timings are inclusive buckets and do not sum to end-to-end time"
    )
    return result



def fixture(path, rows, history_rows=None, commit_batch=1000, instrumented=False):
    """Build history and independent expected winner IDs from write receipts.

    Construction stages are timed and checkpointed separately from extraction.
    ``commit_batch`` controls assertion batch size independently of row count.
    """
    from gestaltdb.epistemic import Claim
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.versioning import ClaimVersionWrite

    history_rows = rows if history_rows is None else history_rows
    if not 1 <= history_rows <= rows:
        raise ValueError("history_rows must be between 1 and rows")
    if isinstance(commit_batch, bool) or not isinstance(commit_batch, int) or commit_batch < 1:
        raise ValueError("commit_batch must be a positive integer")
    # A batch larger than the row count simply produces one assertion commit.
    commit_batch = min(commit_batch, rows)
    begin = time.perf_counter()
    stage = {"create_s": 0.0, "assertions_s": 0.0, "corrections_s": 0.0,
             "retractions_s": 0.0, "oracle_s": 0.0, "close_s": 0.0}
    checkpoint("fixture-create", rows=rows, history_rows=history_rows,
               commit_batch=commit_batch)
    instrument_manager = (instrument(events=lambda event, details:
                                     checkpoint(f"fixture-{event}", **details))
                          if instrumented else None)
    recorder = instrument_manager.__enter__() if instrument_manager is not None else None
    construction_start = recorder.snapshot() if recorder is not None else None
    try:
        created = time.perf_counter()
        graph = GraphDB.create(path, backend="pyrex", serializer="json")
        stage["create_s"] = time.perf_counter() - created
        expected = {str(t): {s: [] for s in SELECTIONS} for t in TIMES}
        versions = 0
        commits = 0
        batches = 0
        result = None
        try:
            for start in range(0, rows, commit_batch):
                batches += 1
                checkpoint("fixture-assertions", batch=batches, start=start,
                           end=min(start + commit_batch, rows))
                claims = [Claim(entity(i), "edge" if i % 4 == 0 else "other", f"target{i}",
                                "positive", "benchmark", provenance={"payload": "p" * (32 + i % 257)})
                          for i in range(start, min(start + commit_batch, rows))]
                assertions_started = time.perf_counter()
                assertions = graph.temporal.commit_versions([
                    ClaimVersionWrite.assertion(c, (0, 100)) for c in claims]).versions
                stage["assertions_s"] += time.perf_counter() - assertions_started
                commits += 1
                corrections = {}
                pending = [ClaimVersionWrite.correction(replace(c, confidence=.8),
                           supersedes_version_id=v.version_id, valid=(20, 80))
                           for i, (c, v) in enumerate(zip(claims, assertions), start)
                           if i < history_rows and i % 5 == 0]
                if pending:
                    checkpoint("fixture-corrections", batch=batches, rows=len(pending))
                    corrections_started = time.perf_counter()
                    corrections = {v.logical_id: v for v in graph.temporal.commit_versions(pending).versions}
                    stage["corrections_s"] += time.perf_counter() - corrections_started
                    commits += 1
                pending = [ClaimVersionWrite.retraction(v.logical_id, valid=(40, 60),
                           supersedes_version_id=corrections.get(v.logical_id, v).version_id)
                           for i, v in enumerate(assertions, start) if i < history_rows and i % 7 == 0]
                if pending:
                    checkpoint("fixture-retractions", batch=batches, rows=len(pending))
                    retractions_started = time.perf_counter()
                    graph.temporal.commit_versions(pending)
                    stage["retractions_s"] += time.perf_counter() - retractions_started
                    commits += 1
                versions += len(assertions) + len(corrections) + len(pending)
                checkpoint("fixture-oracle", batch=batches)
                oracle_started = time.perf_counter()
                for i, v in enumerate(assertions, start):
                    for t in TIMES:
                        if i < history_rows and i % 7 == 0 and 40 <= t < 60:
                            continue
                        winner = corrections.get(v.logical_id, v) if 20 <= t < 80 else v
                        record = (v.claim.subject, v.claim.predicate, v.claim.object, winner.version_id)
                        for selection in SELECTIONS:
                            if selected(i, selection):
                                expected[str(t)][selection].append(record)
                stage["oracle_s"] += time.perf_counter() - oracle_started
            checkpoint("fixture-complete", batches=batches, versions=versions, commits=commits)
            result = {"status": "ok", "rows": rows, "history_rows": history_rows,
                      "commit_batch": commit_batch, "versions": versions, "commits": commits,
                      "expected": {t: {s: {"digest": digest(items), "rows": len(items)}
                                          for s, items in groups.items()} for t, groups in expected.items()}}
            if recorder is not None:
                result["instrumentation"] = stage_delta(recorder.snapshot(), construction_start)
            return result
        finally:
            closed = time.perf_counter()
            graph.close()
            stage["close_s"] = time.perf_counter() - closed
            if result is not None:
                result["construction"] = {
                    **stage, "load_s": time.perf_counter() - begin, "batches": batches,
                    "timing_boundary": "fixture process only; extraction workers are separate",
                }
    finally:
        if instrument_manager is not None:
            instrument_manager.__exit__(None, None, None)


def worker(path, rows, method, selection, valid_time, cache, instrumented=False):
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
        stage_counters = {}
        checkpoint("read-view")
        begin = time.perf_counter()
        pipeline_begin = begin
        instrument_manager = instrument() if instrumented else None
        recorder = instrument_manager.__enter__() if instrument_manager is not None else None
        read_view_start = recorder.snapshot() if recorder is not None else None
        try:
            with graph.temporal.open_read_view(graph, manifest=graph.manifest,
                    database_id=graph.database_id, valid_time=valid_time) as view:
                metrics["view_open_s"] = time.perf_counter() - begin
                if recorder is not None:
                    extraction_start = recorder.snapshot()
                    stage_counters["view_open"] = stage_delta(extraction_start, read_view_start)
                checkpoint("authenticated-extraction")
                begin = time.perf_counter()
                with source_type(view, request=request, ontology_revision="locator-benchmark-v1",
                        selection=ClaimSelection(), mapping=ClaimMapping(), limits=ClaimSourceLimits(rows + 1),
                        **options) as source:
                    evidence = [e for batch in source.iter_batches() for e in batch.evidence]
                    profile = asdict(source.profile)
                    native_profile = asdict(source.native_profile) if method != "ordinary" else None
                metrics["source_open_extract_close_verify_s"] = time.perf_counter() - begin
                if recorder is not None:
                    after_extract = recorder.snapshot()
                    stage_counters["extraction"] = stage_delta(after_extract, extraction_start)
                checkpoint("post-verification")
                begin = time.perf_counter()
                view.provenance.verify_source(graph)
                metrics["post_verify_s"] = time.perf_counter() - begin
            metrics["authenticated_pipeline_s"] = time.perf_counter() - pipeline_begin
            if recorder is not None:
                stage_counters["post_verify"] = stage_delta(recorder.snapshot(), after_extract)
                stage_counters["total"] = stage_delta(recorder.snapshot(), read_view_start)
            return metrics, evidence, profile, native_profile, stage_counters
        finally:
            if instrument_manager is not None:
                instrument_manager.__exit__(None, None, None)

    try:
        if cache == "warm":
            measure()
        metrics, evidence, profile, native_profile, stage_counters = measure()
        metrics["reopen_s"] = reopen_s
        metrics["total_pipeline_s"] = reopen_s + metrics["authenticated_pipeline_s"]
        checkpoint("parity-validation")
        evidence_hash = digest((e.fact.subject, e.fact.predicate, e.fact.object, e.reference_id) for e in evidence)
        expected = manifest["expected"][str(valid_time)][selection]
        if evidence_hash != expected["digest"] or len(evidence) != expected["rows"]:
            raise ValueError("exact historical fact/version evidence parity failed")
        return {"status": "ok", "metrics": metrics, "evidence_digest": evidence_hash,
                "evidence_rows": len(evidence), "profile": profile, "native_profile": native_profile,
                "instrumentation": stage_counters if instrumented else None,
                "evaluator_s": None,
                "evaluator_boundary": "not measured: this harness does not evaluate rules",
                "source_contract": method, "valid_time": valid_time, "selection": selection,
                "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                                  (1 if sys.platform == "darwin" else 1024),
                "rss_boundary": "whole extraction worker including warmup/parity; fixture load in separate worker",
                "counter_boundary": "opt-in benchmark-only counters; inclusive attribution; not RSS or elapsed-time guarantees",
                "cache_boundary": "reopened handle; OS cache uncontrolled, not OS-cold; warm primes same method",
                "scope": "extraction and authentication; no inference/support/publication measurement"}
    finally:
        graph.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--history-rows", type=int,
                        help="apply corrections/retractions only to this many initial assertions; default all")
    parser.add_argument("--commit-batch", type=int, default=1000,
                        help="assertion records per fixture commit; independent of row count")
    parser.add_argument("--instrument", action="store_true",
                        help="record opt-in benchmark-only physical-I/O and target-resolution counters")
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
    if args.commit_batch < 1:
        parser.error("commit-batch must be a positive integer")
    # A batch larger than the row count is one commit; record the effective size.
    args.commit_batch = min(args.commit_batch, args.rows)
    if args.worker:
        if args.fixture is None:
            parser.error("worker requires --fixture")
        result = (fixture(args.fixture, args.rows, args.history_rows,
                          commit_batch=args.commit_batch, instrumented=args.instrument)
                  if args.worker == "load" else worker(
                      args.fixture, args.rows, args.worker, args.selections[0],
                      args.times[0], args.caches[0], instrumented=args.instrument))
        print(json.dumps(result))
        return
    root = Path(__file__).resolve().parents[1]
    report = {"schema_version": 2, "configuration": {k: v for k, v in vars(args).items()
               if k not in ("output", "fixture", "worker")}, "samples": [], "complete": False,
              "command": [sys.executable, "-m", "benchmarks.reasoning_locator_pipeline",
                          *sys.argv[1:]],
              "timeout_policy": "existing timeout preserved; timeouts retain the incomplete "
                                "report and last checkpoint without quantiles",
              "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "shared_harness_sha256": hashlib.sha256(
                  Path(__file__).with_name("reasoning_frontier_pipeline.py").read_bytes()).hexdigest(),
              "instrumentation_sha256": hashlib.sha256(
                  Path(__file__).with_name("reasoning_locator_instrumentation.py").read_bytes()).hexdigest(),
              "python": sys.version, "platform": platform.platform(), "dependencies": {},
              "attribution_boundary": "fixture stages and extraction counters are separate worker processes; "
                                      "inclusive attribution buckets do not sum to end-to-end time",
              "evaluator": {"measured": False,
                            "boundary": "locator harness does not execute rules or publication"},
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
                  "--rows", str(args.rows), "--fixture", str(path),
                  "--commit-batch", str(args.commit_batch)]
        if args.history_rows is not None:
            common += ["--history-rows", str(args.history_rows)]
        if args.instrument:
            common.append("--instrument")
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
        if args.instrument:
            report["attribution"] = {key: _counter_summary(group)
                                     for key, group in groups.items()}
            report["fixture_attribution"] = {
                "construction": report.get("fixture", {}).get("construction"),
                "counters": report.get("fixture", {}).get("instrumentation"),
            }
        save()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
