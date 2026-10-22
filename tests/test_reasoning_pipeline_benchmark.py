"""Small semantic smoke; this is not evidence for 100K-row performance."""

import pytest


def test_pipeline_worker_checks_exact_evidence_and_closure():
    pytest.importorskip("lmdb")
    from benchmarks.reasoning_pipeline import run_once

    report = run_once(5, "lmdb", 2)
    assert report["status"] == "ok"
    assert report["scanned"] == report["accepted"] == report["rows"] == 5
    assert report["closure_facts"] == 15
    assert len(report["evidence_digest"]) == 64
    assert report["metrics"]["load_s"] >= 0
    assert report["metrics"]["reasoning_pipeline_s"] >= report["metrics"]["evaluate_s"]
