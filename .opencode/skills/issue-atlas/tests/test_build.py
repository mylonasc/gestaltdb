"""Offline issue atlas contracts; no GitHub credentials or network required."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys


PATH = Path(__file__).resolve().parents[1] / "scripts/build.py"
spec = importlib.util.spec_from_file_location("issue_map_build", PATH)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def issue(number, state="open", body="Description"):
    return {"number": number, "title": f"Issue {number}", "body": body, "state": state,
            "html_url": f"https://github.com/o/r/issues/{number}", "labels": []}


def snapshot():
    return {"repository": "o/r", "fetched_at": "2026-10-23T00:00:00+00:00", "days": 30,
            "issues": [
                {"issue": issue(1), "dependencies": [], "children": [issue(2)], "comments": []},
                {"issue": issue(2, body="See #3 but this is not a blocker"),
                 "dependencies": [], "children": [], "comments": []},
                {"issue": issue(3, "closed"), "dependencies": [issue(2)], "children": [], "comments": []}],
            "external": [], "overlay": {
                "nodes": [{"number": 1, "kind": "roadmap"},
                          {"number": 2, "kind": "epic", "phase": "E1"},
                          {"number": 3, "kind": "feature", "phase": "E1",
                           "description": "Deliver X", "delivery": "integrated"}],
                "links": [{"source": 1, "target": 2, "kind": "parent"},
                          {"source": 2, "target": 3, "kind": "dependency"}]}}


def test_roadmap_native_dependencies_and_mentions_are_distinct():
    data = build.graph_data(snapshot())
    edges = {(e["source"], e["target"], e["kind"]) for e in data["links"]}
    assert ("2", "3", "dependency") in edges  # prerequisite → dependent
    assert ("2", "3", "reference") in edges  # mention is separate
    assert ("3", "2", "dependency") not in edges
    assert ("1", "2", "parent") in edges
    feature = next(n for n in data["nodes"] if n["id"] == "3")
    assert feature["delivery"] == "integrated" and feature["state"] == "closed"
    assert feature["phase"] == "E1"
    assert feature["description"] == "Deliver X"


def test_html_safely_embeds_untrusted_issue_content():
    data = build.graph_data(snapshot())
    data["nodes"][0]["title"] = '</script><script>alert("x")</script>'
    html = build.render(data, "// d3 stub")
    assert '</script><script>alert' not in html
    assert r"\u003c/script\u003e" in html
    assert "/*__DATA__*/" not in html and "/*__D3__*/" not in html
    assert "textContent" in html


def test_paginated_gh_json_without_slurp(monkeypatch):
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, '[{"number":1}]\n[{"number":2}]', ''))
    assert build.api("endpoint") == [{"number": 1}, {"number": 2}]


def test_description_strips_fences_and_links():
    assert build.brief("## Summary\n[A link](https://x)\n```py\nsecret()\n```\n**done**") == "A link done"
    assert build.brief("Parent #200\n## Problem / user value\nUseful description.\n## Acceptance\nExtra.") == "Useful description."


def test_missing_context_nodes_do_not_create_dangling_edges():
    value = snapshot()
    value["issues"][0]["dependencies"] = [issue(99)]
    data = build.graph_data(value)
    ids = {node["id"] for node in data["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in data["links"])


def test_no_roadmap_is_required_for_an_unrelated_repository():
    value = snapshot()
    value.pop("overlay")
    value["repository"] = "another-team/unrelated-project"
    data = build.graph_data(value)
    assert data["repository"] == "another-team/unrelated-project"
    assert data["title"] == "another-team/unrelated-project · Issue atlas"
    assert all(n["kind"] == "issue" for n in data["nodes"])
    assert ("2", "3", "dependency") in {(e["source"], e["target"], e["kind"]) for e in data["links"]}


def test_config_paths_and_cli_precedence(tmp_path, monkeypatch):
    directory = tmp_path / "settings"
    directory.mkdir()
    config = directory / "atlas.json"
    config.write_text(json.dumps({"repo": "team/project", "days": 60, "comments": True,
                                  "output": "site/index.html", "title": "Custom atlas"}))
    monkeypatch.chdir(tmp_path)
    args = build.parse_args(["--config", str(config), "--days", "7", "--no-comments",
                             "--snapshot", "local.json"])
    assert args.days == 7 and args.repo == "team/project" and not args.comments
    assert args.output == directory / "site/index.html"
    assert args.snapshot == tmp_path / "local.json"
    assert args.title == "Custom atlas"
    assert args.d3 == tmp_path / "artifacts/issue-atlas/d3.v7.9.0.min.js"


def test_invalid_roadmap_and_unknown_config_fail(tmp_path):
    import pytest

    for value in ({"nodes": [{"number": True}]},
                  {"nodes": [{"number": 1, "state": "closed"}]},
                  {"links": [{"source": 1, "target": 2, "kind": "guessed"}]},
                  {"nodes": [{"number": 1}, {"number": 1}]}):
        with pytest.raises(ValueError):
            build.validate_roadmap(value)
    config = tmp_path / "bad.json"
    config.write_text('{"typo": true}')
    with pytest.raises(SystemExit):
        build.parse_args(["--config", str(config)])
    with pytest.raises(SystemExit):
        build.parse_args(["--snapshot", "same", "--output", "same"])


def test_skill_can_be_copied_outside_the_repository_and_render(tmp_path):
    # Copy ONLY the skill. No source package, planning directory, .git, or
    # repository-specific wrapper accompanies it.
    skill = tmp_path / "global-skills/issue-atlas"
    shutil.copytree(PATH.parent.parent, skill, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run([sys.executable, str(skill / "scripts/build.py"), "--help"],
                   cwd=tmp_path, check=True, capture_output=True)
    portable_spec = importlib.util.spec_from_file_location("copied_atlas", skill / "scripts/build.py")
    portable = importlib.util.module_from_spec(portable_spec)
    portable_spec.loader.exec_module(portable)
    value = snapshot()
    value.pop("overlay")
    value["repository"] = "other/repository"
    value["title"] = "Independent atlas"
    html = portable.render(portable.graph_data(value), "// offline D3 test placeholder")
    assert "Independent atlas" in html and '"repository": "other/repository"' in html
    assert "/*__DATA__*/" not in html


def test_fetch_without_comments_or_roadmap_uses_only_the_selected_repository(monkeypatch):
    calls = []

    def fake_api(endpoint):
        calls.append(endpoint)
        if "state=open" in endpoint:
            return [dict(issue(15), comments=3)]
        return []

    monkeypatch.setattr(build, "api", fake_api)
    value = build.fetch_snapshot("different/project", 30, workers=1, comments=False)
    assert value["repository"] == "different/project" and value["overlay"] == {}
    assert len(value["issues"]) == 1 and not value["comments_included"]
    assert all(endpoint.startswith("repos/different/project/") for endpoint in calls)
    assert not any("/comments" in endpoint for endpoint in calls)


def test_offline_roadmap_cannot_silently_drop_missing_issues():
    import pytest

    value = snapshot()
    value["overlay"]["nodes"].append({"number": 999, "kind": "feature"})
    with pytest.raises(ValueError, match="refresh"):
        build.graph_data(value)
