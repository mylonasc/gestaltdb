# GestaltDB Agentic Coding Efficiency Benchmarks

This skill benchmarks how well opencode agents use GestaltDB's codebase and agent-facing documentation while completing coding tasks. It is intentionally self-contained under this skill directory.

The runner creates temporary git worktrees, writes a benchmark-specific opencode config, invokes `opencode run --format json`, validates the resulting patch, optionally judges the solution with an LLM, and writes machine-readable plus HTML reports.

See `SKILL.md` for usage.
