"""Offline issue atlas contracts; no GitHub credentials or network required."""

import importlib.util
from pathlib import Path
import subprocess


PATH = Path(__file__).resolve().parents[1] / "scripts/issue_map/build.py"
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
            "external": [], "register": {"roadmap": 1, "epics": {"E1": 2}, "features": {"F1": 3}},
            "roadmap": {"epics": [{"id": "E1", "depends": [], "goal": "Phase goal"}],
                        "features": [{"id": "F1", "epic": "E1", "depends": ["E1"], "acceptance": ["Deliver X"]}],
                        "delivery": {"F1": {"status": "integrated"}}}}


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
