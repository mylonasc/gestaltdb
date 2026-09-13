"""Benchmark result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass
class ValidationResult:
    """Result of deterministic validation commands."""

    status: str
    command: str = ""
    returncode: int | None = None
    stdout_path: str = ""
    stderr_path: str = ""
    duration_seconds: float | None = None


@dataclass
class JudgeResult:
    """Result of LLM-as-judge evaluation."""

    enabled: bool
    status: str
    score: int | None = None
    max_score: int = 5
    passed: bool | None = None
    reasoning: str = ""
    raw_output_path: str = ""


@dataclass
class AgentBenchmarkResult:
    """Serializable result for one benchmark repetition."""

    run_id: str
    benchmark_id: str
    benchmark_name: str
    repetition: int
    status: str
    model: str
    judge_model: str
    benchmark_skill_version: str
    benchmark_task_hash: str
    repo_commit_hash: str
    repo_dirty: bool
    opencode_version: str
    parallelism_mode: str
    auto_approve: bool
    wall_seconds: float | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_total: int | None = None
    cost_usd: float | None = None
    tool_call_count: int = 0
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    tool_call_trace: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(status="not_run"))
    judge: JudgeResult = field(default_factory=lambda: JudgeResult(enabled=False, status="not_run"))
    run_dir: str = ""
    worktree_path: str = ""
    patch_path: str = ""
    session_export_path: str = ""
    events_path: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
