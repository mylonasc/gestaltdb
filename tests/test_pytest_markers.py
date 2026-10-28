"""The category markers describe tests without gating them.

These checks pin the contract: markers are registered, the heuristics label
the audit-relevant cases (real crash paths are subprocess+fault, backend
fixtures are backend), stub framework tests stay unlabeled, and the summary
script reports collection and JUnit evidence by category.
"""

import re
import subprocess
import sys
from pathlib import Path

from .category_markers import categories_for

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MARKERS = {"backend", "framework", "native", "fault", "subprocess", "unit"}


def _registered_markers():
    text = (ROOT / "pyproject.toml").read_text()
    block = re.search(r"(?ms)^markers = \[(.*?)^\]", text)
    assert block is not None, "markers block not found in pyproject.toml"
    return {line.split(":", 1)[0].strip().strip('"') for line in block.group(1).splitlines() if ":" in line}


def test_all_category_markers_are_registered():
    assert EXPECTED_MARKERS <= _registered_markers()


def test_backend_fixtures_imply_backend():
    assert categories_for("test_kvstores.py", fixturenames=["graph_db"]) == {"backend"}
    assert categories_for("test_anything.py", fixturenames=["db_path"]) == {"backend"}
    assert categories_for("test_plain.py", fixturenames=["tmp_path"]) == set()


def test_crash_and_fault_files_are_labeled():
    assert "fault" in categories_for("test_reasoning_crash_evidence.py")
    assert "fault" in categories_for("test_concurrency.py")
    assert categories_for("test_temporal.py") == set()
    # "blocks" must not imply the "lock" fault hint.
    assert "fault" not in categories_for(
        "test_sampling_pyg_blocks.py",
        test_id="tests/test_sampling_pyg_blocks.py::test_pyg_blocks_require_torch")


def test_subprocess_requires_test_level_evidence():
    from types import SimpleNamespace

    module = SimpleNamespace(subprocess=__import__("subprocess"))
    assert categories_for("test_mod.py", module=module, source="x = 1") == set()
    assert "subprocess" in categories_for(
        "test_mod.py", module=module, source="subprocess.run(cmd)")


def test_framework_and_native_detection():
    assert "framework" in categories_for("test_sampling_real_frameworks.py")
    assert "framework" not in categories_for("test_sampling_pyg_blocks.py")
    assert "native" in categories_for("test_reasoning_arrow.py")
    assert categories_for("test_cypher.py") == set()


def test_crash_evidence_tests_carry_subprocess_fault_and_backend_markers():
    crashes = ROOT / "tests" / "test_reasoning_crash_evidence.py"
    assert "import subprocess" in crashes.read_text()
    node = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-m", "subprocess and fault and backend", str(crashes)],
        capture_output=True, text=True, cwd=ROOT)
    assert node.returncode == 0, node.stderr
    selected = [line for line in node.stdout.splitlines() if "::" in line]
    assert len(selected) == 6


def test_category_summary_reports_collection_and_junit(tmp_path):
    summary = ROOT / "scripts" / "pytest_category_summary.py"
    collection = subprocess.run(
        [sys.executable, str(summary)], capture_output=True, text=True, cwd=ROOT)
    assert collection.returncode == 0, collection.stderr
    assert "backend" in collection.stdout and "unit" in collection.stdout

    junit = tmp_path / "junit.xml"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_viz_caps.py", "-q",
         f"--junitxml={junit}", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0, run.stderr
    mapped = subprocess.run(
        [sys.executable, str(summary), f"--junitxml={junit}"],
        capture_output=True, text=True, cwd=ROOT)
    assert mapped.returncode == 0, mapped.stderr
    assert "unit" in mapped.stdout
