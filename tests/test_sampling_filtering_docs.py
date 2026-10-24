"""Execute the documented filtering-to-sampling workflows, not just their syntax."""

from pathlib import Path
import re
import textwrap

import pytest

from gestaltdb import agent_docs
from gestaltdb.graphdb import Edge, Node


ROOT = Path(__file__).resolve().parents[1]


def _markdown_section(path, heading):
    text = (ROOT / path).read_text(encoding="utf-8")
    section = text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    return agent_docs.extract_python_examples(section)


def _rst_examples(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    return [textwrap.dedent(block) for block in re.findall(
        r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", text, re.MULTILINE,
    )]


def _backend_setup_example(backend="LMDB"):
    """Return one selected tab from the shared backend-setup include."""
    text = (ROOT / "docs/_includes/backend-setup.rst").read_text(encoding="utf-8")
    section = text.split(f".. tab-item:: {backend}\n", 1)[1]
    section = section.split("   .. tab-item::", 1)[0]
    blocks = re.findall(
        r"^      \.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", section,
        re.MULTILINE,
    )
    assert blocks, f"No Python setup for {backend}"
    return textwrap.dedent(blocks[0])


def _execute(examples, filename, namespace=None):
    assert examples, f"No examples found in {filename}"
    namespace = {} if namespace is None else namespace
    for index, example in enumerate(examples, 1):
        exec(compile(example, f"{filename}:example-{index}", "exec"), namespace)
    return namespace


def test_readme_property_filtering_examples(graph_db):
    graph_db.put_nodes([
        Node("drug-1", labels=["Drug"]),
        Node("protein-1", labels=["Protein"], properties={"score": 0.9}),
    ])
    graph_db.put_edge(Edge("e1", "drug-1", "protein-1", properties={
        "type": "binds", "confidence": 0.95,
    }))
    namespace = _execute(
        _markdown_section("README.md", "Property-Graph Filtering and Indexes"),
        "README.md", {"graph": graph_db},
    )
    assert {node.get_id for node in namespace["cohort"]} == {"protein-1"}
    assert {edge.get_id for edge in namespace["strong_edges"]} == {"e1"}
    assert namespace["seed_ids"] == [b"protein-1"]
    assert namespace["result"].records == [{"drug": "drug-1", "protein": "protein-1"}]


def test_readme_filtered_snapshot_example():
    pytest.importorskip("lmdb")
    namespace = _execute(
        _markdown_section("README.md", "High-Performance Graph Sampling"), "README.md",
    )
    assert namespace["layers"].layers[0].edge_indices.size == 1
    assert namespace["walks"].lengths.tolist() == [1]


def test_property_filtering_guide_example():
    pytest.importorskip("lmdb")
    _execute(_rst_examples("docs/property-filtering.rst"), "docs/property-filtering.rst")


def test_array_native_sampling_quickstart_example():
    # The first code block is deliberately backend-free.
    examples = _rst_examples("docs/typed-sampling.rst")
    namespace = _execute(examples[:1], "docs/typed-sampling.rst")
    assert namespace["layers"].layers[0].edge_indices.size == 2
    assert namespace["walks"].lengths.tolist() == [1]


def test_quickstart_including_reusable_sampler(tmp_path, monkeypatch):
    pytest.importorskip("lmdb")
    monkeypatch.chdir(tmp_path)
    namespace = {}
    try:
        _execute([_backend_setup_example()], "docs/_includes/backend-setup.rst", namespace)
        _execute(_rst_examples("docs/quickstart.rst"), "docs/quickstart.rst", namespace)
    finally:
        if "graph_db" in namespace:
            namespace["graph_db"].close()


def test_examples_filtered_sampler_workflow():
    pytest.importorskip("plyvel")
    _execute(_markdown_section("EXAMPLES.md", "Build and Use a Sampler Snapshot"), "EXAMPLES.md")


@pytest.mark.parametrize("example_index", range(4))
def test_packaged_sampling_workflows(example_index):
    examples = agent_docs.extract_python_examples(agent_docs.read_topic("sampling"))
    example = examples[example_index]
    if "LevelDBStore" in example or 'backend="leveldb"' in example:
        pytest.importorskip("plyvel")
    _execute([example], "packaged sampling topic")
