"""RE-26 benchmark-gated scale decisions: join profiles and crossover evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time


def _peak_rss() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _p50(samples):
    return statistics.median(samples)


def _p95(samples):
    ordered = sorted(samples)
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


def make_program():
    from gestaltdb.reasoning.model import Variable
    from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom

    x, y, z = Variable("x"), Variable("y"), Variable("z")
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
        GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                    TypedAtom("reach", [x, z])),
    ])


def chain_facts(n, tag="n"):
    from gestaltdb.reasoning.typed import TypedFact

    return [TypedFact("edge", [f"{tag}{i}", f"{tag}{i + 1}"]) for i in range(n)]


def star_facts(n, tag="s"):
    from gestaltdb.reasoning.typed import TypedFact

    return [TypedFact("link", [f"{tag}hub", f"{tag}leaf{i}"]) for i in range(n)]


def dense_facts(n, tag="d"):
    from gestaltdb.reasoning.typed import TypedFact

    return [TypedFact("edge", [f"{tag}{i}", f"{tag}{j}"]) for i in range(n) for j in range(n) if i != j]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-facts", type=int, default=100_000)
    parser.add_argument("--max-candidates", type=int, default=20_000_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from gestaltdb.reasoning.maintenance import rematerialize, retract_facts
    from gestaltdb.reasoning.model import ReasoningLimits, Variable
    from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom

    limits = ReasoningLimits(max_facts=args.max_facts, max_candidate_rows=args.max_candidates)
    program = make_program()
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    star_program = GenericProgram([
        GenericRule("hub", [TypedAtom("link", [x, y])], [], TypedAtom("adjacent", [x, y])),
        GenericRule("two-hop", [TypedAtom("link", [x, y]), TypedAtom("link", [x, z])], [],
                    TypedAtom("coleaf", [y, z])),
    ])

    rows = []
    for size in (10, 20, 40):
        closure_s, closure_p95 = [], []
        for _ in range(args.repeats):
            start = time.perf_counter()
            result = GenericEngine().evaluate(program, chain_facts(size), limits=limits)
            closure_s.append(time.perf_counter() - start)
        rows.append({"status": "ok", "workload": "transitive-chain", "size": size,
                     "facts": len(result.facts), "iterations": result.iterations,
                     "p50_s": _p50(closure_s), "p95_s": _p95(closure_s),
                     "rss_peak_bytes": _peak_rss()})
    for size in (20, 80):
        star_s = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            result = GenericEngine().evaluate(star_program, star_facts(size), limits=limits)
            star_s.append(time.perf_counter() - start)
        rows.append({"status": "ok", "workload": "star-multiway", "size": size,
                     "facts": len(result.facts), "iterations": result.iterations,
                     "p50_s": _p50(star_s), "p95_s": _p95(star_s),
                     "rss_peak_bytes": _peak_rss()})
    dense_s = []
    for _ in range(args.repeats):
        start = time.perf_counter()
        result = GenericEngine().evaluate(program, dense_facts(8), limits=limits)
        dense_s.append(time.perf_counter() - start)
    rows.append({"status": "ok", "workload": "dense-small", "size": 8,
                 "facts": len(result.facts), "iterations": result.iterations,
                 "p50_s": _p50(dense_s), "p95_s": _p95(dense_s),
                 "rss_peak_bytes": _peak_rss()})

    base = chain_facts(24)
    material = rematerialize(program, base, limits=limits)
    for fraction, label in ((1, "single-edge"), (2, "two-edge"), (12, "half-closure")):
        victims = base[:fraction]
        dred_s, rebuild_s = [], []
        for _ in range(args.repeats):
            start = time.perf_counter()
            out = retract_facts(program, material.facts, victims, limits=limits, index=material.index)
            dred_s.append(time.perf_counter() - start)
            start = time.perf_counter()
            fresh = rematerialize(program, [fact for fact in base if fact not in victims], limits=limits)
            rebuild_s.append(time.perf_counter() - start)
        assert set(out.facts) == set(fresh.facts)
        rows.append({"status": "ok", "workload": f"dred-vs-rebuild-{label}", "size": len(base),
                     "facts": len(out.facts), "dred_work": out.work, "rebuild_work": fresh.work,
                     "overdeleted": out.overdeleted, "rederived": out.rederived,
                     "dred_p50_s": _p50(dred_s), "dred_p95_s": _p95(dred_s),
                     "rebuild_p50_s": _p50(rebuild_s), "rebuild_p95_s": _p95(rebuild_s),
                     "rss_peak_bytes": _peak_rss()})

    root = Path(__file__).resolve().parents[1]
    import hashlib
    import json

    report: dict = {"schema_version": 1, "python": sys.version, "platform": platform.platform(),
                    "complete": False, "rows": rows,
                    "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                    "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
                    "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    report["complete"] = True
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": True, "rows": len(rows),
                      "output": None if args.output is None else str(args.output)}))


if __name__ == "__main__":
    main()
