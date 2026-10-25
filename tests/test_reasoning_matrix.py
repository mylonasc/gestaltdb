"""Small parity/report gates, never throughput assertions in CI."""

import json
from pathlib import Path
import sys

import pytest

from benchmarks import reasoning_matrix as runner


@pytest.mark.parametrize("selection,accepted,pushdown", [
    ("all", 9, "none"), ("predicate", 3, "predicate"), ("subject", 1, "predicate+subject"),
])
@pytest.mark.parametrize("payload", ["fixed", "variable"])
@pytest.mark.parametrize("warmup", [False, True])
def test_claim_matrix_exact_parity(selection, accepted, pushdown, payload, warmup):
    pytest.importorskip("lmdb")
    from benchmarks.reasoning_pipeline import run_once
    report = run_once(9, "lmdb", 2, selection=selection, payload=payload, warmup=warmup)
    assert report["accepted"] == accepted
    assert report["closure_facts"] == 3 * accepted
    assert report["pushdown"] == pushdown
    assert report["scanned"] == accepted
    assert ("warmup_s" in report["metrics"]) == warmup


@pytest.mark.parametrize("selection,accepted", [("all", 9), ("predicate", 3), ("subject", 1)])
@pytest.mark.parametrize("warmup", [False, True])
def test_native_matrix_exact_rows(selection, accepted, warmup):
    pytest.importorskip("pyarrow")
    pytest.importorskip("pyrex")
    from gestaltdb.reasoning.adapters.native import _at_least, _native_version
    if not _at_least(_native_version(), "0.5.0"):
        pytest.skip("native scans require pyrex-rocksdb>=0.5.0")
    report = runner.run_native(9, 2, selection, "variable", warmup)
    assert report["accepted"] == accepted
    assert report["scanned"] == 9  # filters happen AFTER decode
    assert "synthetic" in report["mode"]
    assert report["raw_key_value_bytes"] > 9 * 32


def test_quantiles_require_complete_compatible_finite_samples():
    assert runner.quantiles([]) == {}
    assert runner.quantiles([{"status": "timeout"}]) == {}
    assert runner.quantiles([{"status": "ok", "metrics": {"x": 1}},
                             {"status": "ok", "metrics": {"x": 3}}]) == {
        "x": {"p50": 2, "p95": 3, "n": 2}}
    for metrics in ({"x": float("nan")}, {"x": -1}):
        with pytest.raises(ValueError, match="timing"):
            runner.quantiles([{"status": "ok", "metrics": metrics}])
    with pytest.raises(ValueError, match="incompatible"):
        runner.quantiles([{"status": "ok", "metrics": {"x": 1}},
                          {"status": "ok", "metrics": {"y": 1}}])


@pytest.mark.parametrize("status", ["ok", "timeout", "error", "unavailable"])
def test_parent_persists_explicit_outcomes(tmp_path, monkeypatch, status):
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["matrix", "--sizes", "1", "--backends", "lmdb",
                                     "--repeats", "2", "--output", str(output)])
    calls = []

    def fake(command, timeout):
        calls.append(command)
        # An interrupted report is already on disk before any worker finishes.
        assert not json.loads(output.read_text())["complete"]
        return {"status": status, "metrics": {"stage_s": 1}, "reason": "synthetic test"}

    monkeypatch.setattr(runner, "run_worker", fake)
    if status in ("error", "timeout"):
        with pytest.raises(SystemExit) as error:
            runner.main()
        assert error.value.code == 1
    else:
        runner.main()
    report = json.loads(output.read_text())
    assert report["complete"] == (status == "ok")
    assert report["all_available"] == (status != "unavailable")
    assert len(calls) == (2 if status == "ok" else 1)
    assert bool(report["cases"][0]["summary"]) == (status == "ok")
    assert set(report["source_sha256"]) == {"reasoning_matrix.py", "reasoning_pipeline.py"}


def test_missing_optional_runtime_is_not_fallback(monkeypatch):
    import builtins
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "pyrex":
            raise ImportError("missing")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    report = runner.worker({"backend": "pyrex-native"}, 2)
    assert report["status"] == "unavailable"


def test_worker_protocol_preserves_unavailability(monkeypatch):
    import subprocess
    from benchmarks import reasoning_pipeline
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, json.dumps({"status": "unavailable", "reason": "missing runtime"}), ""))
    assert reasoning_pipeline.run_worker(["worker"], 1)["status"] == "unavailable"


@pytest.mark.parametrize("name,cases,samples,complete", [
    ("ladder", 6, 14, False), ("native", 6, 18, True), ("coverage-1k", 24, 72, True),
])
def test_committed_evidence_preserves_failures_and_sample_counts(name, cases, samples, complete):
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / f"reasoning-194-{name}.json"
    report = json.loads(path.read_text())
    assert report["git_revision"] == "4bd5a6d6853303bc16b415c7442c5930bcb8fc83"
    assert report["working_tree_dirty"] is False
    assert report["complete"] is complete
    assert len(report["cases"]) == cases
    assert sum(len(case["samples"]) for case in report["cases"]) == samples
    for case in report["cases"]:
        assert case["summary"] == runner.quantiles(case["samples"])
        if not case["summary"]:
            assert case["configuration"]["rows"] == 100000
            assert case["samples"][0]["status"] == "timeout"
            assert '"phase": "load_s"' in case["samples"][0]["progress"]
        else:
            assert len(case["samples"]) == 3


@pytest.mark.parametrize("name,cases,samples", [
    ("ladder", 12, 36), ("coverage-10k", 24, 72), ("variable-100k", 4, 12),
])
def test_grouped_read_reports_complete_the_previously_blocked_matrix(name, cases, samples):
    directory = Path(__file__).resolve().parents[1] / "benchmarks" / "results"
    report = json.loads((directory / f"reasoning-314-{name}.json").read_text())
    baseline = json.loads((directory / "reasoning-194-ladder.json").read_text())
    assert report["git_revision"] == "49231f3bddc5b916dace9fa73ef2092089699137"
    assert report["working_tree_dirty"] is False
    assert report["complete"] and report["all_available"]
    assert report["source_sha256"] == baseline["source_sha256"]
    assert report["packages"] == baseline["packages"]
    assert len(report["cases"]) == cases
    assert sum(len(case["samples"]) for case in report["cases"]) == samples
    for case in report["cases"]:
        assert case["summary"] == runner.quantiles(case["samples"])
        assert len(case["samples"]) == 3
        config = case["configuration"]
        expected = {"all": config["rows"], "predicate": (config["rows"] + 3) // 4,
                    "subject": 1}[config["selection"]]
        for sample in case["samples"]:
            assert sample["status"] == "ok"
            assert sample["accepted"] == expected
            assert sample["closure_facts"] == 3 * expected
