"""Execute tutorial content directly; Sphinx alone does not test code cells."""

import importlib.util
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from xml.etree import ElementTree

import pytest


TUTORIAL = Path(__file__).resolve().parents[1] / "docs" / "onto-tutorial"


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, TUTORIAL / "toolkit" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", sorted((TUTORIAL / "notebooks").glob("*.ipynb")), ids=lambda p: p.name)
def test_notebook_cells(path, tmp_path, monkeypatch):
    if path.name.startswith("nb03"):
        pytest.importorskip("lmdb")
    monkeypatch.chdir(tmp_path)
    notebook = json.loads(path.read_text())
    cell_ids = [cell["id"] for cell in notebook["cells"]]
    assert len(cell_ids) == len(set(cell_ids))
    namespace = {"__name__": "__main__"}
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            exec(compile(source, f"{path.name}:cell-{index}", "exec"), namespace)


@pytest.mark.parametrize("chapter", [p for p in sorted(TUTORIAL.glob("ch*.rst"))
                                     if ".. code-block:: python" in p.read_text()],
                         ids=lambda p: p.stem)
def test_chapter_code(chapter, tmp_path, monkeypatch):
    text = chapter.read_text()
    blocks = re.findall(r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", text, re.MULTILINE)
    assert blocks, "the chapter's Python block must be executable, not silently ignored"
    if 'backend="lmdb"' in text:
        pytest.importorskip("lmdb")
    monkeypatch.chdir(tmp_path)
    namespace = {"__name__": "__main__"}
    for block in blocks:
        exec(compile(textwrap.dedent(block), str(chapter), "exec"), namespace)


def check_diagram(path):
    root = ElementTree.parse(path).getroot()
    assert root.get("role") == "img" and root.get("aria-label")
    ns = {"svg": "http://www.w3.org/2000/svg"}
    nodes = root.findall('.//svg:g[@class="node"]', ns)
    edges = root.findall('.//svg:g[@class="edge"]', ns)
    assert nodes and edges
    assert all(node.find("svg:path", ns) is not None for node in nodes)  # rounded boxes
    assert all("C" in edge.find("svg:path", ns).get("d") for edge in edges)
    fonts = [float(element.get("font-size")) for element in root.findall('.//svg:text', ns)]
    assert min(fonts) >= 12 and max(fonts) >= 24
    return {group.get("id") for group in nodes + edges}


def test_committed_figures_are_readable():
    for path in (TUTORIAL / "figures").glob("*.svg"):
        check_diagram(path)


def test_graphviz_regeneration_is_repeatable(tmp_path):
    if shutil.which("dot") is None:
        pytest.skip("Graphviz is only required for figure regeneration")
    landscape = load_helper("landscape")
    first = landscape.render_all(str(tmp_path / "first"))
    second = landscape.render_all(str(tmp_path / "second"))
    for generated, repeated in zip(first, second):
        generated = Path(generated)
        assert generated.read_bytes() == Path(repeated).read_bytes()
        assert check_diagram(generated) == check_diagram(TUTORIAL / "figures" / generated.name)
    for stage in (1, 2, 3):
        nodes, edges = landscape.build_stage(stage)
        ids = {node.get_id for node in nodes}
        assert all(edge.source in ids and edge.target in ids for edge in edges)
    with pytest.raises(ValueError):
        landscape.build_stage(0)


def test_graphviz_missing_dependency_has_actionable_error(tmp_path, monkeypatch):
    landscape = load_helper("landscape")
    monkeypatch.setattr(landscape.shutil, "which", lambda executable: None)
    with pytest.raises(RuntimeError, match="Install Graphviz"):
        landscape.save_tutorial_svg([], [], tmp_path / "unused.svg", title="test")


def test_graph_source_escapes_labels():
    from gestaltdb.graphdb import Node

    source = load_helper("landscape").graph_source([
        Node("a", properties={"label": "A & B < C", "kind": "data", "layer": "data"}),
    ], [], title="Unicode · title")
    assert "A &amp; B &lt; C" in source and "Unicode · title" in source


def test_employment_figure_does_not_invent_a_research_role():
    landscape = load_helper("landscape")
    base_nodes, base_edges = landscape.build_subsumption()
    assert "Researcher" not in {node.get_id for node in base_nodes}
    inferred = {(edge.source, edge.target) for edge in base_edges
                if edge.properties["type"].startswith("inferred:")}
    assert inferred == {("alice", "Employee"), ("alice", "Person")}
    _, refined_edges = landscape.build_job_refinement()
    assert any(edge.source == "alice" and edge.target == "researcher"
               and edge.properties["type"] == "asserted: hasJobDescription"
               for edge in refined_edges)
    assert any(edge.source == "alice" and edge.target == "Researcher"
               and edge.properties["type"].startswith("inferred:")
               for edge in refined_edges)


def test_pdf_preflight_has_actionable_error(tmp_path, monkeypatch):
    script = TUTORIAL.parents[1] / "scripts" / "build_tutorial_pdf.py"
    spec = importlib.util.spec_from_file_location("build_tutorial_pdf", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.shutil, "which", lambda executable: None)
    with pytest.raises(RuntimeError, match="PDF export needs XeLaTeX"):
        module.build(tmp_path)


def test_reader_pdf_rejects_maintainer_material(tmp_path):
    script = TUTORIAL.parents[1] / "scripts" / "build_tutorial_pdf.py"
    spec = importlib.util.spec_from_file_location("build_tutorial_pdf", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = tmp_path / "reader.tex"
    document.write_text("A reader-facing introduction to reasoning engines.")
    module.validate_reader_document(document)
    for page in ("maintainer-notes", "OUTLINE_COVERAGE", "toolkit/index"):
        document.write_text(f"onto-tutorial/{page}::doc")
        with pytest.raises(RuntimeError, match="unexpectedly includes maintainer page"):
            module.validate_reader_document(document)


def test_every_chapter_has_a_question_and_explained_answer():
    for chapter in TUTORIAL.glob("ch*.rst"):
        text = chapter.read_text()
        assert "Check your understanding" in text, chapter.name
        assert "**Question.**" in text and "**Answer.**" in text, chapter.name


def test_build_docs_flag_help_and_validation():
    script = TUTORIAL.parents[1] / "scripts" / "build_docs.sh"
    help_result = subprocess.run([str(script), "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0 and "--pdf" in help_result.stdout
    invalid = subprocess.run([str(script), "--pdf", "--output"], capture_output=True, text=True)
    assert invalid.returncode == 2 and "requires a directory" in invalid.stderr


def test_small_svg_escapes_labels_and_defines_marker():
    document = load_helper("figures").subsumption_svg(["A&B", "C<D"], [("A&B", "C<D")])
    root = ElementTree.fromstring(document)
    assert root.find(".//{http://www.w3.org/2000/svg}marker").attrib["id"] == "a"
    assert "A&amp;B" in document and "C&lt;D" in document
