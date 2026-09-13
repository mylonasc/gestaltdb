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
    "model_context_configured",
    "model_context_available",
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
    trace_views = {result.run_id: _load_trace_view(result) for result in results}
    html = template.render(results=results, summary=summary, trace_views=trace_views)
    output_path = output_dir / "index.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _load_trace_view(result: AgentBenchmarkResult) -> dict[str, Any]:
    events_path = Path(result.events_path) if result.events_path else None
    if not events_path or not events_path.exists():
        return {"events": [], "raw_error": "", "context_configured": result.model_context_configured, "context_available": result.model_context_available}
    events: list[dict[str, Any]] = []
    raw_error = ""
    cumulative_tokens = 0
    for index, line in enumerate(events_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raw_error = f"line {index}: {exc}"
            continue
        if isinstance(event, dict):
            event_tokens = _event_total_tokens(event)
            if event_tokens is not None:
                cumulative_tokens += event_tokens
            events.append(_format_trace_event(index, event, result.model_context_configured, result.model_context_available, cumulative_tokens, event_tokens))
    return {"events": events, "raw_error": raw_error, "context_configured": result.model_context_configured, "context_available": result.model_context_available}


def _format_trace_event(
    index: int,
    event: dict[str, Any],
    context_configured: int | None,
    context_available: int | None,
    cumulative_tokens: int,
    event_tokens: int | None,
) -> dict[str, Any]:
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
    tool_output = state.get("output")
    text = part.get("text") if isinstance(part.get("text"), str) else event.get("text", "")
    event_type = str(event.get("type", ""))
    tool_name = part.get("tool") or event.get("tool") or ""
    title = event_type
    if tool_name:
        title = f"{event_type}: {tool_name}"
    elif text:
        title = f"{event_type}: text"
    return {
        "index": index,
        "timestamp": event.get("timestamp", ""),
        "type": event_type,
        "tool": tool_name,
        "status": state.get("status", event.get("status", "")),
        "title": title,
        "context_configured": context_configured,
        "context_available": context_available,
        "context_used": cumulative_tokens if cumulative_tokens else None,
        "event_tokens": event_tokens,
        "text": _preview(text, 4000),
        "input": json.dumps(tool_input, indent=2, sort_keys=True) if tool_input else "",
        "output": _preview(str(tool_output), 6000) if tool_output is not None else "",
        "raw": json.dumps(event, indent=2, sort_keys=True),
    }


def _event_total_tokens(event: dict[str, Any]) -> int | None:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        message = event.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        part = event.get("part")
        usage = part.get("tokens") if isinstance(part, dict) else None
    if not isinstance(usage, dict):
        return None
    total = _first_int(usage, "total", "total_tokens", "tokens")
    if total is not None:
        return total
    input_tokens = _first_int(usage, "input", "input_tokens", "prompt_tokens", "prompt") or 0
    output_tokens = _first_int(usage, "output", "output_tokens", "completion_tokens", "completion") or 0
    return input_tokens + output_tokens if input_tokens or output_tokens else None


def _first_int(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return None


def _preview(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated {len(value) - limit} characters"
