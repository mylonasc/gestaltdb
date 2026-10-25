"""Phase 0 characterization: scope binding that refactoring must preserve.

These tests lock the riskiest seams before extraction: transaction and
snapshot scopes must bind to their own store, share only deliberate state,
and never leak caches back to the live graph.
"""

import importlib.util
import pickle

import pytest

import gestaltdb.graphdb as graphdb_module
from gestaltdb._graph import entities as entities_module
from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.serializers import JSONSerializer


def _lmdb_graph(tmp_path):
    pytest.importorskip("lmdb")
    path = tmp_path / "graph"
    graph = GraphDB(LMDBStore(path=str(path)), JSONSerializer())
    graph.save_manifest(path)
    return graph


def test_entity_extraction_preserves_identity_and_pickle_path():
    assert graphdb_module.Node is entities_module.Node
    assert graphdb_module.Edge is entities_module.Edge
    assert graphdb_module.TimeIndexedEdge is entities_module.TimeIndexedEdge
    assert graphdb_module.GraphEntityDictSerializer is entities_module.GraphEntityDictSerializer
    # Historical pickle streams record gestaltdb.graphdb; keep that path stable.
    assert entities_module.Node.__module__ == "gestaltdb.graphdb"
    assert entities_module.Edge.__module__ == "gestaltdb.graphdb"
    node = Node(node_id="n1", labels=["A"])
    assert pickle.loads(pickle.dumps(node)).get_id == "n1"
    assert b"gestaltdb.graphdb" in pickle.dumps(node)


def test_transaction_binds_tx_store_and_shares_only_deliberate_state(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        graph.put_node(Node("n1"))
        live_store = graph.store
        with graph.transaction() as tx:
            assert isinstance(tx, GraphDB)
            assert tx.store is not live_store
            assert tx.temporal.is_transaction_bound is True
            assert tx.temporal._write_lock is graph.temporal._write_lock
            assert tx.temporal._clock is graph.temporal._clock
            assert tx._store_path == graph._store_path
            assert tx._database_id == graph._database_id
            tx.put_node(Node("tx-only"))
            assert graph.get_node(b"tx-only") is None
        assert graph.get_node(b"tx-only") is not None
    finally:
        graph.close()


def test_transaction_rollback_does_not_publish_tx_writes(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="abort"):
            with graph.transaction() as tx:
                tx.put_node(Node("aborted"))
                raise RuntimeError("abort")
        assert graph.get_node(b"aborted") is None
    finally:
        graph.close()


def test_transaction_index_config_is_copied_not_shared(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        graph.create_node_property_index("kind")
        with graph.transaction() as tx:
            assert tx.indexed_node_properties == graph.indexed_node_properties
            assert tx.indexed_node_properties is not graph.indexed_node_properties
            tx.indexed_node_properties.add("injected")
        assert "injected" not in graph.indexed_node_properties
    finally:
        graph.close()


def test_snapshot_binds_snapshot_store_and_isolates_caches(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        graph.put_node(Node("n1", labels=["L"]))
        # Populate the live adjacency-count cache path (empty but present).
        assert graph.count_typed_adjacency("n1", "T") == 0
        assert graph._typed_adjacency_count_cache != {}
        live_store = graph.store
        with graph.current_read_view() as view:
            snapshot_graph = view._graph
            assert snapshot_graph.store is not live_store
            assert snapshot_graph._typed_adjacency_count_cache == {}
            assert snapshot_graph.store is view.store
            assert view.get_node(b"n1").get_id == "n1"
        assert graph.store is live_store
    finally:
        graph.close()


def test_snapshot_reads_do_not_repopulate_live_caches(tmp_path):
    pytest.importorskip("lmdb")
    graph = _lmdb_graph(tmp_path)
    try:
        graph.put_node(Node("n1", labels=["L"]))
        graph._typed_adjacency_count_cache.clear()
        with graph.current_read_view() as view:
            assert view.count_typed_adjacency("n1", "T") == 0
        assert graph._typed_adjacency_count_cache == {}
    finally:
        graph.close()
