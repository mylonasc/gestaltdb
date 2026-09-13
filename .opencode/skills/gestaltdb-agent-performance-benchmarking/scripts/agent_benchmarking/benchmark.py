"""Benchmark task definitions and lightweight YAML loading."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentBenchmark:
    """One agentic coding benchmark task."""

    benchmark_id: str
    name: str
    description: str
    prompt: str
    validation_commands: list[str]
    setup_commands: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    judge_rubric: str = ""
    minimum_judge_score: int = 4
    timeout_seconds: int = 900
    source_path: Path | None = None

    @property
    def task_hash(self) -> str:
        source = self.source_path.read_bytes() if self.source_path else repr(self).encode("utf-8")
        return hashlib.sha256(source).hexdigest()[:16]

    @classmethod
    def from_file(cls, path: Path) -> "AgentBenchmark":
        data = _load_simple_yaml(path)
        return cls(
            benchmark_id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            description=str(data.get("description", "")),
            prompt=str(data["prompt"]),
            validation_commands=[str(item) for item in data.get("validation_commands", [])],
            setup_commands=[str(item) for item in data.get("setup_commands", [])],
            allowed_paths=[str(item) for item in data.get("allowed_paths", [])],
            tags=[str(item) for item in data.get("tags", [])],
            judge_rubric=str(data.get("judge_rubric", "")),
            minimum_judge_score=int(data.get("minimum_judge_score", 4)),
            timeout_seconds=int(data.get("timeout_seconds", 900)),
            source_path=path,
        )


def load_benchmarks(directory: Path, selected: list[str] | None = None) -> list[AgentBenchmark]:
    """Load benchmark YAML files from a directory."""
    wanted = set(selected or [])
    benchmarks = [AgentBenchmark.from_file(path) for path in sorted(directory.glob("*.yaml"))]
    if wanted:
        benchmarks = [benchmark for benchmark in benchmarks if benchmark.benchmark_id in wanted]
        missing = wanted - {benchmark.benchmark_id for benchmark in benchmarks}
        if missing:
            raise ValueError(f"unknown benchmark id(s): {', '.join(sorted(missing))}")
    return benchmarks


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """Load the constrained YAML shape used by this skill without PyYAML."""
    lines = path.read_text(encoding="utf-8").splitlines()
    result: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if raw.startswith(" "):
            raise ValueError(f"unexpected indentation in {path}: {raw!r}")
        if ":" not in raw:
            raise ValueError(f"expected key/value in {path}: {raw!r}")
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "|":
            block: list[str] = []
            index += 1
            while index < len(lines):
                block_line = lines[index]
                if block_line and not block_line.startswith(" "):
                    break
                block.append(block_line[2:] if block_line.startswith("  ") else "")
                index += 1
            result[key] = "\n".join(block).rstrip() + "\n"
            continue
        if value == "[]":
            result[key] = []
            index += 1
            continue
        if value == "":
            items: list[str] = []
            index += 1
            while index < len(lines):
                item_line = lines[index]
                if item_line.startswith("  - "):
                    items.append(item_line[4:].strip())
                    index += 1
                    continue
                if not item_line.strip():
                    index += 1
                    continue
                break
            result[key] = items
            continue
        result[key] = _parse_scalar(value)
        index += 1
    return result


def _parse_scalar(value: str) -> Any:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value.strip('"\'')
