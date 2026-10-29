"""Verifiable snapshots on transactional PyRex (requires pyrex-rocksdb>=0.7.0)."""

import copy
import shutil

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import (
    PyRexReadSnapshotStore,
    PyRexStore,
)
from gestaltdb.readview import CurrentReadProvenance, ProvenanceMismatchError
from gestaltdb.serializers import JSONSerializer


def _pyrex_has_verifiable_apis(pyrex) -> bool:
    return hasattr(getattr(pyrex, "Transaction", None), "snapshot_sequence_number") and hasattr(
        getattr(pyrex, "TransactionDB", None), "latest_sequence_number"
    )


def _require_pyrex_070():
    pyrex = pytest.importorskip("pyrex")
    try:
        from importlib.metadata import version
    except ImportError:  # pragma: no cover - Python >=3.8 always has this
        version = None
    if version is not None:
        parsed = tuple(int(part) for part in version("pyrex-rocksdb").split(".")[:3])
        if parsed < (0, 7, 0):
            pytest.skip("requires pyrex-rocksdb>=0.7.0")
    if not _pyrex_has_verifiable_apis(pyrex):
        pytest.skip("installed PyRex binding lacks snapshot-sequence APIs")
    return pyrex


def _pyrex_graph(tmp_path):
    _require_pyrex_070()
    path = tmp_path / "pyrex"
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = GraphDB(PyRexStore(path=str(path), transactional=True), JSONSerializer())
    graph.save_manifest(path)
    return graph


def _populate_snapshot_graph(graph):
    graph.create_node_property_index("kind")
    with graph.transaction() as tx:
        tx.put_node(Node("n1", labels=["Old"], properties={"kind": "old", "value": 1}))
        tx.put_node(Node("n2"))
        tx.put_edge(Edge("e1", "n1", "n2", {"type": "OLD"}))


def test_pyrex_reports_verifiable_runtime_support(tmp_path):
    _require_pyrex_070()
    path = tmp_path / "pyrex"
    store = PyRexStore(path=str(path), transactional=True)
    try:
        assert store.supports_read_snapshots is True
        assert store.supports_verifiable_read_snapshots is True
    finally:
        store.close()


def test_pyrex_current_read_view_pins_entities_indexes_and_adjacency(tmp_path):
    graph = _pyrex_graph(tmp_path)
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


def test_pyrex_provenance_round_trip_and_source_advance(tmp_path):
    graph = _pyrex_graph(tmp_path)
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


def test_pyrex_mutable_provenance_rejects_divergent_copy_at_same_sequence(tmp_path):
    base = _pyrex_graph(tmp_path / "base")
    try:
        base.put_node(Node("n1", properties={"value": "base"}))
    finally:
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


def test_pyrex_cached_verification_does_not_rescan(tmp_path, monkeypatch):
    graph = _pyrex_graph(tmp_path)
    try:
        graph.put_node(Node("n1"))
        with graph.current_read_view(require_verifiable=True) as view:
            provenance = view.provenance
        scans = []
        original = PyRexReadSnapshotStore.state_sha256

        def counted(store):
            scans.append(1)
            return original(store)

        monkeypatch.setattr(PyRexReadSnapshotStore, "state_sha256", counted)
        provenance.verify_source(graph)
        assert len(scans) == 1
        for _ in range(10):
            provenance.verify_source(graph)
        assert len(scans) == 1
        with graph.current_read_view(capture_provenance=False):
            pass
        graph.query("MATCH (n) RETURN n")
        assert len(scans) == 1
    finally:
        graph.close()


def test_pyrex_required_verifiable_sampler_build(tmp_path):
    graph = _pyrex_graph(tmp_path)
    try:
        _populate_snapshot_graph(graph)
        snapshot = graph.build_sampler_snapshot(tmp_path / "snapshot", read_snapshot=True)
        assert snapshot.metadata["source_provenance"]["backend"]["snapshot_kind"] == "mutable_backend_snapshot"
        snapshot.verify_source(graph)
        snapshot.verify_source(graph)
        graph.put_node(Node("n3"))
        with pytest.raises(ProvenanceMismatchError, match="identity mismatch"):
            snapshot.verify_source(graph)
    finally:
        graph.close()
