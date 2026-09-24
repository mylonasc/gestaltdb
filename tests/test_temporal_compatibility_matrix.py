import importlib.util

import pytest

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
