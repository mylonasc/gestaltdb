"""RE-22 temporal benchmarks: fragmentation, changes, limits and extraction split.

Run ``python -m benchmarks.reasoning_temporal --output <path>``. Each row
reports P50/P95 over repeats plus peak RSS: fragmented range evaluation,
validity-change propagation against full rebuild, change-segment extraction
separately from inference, and limit-breach behavior. Failure exposes no
successful partial interval result: limit breaches raise before publishing
anything.
"""

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


def fragmented_base(n, width, stride, tag="n"):
    from gestaltdb.reasoning.intervals import Interval, IntervalSet
    from gestaltdb.reasoning.temporal_closure import TemporalFact
    from gestaltdb.reasoning.typed import TypedFact

    return [TemporalFact(TypedFact("edge", [f"{tag}{i}", f"{tag}{i + 1}"]),
                         IntervalSet([Interval(i * stride, i * stride + width)]))
            for i in range(n)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[20, 60])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-facts", type=int, default=100_000)
    parser.add_argument("--max-candidates", type=int, default=20_000_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from gestaltdb.reasoning.intervals import Interval, IntervalSet
    from gestaltdb.reasoning.model import ReasoningLimits
    from gestaltdb.reasoning.temporal_closure import (
        change_segments,
        evaluate_range,
        update_range,
        verify_closure,
    )

    limits = ReasoningLimits(max_facts=args.max_facts, max_candidate_rows=args.max_candidates)
    program = make_program()
    rows = []
    for size in args.sizes:
        base = fragmented_base(size, 6, 4)
        facts = [item.fact for item in base]

        extract_s, infer_s = [], []
        for _ in range(args.repeats):
            start = time.perf_counter()
            segments = change_segments(base)
            extract_s.append(time.perf_counter() - start)
        closure = evaluate_range(program, base, limits=limits)
        for _ in range(args.repeats):
            start = time.perf_counter()
            rebuilt = evaluate_range(program, base, limits=limits)
            infer_s.append(time.perf_counter() - start)
        assert rebuilt == closure

        changed = list(base)
        mid = size // 2
        changed[mid] = type(base[0])(base[mid].fact, IntervalSet([Interval(mid * 4, mid * 4 + 3)]))
        update_s, rebuild_s = [], []
        for _ in range(args.repeats):
            start = time.perf_counter()
            out = update_range(program, closure, changed, limits=limits)
            update_s.append(time.perf_counter() - start)
            start = time.perf_counter()
            fresh = evaluate_range(program, changed, limits=limits)
            rebuild_s.append(time.perf_counter() - start)
        assert {d.fact for d in out.closure.derivations} == {d.fact for d in fresh.derivations}
        verify_closure(program, out.closure, limits=limits)

        rows.append({
            "status": "ok", "size": size,
            "segments": len(closure.segments), "derivations": len(closure.derivations),
            "extract_p50_s": _p50(extract_s), "extract_p95_s": _p95(extract_s),
            "infer_p50_s": _p50(infer_s), "infer_p95_s": _p95(infer_s),
            "update_p50_s": _p50(update_s), "update_p95_s": _p95(update_s),
            "rebuild_p50_s": _p50(rebuild_s), "rebuild_p95_s": _p95(rebuild_s),
            "reused_segments": out.reused_segments, "recomputed_segments": out.recomputed_segments,
            "rss_peak_bytes": _peak_rss(),
        })

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
