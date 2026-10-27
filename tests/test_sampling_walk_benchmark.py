"""RWS-08 reproducible benchmark smoke; never assert machine-specific speed."""

from benchmarks.random_walks import main


def test_benchmark_checks_parity_and_reports_work_memory_and_cache(capsys):
    report = main(["--nodes", "4", "--edges", "12", "--walks", "6", "--length", "3",
                   "--batch-size", "2", "--cache", "--profile", "--cache-entries", "2"])
    assert len(report["cases"]) == 4
    for case in report["cases"]:
        assert case["reference"]["digest"] == case["cached"]["digest"] == case["legacy"]["digest"]
        assert case["cached"]["cache"]["entries"] <= 2
        assert case["reference"]["walks"] == 6
        assert case["reference"]["transitions_per_second"] > 0
        assert case["reference"]["python_peak_bytes"] > 0
        assert case["profile"]
    assert '"parameters"' in capsys.readouterr().out
