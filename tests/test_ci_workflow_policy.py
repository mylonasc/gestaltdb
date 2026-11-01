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
    patterns = _event_paths(filename, event)
    for path in changed:
        selected = False
        for pattern in patterns:
            excluded = pattern.startswith("!")
            pattern = pattern.removeprefix("!")
            # GitHub **/ also matches zero directories (unlike fnmatch's slash).
            variants = {pattern}
            while any("**/" in item for item in variants):
                additions = {item.replace("**/", "", 1) for item in variants if "**/" in item}
                if additions <= variants:
                    break
                variants |= additions
            if any(fnmatch.fnmatchcase(path, variant) for variant in variants):
                selected = not excluded  # Ordered negatives exclude only this file.
        if selected:
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
        "planning/reasoning-engine/tracking-status.json", "benchmarks/results/report.json",
        "tests/README.md", "tests/fixtures/released_snapshots/README.md"])


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
    checks = (WORKFLOWS / "documentation-checks.yml").read_text()
    assert "sphinx-build -W" in text
    assert "check_docs.py" in checks
    assert "check_roadmap.py" in checks
    assert "sync_tracker.py --check" in checks
    assert "check_tracking.py\n" in checks
    assert "pytest" not in checks and "sphinx-build" not in checks and "python -m build" not in checks
    assert "pip install -e" not in checks
    assert "--only-binary=:all:" in checks
    assert "group: pages-${{ github.event.pull_request.number || 'deployment' }}" in text


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_planning_changes_do_not_build_sphinx_but_do_validate_references(event):
    changed = ["EPIC_REASONING_ENGINE.md", "gestaltdb_reasoning_feature_tracker.xlsx",
               "planning/reasoning-engine/roadmap.json", "AGENTS.md", "EXAMPLES.md"]
    assert not _selected("docs.yml", changed, event)
    assert _selected("documentation-checks.yml", changed, event)
    assert not _selected("docs.yml", ["README.md", "notebooks/example.ipynb"], event)


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize("path", ["docs/index.rst", "docs/conf.py", "docs/_static/components.svg",
                                 "src/gestaltdb/graphdb.py", "pyproject.toml", "uv.lock",
                                 ".github/workflows/docs.yml"])
def test_sphinx_actual_build_inputs_still_trigger_docs(event, path):
    assert _selected("docs.yml", [path], event)


@pytest.mark.parametrize("filename", MATRICES)
@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_tested_planning_code_is_not_misclassified_as_documentation(filename, event):
    assert _selected(filename, ["planning/reasoning-engine/check_roadmap.py"], event)
    assert _selected(filename, ["tests/test_reasoning_tracker.py"], event)


@pytest.mark.parametrize("filename", ["tests.yml", "reasoning-release.yml"])
@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_fixture_inputs_select_suites_but_fixture_prose_does_not(filename, event):
    prose = "tests/fixtures/released_snapshots/README.md"
    array = "tests/fixtures/released_snapshots/dense_v2/src_int.npy"
    assert not _selected(filename, [prose], event)
    assert _selected(filename, [prose, array], event)
    assert _selected(filename, [array, prose], event)


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_ontology_only_selects_test_modules_it_executes(event):
    assert not _selected("ontology.yml", ["tests/test_sampling.py", "tests/test_networkx_integration.py"], event)
    for path in ["tests/conftest.py", "tests/category_markers.py", "tests/test_reasoning_tracker.py",
                 "tests/test_ontology_compiler.py", "tests/test_temporal_cache_integrity.py",
                 "benchmarks/reasoning_maintenance.py"]:
        assert _selected("ontology.yml", [path], event)


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_frontend_and_framework_prose_does_not_trigger_builds(event):
    assert not _selected("visualization.yml", ["web/README.md", "web/docs/guide.md",
                                               "src/gestaltdb/viz/README.md"], event)
    assert not _selected("framework.yml", ["src/gestaltdb/sampling/README.md"], event)
    for path in ["web/src/App.tsx", "web/package-lock.json", "web/vite.config.ts",
                 "web/src/source-input.md", "src/gestaltdb/viz/static/gestaltdb-viz.js",
                 "src/gestaltdb/viz/static/packaged-asset.md", "pyproject.toml"]:
        assert _selected("visualization.yml", ["web/README.md", path], event)
    assert _selected("framework.yml", ["src/gestaltdb/sampling/batch.py"], event)
    assert not _selected("framework.yml", ["tests/test_sampling_walk_cache.py"], event)
    assert _selected("framework.yml", ["tests/test_sampling_real_frameworks.py"], event)


def test_optional_integrations_installs_native_build_prerequisite():
    text = (WORKFLOWS / "tests.yml").read_text().split("  optional-integrations:", 1)[1]
    assert "libleveldb-dev" in text
    assert text.index("libleveldb-dev") < text.index('.[all,coverage]')
    assert 'python-version: "3.13"' in text
