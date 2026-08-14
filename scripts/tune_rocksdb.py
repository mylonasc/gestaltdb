#!/usr/bin/env python3
"""Run a small RocksDB tuning campaign for GestaltDB.

The script benchmarks LevelDB and a matrix of PyRex/RocksDB settings using the
same public ``benchmarks.py`` workload. It writes machine-readable JSON and CSV
so results can be compared over time.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


RATE_RE = re.compile(r"^(?P<name>[a-z_]+): (?P<value>[0-9,]+) (?P<unit>.+)$")
TIME_RE = re.compile(r"^(?P<label>.+): (?P<seconds>[0-9.]+)s$")


def parse_output(output: str) -> dict[str, object]:
    """Parse the benchmark output into a metrics dictionary."""
    metrics: dict[str, object] = {}
    timings: dict[str, float] = {}
    for line in output.splitlines():
        line = line.strip()
        rate_match = RATE_RE.match(line)
        if rate_match:
            metrics[rate_match.group("name")] = float(rate_match.group("value").replace(",", ""))
            continue
        time_match = TIME_RE.match(line)
        if time_match:
            timings[time_match.group("label")] = float(time_match.group("seconds"))
    metrics["timings"] = timings
    return metrics


def run_command(command: list[str], env: dict[str, str]) -> tuple[str, float]:
    """Run a benchmark command and return stdout plus elapsed wall time."""
    start = time.perf_counter()
    completed = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    return completed.stdout, time.perf_counter() - start


def benchmark_config(args, name: str, extra_flags: list[str]) -> dict[str, object]:
    """Run one benchmark configuration."""
    command = [
        sys.executable,
        "benchmarks.py",
        "--backend",
        "leveldb" if name == "leveldb" else "rocksdb",
        "--nodes",
        str(args.nodes),
        "--edges",
        str(args.edges),
        "--batch-size",
        str(args.batch_size),
        "--samples",
        str(args.samples),
        "--sample-size",
        str(args.sample_size),
        "--append-only",
    ] + extra_flags
    stdout, wall_time = run_command(command, os.environ.copy())
    metrics = parse_output(stdout)
    metrics.update({"name": name, "wall_time": wall_time, "stdout": stdout, "command": command})
    return metrics


def profile_config(args, output_dir: Path, name: str, extra_flags: list[str]) -> None:
    """Collect cProfile data for one RocksDB benchmark configuration."""
    profile_path = output_dir / f"{name}.prof"
    command = [
        sys.executable,
        "-m",
        "cProfile",
        "-o",
        str(profile_path),
        "benchmarks.py",
        "--backend",
        "rocksdb",
        "--nodes",
        str(args.nodes),
        "--edges",
        str(args.edges),
        "--batch-size",
        str(args.batch_size),
        "--samples",
        str(args.samples),
        "--sample-size",
        str(args.sample_size),
        "--append-only",
    ] + extra_flags
    subprocess.run(command, check=True)


def rocksdb_matrix() -> list[tuple[str, list[str]]]:
    """Return the RocksDB tuning matrix."""
    return [
        ("rocksdb-default", []),
        ("rocksdb-bloom10", ["--rocksdb-bloom-bits", "10"]),
        ("rocksdb-parallel4", ["--rocksdb-parallelism", "4", "--rocksdb-max-background-jobs", "4"]),
        ("rocksdb-buffer64mb", ["--rocksdb-write-buffer-size", str(64 * 1024 * 1024)]),
        (
            "rocksdb-parallel4-buffer64mb-bloom10",
            [
                "--rocksdb-parallelism",
                "4",
                "--rocksdb-max-background-jobs",
                "4",
                "--rocksdb-write-buffer-size",
                str(64 * 1024 * 1024),
                "--rocksdb-bloom-bits",
                "10",
            ],
        ),
        (
            "rocksdb-parallel4-buffer64mb-bloom10-nowal",
            [
                "--rocksdb-parallelism",
                "4",
                "--rocksdb-max-background-jobs",
                "4",
                "--rocksdb-write-buffer-size",
                str(64 * 1024 * 1024),
                "--rocksdb-bloom-bits",
                "10",
                "--rocksdb-disable-wal",
            ],
        ),
    ]


def write_results(output_dir: Path, results: list[dict[str, object]]) -> None:
    """Write tuning results to JSON and CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "rocksdb_tuning_results.json"
    csv_path = output_dir / "rocksdb_tuning_results.csv"
    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    fields = [
        "name",
        "node_insert_rate",
        "edge_insert_rate",
        "neighbor_sample_rate",
        "typed_path_sample_rate",
        "wall_time",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result.get(field) for field in fields})


def main() -> None:
    """Run the tuning campaign."""
    parser = argparse.ArgumentParser(description="Tune PyRex/RocksDB for GestaltDB workloads")
    parser.add_argument("--nodes", type=int, default=20_000)
    parser.add_argument("--edges", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--samples", type=int, default=1_000)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--output-dir", default="benchmark_results")
    parser.add_argument("--profile", action="store_true", help="write cProfile data for the best RocksDB ingestion config")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    configs = [("leveldb", [])] + rocksdb_matrix()
    results = []
    for name, flags in configs:
        print(f"Running {name}...")
        result = benchmark_config(args, name, flags)
        results.append(result)
        print(result["stdout"])

    write_results(output_dir, results)

    rocksdb_results = [result for result in results if str(result["name"]).startswith("rocksdb")]
    best_rocksdb = max(rocksdb_results, key=lambda result: float(result.get("edge_insert_rate", 0)))
    best_leveldb = next(result for result in results if result["name"] == "leveldb")
    print("Best RocksDB config:", best_rocksdb["name"], best_rocksdb.get("edge_insert_rate"), "edges/s")
    print("LevelDB baseline:", best_leveldb.get("edge_insert_rate"), "edges/s")

    if args.profile:
        profile_flags = next(flags for name, flags in rocksdb_matrix() if name == best_rocksdb["name"])
        profile_config(args, output_dir, str(best_rocksdb["name"]), profile_flags)
        print(f"Profile written under {output_dir}")


if __name__ == "__main__":
    main()
