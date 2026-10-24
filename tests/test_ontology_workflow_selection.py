"""RE follow-through #247: ontology CI must discover test modules, not list them.

Guards against reverting to a stale hand-maintained module list in
``.github/workflows/ontology.yml``: the pytest gate must select
``tests/test_reasoning*.py`` and ``tests/test_ontology*.py`` via shell
globs, and no explicit per-file ``tests/test_*.py`` path may appear in
the workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ontology.yml"
EXPLICIT_MODULE = re.compile(r"tests/test_(?:reasoning|ontology)[\w-]*\.py")


def _workflow_text() -> str:
    if not WORKFLOW.is_file():
        pytest.skip("ontology workflow not present in this checkout")
    return WORKFLOW.read_text(encoding="utf-8")


def test_gate_uses_glob_discovery():
    text = _workflow_text()
    assert "tests/test_reasoning*.py" in text
    assert "tests/test_ontology*.py" in text


def test_gate_has_no_stale_explicit_module_list():
    text = _workflow_text()
    offenders = sorted(set(EXPLICIT_MODULE.findall(text)))
    assert offenders == [], f"explicit test module paths reintroduced: {offenders}"


def test_gate_includes_temporal_storage_integrity_regressions():
    text = _workflow_text()
    assert "tests/test_temporal*.py" in text
    assert "tests/test_graphdb_versions.py" in text


def test_gate_includes_rule_catalog_regressions():
    text = _workflow_text()
    assert "tests/test_rule*.py" in text
