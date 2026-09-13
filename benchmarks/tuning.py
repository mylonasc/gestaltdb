"""Run a RocksDB parameter tuning campaign for GestaltDB.

The script benchmarks LevelDB and a matrix of PyRex/RocksDB settings using the
standard quick ingestion and sampling workload. It writes machine-readable JSON
and CSV so results can be compared over time.
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
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

RATE_RE = re.compile(r"^(?P<name>[a-z_]+): (?P<value>[0-9,]+) (?P<unit>.+)$")
TIME_RE = re.compile(r"^(?P<label>.+): (?P<seconds>[0-9.]+)s$")


def parse_output(output: str) -> dict[str, Any]:
    """Parse the quick benchmark output into metrics."""
    metrics: dict[str, Any] = {}
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


def benchmark_config(args: argparse.Namespace, name: str, extra_flags: list[str]) -> dict[str, Any]:
    """Run one benchmark configuration."""
    quick_script = str(ROOT / "benchmarks" / "quick.py")
    command = [
        sys.executable,
        quick_script,
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
        "--seed",
        str(args.seed),
    ]
    if args.append_only:
        command.append("--append-only")
    command.extend(extra_flags)

    env = dict(os.environ)
    stdout, wall_time = run_command(command, env)
    result = parse_output(stdout)
    result["name"] = name
    result["flags"] = extra_flags
    result["wall_time_s"] = wall_time
    return result


def default_configurations() -> list[tuple[str, list[str]]]:
    """Return standard list of tuning configurations to sweep."""
    return [
        ("leveldb", []),
        ("rocksdb-defaults", []),
        ("rocksdb-wal-disabled", ["--rocksdb-disable-wal"]),
        ("rocksdb-parallel-2", ["--rocksdb-parallelism", "2"]),
        ("rocksdb-parallel-4", ["--rocksdb-parallelism", "4"]),
        ("rocksdb-parallel-8", ["--rocksdb-parallelism", "8"]),
        ("rocksdb-buffer-64mb", ["--rocksdb-write-buffer-size", str(64 * 1024 * 1024)]),
        ("rocksdb-buffer-128mb", ["--rocksdb-write-buffer-size", str(128 * 1024 * 1024)]),
        ("rocksdb-buffer-256mb", ["--rocksdb-write-buffer-size", str(256 * 1024 * 1024)]),
        ("rocksdb-bloom-10", ["--rocksdb-bloom-bits", "10"]),
        ("rocksdb-bloom-14", ["--rocksdb-bloom-bits", "14"]),
        (
            "rocksdb-parallel4-buffer64mb-bloom10",
            [
                "--rocksdb-parallelism",
                "4",
                "--rocksdb-write-buffer-size",
                str(64 * 1024 * 1024),
                "--rocksdb-bloom-bits",
                "10",
            ],
        ),
        (
            "rocksdb-parallel8-buffer128mb-bloom10",
            [
                "--rocksdb-parallelism",
                "8",
                "--rocksdb-write-buffer-size",
                str(128 * 1024 * 1024),
                "--rocksdb-bloom-bits",
                "10",
            ],
        ),
        (
            "rocksdb-parallel8-buffer128mb-bloom10-nowal",
            [
                "--rocksdb-parallelism",
                "8",
                "--rocksdb-write-buffer-size",
                str(128 * 1024 * 1024),
                "--rocksdb-bloom-bits",
                "10",
                "--rocksdb-disable-wal",
            ],
        ),
        (
            "rocksdb-transactional-parallel4-buffer64mb-bloom10",
            [
                "--rocksdb-transactional",
                "--rocksdb-parallelism",
                "4",
                "--rocksdb-write-buffer-size",
                str(64 * 1024 * 1024),
                "--rocksdb-bloom-bits",
                "10",
            ],
        ),
    ]


def write_results(output_prefix: Path, results: list[dict[str, Any]]) -> None:
    """Save tuning results as JSON and CSV."""
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)

    fields = [
        "name",
        "node_insert_rate",
        "edge_insert_rate",
        "neighbor_sample_rate",
        "typed_path_sample_rate",
        "wall_time_s",
        "flags",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for res in results:
            row = {
                "name": res["name"],
                "node_insert_rate": res.get("node_insert_rate", ""),
                "edge_insert_rate": res.get("edge_insert_rate", ""),
                "neighbor_sample_rate": res.get("neighbor_sample_rate", ""),
                "typed_path_sample_rate": res.get("typed_path_sample_rate", ""),
                "wall_time_s": f"{res['wall_time_s']:.3f}",
                "flags": " ".join(res.get("flags", [])),
            }
            writer.writerow(row)


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build or configure argument parser for RocksDB tuning."""
    parser = subparser or argparse.ArgumentParser(description="Run RocksDB tuning parameter sweep")
    parser.add_argument("--nodes", type=int, default=20_000)
    parser.add_argument("--edges", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--samples", type=int, default=1_000)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--append-only", action="store_true", default=True)
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("benchmark_results/rocksdb_tuning"),
        help="Prefix for output files (.json and .csv will be appended)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configs = default_configurations()
    results: list[dict[str, Any]] = []

    for name, flags in configs:
        print(f"Running config: {name} ({' '.join(flags)})")
        res = benchmark_config(args, name, flags)
        results.append(res)
        print(
            f"  edge_insert_rate: {res.get('edge_insert_rate', 0):,.0f} edges/s | "
            f"typed_path_sample_rate: {res.get('typed_path_sample_rate', 0):,.0f} seeds/s | "
            f"wall_time: {res['wall_time_s']:.2f}s"
        )

    write_results(args.output_prefix, results)
    print(f"\nTuning results written to {args.output_prefix}.json and {args.output_prefix}.csv")


if __name__ == "__main__":
    main()
