"""Unified CLI dispatcher for GestaltDB benchmark suites."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import (
    arcadedb,
    compaction,
    embedded,
    external,
    matrix,
    plotting,
    profiling,
    quick,
    sampler,
    temporal,
    tuning,
)

COMMANDS = {
    "quick": (quick.build_parser, quick.run_benchmark, "Run single-run ingestion & sampling microbenchmark"),
    "matrix": (matrix.build_parser, lambda args: matrix.main(sys.argv[2:]), "Run multi-dimensional backend/cores/sizes matrix"),
    "compaction": (compaction.build_parser, lambda args: compaction.main(sys.argv[2:]), "Benchmark LSM overwrite and compaction pressure"),
    "sampler": (sampler.build_parser, lambda args: sampler.main(sys.argv[2:]), "Benchmark SamplerEngine array sampling vs GraphDB baseline"),
    "temporal": (temporal.build_parser, temporal.run_benchmark, "Benchmark temporal storage, queries, sampling, and reasoning"),
    "embedded": (embedded.build_parser, lambda args: embedded.main(sys.argv[2:]), "Benchmark embedded graph databases (GestaltDB, LatticeDB, LadybugDB)"),
    "external": (external.build_parser, lambda args: external.main(sys.argv[2:]), "Benchmark external graph databases (Neo4j, Memgraph, ArcadeDB, AGE)"),
    "arcadedb": (arcadedb.build_parser, lambda args: arcadedb.main(sys.argv[2:]), "Benchmark GestaltDB vs embedded ArcadeDB"),
    "tuning": (tuning.build_parser, lambda args: tuning.main(sys.argv[2:]), "Sweep RocksDB tuning configurations"),
    "profiling": (profiling.build_parser, lambda args: profiling.main(sys.argv[2:]), "Profile end-to-end ingestion phases with cProfile"),
    "plotting": (plotting.build_parser, lambda args: plotting.main(sys.argv[2:]), "Generate SVG charts from external benchmark summary files"),
}


def main(argv: Sequence[str] | None = None) -> None:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0] in {"-h", "--help"}:
        print("GestaltDB Benchmarking Suite\n")
        print("Usage: python -m benchmarks <subcommand> [options]\n")
        print("Available subcommands:")
        for cmd_name, (_, _, desc) in COMMANDS.items():
            print(f"  {cmd_name:<12} {desc}")
        print("\nRun `python -m benchmarks <subcommand> --help` for subcommand options.")
        return

    subcmd = args_list[0]
    if subcmd not in COMMANDS:
        print(f"Unknown benchmark subcommand: {subcmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    parser_builder, runner, _ = COMMANDS[subcmd]
    parser = parser_builder()
    parsed_args = parser.parse_args(args_list[1:])

    # Dispatch to specific benchmark
    if subcmd == "quick":
        quick.run_benchmark(parsed_args)
    elif subcmd == "matrix":
        matrix.main(args_list[1:])
    elif subcmd == "compaction":
        compaction.main(args_list[1:])
    elif subcmd == "sampler":
        sampler.main(args_list[1:])
    elif subcmd == "temporal":
        temporal.main(args_list[1:])
    elif subcmd == "embedded":
        embedded.main(args_list[1:])
    elif subcmd == "external":
        external.main(args_list[1:])
    elif subcmd == "arcadedb":
        arcadedb.main(args_list[1:])
    elif subcmd == "tuning":
        tuning.main(args_list[1:])
    elif subcmd == "profiling":
        profiling.main(args_list[1:])
    elif subcmd == "plotting":
        plotting.main(args_list[1:])


if __name__ == "__main__":
    main()
