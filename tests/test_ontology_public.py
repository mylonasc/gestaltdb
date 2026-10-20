"""Public imports, shipped examples and dependency-free standalone execution."""
import importlib
import importlib.util
from pathlib import Path
import textwrap

import pytest

from gestaltdb import agent_docs


def test_public_exports_are_explicit_and_resolvable():
    for name in ("gestaltdb.ontology", "gestaltdb.reasoning"):
        module = importlib.import_module(name)
        assert len(module.__all__) == len(set(module.__all__))
        for symbol in module.__all__:
            assert getattr(module, symbol) is not None


def test_shipped_ontology_example_executes_without_a_backend():
    for example in agent_docs.extract_python_examples(agent_docs.read_topic("ontology")):
        exec(compile(example, "packaged-ontology-example", "exec"), {})


@pytest.mark.parametrize("index", [0, 1])
def test_user_guide_examples_execute(index):
    if index == 1 and importlib.util.find_spec("lmdb") is None:
        pytest.skip("optional LMDB example")
    document = Path(__file__).parents[1] / "docs" / "ontology.rst"
    block = document.read_text().split(".. code-block:: python\n")[index + 1]
    lines = []
    for line in block.splitlines():
        if line and not line.startswith("   "):
            break
        lines.append(line)
    exec(compile(textwrap.dedent("\n".join(lines)), str(document), "exec"), {})
