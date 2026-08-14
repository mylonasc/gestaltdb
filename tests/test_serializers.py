import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.serializers import JSONSerializer, MessagePackSerializer, PickleSerializer, ProtobufSerializer, Serializer
from .conftest import blocked_import


def serializer_round_trip_cases():
    return [
        PickleSerializer(),
        JSONSerializer(),
        MessagePackSerializer(),
        ProtobufSerializer(),
    ]


def test_base_serializer_methods_are_abstract():
    serializer = Serializer()

    with pytest.raises(NotImplementedError):
        serializer.serialize({})
    with pytest.raises(NotImplementedError):
        serializer.deserialize(b"{}")


@pytest.mark.parametrize("serializer", serializer_round_trip_cases(), ids=lambda serializer: serializer.__class__.__name__)
def test_serializers_round_trip_json_like_dicts(serializer):
    payload = {
        "id": "alice",
        "properties": {
            "name": "Alice",
            "age": 30,
            "score": 9.5,
            "active": True,
            "tags": ["person", "employee"],
            "metadata": {"department": "Engineering"},
        },
    }

    assert serializer.deserialize(serializer.serialize(payload)) == payload


@pytest.mark.parametrize("serializer", [PickleSerializer(), MessagePackSerializer(), ProtobufSerializer()], ids=lambda serializer: serializer.__class__.__name__)
def test_binary_serializers_round_trip_bytes(serializer):
    payload = {
        "edge_ids": [b"edge-1", b"edge-2"],
        "properties": {"raw": b"\x00\x01\x02"},
    }

    assert serializer.deserialize(serializer.serialize(payload)) == payload


def test_protobuf_serializer_round_trips_nested_tuples_and_ints():
    serializer = ProtobufSerializer()
    payload = {"values": (1, b"raw", {"nested": 2}), "flag": True}

    assert serializer.deserialize(serializer.serialize(payload)) == {"values": [1, b"raw", {"nested": 2}], "flag": True}


def test_optional_serializer_deserialize_reports_missing_dependency():
    with blocked_import("msgpack"):
        with pytest.raises(ImportError, match="Missing optional dependency 'msgpack'"):
            MessagePackSerializer().deserialize(b"")
    with blocked_import("google.protobuf"):
        with pytest.raises(ImportError, match="Missing optional dependency 'protobuf'"):
            ProtobufSerializer().deserialize(b"")


@pytest.mark.parametrize("serializer", [MessagePackSerializer(), ProtobufSerializer()], ids=lambda serializer: serializer.__class__.__name__)
def test_graphdb_round_trip_with_binary_serializers(serializer, tmp_path):
    pytest.importorskip("lmdb")
    graph_db = GraphDB(LMDBStore(path=str(tmp_path / serializer.__class__.__name__)), serializer)
    try:
        node_a = Node(node_id="alice", properties={"name": "Alice", "age": 30})
        node_b = Node(node_id="bob", properties={"name": "Bob"})
        edge = Edge(edge_id="alice-bob", source=node_a.get_id, target=node_b.get_id, properties={"relation": "friend"})

        graph_db.put_node(node_a)
        graph_db.put_node(node_b)
        graph_db.put_edge(edge)

        assert graph_db.get_node(b"alice").properties == node_a.properties
        assert graph_db.get_edge(b"alice-bob").properties == edge.properties
        assert graph_db.get_adjacency_list(b"alice", direction="any") == ["alice-bob"]
    finally:
        graph_db.close()
