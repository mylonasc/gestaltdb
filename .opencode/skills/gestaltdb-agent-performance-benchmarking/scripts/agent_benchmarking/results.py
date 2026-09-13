"""Benchmark result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
class TraceAnalysis:
    """Offline analysis of an agent trace and benchmark artifacts."""

    status: str = "not_run"
    summary: str = ""
    self_correction_count: int = 0
    suspected_error_patterns: list[str] = field(default_factory=list)
    library_misuse_signals: list[str] = field(default_factory=list)
    invalid_code_signals: list[str] = field(default_factory=list)
    documentation_gaps: list[str] = field(default_factory=list)
    remediation_actions: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)


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
    run_started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_finished_at: str = ""
    wall_seconds: float | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_total: int | None = None
    model_context_configured: int | None = None
    model_context_available: int | None = None
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
    trace_path: str = ""
    analysis: TraceAnalysis = field(default_factory=TraceAnalysis)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
