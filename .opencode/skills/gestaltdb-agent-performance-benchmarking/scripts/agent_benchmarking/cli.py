"""Command-line interface for GestaltDB agentic benchmarks."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import BenchmarkRunnerConfig
from .runner import BenchmarkRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run GestaltDB agentic coding efficiency benchmarks.")
    parser.add_argument("--model", default="ollama/gemma4:26b", help="opencode model id for coding agents")
    parser.add_argument("--judge-model", default=None, help="model id for LLM-as-judge; defaults to --model")
    parser.add_argument("--benchmarks", nargs="*", default=None, help="benchmark ids to run; defaults to all")
    parser.add_argument("--repetitions", type=int, default=1, help="number of repetitions per benchmark")
    parser.add_argument("--timeout-seconds", type=int, default=None, help="override per-task opencode timeout")
    parser.add_argument("--parallelism", choices=("sequential", "parallel"), default="sequential")
    parser.add_argument("--max-workers", type=int, default=1, help="parallel worker count when --parallelism parallel")
    parser.add_argument("--output-dir", type=Path, default=Path("agent_benchmark_results"))
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite database path; defaults to agent_benchmark_results/agent_benchmarks.sqlite",
    )
    parser.add_argument("--worktree-base", type=Path, default=None, help="directory for temporary git worktrees")
    parser.add_argument("--preserve-worktrees", action="store_true", help="keep worktrees after each run")
    parser.add_argument("--html", action="store_true", help="render index.html report")
    parser.add_argument("--dry-run", action="store_true", help="exercise runner/reporting without calling opencode")
    parser.add_argument("--no-auto", dest="auto", action="store_false", help="do not pass --auto to opencode run")
    parser.set_defaults(auto=True)
    parser.add_argument("--no-judge", dest="judge_enabled", action="store_false", help="disable LLM-as-judge")
    parser.set_defaults(judge_enabled=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    script_path = Path(__file__).resolve()
    skill_root = script_path.parents[2]
    repo_root = skill_root.parents[2]
    max_workers = args.max_workers if args.parallelism == "parallel" else 1
    if args.repetitions < 1:
        parser.error("--repetitions must be >= 1")
    if max_workers < 1:
        parser.error("--max-workers must be >= 1")
    config = BenchmarkRunnerConfig(
        repo_root=repo_root,
        skill_root=skill_root,
        output_dir=args.output_dir,
        db_path=args.db_path,
        model=args.model,
        judge_model=args.judge_model,
        benchmarks=args.benchmarks,
        repetitions=args.repetitions,
        timeout_seconds=args.timeout_seconds,
        parallelism=args.parallelism,
        max_workers=max_workers,
        auto=args.auto,
        judge_enabled=args.judge_enabled,
        html=args.html,
        dry_run=args.dry_run,
        preserve_worktrees=args.preserve_worktrees,
        worktree_base=args.worktree_base,
    )
    results = BenchmarkRunner(config).run()
    passed = sum(1 for result in results if result.status == "passed")
    print(f"agent benchmarks complete: {passed}/{len(results)} passed")
    print(f"results: {config.output_dir}")
    print(f"sqlite db: {config.effective_db_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
