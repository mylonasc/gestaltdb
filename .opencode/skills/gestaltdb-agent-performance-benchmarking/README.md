# GestaltDB Agentic Coding Efficiency Benchmarks

This skill benchmarks how well opencode agents use GestaltDB's public APIs and agent-facing documentation while completing application-style coding tasks. It is intentionally self-contained under this skill directory.

The benchmark tasks are about using the library, not developing the library. Agents are expected to create standalone scripts under `agent_benchmark_solutions/` and are penalized for modifying `src/`, repository tests, docs, or benchmark runner code.

The runner creates temporary git worktrees, writes a benchmark-specific opencode config, invokes `opencode run --format json`, validates the resulting patch, optionally judges the solution with an LLM, and writes machine-readable plus HTML reports.

Every run also writes a full `trace.json` bundle and `trace-analysis.json` file. The trace analyzer is designed to identify concrete agent failure modes and documentation remediation actions, such as missing import guidance, unclear deferred-index rebuild rules, unsupported Cypher syntax use, or sampling ID-space confusion.

Structured run metadata is indexed in `agent_benchmark_results/agent_benchmarks.sqlite` by default. Use `scripts/browse_benchmark_db.py recent` or any SQLite browser to inspect timestamps, commit hashes, statuses, token/tool counts, artifact paths, trace analysis, and remediation actions.

See `SKILL.md` for usage.
