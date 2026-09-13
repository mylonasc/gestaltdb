#!/usr/bin/env python3
"""Inspect existing benchmark traces and write remediation summaries."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from agent_benchmarking.trace_analysis import inspect_results_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect GestaltDB agent benchmark traces.")
    parser.add_argument("results_dir", type=Path, help="benchmark results directory containing runs/")
    args = parser.parse_args(argv)
    rows = inspect_results_dir(args.results_dir)
    print(f"inspected {len(rows)} runs")
    print(f"summary: {args.results_dir / 'trace-inspection-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
