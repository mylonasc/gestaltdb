"""Small real fixtures gate the larger fresh-worker measurement harness."""

import json
import sys
import subprocess

import pytest

from benchmarks.reasoning_frontier_pipeline import analytical_closure, call_worker, fixture, native_evidence, worker


@pytest.mark.parametrize("shape", ["selective", "sparse", "dense"])
def test_actual_claim_pipeline_modes(tmp_path, shape):
    pytest.importorskip("lmdb")
    path = tmp_path / "db"
    manifest = fixture(path, 12, shape, "lmdb")
    (tmp_path / "fixture.json").write_text(json.dumps(manifest))
    samples = [worker(path, 12, shape, method, "first")
               for method in ("frontier", "indexed-python", "indexed-scc")]
    assert len({s["answer_digest"] for s in samples}) == 1
    assert samples[1]["closure_digest"] == samples[2]["closure_digest"]
    assert all(s["status"] == "ok" for s in samples)
    assert len(analytical_closure(12, shape)) == samples[1]["known_facts"]
    assert worker(path, 12, shape, "frontier", "warm")["answer_digest"] == samples[0]["answer_digest"]
    proof = worker(path, 12, shape, "indexed-scc", "first", proofs=True)
    assert proof["proof_nodes"] > 0


def test_actual_native_records_and_index_locators(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.reasoning.extraction import ExtractionCapability
    path = tmp_path / "db"
    manifest = fixture(path, 12, "selective", "pyrex")
    (tmp_path / "fixture.json").write_text(json.dumps(manifest))
    graph = GraphDB.open(path)
    try:
        if not ExtractionCapability.probe(graph.store).native_arrow_scan:
            pytest.skip("runtime lacks plain PyRex native scan")
        with graph._temporal_writer_guard(), graph.read_view(valid_time=50) as view:
            decoded, n = native_evidence(graph, view, "native-decoded", 12)
            located, m = native_evidence(graph, view, "native-locator", 12)
        assert set(decoded) == set(located) and n == m == 12
    finally:
        graph.close()
    for method in ("native-decoded", "native-locator"):
        assert worker(path, 12, "selective", method, "first")["evidence_digest"] == manifest["evidence_digest"]


def test_native_benchmark_refuses_nonfixture_context(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.reasoning.extraction import ExtractionCapability
    path = tmp_path / "db"
    fixture(path, 2, "selective", "pyrex")
    graph = GraphDB.open(path)
    try:
        if not ExtractionCapability.probe(graph.store).native_arrow_scan:
            pytest.skip("runtime lacks plain PyRex native scan")
        graph.assert_claim(subject="bad", predicate="edge", object="other", polarity="positive",
                           agent="not-benchmark", valid=(0, 100))
        with graph._temporal_writer_guard(), graph.read_view(valid_time=50) as view:
            from gestaltdb.reasoning.model import ReasoningError
            with pytest.raises(ReasoningError, match="fixture|decoding"):
                native_evidence(graph, view, "native-decoded", 3)
    finally:
        graph.close()


@pytest.mark.parametrize("code", ["print('[]')", "print('{}')", "raise ValueError('broken')"])
def test_worker_failures_are_not_success(code):
    assert call_worker([sys.executable, "-c", code], 5)["status"] == "error"


def test_worker_timeout_is_not_success():
    assert call_worker([sys.executable, "-c", "import time; time.sleep(5)"], .01)["status"] == "timeout"


def test_limit_is_diagnostic_not_partial_success(tmp_path, monkeypatch):
    pytest.importorskip("lmdb")
    from gestaltdb.reasoning.model import ReasoningLimitError
    path = tmp_path / "db"
    fixture(path, 2, "selective", "lmdb")
    def bounded(*args, **kwargs):
        raise ReasoningLimitError("intentional intermediate cap")
    monkeypatch.setattr("gestaltdb.reasoning.adapters.execution.execute_closure", bounded)
    result = worker(path, 2, "selective", "indexed-python", "first")
    assert result["status"] == "bounded" and result["no_result"] is True
    assert "answers" not in result and "intentional" in result["reason"]


def test_real_polars_budget_and_native_accounting(tmp_path):
    pytest.importorskip("lmdb")
    pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    path = tmp_path / "db"
    manifest = fixture(path, 12, "sparse", "lmdb")
    (tmp_path / "fixture.json").write_text(json.dumps(manifest))
    reference = worker(path, 12, "sparse", "indexed-python", "first")
    default = worker(path, 12, "sparse", "polars-prototype", "first")
    explicit = worker(path, 12, "sparse", "polars-prototype", "warm", polars_max_intermediate_rows=1000)
    assert reference["closure_digest"] == default["closure_digest"] == explicit["closure_digest"]
    assert reference["evidence_digest"] == explicit["evidence_digest"] == manifest["evidence_digest"]
    assert reference["polars_max_intermediate_rows"] is None
    assert default["polars_max_intermediate_rows"] == 100_000
    assert explicit["polars_max_intermediate_rows"] == explicit["metrics"]["polars_max_intermediate_rows"] == 1000
    assert 0 < explicit["metrics"]["polars_peak_intermediate_rows"] <= 1000
    assert explicit["metrics"]["polars_candidate_rows"] > 0
    bounded = worker(path, 12, "sparse", "polars-prototype", "first", polars_max_intermediate_rows=1)
    assert bounded["status"] == "bounded" and bounded["no_result"]
    assert bounded["polars_max_intermediate_rows"] == 1 and "closure_digest" not in bounded


@pytest.mark.parametrize("value,method,proofs", [(0, "polars-prototype", False), (True, "polars-prototype", False),
    (1.5, "polars-prototype", False), (1, "indexed-python", False), (1, "polars-prototype", True)])
def test_invalid_worker_budget_rejected_before_database_open(value, method, proofs):
    with pytest.raises(ValueError, match="Polars closure"):
        worker("missing", 12, "sparse", method, "first", proofs, value)


@pytest.mark.parametrize("options", [["--polars-max-intermediate-rows", "0"],
    ["--methods", "indexed-python", "--polars-max-intermediate-rows", "1"],
    ["--worker", "load", "--polars-max-intermediate-rows", "1"],
    ["--methods", "indexed-python", "--proofs", "--polars-max-intermediate-rows", "1"]])
def test_invalid_cli_budget_rejected_before_workers(monkeypatch, options):
    import benchmarks.reasoning_frontier_pipeline as benchmark
    monkeypatch.setattr(sys, "argv", ["benchmark", *options])
    def forbidden(*args, **kwargs):
        pytest.fail("launched worker before budget validation")
    monkeypatch.setattr(benchmark, "call_worker", forbidden)
    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2


def test_fresh_worker_report_forwards_explicit_budget_only_to_polars(tmp_path):
    pytest.importorskip("lmdb")
    pytest.importorskip("polars")
    pytest.importorskip("pyarrow")
    output = tmp_path / "report.json"
    run = subprocess.run([sys.executable, "-m", "benchmarks.reasoning_frontier_pipeline",
        "--rows", "12", "--shape", "sparse", "--backend", "lmdb", "--methods", "indexed-python", "polars-prototype",
        "--polars-max-intermediate-rows", "1000", "--caches", "first", "--repeats", "1", "--output", str(output)],
        capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    report = json.loads(output.read_text())
    assert report["schema_version"] == 2 and report["configuration"]["polars_max_intermediate_rows"] == 1000
    assert report["complete"] and report["all_parity_verified"]
    indexed, polars = report["samples"]
    assert indexed["method"] == "indexed-python" and indexed["polars_max_intermediate_rows"] is None
    assert polars["polars_max_intermediate_rows"] == 1000 and polars["metrics"]["polars_max_intermediate_rows"] == 1000
    assert indexed["closure_digest"] == polars["closure_digest"]


def test_orchestrator_supports_hosts_without_opencode_temp_root(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path
    import benchmarks.reasoning_frontier_pipeline as benchmark
    original_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: False if str(self) == "/tmp/opencode" else original_is_dir(self))
    original_temporary_directory = tempfile.TemporaryDirectory
    roots = []
    def temporary_directory(*args, **kwargs):
        roots.append(kwargs["dir"])
        return original_temporary_directory(*args, **kwargs)
    monkeypatch.setattr(benchmark.tempfile, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(benchmark, "call_worker", lambda *args: {"status": "ok", "metrics": {}})
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["benchmark", "--rows", "1", "--methods", "indexed-python",
        "--caches", "first", "--repeats", "1", "--output", str(output)])
    benchmark.main()
    assert roots == [None]
    assert json.loads(output.read_text())["complete"]


def test_committed_measurements_distinguish_parity_from_bounded_outcomes():
    from collections import Counter
    from pathlib import Path
    root = Path(__file__).parents[1]
    paths = list((root / "benchmarks/results").glob("reasoning-re342-*.json"))
    counts = Counter()
    final_reports = 0
    for path in paths:
        report = json.loads(path.read_text())
        if "initial-limit-diagnostic" in path.name:
            assert not report["complete"]
            continue
        final_reports += 1
        assert report["complete"] and not report["dirty"]
        assert report["revision"] == "a1cfceb9cbac2d6b8140bf03f55bcc19fd8354e7"
        # Historical reports bind the measured harness, not later additive CLI
        # revisions. Never rewrite their hashes/results to match current code.
        assert report["harness_sha256"] == "7720bda89839f83f3c13f94faee26508d84122791a6d5f2eefa0e5eb68f4eb12"
        config = report["configuration"]
        assert len(report["samples"]) == config["repeats"] * len(config["methods"]) * len(config["caches"])
        assert report["all_parity_verified"] == all(s["status"] == "ok" for s in report["samples"])
        for sample in report["samples"]:
            counts[sample["status"]] += 1
            if sample["status"] == "bounded":
                assert sample["no_result"] is True and "answers" not in sample
                assert sample["method"] == "polars-prototype" and config["rows"] == 100000
            else:
                assert sample["status"] == "ok" and sample["answer_digest"]
                if sample["method"] != "frontier":
                    assert sample["evidence_digest"] == report["fixture"]["evidence_digest"]
    assert final_reports == 6 and counts == {"ok": 126, "bounded": 18}


def test_committed_explicit_budget_reports_have_complete_exact_parity():
    from collections import Counter
    from pathlib import Path
    paths = list((Path(__file__).parents[1] / "benchmarks/results").glob("reasoning-re255-*-100k.json"))
    assert len(paths) == 4
    matrix = set()
    for path in paths:
        report = json.loads(path.read_text())
        assert report["schema_version"] == 2 and report["complete"] and report["all_parity_verified"]
        assert not report["dirty"] and report["revision"] == "b87ef1c035832425349ce3d426b226b3ce1ebde5"
        assert report["harness_sha256"] == "c09f11c2cc1d817a907caac5ef1fb49590a7af8d542cc42ed68de5f18fc3009d"
        config = report["configuration"]
        matrix.add((config["backend"], config["shape"]))
        assert config["rows"] == 100000 and config["repeats"] == 3
        assert config["polars_max_intermediate_rows"] == 1000000
        assert report["threads"] == {"POLARS_MAX_THREADS": "1", "OMP_NUM_THREADS": "1"}
        samples = report["samples"]
        assert len(samples) == 12
        assert Counter((s["method"], s["cache"]) for s in samples) == {
            (method, cache): 3 for method in ("indexed-python", "polars-prototype") for cache in ("first", "warm")}
        assert len({s["closure_digest"] for s in samples}) == len({s["answer_digest"] for s in samples}) == 1
        expected_facts = len(analytical_closure(100000, config["shape"]))
        for sample in samples:
            assert sample["status"] == "ok" and sample["evidence_rows"] == 100000
            assert sample["evidence_digest"] == report["fixture"]["evidence_digest"]
            assert sample["known_facts"] == expected_facts
            assert sample["bytes_copied"] is None and sample["rss_peak_bytes"] > 0
            if sample["method"] == "polars-prototype":
                metrics = sample["metrics"]
                assert sample["polars_max_intermediate_rows"] == metrics["polars_max_intermediate_rows"] == 1000000
                assert metrics["polars_peak_intermediate_rows"] == (100028 if config["shape"] == "selective" else 200019)
                assert metrics["polars_candidate_rows"] > metrics["polars_peak_intermediate_rows"]
                assert sample["polars_threads"] == 1
            else:
                assert sample["polars_max_intermediate_rows"] is None
    assert matrix == {(backend, shape) for backend in ("lmdb", "pyrex") for shape in ("selective", "sparse")}


@pytest.mark.parametrize("shape", ["selective", "sparse", "dense"])
def test_actual_authenticated_native_method_is_distinct_and_exact(tmp_path, shape, monkeypatch):
    import benchmarks.reasoning_frontier_pipeline as benchmark
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    path = tmp_path / "db"
    manifest = fixture(path, 12, shape, "pyrex")
    (tmp_path / "fixture.json").write_text(json.dumps(manifest))
    reference = worker(path, 12, shape, "indexed-python", "first")
    def forbidden(*args, **kwargs):
        pytest.fail("authenticated source used benchmark-only decoder")
    monkeypatch.setattr(benchmark, "native_evidence", forbidden)
    native = worker(path, 12, shape, "native-authenticated", "first")
    warm = worker(path, 12, shape, "native-authenticated", "warm")
    assert native["source_contract"] == warm["source_contract"] == "authenticated-native-full-history"
    for sample in (native, warm):
        assert sample["closure_digest"] == reference["closure_digest"]
        assert sample["evidence_digest"] == reference["evidence_digest"] == manifest["evidence_digest"]
        for field in ("native_raw_rows", "native_visible_records", "native_logical_claims", "native_resolved_claims"):
            assert sample["metrics"][field] == 12
        assert sample["status"] == "ok" and sample["polars_max_intermediate_rows"] is None


def test_timeout_and_error_keep_worker_stage_without_partial_result():
    code = "import sys,time; print('PIPELINE_PROGRESS {\"stage\":\"extraction\"}',file=sys.stderr,flush=True); time.sleep(5)"
    result = call_worker([sys.executable, "-c", code], .5)
    assert result["status"] == "timeout" and result["last_stage"] == "extraction"
    assert "metrics" not in result and "closure_digest" not in result
    failed = code.replace("time.sleep(5)", "raise RuntimeError('injected')")
    result = call_worker([sys.executable, "-c", failed], 5)
    assert result["status"] == "error" and result["last_stage"] == "extraction"


def test_checkpoint_parser_accepts_timeout_bytes_and_ignores_noise():
    from benchmarks.reasoning_frontier_pipeline import last_checkpoint
    assert last_checkpoint(None) is None
    assert last_checkpoint(b'noise\nPIPELINE_PROGRESS {"stage":"extraction"}\nPIPELINE_PROGRESS invalid\n') == "extraction"
    assert last_checkpoint('PIPELINE_PROGRESS {"stage":1}') is None
