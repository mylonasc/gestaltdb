"""The documentation inventory catches additions and broken references without extras."""

from __future__ import annotations

import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = runpy.run_path(str(ROOT / "planning/documentation-review/check_inventory.py"))


@pytest.fixture
def inventory_root(tmp_path):
    """Build a tiny on-disk mirror of inventory references, not backend fixtures."""
    for name in ("capabilities.json", "documents.json"):
        source = ROOT / "planning/documentation-review" / name
        target = tmp_path / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    caps = CHECKER["load"](tmp_path, "capabilities.json")["capabilities"]
    groups = CHECKER["load"](tmp_path, "documents.json")["groups"]
    paths = {path for row in caps for field in ("sources", "tests", "existing_docs") for path in row[field]}
    paths.update(path for group in groups for path in group["files"])
    for relative in sorted(paths, key=lambda value: (value.count("/"), value), reverse=True):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
    registry = tmp_path / "src/gestaltdb/agent_docs.py"
    registry.write_text((ROOT / registry.relative_to(tmp_path)).read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_repository_inventory():
    assert CHECKER["validate"](ROOT) == []
    table = CHECKER["matrix"](ROOT)
    assert "Cypher" in table
    assert "backend" in table
    assert "experimental" in table


def test_fixture_inventory(inventory_root):
    assert CHECKER["validate"](inventory_root) == []


@pytest.mark.parametrize("path", ["docs/new-guide.rst", "src/gestaltdb/agent_skill/references/new-topic.md"])
def test_new_document_requires_classification(inventory_root, path):
    (inventory_root / path).touch()
    assert f"unclassified document: {path}" in CHECKER["validate"](inventory_root)


def test_missing_citation(inventory_root):
    (inventory_root / "tests/test_serializers.py").unlink()
    assert any("missing path tests/test_serializers.py" in error for error in CHECKER["validate"](inventory_root))


def test_duplicate_classification(inventory_root):
    path = inventory_root / "planning/documentation-review/documents.json"
    data = json.loads(path.read_text())
    data["groups"].append(data["groups"][0])
    path.write_text(json.dumps(data))
    assert "duplicate document classification: README.md" in CHECKER["validate"](inventory_root)


def test_invalid_status_and_unsafe_path(inventory_root):
    path = inventory_root / "planning/documentation-review/capabilities.json"
    data = json.loads(path.read_text())
    data["capabilities"][0]["status"] = "everything-supported"
    data["capabilities"][0]["sources"] = ["../outside.py"]
    path.write_text(json.dumps(data))
    errors = CHECKER["validate"](inventory_root)
    assert "graph-model: invalid status" in errors
    assert "graph-model sources: unsafe path ../outside.py" in errors


def test_topic_registry_drift(inventory_root):
    path = inventory_root / "src/gestaltdb/agent_docs.py"
    path.write_text(path.read_text().replace('"references/ontology.md"', '"references/missing.md"'))
    errors = CHECKER["validate"](inventory_root)
    assert any("missing topic resource:" in error for error in errors)
    assert any("unregistered topic resource:" in error for error in errors)
