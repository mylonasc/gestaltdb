"""The benchmark must exercise production historical admission, not raw scans."""

import json
import subprocess
import sys

import pytest

from benchmarks.reasoning_locator_instrumentation import instrument, stage_delta
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


def test_commit_batch_is_independent_of_rows_and_preserves_oracle(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    single = fixture(tmp_path / "single", 36, history_rows=8, commit_batch=36)
    batched = fixture(tmp_path / "batched", 36, history_rows=8, commit_batch=7)
    assert single["versions"] == batched["versions"] == 40
    # Version IDs are random per build, so compare selection cardinalities,
    # not per-build digests; both fixtures must expose the same fact rows.
    for valid_time, selections in single["expected"].items():
        for selection, expected in selections.items():
            assert expected["rows"] == batched["expected"][valid_time][selection]["rows"]
    assert single["construction"]["batches"] == 1
    assert batched["construction"]["batches"] == -(-36 // 7)
    assert single["commits"] < batched["commits"]


def test_instrumented_fixture_records_construction_phases(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    manifest = fixture(tmp_path / "db", 36, history_rows=8, commit_batch=12,
                       instrumented=True)
    construction = manifest["construction"]
    for field in ("create_s", "assertions_s", "corrections_s", "retractions_s",
                  "oracle_s", "close_s", "load_s", "batches"):
        assert field in construction
    counts = manifest["instrumentation"]
    assert counts["commit_calls"] == manifest["commits"]
    assert counts["sequence_reservations"] == manifest["commits"]
    assert counts["index_write_calls"] >= manifest["commits"]
    assert counts["publication_calls"] == manifest["commits"]
    assert counts["commit_s"] > 0
    assert counts["sequence_reserve_s"] > 0
    assert counts["index_write_s"] > 0
    assert counts["index_entries_written"] > 0
    assert counts["distinct_target_version_ids"] > 0
    assert counts["target_lookup_calls"] > 0
    # Batched resolution: one call resolves many distinct targets.
    assert counts["distinct_target_version_ids"] >= counts["target_lookup_calls"]
    assert counts["checksum_calls"] > 0
    assert counts["checksum_marker_visits"] > 0
    assert counts["commit_cache_entries_peak"] > 0
    assert counts["metadata_reads_by_scope"]
    assert counts["metadata_read_s_by_category"]["commit_descriptor"] >= 0
    assert counts["metadata_writes_by_category"]["visibility_marker"] == manifest["commits"]


def test_instrumented_fixture_emits_durable_phase_checkpoints(tmp_path, monkeypatch):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    import benchmarks.reasoning_locator_pipeline as benchmark
    events = []
    monkeypatch.setattr(benchmark, "checkpoint",
                        lambda stage, **details: events.append((stage, details)))
    benchmark.fixture(tmp_path / "db", 36, history_rows=8, commit_batch=12,
                      instrumented=True)
    stages = [stage for stage, _ in events]
    assert stages[0] == "fixture-create"
    assert "fixture-assertions" in stages
    assert "fixture-sequence-reserved" in stages
    assert "fixture-index-write" in stages
    assert "fixture-index-publication" in stages
    assert stages[-1] == "fixture-complete"


def test_instrumentation_counts_transaction_scoped_stores(tmp_path):
    pytest.importorskip("lmdb")
    from gestaltdb.kvstores import LMDBStore
    store = LMDBStore(path=str(tmp_path / "db"))
    try:
        with instrument() as recorder:
            transaction = store.transaction(write=True)
            try:
                transaction.put_metadata(b"k", b"v")
                assert transaction.get_metadata(b"k") == b"v"
            finally:
                transaction.commit()
        counts = recorder.snapshot()
        assert counts["metadata_reads"] >= 1
        assert counts["metadata_writes"] >= 1
        assert counts["metadata_reads_by_scope"]["LMDBTransactionStore"] >= 1
        assert counts["metadata_writes_by_scope"]["LMDBTransactionStore"] >= 1
    finally:
        store.close()


def test_instrumented_worker_preserves_parity_and_attribution(history):
    path, manifest = history
    plain = worker(path, 36, METHODS[-1], "all", 50, "first")
    measured = worker(path, 36, METHODS[-1], "all", 50, "first", instrumented=True)
    assert plain["evidence_digest"] == measured["evidence_digest"]
    assert plain["native_profile"] == measured["native_profile"]
    stages = measured["instrumentation"]
    assert set(stages) == {"view_open", "extraction", "post_verify", "total"}
    extraction = stages["extraction"]
    assert extraction["native_scan_openings"] >= 1
    assert extraction["native_rows"] > 0
    assert extraction["native_prepare_calls"] == 1
    assert extraction["commit_auth_calls"] >= manifest["commits"]
    assert extraction["target_lookup_calls"] > 0
    assert extraction["checksum_marker_visits"] > 0
    assert extraction["canonical_conformance_calls"] == 1
    assert extraction["metadata_reads_by_category"]["record_envelope"] > 0
    assert stages["view_open"]["metadata_reads_by_category"]["visibility_marker"] > 0
    assert stages["total"]["metadata_reads_by_scope"]


def test_ordinary_source_instrumentation_reports_no_native_scan(history):
    path, _ = history
    measured = worker(path, 36, "ordinary", "all", 50, "first", instrumented=True)
    extraction = measured["instrumentation"]["extraction"]
    assert extraction["native_scan_openings"] == 0
    assert extraction["native_rows"] == 0
    assert extraction["index_rows"] > 0
    assert extraction["target_lookup_calls"] > 0


def test_stage_delta_reports_peaks_and_inclusive_boundaries():
    before = {"metadata_reads": 2, "commit_cache_entries_peak": 3,
              "metadata_read_s_by_category": {"commit_descriptor": 0.5},
              "timing_boundary": "inclusive", "validation_boundary": "validation"}
    after = {"metadata_reads": 7, "commit_cache_entries_peak": 5,
             "metadata_read_s_by_category": {"commit_descriptor": 1.25},
             "timing_boundary": "inclusive", "validation_boundary": "validation"}
    delta = stage_delta(after, before)
    assert delta["metadata_reads"] == 5
    assert delta["commit_cache_entries_peak"] == 5
    assert delta["metadata_read_s_by_category"]["commit_descriptor"] == pytest.approx(0.75)
    assert "inclusive" in delta["timing_boundary"]


def test_instrumentation_restores_production_classes():
    from gestaltdb._graph.temporal_reads import TemporalReader
    original = TemporalReader.get_commit_cached
    with instrument() as recorder:
        assert TemporalReader.get_commit_cached is not original
        assert recorder.snapshot()["metadata_reads"] == 0
    assert TemporalReader.get_commit_cached is original


@pytest.mark.parametrize("commit_batch", [0, -1, True, 1.5])
def test_fixture_rejects_invalid_commit_batch(tmp_path, commit_batch):
    with pytest.raises(ValueError, match="commit_batch"):
        fixture(tmp_path / "db", 36, commit_batch=commit_batch)
