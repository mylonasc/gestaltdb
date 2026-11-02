"""Execute DOC-12 tutorial blocks independently, with their declared extras."""

from pathlib import Path
import re
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("data-access", 0, ("lmdb",)),
    ("ingestion", 0, ("lmdb", "pyarrow")),
    ("ingestion", 1, ("lmdb", "polars", "pyarrow")),
    ("ingestion", 2, ("lmdb", "pyarrow")),
    ("cypher-tutorial", 0, ("lmdb",)),
    ("cypher-tutorial", 1, ("lmdb",)),
    ("cypher-tutorial", 2, ("lmdb",)),
]


def examples(page):
    source = (ROOT / f"docs/{page}.rst").read_text(encoding="utf-8")
    return [textwrap.dedent(block) for block in re.findall(
        r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", source, re.MULTILINE,
    )]


@pytest.mark.parametrize("page,index,requirements", CASES,
                         ids=[f"{page}-{index}" for page, index, _ in CASES])
def test_standalone_data_access_example(page, index, requirements, tmp_path, monkeypatch):
    for dependency in requirements:
        pytest.importorskip(dependency)
    monkeypatch.chdir(tmp_path)
    exec(compile(examples(page)[index], f"docs/{page}.rst:{index}", "exec"), {})
    assert not list(tmp_path.iterdir())


def test_all_tutorial_blocks_are_registered():
    for page in {case[0] for case in CASES}:
        assert {index for name, index, _ in CASES if name == page} == set(range(len(examples(page))))


def test_existing_architecture_asset_is_retained_and_linked():
    architecture = (ROOT / "docs/architecture.rst").read_text()
    assert ".. figure:: _static/components.svg" in architecture
    assert ":alt: Component map" in architecture
    assert (ROOT / "docs/_static/components.dot").is_file()
    assert ":doc:`architecture`" in (ROOT / "docs/data-access.rst").read_text()


def test_table_styles_are_loaded_and_override_nowrap():
    conf = (ROOT / "docs/conf.py").read_text()
    assert 'html_css_files = ["documentation.css"]' in conf
    assert 'html_js_files = ["documentation.js"]' in conf
    css = (ROOT / "docs/_static/documentation.css").read_text()
    assert "table-layout: fixed" in css
    assert "white-space: normal" in css
    assert "overflow-wrap: anywhere" in css
