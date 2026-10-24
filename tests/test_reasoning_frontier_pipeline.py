"""Small real fixtures gate the larger fresh-worker measurement harness."""

import json
import sys

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


def test_committed_measurements_distinguish_parity_from_bounded_outcomes():
    from collections import Counter
    from pathlib import Path
    import hashlib
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
        assert report["harness_sha256"] == hashlib.sha256(
            (root / "benchmarks/reasoning_frontier_pipeline.py").read_bytes()).hexdigest()
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
