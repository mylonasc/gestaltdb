"""Reviewed source examples execute independently; fragments never do."""

import ast
import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / "docs/_ext/example_inventory.py"))
ROWS = HELPER["catalog"](ROOT)
STANDALONE = [row for row in ROWS if row["runner"] == "execute"]
QUERIES = [(row["id"], query) for row in ROWS if row["kind"] != "pseudocode"
           for query in ([row["code"]] if row["language"] == "cypher"
                         else HELPER["literal_queries"](row["code"]))]


def test_all_source_examples_have_reviewed_execution_scope():
    assert HELPER["validate"](ROOT) == []
    assert HELPER["local_link_errors"](ROOT) == []


def test_variant_ownership_matches_the_existing_capability_catalog():
    helper = runpy.run_path(str(ROOT / "docs/_ext/database_examples.py"))
    specs = [spec for page in helper["declarations"]()
             for spec in helper["page_specifications"]((ROOT / f"docs/{page}.rst").read_text(), page)]
    expected = {spec["id"] for spec in specs if not spec["fragment"]}
    actual = {f"{Path(row['path']).stem}-{row['index']}" for row in ROWS if row["runner"] == "variants"}
    assert actual == expected


@pytest.mark.parametrize("row", STANDALONE, ids=[row["id"] for row in STANDALONE])
def test_standalone_documentation_example(row, tmp_path, monkeypatch):
    for extra in row["extras"]:
        pytest.importorskip(HELPER["EXTRAS"][extra], reason=f"example requires gestaltdb[{extra}]")
    monkeypatch.chdir(tmp_path)
    exec(compile(row["code"], row["id"], "exec"), {})
    assert not list(tmp_path.iterdir()), "Standalone examples must clean their disposable artifacts"


@pytest.mark.parametrize("name,query", QUERIES, ids=[f"{name}-{i}" for i, (name, _) in enumerate(QUERIES)])
def test_documented_literal_cypher_parses(name, query):
    from gestaltdb.query_engine.cypher.parser import parse_ast

    assert parse_ast(query) is not None, name


def test_standalone_database_examples_declare_disposable_lifetimes():
    for row in ROWS:
        if row["kind"] != "standalone" or row["language"] != "python":
            continue
        tree = ast.parse(row["code"])
        calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        if calls & {"GraphDB", "GraphDB.create", "GraphDB.open"}:
            assert "TemporaryDirectory" in calls, row["id"]
            assert any(call.endswith(".close") for call in calls) or any(
                isinstance(node, ast.With) and any(
                    isinstance(item.context_expr, ast.Call)
                    and ast.unparse(item.context_expr.func) in {"GraphDB.create", "GraphDB.open"}
                    for item in node.items) for node in ast.walk(tree)
            ), row["id"]


def test_rst_extractor_handles_options_nested_blocks_and_real_boundaries():
    source = (".. code-block:: python\n   :linenos:\n\n   value = 1\n\nProse.\n\n"
              "* Item\n\n   .. code-block:: cypher\n\n      RETURN 1 AS value\n\nEnd.\n")
    rows = HELPER["blocks"](source, "docs/test.rst")
    assert [row["code"] for row in rows] == ["value = 1", "RETURN 1 AS value"]
    assert [row["line"] for row in rows] == [1, 10]
    assert all(source[row["start"]:].lstrip().startswith(".. code-block::") for row in rows)


def test_markdown_extractor_ignores_non_python_fences_and_keeps_offsets():
    source = "```text\n```python\nnot_code\n```\n\n~~~python\nassert True\n~~~\n"
    rows = HELPER["blocks"](source, "README.md")
    assert len(rows) == 1 and rows[0]["code"] == "assert True"
    with pytest.raises(ValueError, match="unclosed"):
        HELPER["blocks"]("```python\nx = 1\n", "README.md")


@pytest.fixture
def example_root(tmp_path):
    (tmp_path / "docs/_ext").mkdir(parents=True)
    (tmp_path / "src/gestaltdb/agent_skill").mkdir(parents=True)
    for name in ("README.md", "EXAMPLES.md", "AGENTS.md"):
        (tmp_path / name).write_text("")
    source = ".. code-block:: python\n\n   assert True\n"
    (tmp_path / "docs/test.rst").write_text(source)
    items = HELPER["blocks"](source, "docs/test.rst")
    data = {"schema_version": 1, "examples": [{
        "path": "docs/test.rst", "sha256": HELPER["page_digest"](items),
        "groups": [["execute", [0], []]],
    }]}
    (tmp_path / HELPER["CATALOG"]).write_text(json.dumps(data))
    return tmp_path


def test_new_or_changed_examples_cannot_silently_escape_review(example_root):
    assert HELPER["validate"](example_root) == []
    (example_root / "docs/new.rst").write_text(".. code-block:: python\n\n   assert False\n")
    assert any("unclassified" in error for error in HELPER["validate"](example_root))
    (example_root / "docs/test.rst").write_text(".. code-block:: python\n\n   assert False\n")
    with pytest.raises(ValueError, match="code changed"):
        HELPER["validate"](example_root)


def test_duplicate_classification_is_rejected(example_root):
    path = example_root / HELPER["CATALOG"]
    data = json.loads(path.read_text())
    data["examples"][0]["groups"].append(["compile", [0], []])
    path.write_text(json.dumps(data))
    assert any("duplicate example" in error for error in HELPER["validate"](example_root))


def test_local_links_check_files_without_fetching_external_urls(example_root):
    path = example_root / "README.md"
    path.write_text("[ok](docs/test.rst) [external](https://example.invalid/no) [local](#section)")
    assert HELPER["local_link_errors"](example_root) == []
    path.write_text("[bad](docs/missing.rst#section)")
    assert HELPER["local_link_errors"](example_root) == [
        "README.md: missing local link docs/missing.rst#section"]


def test_status_labels_distinguish_installation_and_shared_state():
    assert "gestaltdb[arrow,lmdb]" in HELPER["status_text"]({
        "kind": "standalone", "runner": "execute", "extras": ["arrow", "lmdb"], "reason": "Loads a table.",
    })
    label = HELPER["status_text"]({"kind": "illustrative", "runner": "compile", "reason": "Requires a graph."})
    assert label == "**Illustrative fragment (not standalone).**"


def test_sphinx_hook_labels_source_before_tab_rewriting(example_root):
    pytest.importorskip("sphinx")
    from types import SimpleNamespace

    hooks, dependencies = [], []
    app = SimpleNamespace(srcdir=example_root / "docs", env=SimpleNamespace(note_dependency=dependencies.append),
                          connect=lambda *args, **kwargs: hooks.append((args, kwargs)))
    HELPER["setup"](app)
    assert hooks[0][0][0] == "source-read" and hooks[0][1]["priority"] < 500
    source = [(example_root / "docs/test.rst").read_text()]
    hooks[0][0][1](app, "test", source)
    assert "**Standalone example.**" in source[0]
    assert "   assert True\n" in source[0]
    assert str(example_root / HELPER["CATALOG"]) in dependencies
