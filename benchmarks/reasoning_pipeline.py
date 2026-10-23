"""Fresh-worker closure-pipeline baseline for #194; not a native speedup claim."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time


def run_once(rows: int, backend: str, batch_rows: int, commit_batch: int = 2000,
             progress_path: "Path | None" = None, *, selection: str = "all",
             payload: str = "fixed", warmup: bool = False) -> dict:
    from gestaltdb.epistemic import Claim
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.ontology import Domain, Ontology, SubClassOf, compile_ontology
    from gestaltdb.ontology.model import TYPE_PREDICATE
    from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
    from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, PinnedClaimSource
    from gestaltdb.reasoning.engine import DatalogEngine
    from gestaltdb.reasoning.extraction import ExtractionRequest
    from gestaltdb.reasoning.model import Fact, ReasoningLimits
    from gestaltdb.versioning import ClaimVersionWrite

    if not 1 <= rows <= 1_000_000 or not 1 <= batch_rows <= 100_000:
        raise ValueError("rows must be 1..1000000 and batch_rows 1..100000")
    if isinstance(commit_batch, bool) or not isinstance(commit_batch, int) or commit_batch < 1:
        raise ValueError("commit_batch must be a positive integer")
    if selection not in ("all", "predicate", "subject") or payload not in ("fixed", "variable"):
        raise ValueError("unknown selection or payload")

    def note(phase: str, seconds: float) -> None:
        metrics[phase] = seconds
        if progress_path is not None:
            with open(progress_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"phase": phase, "s": seconds}) + "\n")
    metrics = {}
    backend_options = {"map_size": max(64 * 1024 * 1024, rows * 32_768)} if backend == "lmdb" else {}
    with tempfile.TemporaryDirectory(prefix="reasoning-pipeline-") as directory:
        graph = GraphDB.create(Path(directory) / "db", backend=backend, serializer="json",
                               backend_options=backend_options)
        try:
            begin = time.perf_counter()
            pending = []
            for index in range(rows):
                predicate = "memberOf" if selection == "all" or index % 4 == 0 else "other"
                size = 32 if payload == "fixed" else 32 + (index * 131) % 4065
                claim = Claim(f"n{index}", predicate, f"group{index % 8}", "positive", "registry",
                              world="default", provenance={"benchmark_payload": "x" * size})
                pending.append(ClaimVersionWrite.assertion(claim, (0, 100)))
                if len(pending) >= commit_batch:
                    graph.commit_versions(pending)
                    pending = []
            if pending:
                graph.commit_versions(pending)
            note("load_s", time.perf_counter() - begin)
            graph.close()
            begin = time.perf_counter()
            graph = GraphDB.open(Path(directory) / "db")
            note("reopen_s", time.perf_counter() - begin)
            begin = time.perf_counter()
            compiled = compile_ontology(Ontology("pipeline", [Domain("memberOf", "Member"),
                                                               SubClassOf("Member", "Entity")]))
            note("compile_s", time.perf_counter() - begin)
            with graph.read_view(valid_time=50) as view:
                request = ExtractionRequest(
                    world="default", batch_rows=batch_rows, max_rows=rows,
                    predicates=None if selection == "all" else ("memberOf",),
                    subject="n0" if selection == "subject" else None)
                if warmup:
                    begin = time.perf_counter()
                    with PinnedBatchedClaimSource(
                        view, request=request, ontology_revision=compiled.revision_id,
                        selection=ClaimSelection(), mapping=ClaimMapping()) as primer:
                        for batch in primer.iter_batches():
                            pass
                    note("warmup_s", time.perf_counter() - begin)
                begin = time.perf_counter()
                source = PinnedBatchedClaimSource(
                    view, request=request,
                    ontology_revision=compiled.revision_id, selection=ClaimSelection(), mapping=ClaimMapping())
                note("source_open_verify_s", time.perf_counter() - begin)
                begin = time.perf_counter()
                with source:
                    note("source_enter_verify_s", time.perf_counter() - begin)
                    begin = time.perf_counter()
                    evidence = [item for batch in source.iter_batches() for item in batch.evidence]
                    note("extract_decode_verify_s", time.perf_counter() - begin)
                    profile = source.profile
                with PinnedClaimSource(view, world="default", ontology_revision=compiled.revision_id,
                                       selection=ClaimSelection(), mapping=ClaimMapping()) as reference:
                    expected = [item for item in reference.iter_evidence()
                                if (selection == "all" or item.fact.predicate == "memberOf")
                                and (selection != "subject" or item.fact.subject == "n0")]
                identity = lambda item: (item.fact.subject, item.fact.predicate, item.fact.object, item.reference_id)
                limits = ReasoningLimits(max_input_facts=rows + 1, max_facts=3 * rows + 1,
                                         max_candidate_rows=max(1000, 10 * rows))
                begin = time.perf_counter()
                result = DatalogEngine().evaluate(compiled.program, evidence, limits=limits)
                note("evaluate_s", time.perf_counter() - begin)
                oracle = DatalogEngine().evaluate(compiled.program, expected, limits=limits)
                expected_rows = rows if selection == "all" else (1 if selection == "subject" else (rows + 3) // 4)
                analytical = {item.fact for item in expected}
                for item in expected:
                    analytical.add(Fact(item.fact.subject, TYPE_PREDICATE, "Member"))
                    analytical.add(Fact(item.fact.subject, TYPE_PREDICATE, "Entity"))
                if sorted(map(identity, evidence)) != sorted(map(identity, expected)):
                    raise ValueError("exact evidence parity failed")
                if (result.facts != oracle.facts or set(result.facts) != analytical
                        or len(result.facts) != 3 * expected_rows):
                    raise ValueError("closure parity/cardinality failed")
                begin = time.perf_counter()
                view.provenance.verify_source(graph)
                note("post_evaluation_verify_s", time.perf_counter() - begin)
            # Reference verification is outside the measured production stages.
            metrics["reasoning_pipeline_s"] = sum(metrics[key] for key in (
                "reopen_s", "compile_s", "source_open_verify_s", "source_enter_verify_s", "extract_decode_verify_s",
                "evaluate_s", "post_evaluation_verify_s"))
            digest = hashlib.sha256(json.dumps(sorted(map(identity, evidence))).encode()).hexdigest()
            return {"status": "ok", "rows": rows, "closure_facts": len(result.facts),
                    "evidence_digest": digest, "backend": backend, "batch_rows": batch_rows,
                    "commit_batch": commit_batch,
                    "selection": selection, "payload": payload,
                    "payload_location": "claim provenance; short entity IDs respect index limits",
                    "pushdown": profile.pushdown,
                    "backend_options": backend_options,
                    "cache_state": ("primed extraction on reopened handle" if warmup else "first extraction on reopened handle") + "; OS cache uncontrolled, not OS-cold",
                    "scanned": profile.scanned, "accepted": profile.rows, "verifications": profile.verifications,
                    "metrics": metrics, "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                    "mode": "closure-only; supports/publication/native adapters require separate measurements"}
        finally:
            graph.close()


def run_worker(command: list[str], timeout: float) -> dict:
    """Preserve bounded worker diagnostics; a failed worker is never success."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "timeout_s": timeout}
    if completed.returncode:
        return {"status": "error", "returncode": completed.returncode,
                "stderr": completed.stderr[-4000:], "stdout": completed.stdout[-4000:]}
    try:
        sample = json.loads(completed.stdout)
    except (ValueError, TypeError):
        return {"status": "error", "reason": "worker emitted invalid JSON", "stdout": completed.stdout[-4000:]}
    if not isinstance(sample, dict) or sample.get("status") != "ok" or not isinstance(sample.get("metrics"), dict):
        if isinstance(sample, dict) and sample.get("status") == "unavailable" and isinstance(sample.get("reason"), str):
            return sample
        return {"status": "error", "reason": "worker emitted an incomplete sample"}
    return sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--backend", choices=("lmdb", "leveldb", "pyrex"), default="lmdb")
    parser.add_argument("--batch-rows", type=int, default=256)
    parser.add_argument("--commit-batch", type=int, default=2000)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--selection", choices=("all", "predicate", "subject"), default="all")
    parser.add_argument("--payload", choices=("fixed", "variable"), default="fixed")
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0:
        parser.error("repeats and timeout must be positive")
    if args.worker:
        print(json.dumps(run_once(args.rows, args.backend, args.batch_rows,
                                  commit_batch=args.commit_batch, progress_path=args.progress,
                                  selection=args.selection, payload=args.payload, warmup=args.warmup)))
        return
    command = [sys.executable, "-m", "benchmarks.reasoning_pipeline", "--worker", "--rows", str(args.rows),
               "--backend", args.backend, "--batch-rows", str(args.batch_rows),
               "--commit-batch", str(args.commit_batch), "--selection", args.selection,
               "--payload", args.payload] + (["--warmup"] if args.warmup else [])
    progress_path = args.progress
    if progress_path is not None and args.repeats != 1:
        parser.error("--progress requires --repeats 1")
    samples = []
    for _ in range(args.repeats):
        if progress_path is not None and progress_path.exists():
            progress_path.unlink()
        worker_command = command + (["--progress", str(progress_path)] if progress_path is not None else [])
        sample = run_worker(worker_command, args.timeout)
        if sample["status"] != "ok" and progress_path is not None and progress_path.exists():
            sample["progress"] = progress_path.read_text()[-2000:]
        samples.append(sample)
        if sample["status"] != "ok":
            break
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    summary = {}
    complete = len(samples) == args.repeats and all(sample["status"] == "ok" for sample in samples)
    for metric in samples[0]["metrics"] if complete else ():
        values = sorted(sample["metrics"][metric] for sample in samples)
        summary[metric] = {"p50": statistics.median(values), "p95": values[max(0, math.ceil(.95 * len(values)) - 1)]}
    report = {"schema_version": 1, "git_revision": revision, "python": sys.version, "samples": samples,
              "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "summary": summary,
              "configuration": {"rows": args.rows, "backend": args.backend, "batch_rows": args.batch_rows,
                                 "commit_batch": args.commit_batch, "repeats": args.repeats, "timeout_s": args.timeout}, "complete": complete}
    report["configuration"].update(selection=args.selection, payload=args.payload, warmup=args.warmup)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    if not complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
