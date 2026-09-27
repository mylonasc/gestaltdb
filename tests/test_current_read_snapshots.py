import copy
import shutil
import threading

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import (
    LMDBReadSnapshotStore,
    LMDBStore,
    LMDBTransactionStore,
    LevelDBStore,
    PyRexStore,
)
from gestaltdb.readview import CurrentReadProvenance, ProvenanceMismatchError
from gestaltdb.serializers import JSONSerializer


def _lmdb_graph(tmp_path):
    pytest.importorskip("lmdb")
    path = tmp_path / "graph"
    graph = GraphDB(LMDBStore(path=str(path)), JSONSerializer())
    graph.save_manifest(path)
    return graph


def _populate_snapshot_graph(graph):
    graph.create_node_property_index("kind")
    with graph.transaction() as tx:
        tx.put_node(Node("n1", labels=["Old"], properties={"kind": "old", "value": 1}))
        tx.put_node(Node("n2"))
        tx.put_edge(Edge("e1", "n1", "n2", {"type": "OLD"}))


def test_lmdb_current_read_view_pins_entities_indexes_and_adjacency(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        _populate_snapshot_graph(graph)
        with graph.current_read_view(require_verifiable=True) as view:
            sequence = view.provenance.snapshot_sequence
            assert view.get_node(b"n1").properties["value"] == 1
            assert list(view.iter_node_ids_by_property("kind", "old")) == [b"n1"]
            assert [record["edge_id"] for record in view.iter_typed_adjacency("n1", "OLD")] == [b"e1"]

            with graph.transaction() as tx:
                tx.put_node(Node("n1", labels=["New"], properties={"kind": "new", "value": 2}))
                tx.put_edge(Edge("e1", "n1", "n2", {"type": "NEW"}))

            assert view.provenance.snapshot_sequence == sequence
            assert view.get_node(b"n1").properties["value"] == 1
            assert list(view.iter_node_ids_by_property("kind", "old")) == [b"n1"]
            assert [record["edge_id"] for record in view.iter_typed_adjacency("n1", "OLD")] == [b"e1"]
            with pytest.raises(RuntimeError, match="read-only"):
                view.store.put_node(b"forbidden", b"payload")
            with pytest.raises(RuntimeError, match="read-only"):
                view.put_node(Node("forbidden"))

        with graph.current_read_view(require_verifiable=True) as newer:
            assert newer.provenance.snapshot_sequence > sequence
            assert newer.get_node(b"n1").properties["value"] == 2
            assert list(newer.iter_node_ids_by_property("kind", "new")) == [b"n1"]
            assert [record["edge_id"] for record in newer.iter_typed_adjacency("n1", "NEW")] == [b"e1"]
    finally:
        graph.close()


def test_current_read_provenance_authenticates_and_detects_source_advance(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        graph.put_node(Node("n1"))
        with graph.current_read_view(require_verifiable=True) as view:
            provenance = view.provenance

        assert CurrentReadProvenance.from_json(provenance.to_json()) == provenance
        provenance.verify_source(graph)

        tampered = copy.deepcopy(provenance.to_dict())
        tampered["snapshot_sequence"] += 1
        with pytest.raises(ProvenanceMismatchError, match="token mismatch"):
            CurrentReadProvenance.from_dict(tampered)

        graph.put_node(Node("n2"))
        with pytest.raises(ProvenanceMismatchError, match="identity mismatch"):
            provenance.verify_source(graph)
    finally:
        graph.close()


def test_implicit_edge_transactions_invalidate_parent_count_cache(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        graph.put_nodes([Node("n1"), Node("n2")])
        assert graph.count_typed_adjacency("n1", "REL") == 0

        graph.put_edge(Edge("e1", "n1", "n2", {"type": "REL"}))
        assert graph.count_typed_adjacency("n1", "REL") == 1

        graph.delete_edge(b"e1")
        assert graph.count_typed_adjacency("n1", "REL") == 0
    finally:
        graph.close()


def test_cypher_uses_one_lmdb_snapshot_across_index_scan_and_hydration(tmp_path, monkeypatch):
    graph = _lmdb_graph(tmp_path)
    ready = threading.Event()
    resume = threading.Event()
    result = {}
    try:
        _populate_snapshot_graph(graph)
        original = LMDBReadSnapshotStore.iter_index_prefix

        def blocking_iter(snapshot, index_name, key_parts):
            for value in original(snapshot, index_name, key_parts):
                ready.set()
                assert resume.wait(5)
                yield value

        monkeypatch.setattr(LMDBReadSnapshotStore, "iter_index_prefix", blocking_iter)

        def run_query():
            try:
                result["records"] = graph.query(
                    "MATCH (n:Old {kind: 'old'}) RETURN n.value AS value"
                ).records
            except Exception as exc:  # pragma: no cover - asserted below
                result["error"] = exc

        thread = threading.Thread(target=run_query)
        thread.start()
        assert ready.wait(5)
        with graph.transaction() as tx:
            tx.put_node(Node("n1", labels=["New"], properties={"kind": "new", "value": 2}))
        resume.set()
        thread.join(5)

        assert not thread.is_alive()
        assert "error" not in result
        assert result["records"] == [{"value": 1}]
    finally:
        resume.set()
        graph.close()


def test_ordinary_lmdb_graph_write_publishes_entity_and_indexes_atomically(tmp_path, monkeypatch):
    graph = _lmdb_graph(tmp_path)
    ready = threading.Event()
    resume = threading.Event()
    result = {}
    try:
        _populate_snapshot_graph(graph)
        original = LMDBTransactionStore.put_node

        def blocking_put(store, node_id, value):
            original(store, node_id, value)
            if node_id == b"n1":
                ready.set()
                assert resume.wait(5)

        monkeypatch.setattr(LMDBTransactionStore, "put_node", blocking_put)

        def replace_node():
            try:
                graph.put_node(Node("n1", labels=["New"], properties={"kind": "new", "value": 2}))
            except Exception as exc:  # pragma: no cover - asserted below
                result["error"] = exc

        thread = threading.Thread(target=replace_node)
        thread.start()
        assert ready.wait(5)
        assert graph.query(
            "MATCH (n:Old {kind: 'old'}) RETURN n.value AS value"
        ).records == [{"value": 1}]
        assert graph.query("MATCH (n:New) RETURN n.value AS value").records == []
        resume.set()
        thread.join(5)

        assert not thread.is_alive()
        assert "error" not in result
        assert graph.query(
            "MATCH (n:New {kind: 'new'}) RETURN n.value AS value"
        ).records == [{"value": 2}]
    finally:
        resume.set()
        graph.close()


def test_lmdb_columnar_ingestion_publishes_entity_and_indexes_atomically(tmp_path, monkeypatch):
    graph = _lmdb_graph(tmp_path)
    ready = threading.Event()
    resume = threading.Event()
    result = {}
    try:
        graph.create_node_property_index("kind")
        graph.put_node(Node("n1", labels=["Old"], properties={"kind": "old"}))
        replacement = graph.serialize_node_value(
            Node("n1", labels=["New"], properties={"kind": "new"})
        )
        original = LMDBTransactionStore.ingest_nodes_columnar

        def blocking_ingest(store, node_list, *, native=True):
            original(store, node_list, native=native)
            ready.set()
            assert resume.wait(5)

        monkeypatch.setattr(LMDBTransactionStore, "ingest_nodes_columnar", blocking_ingest)

        def replace_node():
            try:
                graph.ingest_nodes_arrow(
                    [b"n1"], [replacement], append_only=False, native=False
                )
            except Exception as exc:  # pragma: no cover - asserted below
                result["error"] = exc

        thread = threading.Thread(target=replace_node)
        thread.start()
        assert ready.wait(5)
        assert [node.get_id for node in graph.nodes_by_property("kind", "old")] == ["n1"]
        assert graph.nodes_by_property("kind", "new") == []
        resume.set()
        thread.join(5)

        assert not thread.is_alive()
        assert "error" not in result
        assert graph.nodes_by_property("kind", "old") == []
        assert [node.get_id for node in graph.nodes_by_property("kind", "new")] == ["n1"]
    finally:
        resume.set()
        graph.close()


def test_lmdb_columnar_edge_ingestion_publishes_adjacency_atomically(tmp_path, monkeypatch):
    graph = _lmdb_graph(tmp_path)
    ready = threading.Event()
    resume = threading.Event()
    result = {}
    try:
        graph.put_nodes([Node("n1"), Node("n2")])
        edge = Edge("e1", "n1", "n2", {"type": "REL"})
        original = LMDBTransactionStore.ingest_edges_columnar

        def blocking_ingest(store, edge_list, **kwargs):
            original(store, edge_list, **kwargs)
            ready.set()
            assert resume.wait(5)

        monkeypatch.setattr(LMDBTransactionStore, "ingest_edges_columnar", blocking_ingest)

        def add_edge():
            try:
                graph.ingest_edges_arrow(
                    [b"e1"], [b"n1"], [b"n2"], ["REL"],
                    [graph.serialize_edge_value(edge)], native=False,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                result["error"] = exc

        thread = threading.Thread(target=add_edge)
        thread.start()
        assert ready.wait(5)
        with graph.current_read_view() as view:
            assert view.get_edge(b"e1") is None
            assert list(view.iter_typed_adjacency("n1", "REL")) == []
        resume.set()
        thread.join(5)

        assert not thread.is_alive()
        assert "error" not in result
        assert graph.get_edge(b"e1").get_id == "e1"
        assert [record["edge_id"] for record in graph.iter_typed_adjacency("n1", "REL")] == [b"e1"]
    finally:
        resume.set()
        graph.close()


def test_sampler_build_uses_one_lmdb_snapshot_and_records_provenance(tmp_path, monkeypatch):
    graph = _lmdb_graph(tmp_path)
    ready = threading.Event()
    resume = threading.Event()
    result = {}
    try:
        _populate_snapshot_graph(graph)
        original = LMDBReadSnapshotStore.get_edge_keys_generator

        def blocking_edges(snapshot, num_edges=None, key_offset=None):
            ready.set()
            assert resume.wait(5)
            yield from original(snapshot, num_edges=num_edges, key_offset=key_offset)

        monkeypatch.setattr(LMDBReadSnapshotStore, "get_edge_keys_generator", blocking_edges)

        def build_snapshot():
            try:
                result["snapshot"] = graph.build_sampler_snapshot(tmp_path / "snapshot")
            except Exception as exc:  # pragma: no cover - asserted below
                result["error"] = exc

        thread = threading.Thread(target=build_snapshot)
        thread.start()
        assert ready.wait(5)
        with graph.transaction() as tx:
            tx.put_node(Node("n3"))
            tx.put_edge(Edge("e2", "n1", "n3", {"type": "NEW"}))
        resume.set()
        thread.join(5)

        assert not thread.is_alive()
        assert "error" not in result
        snapshot = result["snapshot"]
        assert snapshot.external_node_ids.tolist() == ["n1", "n2"]
        assert snapshot.external_edge_ids.tolist() == ["e1"]
        assert snapshot.metadata["source_provenance"]["backend"]["snapshot_kind"] == "mutable_backend_snapshot"
        with pytest.raises(ProvenanceMismatchError, match="identity mismatch"):
            snapshot.verify_source(graph)
    finally:
        resume.set()
        graph.close()


def test_sampler_build_does_not_fallback_after_snapshot_acquisition(tmp_path):
    graph = _lmdb_graph(tmp_path)
    calls = 0
    try:
        graph.put_node(Node("n1"))

        def unsupported_filter(node):
            nonlocal calls
            calls += 1
            raise NotImplementedError("filter failure")

        with pytest.raises(NotImplementedError, match="filter failure"):
            graph.build_sampler_snapshot(
                tmp_path / "snapshot-filter-failure", node_filter=unsupported_filter
            )
        assert calls == 1
    finally:
        graph.close()


def test_current_read_view_blocks_side_effecting_graph_methods(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        initial_sequence = graph.store.current_snapshot_sequence()
        with graph.current_read_view() as view:
            for method_name in (
                "assert_claim",
                "commit_versions",
                "maintain_truth",
                "save_manifest",
            ):
                with pytest.raises(RuntimeError, match="read-only"):
                    getattr(view, method_name)
        assert graph.store.current_snapshot_sequence() == initial_sequence
        assert graph.list_temporal_orphans().artifacts == ()
    finally:
        graph.close()


def test_leveldb_snapshot_requests_fail_closed(tmp_path):
    pytest.importorskip("plyvel")
    path = tmp_path / "leveldb"
    graph = GraphDB(LevelDBStore(path=str(path)), JSONSerializer())
    try:
        graph.save_manifest(path)
        graph.put_node(Node("n1"))
        with pytest.raises(NotImplementedError, match="cannot provide a unified snapshot"):
            with graph.current_read_view():
                pass
        with pytest.raises(NotImplementedError, match="cannot provide a unified snapshot"):
            graph.query("MATCH (n) RETURN n", read_snapshot=True)
        with pytest.raises(NotImplementedError, match="cannot provide a unified snapshot"):
            graph.build_sampler_snapshot(tmp_path / "snapshot", read_snapshot=True)
    finally:
        graph.close()


def test_pyrex_read_snapshot_binds_one_transaction_and_is_read_only():
    class Options:
        def __init__(self):
            self.set_snapshot = False

    class Iterator:
        def __init__(self, values):
            self.values = sorted(values.items())
            self.index = len(self.values)

        def seek(self, key):
            self.index = next(
                (index for index, (candidate, _) in enumerate(self.values) if candidate >= key),
                len(self.values),
            )

        def valid(self):
            return self.index < len(self.values)

        def key(self):
            return self.values[self.index][0]

        def value(self):
            return self.values[self.index][1]

        def next(self):
            self.index += 1

        def check_status(self):
            pass

    class Transaction:
        def __init__(self):
            self.is_active = True
            self.snapshot_sequence_number = 17
            self.values = {b"N\x1fn1": b"node", b"N\x1fn2": b"other"}

        def get(self, key):
            return self.values.get(key)

        def new_iterator(self):
            return Iterator(self.values)

        def rollback(self):
            self.is_active = False

    class Database:
        def __init__(self):
            self.options = None
            self.transaction = None

        def begin_transaction(self, write_options, options):
            self.options = options
            self.transaction = Transaction()
            return self.transaction

    store = object.__new__(PyRexStore)
    store.transactional = True
    store._pyrex = type("PyRex", (), {"TransactionOptions": Options})
    store.db = Database()
    store.write_options = object()

    with store.read_snapshot() as snapshot:
        assert store.db.options.set_snapshot is True
        assert snapshot.snapshot_identity.sequence == 17
        assert snapshot.snapshot_identity.verifiable is True
        assert snapshot.get_node(b"n1") == b"node"
        assert list(snapshot.get_node_keys_generator()) == [b"n1", b"n2"]
        with pytest.raises(RuntimeError, match="read-only"):
            snapshot.put_node(b"n3", b"payload")

    assert store.db.transaction.is_active is False


def test_snapshot_provenance_survives_clean_source_reopen(tmp_path):
    graph = _lmdb_graph(tmp_path)
    graph.put_node(Node("n1", properties={"kind": "node"}))
    snapshot = graph.build_sampler_snapshot(tmp_path / "snapshot")
    graph.close()

    source = snapshot.open_source_graph()
    try:
        assert snapshot.get_node(source, 0).get_id == "n1"
    finally:
        source.close()


def test_mutable_provenance_rejects_divergent_database_copy_at_same_sequence(tmp_path):
    base = _lmdb_graph(tmp_path)
    base.put_node(Node("n1", properties={"value": "base"}))
    base_path = base._store_path
    base.close()

    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    shutil.copytree(base_path, first_path)
    shutil.copytree(base_path, second_path)
    first = GraphDB.open(first_path)
    second = GraphDB.open(second_path)
    try:
        first.put_node(Node("n1", properties={"value": "first"}))
        second.put_node(Node("n1", properties={"value": "second"}))
        snapshot = first.build_sampler_snapshot(tmp_path / "copied-source-snapshot")

        with first.current_read_view(require_verifiable=True) as first_view:
            first_sequence = first_view.provenance.snapshot_sequence
        with second.current_read_view(require_verifiable=True) as second_view:
            assert second_view.provenance.snapshot_sequence == first_sequence
            assert second_view.provenance.state_sha256 != first_view.provenance.state_sha256

        with pytest.raises(ProvenanceMismatchError, match="identity mismatch"):
            snapshot.verify_source(second)
    finally:
        first.close()
        second.close()


def test_read_snapshot_option_rejects_cypher_writes(tmp_path):
    graph = _lmdb_graph(tmp_path)
    try:
        with pytest.raises(ValueError, match="cannot be used with Cypher writes"):
            graph.query('CREATE (n:Person {id: "n1"}) RETURN n.id', read_snapshot=True)
    finally:
        graph.close()


def test_show_catalog_snapshot_request_fails_closed_on_leveldb(tmp_path):
    pytest.importorskip("plyvel")
    path = tmp_path / "leveldb-show"
    graph = GraphDB(LevelDBStore(path=str(path)), JSONSerializer())
    try:
        graph.save_manifest(path)
        with pytest.raises(NotImplementedError, match="cannot provide a unified snapshot"):
            graph.query("SHOW INDEXES", read_snapshot=True)
    finally:
        graph.close()
