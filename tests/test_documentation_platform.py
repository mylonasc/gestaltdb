"""Keep platform workflows and dependency promises tied to the shipped surface."""

import ast
from pathlib import Path
import re
import runpy


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = runpy.run_path(str(ROOT / "docs/_ext/example_inventory.py"))


def text(page):
    return (ROOT / f"docs/{page}.rst").read_text(encoding="utf-8")


def test_visualization_first_examples_cover_detached_query_sample_and_proof():
    rows = [row for row in EXAMPLES["catalog"]() if row["path"] == "docs/visualization.rst"]
    assert len(rows) == 2 and all(row["kind"] == "standalone" for row in rows)
    graph, proof = rows
    for term in ("graph.visualize", "seeds=", "figure.save(", "figure.save_svg", "assert"):
        assert term in graph["code"]
    assert proof["extras"] == []
    assert "visualize_proof_explanation" in proof["code"] and "visualize_ontology" in proof["code"]
    assert "   visualization-reference\n" in text("visualization")
    for term in ("not access control", "not capture browser", "same embedded topology", "PNG rasterization"):
        assert term in text("visualization")


def test_networkx_task_checks_multigraph_and_static_exchange():
    source = text("networkx")
    for term in ("isolated node", 'key="colleague"', 'has_edge("bob", "bob", "self")',
                 "restored.imported_nodes", "exported.edges(data=True)", "DTD", "not one atomic",
                 "out-of-core loading", "GEXF writing"):
        assert term in source


def test_notebook_example_no_longer_depends_on_shared_open_handles():
    rows = [row for row in EXAMPLES["catalog"]() if row["path"] == "docs/notebooks.rst"]
    assert len(rows) == 2 and all(row["kind"] == "standalone" for row in rows)
    assert rows[0]["runner"] == "variants"
    assert "with TemporaryDirectory()" in rows[0]["code"]
    assert "with GraphDB.create(" in rows[0]["code"]
    assert "separately configured local model" in text("notebooks")
    assert "different environments" in text("notebooks")


def test_installation_follows_declared_python_and_dependency_floors():
    project = (ROOT / "pyproject.toml").read_text()
    required = re.search(r'^requires-python = "([^"]+)"', project, re.MULTILINE)[1]
    assert required in text("installation")
    floor = re.search(r"pyrex-rocksdb>=[0-9.]+", project)[0]
    assert floor in text("installation") and floor in text("operations")
    section = project.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    extras = {name: ast.literal_eval(value) for name, value in re.findall(r"^([\w-]+) = (\[[^\n]+\])$", section, re.MULTILINE)}
    for name in ("networkx", "rdf", "arrow", "polars", "fast-ingest", "backends", "serializers"):
        assert name in extras and f"``{name}``" in text("installation")
    assert any(item.startswith("defusedxml") for item in extras["networkx"])
    assert not any(item.startswith("rdflib") for item in extras["all"])
    assert "separate ``rdf`` extra" in text("installation")


def test_performance_and_integration_routes_keep_stage_boundaries():
    source = text("performance")
    for term in ("Ingestion", "Querying", "Snapshot construction", "repeated draws", "Reasoning",
                 "Source/state authentication", "Publication", "Exchange and visualization",
                 "not automatically an end-to-end", "not wall-time or RSS guarantees"):
        assert term in source
    for page in ("architecture", "api"):
        for route in ("networkx", "visualization", "notebooks", "installation", "performance"):
            assert f":doc:`{route}`" in text(page)
