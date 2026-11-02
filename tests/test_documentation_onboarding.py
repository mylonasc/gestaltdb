"""Keep major audience routes visible and the first-run example executable."""

from pathlib import Path
import re
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
PILLARS = {
    "storage-backends", "serializers", "property-filtering", "cypher",
    "typed-sampling", "random-walks", "temporal-versioning", "modal-epistemic",
    "ontology", "reasoning-typed", "reasoning-negation", "reasoning-temporal",
    "reasoning-authenticated-sessions", "visualization", "networkx", "operations",
}


@pytest.mark.parametrize("name", ["index", "quickstart"])
def test_major_capabilities_have_direct_routes(name):
    text = (ROOT / f"docs/{name}.rst").read_text(encoding="utf-8")
    links = set(re.findall(r":doc:`([^`]+)`", text))
    assert PILLARS <= links
    for target in links:
        assert (ROOT / f"docs/{target}.rst").is_file(), target


def test_navigation_groups_are_unique_and_real():
    text = (ROOT / "docs/index.rst").read_text(encoding="utf-8")
    captions = re.findall(r"^   :caption: (.+)$", text, re.MULTILINE)
    assert len(captions) == len(set(captions)) == 10
    targets = re.findall(r"^   ([a-z][a-z-]+)$", text, re.MULTILINE)
    assert len(targets) == len(set(targets))
    for target in targets:
        assert (ROOT / f"docs/{target}.rst").is_file(), target


def test_readme_goal_routes():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    table = text.split("## Components and Applications", 1)[1].split("```mermaid", 1)[0]
    links = set(re.findall(r"\]\(docs/([a-z-]+)\.rst\)", table))
    assert PILLARS <= links


def test_standalone_quickstart(tmp_path, monkeypatch, capsys):
    pytest.importorskip("lmdb")
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "docs/quickstart.rst").read_text(encoding="utf-8")
    blocks = re.findall(
        r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", text, re.MULTILINE,
    )
    assert len(blocks) == 1, "Quickstart must have one complete, independently executable workflow"
    namespace = {}
    exec(compile(textwrap.dedent(blocks[0]), "docs/quickstart.rst", "exec"), namespace)
    assert namespace["result"].records == [{"drug": "drug-1", "protein": "PTGS1", "score": 0.9}]
    assert "PTGS1" in capsys.readouterr().out
    assert not Path(namespace["root"]).exists(), "Disposable storage should be cleaned up"
