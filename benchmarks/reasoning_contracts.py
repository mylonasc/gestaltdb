"""RE-01 frozen-budget runner; reference updates are full rematerializations.

Run ``python -m benchmarks.reasoning_contracts --output <path>``. Existing
ontology workers retain their staged measurements and analytical answer checks.
No benchmark changes production evaluator behavior or imports test oracles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

from .ontology import _digest, _peak_rss, _run_worker, _save_report, _timed, footprint


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGETS = ROOT / "planning/reasoning-engine/performance-budgets.json"


def update_worker(size, mode):
    metrics = {}

    def imports():
        import gestaltdb.reasoning.engine

    _timed(metrics, "common_import_s", imports)
    from gestaltdb.reasoning.engine import DatalogEngine
    from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningLimits, Rule, Variable

    x, y, z = Variable("x"), Variable("y"), Variable("z")
    base = tuple((f"n{i}", "p", "root") for i in range(size))
    insertion = (*base, ("leaf", "p", "n0"))
    deletion = tuple(row for row in insertion if row != ("n0", "p", "root"))
    phases = [("base", base, set(base)),
              ("insert", insertion, set(insertion) | {("leaf", "p", "root")}),
              ("delete", deletion, set(deletion))]
    start = time.perf_counter()
    program = _timed(metrics, "compile_s", lambda: Program([
        Rule("transitive", [Atom(x, "p", y), Atom(y, "p", z)], Atom(x, "p", z)),
    ]))
    limits = ReasoningLimits(max_proof_nodes=100_000, max_proof_edges=400_000)
    phase_results = []
    pipeline = metrics["compile_s"]
    for name, rows, expected in phases:
        facts = _timed(metrics, name + "_conversion_s", lambda: [Fact(*row) for row in rows])
        result = _timed(metrics, name + "_execution_s", lambda: DatalogEngine().evaluate(
            program, facts, limits=limits, collect_proofs=mode == "native-proofs"))
        decoded = _timed(metrics, name + "_decode_s", lambda: {(f.subject, f.predicate, f.object) for f in result.facts})
        pipeline += sum(metrics[name + suffix] for suffix in ("_conversion_s", "_execution_s", "_decode_s"))
        if decoded != expected:
            raise ValueError(f"{name}: closure mismatch")
        if result.proofs is not None and result.proofs.truncated:
            raise ValueError(f"{name}: incomplete witness trace")
        phase_results.append({"phase": name, "closure_digest": _digest(decoded),
                              "closure_facts": len(decoded), "iterations": result.iterations})
    metrics["pipeline_s"] = pipeline
    metrics["pipeline_with_validation_s"] = time.perf_counter() - start
    return {"status": "ok", "metrics": metrics, "rss_peak_bytes": _peak_rss(),
            "execution_kind": "full-rematerialization-not-incremental",
            "phases": phase_results}


def worker(spec):
    if spec["case"]["workload"] == "update":
        return update_worker(spec["case"]["size"], spec["mode"])
    return _run_worker({"backend": spec["mode"], "workload": spec["case"]["workload"],
                        "size": spec["case"]["size"], "max_input": 100_000,
                        "max_facts": 100_000, "max_candidates": 2_000_000,
                        "max_justifications": 100_000, "temp_root": spec["temp_root"]})


def percentile95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def validate_budgets(budgets):
    if budgets.get("schema_version") != 1:
        raise ValueError("unsupported budget schema")
    repeats = budgets.get("repeats")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ValueError("positive integer repeat count required")
    modes = budgets.get("modes")
    if (not isinstance(modes, list) or not modes or len(set(modes)) != len(modes)
            or set(modes) - {"native", "native-proofs"}):
        raise ValueError("unsupported or duplicate execution modes")
    timeout = budgets.get("worker_timeout_s")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("positive finite timeout required")
    cases = budgets.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("nonempty cases required")
    names = set()
    for case in cases:
        if not isinstance(case.get("name"), str) or not case["name"] or case["name"] in names:
            raise ValueError("unique nonempty case names required")
        names.add(case["name"])
        if case.get("workload") not in {"hierarchy", "recursive", "dense", "duplicates", "update"}:
            raise ValueError("unsupported workload")
        size = case.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 10_000:
            raise ValueError("integer size between 2 and 10000 required")
        for key in ("pipeline_p95_s", "cold_worker_p95_s", "rss_peak_bytes"):
            value = case.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"positive finite {key} required")


def summarize(rows, budgets):
    validate_budgets(budgets)
    summaries = []
    for case in budgets["cases"]:
        for mode in budgets["modes"]:
            selected = [r for r in rows if r["case"] == case["name"] and r["mode"] == mode]
            failures = []
            if len(selected) != budgets["repeats"] or any(r["status"] != "ok" for r in selected):
                failures.append("missing/failed worker")
            metrics = {}
            for key, limit_key in (("pipeline_s", "pipeline_p95_s"),
                                   ("worker_wall_s", "cold_worker_p95_s"),
                                   ("rss_peak_bytes", "rss_peak_bytes")):
                values = [r.get(key) if key != "pipeline_s" else r.get("metrics", {}).get(key) for r in selected]
                if not values or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                     or not math.isfinite(v) or v < 0 for v in values):
                    failures.append(f"missing/invalid {key}")
                    continue
                metrics[key] = {"median": statistics.median(values), "p95": percentile95(values),
                                "max": max(values), "budget": case[limit_key]}
                measured = max(values) if key == "rss_peak_bytes" else percentile95(values)
                if measured > case[limit_key]:
                    failures.append(key + " budget exceeded")
            summaries.append({"case": case["name"], "mode": mode, "passed": not failures,
                              "failures": failures, "metrics": metrics})
    return summaries


def run(args):
    raw = args.budgets.read_bytes()
    budgets = json.loads(raw)
    acceptance_repeats = budgets["repeats"]
    if args.repeats is not None:
        budgets["repeats"] = args.repeats
    validate_budgets(budgets)
    if not args.temp_root.is_dir():
        raise ValueError("temp root must exist")
    threads = {key: "1" for key in ("OMP_NUM_THREADS", "POLARS_MAX_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}
    env = {**os.environ, "PYTHONHASHSEED": "0", **threads}
    report = {"schema_version": 1, "budget_sha256": hashlib.sha256(raw).hexdigest(),
              "effective_budgets": budgets, "python": sys.version, "platform": platform.platform(),
              "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
              "thread_environment": threads,
              "acceptance_repeat_count": budgets["repeats"] >= acceptance_repeats,
              "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "ontology_harness_sha256": hashlib.sha256((ROOT / "benchmarks/ontology.py").read_bytes()).hexdigest(),
              "footprint": footprint(), "rows": [], "complete": False}
    _save_report(args.output, report)
    for case in budgets["cases"]:
        for mode in budgets["modes"]:
            for repeat in range(budgets["repeats"]):
                spec = {"case": case, "mode": mode, "temp_root": str(args.temp_root)}
                start = time.perf_counter()
                try:
                    process = subprocess.run([sys.executable, "-m", "benchmarks.reasoning_contracts", "--worker", json.dumps(spec)],
                                             capture_output=True, text=True, env=env, cwd=ROOT,
                                             timeout=budgets["worker_timeout_s"])
                    result = json.loads(process.stdout) if not process.returncode else {
                        "status": "error", "reason": process.stderr[-4000:]}
                except subprocess.TimeoutExpired:
                    result = {"status": "timeout"}
                except ValueError as exc:
                    result = {"status": "error", "reason": str(exc)}
                report["rows"].append({**result, "case": case["name"], "mode": mode, "repeat": repeat,
                                       "worker_wall_s": time.perf_counter() - start})
                _save_report(args.output, report)
    report["complete"] = True
    report["summaries"] = summarize(report["rows"], budgets)
    report["passed"] = all(item["passed"] for item in report["summaries"])
    _save_report(args.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    parser.add_argument("--repeats", type=int, help="smoke override; five repeats required for acceptance")
    parser.add_argument("--temp-root", type=Path, default=Path("/tmp/opencode"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.loads(args.worker))))
        return
    report = run(args)
    print(json.dumps({"passed": report["passed"], "rows": len(report["rows"]),
                      "output": str(args.output)}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
