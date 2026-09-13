---
name: gestaltdb-agent-performance-benchmarking
description: Use when benchmarking how opencode agents and model configurations learn to use GestaltDB in application code, including token usage, tool usage, invalid code, validation results, and HTML reports.
---

# GestaltDB Agent Performance Benchmarking

Use this skill to run repeatable agentic coding benchmarks that measure how effectively assistants discover and use GestaltDB's public APIs. These benchmarks are usage tasks, not library development tasks: agents should create standalone application/example code and should not modify GestaltDB internals. The benchmark runner starts isolated opencode runs in temporary git worktrees, measures speed, tokens, tool calls, deterministic validation results, invalid edit scope, and LLM-as-judge scores, then writes JSONL, CSV, summary JSON, and HTML reports.

## Quick Start

Run the default local Ollama benchmark configuration:

```bash
uv run python .opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/run_agent_benchmarks.py \
  --model ollama/gemma4:26b \
  --parallelism sequential \
  --repetitions 1 \
  --html
```

For remote/API models, parallelize independent worktree runs:

```bash
uv run python .opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/run_agent_benchmarks.py \
  --model anthropic/claude-sonnet-4-6 \
  --parallelism parallel \
  --max-workers 4 \
  --repetitions 3 \
  --html
```

## Defaults

- Coding model: `ollama/gemma4:26b`.
- Judge model: same as coding model unless `--judge-model` is provided.
- LLM-as-judge: enabled by default.
- Permission auto-approval: enabled by default with `--auto`.
- Execution mode: sequential by default to avoid overloading local Ollama/GPU models.
- Isolation: one temporary git worktree per run.

## Auto-Approval Safety

The runner uses `opencode run --auto` so benchmarks do not block on permission prompts. To reduce risk, each run happens in an isolated git worktree and the runner writes a benchmark-specific opencode config with stricter default permissions. Use `--no-auto` for interactive debugging.

## Metrics Collected

Each run records:

- Wall-clock duration.
- Input, output, and total tokens when opencode/provider metadata includes them.
- Cost when available.
- Tool call count.
- Tool call counts by tool name.
- Ordered tool call trace.
- Model, judge model, opencode version, skill version, task hash, commit hash, dirty status, and runner configuration.
- Validation stdout/stderr, session export, patch diff, changed-path scope checks, and judge result.

Token and cost fields may be null for local providers that do not report usage.

## Benchmark Tasks

Task definitions live in `agentic-coding-efficiency-benchmarks/`. Each YAML file contains a user-oriented coding prompt, validation commands, timeout, allowed file scopes, and judge rubric. The default tasks ask agents to create standalone scripts under `agent_benchmark_solutions/`; changes to `src/`, `tests/`, docs, or benchmark runner code are treated as benchmark failures.

Initial tasks cover:

- Creating a script that uses `GraphDB.query(...)` and the documented read-only Cypher subset.
- Creating a script that uses typed sampling APIs with external node IDs correctly.
- Creating a script that uses property indexes and deferred index rebuild APIs correctly.

## Outputs

The default output directory is `agent_benchmark_results/`:

```text
agent_benchmark_results/
├── results.jsonl
├── results.csv
├── summary.json
├── index.html
└── runs/
    └── <run-id>/
        ├── opencode-events.jsonl
        ├── session-export.json
        ├── patch.diff
        ├── validation.stdout
        ├── validation.stderr
        └── judge.json
```

## Verification

Use dry-run mode to test benchmark loading, result writing, and report generation without calling a model:

```bash
uv run python .opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/run_agent_benchmarks.py --dry-run --html
```

After modifying this skill, restart opencode so the running session loads the updated skill definition.
