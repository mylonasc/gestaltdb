"""Runtime contracts for the issue-410 typing slice.

These tests pin the behavior the new annotations promise: optional
missing-value results, read-only reader narrowness, key normalization in
repository updates, and the small structural contracts (labels, IDs,
directions, RNG) that static types now describe.
"""

import random

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node


@pytest.fixture(params=["lmdb", "leveldb", "pyrex"])
def graph(tmp_path, request):
    from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
    from gestaltdb.serializers import JSONSerializer

    stores = {"lmdb": LMDBStore, "leveldb": LevelDBStore, "pyrex": PyRexStore}
    try:
        store = stores[request.param](path=str(tmp_path / "graph"))
    except Exception as exc:  # pragma: no cover - missing system dependency
        pytest.skip(f"{request.param} backend unavailable: {exc}")
    handle = GraphDB(store, JSONSerializer())
    try:
        yield handle
    finally:
        handle.close()


def test_missing_lookups_return_none(graph):
    assert graph.get_node(b"missing") is None
    assert graph.get_edge(b"missing") is None
    assert graph.get_nodes([b"missing"]) == [None]


def test_entity_identity_and_labels():
    node = Node(node_id="n1", labels=["Drug", "Drug"], properties={"kind": "drug"})
    assert node.get_id == "n1"
    assert node.labels == ("Drug",)
    assert node.get_id_bytes == b"n1"
    assert Node.from_dict(node.to_dict()).labels == ("Drug",)

    edge = Edge(edge_id="e1", source="n1", target="n2", properties={"type": "R"})
    assert edge.get_id == "e1"
    assert edge.get_type == "R"
    assert Edge(properties={"type": 42}).get_type is None


def test_readers_expose_no_writes(graph):
    with pytest.raises(AttributeError):
        graph.nodes.reader.put(Node("n1"))
    with pytest.raises(AttributeError):
        graph.edges.reader.put(Edge("e1", "n1", "n2", properties={"type": "R"}))
    with pytest.raises(AttributeError):
        graph.traversal.reader.rebuild_typed_adjacency()
    with pytest.raises(AttributeError):
        graph.indexes.reader.create_node_property_index("name")


def test_repository_update_normalizes_keys(graph):
    graph.put_node(Node("n1", properties={"count": 1}))
    updated = graph.nodes.update(b"n1", {"count": 2}, lambda old, new: {**old, **new})
    assert updated.get_id == "n1"
    assert graph.get_node(b"n1").properties == {"count": 2}


def test_live_sampling_accepts_random_instances(graph):
    graph.put_node(Node("n1"))
    graph.put_node(Node("n2"))
    graph.put_edge(Edge("e1", "n1", "n2", properties={"type": "R"}))
    records = graph.traversal.sample_neighbors("n1", "R", sample_size=1, rng=random.Random(7))
    assert [record["edge_id"] for record in records] == [b"e1"]
    assert graph.traversal.sample_nodes(10, rng=random.Random(7)) != []
    with pytest.raises(ValueError):
        graph.traversal.sample_neighbors("n1", "R", direction="sideways")
