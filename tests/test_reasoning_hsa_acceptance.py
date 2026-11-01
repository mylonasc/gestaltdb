"""Acceptance cannot silently mix checkouts, profile timings or pass noise."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks import reasoning_hsa_acceptance as acceptance


def test_observed_gate_needs_six_consistent_pairs_and_never_passes_noise():
    assert acceptance.gate([1.] * 5, 1.05) == "inconclusive"
    assert acceptance.gate([1.] * 6, 1.05) == "observed-within-limit"
    assert acceptance.gate([1.06] * 6, 1.05) == "observed-breach"
    assert acceptance.gate([1., 1.06] * 3, 1.05) == "inconclusive"
    assert acceptance.gate([float("nan")] * 6, 1.05) == "inconclusive"
    assert acceptance.gate([0.] * 6, 1.05) == "inconclusive"


@pytest.mark.parametrize("elapsed,rss,profile", [(0, 1, None), (float("nan"), 1, None),
    (True, 1, None), (1, True, None), (1, 0, None), (1, 1, {})])
def test_invalid_or_profiled_receipts_cannot_pass(elapsed, rss, profile):
    assert not acceptance.valid_receipt({"metrics": {"authenticated_pipeline_s": elapsed},
        "rss_peak_bytes": rss, "instrumentation": profile})


def fake_receipt(elapsed=1.):
    return {"status": "ok", "metrics": {"authenticated_pipeline_s": elapsed},
            "rss_peak_bytes": 100, "instrumentation": None, "evidence_digest": "f" * 64}


def setup_cli(tmp_path, monkeypatch, extra=()):
    monkeypatch.setattr(sys, "argv", ["acceptance", "--before", str(tmp_path / "before"),
        "--after", str(tmp_path / "after"), "--rows", "4", "--repeats", "6",
        "--methods", "ordinary", "native-authenticated-locator", "--selections", "subject",
        "--after-history-batch-size", "2", "--output", str(tmp_path / "report.json"), *extra])
    monkeypatch.setattr(acceptance, "provenance", lambda root: {
        "revision": "rev", "library_sha256": "lib", "harness_sha256": {"worker": "hash"}})
    monkeypatch.setattr(acceptance.importlib.metadata, "version", lambda name: "test")


def test_pairs_alternate_methods_rotate_fixture_is_shared_and_receipts_are_separate(tmp_path, monkeypatch):
    setup_cli(tmp_path, monkeypatch)
    calls = []
    def worker(root, action, arguments, timeout):
        calls.append((root.name, action, dict(arguments)))
        return {"status": "ok"} if action == "load" else fake_receipt(1. if root.name == "before" else .5)
    monkeypatch.setattr(acceptance, "run_worker", worker)
    acceptance.main()
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["complete"] and report["all_parity_verified"]
    assert len(report["samples"]) == 24
    assert report["fixture_writer"] == "after"
    assert calls[0][0:2] == ("after", "load")
    assert len({args["path"] for _, _, args in calls}) == 1
    for sample in report["samples"]:
        assert sample["pair_order"] == (["before", "after"] if sample["repeat"] % 2 == 0 else ["after", "before"])
        assert sample["instrumentation"] is None
    assert report["samples"][0]["method_order"] != report["samples"][4]["method_order"]
    for label, action, args in calls:
        if action == "read":
            assert args["instrumented"] is False
            assert ("locator_history_batch_size" in args) == (label == "after" and args["method"] != "ordinary")
    summary = report["summary"]["ordinary/subject/50/first"]
    assert summary["authenticated_pipeline_s"]["gate"] == "observed-within-limit"
    assert summary["rss_peak_bytes"]["after"]["individual"] == [100] * 6
    assert report["selective_relative_to_ordinary"]["native-authenticated-locator/subject/50/first"]["latency_gate"] == "observed-within-limit"


@pytest.mark.parametrize("fault", ["fixture", "worker", "parity", "profile", "source-drift"])
def test_failures_keep_raw_incomplete_report_without_summaries(tmp_path, monkeypatch, fault):
    setup_cli(tmp_path, monkeypatch)
    def worker(root, action, arguments, timeout):
        if fault == "fixture" or (fault == "worker" and action == "read"):
            return {"status": "timeout", "last_stage": "fixture-commit"}
        if action == "load":
            return {"status": "ok"}
        row = fake_receipt()
        if fault == "parity" and root.name == "before":
            row["evidence_digest"] = "bad"
        if fault == "profile":
            row["instrumentation"] = {}
        return row
    monkeypatch.setattr(acceptance, "run_worker", worker)
    if fault == "source-drift":
        calls = []
        def changed(root):
            calls.append(root)
            return {"revision": "old" if len(calls) <= 2 else "new",
                    "library_sha256": "lib", "harness_sha256": {"worker": "hash"}}
        monkeypatch.setattr(acceptance, "provenance", changed)
    with pytest.raises(SystemExit):
        acceptance.main()
    report = json.loads((tmp_path / "report.json").read_text())
    assert not report["complete"] and not report["all_parity_verified"]
    assert "summary" not in report and "selective_relative_to_ordinary" not in report


def test_unpaired_summary_is_rejected():
    row = dict(fake_receipt(), label="before", repeat=0, method="ordinary", selection="all", valid_time=50, cache="first")
    with pytest.raises(ValueError, match="unpaired"):
        acceptance.summarize([row])


def test_real_workers_attest_checkout_and_share_exact_versions(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    root = Path(__file__).parents[1]
    path = tmp_path / "paired.json"
    result = subprocess.run([sys.executable, "-m", "benchmarks.reasoning_hsa_acceptance",
        "--before", str(root), "--after", str(root), "--rows", "8", "--repeats", "2",
        "--methods", "ordinary", "native-authenticated-locator", "--selections", "subject",
        "--times", "30", "--after-history-batch-size", "2", "--output", str(path)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    report = json.loads(path.read_text())
    assert report["complete"] and report["all_parity_verified"]
    assert len({row["evidence_digest"] for row in report["samples"]}) == 1
    for row in report["samples"]:
        assert Path(row["loaded_source"]["library"]).is_relative_to(root / "src")
        assert Path(row["loaded_source"]["harness"]).is_relative_to(root / "benchmarks")
    assert all(metric["gate"] == "inconclusive" for group in report["summary"].values() for metric in group.values())
