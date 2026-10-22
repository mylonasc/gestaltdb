"""Fresh-worker closure-pipeline baseline for #194; not a native speedup claim."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time


def run_once(rows: int, backend: str, batch_rows: int) -> dict:
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.ontology import Domain, Ontology, SubClassOf, compile_ontology
    from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
    from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, PinnedClaimSource
    from gestaltdb.reasoning.engine import DatalogEngine
    from gestaltdb.reasoning.extraction import ExtractionRequest
    from gestaltdb.reasoning.model import ReasoningLimits

    if not 1 <= rows <= 1_000_000 or not 1 <= batch_rows <= 100_000:
        raise ValueError("rows must be 1..1000000 and batch_rows 1..100000")
    metrics = {}
    with tempfile.TemporaryDirectory(prefix="reasoning-pipeline-", dir="/tmp/opencode") as directory:
        graph = GraphDB.create(Path(directory) / "db", backend=backend, serializer="json")
        try:
            begin = time.perf_counter()
            for index in range(rows):
                graph.assert_claim(subject=f"n{index}", predicate="memberOf", object=f"group{index % 8}",
                                   agent="registry", polarity="positive", valid=(0, 100))
            metrics["load_s"] = time.perf_counter() - begin
            begin = time.perf_counter()
            compiled = compile_ontology(Ontology("pipeline", [Domain("memberOf", "Member"),
                                                               SubClassOf("Member", "Entity")]))
            metrics["compile_s"] = time.perf_counter() - begin
            with graph.read_view(valid_time=50) as view:
                begin = time.perf_counter()
                source = PinnedBatchedClaimSource(
                    view, request=ExtractionRequest(world="default", batch_rows=batch_rows, max_rows=rows),
                    ontology_revision=compiled.revision_id, selection=ClaimSelection(), mapping=ClaimMapping())
                metrics["source_open_verify_s"] = time.perf_counter() - begin
                begin = time.perf_counter()
                with source:
                    metrics["source_enter_verify_s"] = time.perf_counter() - begin
                    begin = time.perf_counter()
                    evidence = [item for batch in source.iter_batches() for item in batch.evidence]
                    metrics["extract_decode_verify_s"] = time.perf_counter() - begin
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
                metrics["evaluate_s"] = time.perf_counter() - begin
                oracle = DatalogEngine().evaluate(compiled.program, expected, limits=limits)
                assert result.facts == oracle.facts and len(result.facts) == 3 * rows
                begin = time.perf_counter()
                view.provenance.verify_source(graph)
                metrics["post_evaluation_verify_s"] = time.perf_counter() - begin
            # Reference verification is outside the measured production stages.
            metrics["reasoning_pipeline_s"] = sum(metrics[key] for key in (
                "compile_s", "source_open_verify_s", "source_enter_verify_s", "extract_decode_verify_s",
                "evaluate_s", "post_evaluation_verify_s"))
            digest = hashlib.sha256(json.dumps(sorted(map(identity, evidence))).encode()).hexdigest()
            return {"status": "ok", "rows": rows, "closure_facts": len(result.facts),
                    "evidence_digest": digest, "backend": backend, "batch_rows": batch_rows,
                    "cache_state": "fresh worker with newly loaded warm database; not OS-cold",
                    "scanned": profile.scanned, "accepted": profile.rows, "verifications": profile.verifications,
                    "metrics": metrics, "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                    "mode": "closure-only; supports/publication/native adapters require separate measurements"}
        finally:
            graph.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--backend", choices=("lmdb", "leveldb", "pyrex"), default="lmdb")
    parser.add_argument("--batch-rows", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0:
        parser.error("repeats and timeout must be positive")
    if args.worker:
        print(json.dumps(run_once(args.rows, args.backend, args.batch_rows)))
        return
    command = [sys.executable, "-m", "benchmarks.reasoning_pipeline", "--worker", "--rows", str(args.rows),
               "--backend", args.backend, "--batch-rows", str(args.batch_rows)]
    samples = []
    for _ in range(args.repeats):
        completed = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout, check=True)
        samples.append(json.loads(completed.stdout))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    report = {"schema_version": 1, "git_revision": revision, "python": sys.version, "samples": samples,
              "complete": all(sample["status"] == "ok" for sample in samples)}
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
