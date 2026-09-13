"""Result writers and HTML reporting."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import statistics
from typing import Any, Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .results import AgentBenchmarkResult


RESULT_FIELDS = [
    "run_id",
    "benchmark_id",
    "benchmark_name",
    "repetition",
    "status",
    "model",
    "judge_model",
    "benchmark_skill_version",
    "benchmark_task_hash",
    "repo_commit_hash",
    "repo_dirty",
    "opencode_version",
    "parallelism_mode",
    "auto_approve",
    "wall_seconds",
    "tokens_input",
    "tokens_output",
    "tokens_total",
    "cost_usd",
    "tool_call_count",
    "session_id",
    "run_dir",
    "patch_path",
    "session_export_path",
    "events_path",
    "trace_path",
    "error",
]


class BenchmarkResultWriter:
    """Write benchmark results in JSONL and CSV formats."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = output_dir / "results.jsonl"
        self.csv_path = output_dir / "results.csv"

    def write(self, results: Iterable[AgentBenchmarkResult]) -> None:
        rows = [result.to_dict() for result in results]
        with self.jsonl_path.open("w", encoding="utf-8") as jsonl_file:
            for row in rows:
                jsonl_file.write(json.dumps(row, sort_keys=True) + "\n")
        with self.csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=RESULT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def summarize_results(results: list[AgentBenchmarkResult]) -> dict[str, Any]:
    """Create a compact summary suitable for JSON and HTML reports."""
    summary: dict[str, Any] = {
        "total_runs": len(results),
        "passed_runs": sum(1 for result in results if result.status == "passed"),
        "failed_runs": sum(1 for result in results if result.status != "passed"),
        "by_benchmark": {},
        "remediation_actions": [],
    }
    grouped: dict[str, list[AgentBenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(result.benchmark_id, []).append(result)
    for benchmark_id, group in grouped.items():
        wall = [r.wall_seconds for r in group if isinstance(r.wall_seconds, (int, float))]
        tokens = [r.tokens_total for r in group if isinstance(r.tokens_total, int)]
        summary["by_benchmark"][benchmark_id] = {
            "runs": len(group),
            "passed": sum(1 for result in group if result.status == "passed"),
            "failed": sum(1 for result in group if result.status != "passed"),
            "wall_seconds_mean": statistics.mean(wall) if wall else None,
            "tokens_total_mean": statistics.mean(tokens) if tokens else None,
        }
    summary["remediation_actions"] = sorted(
        {
            action
            for result in results
            for action in result.analysis.remediation_actions
        }
    )
    return summary


def write_summary(output_dir: Path, summary: dict[str, Any]) -> None:
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def render_html_report(skill_root: Path, output_dir: Path, results: list[AgentBenchmarkResult], summary: dict[str, Any]) -> Path:
    """Render the benchmark HTML report with Jinja2."""
    env = Environment(
        loader=FileSystemLoader(skill_root / "scripts" / "agent_benchmarking" / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(results=results, summary=summary)
    output_path = output_dir / "index.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path
