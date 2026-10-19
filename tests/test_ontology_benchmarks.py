"""Small correctness/isolation checks, never timing thresholds or speed claims."""

from dataclasses import replace
import json
import subprocess
import sys
import zipfile

import pytest

from benchmarks import ontology


def _spec(tmp_path, backend="native", workload="hierarchy", **changes):
    spec = dict(backend=backend, workload=workload, size=6, repeat=0, max_input=1000,
                max_facts=1000, max_candidates=100_000, max_justifications=100_000,
                temp_root=str(tmp_path))
    spec.update(changes)
    return spec


@pytest.mark.parametrize("workload", ontology.WORKLOADS)
@pytest.mark.parametrize("backend", ontology.BACKENDS)
def test_each_backend_matches_analytical_closure(tmp_path, backend, workload):
    module = {"clingo": "clingo", "owlrl": "owlrl", "legacy-lmdb": "lmdb", "claims-native": "lmdb"}.get(backend)
    if module:
        pytest.importorskip(module)
    row = ontology._run_worker(_spec(tmp_path, backend, workload))
    assert row["status"] == "ok", row
    assert row["closure_digest"] == row["expected_digest"]
    assert row["closure_facts"] == len(ontology.make_workload(workload, 6).expected)
    assert row["metrics"]["pipeline_s"] >= row["metrics"]["execution_s"] >= 0
    if backend == "clingo":
        assert row["metrics"]["ground_s"] >= 0
    if backend == "claims-native":
        assert row["metrics"]["extraction_s"] >= 0
        assert row["metrics"]["read_reasoning_s"] > 0
    assert not list(tmp_path.iterdir())


def test_duplicate_rows_are_not_duplicate_logical_facts():
    workload = ontology.make_workload("duplicates", 10)
    assert len(workload.rows) == 80
    assert len(set(workload.rows)) == 10
    assert len(workload.expected) == 30


@pytest.mark.parametrize("workload", ontology.WORKLOADS)
def test_preflight_guards_output_before_allocating_large_closure(workload):
    with pytest.raises(ontology.PreflightLimit):
        ontology.make_workload(workload, 1000, max_facts=2)


def test_worker_limits_are_not_successful_speed_results(tmp_path):
    limited = ontology._run_worker(_spec(tmp_path, workload="dense", size=32, max_facts=10))
    assert limited["status"] == "limited"
    assert limited["limit_stage"] == "analytical_preflight"
    limited = ontology._run_worker(_spec(tmp_path, max_candidates=1))
    assert limited["status"] == "limited"
    assert "max_candidate_rows" in limited["reason"]


def test_optional_absence_is_distinguished_from_broken_required_imports(tmp_path, monkeypatch):
    original = ontology.importlib.import_module

    def missing(name):
        if name == "clingo":
            raise ModuleNotFoundError("optional engine missing", name="clingo")
        return original(name)

    monkeypatch.setattr(ontology.importlib, "import_module", missing)
    assert ontology._run_worker(_spec(tmp_path, "clingo"))["status"] == "unavailable"

    def broken(name):
        raise ModuleNotFoundError("required dependency broken", name="numpy")

    monkeypatch.setattr(ontology.importlib, "import_module", broken)
    assert ontology._run_worker(_spec(tmp_path, "clingo"))["status"] == "error"


def test_wrong_closure_is_an_error_not_a_fast_result(tmp_path, monkeypatch):
    workload = ontology.make_workload("hierarchy", 6)
    monkeypatch.setattr(ontology, "make_workload", lambda *a, **kw: replace(workload, expected=frozenset()))
    row = ontology._run_worker(_spec(tmp_path))
    assert row["status"] == "error"
    assert "closure mismatch" in row["reason"]


def test_parent_records_timeout_and_cleans_its_owned_directory(tmp_path, monkeypatch):
    original = subprocess.run

    def timed_out(command, **kwargs):
        if command[0] == sys.executable:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return original(command, **kwargs)

    monkeypatch.setattr(ontology.subprocess, "run", timed_out)
    args = ontology.build_parser().parse_args([
        "--backends", "native", "--workloads", "hierarchy", "--sizes", "4",
        "--repeats", "1", "--temp-root", str(tmp_path),
    ])
    report = ontology.run_benchmark(args)
    assert report["rows"][0]["status"] == "timeout"
    assert not list(tmp_path.iterdir())


def test_fresh_worker_report_has_verified_digests_and_repeat_metadata(tmp_path):
    output = tmp_path / "results.json"
    args = ontology.build_parser().parse_args([
        "--backends", "native", "native-proofs", "--workloads", "duplicates", "--sizes", "4",
        "--repeats", "2", "--temp-root", str(tmp_path), "--output", str(output),
    ])
    report = ontology.run_benchmark(args)
    assert report == json.loads(output.read_text())
    assert len(report["rows"]) == 4
    assert all(row["status"] == "ok" and row["closure_facts"] == 12 for row in report["rows"])
    assert len({row["closure_digest"] for row in report["rows"]}) == 1
    assert len(report["harness_sha256"]) == 64
    assert {row["repeat"] for row in report["rows"]} == {0, 1}
    assert report["rows"][0]["metrics"]["common_import_s"] > 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["results.json"]


def test_wheel_measurement_reports_only_feature_entries(tmp_path):
    wheel = tmp_path / "test.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("gestaltdb/reasoning/engine.py", "x" * 100)
        archive.writestr("gestaltdb/ontology/model.py", "y" * 50)
        archive.writestr("unrelated.txt", "z" * 200)
    metrics = ontology.footprint(wheel)
    assert metrics["wheel"]["feature_uncompressed_bytes"] == 150
    assert 0 < metrics["wheel"]["feature_compressed_bytes"] < 150
    assert metrics["feature_source_bytes"] > 0
    assert len(metrics["feature_sources_sha256"]) == 64


@pytest.mark.parametrize("options", [["--timeout", "nan"], ["--timeout", "0"], ["--sizes", "1"],
                                     ["--max-facts", "0"], ["--repeats", "0"]])
def test_invalid_configuration_fails_before_launching_workers(options):
    with pytest.raises(ValueError):
        ontology.run_benchmark(ontology.build_parser().parse_args(options))


def test_unified_cli_dispatches_the_explicit_argument_list(monkeypatch):
    from benchmarks import cli

    called = []
    monkeypatch.setattr(ontology, "main", lambda argv: called.append(argv))
    options = ["--backends", "native", "--sizes", "4", "--repeats", "1"]
    cli.main(["ontology", *options])
    assert called == [options]


def test_cli_returns_failure_for_invalid_results(monkeypatch):
    monkeypatch.setattr(ontology, "run_benchmark", lambda args: {"rows": [{"status": "error"}]})
    with pytest.raises(SystemExit) as exc:
        ontology.main([])
    assert exc.value.code == 1
