"""Runtime contracts for the issue-410 typing slice.

These tests pin the behavior the new annotations promise: optional
missing-value results, read-only reader narrowness, key normalization in
repository updates, and the small structural contracts (labels, IDs,
directions, RNG) that static types now describe.
"""

import random
from types import SimpleNamespace

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

    # Skip only when the optional package is genuinely absent. A constructor
    # failure while the package is importable is a regression and must fail.
    pytest.importorskip({"lmdb": "lmdb", "leveldb": "plyvel", "pyrex": "pyrex"}[request.param])
    stores = {"lmdb": LMDBStore, "leveldb": LevelDBStore, "pyrex": PyRexStore}
    store = stores[request.param](path=str(tmp_path / "graph"))
    handle = GraphDB(store, JSONSerializer())
    try:
        yield handle
    finally:
        handle.close()


def test_backend_fixture_fails_on_constructor_regression(tmp_path, monkeypatch):
    pytest.importorskip("lmdb")
    from gestaltdb.kvstores import LMDBStore

    def broken_init(self, *args, **kwargs):
        raise RuntimeError("simulated backend constructor regression")

    monkeypatch.setattr(LMDBStore, "__init__", broken_init)
    with pytest.raises(RuntimeError, match="simulated backend constructor regression"):
        next(graph.__wrapped__(tmp_path, SimpleNamespace(param="lmdb")))


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


def test_partial_temporal_batch_export_preserves_optional_companions():
    import numpy as np
    from gestaltdb.sampling.batch import SampledSubgraphBatch

    ids = np.asarray([0], dtype=np.int64)
    triples = np.empty((0, 3), dtype=np.int64)
    batch = SampledSubgraphBatch(
        ids, ids, ids, ids, ids, ids, triples, triples,
        edge_version_ids=np.asarray(["v"], dtype=str),
        positive_time_us=np.asarray([1], dtype=np.int64),
    )
    arrays = batch.to_numpy()
    assert arrays["senders"] is ids
    assert arrays["valid_from_us"] is None
    assert arrays["valid_to_us"] is None
    assert arrays["valid_to_open"] is None
    assert arrays["negative_time_us"] is None
    assert "graph_node_offsets" not in arrays


def test_typed_sampling_arrays_preserve_memmaps(tmp_path):
    import numpy as np
    from gestaltdb.sampling.snapshot import SamplerSnapshot

    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot", external_node_ids=["a", "b"],
        external_edge_ids=["e"], external_relation_ids=["R"],
        src_int=[0], dst_int=[1], rel_int=[0],
    )
    loaded = SamplerSnapshot.load(snapshot.path, mmap=True)
    assert isinstance(loaded.src_int, np.memmap)
    assert loaded.src_int.dtype == np.int64
    assert loaded.edge_weights.dtype == np.float64
    assert loaded.external_node_ids.dtype.kind == "U"
    assert loaded.node_ints(["b", "a"]).tolist() == [1, 0]


def test_temporal_kind_contracts_and_detached_cache_payloads(graph):
    from gestaltdb.versioning import ClaimVersion, EdgeVersion, NodeVersion

    node = graph.temporal.put_node_version(Node("n", properties={"value": 1}), valid=(0, 100))
    edge = graph.temporal.put_edge_version(Edge("e", "n", "n"), valid=(0, 100))
    claim = graph.epistemic.assert_claim(
        subject="n", predicate="p", object="n", agent="test", polarity="positive", valid=(0, 100),
    )
    for kind, version, expected_type in [("node", node, NodeVersion), ("edge", edge, EdgeVersion), ("claim", claim, ClaimVersion)]:
        selected = graph.temporal.entity_as_of(kind, version.logical_id, valid_time=50)
        historical = graph.temporal.history().entity_as_of(kind, version.logical_id, valid_time=50)
        assert isinstance(selected, expected_type)
        assert isinstance(historical, expected_type)
        assert historical.version_id == selected.version_id
        assert historical.valid == selected.valid
    selected_node = graph.temporal.get_node_version(node.version_id)
    selected_node.node.properties["value"] = 99
    assert graph.temporal.get_node_version(node.version_id).node.properties["value"] == 1
    reader = graph.temporal.reader()
    assert reader.get_commit(node.commit_id) is not None
    for (store_id, _, _), entry in reader.commit_cache.items():
        assert store_id == id(entry[0])


def test_typed_claim_correction_preserves_unset_versus_none(graph):
    first = graph.epistemic.assert_claim(
        subject="n", predicate="p", object="n", agent="test", polarity="positive",
        confidence=0.75, valid=(0, 100),
    )
    inherited = graph.epistemic.correct_claim(first.logical_id, supersedes_version_id=first.version_id)
    cleared = graph.epistemic.correct_claim(
        first.logical_id, supersedes_version_id=inherited.version_id, confidence=None,
    )
    assert inherited.claim.confidence == 0.75
    assert cleared.claim.confidence is None
