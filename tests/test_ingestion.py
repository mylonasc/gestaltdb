import importlib.util

import pytest

from gestaltdb.ingestion import _column_to_list
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.ingestion import EdgeList, NodeList
from gestaltdb.kvstores import LMDBStore, PyRexStore
from gestaltdb.serializers import JSONSerializer, PickleSerializer


def test_node_list_requires_serialized_values():
    with pytest.raises(ValueError, match="node_values is required"):
        NodeList.from_arrow(["n1"], None)


def test_edge_list_requires_serialized_values():
    with pytest.raises(ValueError, match="edge_values is required"):
        EdgeList.from_arrow(["e1"], ["n1"], ["n2"], ["rel"], None)


def test_edge_list_validates_matching_lengths():
    with pytest.raises(ValueError, match="column lengths must match"):
        EdgeList.from_arrow(["e1", "e2"], ["n1"], ["n2"], ["rel"], [b"value"])


def test_column_to_list_rejects_null_values():
    with pytest.raises(ValueError, match="contains null values"):
        _column_to_list(["n1", None], "node_ids")


def test_node_list_normalizes_bytes_like_values_and_chunks():
    node_list = NodeList.from_arrow([bytearray(b"n1"), memoryview(b"n2")], [bytearray(b"v1"), memoryview(b"v2")])

    assert node_list.node_ids == [b"n1", b"n2"]
    assert node_list.node_values == [b"v1", b"v2"]
    assert [chunk.node_ids for chunk in node_list.chunks(1)] == [[b"n1"], [b"n2"]]


def test_node_list_chunks_requires_positive_chunk_size():
    node_list = NodeList.from_arrow(["n1"], [b"value"])

    with pytest.raises(ValueError, match="chunk_size must be positive"):
        list(node_list.chunks(0))


def test_node_list_rejects_invalid_identifier_and_payload_types():
    with pytest.raises(TypeError, match="node_ids values"):
        NodeList.from_arrow([object()], [b"value"])
    with pytest.raises(TypeError, match="node_values values"):
        NodeList.from_arrow(["n1"], ["not-bytes"])


def test_edge_list_normalizes_bytes_edge_types_and_chunks():
    edge_list = EdgeList.from_arrow(["e1"], ["n1"], ["n2"], [b"rel"], [memoryview(b"value")])

    assert edge_list.edge_ids == [b"e1"]
    assert edge_list.sources == [b"n1"]
    assert edge_list.targets == [b"n2"]
    assert edge_list.edge_types == ["rel"]
    assert edge_list.edge_values == [b"value"]
    assert list(edge_list.chunks(1)) == [edge_list]


def test_edge_list_rejects_invalid_edge_type_and_chunk_size():
    with pytest.raises(TypeError, match="edge_types values"):
        EdgeList.from_arrow(["e1"], ["n1"], ["n2"], [1], [b"value"])

    edge_list = EdgeList.from_arrow(["e1"], ["n1"], ["n2"], ["rel"], [b"value"])
    with pytest.raises(ValueError, match="chunk_size must be positive"):
        list(edge_list.chunks(0))


def test_polars_ingestion_containers_validate_dataframe_and_columns():
    pl = pytest.importorskip("polars")

    node_df = pl.DataFrame({"node_id": ["n1"], "node_value": [b"value"]})
    assert NodeList.from_polars(node_df).node_ids == [b"n1"]

    edge_df = pl.DataFrame({"edge_id": ["e1"], "source": ["n1"], "target": ["n2"], "edge_type": ["rel"], "edge_value": [b"value"]})
    assert EdgeList.from_polars(edge_df).edge_types == ["rel"]

    with pytest.raises(TypeError, match="polars.DataFrame"):
        NodeList.from_polars({"node_id": ["n1"]})
    with pytest.raises(ValueError, match="missing required columns"):
        EdgeList.from_polars(pl.DataFrame({"edge_id": ["e1"]}))


def test_graphdb_ingests_serialized_nodes_and_edges_from_columns(tmp_path):
    pytest.importorskip("lmdb")
    graph_db = GraphDB(LMDBStore(path=str(tmp_path / "lmdb")), PickleSerializer())
    try:
        graph_db.create_node_property_index("kind")
        graph_db.create_edge_property_index("score")
        nodes = [
            Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}),
            Node(node_id="protein-1", labels=["Protein"], properties={"kind": "protein"}),
        ]
        node_values = [graph_db.serialize_node_value(node) for node in nodes]

        assert graph_db.ingest_nodes_arrow([node.get_id for node in nodes], node_values, chunk_size=1) == 2

        edge = Edge(
            edge_id="d1-p1",
            source="drug-1",
            target="protein-1",
            properties={"type": "drug-to-protein", "score": 0.9},
        )
        assert graph_db.ingest_edges_arrow(
            [edge.get_id],
            [edge.source],
            [edge.target],
            [edge.get_type],
            [graph_db.serialize_edge_value(edge)],
            chunk_size=1,
        ) == 1

        assert graph_db.get_node(b"drug-1").properties == {"kind": "drug"}
        assert [node.get_id for node in graph_db.nodes_by_label("Drug")] == ["drug-1"]
        assert [node.get_id for node in graph_db.nodes_by_property("kind", "drug")] == ["drug-1"]
        assert graph_db.get_edge(b"d1-p1").properties["score"] == 0.9
        assert [edge.get_id for edge in graph_db.edges_by_property("score", 0.9)] == ["d1-p1"]
        assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-1"]
        assert graph_db.neighbors_by_edge_type("protein-1", "drug-to-protein", direction="in") == [b"drug-1"]
        assert graph_db.get_adjacency_list(b"drug-1", direction="any") == []
    finally:
        graph_db.close()


def test_graphdb_ingests_polars_node_and_edge_entities_with_json_serializer(tmp_path):
    pytest.importorskip("lmdb")
    pl = pytest.importorskip("polars")
    graph_db = GraphDB(LMDBStore(path=str(tmp_path / "lmdb")), JSONSerializer())
    try:
        graph_db.create_node_property_index("kind")
        graph_db.create_edge_property_index("score")
        node_df = pl.DataFrame(
            {
                "node_id": ["drug-1", "protein-1"],
                "labels": [["Drug"], ["Protein"]],
                "kind": ["drug", "protein"],
            }
        )

        assert graph_db.ingest_nodes_polars_entities(node_df, property_columns=["kind"], chunk_size=1) == 2

        edge_df = pl.DataFrame(
            {
                "edge_id": ["d1-p1"],
                "source": ["drug-1"],
                "target": ["protein-1"],
                "edge_type": ["drug-to-protein"],
                "score": [0.9],
            }
        )

        assert graph_db.ingest_edges_polars_entities(edge_df, property_columns=["score"], chunk_size=1) == 1

        assert graph_db.get_node(b"drug-1").properties == {"kind": "drug"}
        assert [node.get_id for node in graph_db.nodes_by_label("Drug")] == ["drug-1"]
        assert [node.get_id for node in graph_db.nodes_by_property("kind", "drug")] == ["drug-1"]
        assert graph_db.get_edge(b"d1-p1").properties == {"type": "drug-to-protein", "score": 0.9}
        assert [edge.get_id for edge in graph_db.edges_by_property("score", 0.9)] == ["d1-p1"]
        assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein") == [b"protein-1"]
    finally:
        graph_db.close()


def test_graphdb_ingests_arrow_entities_with_json_serializer(tmp_path):
    pytest.importorskip("lmdb")
    pa = pytest.importorskip("pyarrow")
    graph_db = GraphDB(LMDBStore(path=str(tmp_path / "lmdb")), JSONSerializer())
    try:
        assert graph_db.ingest_nodes_arrow_entities(
            pa.array(["n1"]),
            labels=pa.array([["Entity"]]),
            properties={"kind": pa.array(["entity"])},
        ) == 1
        assert graph_db.ingest_edges_arrow_entities(
            pa.array(["e1"]),
            pa.array(["n1"]),
            pa.array(["n2"]),
            pa.array(["rel"]),
            properties={"score": pa.array([1])},
        ) == 1

        assert graph_db.get_node(b"n1").labels == ("Entity",)
        assert graph_db.get_edge(b"e1").properties == {"type": "rel", "score": 1}
    finally:
        graph_db.close()


def test_graphdb_entity_ingestion_falls_back_for_non_json_serializer(tmp_path):
    pytest.importorskip("lmdb")
    pl = pytest.importorskip("polars")
    graph_db = GraphDB(LMDBStore(path=str(tmp_path / "lmdb")), PickleSerializer())
    try:
        node_df = pl.DataFrame({"node_id": ["n1"], "labels": [["Entity"]], "kind": ["entity"]})
        edge_df = pl.DataFrame({"edge_id": ["e1"], "source": ["n1"], "target": ["n2"], "edge_type": ["rel"], "score": [1]})

        assert graph_db.ingest_nodes_polars_entities(node_df, property_columns=["kind"]) == 1
        assert graph_db.ingest_edges_polars_entities(edge_df, property_columns=["score"]) == 1

        assert graph_db.get_node(b"n1").properties == {"kind": "entity"}
        assert graph_db.get_edge(b"e1").properties == {"score": 1, "type": "rel"}
    finally:
        graph_db.close()


def test_columnar_node_ingestion_removes_stale_indexes(tmp_path):
    pytest.importorskip("lmdb")
    graph_db = GraphDB(LMDBStore(path=str(tmp_path / "lmdb")), PickleSerializer())
    try:
        graph_db.create_node_property_index("kind")
        old_node = Node(node_id="n1", labels=["Old"], properties={"kind": "old"})
        new_node = Node(node_id="n1", labels=["New"], properties={"kind": "new"})

        graph_db.put_node(old_node)
        graph_db.ingest_nodes_arrow(["n1"], [graph_db.serialize_node_value(new_node)])

        assert graph_db.nodes_by_label("Old") == []
        assert [node.get_id for node in graph_db.nodes_by_label("New")] == ["n1"]
        assert graph_db.nodes_by_property("kind", "old") == []
        assert [node.get_id for node in graph_db.nodes_by_property("kind", "new")] == ["n1"]
    finally:
        graph_db.close()


def test_columnar_edge_ingestion_requires_append_only(tmp_path):
    pytest.importorskip("lmdb")
    graph_db = GraphDB(LMDBStore(path=str(tmp_path / "lmdb")), PickleSerializer())
    try:
        with pytest.raises(NotImplementedError, match="append_only=True"):
            graph_db.ingest_edges_arrow(["e1"], ["n1"], ["n2"], ["rel"], [b"value"], append_only=False)
    finally:
        graph_db.close()


def test_pyrex_store_native_columnar_path_writes_expected_batches():
    class FakeDB:
        def __init__(self):
            self.batches = []

        def write_columnar_batch(self, keys, values, write_options=None):
            self.batches.append((list(keys), list(values), write_options))

    store = object.__new__(PyRexStore)
    store.db = FakeDB()
    store.write_options = object()
    edge_list = EdgeList.from_arrow(
        ["e1", "e2"],
        ["n1", "n1"],
        ["n2", "n3"],
        ["rel", "rel"],
        [b"edge-value-1", b"edge-value-2"],
    )

    store.ingest_edges_columnar(edge_list, native=True)

    assert len(store.db.batches) == 4
    assert store.db.batches[0][0] == [b"E\x1fe1", b"E\x1fe2"]
    assert store.db.batches[0][1] == [b"edge-value-1", b"edge-value-2"]
    assert store.db.batches[1][0] == [b"T\x1fout\x1fn1\x1frel\x1fe1", b"T\x1fout\x1fn1\x1frel\x1fe2"]
    assert store.db.batches[1][1] == [b"n2", b"n3"]
    assert store.db.batches[2][0] == [b"T\x1fin\x1fn2\x1frel\x1fe1", b"T\x1fin\x1fn3\x1frel\x1fe2"]
    assert store.db.batches[2][1] == [b"n1", b"n1"]
    assert store.db.batches[3][1] == [b"e1", b"e2"]
    assert all(key.startswith(b"I\x1f") for key in store.db.batches[3][0])


def test_pyrex_store_native_columnar_node_path_writes_expected_batch():
    class FakeDB:
        def __init__(self):
            self.batches = []

        def write_columnar_batch(self, keys, values, write_options=None):
            self.batches.append((list(keys), list(values), write_options))

    store = object.__new__(PyRexStore)
    store.db = FakeDB()
    store.write_options = object()
    node_list = NodeList.from_arrow(["n1", "n2"], [b"node-value-1", b"node-value-2"])

    store.ingest_nodes_columnar(node_list, native=True)

    assert len(store.db.batches) == 1
    assert store.db.batches[0][0] == [b"N\x1fn1", b"N\x1fn2"]
    assert store.db.batches[0][1] == [b"node-value-1", b"node-value-2"]


def test_pyrex_store_native_columnar_node_path_passes_arrow_values_directly():
    pa = pytest.importorskip("pyarrow")

    class FakeDB:
        def __init__(self):
            self.batches = []

        def write_columnar_batch(self, keys, values, write_options=None):
            self.batches.append((keys, values, write_options))

    store = object.__new__(PyRexStore)
    store.db = FakeDB()
    store.write_options = object()
    node_values = pa.array([b"node-value-1", b"node-value-2"], type=pa.binary())
    node_list = NodeList.from_arrow(pa.array(["n1", "n2"]), node_values)

    store.ingest_nodes_columnar(node_list, native=True)

    assert store.db.batches[0][0] == [b"N\x1fn1", b"N\x1fn2"]
    assert store.db.batches[0][1] is node_values


def test_pyrex_store_native_columnar_edge_path_passes_arrow_values_directly():
    pa = pytest.importorskip("pyarrow")

    class FakeDB:
        def __init__(self):
            self.batches = []

        def write_columnar_batch(self, keys, values, write_options=None):
            self.batches.append((keys, values, write_options))

    store = object.__new__(PyRexStore)
    store.db = FakeDB()
    store.write_options = object()
    edge_ids = pa.array(["e1", "e2"])
    edge_values = pa.array([b"edge-value-1", b"edge-value-2"], type=pa.binary())
    sources = pa.array(["n1", "n1"])
    targets = pa.array(["n2", "n3"])
    edge_list = EdgeList.from_arrow(
        edge_ids,
        sources,
        targets,
        pa.array(["rel", "rel"]),
        edge_values,
    )

    store.ingest_edges_columnar(edge_list, native=True)

    assert store.db.batches[0][1] is edge_values
    assert store.db.batches[1][1] is targets
    assert store.db.batches[2][1] is sources
    assert store.db.batches[3][1] is edge_ids


def test_columnar_chunks_preserve_arrow_value_slices():
    pa = pytest.importorskip("pyarrow")
    node_values = pa.array([b"v1", b"v2", b"v3"], type=pa.binary())
    node_list = NodeList.from_arrow(pa.array(["n1", "n2", "n3"]), node_values)

    first, second = list(node_list.chunks(2))

    assert first.node_values_column.to_pylist() == [b"v1", b"v2"]
    assert second.node_values_column.to_pylist() == [b"v3"]

    edge_values = pa.array([b"ev1", b"ev2", b"ev3"], type=pa.binary())
    targets = pa.array(["n2", "n3", "n4"])
    edge_list = EdgeList.from_arrow(
        pa.array(["e1", "e2", "e3"]),
        pa.array(["n1", "n1", "n1"]),
        targets,
        pa.array(["rel", "rel", "rel"]),
        edge_values,
    )

    first, second = list(edge_list.chunks(2))

    assert first.edge_values_column.to_pylist() == [b"ev1", b"ev2"]
    assert second.edge_values_column.to_pylist() == [b"ev3"]
    assert first.targets_column.to_pylist() == ["n2", "n3"]
    assert second.targets_column.to_pylist() == ["n4"]


@pytest.mark.skipif(importlib.util.find_spec("pyrex") is None, reason="pyrex not installed")
def test_graphdb_ingests_nodes_and_edges_with_real_pyrex_native_columnar_path(tmp_path):
    graph_db = GraphDB(PyRexStore(path=str(tmp_path / "pyrex")), PickleSerializer())
    try:
        if not graph_db.store.has_native_columnar_ingestion():
            pytest.skip("pyrex native columnar ingestion is not available")

        node = Node(node_id="n1", properties={"label": "drug"})
        edge = Edge(edge_id="e1", source="n1", target="n2", properties={"type": "rel", "weight": 1})

        graph_db.ingest_nodes_arrow(["n1"], [graph_db.serialize_node_value(node)])
        graph_db.ingest_edges_arrow(["e1"], ["n1"], ["n2"], ["rel"], [graph_db.serialize_edge_value(edge)])

        assert graph_db.get_node(b"n1").properties == {"label": "drug"}
        assert graph_db.get_edge(b"e1").properties["weight"] == 1
        assert graph_db.neighbors_by_edge_type("n1", "rel") == [b"n2"]
    finally:
        graph_db.close()


@pytest.mark.skipif(importlib.util.find_spec("pyrex") is None, reason="pyrex not installed")
def test_graphdb_ingests_json_entities_with_real_pyrex_native_columnar_path(tmp_path):
    pl = pytest.importorskip("polars")
    graph_db = GraphDB(PyRexStore(path=str(tmp_path / "pyrex_json")), JSONSerializer())
    try:
        if not graph_db.store.has_native_columnar_ingestion():
            pytest.skip("pyrex native columnar ingestion is not available")

        node_df = pl.DataFrame(
            {
                "node_id": ["n1", "n2"],
                "labels": [["Entity"], ["Entity"]],
                "kind": ["drug", "protein"],
            }
        )
        edge_df = pl.DataFrame(
            {
                "edge_id": ["e1"],
                "source": ["n1"],
                "target": ["n2"],
                "edge_type": ["binds"],
                "score": [3.5],
            }
        )

        graph_db.ingest_nodes_polars_entities(node_df, property_columns=["kind"], chunk_size=1)
        graph_db.ingest_edges_polars_entities(edge_df, property_columns=["score"], chunk_size=1)

        assert graph_db.get_node(b"n1").properties == {"kind": "drug"}
        assert graph_db.get_edge(b"e1").properties == {"type": "binds", "score": 3.5}
        assert graph_db.neighbors_by_edge_type("n1", "binds") == [b"n2"]
    finally:
        graph_db.close()
