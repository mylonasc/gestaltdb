"""The existing glossary is the sole source for linked term definitions."""

from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


nodes = pytest.importorskip("docutils.nodes", reason="Install the docs extra for glossary rendering tests")
HELPER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "docs/_ext/terminology.py"))


def test_terms_normalize_only_lookup_keys(tmp_path):
    glossary = tmp_path / "glossary.rst"
    glossary.write_text(".. glossary::\n\n   Datalog\n      A rule language. More detail.\n\n   closure\n      All consequences.\n")
    callbacks = {}
    app = SimpleNamespace(srcdir=tmp_path, env=SimpleNamespace(note_dependency=lambda path: None),
                          connect=lambda event, callback, **kwargs: callbacks.update({event: callback}))
    HELPER["setup"](app)
    tree = nodes.document("", "")
    for fragment in ("term-Datalog", "term-datalog", "term-closure"):
        tree += nodes.reference("", fragment, refuri=f"glossary.html#{fragment}")
    callbacks["doctree-resolved"](app, tree, "other")
    for link in tree.findall(nodes.reference):
        assert "term-definition-link" in link["classes"]
        assert link["reftitle"] in {"A rule language.", "All consequences."}
        assert link["refuri"].endswith(link.astext())


def test_duplicate_terms_fail_case_insensitively(tmp_path):
    path = tmp_path / "glossary.rst"
    path.write_text("   Datalog\n      First.\n\n   datalog\n      Second.\n")
    with pytest.raises(ValueError, match="Duplicate glossary term"):
        HELPER["definitions"](path)
