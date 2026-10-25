"""#314: grouped reads must bound work without trusting mutable commit caches."""

import hashlib
import json
import random
import copy

import pytest

from gestaltdb.epistemic import Claim
from gestaltdb.graphdb import GraphDB
from gestaltdb.versioning import ClaimVersionWrite, TemporalCorruptionError


@pytest.fixture(params=["lmdb", "leveldb", "pyrex"])
def backend(request):
    pytest.importorskip({"lmdb": "lmdb", "leveldb": "plyvel", "pyrex": "pyrex"}[request.param])
    return request.param


def writes(count):
    return [ClaimVersionWrite.assertion(Claim(f"n{i}", "p", "o", "positive", "registry"), (0, 10))
            for i in range(count)]


@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("commit_batch", [8, 48])
def test_loaded_and_reopened_claim_reads_are_linear_in_touched_envelopes(
    backend, tmp_path, monkeypatch, reopen, commit_batch,
):
    graph = GraphDB.create(tmp_path / "db", backend=backend)
    try:
        pending = writes(48)
        for start in range(0, 48, commit_batch):
            graph.commit_versions(pending[start:start + commit_batch])
        expected = {version.version_id for version in graph.iter_claims_as_of(valid_time=5)}
        if reopen:
            graph.close()
            graph = GraphDB.open(tmp_path / "db")
        original = graph.store.get_metadata
        prefix = graph._temporal_record_key(1, 0)[:-12]
        reads = 0

        def counted(key):
            nonlocal reads
            reads += key.startswith(prefix)
            return original(key)

        monkeypatch.setattr(graph.store, "get_metadata", counted)
        actual = list(graph.iter_claims_as_of(valid_time=5))
        assert {version.version_id for version in actual} == expected
        # First admission + two grouped passes + final live-store validation.
        # This is a deterministic I/O-work bound, never a timing assertion.
        assert reads <= 8 * 48
    finally:
        graph.close()


@pytest.mark.parametrize("damage", ["envelope", "marker", "descriptor"])
def test_unrelated_corrupt_row_in_a_touched_commit_is_not_hidden(backend, tmp_path, damage):
    graph = GraphDB.create(tmp_path / "db", backend=backend)
    try:
        graph.commit_versions(writes(4))
        graph.assert_claim(subject="later", predicate="p", object="o", polarity="positive",
                           agent="registry", valid=(0, 10))
        assert list(graph.iter_claims_as_of(valid_time=5, subject="n0"))
        key = {"envelope": graph._temporal_record_key(1, 3),
               "marker": graph._temporal_visible_key(1),
               "descriptor": graph._temporal_commit_key(1)}[damage]
        graph.store.put_metadata(key, b"corrupt")
        with pytest.raises(TemporalCorruptionError):
            list(graph.iter_claims_as_of(valid_time=5, subject="n0"))
    finally:
        graph.close()


@pytest.mark.parametrize("damage", ["envelope", "missing_marker", "rewritten_descriptor"])
def test_live_changes_during_iteration_prevent_successful_exhaustion(backend, tmp_path, damage):
    graph = GraphDB.create(tmp_path / "db", backend=backend)
    try:
        graph.commit_versions(writes(4))
        iterator = graph.iter_claims_as_of(valid_time=5)
        next(iterator)
        if damage == "envelope":
            graph.store.put_metadata(graph._temporal_record_key(1, 3), b"corrupt")
        elif damage == "missing_marker":
            graph.store.delete_metadata(graph._temporal_visible_key(1))
        else:
            key = graph._temporal_commit_key(1)
            descriptor = json.loads(graph.store.get_metadata(key))
            descriptor["metadata"] = {"changed": True}
            encoded = json.dumps(descriptor).encode()
            graph.store.put_metadata(key, encoded)
            graph.store.put_metadata(graph._temporal_visible_key(1), hashlib.sha256(encoded).digest())
        with pytest.raises(TemporalCorruptionError):
            list(iterator)
    finally:
        graph.close()


def test_appending_after_first_yield_keeps_the_captured_horizon(backend, tmp_path):
    graph = GraphDB.create(tmp_path / "db", backend=backend)
    try:
        graph.commit_versions(writes(4))
        expected = list(graph.iter_claims_as_of(valid_time=5))
        iterator = graph.iter_claims_as_of(valid_time=5)
        first = next(iterator)
        graph.assert_claim(subject="new", predicate="p", object="o", polarity="positive",
                           agent="registry", valid=(0, 10))
        assert [first, *iterator] == expected
        assert len(list(graph.iter_claims_as_of(valid_time=5))) == 5
    finally:
        graph.close()


def test_grouped_history_matches_point_resolution_and_filters(backend, tmp_path):
    graph = GraphDB.create(tmp_path / "db", backend=backend)
    try:
        claims = [Claim(f"n{i}", f"p{i % 2}", "o", "positive" if i % 2 else "negative",
                        f"agent{i % 2}", source=None if i % 2 else "trusted", world=f"w{i % 2}")
                  for i in range(8)]
        initial = graph.commit_versions([ClaimVersionWrite.assertion(claim, (0, 10)) for claim in claims])
        order = list(range(8))
        random.Random(314).shuffle(order)
        for i in order[:4]:
            graph.correct_claim(claims[i], supersedes_version_id=initial.versions[i].version_id,
                                confidence=.5, valid=(2, 8))
        for i in order[2:6]:
            graph.retract_claim(claims[i].claim_id, valid=(4, 7))
        graph.commit_versions([ClaimVersionWrite.assertion(claims[order[2]], (5, 6))])
        for horizon in (1, 4, 7, 10):
            for instant in (0, 3, 5, 6, 9, 10):
                point = [graph.get_claim_as_of(claim.claim_id, valid_time=instant, through_commit=horizon)
                         for claim in claims]
                expected = sorted((v for v in point if v is not None), key=lambda v: v.logical_id)
                assert list(graph.iter_claims_as_of(valid_time=instant, through_commit=horizon)) == expected
                for filters in ({"world": "w0"}, {"predicate": "p1"}, {"source": None},
                                {"subject": "n0", "polarity": "negative"}):
                    selected = [v for v in expected
                                if all(getattr(v.claim, key) == value for key, value in filters.items())]
                    assert list(graph.iter_claims_as_of(valid_time=instant, through_commit=horizon,
                                                        **filters)) == selected
    finally:
        graph.close()


def test_store_rebinding_cannot_finish_a_grouped_read(backend, tmp_path):
    graph = GraphDB.create(tmp_path / "db", backend=backend)
    other = GraphDB.create(tmp_path / "other", backend=backend)
    store = graph.store
    try:
        graph.commit_versions(writes(4))
        iterator = graph.iter_claims_as_of(valid_time=5)
        next(iterator)
        graph.store = other.store
        with pytest.raises(TemporalCorruptionError, match="store changed"):
            list(iterator)
    finally:
        graph.store = store
        graph.close()
        other.close()


def test_grouped_snapshot_reads_remain_isolated_from_live_corruption(tmp_path):
    pytest.importorskip("lmdb")
    from gestaltdb._graph.scopes import configure_snapshot_child

    graph = GraphDB.create(tmp_path / "db", backend="lmdb")
    try:
        graph.commit_versions(writes(4))
        with graph.store.read_snapshot() as store:
            pinned = copy.copy(graph)
            pinned.store = store
            configure_snapshot_child(graph, pinned)
            expected = list(pinned.iter_claims_as_of(valid_time=5))
            iterator = pinned.iter_claims_as_of(valid_time=5)
            first = next(iterator)
            graph.store.put_metadata(graph._temporal_record_key(1, 3), b"corrupt")
            assert [first, *iterator] == expected
            with pytest.raises(TemporalCorruptionError):
                list(graph.iter_claims_as_of(valid_time=5))
    finally:
        graph.close()
