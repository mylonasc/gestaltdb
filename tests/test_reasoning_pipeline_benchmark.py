"""Small semantic smoke; this is not evidence for 100K-row performance."""

import json
import subprocess
import sys

import pytest


def test_pipeline_worker_checks_exact_evidence_and_closure(tmp_path):
    pytest.importorskip("lmdb")
    from benchmarks.reasoning_pipeline import run_once

    report = run_once(5, "lmdb", 2)
    assert report["status"] == "ok"
    assert report["scanned"] == report["accepted"] == report["rows"] == 5
    assert report["closure_facts"] == 15
    assert len(report["evidence_digest"]) == 64
    assert report["metrics"]["load_s"] >= 0
    assert report["metrics"]["reasoning_pipeline_s"] >= report["metrics"]["evaluate_s"]
    assert report["backend_options"]["map_size"] >= 64 * 1024 * 1024


def test_batched_commits_match_per_claim_load_and_record_progress(tmp_path):
    pytest.importorskip("lmdb")
    from benchmarks.reasoning_pipeline import run_once

    progress = tmp_path / "progress.jsonl"
    batched = run_once(20, "lmdb", 4, commit_batch=7, progress_path=progress)
    single = run_once(20, "lmdb", 4, commit_batch=1)
    # Version IDs are fresh per database, so cross-run evidence_digest
    # equality is not expected; compare logical output instead.
    assert batched["closure_facts"] == single["closure_facts"] == 60
    assert batched["scanned"] == single["scanned"] == 20
    phases = [json.loads(line)["phase"] for line in progress.read_text().splitlines()]
    assert phases[:2] == ["load_s", "compile_s"]
    assert set(phases) >= {"load_s", "extract_decode_verify_s", "evaluate_s"}


def test_failed_workers_write_an_explicit_incomplete_report(tmp_path, monkeypatch):
    from benchmarks import reasoning_pipeline as runner

    output = tmp_path / "failed.json"
    monkeypatch.setattr(sys, "argv", ["runner", "--repeats", "2", "--output", str(output)])
    calls = []

    def failure(command, timeout):
        calls.append(command)
        return {"status": "error", "stderr": "MapFullError", "returncode": 1}

    monkeypatch.setattr(runner, "run_worker", failure)
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 1
    report = json.loads(output.read_text())
    assert not report["complete"] and report["summary"] == {}
    assert report["samples"][0]["stderr"] == "MapFullError"
    assert len(calls) == 1  # do not repeat a known failing expensive workload


def test_worker_timeout_and_invalid_output_are_not_success(monkeypatch):
    from benchmarks import reasoning_pipeline as runner

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    assert runner.run_worker(["worker"], 1)["status"] == "timeout"
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "no JSON", ""))
    assert runner.run_worker(["worker"], 1)["status"] == "error"
