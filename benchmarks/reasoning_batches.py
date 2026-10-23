"""Fresh-worker compact batch exchange measurements, not inference benchmarks."""

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

from .ontology import _peak_rss, _save_report, _timed


def worker(size, arrow, polars):
    metrics = {}

    def imports():
        import gestaltdb.reasoning.batches

    _timed(metrics, "package_import_s", imports)
    from gestaltdb.reasoning.batches import CompactRelation, RelationState, SymbolDictionary
    from gestaltdb.reasoning.protocols import InputContext
    from gestaltdb.reasoning.relational import RelationSchema

    context = InputContext("benchmark-horizon", "world", "ontology", "policy", "mapping")
    schema = RelationSchema("parent", ("subject", "object"))
    dictionary = _timed(metrics, "dictionary_s", lambda: SymbolDictionary.build(
        context, (f"n{i}" for i in range(size + 1)), max_input_symbols=size + 1, max_symbols=size + 1))
    base = _timed(metrics, "encode_s", lambda: CompactRelation.from_rows(
        schema, dictionary, ((f"n{i}", f"n{i+1}") for i in range(size - 1)),
        max_input_rows=size, max_rows=size))
    candidates = CompactRelation.from_rows(schema, dictionary, [(f"n{size-1}", f"n{size}"), ("n0", "n1")])
    state = _timed(metrics, "advance_s", lambda: RelationState.seed(base).advance(candidates, max_rows=size))
    assert len(state.stable) == size and len(state.delta) == 1
    report = {"status": "ok", "metrics": metrics, "rows": size,
              "column_payload_bytes": state.stable.nbytes,
              "dictionary_symbols": len(dictionary.symbols),
              "dictionary_utf8_bytes": sum(len(s.encode("utf-8")) for s in dictionary.symbols),
              "dictionary_id": dictionary.dictionary_id,
              "execution_kind": "reference-build-and-set-operations-not-inference"}
    if arrow or polars:
        def arrow_import():
            import pyarrow

        _timed(metrics, "arrow_import_s", arrow_import)
        import pyarrow as pa
        from gestaltdb.reasoning.adapters.arrow import from_arrow, to_arrow

        table = _timed(metrics, "arrow_export_s", lambda: to_arrow(state.stable))
        restored = _timed(metrics, "arrow_decode_copy_s", lambda: from_arrow(
            table, schema=schema, dictionary=dictionary, max_input_rows=size, max_rows=size, batch_rows=1024))
        assert restored == state.stable
        report["arrow_payload_shared"] = all(
            table.column(i).chunk(0).buffers()[1].address == pa.py_buffer(buffer).address
            for i, buffer in enumerate(state.stable.buffers))
        report["pyarrow_version"] = pa.__version__
        if polars:
            def polars_import():
                import polars

            _timed(metrics, "polars_import_s", polars_import)
            import polars as pl

            frame = _timed(metrics, "polars_consume_s", lambda: pl.from_arrow(table))
            assert frame.shape == (size, 2)
            report["polars_version"] = pl.__version__
    report["rss_peak_bytes"] = _peak_rss()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--arrow", action="store_true")
    parser.add_argument("--polars", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 2 <= args.rows <= 100_000 or args.repeats < 1:
        parser.error("rows must be 2..100000; repeats must be positive")
    if args.worker:
        print(json.dumps(worker(args.rows, args.arrow, args.polars)))
        return
    root = Path(__file__).resolve().parents[1]
    threads = {key: "1" for key in ("OMP_NUM_THREADS", "POLARS_MAX_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}
    source_files = [Path(__file__), root / "src/gestaltdb/reasoning/batches.py",
                    root / "src/gestaltdb/reasoning/adapters/arrow.py"]
    report = {"schema_version": 1, "python": sys.version, "platform": platform.platform(),
              "threads": threads, "complete": False, "rows": [],
              "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
              "source_sha256": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in source_files}}
    command = [sys.executable, "-m", "benchmarks.reasoning_batches", "--worker", "--rows", str(args.rows)]
    if args.arrow:
        command.append("--arrow")
    if args.polars:
        command.append("--polars")
    for repeat in range(args.repeats):
        start = time.perf_counter()
        try:
            result = subprocess.run(command, cwd=root, env={**os.environ, **threads, "PYTHONHASHSEED": "0"},
                                    capture_output=True, text=True, timeout=30)
            row = json.loads(result.stdout) if result.returncode == 0 else {"status": "error", "reason": result.stderr[-4000:]}
        except subprocess.TimeoutExpired:
            row = {"status": "timeout"}
        report["rows"].append({**row, "repeat": repeat, "worker_wall_s": time.perf_counter() - start})
        _save_report(args.output, report)
    report["complete"] = True
    _save_report(args.output, report)
    passed = all(row["status"] == "ok" for row in report["rows"])
    print(json.dumps({"passed": passed, "repeats": len(report["rows"]), "output": str(args.output)}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
