"""The benchmark must exercise production historical admission, not raw scans."""

import json
import subprocess
import sys

import pytest

from benchmarks.reasoning_locator_pipeline import METHODS, TIMES, fixture, worker


@pytest.fixture
def history(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    path = tmp_path / "db"
    manifest = fixture(path, 36)
    (tmp_path / "fixture.json").write_text(json.dumps(manifest))
    return path, manifest


@pytest.mark.parametrize("selection", ["all", "predicate", "subject"])
@pytest.mark.parametrize("valid_time", TIMES)
def test_actual_adapters_historical_evidence_parity(history, selection, valid_time):
    path, manifest = history
    results = [worker(path, 36, method, selection, valid_time, "first") for method in METHODS]
    assert len({r["evidence_digest"] for r in results}) == 1
    assert all(r["status"] == "ok" for r in results)
    profile = results[-1]["native_profile"]
    assert profile["authenticated_records"] == manifest["versions"]
    assert profile["materialized_records"] >= results[-1]["evidence_rows"]
    assert "full-catalog-authentication" in profile["scan_mode"]
    if selection == "subject":
        assert profile["candidate_claims"] == 1
    if valid_time == 50 and selection == "subject":
        assert results[-1]["evidence_rows"] == 0  # retraction wins, not correction
    if valid_time == 30 and selection == "subject":
        assert results[-1]["evidence_digest"] != manifest["expected"]["10"]["subject"]["digest"]


def test_priming_preserves_exact_version_parity(history):
    path, _ = history
    first = worker(path, 36, METHODS[-1], "predicate", 70, "first")
    warm = worker(path, 36, METHODS[-1], "predicate", 70, "warm")
    assert first["evidence_digest"] == warm["evidence_digest"]
    assert first["native_profile"] == warm["native_profile"]


def test_wrong_oracle_fails_instead_of_timing_success(history):
    path, manifest = history
    manifest["expected"]["50"]["all"]["digest"] = "forged"
    (path.parent / "fixture.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="parity"):
        worker(path, 36, METHODS[-1], "all", 50, "first")


def test_cli_timeout_retains_incomplete_report_without_quantiles(tmp_path):
    report_path = tmp_path / "timeout.json"
    result = subprocess.run([sys.executable, "-m", "benchmarks.reasoning_locator_pipeline",
        "--rows", "1", "--timeout", "0.000001", "--output", str(report_path)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    report = json.loads(report_path.read_text())
    assert report["complete"] is False
    assert report["fixture"]["status"] == "timeout"
    assert "summary" not in report


def test_sparse_history_keeps_real_versions_and_distinct_density(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    path = tmp_path / "db"
    manifest = fixture(path, 36, history_rows=8)
    (tmp_path / "fixture.json").write_text(json.dumps(manifest))
    assert manifest["history_rows"] == 8
    assert manifest["versions"] == 40  # two corrections and two retractions
    assert manifest["expected"]["50"]["all"]["rows"] == 34
    for t in (30, 50):
        samples = [worker(path, 36, method, "all", t, "first") for method in METHODS]
        assert len({s["evidence_digest"] for s in samples}) == 1


def test_missing_optional_metadata_still_writes_timeout_report(tmp_path, monkeypatch):
    import benchmarks.reasoning_locator_pipeline as benchmark
    def missing(package):
        raise benchmark.importlib.metadata.PackageNotFoundError(package)
    monkeypatch.setattr(benchmark.importlib.metadata, "version", missing)
    monkeypatch.setattr(benchmark, "call_worker", lambda *args: {"status": "timeout", "timeout_s": .001})
    path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["benchmark", "--rows", "1", "--output", str(path)])
    with pytest.raises(SystemExit, match="fixture failed"):
        benchmark.main()
    report = json.loads(path.read_text())
    assert all(v is None for v in report["dependencies"].values())
    assert report["fixture"]["status"] == "timeout" and report["complete"] is False
