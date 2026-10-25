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


def test_standalone_coordinator_rebuilds_without_facade(tmp_path, monkeypatch):
    from gestaltdb.ingestion import IndexMaintenanceMode
    from gestaltdb.ingestion_service import IngestionCoordinator
    from gestaltdb.indexes import STALE_KEY

    graph = _graph(tmp_path, "lmdb")
    try:
        graph.indexes.create_node_property_index("name")
        coordinator = IngestionCoordinator(StorageSession(graph.store, graph.serializer))
        for name in ("__init__", "transaction", "ingest_arrow", "rebuild_deferred_indexes",
                     "rebuild_node_indexes", "rebuild_edge_indexes"):
            monkeypatch.setattr(GraphDB, name, forbidden)
        result = coordinator.ingest_arrow(
            ["a", "b"], ["ab"], ["a"], ["b"], ["T"],
            labels=[["Person"], ["Person"]],
            node_properties={"name": ["Alice", "Bob"]},
            edge_properties={"score": [7]},
            index_mode=IndexMaintenanceMode.DEFER, native=False)
        assert result["nodes"] == 2
        assert result["edges"] == 1
        assert result["rebuilt_indexes"] == {}
        assert set(result["stale_indexes"]) == {"node_label", "node_property", "edge_type"}
        rebuilt = coordinator.rebuild_deferred_indexes()
        assert rebuilt["node_label"] == 2
        assert rebuilt["node_property:name"] == 2
        assert rebuilt["edge_type"] == 1
        assert coordinator.stale_indexes() == ()
        assert list(graph.indexes.iter_node_ids_by_property("name", "Alice")) == [b"a"]
        assert graph.nodes.get(b"a").properties["name"] == "Alice"
        assert graph.store.get_metadata(STALE_KEY) in (None, b"[]")
    finally:
        graph.close()


class _FakeTemporal:
    def __init__(self):
        self.rebuilds = 0

    def index_horizon(self):
        return None

    def has_visible_history(self):
        return True

    def rebuild_temporal_indexes(self):
        self.rebuilds += 1
        return {"temporal": 3}


def test_coordinator_uses_explicit_temporal_capability(tmp_path):
    from gestaltdb.ingestion_service import IngestionCoordinator

    graph = _graph(tmp_path, "lmdb")
    try:
        session = StorageSession(graph.store, graph.serializer)
        temporal = _FakeTemporal()
        coordinator = IngestionCoordinator(session, temporal=temporal)
        assert coordinator.stale_indexes() == ("temporal",)
        assert coordinator.rebuild_deferred_indexes() == {"temporal": 3}
        assert temporal.rebuilds == 1
    finally:
        graph.close()


def test_coordinator_fails_closed_on_temporal_rebuild_without_capability(tmp_path):
    from gestaltdb._graph import index_maintenance
    from gestaltdb.ingestion_service import IngestionCoordinator
    from gestaltdb.indexes import STALE_KEY

    graph = _graph(tmp_path, "lmdb")
    try:
        index_maintenance.persist_stale_indexes(graph.store, STALE_KEY, {"temporal"})
        coordinator = IngestionCoordinator(StorageSession(graph.store, graph.serializer))
        with pytest.raises(RuntimeError, match="temporal"):
            coordinator.rebuild_deferred_indexes()
    finally:
        graph.close()


def test_high_level_ingestion_rolls_back_both_families(tmp_path, monkeypatch):
    from gestaltdb._graph.entities import GraphEntityDictSerializer

    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="pickle")
    try:
        graph.indexes.create_node_property_index("name")
        original = GraphEntityDictSerializer.serialize

        def broken(self, entity, kind):
            if kind == "Edge" and entity.get_id == "bad":
                raise ValueError("injected edge failure")
            return original(self, entity, kind)

        monkeypatch.setattr(GraphEntityDictSerializer, "serialize", broken)
        with pytest.raises(ValueError, match="injected"):
            graph.ingest_arrow(
                ["a"], ["bad"], ["a"], ["b"], ["T"],
                node_properties={"name": ["Alice"]}, native=False)
        assert graph.nodes.get(b"a") is None
        assert graph.edges.get(b"bad") is None
        assert list(graph.indexes.iter_node_ids_by_property("name", "Alice")) == []
    finally:
        graph.close()
