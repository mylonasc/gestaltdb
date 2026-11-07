"""Standalone learning paths execute without storage/solver dependencies."""

from pathlib import Path
import re
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def python_blocks(page):
    source = (ROOT / f"docs/{page}.rst").read_text(encoding="utf-8")
    return [textwrap.dedent(block) for block in re.findall(
        r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", source, re.MULTILINE)]


def test_typed_tutorial_examples_are_independent():
    blocks = python_blocks("reasoning-typed-tutorial")
    assert len(blocks) == 4
    for index, code in enumerate(blocks):
        exec(compile(code, f"typed-tutorial-{index}", "exec"), {})


def test_classification_precedes_optional_rdf_and_adapters():
    source = (ROOT / "docs/ontology.rst").read_text()
    assert source.index(".. code-block:: python") < source.index("Optional offline RDF import")
    exec(compile(python_blocks("ontology")[0], "ontology-first-example", "exec"), {})
    for term in ("Classification", "Validation", "Hierarchy expansion", "not full RDFS/OWL"):
        assert term in source


def test_engine_choice_and_progressive_routes_are_visible():
    intro = (ROOT / "docs/reasoning-introduction.rst").read_text()
    assert "Choose binary or typed rules" in intro
    for term in ("Fact(subject, predicate, object)", "TypedFact(predicate, [argument, ...])", "stored-rules"):
        assert term in intro
    source = (ROOT / "docs/reasoning-typed-tutorial.rst").read_text()
    for target in ("reasoning-demand", "reasoning-maintenance", "reasoning-equality",
                   "reasoning-validation", "reasoning-temporal", "stored-rules"):
        assert f":doc:`{target}`" in source


def test_range_task_examples_are_independent():
    blocks = python_blocks("reasoning-range-tutorial")
    assert len(blocks) == 3
    for index, code in enumerate(blocks):
        exec(compile(code, f"range-tutorial-{index}", "exec"), {})


def test_range_introduction_checks_gaps_boundaries_and_evidence():
    blocks = python_blocks("reasoning-temporal")
    assert len(blocks) == 1
    exec(compile(blocks[0], "range-first-example", "exec"), {})
    text = (ROOT / "docs/reasoning-temporal.rst").read_text()
    for term in ("not temporal inference", "gap", "change point", "proofs_truncated"):
        assert term in text
    for target in ("reasoning-range-admission", "reasoning-bound-range-publication",
                   "reasoning-range-tutorial", "temporal-introduction"):
        assert f":doc:`{target}`" in text


def test_range_authority_and_scope_limits_are_visible():
    text = (ROOT / "docs/reasoning-range-tutorial.rst").read_text()
    for term in ("not a negative claim", "proofs_truncated", "each completed segment", "sinks", "Computation, admission",
                 "not automatic real-world identity", "stage_range"):
        assert term in text


def test_advanced_entry_examples_execute_independently():
    for page in ("reasoning-workflows", "reasoning-gates", "reasoning-scopes"):
        blocks = python_blocks(page)
        assert len(blocks) == 1
        exec(compile(blocks[0], page, "exec"), {})


def test_advanced_hub_covers_all_owned_pages_and_roles():
    import json

    groups = json.loads((ROOT / "planning/documentation-review/documents.json").read_text())["groups"]
    hub = (ROOT / "docs/reasoning-workflows.rst").read_text()
    children = set(re.findall(r"^   ([a-z][a-z0-9-]+)$", hub, re.MULTILINE))
    for group in groups:
        if group["owner"] != "DOC-08":
            continue
        assert group["disposition"] == "keep"
        for filename in group["files"]:
            page = Path(filename).stem
            if page != "reasoning-workflows":
                assert page in children, f"missing intentional hub path: {filename}"
    index = (ROOT / "docs/index.rst").read_text()
    assert "   reasoning-workflows\n" in index
    for child in children:
        assert (ROOT / f"docs/{child}.rst").is_file()


def test_advanced_hub_keeps_authority_and_optional_paths_distinct():
    source = (ROOT / "docs/reasoning-workflows.rst").read_text()
    for term in ("Ordinary extraction remains the default", "Indexed Python is the",
                 "not user login", "independent proof certificate", "grants no publication authority",
                 "experimental closure-only", "benchmark", "exact", "partial closure"):
        assert term in source
    for target in ("reasoning-current-admission", "reasoning-frontier", "reasoning-sessions",
                   "reasoning-authenticated-sessions", "reasoning-range-admission",
                   "reasoning-bound-range-publication", "reasoning-queries"):
        assert f":doc:`{target}`" in source
