"""Fresh-worker indexed current-graph demand versus complete record projection."""

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

from .reasoning_frontier_pipeline import call_worker


def setup(path, rows):
    from gestaltdb.graphdb import Edge, GraphDB, Node
    graph = GraphDB.create(path, backend="lmdb", serializer="json",
                           backend_options={"map_size": max(256 * 1024**2, rows * 65536)})
    start = time.perf_counter()
    try:
        graph.create_node_property_index("employer")
        graph.put_nodes([Node(f"n{i}", labels=["Student"], properties={"employer": "org"}) for i in range(rows)])
        graph.put_edges_bulk([Edge(f"e{i}", f"n{i}", f"n{i+1}" if i < 8 else f"n{i}", {"type": "knows"})
                              for i in range(rows - 1)])
        return {"status": "ok", "load_s": time.perf_counter() - start, "nodes": rows, "edges": rows - 1}
    finally:
        graph.close()


def measure(path, rows, mode):
    start = time.perf_counter()
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.ontology import Ontology, SubClassOf, SubPropertyOf, Transitive, compile_ontology
    from gestaltdb.reasoning import Atom, Fact, ReasoningLimits, Variable
    from gestaltdb.reasoning.adapters.graph import GraphMapping, GraphSourceLimits, PinnedGraphSource
    from gestaltdb.reasoning.adapters.graph_frontier import answer_current_graph
    compiled = compile_ontology(Ontology("benchmark", [SubClassOf("Student", "Person"),
                                                       SubPropertyOf("edge", "reach"), Transitive("reach")]))
    mapping = GraphMapping(labels=(("Student", "Student"),), edge_types=(("knows", "edge"),),
                           node_properties=(("employer", "worksFor"),))
    limits = ReasoningLimits(max_input_facts=rows * 5, max_facts=rows * 10)
    graph = GraphDB.open(path)
    opened = time.perf_counter()
    metrics = {"import_compile_reopen_s": opened - start}
    try:
        with graph.current_read_view(require_verifiable=True) as view:
            metrics["snapshot_open_authenticate_s"] = time.perf_counter() - opened
            begin = time.perf_counter()
            if mode == "frontier":
                result = answer_current_graph(view, compiled.program, Atom("n0", "reach", Variable("y")),
                                               ontology_revision=compiled.revision_id, mapping=mapping,
                                               limits=limits, source_limits=GraphSourceLimits(rows * 3))
                answers = result.answer.facts
                read = result.records_read
                inputs = len(result.answer.evidence)
                accesses = dict(result.accesses)
            else:
                with PinnedGraphSource(view, mapping=mapping, limits=GraphSourceLimits(rows * 3)) as source:
                    result = source.evaluate(compiled, limits=limits)
                answers = result.match(subject="n0", predicate="reach")
                read = rows * 2 - 1
                inputs = rows * 3 - 1
                accesses = {"complete-record-projection": read}
            metrics["extract_evaluate_authenticate_s"] = time.perf_counter() - begin
        metrics["total_pipeline_s"] = time.perf_counter() - start
        expected = tuple(sorted(Fact("n0", "reach", f"n{i}") for i in range(1, 9)))
        if answers != expected:
            raise ValueError("analytical goal parity failed")
        return {"status": "ok", "mode": mode, "metrics": metrics, "records_read": read,
                "input_evidence": inputs, "answers": len(answers), "accesses": accesses,
                "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                "cache": "fresh worker, reopened handle; OS cache uncontrolled, not OS-cold",
                "rss_boundary": "worker high-water includes imports and source authentication; not query-only RSS"}
    finally:
        graph.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", choices=("setup", "reference", "frontier"))
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    if not 10 <= args.rows <= 100000 or args.repeats < 1:
        parser.error("rows must be 10..100000; repeats must be positive")
    if args.worker:
        result = setup(args.path, args.rows) if args.worker == "setup" else measure(args.path, args.rows, args.worker)
        print(json.dumps(result))
        return
    report = {"complete": False, "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "python": sys.version, "rows": args.rows, "repeats": args.repeats, "samples": []}
    def save():
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="re350-", dir="/tmp/opencode") as path:
        common = [sys.executable, "-m", "benchmarks.reasoning_current_frontier", "--path", str(Path(path) / "db"),
                  "--rows", str(args.rows)]
        report["fixture"] = call_worker([*common, "--worker", "setup"], 300)
        save()
        if report["fixture"]["status"] != "ok":
            raise SystemExit("fixture failed; diagnostics saved")
        for repeat in range(args.repeats):
            for mode in ("reference", "frontier"):
                result = call_worker([*common, "--worker", mode], 300)
                result.update(mode=mode, repeat=repeat)
                report["samples"].append(result)
                save()
                if result["status"] != "ok":
                    raise SystemExit("worker failed; diagnostics saved")
        report["complete"] = True
        save()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
