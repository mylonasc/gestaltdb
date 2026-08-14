import builtins
from contextlib import contextmanager
import importlib.util

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.serializers import PickleSerializer


@contextmanager
def blocked_import(package_name):
    original_import = builtins.__import__

    def import_hook(name, globals=None, locals=None, fromlist=(), level=0):
        if name == package_name or name.startswith(f"{package_name}."):
            raise ImportError(f"blocked import: {package_name}")
        return original_import(name, globals, locals, fromlist, level)

    builtins.__import__ = import_hook
    try:
        yield
    finally:
        builtins.__import__ = original_import


BACKEND_PARAMS = [
    pytest.param(("lmdb", LMDBStore), marks=pytest.mark.skipif(importlib.util.find_spec("lmdb") is None, reason="lmdb not installed")),
    pytest.param(("leveldb", LevelDBStore), marks=pytest.mark.skipif(importlib.util.find_spec("plyvel") is None, reason="plyvel not installed")),
    pytest.param(("pyrex", PyRexStore), marks=pytest.mark.skipif(importlib.util.find_spec("pyrex") is None, reason="pyrex not installed")),
]


@pytest.fixture(params=BACKEND_PARAMS, ids=lambda param: param[0] if isinstance(param, tuple) else str(param))
def graph_db(request, tmp_path):
    backend_name, store_cls = request.param
    path = tmp_path / backend_name
    graph = GraphDB(store_cls(path=str(path)), PickleSerializer())
    try:
        yield graph
    finally:
        graph.close()


@pytest.fixture
def lmdb_graph_db(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB(LMDBStore(path=str(tmp_path / "lmdb")), PickleSerializer())
    try:
        yield graph
    finally:
        graph.close()


def populate_typed_graph(graph):
    nodes = [
        Node(node_id="drug-1", properties={"kind": "drug"}),
        Node(node_id="drug-2", properties={"kind": "drug"}),
        Node(node_id="protein-1", properties={"kind": "protein"}),
        Node(node_id="protein-2", properties={"kind": "protein"}),
        Node(node_id="protein-3", properties={"kind": "protein"}),
        Node(node_id="disease-1", properties={"kind": "disease"}),
        Node(node_id="disease-2", properties={"kind": "disease"}),
        Node(node_id="disease-3", properties={"kind": "disease"}),
    ]
    for node in nodes:
        graph.put_node(node)

    edges = [
        Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"}),
        Edge(edge_id="d1-p2", source="drug-1", target="protein-2", properties={"type": "drug-to-protein"}),
        Edge(edge_id="d1-disease", source="drug-1", target="disease-1", properties={"type": "drug-to-disease"}),
        Edge(edge_id="d2-p3", source="drug-2", target="protein-3", properties={"type": "drug-to-protein"}),
        Edge(edge_id="p1-dis1", source="protein-1", target="disease-1", properties={"type": "protein-to-disease"}),
        Edge(edge_id="p1-dis2", source="protein-1", target="disease-2", properties={"type": "protein-to-disease"}),
        Edge(edge_id="p2-dis3", source="protein-2", target="disease-3", properties={"type": "protein-to-disease"}),
    ]
    graph.put_edges_bulk(edges)
    return edges
