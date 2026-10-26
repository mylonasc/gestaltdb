"""Keep PR cost down without shrinking the selected test suites or real backends.

These checks cover the deliberately simple inline path/matrix declarations.
Actionlint validates complete GitHub expression/YAML syntax separately.
"""

import fnmatch
import json
from pathlib import Path
import re

import pytest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
MATRICES = ("tests.yml", "ontology.yml", "reasoning-release.yml")
CODE_WORKFLOWS = MATRICES + ("codeql.yml", "cflite_pr.yml")


def _event_paths(filename, event="pull_request"):
    text = (WORKFLOWS / filename).read_text()
    block = re.search(rf"(?ms)^  {event}:\n(.*?)(?=^  \w[\w-]*:|^\S|\Z)", text)
    assert block is not None, (filename, event)
    paths = re.search(r"(?ms)^    paths:\n(.*)", block[1])
    assert paths is not None, filename
    return [line.strip()[2:].strip("\"'") for line in paths[1].splitlines()
            if line.strip().startswith("- ")]


def _selected(filename, changed, event="pull_request"):
    for pattern in _event_paths(filename, event):
        # GitHub **/ also matches zero directories (unlike fnmatch's slash).
        variants = {pattern}
        while any("**/" in item for item in variants):
            additions = {item.replace("**/", "", 1) for item in variants if "**/" in item}
            if additions <= variants:
                break
            variants |= additions
        if any(fnmatch.fnmatchcase(path, variant) for path in changed for variant in variants):
            return True
    return False


@pytest.mark.parametrize("filename", MATRICES)
def test_pr_edge_versions_and_post_merge_full_matrix(filename):
    text = (WORKFLOWS / filename).read_text()
    expressions = re.findall(
        r"fromJSON\(github.event_name == 'pull_request' && '([^']+)' \|\| '([^']+)'\)", text)
    assert len(expressions) == (2 if filename == "reasoning-release.yml" else 1)
    for pr, push in expressions:
        assert json.loads(pr) == ["3.10", "3.13"]
        assert json.loads(push) == ["3.10", "3.11", "3.12", "3.13"]


@pytest.mark.parametrize("filename", CODE_WORKFLOWS)
def test_prose_and_evidence_only_pr_does_not_select_code_workflows(filename):
    assert not _selected(filename, ["README.md", "docs/architecture.rst", "docs/_static/components.svg",
        "AGENTS.md", "src/gestaltdb/agent_skill/SKILL.md", "EPIC_REASONING_ENGINE.md",
        "planning/reasoning-engine/tracking-status.json", "benchmarks/results/report.json"])


@pytest.mark.parametrize("filename", CODE_WORKFLOWS)
def test_library_code_and_dependency_changes_still_select_code_workflows(filename):
    assert _selected(filename, ["src/gestaltdb/graphdb.py"])
    assert _selected(filename, ["pyproject.toml"])
    assert _selected(filename, ["uv.lock"])


@pytest.mark.parametrize("filename", MATRICES)
def test_regressions_and_workflow_edits_select_their_suites(filename):
    assert _selected(filename, ["tests/test_reasoning_sessions.py"])
    assert _selected(filename, [f".github/workflows/{filename}"])


def test_full_suite_and_backend_preflight_are_not_replaced_by_a_subset():
    text = (WORKFLOWS / "reasoning-release.yml").read_text()
    assert "python scripts/check_release_backends.py" in text
    assert "python -m pytest -q -ra --junitxml=release-tests.xml" in text
    assert "coverage,backends,serializers,rdf,polars,bloom,docs" in text
    assert "Test isolated wheel and baseline database compatibility" in text


def test_docs_validation_remains_and_pr_builds_do_not_queue_behind_deployment():
    text = (WORKFLOWS / "docs.yml").read_text()
    assert "sphinx-build -W" in text
    assert "check_docs.py" in text
    assert "check_roadmap.py" in text
    assert "group: pages-${{ github.event.pull_request.number || 'deployment' }}" in text


def test_optional_integrations_installs_native_build_prerequisite():
    text = (WORKFLOWS / "tests.yml").read_text().split("  optional-integrations:", 1)[1]
    assert "libleveldb-dev" in text
    assert text.index("libleveldb-dev") < text.index('.[all,coverage]')
    assert 'python-version: "3.13"' in text
