# GestaltDB Agentic Coding Efficiency Benchmarks

This skill benchmarks how well opencode agents use GestaltDB's public APIs and agent-facing documentation while completing application-style coding tasks. It is intentionally self-contained under this skill directory.

The benchmark tasks are about using the library, not developing the library. Agents are expected to create standalone scripts under `agent_benchmark_solutions/` and are penalized for modifying `src/`, repository tests, docs, or benchmark runner code.

The runner creates temporary git worktrees, writes a benchmark-specific opencode config, invokes `opencode run --format json`, validates the resulting patch, optionally judges the solution with an LLM, and writes machine-readable plus HTML reports.

See `SKILL.md` for usage.
