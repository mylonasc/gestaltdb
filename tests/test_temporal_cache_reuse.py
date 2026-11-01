"""Bounded commit-cache reuse with explicit identity and invalidation (HSA-03)."""

import copy

import pytest

from gestaltdb._graph import temporal_reads as _reads
from gestaltdb._graph.scopes import configure_snapshot_child
from gestaltdb.graphdb import GraphDB
from gestaltdb._graph.entities import Node
from gestaltdb.versioning import NodeVersionWrite, VersionOperation


BACKENDS = [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")]


def _make(tmp_path, backend):
    return GraphDB.create(tmp_path / backend, backend=backend, serializer="json")


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_snapshot_child_gets_fresh_cache_and_parent_stays_warm(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = _make(tmp_path, backend)
    try:
        graph.temporal.put_node_version(Node("n1"), valid=(0, None))
        assert graph.temporal.get_commit_cached(1) is not None
        parent_entries = len(graph.temporal._commit_cache)
        assert parent_entries > 0
        child = copy.copy(graph)
        # Simulate a snapshot fork bound to the same store object; the helper
        # must give the child a fresh dict, never the live parent's dict.
        configure_snapshot_child(graph, child)
        assert child.temporal._commit_cache is not graph.temporal._commit_cache
        assert len(child.temporal._commit_cache) == 0
        # Parent cache intact.
        assert len(graph.temporal._commit_cache) == parent_entries
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_invalidate_for_store_and_clear(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = _make(tmp_path, backend)
    try:
        graph.temporal.put_node_version(Node("n1"), valid=(0, None))
        assert graph.temporal.get_commit_cached(1) is not None
        assert len(graph.temporal._commit_cache) > 0
        graph.temporal.invalidate_commit_cache_for_store(graph.store)
        assert len(graph.temporal._commit_cache) == 0
        # Re-read repopulates without backend corruption.
        assert graph.temporal.get_commit_cached(1) is not None
        graph.temporal.invalidate_commit_cache()
        assert len(graph.temporal._commit_cache) == 0
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_byte_and_entry_bounds_include_incoming_commit(tmp_path, backend, module, monkeypatch):
    pytest.importorskip(module)
    graph = _make(tmp_path, backend)
    try:
        for i in range(5):
            graph.temporal.put_node_version(Node(f"n{i}"), valid=(0, None))
        for commit_id in range(1, 6):
            assert graph.temporal.get_commit_cached(commit_id) is not None
        cache = graph.temporal._commit_cache
        assert len(cache) <= 256
        assert _reads._commit_cache_bytes(cache) > 0
        one_entry = next(iter(cache.values()))
        one_size = len(one_entry[1]) + sum(one_entry[2])
        cache.clear()
        monkeypatch.setattr(_reads, "_COMMIT_CACHE_MAX_ENTRIES", 2)
        monkeypatch.setattr(_reads, "_COMMIT_CACHE_MAX_BYTES", one_size * 2)
        for commit_id in range(1, 6):
            assert graph.temporal.get_commit_cached(commit_id) is not None
            assert len(cache) <= 2
            assert _reads._commit_cache_bytes(cache) <= one_size * 2
        # Admission of one oversized commit must not break the bound or
        # discard other useful entries. Reading still succeeds uncached.
        monkeypatch.setattr(_reads, "_COMMIT_CACHE_MAX_BYTES", 1)
        cache.clear()
        assert graph.temporal.get_commit_cached(1) is not None
        assert cache == {}
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_warm_cache_tamper_is_detected(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = _make(tmp_path, backend)
    try:
        from gestaltdb.versioning import TemporalCorruptionError
        graph.temporal.put_node_version(Node("n1"), valid=(0, None))
        assert graph.temporal.get_commit_cached(1, _validate_supersession=False) is not None
        reader = graph.temporal.reader()
        from gestaltdb._graph.temporal_keys import temporal_record_key
        # Tamper the live record after warming the cache.
        original = graph.store.get_metadata(temporal_record_key(1, 0))
        assert original is not None
        graph.store.put_metadata(temporal_record_key(1, 0), b"tampered")
        try:
            with pytest.raises(TemporalCorruptionError):
                reader.get_commit_cached(1, _validate_supersession=False)
            assert not graph.temporal._commit_cache
        finally:
            graph.store.put_metadata(temporal_record_key(1, 0), original)
        # After repair, the cached entry validates again.
        assert reader.get_commit_cached(1, _validate_supersession=False) is not None
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_historical_commit_survives_late_append(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = _make(tmp_path, backend)
    try:
        first = graph.temporal.put_node_version(Node("n1"), valid=(0, None))
        warmed = graph.temporal.get_commit_cached(1)
        assert warmed is not None
        # Late append: correction superseding the warmed assertion.
        second = graph.temporal.commit_versions([
            NodeVersionWrite(VersionOperation.CORRECT, "n1", (0, None),
                             node=Node("n1"),
                             supersedes_version_id=first.version_id)])
        assert second.versions[0].supersedes_version_id == first.version_id
        # Old commit still validates and is identical (immutable history).
        again = graph.temporal.get_commit_cached(1)
        assert again is not None
        assert again.versions[0].version_id == first.version_id
        # New commit validates with its supersession link intact.
        assert graph.temporal.get_commit_cached(2) is not None
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_store_setter_and_close_retire_admission(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = _make(tmp_path, backend)
    try:
        graph.temporal.put_node_version(Node("n1"), valid=(0, None))
        assert graph.temporal.get_commit_cached(1) is not None
        old_store = graph.store
        assert len(graph.temporal._commit_cache) > 0
        old_cache = graph.temporal._commit_cache
        second = GraphDB.create(tmp_path / "second", backend=backend, serializer="json")
        try:
            version = second.temporal.put_node_version(Node("other"), valid=(0, None))
            graph.store = second.store
            assert not old_cache
            assert graph.temporal._commit_cache is not old_cache
            commit = graph.temporal.get_commit_cached(1)
            assert commit.versions[0].version_id == version.version_id
            new_cache = graph.temporal._commit_cache
            graph.store = old_store
            assert not new_cache
        finally:
            second.close()
        graph.temporal.get_commit_cached(1)
        assert graph.temporal._commit_cache
    finally:
        graph.close()
    assert not graph.temporal._commit_cache


def test_real_snapshot_pins_old_versions_and_retires_cache(tmp_path):
    pytest.importorskip("lmdb")
    graph = _make(tmp_path, "lmdb")
    try:
        first = graph.temporal.put_node_version(Node("n1"), valid=(0, None))
        graph.temporal.get_commit_cached(1)
        parent_cache = graph.temporal._commit_cache
        with graph.current_read_view(require_verifiable=True) as view:
            scoped = view._graph.temporal
            cache = scoped._commit_cache
            assert cache is not parent_cache and not cache
            assert scoped.get_commit_cached(1).versions[0].version_id == first.version_id
            graph.temporal.commit_versions([
                NodeVersionWrite(VersionOperation.CORRECT, "n1", (0, None),
                                 node=Node("n1"), supersedes_version_id=first.version_id)])
            assert scoped.get_commit_cached(2) is None
            assert scoped.get_commit_cached(1).versions[0].version_id == first.version_id
            assert parent_cache
        assert not cache
        assert graph.temporal.get_commit_cached(2) is not None
    finally:
        graph.close()


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_interrupted_reader_clears_admission(tmp_path, monkeypatch, failure):
    pytest.importorskip("lmdb")
    graph = _make(tmp_path, "lmdb")
    try:
        graph.temporal.put_node_version(Node("n"), valid=(0, None))
        graph.temporal.get_commit_cached(1)
        assert graph.temporal._commit_cache
        def fail(*args):
            raise failure("interrupted")
        monkeypatch.setattr(graph.store, "get_metadata", fail)
        with pytest.raises(failure):
            graph.temporal.get_commit_cached(1)
        assert not graph.temporal._commit_cache
    finally:
        graph.close()


@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt])
def test_transaction_exit_retires_admission(tmp_path, failure):
    pytest.importorskip("lmdb")
    graph = _make(tmp_path, "lmdb")
    try:
        graph.temporal.put_node_version(Node("n"), valid=(0, None))
        def run():
            with graph.transaction() as tx:
                tx.temporal.get_commit_cached(1)
                retained.append(tx.temporal._commit_cache)
                assert retained[0]
                if failure is not None:
                    raise failure("interrupted")
        retained = []
        if failure is None:
            run()
        else:
            with pytest.raises(failure):
                run()
        assert retained == [{}]
    finally:
        graph.close()


def test_decoder_rebinding_cannot_reuse_old_decode(tmp_path, monkeypatch):
    pytest.importorskip("lmdb")
    from gestaltdb._graph.entities import GraphEntityDictSerializer
    from gestaltdb.serializers import JSONSerializer

    graph = _make(tmp_path, "lmdb")
    try:
        graph.temporal.put_node_version(Node("n"), valid=(0, None))
        reader = graph.temporal.reader()
        first = reader.get_commit_cached(1)
        reader.entity_serializer = GraphEntityDictSerializer(JSONSerializer())
        decoded = []
        original = reader.decode_version
        def decode(payload):
            decoded.append(payload)
            return original(payload)
        monkeypatch.setattr(reader, "decode_version", decode)
        assert reader.get_commit_cached(1).versions[0].version_id == first.versions[0].version_id
        assert len(decoded) == 1
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_warm_supersession_reuses_decodes_but_detects_target_tamper(
    tmp_path, backend, module, monkeypatch,
):
    pytest.importorskip(module)
    from gestaltdb._graph.temporal_keys import temporal_record_key
    from gestaltdb.versioning import TemporalCorruptionError

    graph = _make(tmp_path, backend)
    try:
        first = graph.temporal.put_node_version(Node("n"), valid=(0, None))
        graph.temporal.commit_versions([
            NodeVersionWrite(VersionOperation.CORRECT, "n", (0, None),
                             node=Node("n"), supersedes_version_id=first.version_id)])
        graph.temporal.get_commit_cached(2)
        calls = []
        original = _reads.TemporalReader.decode_version
        def decode(reader, payload):
            calls.append(payload)
            return original(reader, payload)
        monkeypatch.setattr(_reads.TemporalReader, "decode_version", decode)
        graph.temporal.get_commit_cached(2)
        assert calls == []
        key = temporal_record_key(1, 0)
        envelope = graph.store.get_metadata(key)
        graph.store.put_metadata(key, b"tampered target")
        try:
            with pytest.raises(TemporalCorruptionError):
                graph.temporal.get_commit_cached(2)
            assert not graph.temporal._commit_cache
        finally:
            graph.store.put_metadata(key, envelope)
    finally:
        graph.close()


def test_close_failure_still_clears_cache(tmp_path, monkeypatch):
    pytest.importorskip("lmdb")
    graph = _make(tmp_path, "lmdb")
    try:
        graph.temporal.put_node_version(Node("n"), valid=(0, None))
        graph.temporal.get_commit_cached(1)
        with monkeypatch.context() as patch:
            def fail():
                raise RuntimeError("close failed")
            patch.setattr(graph.store, "close", fail)
            with pytest.raises(RuntimeError, match="close failed"):
                graph.close()
            assert not graph.temporal._commit_cache
    finally:
        graph.close()
