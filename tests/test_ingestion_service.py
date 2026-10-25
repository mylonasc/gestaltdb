"""Format loaders work without GraphDB and retain scope/rollback semantics."""

import pytest

from gestaltdb.current import StorageSession
from gestaltdb.graphdb import GraphDB
from gestaltdb.ingestion_service import IngestionService
from tests.test_current_node_repository import _graph, forbidden


@pytest.mark.parametrize("backend", ["lmdb", "leveldb", "pyrex"])
@pytest.mark.parametrize("format", ["arrow", "polars"])
def test_standalone_format_loaders_without_facade(tmp_path, backend, format, monkeypatch):
    pl = pytest.importorskip("polars") if format == "polars" else None
    graph = _graph(tmp_path, backend)
    try:
        graph.indexes.create_node_property_index("name")
        graph.indexes.create_edge_property_index("score")
        ingest = IngestionService(StorageSession(graph.store, graph.serializer))
        for name in ("__init__", "transaction", "ingest_nodes_arrow",
                     "ingest_nodes_polars", "ingest_edges_arrow", "ingest_edges_polars"):
            monkeypatch.setattr(GraphDB, name, forbidden)
        if format == "arrow":
            assert ingest.arrow.node_entities(
                ["a", "b"], labels=[["Person"], ["Person"]],
                properties={"name": ["Alice", "Bob"]}, native=False) == 2
            assert ingest.arrow.edge_entities(
                ["ab"], ["a"], ["b"], ["T"], properties={"score": [7]}, native=False) == 1
        else:
            assert ingest.polars.node_entities(pl.DataFrame({
                "node_id": ["a", "b"], "labels": [["Person"], ["Person"]],
                "name": ["Alice", "Bob"],
            }), property_columns=["name"], native=False) == 2
            assert ingest.polars.edge_entities(pl.DataFrame({
                "edge_id": ["ab"], "source": ["a"], "target": ["b"],
                "edge_type": ["T"], "score": [7],
            }), property_columns=["score"], native=False) == 1
        assert graph.nodes.get(b"a").properties["name"] == "Alice"
        assert graph.edges.get(b"ab").properties["score"] == 7
        assert list(graph.indexes.iter_node_ids_by_property("name", "Alice")) == [b"a"]
        assert list(graph.indexes.iter_edge_ids_by_type_property("T", "score", 7)) == [b"ab"]
    finally:
        graph.close()


@pytest.mark.parametrize("backend", ["lmdb", "pyrex"])
def test_format_failure_rolls_back_chunks_and_indexes(tmp_path, backend, monkeypatch):
    pytest.importorskip("lmdb" if backend == "lmdb" else "pyrex")
    graph = GraphDB.create(
        tmp_path / "db", backend=backend, serializer="pickle",
        backend_options={"transactional": True} if backend == "pyrex" else None)
    try:
        graph.indexes.create_node_property_index("name")
        original = graph.entity_serializer.serialize

        def broken(entity, kind):
            if kind == "Node" and entity.get_id == "bad":
                raise ValueError("injected serialization failure")
            return original(entity, kind)

        monkeypatch.setattr(graph.entity_serializer, "serialize", broken)
        with pytest.raises(ValueError, match="injected"):
            graph.ingest.arrow.node_entities(
                ["a", "bad"], properties={"name": ["Alice", "Bad"]},
                chunk_size=1, native=False)
        assert graph.nodes.get(b"a") is None
        assert list(graph.indexes.iter_node_ids_by_property("name", "Alice")) == []
    finally:
        graph.close()


def test_ingestion_transaction_binding_and_snapshot_authority(tmp_path):
    graph = _graph(tmp_path, "lmdb")
    try:
        with pytest.raises(ValueError, match="abort"):
            with graph.transaction() as tx:
                assert tx.ingest.arrow._session.store is tx.store
                tx.ingest.arrow.node_entities(["a"], native=False)
                assert tx.nodes.get(b"a") is not None
                assert graph.nodes.get(b"a") is None
                raise ValueError("abort")
        assert graph.nodes.get(b"a") is None
        with graph.current_read_view() as view:
            with pytest.raises(RuntimeError, match="read-only"):
                _ = view.ingest
    finally:
        graph.close()
