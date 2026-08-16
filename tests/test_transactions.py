import importlib.util

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import KVStore, LMDBStore, PyRexStore
from gestaltdb.serializers import PickleSerializer


def _transactional_backends():
    backends = []
    if importlib.util.find_spec("lmdb") is not None:
        backends.append(("lmdb", lambda path: LMDBStore(path=str(path))))
    if importlib.util.find_spec("pyrex") is not None:
        import pyrex

        if getattr(pyrex, "has_transactions", False):
            backends.append(("pyrex", lambda path: PyRexStore(path=str(path), transactional=True)))
    return backends


TRANSACTIONAL_BACKENDS = _transactional_backends()
TRANSACTIONAL_BACKEND_PARAMS = TRANSACTIONAL_BACKENDS or [
    pytest.param(None, marks=pytest.mark.skip(reason="no transactional backend installed"))
]


def test_kvstore_default_transaction_is_unsupported():
    with pytest.raises(NotImplementedError):
        KVStore().transaction()


@pytest.mark.parametrize("backend", TRANSACTIONAL_BACKEND_PARAMS, ids=lambda item: item[0] if item is not None else "none")
def test_graph_transaction_commits_node_and_indexes(backend, tmp_path):
    backend_name, store_factory = backend
    graph = GraphDB(store_factory(tmp_path / backend_name), PickleSerializer(), indexed_node_properties=["kind"])
    try:
        with graph.transaction() as tx:
            tx.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}))

        assert graph.get_node(b"drug-1").properties == {"kind": "drug"}
        assert [node.get_id for node in graph.nodes_by_label("Drug")] == ["drug-1"]
        assert [node.get_id for node in graph.nodes_by_property("kind", "drug")] == ["drug-1"]
    finally:
        graph.close()


@pytest.mark.parametrize("backend", TRANSACTIONAL_BACKEND_PARAMS, ids=lambda item: item[0] if item is not None else "none")
def test_graph_transaction_rolls_back_node_and_indexes(backend, tmp_path):
    backend_name, store_factory = backend
    graph = GraphDB(store_factory(tmp_path / backend_name), PickleSerializer(), indexed_node_properties=["kind"])
    try:
        with pytest.raises(RuntimeError):
            with graph.transaction() as tx:
                tx.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}))
                raise RuntimeError("abort")

        assert graph.get_node(b"drug-1") is None
        assert graph.nodes_by_label("Drug") == []
        assert graph.nodes_by_property("kind", "drug") == []
    finally:
        graph.close()


@pytest.mark.parametrize("backend", TRANSACTIONAL_BACKEND_PARAMS, ids=lambda item: item[0] if item is not None else "none")
def test_graph_transaction_commits_edge_adjacency_and_type_index(backend, tmp_path):
    backend_name, store_factory = backend
    graph = GraphDB(store_factory(tmp_path / backend_name), PickleSerializer())
    try:
        with graph.transaction() as tx:
            tx.put_node(Node(node_id="n1"))
            tx.put_node(Node(node_id="n2"))
            tx.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "rel"}))

        assert graph.get_edge(b"e1") is not None
        assert graph.neighbors_by_edge_type("n1", "rel") == [b"n2"]
        assert [edge.get_id for edge in graph.edges_by_type("rel")] == ["e1"]
    finally:
        graph.close()


@pytest.mark.parametrize("backend", TRANSACTIONAL_BACKEND_PARAMS, ids=lambda item: item[0] if item is not None else "none")
def test_graph_transaction_rolls_back_edge_adjacency_and_type_index(backend, tmp_path):
    backend_name, store_factory = backend
    graph = GraphDB(store_factory(tmp_path / backend_name), PickleSerializer())
    try:
        graph.put_node(Node(node_id="n1"))
        graph.put_node(Node(node_id="n2"))
        with pytest.raises(RuntimeError):
            with graph.transaction() as tx:
                tx.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "rel"}))
                raise RuntimeError("abort")

        assert graph.get_edge(b"e1") is None
        assert graph.neighbors_by_edge_type("n1", "rel") == []
        assert graph.edges_by_type("rel") == []
    finally:
        graph.close()
