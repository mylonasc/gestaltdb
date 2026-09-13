"""opencode subprocess integration and event parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import time
from typing import Any

from .benchmark import AgentBenchmark


@dataclass
class OpencodeRunResult:
    """Raw opencode execution result."""

    returncode: int
    wall_seconds: float
    events: list[dict[str, Any]] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    session_id: str = ""
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_total: int | None = None
    cost_usd: float | None = None
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    tool_call_trace: list[dict[str, Any]] = field(default_factory=list)
    timed_out: bool = False

    @property
    def tool_call_count(self) -> int:
        return sum(self.tool_call_counts.values())


def run_opencode_task(
    benchmark: AgentBenchmark,
    worktree: Path,
    model: str,
    agent_name: str,
    auto: bool,
    timeout_seconds: int,
    events_path: Path,
) -> OpencodeRunResult:
    """Run opencode for one coding task."""
    cmd = [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        model,
        "--agent",
        agent_name,
        "--dir",
        str(worktree),
    ]
    if auto:
        cmd.append("--auto")
    cmd.append(benchmark.prompt)

    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        stdout, stderr = proc.communicate()
    wall = time.monotonic() - started
    events_path.write_text(stdout or "", encoding="utf-8")
    stderr_path = events_path.with_suffix(".stderr")
    stderr_path.write_text(stderr or "", encoding="utf-8")
    parsed = parse_opencode_events(stdout or "")
    parsed.returncode = 124 if timed_out else int(proc.returncode or 0)
    parsed.wall_seconds = wall
    parsed.stdout = stdout or ""
    parsed.stderr = stderr or ""
    parsed.timed_out = timed_out
    return parsed


def export_session(session_id: str, worktree: Path, output_path: Path) -> bool:
    """Export an opencode session if a session ID was discovered."""
    if not session_id:
        return False
    proc = subprocess.run(
        ["opencode", "export", session_id],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        output_path.write_text(proc.stderr, encoding="utf-8")
        return False
    output_path.write_text(proc.stdout, encoding="utf-8")
    return True


def parse_opencode_events(output: str) -> OpencodeRunResult:
    """Parse opencode JSON event lines, keeping unknown fields intact."""
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)

    result = OpencodeRunResult(returncode=0, wall_seconds=0.0, events=events)
    for event in events:
        _collect_session_id(event, result)
        _collect_usage(event, result)
        _collect_tool_call(event, result)
    if result.tokens_total is None and result.tokens_input is not None and result.tokens_output is not None:
        result.tokens_total = result.tokens_input + result.tokens_output
    return result


def _collect_session_id(event: dict[str, Any], result: OpencodeRunResult) -> None:
    for key in ("sessionID", "session_id", "sessionId", "id"):
        value = event.get(key)
        if isinstance(value, str) and ("session" in key.lower() or event.get("type") == "session"):
            result.session_id = value
    nested = event.get("session")
    if isinstance(nested, dict):
        value = nested.get("id") or nested.get("sessionID") or nested.get("session_id")
        if isinstance(value, str):
            result.session_id = value


def _collect_usage(event: dict[str, Any], result: OpencodeRunResult) -> None:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        message = event.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        part = event.get("part")
        usage = part.get("tokens") if isinstance(part, dict) else None
    if not isinstance(usage, dict):
        return
    input_tokens = _first_int(usage, "input", "input_tokens", "prompt_tokens", "prompt")
    output_tokens = _first_int(usage, "output", "output_tokens", "completion_tokens", "completion")
    total_tokens = _first_int(usage, "total", "total_tokens", "tokens")
    cost = _first_float(usage, "cost", "cost_usd")
    if input_tokens is not None:
        result.tokens_input = (result.tokens_input or 0) + input_tokens
    if output_tokens is not None:
        result.tokens_output = (result.tokens_output or 0) + output_tokens
    if total_tokens is not None:
        result.tokens_total = (result.tokens_total or 0) + total_tokens
    if cost is not None:
        result.cost_usd = (result.cost_usd or 0.0) + cost


def _collect_tool_call(event: dict[str, Any], result: OpencodeRunResult) -> None:
    tool = None
    status = str(event.get("status", "")) or "unknown"
    if isinstance(event.get("tool"), str):
        tool = event["tool"]
    elif isinstance(event.get("tool"), dict):
        tool = event["tool"].get("name") or event["tool"].get("id")
    elif isinstance(event.get("part"), dict):
        part = event["part"]
        if part.get("type") == "tool":
            tool = part.get("tool") or part.get("name")
            state = part.get("state")
            if isinstance(state, dict):
                status = str(state.get("status", status))
    elif isinstance(event.get("call"), dict):
        call = event["call"]
        tool = call.get("tool") or call.get("name")
    event_type = str(event.get("type", ""))
    if not tool and "tool" not in event_type.lower():
        return
    if not tool:
        tool = event_type or "unknown"
    tool = str(tool)
    if _looks_like_tool_start(event):
        result.tool_call_counts[tool] = result.tool_call_counts.get(tool, 0) + 1
        result.tool_call_trace.append({"tool": tool, "status": status, "event_type": event_type})


def _looks_like_tool_start(event: dict[str, Any]) -> bool:
    status = str(event.get("status", "")).lower()
    event_type = str(event.get("type", "")).lower()
    phase = str(event.get("phase", "")).lower()
    if any(marker in event_type for marker in ("tool.execute", "tool_call", "tool.call", "tool_use")):
        return not any(done in event_type for done in ("after", "result", "complete", "finish"))
    return status in {"started", "running", "pending"} or phase in {"start", "before"}


def _first_int(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return None


def _first_float(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None
