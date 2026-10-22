"""RE-18 incremental maintenance workloads: inserts, retractions, revisions, cycles.

Run ``python -m benchmarks.reasoning_maintenance --output <path>``. Each
workload rematerializes once, then times the incremental operation against a
full rebuild over several repeats, reporting P50/P95, peak RSS, persisted
support counts and write amplification (facts plus supports per base fact).
No correctness trade for latency: every incremental result must equal the
rebuilt closure exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


def make_program_and_base(size):
    from gestaltdb.reasoning.model import Variable
    from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact

    x, y, z = Variable("x"), Variable("y"), Variable("z")
    program = GenericProgram([
        GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
        GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                    TypedAtom("reach", [x, z])),
    ])
    base = [TypedFact("edge", [f"a{i}", f"a{i + 1}"]) for i in range(size)]
    base += [TypedFact("edge", [f"b{i}", f"b{i + 1}"]) for i in range(size)]
    fresh = TypedFact("edge", ["new-a", "new-b"])
    return program, base, fresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[8, 40])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-facts", type=int, default=100_000)
    parser.add_argument("--max-candidates", type=int, default=20_000_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from gestaltdb.reasoning.maintenance import (
        propagate_insertions,
        rematerialize,
        retract_facts,
    )
    from gestaltdb.reasoning.model import ReasoningLimits, Variable
    from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact

    limits = ReasoningLimits(max_facts=args.max_facts, max_candidate_rows=args.max_candidates)
    rows = []
    for size in args.sizes:
        program, base, fresh = make_program_and_base(size)
        material = rematerialize(program, base, limits=limits)

        insert_s, retract_s, revision_s, noop_insert_s, noop_retract_s = [], [], [], [], []
        for _ in range(args.repeats):
            start = time.perf_counter()
            grown = propagate_insertions(program, list(material.facts), [fresh],
                                         limits=limits, index=material.index)
            insert_s.append(time.perf_counter() - start)

            start = time.perf_counter()
            shrunk = retract_facts(program, material.facts, [base[0]],
                                   limits=limits, index=material.index)
            retract_s.append(time.perf_counter() - start)

            x, y = Variable("x"), Variable("y")
            revised = GenericProgram(list(program.rules) + [
                GenericRule("loop", [TypedAtom("edge", [x, y])], [], TypedAtom("loop", [x, y]))])
            start = time.perf_counter()
            revised_material = rematerialize(revised, base, limits=limits)
            revision_s.append(time.perf_counter() - start)

            start = time.perf_counter()
            noop_grown = propagate_insertions(program, list(material.facts), list(base),
                                              limits=limits, index=material.index)
            noop_insert_s.append(time.perf_counter() - start)
            assert noop_grown.added == ()

            start = time.perf_counter()
            noop_shrunk = retract_facts(program, list(material.facts), [TypedFact("edge", ["ghost"])],
                                        limits=limits, index=material.index)
            noop_retract_s.append(time.perf_counter() - start)
            assert noop_shrunk.removed == ()

        assert set(grown.facts) == set(rematerialize(program, base + [fresh], limits=limits).facts)
        assert set(shrunk.facts) == set(
            rematerialize(program, [fact for fact in base if fact != base[0]], limits=limits).facts)
        assert TypedFact("loop", [f"a0", "a1"]) in revised_material.facts

        rows.append({
            "status": "ok", "size": size, "closure_facts": len(material.facts),
            "insert_p50_s": _p50(insert_s), "insert_p95_s": _p95(insert_s),
            "retract_p50_s": _p50(retract_s), "retract_p95_s": _p95(retract_s),
            "revision_p50_s": _p50(revision_s), "revision_p95_s": _p95(revision_s),
            "noop_insert_p50_s": _p50(noop_insert_s), "noop_retract_p50_s": _p50(noop_retract_s),
            "persisted_supports": len(material.supports),
            "write_amplification": (len(material.facts) + len(material.supports)) / max(1, len(base)),
            "rss_peak_bytes": _peak_rss(),
        })

    root = Path(__file__).resolve().parents[1]
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
