"""Tests for packaged agent-facing GestaltDB documentation."""

from __future__ import annotations

from gestaltdb import agent_docs


def test_agent_docs_topics_and_skill_are_available():
    assert "cypher" in agent_docs.TOPICS
    assert "backends" in agent_docs.TOPICS
    assert "temporal" in agent_docs.TOPICS
    skill = agent_docs.read_skill()
    assert "GestaltDB User Guide For Agents" in skill
    assert "python -m gestaltdb.agent_docs" in skill


def test_agent_docs_extract_examples():
    cypher_doc = agent_docs.read_topic("cypher")
    examples = agent_docs.extract_python_examples(cypher_doc)
    assert examples
    assert "GraphDB" in examples[0]
    assert "graph.query" in examples[0]


def test_agent_docs_cli_list_and_get(capsys):
    assert agent_docs.main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "quickstart" in listed

    assert agent_docs.main(["get", "indexing", "--rules"]) == 0
    rules = capsys.readouterr().out
    assert "Property indexes" in rules


def test_agent_docs_search(capsys):
    assert agent_docs.main(["search", "rebuild_deferred_indexes", "--limit", "1"]) == 0
    output = capsys.readouterr().out
    assert "indexing" in output or "skill" in output
    assert "page 1/" in output


def test_agent_docs_search_pagination_and_context(capsys):
    assert agent_docs.main(["search", "GraphDB", "--limit", "2", "--page", "2", "--context", "1"]) == 0
    output = capsys.readouterr().out
    assert "page 2/" in output
    assert "snippet:" in output
    assert output.count("[") <= 2


def test_agent_docs_install_opencode_skill(tmp_path):
    destination = agent_docs.install_opencode_skill(tmp_path)
    assert destination == tmp_path / ".opencode" / "skills" / "gestaltdb-user-guide"
    assert (destination / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert (destination / "references" / "quickstart.md").exists()


def test_agent_docs_install_opencode_skill_cli(tmp_path, capsys):
    assert agent_docs.main(["install-opencode-skill", "--target", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "installed GestaltDB opencode skill" in output

    assert agent_docs.main(["install-opencode-skill", "--target", str(tmp_path)]) == 2
    error = capsys.readouterr().err
    assert "pass --force" in error

    assert agent_docs.main(["install-opencode-skill", "--target", str(tmp_path), "--force"]) == 0
