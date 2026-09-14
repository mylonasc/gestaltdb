"""Benchmark orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from .benchmark import AgentBenchmark, load_benchmarks
from .config import BenchmarkRunnerConfig
from .judge import dry_run_judge, run_judge
from .opencode_runner import export_session, parse_opencode_events, run_opencode_task
from .reporting import BenchmarkResultWriter, render_html_report, summarize_results, write_summary
from .results import AgentBenchmarkResult, JudgeResult, ValidationResult
from .storage import BenchmarkDB
from .trace_analysis import analyze_trace, write_trace_bundle


class BenchmarkRunner:
    """Run agentic coding benchmarks in isolated git worktrees."""

    def __init__(self, config: BenchmarkRunnerConfig) -> None:
        self.config = config

    def run(self) -> list[AgentBenchmarkResult]:
        benchmarks = load_benchmarks(self.config.benchmark_dir, self.config.benchmarks)
        jobs = [(benchmark, repetition) for benchmark in benchmarks for repetition in range(1, self.config.repetitions + 1)]
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        if self.config.parallelism == "parallel" and self.config.max_workers > 1:
            with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
                futures = [pool.submit(self._run_one, benchmark, repetition) for benchmark, repetition in jobs]
                results = [future.result() for future in as_completed(futures)]
        else:
            results = [self._run_one(benchmark, repetition) for benchmark, repetition in jobs]
        results.sort(key=lambda item: (item.benchmark_id, item.repetition, item.run_id))
        BenchmarkResultWriter(self.config.output_dir).write(results)
        summary = summarize_results(results)
        summary["sqlite_db_path"] = str(self.config.effective_db_path)
        write_summary(self.config.output_dir, summary)
        with BenchmarkDB(self.config.effective_db_path) as db:
            db.upsert_results(results)
        if self.config.html:
            render_html_report(self.config.skill_root, self.config.output_dir, results, summary)
        return results

    def _run_one(self, benchmark: AgentBenchmark, repetition: int) -> AgentBenchmarkResult:
        started_at = datetime.now(timezone.utc).isoformat()
        run_id = f"{benchmark.benchmark_id}-r{repetition}-{uuid.uuid4().hex[:8]}"
        run_dir = self.config.output_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._base_metadata(benchmark, repetition, run_id, run_dir)
        metadata["run_started_at"] = started_at
        worktree: Path | None = None
        try:
            worktree = self._prepare_worktree(run_id)
            metadata["worktree_path"] = str(worktree if self.config.preserve_worktrees else "")
            self._write_opencode_config(worktree)
            self._install_packaged_user_skill(worktree)
            if self.config.dry_run:
                return self._run_dry(benchmark, repetition, run_id, run_dir, worktree, metadata)
            self._run_setup(benchmark, worktree, run_dir)
            events_path = run_dir / "opencode-events.jsonl"
            opencode_result = run_opencode_task(
                benchmark=benchmark,
                worktree=worktree,
                model=self.config.model,
                agent_name=self.config.agent_name,
                auto=self.config.auto,
                timeout_seconds=self.config.timeout_seconds or benchmark.timeout_seconds,
                events_path=events_path,
            )
            session_export_path = run_dir / "session-export.json"
            if opencode_result.session_id:
                export_session(opencode_result.session_id, worktree, session_export_path)
            patch_path = run_dir / "patch.diff"
            patch_text = self._write_patch(worktree, patch_path)
            if opencode_result.timed_out:
                validation = ValidationResult(status="not_run", command="skipped after opencode timeout")
                judge = JudgeResult(enabled=self.config.judge_enabled, status="skipped", passed=False if self.config.judge_enabled else None)
            else:
                validation = self._run_validation(benchmark, worktree, run_dir)
                judge = self._run_judge_if_enabled(benchmark, worktree, run_dir, patch_text, validation)
            status = self._combined_status(opencode_result.returncode, validation, judge)
            result = AgentBenchmarkResult(
                **metadata,
                status=status,
                wall_seconds=opencode_result.wall_seconds,
                tokens_input=opencode_result.tokens_input,
                tokens_output=opencode_result.tokens_output,
                tokens_total=opencode_result.tokens_total,
                cost_usd=opencode_result.cost_usd,
                tool_call_count=opencode_result.tool_call_count,
                tool_call_counts=opencode_result.tool_call_counts,
                tool_call_trace=opencode_result.tool_call_trace,
                session_id=opencode_result.session_id,
                validation=validation,
                judge=judge,
                patch_path=str(patch_path),
                session_export_path=str(session_export_path if session_export_path.exists() else ""),
                events_path=str(events_path),
                error="opencode timed out" if opencode_result.timed_out else "",
            )
            result.run_finished_at = datetime.now(timezone.utc).isoformat()
            trace_path = write_trace_bundle(run_dir, result)
            result.trace_path = str(trace_path)
            result.analysis = analyze_trace(run_dir, result)
            write_trace_bundle(run_dir, result)
            result.write_json(run_dir / "result.json")
            return result
        except Exception as exc:  # noqa: BLE001 - benchmark runner should record failures.
            return self._error_result(metadata, "error", str(exc), run_dir)
        finally:
            if worktree and not self.config.preserve_worktrees:
                self._remove_worktree(worktree)

    def _base_metadata(self, benchmark: AgentBenchmark, repetition: int, run_id: str, run_dir: Path) -> dict[str, object]:
        return {
            "run_id": run_id,
            "benchmark_id": benchmark.benchmark_id,
            "benchmark_name": benchmark.name,
            "repetition": repetition,
            "model": self.config.model,
            "judge_model": self.config.effective_judge_model,
            "benchmark_skill_version": self.config.skill_version,
            "benchmark_task_hash": benchmark.task_hash,
            "repo_commit_hash": self._git_text(["git", "rev-parse", "HEAD"]),
            "repo_dirty": bool(self._git_text(["git", "status", "--porcelain"])),
            "opencode_version": self._command_text(["opencode", "--version"]),
            "parallelism_mode": self.config.parallelism,
            "auto_approve": self.config.auto,
            "run_dir": str(run_dir),
            "model_context_configured": _configured_context_limit(self.config.model),
            "model_context_available": _available_context_limit(self.config.model),
        }

    def _run_dry(
        self,
        benchmark: AgentBenchmark,
        repetition: int,
        run_id: str,
        run_dir: Path,
        worktree: Path,
        metadata: dict[str, object],
    ) -> AgentBenchmarkResult:
        events_path = run_dir / "opencode-events.jsonl"
        events_path.write_text(
            json.dumps({"type": "tool.execute.before", "tool": "read", "status": "started"}) + "\n"
            + json.dumps({"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}) + "\n",
            encoding="utf-8",
        )
        parsed = parse_opencode_events(events_path.read_text(encoding="utf-8"))
        patch_path = run_dir / "patch.diff"
        patch_path.write_text("", encoding="utf-8")
        validation = ValidationResult(status="passed", command="dry-run", returncode=0)
        judge = dry_run_judge(run_dir / "judge.json") if self.config.judge_enabled else JudgeResult(enabled=False, status="disabled")
        result = AgentBenchmarkResult(
            **metadata,
            status="passed",
            wall_seconds=0.0,
            tokens_input=parsed.tokens_input,
            tokens_output=parsed.tokens_output,
            tokens_total=parsed.tokens_total,
            tool_call_count=parsed.tool_call_count,
            tool_call_counts=parsed.tool_call_counts,
            tool_call_trace=parsed.tool_call_trace,
            validation=validation,
            judge=judge,
            patch_path=str(patch_path),
            events_path=str(events_path),
        )
        result.run_finished_at = datetime.now(timezone.utc).isoformat()
        trace_path = write_trace_bundle(run_dir, result)
        result.trace_path = str(trace_path)
        result.analysis = analyze_trace(run_dir, result)
        write_trace_bundle(run_dir, result)
        result.write_json(run_dir / "result.json")
        return result

    def _prepare_worktree(self, run_id: str) -> Path:
        base = self.config.worktree_base or (self.config.output_dir / "worktrees")
        base.mkdir(parents=True, exist_ok=True)
        worktree = base / run_id
        if self.config.dry_run:
            worktree.mkdir(parents=True, exist_ok=True)
            return worktree
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
            cwd=self.config.repo_root,
            text=True,
            capture_output=True,
            check=True,
        )
        return worktree

    def _remove_worktree(self, worktree: Path) -> None:
        if self.config.dry_run:
            shutil.rmtree(worktree, ignore_errors=True)
            return
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=self.config.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )

    def _write_opencode_config(self, worktree: Path) -> None:
        config_dir = worktree / ".opencode"
        config_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": self.config.model,
            "provider": _ollama_provider_config(),
            "agent": {
                self.config.agent_name: {
                    "description": "Runs isolated GestaltDB agentic benchmark coding tasks.",
                    "mode": "primary",
                    "model": self.config.model,
                    "permission": _benchmark_permissions(),
                    "prompt": "You are a careful benchmarked coding agent. Write the required solution file first (creating parent directories as needed), then validate with the requested commands and iterate on failures. Prefer `python -m gestaltdb.agent_docs` for API guidance over reading library source files, and keep changes minimal.",
                },
                self.config.judge_agent_name: {
                    "description": "Judges GestaltDB benchmark patches with a fixed rubric.",
                    "mode": "primary",
                    "model": self.config.effective_judge_model,
                    "permission": {
                        "read": "allow",
                        "glob": "allow",
                        "grep": "allow",
                        "list": "allow",
                        "edit": "deny",
                        "bash": "deny",
                        "external_directory": "deny",
                    },
                    "prompt": "You are an impartial evaluator. Return only the requested structured JSON judgement.",
                },
            },
        }
        (config_dir / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    def _install_packaged_user_skill(self, worktree: Path) -> None:
        from gestaltdb import agent_docs

        agent_docs.install_opencode_skill(worktree, force=True)

    def _run_setup(self, benchmark: AgentBenchmark, worktree: Path, run_dir: Path) -> None:
        for index, command in enumerate(benchmark.setup_commands, 1):
            self._run_shell(command, worktree, run_dir / f"setup-{index}.stdout", run_dir / f"setup-{index}.stderr")

    def _run_validation(self, benchmark: AgentBenchmark, worktree: Path, run_dir: Path) -> ValidationResult:
        if not benchmark.validation_commands:
            command_result = ValidationResult(status="not_run")
            return self._apply_allowed_path_check(benchmark, worktree, run_dir, command_result)
        command = " && ".join(benchmark.validation_commands)
        stdout = run_dir / "validation.stdout"
        stderr = run_dir / "validation.stderr"
        started = time.monotonic()
        proc = self._run_shell(command, worktree, stdout, stderr, check=False)
        duration = time.monotonic() - started
        result = ValidationResult(
            status="passed" if proc.returncode == 0 else "failed",
            command=command,
            returncode=proc.returncode,
            stdout_path=str(stdout),
            stderr_path=str(stderr),
            duration_seconds=duration,
        )
        return self._apply_allowed_path_check(benchmark, worktree, run_dir, result)

    def _apply_allowed_path_check(
        self,
        benchmark: AgentBenchmark,
        worktree: Path,
        run_dir: Path,
        result: ValidationResult,
    ) -> ValidationResult:
        if self.config.dry_run or not benchmark.allowed_paths:
            return result
        changed = [path for path in self._changed_paths(worktree) if not path.startswith(".opencode/")]
        violations = [path for path in changed if not _path_allowed(path, benchmark.allowed_paths)]
        if not violations:
            return result
        result.status = "failed"
        message = "\nChanged files outside allowed_paths:\n" + "\n".join(f"- {path}" for path in violations) + "\n"
        stderr_path = Path(result.stderr_path) if result.stderr_path else run_dir / "validation.stderr"
        with stderr_path.open("a", encoding="utf-8") as stderr_file:
            stderr_file.write(message)
        result.stderr_path = str(stderr_path)
        return result

    def _changed_paths(self, worktree: Path) -> list[str]:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=False,
        )
        paths: list[str] = []
        for line in proc.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.append(path)
        return paths

    def _run_judge_if_enabled(
        self,
        benchmark: AgentBenchmark,
        worktree: Path,
        run_dir: Path,
        patch_text: str,
        validation: ValidationResult,
    ) -> JudgeResult:
        if not self.config.judge_enabled:
            return JudgeResult(enabled=False, status="disabled")
        return run_judge(
            benchmark=benchmark,
            worktree=worktree,
            model=self.config.effective_judge_model,
            judge_agent_name=self.config.judge_agent_name,
            auto=self.config.auto,
            patch_text=patch_text,
            validation=validation,
            output_path=run_dir / "judge.json",
        )

    def _write_patch(self, worktree: Path, patch_path: Path) -> str:
        if self.config.dry_run:
            patch_path.write_text("", encoding="utf-8")
            return ""
        proc = subprocess.run(["git", "diff", "--binary"], cwd=worktree, text=True, capture_output=True, check=False)
        patch_path.write_text(proc.stdout, encoding="utf-8")
        return proc.stdout

    def _combined_status(self, opencode_returncode: int, validation: ValidationResult, judge: JudgeResult) -> str:
        if opencode_returncode == 124:
            return "timeout"
        if opencode_returncode != 0:
            return "error"
        if validation.status not in {"passed", "not_run"}:
            return "failed"
        if judge.enabled and judge.passed is False:
            return "judge_failed"
        return "passed"

    def _error_result(self, metadata: dict[str, object], status: str, error: str, run_dir: Path) -> AgentBenchmarkResult:
        result = AgentBenchmarkResult(**metadata, status=status, error=error)
        result.run_finished_at = datetime.now(timezone.utc).isoformat()
        trace_path = write_trace_bundle(run_dir, result)
        result.trace_path = str(trace_path)
        result.analysis = analyze_trace(run_dir, result)
        write_trace_bundle(run_dir, result)
        result.write_json(run_dir / "result.json")
        return result

    def _run_shell(self, command: str, cwd: Path, stdout_path: Path, stderr_path: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, check=False)
        stdout_path.write_text(proc.stdout, encoding="utf-8")
        stderr_path.write_text(proc.stderr, encoding="utf-8")
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, command, proc.stdout, proc.stderr)
        return proc

    def _git_text(self, cmd: list[str]) -> str:
        return self._command_text(cmd, cwd=self.config.repo_root)

    def _command_text(self, cmd: list[str], cwd: Path | None = None) -> str:
        proc = subprocess.run(cmd, cwd=cwd or self.config.repo_root, text=True, capture_output=True, check=False)
        return (proc.stdout or proc.stderr).strip()


def _benchmark_permissions() -> dict[str, object]:
    return {
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "edit": "allow",
        "external_directory": "deny",
        "bash": {
            "*": "ask",
            "uv run pytest *": "allow",
            "uv run python *": "allow",
            "python *": "allow",
            "python -m pytest *": "allow",
            "git diff*": "allow",
            "git status*": "allow",
            "git rev-parse*": "allow",
            "ls *": "allow",
            "rm *": "deny",
            "git reset *": "deny",
            "git checkout *": "deny",
            "git clean *": "deny",
            "git worktree *": "deny",
        },
    }


def _ollama_provider_config() -> dict[str, object]:
    models: dict[str, object] = {}
    for model_id, name, family in (
        ("gemma4:latest", "Gemma 4", "gemma4"),
        ("gemma4:26b", "Gemma 4 26B", "gemma4"),
        ("gemma4:31b", "Gemma 4 31B", "gemma4"),
        ("qwen3.8:latest", "Qwen 3 27B", "qwen3"),
    ):
        limit = {"context": 8192, "output": 4096}
        if model_id == "qwen3.8:latest":
            limit = {"context": 262144, "output": 32768}
        models[model_id] = {
            "id": model_id,
            "name": name,
            "family": family,
            "status": "active",
            "reasoning": True,
            "tool_call": True,
            "temperature": True,
            "cost": {"input": 0, "output": 0},
            "limit": limit,
        }
    return {"ollama": {"models": models}}


def _configured_context_limit(model: str) -> int | None:
    provider, _, model_id = model.partition("/")
    if provider != "ollama" or not model_id:
        return None
    models = _ollama_provider_config()["ollama"]["models"]  # type: ignore[index]
    model_config = models.get(model_id) if isinstance(models, dict) else None
    if not isinstance(model_config, dict):
        return None
    limit = model_config.get("limit")
    if not isinstance(limit, dict):
        return None
    context = limit.get("context")
    return context if isinstance(context, int) else None


def _available_context_limit(model: str) -> int | None:
    provider, _, model_id = model.partition("/")
    if provider != "ollama" or not model_id:
        return None
    proc = subprocess.run(["ollama", "show", model_id], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        fields = line.strip().split()
        if len(fields) >= 3 and fields[0] == "context" and fields[1] == "length":
            try:
                return int(fields[2])
            except ValueError:
                return None
    return None


def _path_allowed(path: str, allowed_patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in allowed_patterns)
