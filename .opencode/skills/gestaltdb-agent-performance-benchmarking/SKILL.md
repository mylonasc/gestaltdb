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
- Full trace bundles in `trace.json` plus deterministic trace analysis in `trace-analysis.json`.
- A repo-local SQLite index at `agent_benchmark_results/agent_benchmarks.sqlite` unless `--db-path` is provided.

Token and cost fields may be null for local providers that do not report usage.

## Benchmark Tasks

Task definitions live in `agentic-coding-efficiency-benchmarks/`. Each YAML file contains a user-oriented coding prompt, validation commands, timeout, allowed file scopes, and judge rubric. The default tasks ask agents to create standalone scripts under `agent_benchmark_solutions/`; changes to `src/`, `tests/`, docs, or benchmark runner code are treated as benchmark failures.

Initial tasks cover:

- Creating a script that uses `GraphDB.query(...)` and the documented read-only Cypher subset.
- Creating a script that uses typed sampling APIs with external node IDs correctly.
- Creating a script that uses property indexes and deferred index rebuild APIs correctly.
- Creating a script that stores, reopens, and inspects a manifest-backed database with `GraphDB.create/open`, `manifest`, `index_statistics`, and entity properties.
- Creating a script that uses the PyRex/RocksDB backend with Polars CSV artifacts, `ingest_polars`, property indexes, and typed traversal.

## Outputs

The default output directory is `agent_benchmark_results/`:

```text
agent_benchmark_results/
├── agent_benchmarks.sqlite
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
        ├── trace.json
        ├── trace-analysis.json
        └── judge.json
```

Browse the SQLite index with standard tools or the bundled helper:

```bash
uv run python .opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/browse_benchmark_db.py recent --limit 20
uv run python .opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/browse_benchmark_db.py remediation
sqlite3 agent_benchmark_results/agent_benchmarks.sqlite '.tables'
```

The SQLite DB stores structured metadata: run timestamps, commit hashes, model/config, token and tool counts, statuses, validation/judge state, trace-analysis summaries, artifact paths, and remediation actions. Large artifacts remain editable files under each run directory.

## Trace Inspection

Each run stores the opencode JSON event stream, session export, patch, validation output, judge output, and deterministic trace analysis. Use the inspector on any existing results directory to regenerate analysis and aggregate remediation actions:

```bash
uv run python .opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/inspect_agent_traces.py agent_benchmark_results/<run-dir>
```

The inspector looks for signals such as repeated self-correction, runtime errors, unsupported Cypher syntax, wrong import paths, stale deferred index use, ID-space confusion in sampling, and edits outside the allowed usage-script scope. It writes `trace-inspection-summary.json` with suggested remediation actions such as updating `AGENTS.md`, `EXAMPLES.md`, or relevant API docstrings.

## Lessons Learned

- Validate the effective context limit before diagnosing agent loops. Model documentation or local model metadata may advertise a large context window, while opencode provider config can impose a smaller `limit.context`. Compare the model's advertised context, the opencode configured context, and trace compaction events before attributing repeated reads or summaries to API confusion.
- For Ollama models, check both `ollama show <model>` and the generated benchmark `.opencode/opencode.json`. A model can advertise 250k+ tokens while the benchmark actually runs with an 8k context cap, triggering frequent compaction and loop-like behavior.

## Verification

Use dry-run mode to test benchmark loading, result writing, and report generation without calling a model:

```bash
uv run python .opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/run_agent_benchmarks.py --dry-run --html
```

After modifying this skill, restart opencode so the running session loads the updated skill definition.
