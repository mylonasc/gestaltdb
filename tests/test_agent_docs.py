"""Tests for packaged agent-facing GestaltDB documentation."""

from __future__ import annotations

from gestaltdb import agent_docs


def test_agent_docs_topics_and_skill_are_available():
    assert "cypher" in agent_docs.TOPICS
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
    assert agent_docs.main(["search", "rebuild_deferred_indexes"]) == 0
    output = capsys.readouterr().out
    assert "indexing" in output or "skill" in output
