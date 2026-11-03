"""Sampling task guides execute independently and retain a discoverable route."""

from pathlib import Path
import re
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
TASKS = ("sampling-neighborhoods", "sampling-subgraphs", "sampling-temporal",
         "sampling-negatives", "random-walks")


@pytest.mark.parametrize("page", ("sampling-neighborhoods", "sampling-subgraphs", "sampling-negatives"))
def test_array_task_is_standalone(page, tmp_path, monkeypatch):
    text = (ROOT / f"docs/{page}.rst").read_text(encoding="utf-8")
    blocks = re.findall(r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", text, re.MULTILINE)
    assert len(blocks) == 1
    monkeypatch.chdir(tmp_path)
    exec(compile(textwrap.dedent(blocks[0]), f"docs/{page}.rst", "exec"), {})
    assert not list(tmp_path.iterdir())


def test_learning_path_starts_with_id_and_filter_meaning():
    text = (ROOT / "docs/sampling-introduction.rst").read_text()
    introduction = text.split(".. code-block:: python", 1)[0]
    for concept in ("Live", "Compact global IDs", "Batch-local IDs", "filtering seeds is not"):
        assert concept in introduction
    for task in TASKS:
        assert f":doc:`{task}`" in text
    assert ":doc:`typed-sampling`" in text
    navigation = (ROOT / "docs/index.rst").read_text()
    for page in ("sampling-introduction", *TASKS):
        assert f"   {page}\n" in navigation
    assert "docs/sampling-introduction.rst" in (ROOT / "README.md").read_text()


def test_task_guides_keep_specialized_boundaries_visible():
    checks = {
        "sampling-neighborhoods": ("neighbor", "edge_index", "unused quota", "Torch"),
        "sampling-subgraphs": ("node_ids_global", "max_edges", "self-loops", "confidence"),
        "sampling-temporal": ("system_time", "before", "contained", "causal_policy"),
        "sampling-negatives": ("false", "exhaustion_policy", "positive_times_us", "ContextNegativeSampler"),
    }
    for page, terms in checks.items():
        text = (ROOT / f"docs/{page}.rst").read_text()
        for term in terms:
            assert term in text, (page, term)
