"""LLM-as-judge support."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time

from .benchmark import AgentBenchmark
from .results import JudgeResult, ValidationResult


def run_judge(
    benchmark: AgentBenchmark,
    worktree: Path,
    model: str,
    judge_agent_name: str,
    auto: bool,
    patch_text: str,
    validation: ValidationResult,
    output_path: Path,
    timeout_seconds: int = 600,
) -> JudgeResult:
    """Ask an opencode judge agent to evaluate a completed benchmark patch."""
    prompt = _build_judge_prompt(benchmark, patch_text, validation)
    cmd = [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        model,
        "--agent",
        judge_agent_name,
        "--dir",
        str(worktree),
    ]
    if auto:
        cmd.append("--auto")
    cmd.append(prompt)
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=worktree,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    duration = time.monotonic() - started
    raw = proc.stdout + ("\nSTDERR:\n" + proc.stderr if proc.stderr else "")
    output_path.write_text(raw, encoding="utf-8")
    if proc.returncode != 0:
        return JudgeResult(
            enabled=True,
            status="error",
            passed=False,
            reasoning=f"judge command failed with return code {proc.returncode} after {duration:.2f}s",
            raw_output_path=str(output_path),
        )
    parsed = _extract_judge_json(raw)
    score = parsed.get("score") if isinstance(parsed.get("score"), int) else None
    reasoning = str(parsed.get("reasoning", "")).strip() if parsed else raw[-2000:]
    passed = parsed.get("passed")
    if not isinstance(passed, bool):
        passed = score is not None and score >= benchmark.minimum_judge_score
    return JudgeResult(
        enabled=True,
        status="passed" if passed else "failed",
        score=score,
        max_score=int(parsed.get("max_score", 5)) if parsed else 5,
        passed=passed,
        reasoning=reasoning,
        raw_output_path=str(output_path),
    )


def dry_run_judge(output_path: Path) -> JudgeResult:
    """Write and return a deterministic judge result for dry-run mode."""
    payload = {
        "score": 5,
        "max_score": 5,
        "passed": True,
        "reasoning": "Dry-run judge result. No model was called.",
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return JudgeResult(
        enabled=True,
        status="passed",
        score=5,
        max_score=5,
        passed=True,
        reasoning=payload["reasoning"],
        raw_output_path=str(output_path),
    )


def _build_judge_prompt(
    benchmark: AgentBenchmark,
    patch_text: str,
    validation: ValidationResult,
) -> str:
    return f"""
You are judging the result of a GestaltDB agentic coding benchmark.

Benchmark ID: {benchmark.benchmark_id}
Benchmark name: {benchmark.name}

Original coding task:
{benchmark.prompt}

Rubric:
{benchmark.judge_rubric}

Deterministic validation status: {validation.status}
Validation command: {validation.command}
Validation return code: {validation.returncode}

Patch diff:
```diff
{patch_text[:60000]}
```

Return a single JSON object with this exact shape:
{{"score": 1, "max_score": 5, "passed": false, "reasoning": "brief explanation"}}
Use score >= {benchmark.minimum_judge_score} for passed unless the deterministic validation failure reveals a serious correctness issue.
""".strip()


def _extract_judge_json(raw: str) -> dict[str, object]:
    candidates = re.findall(r"\{.*?\}", raw, flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "score" in value:
            return value
    return {}
