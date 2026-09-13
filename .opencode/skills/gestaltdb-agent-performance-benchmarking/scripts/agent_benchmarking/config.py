"""Runner configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkRunnerConfig:
    """Configuration for an agentic coding benchmark run."""

    repo_root: Path
    skill_root: Path
    output_dir: Path
    db_path: Path | None = None
    model: str = "ollama/gemma4:26b"
    judge_model: str | None = None
    benchmarks: list[str] | None = None
    repetitions: int = 1
    timeout_seconds: int | None = None
    parallelism: str = "sequential"
    max_workers: int = 1
    auto: bool = True
    judge_enabled: bool = True
    html: bool = False
    dry_run: bool = False
    preserve_worktrees: bool = False
    worktree_base: Path | None = None
    agent_name: str = "gestaltdb-benchmark-agent"
    judge_agent_name: str = "gestaltdb-benchmark-judge"

    @property
    def effective_judge_model(self) -> str:
        return self.judge_model or self.model

    @property
    def benchmark_dir(self) -> Path:
        return self.skill_root / "agentic-coding-efficiency-benchmarks"

    @property
    def effective_db_path(self) -> Path:
        return self.db_path or (self.repo_root / "agent_benchmark_results" / "agent_benchmarks.sqlite")

    @property
    def skill_version(self) -> str:
        version_file = self.skill_root / "VERSION"
        if not version_file.exists():
            return "unknown"
        return version_file.read_text(encoding="utf-8").strip() or "unknown"
