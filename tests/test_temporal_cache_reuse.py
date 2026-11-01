"""Bounded commit-cache reuse with explicit identity and invalidation (HSA-03)."""

import pytest

from gestaltdb._graph import temporal_reads as _reads
from gestaltdb.graphdb import GraphDB
from gestaltdb._graph.entities import Node
from gestaltdb.versioning import NodeVersionWrite, VersionOperation


BACKENDS = [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")]


def _make(tmp_path, backend):
    return GraphDB.create(tmp_path / backend, backend=backend, serializer="json")


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_snapshot_child_gets_fresh_cache_and_parent_stays_warm(tmp_path, backend, module):
    pytest.importorskip(module)
    import copy
    from gestaltdb._graph.scopes import configure_snapshot_child
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
def test_byte_and_entry_bounds_evict_oldest_half(tmp_path, backend, module):
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
        # Fill the live dict with fake entries, then force a real miss that
        # must evict the oldest half before inserting.
        real_commit = graph.temporal.get_commit_cached(1, _validate_supersession=False)
        assert real_commit is not None
        for i in range(300):
            cache[(id(graph.store), 10000 + i, False)] = (
                graph.store, b"d", (b"e",), real_commit,
            )
        assert len(cache) > 256
        graph.temporal.put_node_version(Node("evict"), valid=(0, None))
        # The write uses a transaction-scoped cache on some backends; force
        # the eviction on the live dict with a real miss.
        assert graph.temporal.get_commit_cached(6) is not None
        assert len(graph.temporal._commit_cache) <= 256
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", BACKENDS)
def test_warm_cache_tamper_is_detected(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = _make(tmp_path, backend)
    try:
        from gestaltdb.versioning import TemporalCorruptionError
        graph.temporal.put_node_version(Node("n1"), valid=(0, None))
        assert graph.temporal.get_commit_cached(1) is not None
        reader = graph.temporal.reader()
        from gestaltdb._graph.temporal_keys import temporal_record_key
        # Tamper the live record after warming the cache.
        original = graph.store.get_metadata(temporal_record_key(1, 0))
        assert original is not None
        graph.store.put_metadata(temporal_record_key(1, 0), b"tampered")
        try:
            with pytest.raises(TemporalCorruptionError):
                reader.get_commit_cached(1, _validate_supersession=False)
        finally:
            graph.store.put_metadata(temporal_record_key(1, 0), original)
        # After repair, the cached entry validates again.
        assert reader.get_commit_cached(1, _validate_supersession=False) is not None
    finally:
        graph.close()
