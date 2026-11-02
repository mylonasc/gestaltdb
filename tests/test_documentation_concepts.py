"""Examples, glossary entries and generated figures stay reproducible."""

from pathlib import Path
import re
import runpy
import textwrap
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("page", ["reasoning-introduction", "reasoning-temporal"])
def test_reasoning_introductions_run_without_backend(page):
    text = (ROOT / f"docs/{page}.rst").read_text(encoding="utf-8")
    blocks = re.findall(r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", text, re.MULTILINE)
    assert len(blocks) == 1
    exec(compile(textwrap.dedent(blocks[0]), f"docs/{page}.rst", "exec"), {})


def test_generated_figures_match_committed_artifacts():
    generator = runpy.run_path(str(ROOT / "scripts/generate_documentation_figures.py"))
    figures = generator["figures"]()
    assert len(figures) == 3
    for name, svg in figures.items():
        assert (ROOT / "docs/_static/examples" / name).read_text(encoding="utf-8") == svg
        root = ET.fromstring(svg)
        assert root.attrib["role"] == "img"
        assert root.find("{http://www.w3.org/2000/svg}title") is not None
    assert "Aspirin" in figures["property-graph-example.svg"]
    assert "Ibuprofen" in figures["cypher-example.svg"]
    assert "candidateFor" in figures["reasoning-witness.svg"]
    assert "stroke-dasharray" in figures["reasoning-witness.svg"]


def test_witness_is_explained_before_specialist_examples():
    for page in ("ontology", "reasoning-typed", "reasoning-proofs"):
        text = (ROOT / f"docs/{page}.rst").read_text()
        before_code = text.split(".. code-block::", 1)[0]
        assert ":doc:`reasoning-introduction`" in before_code
        assert "witness" in before_code.lower()
    glossary = (ROOT / "docs/glossary.rst").read_text()
    for term in ("witness", "support", "fixpoint", "stratification", "CSR", "fanout", "arity", "grounded"):
        assert f"\n   {term}\n" in glossary
