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
    # transaction store handle for transactional backends, so counting must
    # happen at the GraphDB layer rather than on one store instance.
    from gestaltdb.graphdb import GraphDB as G

    reads = 0
    original = G.__dict__["_temporal_commit_key"]

    @staticmethod
    def counting(commit_id):
        nonlocal reads
        reads += 1
        return original.__func__(commit_id)

    G._temporal_commit_key = counting
    try:
        func()
    finally:
        G._temporal_commit_key = original
    return reads


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_bulk_commit_avoids_history_scan_per_write(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        graph.commit_versions(_claims(0, 150))
        graph.commit_versions(_claims(150, 150))

        def second_bulk():
            graph.commit_versions(_claims(300, 150))

        # The commit path still performs O(1) descriptor reads per commit
        # (sequence reservation plus post-write validation). What must not
        # happen is a per-version history scan: pre-fix this count grew with
        # history (150 reads here) instead of staying constant.
        assert _descriptor_reads(graph, second_bulk) <= 4
        with graph.read_view(valid_time=50) as view:
            assert len(list(view.iter_claims_as_of(world="default"))) == 450
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_duplicate_version_id_rejected_in_maintain_mode(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        first = graph.commit_versions(_claims(0, 3))
        duplicate = ClaimVersionWrite.assertion(
            Claim("other", "memberOf", "group0", "positive", "registry", world="default"),
            (0, 100),
            version_id=first.versions[0].version_id,
        )
        with pytest.raises(TemporalVersionError, match="already exists"):
            graph.commit_versions([duplicate])
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_duplicate_version_id_rejected_with_deferred_indexes(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        first = graph.commit_versions(_claims(0, 3), index_mode=IndexMaintenanceMode.DEFER)
        duplicate = ClaimVersionWrite.assertion(
            Claim("other", "memberOf", "group0", "positive", "registry", world="default"),
            (0, 100),
            version_id=first.versions[0].version_id,
        )
        with pytest.raises(TemporalVersionError, match="already exists"):
            graph.commit_versions([duplicate], index_mode=IndexMaintenanceMode.DEFER)
    finally:
        graph.close()
