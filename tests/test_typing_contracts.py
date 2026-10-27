"""Runtime contracts for the issue-410 typing slice.

These tests pin the behavior the new annotations promise: optional
missing-value results, read-only reader narrowness, key normalization in
repository updates, and the small structural contracts (labels, IDs,
directions, RNG) that static types now describe.
"""

import random

import pytest

from gestaltdb import QueryResult
from gestaltdb.current import EdgeRepository, NodeRepository
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.indexes import IndexService
from gestaltdb.ingestion_service import IngestionService
from gestaltdb.lookups import EntityLookup
from gestaltdb.traversal import TraversalService


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


def test_service_properties_expose_typed_capabilities(graph):
    assert isinstance(graph.nodes, NodeRepository)
    assert isinstance(graph.edges, EdgeRepository)
    assert isinstance(graph.indexes, IndexService)
    assert isinstance(graph.traversal, TraversalService)
    assert isinstance(graph.lookup, EntityLookup)
    assert isinstance(graph.ingest, IngestionService)


def test_query_returns_query_result(graph):
    result = graph.query("UNWIND [1] AS value RETURN value")
    assert isinstance(result, QueryResult)
    assert result.columns == ("value",)
    assert result.records == [{"value": 1}]
    assert list(result) == [{"value": 1}]


def test_label_expression_with_property_seek(graph):
    graph.put_node(Node("a", labels=["A"], properties={"v": 1}))
    graph.put_node(Node("b", labels=["B"], properties={"v": 1}))
    graph.create_node_property_index("v")
    result = graph.query("MATCH (n:A|B {v: 1}) RETURN n")
    assert {node.get_id for node in (record["n"] for record in result)} == {"a", "b"}


def test_multi_stage_with_query_plans_every_stage(graph):
    graph.put_node(Node("a", labels=["Person"], properties={"name": "Ann"}))
    graph.put_node(Node("b", labels=["Person"], properties={"name": "Bo"}))
    result = graph.query("MATCH (p:Person) WITH p AS person RETURN person.name AS name ORDER BY name")
    assert result.columns == ("name",)
    assert result.records == [{"name": "Ann"}, {"name": "Bo"}]


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
