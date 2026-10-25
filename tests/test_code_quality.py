"""Regression coverage for the shrinking library-module budget."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_code_quality", ROOT / "scripts/check_code_quality.py")
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


def make_tree(tmp_path, *, lines=800, baseline=None):
    module = tmp_path / "src/gestaltdb/example.py"
    module.parent.mkdir(parents=True)
    module.write_text("# line\n" * lines, encoding="utf-8")
    manifest = tmp_path / quality.BASELINE
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({} if baseline is None else baseline), encoding="utf-8")
    return tmp_path


def test_library_module_budgets():
    assert quality.check_module_sizes(ROOT) == []


def test_workflow_runs_lint_and_module_gate():
    workflow = (ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
    for command in ("python -m ruff check .", "python scripts/check_code_quality.py",
                    "python -m pytest tests/test_code_quality.py -q"):
        assert command in workflow
    assert "planning/code-quality/module-size-baseline.json" in workflow
    assert "contents: read" in workflow


def test_empty_library_fails(tmp_path):
    root = make_tree(tmp_path)
    (root / "src/gestaltdb/example.py").unlink()
    assert "no Python modules found" in quality.check_module_sizes(root)[0]


def test_new_module_at_limit_passes(tmp_path):
    assert quality.check_module_sizes(make_tree(tmp_path)) == []


def test_new_oversized_module_fails(tmp_path):
    errors = quality.check_module_sizes(make_tree(tmp_path, lines=801))
    assert len(errors) == 1
    assert "801 lines exceeds 800" in errors[0]


@pytest.mark.parametrize("lines,expected", [(900, []), (901, ["exceeds 900"]),
                                            (899, ["lower its budget to 899"]),
                                            (800, ["remove its exception"])])
def test_legacy_budget_ratchets(tmp_path, lines, expected):
    root = make_tree(tmp_path, lines=lines, baseline={"src/gestaltdb/example.py": 900})
    errors = quality.check_module_sizes(root)
    assert len(errors) == len(expected)
    for fragment, error in zip(expected, errors):
        assert fragment in error


@pytest.mark.parametrize("budget", [True, "900", 800, -1, None])
def test_invalid_exception_fails(tmp_path, budget):
    errors = quality.check_module_sizes(make_tree(
        tmp_path, baseline={"src/gestaltdb/example.py": budget}))
    assert any("exception must be an integer" in error for error in errors)


def test_deleted_exception_fails(tmp_path):
    root = make_tree(tmp_path, baseline={"src/gestaltdb/deleted.py": 900})
    assert "stale exception" in quality.check_module_sizes(root)[0]


def test_nested_modules_are_checked(tmp_path):
    root = make_tree(tmp_path)
    nested = root / "src/gestaltdb/nested/module.py"
    nested.parent.mkdir()
    nested.write_text("# line\n" * 801, encoding="utf-8")
    assert "nested/module.py" in quality.check_module_sizes(root)[0]


def test_missing_or_invalid_baseline_fails(tmp_path):
    root = make_tree(tmp_path)
    (root / quality.BASELINE).write_text("[]", encoding="utf-8")
    assert "expected a path-to-line-budget object" in quality.check_module_sizes(root)[0]
    (root / quality.BASELINE).write_text("{", encoding="utf-8")
    assert "cannot read baseline" in quality.check_module_sizes(root)[0]
    (root / quality.BASELINE).unlink()
    assert "cannot read baseline" in quality.check_module_sizes(root)[0]
