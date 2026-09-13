#!/usr/bin/env python3
"""Browse the local GestaltDB agent benchmark SQLite database."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from agent_benchmarking.storage import BenchmarkDB  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Browse local agent benchmark SQLite results.")
    parser.add_argument("--db-path", type=Path, default=Path("agent_benchmark_results/agent_benchmarks.sqlite"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    recent = subparsers.add_parser("recent", help="show recent runs")
    recent.add_argument("--limit", type=int, default=20)
    subparsers.add_parser("remediation", help="summarize remediation actions")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with BenchmarkDB(args.db_path) as db:
        if args.command == "recent":
            for row in db.recent_runs(limit=args.limit):
                print(
                    f"{row['run_started_at'] or ''}\t{row['status']}\t{row['benchmark_id']}\t"
                    f"{row['model']}\t{row['repo_commit_hash'][:8]}\t{row['run_id']}"
                )
                if row["analysis_summary"]:
                    print(f"  analysis: {row['analysis_summary']}")
        elif args.command == "remediation":
            for row in db.remediation_summary():
                print(f"{row['occurrences']}\t{row['action']}\t[{row['benchmarks'] or ''}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
