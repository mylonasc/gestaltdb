import datetime

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node, TimeIndexedEdge, bytes_to_datetime, datetime_to_bytes
from gestaltdb.serializers import PickleSerializer

from .conftest import BACKEND_PARAMS


def test_datetime_byte_helpers_round_trip():
    value = datetime.datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=datetime.timezone.utc)

    assert bytes_to_datetime(datetime_to_bytes(value)) == value


def test_property_index_encoding_handles_bytes_lists_and_dicts(graph_db):
    graph_db.indexed_node_properties.add("meta")
    graph_db.put_node(Node(node_id="n1", properties={"meta": {"raw": b"abc", "values": (1, 2)}}))

    assert [node.get_id for node in graph_db.nodes_by_property("meta", {"values": [1, 2], "raw": b"abc"})] == ["n1"]


def test_node_serialization_preserves_labels_and_older_payloads_default_empty_labels():
    node = Node(node_id="drug-1", labels=["Drug", "Drug"], properties={"kind": "drug"})

    assert node.labels == ("Drug",)
    assert Node.from_dict(node.to_dict()).labels == ("Drug",)
    assert Node.from_dict({"id": "old", "properties": {}}).labels == ()


def test_graphdb_key_normalizers_handle_bytes_and_strings(graph_db):
    assert graph_db.node_key_to_bytes(b"n1") == b"n1"
    assert graph_db.node_key_to_bytes("n1") == b"n1"
    assert graph_db.edge_key_to_bytes(b"e1") == b"e1"
    assert graph_db.edge_key_to_bytes("e1") == b"e1"
    assert graph_db.key_to_string(b"n1") == "n1"
    assert graph_db.key_to_string("n1") == "n1"


def test_time_indexed_edge_serializes_timestamp_and_edge_id():
    timestamp = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    edge = TimeIndexedEdge(timestamp, edge_id="e1", source="n1", target="n2", properties={"type": "rel"})

    payload = edge.to_dict()

    assert edge.get_id_bytes.endswith(b":e1")
    assert TimeIndexedEdge.from_dict(payload).timestamp_dat == timestamp


def test_single_node(graph_db):
    node_a = Node(properties={"name": "Alice", "age": 30})
    graph_db.put_node(node_a)

    fetched = graph_db.get_node(node_a.get_id_bytes)
    assert fetched is not None
    assert fetched.properties["name"] == "Alice"
    assert fetched.properties["age"] == 30

    graph_db.delete_node(node_a.get_id_bytes)
    assert graph_db.get_node(node_a.get_id_bytes) is None


def test_single_edge(graph_db):
    node_a = Node(properties={"name": "Alice"})
    node_b = Node(properties={"name": "Bob"})
    graph_db.put_node(node_a)
    graph_db.put_node(node_b)

    edge_ab = Edge(source=node_a.get_id, target=node_b.get_id, properties={"relation": "friend"})
    graph_db.put_edge(edge_ab)

    fetched_edge = graph_db.get_edge(edge_ab.get_id_bytes)
    assert fetched_edge is not None
    assert fetched_edge.properties["relation"] == "friend"
    assert fetched_edge.source == node_a.get_id
    assert fetched_edge.target == node_b.get_id

    graph_db.delete_edge(edge_ab.get_id_bytes)
    assert graph_db.get_edge(edge_ab.get_id_bytes) is None


def test_deleting_edge_removes_legacy_adjacency(graph_db):
    graph_db.put_node(Node(node_id="n1"))
    graph_db.put_node(Node(node_id="n2"))
    graph_db.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "rel"}))

    graph_db.delete_edge(b"e1")

    assert graph_db.get_adjacency_list(b"n1", direction="any") == []
    assert graph_db.get_adjacency_list(b"n2", direction="any") == []


def test_replacing_edge_removes_legacy_adjacency_from_old_endpoints(graph_db):
    for node_id in ["n1", "n2", "n3", "n4"]:
        graph_db.put_node(Node(node_id=node_id))

    graph_db.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "old"}))
    graph_db.put_edge(Edge(edge_id="e1", source="n3", target="n4", properties={"type": "new"}))

    assert graph_db.get_adjacency_list(b"n1", direction="any") == []
    assert graph_db.get_adjacency_list(b"n2", direction="any") == []
    assert graph_db.get_adjacency_list(b"n3", direction="any") == ["e1"]
    assert graph_db.get_adjacency_list(b"n4", direction="any") == ["e1"]


def test_deleting_node_removes_incident_edges_and_edge_indexes(graph_db):
    graph_db.put_node(Node(node_id="n1"))
    graph_db.put_node(Node(node_id="n2"))
    graph_db.put_node(Node(node_id="n3"))
    graph_db.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "rel", "score": 1}))
    graph_db.put_edge(Edge(edge_id="e2", source="n2", target="n3", properties={"type": "rel", "score": 2}))
    graph_db.create_edge_property_index("score")

    graph_db.delete_node(b"n2")

    assert graph_db.get_node(b"n2") is None
    assert graph_db.get_edge(b"e1") is None
    assert graph_db.get_edge(b"e2") is None
    assert graph_db.neighbors_by_edge_type("n1", "rel", direction="out") == []
    assert graph_db.neighbors_by_edge_type("n3", "rel", direction="in") == []
    assert graph_db.edges_by_type("rel") == []
    assert graph_db.edges_by_property("score", 1) == []


def test_bfs_simple(graph_db):
    node_a = Node(properties={"label": "A"})
    node_b = Node(properties={"label": "B"})
    node_c = Node(properties={"label": "C"})
    graph_db.put_node(node_a)
    graph_db.put_node(node_b)
    graph_db.put_node(node_c)

    edge_ab = Edge(source=node_a.get_id, target=node_b.get_id)
    edge_bc = Edge(source=node_b.get_id, target=node_c.get_id)
    edge_ac = Edge(source=node_a.get_id, target=node_c.get_id)
    graph_db.put_edge(edge_ab)
    graph_db.put_edge(edge_bc)
    graph_db.put_edge(edge_ac)

    bfs_result = graph_db.bfs(node_a.get_id_bytes)
    assert set(bfs_result) == {node_a.get_id_bytes, node_b.get_id_bytes, node_c.get_id_bytes}
    assert len(bfs_result) == 3


def test_bulk_nodes(graph_db):
    nodes = [Node(properties={"name": f"User{i}"}) for i in range(5)]

    for node in nodes:
        graph_db.put_node(node)

    retrieved = graph_db.get_nodes([node.get_id_bytes for node in nodes])

    for index, node in enumerate(retrieved):
        assert node is not None
        assert node.properties["name"] == f"User{index}"

    for node in nodes:
        graph_db.delete_node(node.get_id_bytes)
    for node in nodes:
        assert graph_db.get_node(node.get_id_bytes) is None


def test_node_labels_are_indexed_and_updated(graph_db):
    node = Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"})
    graph_db.put_node(node)

    assert graph_db.get_node(b"drug-1").labels == ("Drug",)
    assert [node.get_id for node in graph_db.nodes_by_label("Drug")] == ["drug-1"]

    graph_db.put_node(Node(node_id="drug-1", labels=["Compound"], properties={"kind": "drug"}))

    assert graph_db.nodes_by_label("Drug") == []
    assert [node.get_id for node in graph_db.nodes_by_label("Compound")] == ["drug-1"]


def test_exact_node_property_index_supports_lookup(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug", "name": "Aspirin"}),
        Node(node_id="protein-1", labels=["Protein"], properties={"kind": "protein"}),
    ])

    assert graph_db.create_node_property_index("kind") == 2
    assert [node.get_id for node in graph_db.nodes_by_property("kind", "drug")] == ["drug-1"]


@pytest.mark.parametrize("backend", BACKEND_PARAMS, ids=lambda param: param[0] if isinstance(param, tuple) else str(param))
def test_node_property_index_definitions_persist_after_reopen(backend, tmp_path):
    backend_name, store_cls = backend
    path = tmp_path / backend_name
    graph = GraphDB(store_cls(path=str(path)), PickleSerializer())
    try:
        graph.create_node_property_index("kind")
    finally:
        graph.close()

    reopened = GraphDB(store_cls(path=str(path)), PickleSerializer())
    try:
        assert "kind" in reopened.indexed_node_properties
        reopened.put_node(Node(node_id="drug-1", properties={"kind": "drug"}))
        assert [node.get_id for node in reopened.nodes_by_property("kind", "drug")] == ["drug-1"]
    finally:
        reopened.close()


@pytest.mark.parametrize("backend", BACKEND_PARAMS, ids=lambda param: param[0] if isinstance(param, tuple) else str(param))
def test_edge_property_index_definitions_persist_after_reopen(backend, tmp_path):
    backend_name, store_cls = backend
    path = tmp_path / backend_name
    graph = GraphDB(store_cls(path=str(path)), PickleSerializer())
    try:
        graph.create_edge_property_index("score")
    finally:
        graph.close()

    reopened = GraphDB(store_cls(path=str(path)), PickleSerializer())
    try:
        assert "score" in reopened.indexed_edge_properties
        reopened.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "rel", "score": 1}))
        assert [edge.get_id for edge in reopened.edges_by_property("score", 1)] == ["e1"]
    finally:
        reopened.close()


def test_update_node_creates_or_merges_properties(graph_db):
    def merge(old, new):
        return {**old, **new}

    graph_db.put_node(Node(node_id="n1", properties={"name": "Alice"}))
    updated = graph_db.update_node(b"n1", {"age": 30}, merge)

    assert updated.properties == {"name": "Alice", "age": 30}


def test_update_edge_creates_or_merges_properties(graph_db):
    def merge(old, new):
        return {**old, **new}

    graph_db.put_node(Node(node_id="n1"))
    graph_db.put_node(Node(node_id="n2"))
    graph_db.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"weight": 1}))
    updated = graph_db.update_edge(b"e1", {"score": 2}, merge)

    assert updated.properties == {"weight": 1, "score": 2}


def test_label_and_property_indexes_can_be_rebuilt(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}),
        Node(node_id="drug-2", labels=["Drug"], properties={"kind": "drug"}),
    ])

    assert graph_db.rebuild_label_index() == 2
    assert graph_db.rebuild_node_property_index("kind") == 2
    assert {node.get_id for node in graph_db.nodes_by_label("Drug")} == {"drug-1", "drug-2"}


def test_index_cardinality_helpers(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}),
        Node(node_id="drug-2", labels=["Drug"], properties={"kind": "drug"}),
        Node(node_id="protein-1", labels=["Protein"], properties={"kind": "protein"}),
    ])
    graph_db.put_edges_bulk([
        Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "binds", "score": 1}),
        Edge(edge_id="e2", source="drug-2", target="protein-1", properties={"type": "binds", "score": 2}),
    ])
    graph_db.create_node_property_index("kind")
    graph_db.create_edge_property_index("score")

    assert graph_db.count_nodes_by_label("Drug") == 2
    assert graph_db.count_nodes_by_property("kind", "drug") == 2
    assert [node.get_id for node in graph_db.nodes_by_label_property("Drug", "kind", "drug")] == ["drug-1", "drug-2"]
    assert graph_db.count_nodes_by_label_property("Drug", "kind", "drug") == 2
    graph_db.put_node(Node(node_id="drug-3", labels=["Drug"], properties={"age": 20}))
    graph_db.put_node(Node(node_id="drug-4", labels=["Drug"], properties={"age": 40}))
    graph_db.create_node_property_index("age")
    assert [node.get_id for node in graph_db.nodes_by_property_range("age", 25, 45)] == ["drug-4"]
    assert [node.get_id for node in graph_db.nodes_by_label_property_range("Drug", "age", 20, 40, include_start=False)] == ["drug-4"]
    assert graph_db.count_nodes_by_label_property_range("Drug", "age", 20, 40) == 2
    assert graph_db.count_edges_by_type("binds") == 2
    assert graph_db.count_edges_by_property("score", 1) == 1
    assert [edge.get_id for edge in graph_db.edges_by_type_property("binds", "score", 1)] == ["e1"]
    assert graph_db.count_edges_by_type_property("binds", "score", 1) == 1
    assert [edge.get_id for edge in graph_db.edges_by_property_range("score", 1, 2)] == ["e1", "e2"]
    assert [edge.get_id for edge in graph_db.edges_by_type_property_range("binds", "score", 1, 2, include_start=False)] == ["e2"]
    assert graph_db.count_edges_by_type_property_range("binds", "score", 1, 2) == 2
    assert graph_db.index_statistics() == {
        "indexed_node_properties": ("age", "kind"),
        "indexed_edge_properties": ("score",),
    }


def test_bulk_edges(graph_db):
    node_a = Node(properties={"label": "A"})
    node_b = Node(properties={"label": "B"})
    node_c = Node(properties={"label": "C"})
    graph_db.put_node(node_a)
    graph_db.put_node(node_b)
    graph_db.put_node(node_c)

    edges = [
        Edge(source=node_a.get_id, target=node_b.get_id, properties={"weight": 1}),
        Edge(source=node_b.get_id, target=node_c.get_id, properties={"weight": 2}),
        Edge(source=node_a.get_id, target=node_c.get_id, properties={"weight": 3}),
    ]

    for edge in edges:
        graph_db.put_edge(edge)

    fetched_edges = [graph_db.get_edge(edge.get_id_bytes) for edge in edges]
    for edge in fetched_edges:
        assert edge is not None
        assert "weight" in edge.properties

    for edge in edges:
        graph_db.delete_edge(edge.get_id_bytes)
    for edge in edges:
        assert graph_db.get_edge(edge.get_id_bytes) is None


def test_put_edges_bulk(graph_db):
    node_a = Node(properties={"label": "A"})
    node_b = Node(properties={"label": "B"})
    node_c = Node(properties={"label": "C"})
    graph_db.put_node(node_a)
    graph_db.put_node(node_b)
    graph_db.put_node(node_c)

    edge_ab = Edge(source=node_a.get_id, target=node_b.get_id, properties={"weight": 1})
    edge_bc = Edge(source=node_b.get_id, target=node_c.get_id, properties={"weight": 2})
    edge_ac = Edge(source=node_a.get_id, target=node_c.get_id, properties={"weight": 3})

    graph_db.put_edges_bulk([edge_ab, edge_bc, edge_ac])

    adj_a = set(graph_db.get_adjacency_list(node_a.get_id_bytes, direction="any"))
    adj_b = set(graph_db.get_adjacency_list(node_b.get_id_bytes, direction="any"))
    adj_c = set(graph_db.get_adjacency_list(node_c.get_id_bytes, direction="any"))

    assert edge_ab.get_id_bytes in adj_a
    assert edge_ac.get_id_bytes in adj_a
    assert edge_ab.get_id_bytes in adj_b
    assert edge_bc.get_id_bytes in adj_b
    assert edge_bc.get_id_bytes in adj_c
    assert edge_ac.get_id_bytes in adj_c


def test_range_query_nodes_uses_store_range_iter(graph_db):
    graph_db.put_node(Node(node_id="n1", properties={"name": "Alice"}))

    class RangeStore:
        def range_iter(self, start_key, end_key):
            assert start_key == b"IDX:N:name:A:"
            assert end_key == "IDX:N:name:Z:\xff".encode("utf-8")
            yield b"IDX:N:name:A:n1", b""

    original_store = graph_db.store
    graph_db.store = RangeStore()
    try:
        graph_db.store.get_node = lambda node_id: original_store.get_node(node_id.encode("utf-8"))
        assert [node.get_id for node in graph_db.range_query_nodes("name", "A", "Z")] == ["n1"]
    finally:
        graph_db.store = original_store
