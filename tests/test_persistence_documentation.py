"""Execute the persistence guides as independent examples with real extras."""

from pathlib import Path
import re
import textwrap

import pytest

from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.temporal import TemporalInstant


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = [
    ("property-graph", 0, ("lmdb",)),
    ("storage-backends", 0, ("lmdb",)),
    ("storage-backends", 1, ("plyvel", "msgpack")),
    ("storage-backends", 2, ("lmdb",)),
    ("storage-backends", 3, ("lmdb",)),
    ("serializers", 0, ()),
    ("serializers", 1, ("msgpack",)),
    ("serializers", 2, ("google.protobuf",)),
    ("serializers", 3, ()),
    ("serializers", 4, ("lmdb", "msgpack")),
]


def examples(page):
    text = (ROOT / f"docs/{page}.rst").read_text(encoding="utf-8")
    return [textwrap.dedent(block) for block in re.findall(
        r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", text, re.MULTILINE,
    )]


def test_all_persistence_examples_are_registered():
    for page in {entry[0] for entry in EXAMPLES}:
        indices = {index for name, index, _ in EXAMPLES if name == page}
        assert indices == set(range(len(examples(page)))), page


@pytest.mark.parametrize("page,index,requirements", EXAMPLES,
                         ids=[f"{page}-{index}" for page, index, _ in EXAMPLES])
def test_persistence_example(page, index, requirements, tmp_path, monkeypatch):
    for dependency in requirements:
        pytest.importorskip(dependency)
    monkeypatch.chdir(tmp_path)
    namespace = {}
    exec(compile(examples(page)[index], f"docs/{page}.rst:example-{index}", "exec"), namespace)
    assert not list(tmp_path.iterdir()), "Examples must not leave persistent stores in the working directory"


@pytest.mark.parametrize("backend,dependency", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
@pytest.mark.parametrize("codec,dependency_codec", [
    ("json", None), ("pickle", None), ("messagepack", "msgpack"), ("protobuf", "google.protobuf"),
])
def test_independent_backend_codec_choices(tmp_path, backend, dependency, codec, dependency_codec):
    pytest.importorskip(dependency)
    if dependency_codec:
        pytest.importorskip(dependency_codec)
    path = tmp_path / "graph"
    payload = {"nested": [{"time": TemporalInstant(0), "count": 3}]}
    with GraphDB.create(path, backend=backend, serializer=codec) as graph:
        graph.put_node(Node("sample-1", properties=payload))
    with GraphDB.open(path) as reopened:
        assert reopened.get_node("sample-1").properties == payload
        assert reopened.manifest["backend"]["name"] == backend
        assert reopened.manifest["serializer"]["name"] == codec
