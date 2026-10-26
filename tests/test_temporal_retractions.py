import pytest

from gestaltdb import Claim
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.temporal_engine import TemporalEngine
from gestaltdb.versioning import (
    ClaimVersionWrite,
    EdgeVersionWrite,
    NodeVersionWrite,
    TemporalVersionError,
)


@pytest.fixture(params=["node", "edge", "claim"])
def temporal_entity(request):
    kind = request.param
    if kind == "node":
        return NodeVersionWrite, Node("identity"), "identity", ("temporal", "retract_node_version")
    if kind == "edge":
        return EdgeVersionWrite, Edge("identity", "a", "b"), "identity", ("temporal", "retract_edge_version")
    claim = Claim("a", "P", "b", "positive", "agent")
    return ClaimVersionWrite, claim, claim.claim_id, ("epistemic", "retract_claim")


def _retract(graph_db, retractor, *args, **kwargs):
    """Resolve a retraction through its owning engine, not the facade."""
    service, method = retractor
    return getattr(getattr(graph_db, service), method)(*args, **kwargs)


@pytest.mark.parametrize("interval", [{"valid": (0, 100)}, {"valid_from": 0}])
def test_unknown_retraction_rejected_before_sequence_allocation(graph_db, temporal_entity, interval):
    _, _, logical_id, retractor = temporal_entity
    before = graph_db.temporal.sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        _retract(graph_db, retractor, logical_id, **interval)
    assert graph_db.temporal.sequence() == before
    assert list(graph_db.temporal.iter_temporal_commits()) == []


def test_unknown_retraction_rejects_entire_batch(graph_db, temporal_entity):
    write_type, _, logical_id, _ = temporal_entity
    before = graph_db.temporal.sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        graph_db.temporal.commit_versions([
            NodeVersionWrite.assertion(Node("unrelated"), valid=(0, 100)),
            write_type.retraction(logical_id, valid=(0, 100)),
        ])
    assert graph_db.temporal.sequence() == before
    assert list(graph_db.temporal.iter_temporal_commits()) == []


def test_known_identity_retraction_works_with_deferred_indexes(graph_db, temporal_entity):
    write_type, entity, logical_id, retractor = temporal_entity
    asserted = graph_db.temporal.commit_versions([
        write_type.assertion(entity, valid=(0, 100)),
    ], index_mode="defer").versions[0]
    first = _retract(graph_db, retractor, logical_id, valid=(20, 40), index_mode="defer")
    # Repeated withdrawals, superseding a withdrawal, and disjoint validity
    # remain supported once an identity has been established.
    second = _retract(graph_db, retractor,
        logical_id, supersedes_version_id=first.version_id, index_mode="defer",
    )
    third = _retract(graph_db, retractor, logical_id, valid=(200, 300), index_mode="defer")
    graph_db.temporal.rebuild_temporal_indexes()
    assert [v.version_id for c in graph_db.temporal.iter_temporal_commits() for v in c.versions] == [
        asserted.version_id, first.version_id, second.version_id, third.version_id,
    ]


def test_same_batch_assertion_can_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, _ = temporal_entity
    commit = graph_db.temporal.commit_versions([
        write_type.assertion(entity, valid=(0, 100)),
        write_type.retraction(logical_id, valid=(20, 40)),
    ])
    assert len(commit.versions) == 2
    assert commit.versions[0].logical_id == commit.versions[1].logical_id


def test_later_batch_assertion_cannot_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, _ = temporal_entity
    before = graph_db.temporal.sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        graph_db.temporal.commit_versions([
            write_type.retraction(logical_id, valid=(20, 40)),
            write_type.assertion(entity, valid=(0, 100)),
        ])
    assert graph_db.temporal.sequence() == before


def test_other_kind_does_not_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, retractor = temporal_entity
    other = (
        EdgeVersionWrite.assertion(Edge(logical_id, "a", "b"), valid=(0, 100))
        if isinstance(entity, Node)
        else NodeVersionWrite.assertion(Node(logical_id), valid=(0, 100))
    )
    graph_db.temporal.commit_versions([other])
    before = graph_db.temporal.sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        _retract(graph_db, retractor, logical_id, valid=(0, 100))
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        graph_db.temporal.commit_versions([other, write_type.retraction(logical_id, valid=(0, 100))])
    assert graph_db.temporal.sequence() == before


def test_unpublished_assertion_does_not_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, retractor = temporal_entity
    commit = graph_db.temporal.commit_versions([write_type.assertion(entity, valid=(0, 100))])
    graph_db.store.delete_metadata(graph_db.temporal.visible_key(commit.commit_id))
    before = graph_db.temporal.sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        _retract(graph_db, retractor, logical_id, valid=(0, 100))
    assert graph_db.temporal.sequence() == before


def test_legacy_orphan_retraction_cannot_establish_identity(graph_db, temporal_entity, monkeypatch):
    _, _, logical_id, retractor = temporal_entity
    # Simulate a retraction-only identity written by releases before this fix.
    # Patched on the engine class so transaction-scoped child engines inherit it.
    with monkeypatch.context() as patch:
        patch.setattr(TemporalEngine, "require_retraction_identities", lambda self, ids: None)
        orphan = _retract(graph_db, retractor, logical_id, valid=(0, 100))
    before = graph_db.temporal.sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        _retract(graph_db, retractor, logical_id, supersedes_version_id=orphan.version_id)
    assert graph_db.temporal.sequence() == before
