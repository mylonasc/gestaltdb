"""Isolated unprofiled HSA pairs over one receipt-authenticated fixture.

Before/after order alternates and method order rotates. Each worker attests the
checkout actually imported, avoiding accidental use of another editable install.
Profiling stays in reasoning_locator_pipeline, not in these timing workers.
"""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

from .reasoning_frontier_pipeline import last_checkpoint
from .reasoning_locator_pipeline import METHODS, SELECTIONS, TIMES


WORKER = r'''
import json, pathlib, sys
config = json.loads(sys.argv[1])
root = pathlib.Path(config["checkout"]).resolve()
sys.path[:0] = [str(root), str(root / "src")]
import gestaltdb
from benchmarks import reasoning_locator_pipeline as pipeline
loaded = {"library": str(pathlib.Path(gestaltdb.__file__).resolve()),
          "harness": str(pathlib.Path(pipeline.__file__).resolve())}
if not pathlib.Path(loaded["library"]).is_relative_to(root / "src"):
    raise RuntimeError("library was imported from the wrong checkout")
if not pathlib.Path(loaded["harness"]).is_relative_to(root / "benchmarks"):
    raise RuntimeError("harness was imported from the wrong checkout")
function = pipeline.fixture if config["action"] == "load" else pipeline.worker
result = function(**config["arguments"])
result["loaded_source"] = loaded
print(json.dumps(result))
'''


def provenance(root):
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((root / "src/gestaltdb").rglob("*.py"))}
    if not files:
        raise ValueError(f"no library sources in {root}")
    return {
        "checkout": str(root),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
        "library_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "harness_sha256": {name: hashlib.sha256((root / "benchmarks" / name).read_bytes()).hexdigest()
                           for name in ("reasoning_locator_pipeline.py", "reasoning_frontier_pipeline.py",
                                        "reasoning_locator_instrumentation.py")},
    }


def run_worker(root, action, arguments, timeout):
    config = {"checkout": str(root), "action": action, "arguments": arguments}
    command = [sys.executable, "-c", WORKER, json.dumps(config)]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root), str(root / "src"))))
    begin = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=root, env=environment, text=True,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"status": "timeout", "timeout_s": timeout, "last_stage": last_checkpoint(exc.stderr)}
    if result.returncode:
        return {"status": "error", "returncode": result.returncode, "stderr": result.stderr[-8000:],
                "stdout": result.stdout[-1000:], "last_stage": last_checkpoint(result.stderr)}
    try:
        row = json.loads(result.stdout)
    except ValueError:
        return {"status": "error", "stdout": result.stdout[-1000:]}
    if not isinstance(row, dict) or row.get("status") != "ok":
        return {"status": "error", "reason": "incomplete worker result"}
    row["worker_wall_s"] = time.perf_counter() - begin
    return row


def gate(ratios, limit):
    """Conservative observed gate; mixed/noisy or <6 pairs never pass."""
    if len(ratios) < 6 or any(not math.isfinite(value) or value <= 0 for value in ratios):
        return "inconclusive"
    if all(value <= limit for value in ratios):
        return "observed-within-limit"
    if all(value > limit for value in ratios):
        return "observed-breach"
    return "inconclusive"


def valid_receipt(row):
    elapsed = row.get("metrics", {}).get("authenticated_pipeline_s")
    return (row.get("instrumentation") is None and type(elapsed) in (int, float)
            and math.isfinite(elapsed) and elapsed > 0
            and type(row.get("rss_peak_bytes")) is int and row["rss_peak_bytes"] > 0)


def summarize(samples):
    groups = {}
    for row in samples:
        key = f'{row["method"]}/{row["selection"]}/{row["valid_time"]}/{row["cache"]}'
        bucket = groups.setdefault(key, {}).setdefault(row["label"], {})
        if row["repeat"] in bucket:
            raise ValueError("duplicate acceptance observation")
        bucket[row["repeat"]] = row
    summary = {}
    for key, labels in groups.items():
        if set(labels) != {"before", "after"}:
            raise ValueError("unpaired acceptance samples")
        before, after = labels["before"], labels["after"]
        repeats = sorted(before)
        if set(repeats) != set(after):
            raise ValueError("unpaired acceptance samples")
        summary[key] = {}
        for metric, limit in (("authenticated_pipeline_s", 1.05), ("rss_peak_bytes", 1.10)):
            def value(row):
                return row[metric] if metric == "rss_peak_bytes" else row["metrics"][metric]
            x, y = [value(before[r]) for r in repeats], [value(after[r]) for r in repeats]
            ratios = [b / a for a, b in zip(x, y)]
            summary[key][metric] = {
                "before": {"individual": x, "median": statistics.median(x), "maximum": max(x),
                           "p95": sorted(x)[math.ceil(.95 * len(x)) - 1]},
                "after": {"individual": y, "median": statistics.median(y), "maximum": max(y),
                          "p95": sorted(y)[math.ceil(.95 * len(y)) - 1]},
                "paired_ratios": ratios, "median_ratio": statistics.median(y) / statistics.median(x),
                "limit": limit, "gate": gate(ratios, limit),
            }
    relative = {}
    for key, labels in groups.items():
        method, selection, valid_time, cache = key.split("/")
        ordinary = groups.get(f"ordinary/{selection}/{valid_time}/{cache}")
        if method != "native-authenticated-locator" or selection == "all" or ordinary is None:
            continue
        native, base = labels["after"], ordinary["after"]
        latency = [native[r]["metrics"]["authenticated_pipeline_s"] /
                   base[r]["metrics"]["authenticated_pipeline_s"] for r in sorted(native)]
        rss = [native[r]["rss_peak_bytes"] / base[r]["rss_peak_bytes"] for r in sorted(native)]
        relative[key] = {"latency_ratios": latency, "rss_ratios": rss, "limit": 1.25,
                         "latency_gate": gate(latency, 1.25), "rss_gate": gate(rss, 1.25)}
    return summary, relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, default=Path.cwd())
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--history-rows", type=int)
    parser.add_argument("--commit-batch", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=6)
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--selections", nargs="+", choices=SELECTIONS, default=list(SELECTIONS))
    parser.add_argument("--times", nargs="+", type=int, choices=TIMES, default=[50])
    parser.add_argument("--caches", nargs="+", choices=("first", "warm"), default=["first"])
    parser.add_argument("--after-history-batch-size", type=int)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (not 1 <= args.rows <= 1_000_000 or args.commit_batch < 1 or args.repeats < 1
            or not 0 < args.timeout < math.inf
            or (args.history_rows is not None and not 1 <= args.history_rows <= args.rows)
            or (args.after_history_batch_size is not None and not 1 <= args.after_history_batch_size <= 65536)):
        parser.error("invalid bounded workload, repeat count, timeout or batch size")
    if any(len(values) != len(set(values)) for values in (args.methods, args.selections, args.times, args.caches)):
        parser.error("comparison dimensions must not contain duplicates")
    roots = {"before": args.before.resolve(), "after": args.after.resolve()}
    report = {
        "schema_version": 1, "complete": False, "all_parity_verified": False, "samples": [],
        "configuration": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "command": [sys.executable, "-m", "benchmarks.reasoning_hsa_acceptance", *sys.argv[1:]],
        "sources": {label: provenance(root) for label, root in roots.items()},
        "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version, "platform": platform.platform(),
        "dependencies": {name: importlib.metadata.version(name)
                         for name in ("gestaltdb", "pyrex-rocksdb", "pyarrow")},
        "boundary": "unprofiled isolated workers; one after-written fixture; exact shared fact/version receipts; "
                    "alternating before/after and rotating methods; uncontrolled OS caches; not OS-cold",
        "gate_boundary": "observed ratios only, not statistical confidence/general latency guarantees; "
                         "at least six pairs and every ratio within the bound to pass the observed gate; "
                         "mixed results are inconclusive, not passes",
        "decision": "manual HSA-06 review required; ordinary default unchanged; no automatic adoption",
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=args.output.parent, prefix=args.output.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
            temporary = Path(stream.name)
        temporary.replace(args.output)

    save()
    with tempfile.TemporaryDirectory(prefix="hsa-paired-", dir="/tmp/opencode") as directory:
        path = Path(directory) / "db"
        report["fixture_writer"] = "after"
        report["fixture"] = run_worker(roots["after"], "load", {
            "path": str(path), "rows": args.rows, "history_rows": args.history_rows,
            "commit_batch": args.commit_batch, "instrumented": False}, args.timeout)
        save()
        if report["fixture"]["status"] != "ok":
            raise SystemExit("fixture failed; incomplete report retained without summaries")
        (path.parent / "fixture.json").write_text(json.dumps(report["fixture"]))
        for repeat in range(args.repeats):
            offset = repeat % len(args.methods)
            methods = args.methods[offset:] + args.methods[:offset]
            labels = ("before", "after") if repeat % 2 == 0 else ("after", "before")
            for valid_time in args.times:
                for selection in args.selections:
                    for cache in args.caches:
                        for method in methods:
                            pair = []
                            for label in labels:
                                arguments = dict(path=str(path), rows=args.rows, method=method,
                                    selection=selection, valid_time=valid_time, cache=cache, instrumented=False)
                                if label == "after" and method == "native-authenticated-locator" and args.after_history_batch_size:
                                    arguments["locator_history_batch_size"] = args.after_history_batch_size
                                row = run_worker(roots[label], "read", arguments, args.timeout)
                                row.update(label=label, repeat=repeat, method=method, selection=selection,
                                           valid_time=valid_time, cache=cache, pair_order=list(labels),
                                           method_order=list(methods), sequence=len(report["samples"]))
                                report["samples"].append(row)
                                save()
                                if row["status"] != "ok":
                                    raise SystemExit("worker failed; incomplete report retained without summaries")
                                if not valid_receipt(row):
                                    raise SystemExit("invalid timing/profiling receipt; incomplete report retained")
                                pair.append(row)
                            if pair[0]["evidence_digest"] != pair[1]["evidence_digest"]:
                                raise SystemExit("paired fact/version parity failed; incomplete report retained")
        report["sources_final"] = {label: provenance(root) for label, root in roots.items()}
        save()
        for label in roots:
            if any(report["sources_final"][label][field] != report["sources"][label][field]
                   for field in ("revision", "library_sha256", "harness_sha256")):
                raise SystemExit("source changed during acceptance; incomplete report retained")
        report["summary"], report["selective_relative_to_ordinary"] = summarize(report["samples"])
        report["complete"] = report["all_parity_verified"] = True
        save()
    print(json.dumps({"complete": True, "samples": len(report["samples"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
