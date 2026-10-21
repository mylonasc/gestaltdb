"""RE-07 typed/binary engine parity and cost; translation is 1:1 and lossless.

Run ``python -m benchmarks.reasoning_typed --output <path>``. Binary RE-01
workloads are translated atom-for-atom into typed rules (entities stay
entities; no literals or filters), then both engines must produce the
identical closure digest. Reports per-engine pipeline times, iterations and
RSS. This measures the positional-index cost of the generic engine, not a
typed-feature speedup.
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
import time


def worker(workload, size, max_input, max_facts, max_candidates):
    from benchmarks.ontology import _digest, _peak_rss, _timed, make_workload
    from gestaltdb.reasoning.engine import DatalogEngine
    from gestaltdb.reasoning.model import Fact, ReasoningLimits
    from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact

    metrics: dict[str, float] = {}
    workload_plan = _timed(metrics, "fixture_s", lambda: make_workload(
        workload, size, max_input=max_input, max_facts=max_facts))
    from gestaltdb.ontology.compiler import compile_ontology
    program = _timed(metrics, "compile_s", lambda: compile_ontology(workload_plan.ontology).program)

    def translate():
        rules = [GenericRule(rule.rule_id,
                             [TypedAtom(atom.predicate, [atom.subject, atom.object]) for atom in rule.body],
                             [], TypedAtom(rule.head.predicate, [rule.head.subject, rule.head.object]))
                 for rule in program.rules]
        return GenericProgram(rules)

    generic = _timed(metrics, "translation_s", translate)
    assert generic.digest != program.digest
    facts = [Fact(*row) for row in workload_plan.rows]
    typed = [TypedFact(row[1], [row[0], row[2]]) for row in workload_plan.rows]
    limits = ReasoningLimits(max_input_facts=max_input, max_facts=max_facts,
                             max_iterations=1000, max_candidate_rows=max_candidates,
                             max_proof_nodes=max_facts, max_proof_edges=4 * max_facts)
    start = time.perf_counter()
    native = DatalogEngine().evaluate(program, facts, limits=limits)
    metrics["indexed_s"] = time.perf_counter() - start
    start = time.perf_counter()
    generic_result = GenericEngine().evaluate(generic, typed, limits=limits)
    metrics["generic_s"] = time.perf_counter() - start
    assert generic_result.iterations == native.iterations
    decoded = {(f.subject, f.predicate, f.object) for f in native.facts}
    typed_decoded = {(f.values[0], f.predicate, f.values[1]) for f in generic_result.facts
                     if all(isinstance(v, str) for v in f.values)}
    assert typed_decoded == decoded
    assert _digest(decoded) == _digest(workload_plan.expected)
    metrics["pipeline_s"] = metrics["indexed_s"] + metrics["generic_s"]
    return {"status": "ok", "metrics": metrics, "closure_digest": _digest(decoded),
            "closure_facts": len(decoded), "iterations": native.iterations,
            "rss_peak_bytes": _peak_rss(), "execution_kind": "translated-binary-programs-no-literals"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workloads", nargs="+", default=["hierarchy", "recursive", "dense"])
    parser.add_argument("--sizes", nargs="+", type=int, default=[256, 1024, 128])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-input", type=int, default=100_000)
    parser.add_argument("--max-facts", type=int, default=100_000)
    parser.add_argument("--max-candidates", type=int, default=2_000_000)
    parser.add_argument("--temp-root", type=Path, default=Path("/tmp/opencode"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        spec = json.loads(args.worker)
        print(json.dumps(worker(**spec)))
        return
    root = Path(__file__).resolve().parents[1]
    report: dict = {"schema_version": 1, "python": sys.version, "platform": platform.platform(),
                    "complete": False, "rows": [],
                    "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                    "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
                    "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    env = {**os.environ, "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "POLARS_MAX_THREADS": "1"}
    for workload, size in zip(args.workloads, args.sizes):
        for _ in range(args.repeats):
            spec = {"workload": workload, "size": size, "max_input": args.max_input,
                    "max_facts": args.max_facts, "max_candidates": args.max_candidates}
            start = time.perf_counter()
            try:
                process = subprocess.run([sys.executable, "-m", "benchmarks.reasoning_typed", "--worker", json.dumps(spec)],
                                         capture_output=True, text=True, env=env, cwd=root, timeout=120)
                row = json.loads(process.stdout) if process.returncode == 0 else {
                    "status": "error", "reason": process.stderr[-4000:]}
            except subprocess.TimeoutExpired:
                row = {"status": "timeout"}
            report["rows"].append({**row, "workload": workload, "size": size,
                                   "worker_wall_s": time.perf_counter() - start})
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report["complete"] = True
    if args.output is not None:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    passed = all(row.get("status") == "ok" for row in report["rows"])
    print(json.dumps({"passed": passed, "rows": len(report["rows"]),
                      "output": None if args.output is None else str(args.output)}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
