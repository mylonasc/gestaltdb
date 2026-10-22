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
             progress_path: "Path | None" = None) -> dict:
    from gestaltdb.epistemic import Claim
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.ontology import Domain, Ontology, SubClassOf, compile_ontology
    from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
    from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, PinnedClaimSource
    from gestaltdb.reasoning.engine import DatalogEngine
    from gestaltdb.reasoning.extraction import ExtractionRequest
    from gestaltdb.reasoning.model import ReasoningLimits
    from gestaltdb.versioning import ClaimVersionWrite

    if not 1 <= rows <= 1_000_000 or not 1 <= batch_rows <= 100_000:
        raise ValueError("rows must be 1..1000000 and batch_rows 1..100000")
    if isinstance(commit_batch, bool) or not isinstance(commit_batch, int) or commit_batch < 1:
        raise ValueError("commit_batch must be a positive integer")

    def note(phase: str, seconds: float) -> None:
        metrics[phase] = seconds
        if progress_path is not None:
            with open(progress_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"phase": phase, "s": seconds}) + "\n")
    metrics = {}
    backend_options = {"map_size": max(64 * 1024 * 1024, rows * 32_768)} if backend == "lmdb" else {}
    with tempfile.TemporaryDirectory(prefix="reasoning-pipeline-", dir="/tmp/opencode") as directory:
        graph = GraphDB.create(Path(directory) / "db", backend=backend, serializer="json",
                               backend_options=backend_options)
        try:
            begin = time.perf_counter()
            pending = []
            for index in range(rows):
                claim = Claim(f"n{index}", "memberOf", f"group{index % 8}", "positive", "registry",
                              world="default")
                pending.append(ClaimVersionWrite.assertion(claim, (0, 100)))
                if len(pending) >= commit_batch:
                    graph.commit_versions(pending)
                    pending = []
            if pending:
                graph.commit_versions(pending)
            note("load_s", time.perf_counter() - begin)
            begin = time.perf_counter()
            compiled = compile_ontology(Ontology("pipeline", [Domain("memberOf", "Member"),
                                                               SubClassOf("Member", "Entity")]))
            note("compile_s", time.perf_counter() - begin)
            with graph.read_view(valid_time=50) as view:
                begin = time.perf_counter()
                source = PinnedBatchedClaimSource(
                    view, request=ExtractionRequest(world="default", batch_rows=batch_rows, max_rows=rows),
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
                    expected = list(reference.iter_evidence())
                identity = lambda item: (item.fact.subject, item.fact.predicate, item.fact.object, item.reference_id)
                assert sorted(map(identity, evidence)) == sorted(map(identity, expected))
                limits = ReasoningLimits(max_input_facts=rows + 1, max_facts=3 * rows + 1,
                                         max_candidate_rows=max(1000, 10 * rows))
                begin = time.perf_counter()
                result = DatalogEngine().evaluate(compiled.program, evidence, limits=limits)
                note("evaluate_s", time.perf_counter() - begin)
                oracle = DatalogEngine().evaluate(compiled.program, expected, limits=limits)
                assert result.facts == oracle.facts and len(result.facts) == 3 * rows
                begin = time.perf_counter()
                view.provenance.verify_source(graph)
                note("post_evaluation_verify_s", time.perf_counter() - begin)
            # Reference verification is outside the measured production stages.
            metrics["reasoning_pipeline_s"] = sum(metrics[key] for key in (
                "compile_s", "source_open_verify_s", "source_enter_verify_s", "extract_decode_verify_s",
                "evaluate_s", "post_evaluation_verify_s"))
            digest = hashlib.sha256(json.dumps(sorted(map(identity, evidence))).encode()).hexdigest()
            return {"status": "ok", "rows": rows, "closure_facts": len(result.facts),
                    "evidence_digest": digest, "backend": backend, "batch_rows": batch_rows,
                    "commit_batch": commit_batch,
                    "backend_options": backend_options,
                    "cache_state": "fresh worker with newly loaded warm database; not OS-cold",
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
        return {"status": "error", "reason": "worker emitted an incomplete sample"}
    return sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--backend", choices=("lmdb", "leveldb", "pyrex"), default="lmdb")
    parser.add_argument("--batch-rows", type=int, default=256)
    parser.add_argument("--commit-batch", type=int, default=2000)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0:
        parser.error("repeats and timeout must be positive")
    if args.worker:
        print(json.dumps(run_once(args.rows, args.backend, args.batch_rows,
                                  commit_batch=args.commit_batch, progress_path=args.progress)))
        return
    command = [sys.executable, "-m", "benchmarks.reasoning_pipeline", "--worker", "--rows", str(args.rows),
               "--backend", args.backend, "--batch-rows", str(args.batch_rows),
               "--commit-batch", str(args.commit_batch)]
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
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    if not complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
