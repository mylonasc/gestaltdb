"""SQLite persistence for agent benchmark results.

The database is intentionally simple and local. It indexes structured metadata
while large, editable artifacts remain as plain files under each results
directory.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .results import AgentBenchmarkResult


SCHEMA_VERSION = 1


class BenchmarkDB:
    """Small SQLite index for benchmark runs and artifacts."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "BenchmarkDB":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                run_id TEXT PRIMARY KEY,
                benchmark_id TEXT NOT NULL,
                benchmark_name TEXT NOT NULL,
                repetition INTEGER NOT NULL,
                status TEXT NOT NULL,
                model TEXT NOT NULL,
                judge_model TEXT NOT NULL,
                benchmark_skill_version TEXT NOT NULL,
                benchmark_task_hash TEXT NOT NULL,
                repo_commit_hash TEXT NOT NULL,
                repo_dirty INTEGER NOT NULL,
                opencode_version TEXT NOT NULL,
                parallelism_mode TEXT NOT NULL,
                auto_approve INTEGER NOT NULL,
                run_started_at TEXT,
                run_finished_at TEXT,
                ingested_at TEXT NOT NULL,
                wall_seconds REAL,
                tokens_input INTEGER,
                tokens_output INTEGER,
                tokens_total INTEGER,
                cost_usd REAL,
                tool_call_count INTEGER NOT NULL,
                tool_call_counts_json TEXT NOT NULL,
                session_id TEXT,
                validation_status TEXT,
                validation_command TEXT,
                validation_returncode INTEGER,
                judge_status TEXT,
                judge_score INTEGER,
                judge_passed INTEGER,
                analysis_summary TEXT,
                analysis_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS run_artifacts (
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                PRIMARY KEY (run_id, kind),
                FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS remediation_actions (
                run_id TEXT NOT NULL,
                action TEXT NOT NULL,
                documentation_gap TEXT,
                PRIMARY KEY (run_id, action),
                FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_benchmark_runs_commit ON benchmark_runs(repo_commit_hash);
            CREATE INDEX IF NOT EXISTS idx_benchmark_runs_started ON benchmark_runs(run_started_at);
            CREATE INDEX IF NOT EXISTS idx_benchmark_runs_status ON benchmark_runs(status);
            CREATE INDEX IF NOT EXISTS idx_benchmark_runs_benchmark ON benchmark_runs(benchmark_id);
            """
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self.conn.commit()

    def upsert_results(self, results: Iterable[AgentBenchmarkResult]) -> None:
        for result in results:
            self.upsert_result(result, commit=False)
        self.conn.commit()

    def upsert_result(self, result: AgentBenchmarkResult, commit: bool = True) -> None:
        data = result.to_dict()
        ingested_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO benchmark_runs (
                run_id, benchmark_id, benchmark_name, repetition, status, model, judge_model,
                benchmark_skill_version, benchmark_task_hash, repo_commit_hash, repo_dirty,
                opencode_version, parallelism_mode, auto_approve, run_started_at, run_finished_at,
                ingested_at, wall_seconds, tokens_input, tokens_output, tokens_total, cost_usd,
                tool_call_count, tool_call_counts_json, session_id, validation_status,
                validation_command, validation_returncode, judge_status, judge_score, judge_passed,
                analysis_summary, analysis_json, result_json, error
            ) VALUES (
                :run_id, :benchmark_id, :benchmark_name, :repetition, :status, :model, :judge_model,
                :benchmark_skill_version, :benchmark_task_hash, :repo_commit_hash, :repo_dirty,
                :opencode_version, :parallelism_mode, :auto_approve, :run_started_at, :run_finished_at,
                :ingested_at, :wall_seconds, :tokens_input, :tokens_output, :tokens_total, :cost_usd,
                :tool_call_count, :tool_call_counts_json, :session_id, :validation_status,
                :validation_command, :validation_returncode, :judge_status, :judge_score, :judge_passed,
                :analysis_summary, :analysis_json, :result_json, :error
            )
            """,
            {
                "run_id": result.run_id,
                "benchmark_id": result.benchmark_id,
                "benchmark_name": result.benchmark_name,
                "repetition": result.repetition,
                "status": result.status,
                "model": result.model,
                "judge_model": result.judge_model,
                "benchmark_skill_version": result.benchmark_skill_version,
                "benchmark_task_hash": result.benchmark_task_hash,
                "repo_commit_hash": result.repo_commit_hash,
                "repo_dirty": int(result.repo_dirty),
                "opencode_version": result.opencode_version,
                "parallelism_mode": result.parallelism_mode,
                "auto_approve": int(result.auto_approve),
                "run_started_at": result.run_started_at,
                "run_finished_at": result.run_finished_at,
                "ingested_at": ingested_at,
                "wall_seconds": result.wall_seconds,
                "tokens_input": result.tokens_input,
                "tokens_output": result.tokens_output,
                "tokens_total": result.tokens_total,
                "cost_usd": result.cost_usd,
                "tool_call_count": result.tool_call_count,
                "tool_call_counts_json": json.dumps(result.tool_call_counts, sort_keys=True),
                "session_id": result.session_id,
                "validation_status": result.validation.status,
                "validation_command": result.validation.command,
                "validation_returncode": result.validation.returncode,
                "judge_status": result.judge.status,
                "judge_score": result.judge.score,
                "judge_passed": _bool_or_none(result.judge.passed),
                "analysis_summary": result.analysis.summary,
                "analysis_json": json.dumps(asdict(result.analysis), sort_keys=True),
                "result_json": json.dumps(data, sort_keys=True),
                "error": result.error,
            },
        )
        self._replace_artifacts(result)
        self._replace_remediation_actions(result)
        if commit:
            self.conn.commit()

    def _replace_artifacts(self, result: AgentBenchmarkResult) -> None:
        self.conn.execute("DELETE FROM run_artifacts WHERE run_id = ?", (result.run_id,))
        artifacts = {
            "run_dir": result.run_dir,
            "worktree": result.worktree_path,
            "patch": result.patch_path,
            "session_export": result.session_export_path,
            "events": result.events_path,
            "trace": result.trace_path,
            "validation_stdout": result.validation.stdout_path,
            "validation_stderr": result.validation.stderr_path,
            "judge_raw": result.judge.raw_output_path,
        }
        for kind, path in artifacts.items():
            if path:
                self.conn.execute(
                    "INSERT OR REPLACE INTO run_artifacts(run_id, kind, path) VALUES (?, ?, ?)",
                    (result.run_id, kind, path),
                )

    def _replace_remediation_actions(self, result: AgentBenchmarkResult) -> None:
        self.conn.execute("DELETE FROM remediation_actions WHERE run_id = ?", (result.run_id,))
        gaps = result.analysis.documentation_gaps or [None]
        for action in result.analysis.remediation_actions:
            self.conn.execute(
                "INSERT OR REPLACE INTO remediation_actions(run_id, action, documentation_gap) VALUES (?, ?, ?)",
                (result.run_id, action, gaps[0]),
            )

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT run_started_at, run_id, benchmark_id, status, model, repo_commit_hash,
                       tokens_total, tool_call_count, analysis_summary
                FROM benchmark_runs
                ORDER BY COALESCE(run_started_at, ingested_at) DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    def remediation_summary(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT action, COUNT(*) AS occurrences, GROUP_CONCAT(DISTINCT benchmark_id) AS benchmarks
                FROM remediation_actions
                JOIN benchmark_runs USING (run_id)
                GROUP BY action
                ORDER BY occurrences DESC, action ASC
                """
            )
        )


def _bool_or_none(value: bool | None) -> int | None:
    if value is None:
        return None
    return int(value)
