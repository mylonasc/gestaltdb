"""Execute tutorial content directly; Sphinx alone does not test code cells."""

import importlib.util
import json
import re
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
    if chapter.name.startswith("ch01"):
        pytest.importorskip("lmdb")
    monkeypatch.chdir(tmp_path)
    namespace = {"__name__": "__main__"}
    for block in blocks:
        exec(compile(textwrap.dedent(block), str(chapter), "exec"), namespace)


def test_committed_figures_are_reproducible(tmp_path):
    landscape = load_helper("landscape")
    for generated in landscape.render_all(str(tmp_path)):
        generated = Path(generated)
        assert generated.read_bytes() == (TUTORIAL / "figures" / generated.name).read_bytes()
        ElementTree.parse(generated)
    for stage in (1, 2, 3):
        nodes, edges = landscape.build_stage(stage)
        ids = {node.get_id for node in nodes}
        assert all(edge.source in ids and edge.target in ids for edge in edges)
    with pytest.raises(ValueError):
        landscape.build_stage(0)


def test_small_svg_escapes_labels_and_defines_marker():
    document = load_helper("figures").subsumption_svg(["A&B", "C<D"], [("A&B", "C<D")])
    root = ElementTree.fromstring(document)
    assert root.find(".//{http://www.w3.org/2000/svg}marker").attrib["id"] == "a"
    assert "A&amp;B" in document and "C&lt;D" in document
