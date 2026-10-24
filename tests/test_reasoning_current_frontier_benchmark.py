from pathlib import Path
import re

import pytest


def test_current_frontier_benchmark_parity_and_read_counts(tmp_path):
    pytest.importorskip("lmdb")
    from benchmarks.reasoning_current_frontier import measure, setup
    path = tmp_path / "benchmark"
    assert setup(path, 20)["nodes"] == 20
    reference = measure(path, 20, "reference")
    frontier = measure(path, 20, "frontier")
    assert reference["answers"] == frontier["answers"] == 8
    assert reference["records_read"] == 39
    assert frontier["records_read"] == 9
    assert frontier["accesses"] == {"typed-adjacency-out": 9}
    for result in (reference, frontier):
        assert result["metrics"]["total_pipeline_s"] >= result["metrics"]["snapshot_open_authenticate_s"]


def test_current_frontier_documentation_example():
    pytest.importorskip("lmdb")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-current-frontier.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    exec("\n".join(line[3:] if line.startswith("   ") else line for line in blocks[0].splitlines()), {})
