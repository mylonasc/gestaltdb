import importlib.util
from datetime import date, datetime

import pytest

from gestaltdb import (
    TemporalDate,
    TemporalDuration,
    TemporalInstant,
    TemporalLocalDateTime,
    TemporalTime,
)
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.serializers import (
    JSONSerializer,
    MessagePackSerializer,
    PickleSerializer,
    ProtobufSerializer,
)


BACKENDS = [
    pytest.param("lmdb", LMDBStore, marks=pytest.mark.skipif(importlib.util.find_spec("lmdb") is None, reason="lmdb not installed")),
    pytest.param("leveldb", LevelDBStore, marks=pytest.mark.skipif(importlib.util.find_spec("plyvel") is None, reason="plyvel not installed")),
    pytest.param("pyrex", PyRexStore, marks=pytest.mark.skipif(importlib.util.find_spec("pyrex") is None, reason="pyrex not installed")),
]
SERIALIZERS = [
    pytest.param("pickle", PickleSerializer),
    pytest.param("json", JSONSerializer),
    pytest.param("messagepack", MessagePackSerializer, marks=pytest.mark.skipif(importlib.util.find_spec("msgpack") is None, reason="msgpack not installed")),
    pytest.param("protobuf", ProtobufSerializer, marks=pytest.mark.skipif(importlib.util.find_spec("google.protobuf") is None, reason="protobuf not installed")),
]


@pytest.mark.parametrize(("backend_name", "store_cls"), BACKENDS)
@pytest.mark.parametrize(("serializer_name", "serializer_cls"), SERIALIZERS)
def test_temporal_epistemic_history_reopens_across_backend_serializer_matrix(
    tmp_path, backend_name, store_cls, serializer_name, serializer_cls
):
    path = str(tmp_path / f"{backend_name}-{serializer_name}")
    graph = GraphDB(store_cls(path=path), serializer_cls())
    try:
        node = graph.put_node_version(Node("alice", labels=["Person"]), valid=(0, None))
        edge = graph.put_edge_version(
            Edge("employment", "alice", "acme", {"type": "WORKS_FOR"}),
            valid=(0, None),
        )
        claim = graph.assert_claim(
            subject="alice", predicate="WORKS_FOR", object="acme",
            polarity="positive", agent="source:test", world="reported", valid=(0, None),
        )
    finally:
        graph.close()

    reopened = GraphDB(store_cls(path=path), serializer_cls())
    try:
        assert reopened.get_node_version(node.version_id).node.labels == ("Person",)
        assert reopened.get_edge_version(edge.version_id).edge.get_type == "WORKS_FOR"
        assert reopened.get_claim_as_of(claim.logical_id, valid_time=0).claim.agent == "source:test"
    finally:
        reopened.close()


@pytest.mark.parametrize(("backend_name", "store_cls"), BACKENDS)
@pytest.mark.parametrize(("serializer_name", "serializer_cls"), SERIALIZERS)
def test_temporal_graph_properties_reopen_and_use_indexes_across_matrix(
    tmp_path, backend_name, store_cls, serializer_name, serializer_cls
):
    path = str(tmp_path / f"properties-{backend_name}-{serializer_name}")
    graph = GraphDB(store_cls(path=path), serializer_cls())
    try:
        graph.create_node_property_index("when")
        graph.create_node_property_index("at")
        graph.create_edge_property_index("since")
        created = graph.query(
            "CREATE (n:Event {id: 'event-1', when: date('2024-01-02'), "
            "at: time('12:00:00+01:00'), nested: [duration('PT1S')]}) "
            "RETURN n.when AS value"
        )
        assert created.records == [{"value": TemporalDate(date(2024, 1, 2))}]
        graph.put_node(Node("target"))
        graph.put_edge(Edge(
            "event-target",
            "event-1",
            "target",
            {
                "type": "OBSERVED",
                "since": TemporalInstant(-1),
                "nested": {"local": TemporalLocalDateTime(datetime(2024, 1, 2, 3, 4, 5))},
            },
        ))
    finally:
        graph.close()

    reopened = GraphDB(store_cls(path=path), serializer_cls())
    try:
        node = reopened.get_node(b"event-1")
        assert node.properties["nested"] == [TemporalDuration(1_000_000)]
        assert [item.get_id for item in reopened.nodes_by_property(
            "at", TemporalTime(39_600_000_000, 0)
        )] == ["event-1"]
        assert [item.get_id for item in reopened.nodes_by_property_range(
            "when", TemporalDate(date(2024, 1, 1)), TemporalDate(date(2024, 1, 3))
        )] == ["event-1"]
        assert reopened.get_edge(b"event-target").properties["nested"] == {
            "local": TemporalLocalDateTime(datetime(2024, 1, 2, 3, 4, 5))
        }
        assert [item.get_id for item in reopened.edges_by_property(
            "since", TemporalInstant(-1)
        )] == ["event-target"]
        assert [item.get_id for item in reopened.edges_by_property_range(
            "since", TemporalInstant(-2), TemporalInstant(0)
        )] == ["event-target"]
        result = reopened.query(
            "MATCH (n:Event) WHERE n.when >= date('2024-01-01') "
            "AND n.when < date('2024-01-03') "
            "RETURN n.when AS value, n.nested AS nested"
        )
        assert result.records == [{
            "value": TemporalDate(date(2024, 1, 2)),
            "nested": [TemporalDuration(1_000_000)],
        }]
    finally:
        reopened.close()
