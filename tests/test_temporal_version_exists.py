"""Duplicate version IDs stay rejected without O(history) scans per write."""

import pytest

from gestaltdb.epistemic import Claim
from gestaltdb.graphdb import GraphDB, IndexMaintenanceMode
from gestaltdb.versioning import ClaimVersionWrite, TemporalVersionError


def _claims(start, count):
    return [
        ClaimVersionWrite.assertion(
            Claim(f"n{index}", "memberOf", f"group{index % 8}", "positive", "registry",
                  world="default"),
            (0, 100),
        )
        for index in range(start, start + count)
    ]


def _descriptor_reads(graph, func):
    # Count commit-descriptor key constructions: the commit path runs on a
    # transaction-scoped engine for transactional backends, so counting must
    # happen on the engine class rather than on one store instance.
    from gestaltdb.temporal_engine import TemporalEngine as E

    reads = 0
    original = E.__dict__["commit_key"]

    @staticmethod
    def counting(commit_id):
        nonlocal reads
        reads += 1
        return original.__func__(commit_id)

    E.commit_key = counting
    try:
        func()
    finally:
        E.commit_key = original
    return reads


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_bulk_commit_avoids_history_scan_per_write(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        graph.temporal.commit_versions(_claims(0, 150))
        graph.temporal.commit_versions(_claims(150, 150))

        def second_bulk():
            graph.temporal.commit_versions(_claims(300, 150))

        # The commit path still performs O(1) descriptor reads per commit
        # (sequence reservation plus post-write validation). What must not
        # happen is a per-version history scan: pre-fix this count grew with
        # history (150 reads here) instead of staying constant.
        assert _descriptor_reads(graph, second_bulk) <= 4
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
            assert len(list(view.iter_claims_as_of(world="default"))) == 450
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_duplicate_version_id_rejected_in_maintain_mode(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        first = graph.temporal.commit_versions(_claims(0, 3))
        duplicate = ClaimVersionWrite.assertion(
            Claim("other", "memberOf", "group0", "positive", "registry", world="default"),
            (0, 100),
            version_id=first.versions[0].version_id,
        )
        with pytest.raises(TemporalVersionError, match="already exists"):
            graph.temporal.commit_versions([duplicate])
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_duplicate_version_id_rejected_with_deferred_indexes(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        first = graph.temporal.commit_versions(_claims(0, 3), index_mode=IndexMaintenanceMode.DEFER)
        duplicate = ClaimVersionWrite.assertion(
            Claim("other", "memberOf", "group0", "positive", "registry", world="default"),
            (0, 100),
            version_id=first.versions[0].version_id,
        )
        with pytest.raises(TemporalVersionError, match="already exists"):
            graph.temporal.commit_versions([duplicate], index_mode=IndexMaintenanceMode.DEFER)
    finally:
        graph.close()
