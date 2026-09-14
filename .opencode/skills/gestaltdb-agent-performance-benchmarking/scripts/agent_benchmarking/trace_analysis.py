"""Trace collection and deterministic failure analysis for benchmark runs."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
from typing import Any

from .results import AgentBenchmarkResult, TraceAnalysis


SELF_CORRECTION_PATTERNS = (
    r"\bfix(ed|ing)?\b",
    r"\btry again\b",
    r"\bmistake\b",
    r"\berror\b",
    r"\bfailed\b",
    r"\btraceback\b",
    r"\bincorrect\b",
    r"\bnot supported\b",
)

SIGNAL_RULES = [
    (
        "core classes imported from package root",
        r"from\s+gestaltdb\s+import\s+.*\b(GraphDB|Node|Edge|LevelDBStore|LMDBStore|PyRexStore)\b",
        "AGENTS.md Import Map and src/gestaltdb/__init__.py should emphasize importing core classes from submodules.",
    ),
    (
        "graph handle may not be closed",
        r"GraphDB\.(create|open)\(|GraphDB\(",
        "AGENTS.md and examples should continue emphasizing graph.close() in finally blocks.",
    ),
    (
        "unsupported Cypher syntax likely used",
        r"\b(OPTIONAL\s+MATCH|WITH\b|CREATE\b|MERGE\b|DELETE\b|SET\b|COUNT\s*\(|MATCH\s+\w+\s*=|\*\d+)",
        "Cypher docs and AGENTS.md should make unsupported syntax more discoverable for application authors.",
    ),
    (
        "relationship type may not be stored in edge.properties['type']",
        r"Edge\([^\n]*(type=|label=|relationship=)",
        "Edge construction docs should state relationship types are stored in properties={\"type\": ...}.",
    ),
    (
        "deferred indexes used without rebuild",
        r"IndexMaintenanceMode\.DEFER",
        "Ingestion/indexing docs should show that DEFER requires rebuild_deferred_indexes() before index-backed queries.",
    ),
    (
        "snapshot compact IDs may be confused with external node IDs",
        r"SamplerSnapshot|SamplerEngine|global_id|compact",
        "Sampling docs should contrast GraphDB external IDs with SamplerSnapshot compact IDs in task-oriented examples.",
    ),
]


def _solution_patch(patch_text: str) -> str:
    """Return patch hunks excluding benchmark harness config under .opencode/.

    The runner rewrites ``.opencode/opencode.json`` in every worktree, so its
    prose (permissions, prompts) must not trigger solution-code signals.
    """
    hunks = re.split(r"(?m)^(?=diff --git )", patch_text)
    kept = [hunk for hunk in hunks if not re.match(r"diff --git a/\.opencode/", hunk)]
    return "".join(kept)


def write_trace_bundle(run_dir: Path, result: AgentBenchmarkResult) -> Path:
    """Write a single JSON file that points to and embeds key trace artifacts."""
    trace_path = run_dir / "trace.json"
    payload = {
        "result": result.to_dict(),
        "events": _read_jsonl(Path(result.events_path)) if result.events_path else [],
        "session_export": _read_json(Path(result.session_export_path)) if result.session_export_path else None,
        "patch": _read_text(Path(result.patch_path)) if result.patch_path else "",
        "validation_stdout": _read_text(Path(result.validation.stdout_path)) if result.validation.stdout_path else "",
        "validation_stderr": _read_text(Path(result.validation.stderr_path)) if result.validation.stderr_path else "",
        "judge_raw": _read_text(Path(result.judge.raw_output_path)) if result.judge.raw_output_path else "",
    }
    trace_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return trace_path


def analyze_trace(run_dir: Path, result: AgentBenchmarkResult) -> TraceAnalysis:
    """Analyze run artifacts for usage errors and documentation remediation actions."""
    session_export = _read_json(Path(result.session_export_path)) if result.session_export_path else None
    assistant_corpus = "\n".join([
        _assistant_text_from_events(_read_jsonl(Path(result.events_path))) if result.events_path else "",
        _assistant_text_from_session(session_export),
    ])
    signal_corpus = "\n".join([
        _read_text(Path(result.patch_path)) if result.patch_path else "",
        _read_text(Path(result.validation.stdout_path)) if result.validation.stdout_path else "",
        _read_text(Path(result.validation.stderr_path)) if result.validation.stderr_path else "",
    ])
    solution_corpus = "\n".join([
        _solution_patch(_read_text(Path(result.patch_path))) if result.patch_path else "",
        _read_text(Path(result.validation.stdout_path)) if result.validation.stdout_path else "",
        _read_text(Path(result.validation.stderr_path)) if result.validation.stderr_path else "",
    ])
    corpus = "\n".join([assistant_corpus, signal_corpus])
    execution_text = "\n".join([corpus, result.error])
    analysis = TraceAnalysis(status="ok")
    analysis.self_correction_count = sum(len(re.findall(pattern, corpus, flags=re.IGNORECASE)) for pattern in SELF_CORRECTION_PATTERNS)

    if result.status == "timeout":
        analysis.suspected_error_patterns.append("agent timed out before producing a valid benchmark result")
        analysis.remediation_actions.append("Reduce task ambiguity or add more direct examples for the relevant API in AGENTS.md/EXAMPLES.md.")
    if "No such file or directory" in execution_text and "agent_benchmark_solutions" in execution_text:
        analysis.invalid_code_signals.append("expected solution script was not created")
        analysis.remediation_actions.append("Make benchmark prompts and examples clearer about the required output file path.")
    if "Traceback" in corpus or "AssertionError" in corpus:
        analysis.invalid_code_signals.append("generated code failed at runtime or assertion time")
    if "Unexpected server error" in execution_text:
        analysis.suspected_error_patterns.append("opencode/provider failed before agent work could be evaluated")

    for label, pattern, remediation in SIGNAL_RULES:
        # The Cypher rule must only see solution code, not harness config prose.
        corpus_to_scan = solution_corpus if label == "unsupported Cypher syntax likely used" else signal_corpus
        if re.search(pattern, corpus_to_scan, flags=re.IGNORECASE | re.DOTALL):
            analysis.library_misuse_signals.append(label)
            if remediation not in analysis.remediation_actions:
                analysis.remediation_actions.append(remediation)
            analysis.evidence.append({"pattern": label, "regex": pattern})

    if "Changed files outside allowed_paths" in corpus:
        analysis.invalid_code_signals.append("agent modified files outside the allowed usage-script scope")
        analysis.remediation_actions.append("Benchmark prompts should continue explicitly forbidding src/, tests/, docs/, and runner edits for usage tasks.")

    if not analysis.suspected_error_patterns and not analysis.library_misuse_signals and not analysis.invalid_code_signals:
        analysis.summary = "No deterministic trace issues were detected. Inspect trace.json for qualitative review."
    else:
        parts = []
        if analysis.suspected_error_patterns:
            parts.append("; ".join(analysis.suspected_error_patterns))
        if analysis.library_misuse_signals:
            parts.append("library misuse: " + "; ".join(analysis.library_misuse_signals))
        if analysis.invalid_code_signals:
            parts.append("invalid code: " + "; ".join(analysis.invalid_code_signals))
        analysis.summary = " | ".join(parts)
    analysis.documentation_gaps = sorted({action.split(" should ", 1)[0] for action in analysis.remediation_actions if " should " in action})
    (run_dir / "trace-analysis.json").write_text(json.dumps(asdict(analysis), indent=2, sort_keys=True), encoding="utf-8")
    return analysis


def inspect_results_dir(results_dir: Path) -> list[dict[str, Any]]:
    """Inspect existing run directories and write aggregate remediation output."""
    rows: list[dict[str, Any]] = []
    for result_path in sorted((results_dir / "runs").glob("*/result.json")):
        result = _read_json(result_path)
        if not isinstance(result, dict):
            continue
        run_dir = result_path.parent
        faux = _result_from_dict(result)
        analysis = analyze_trace(run_dir, faux)
        rows.append({"run_id": faux.run_id, "benchmark_id": faux.benchmark_id, "analysis": asdict(analysis)})
    aggregate = {
        "runs": rows,
        "remediation_actions": sorted({action for row in rows for action in row["analysis"].get("remediation_actions", [])}),
    }
    (results_dir / "trace-inspection-summary.json").write_text(json.dumps(aggregate, indent=2, sort_keys=True), encoding="utf-8")
    return rows


def _result_from_dict(data: dict[str, Any]) -> AgentBenchmarkResult:
    from .results import JudgeResult, ValidationResult

    validation = ValidationResult(**data.get("validation", {"status": "not_run"}))
    judge = JudgeResult(**data.get("judge", {"enabled": False, "status": "not_run"}))
    payload = dict(data)
    payload["validation"] = validation
    payload["judge"] = judge
    payload.pop("analysis", None)
    return AgentBenchmarkResult(**payload)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[Any]:
    rows: list[Any] = []
    for line in _read_text(path).splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line})
    return rows


def _assistant_text_from_events(events: list[Any]) -> str:
    chunks: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        if event_type == "text":
            part = event.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("synthetic"):
                chunks.append(part["text"])
        if "tool" in event_type.lower() or event_type == "error":
            chunks.append(json.dumps(event, sort_keys=True))
    return "\n".join(chunks)


def _assistant_text_from_session(session_export: Any) -> str:
    if not isinstance(session_export, dict):
        return ""
    chunks: list[str] = []
    for message in session_export.get("messages", []):
        if not isinstance(message, dict):
            continue
        info = message.get("info")
        if not isinstance(info, dict) or info.get("role") == "user":
            continue
        for part in message.get("parts", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "\n".join(chunks)
