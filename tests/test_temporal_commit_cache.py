"""Bulk temporal commits must not make version reads O(commit size)."""

import pytest

from gestaltdb.epistemic import Claim
from gestaltdb.graphdb import GraphDB
from gestaltdb.temporal_engine import TemporalEngine
from gestaltdb.versioning import ClaimVersionWrite


def _bulk_graph(tmp_path, backend, module, rows):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    writes = [
        ClaimVersionWrite.assertion(
            Claim(f"n{index}", "memberOf", f"group{index % 8}", "positive", "registry",
                  world="default"),
            (0, 100),
        )
        for index in range(rows)
    ]
    graph.temporal.commit_versions(writes)
    return graph


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_bulk_commit_iteration_matches_per_claim_results(tmp_path, backend, module):
    pytest.importorskip(module)
    bulk = _bulk_graph(tmp_path / "bulk", backend, module, 50)
    single = GraphDB.create(tmp_path / "single", backend=backend, serializer="json")
    try:
        for index in range(50):
            single.epistemic.assert_claim(subject=f"n{index}", predicate="memberOf", object=f"group{index % 8}",
                                agent="registry", polarity="positive", valid=(0, 100))
        with bulk.temporal.open_read_view(bulk, manifest=bulk.manifest, database_id=bulk.database_id, valid_time=50) as view:
            bulk_ids = sorted(version.logical_id for version in view.iter_claims_as_of(world="default"))
        with single.temporal.open_read_view(single, manifest=single.manifest, database_id=single.database_id, valid_time=50) as view:
            single_ids = sorted(version.logical_id for version in view.iter_claims_as_of(world="default"))
        assert bulk_ids == single_ids
        assert len(bulk_ids) == 50
    finally:
        bulk.close()
        single.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_bulk_commit_decodes_each_version_once_per_read(tmp_path, backend, module):
    rows = 100
    graph = _bulk_graph(tmp_path, backend, module, rows)
    try:
        calls = 0
        original = TemporalEngine.decode_version

        def counting(self, envelope):
            nonlocal calls
            calls += 1
            return original(self, envelope)

        graph.temporal.decode_version = counting.__get__(graph.temporal, TemporalEngine)
        try:
            with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
                assert len(list(view.iter_claims_as_of(world="default"))) == rows
        finally:
            del graph.temporal.decode_version
        # One full commit decode plus small constant overhead; pre-fix this
        # was ~600 decodes per version (60,000+ here).
        assert calls <= 3 * rows
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_new_commits_stay_visible_with_commit_cache(tmp_path, backend, module):
    graph = _bulk_graph(tmp_path, backend, module, 10)
    try:
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
            assert len(list(view.iter_claims_as_of(world="default"))) == 10
        graph.epistemic.assert_claim(subject="extra", predicate="memberOf", object="group0",
                           agent="registry", polarity="positive", valid=(0, 100))
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
            found = sorted(version.logical_id for version in view.iter_claims_as_of(world="default"))
        assert len(found) == 11
    finally:
        graph.close()
