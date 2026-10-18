import pytest

from gestaltdb import Claim
from gestaltdb.graphdb import Edge, GraphDB, Node
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
        return NodeVersionWrite, Node("identity"), "identity", "retract_node_version"
    if kind == "edge":
        return EdgeVersionWrite, Edge("identity", "a", "b"), "identity", "retract_edge_version"
    claim = Claim("a", "P", "b", "positive", "agent")
    return ClaimVersionWrite, claim, claim.claim_id, "retract_claim"


@pytest.mark.parametrize("interval", [{"valid": (0, 100)}, {"valid_from": 0}])
def test_unknown_retraction_rejected_before_sequence_allocation(graph_db, temporal_entity, interval):
    _, _, logical_id, retractor = temporal_entity
    before = graph_db._temporal_sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        getattr(graph_db, retractor)(logical_id, **interval)
    assert graph_db._temporal_sequence() == before
    assert list(graph_db.iter_temporal_commits()) == []


def test_unknown_retraction_rejects_entire_batch(graph_db, temporal_entity):
    write_type, _, logical_id, _ = temporal_entity
    before = graph_db._temporal_sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        graph_db.commit_versions([
            NodeVersionWrite.assertion(Node("unrelated"), valid=(0, 100)),
            write_type.retraction(logical_id, valid=(0, 100)),
        ])
    assert graph_db._temporal_sequence() == before
    assert list(graph_db.iter_temporal_commits()) == []


def test_known_identity_retraction_works_with_deferred_indexes(graph_db, temporal_entity):
    write_type, entity, logical_id, retractor = temporal_entity
    asserted = graph_db.commit_versions([
        write_type.assertion(entity, valid=(0, 100)),
    ], index_mode="defer").versions[0]
    first = getattr(graph_db, retractor)(logical_id, valid=(20, 40), index_mode="defer")
    # Repeated withdrawals, superseding a withdrawal, and disjoint validity
    # remain supported once an identity has been established.
    second = getattr(graph_db, retractor)(
        logical_id, supersedes_version_id=first.version_id, index_mode="defer",
    )
    third = getattr(graph_db, retractor)(logical_id, valid=(200, 300), index_mode="defer")
    graph_db.rebuild_temporal_indexes()
    assert [v.version_id for c in graph_db.iter_temporal_commits() for v in c.versions] == [
        asserted.version_id, first.version_id, second.version_id, third.version_id,
    ]


def test_same_batch_assertion_can_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, _ = temporal_entity
    commit = graph_db.commit_versions([
        write_type.assertion(entity, valid=(0, 100)),
        write_type.retraction(logical_id, valid=(20, 40)),
    ])
    assert len(commit.versions) == 2
    assert commit.versions[0].logical_id == commit.versions[1].logical_id


def test_later_batch_assertion_cannot_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, _ = temporal_entity
    before = graph_db._temporal_sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        graph_db.commit_versions([
            write_type.retraction(logical_id, valid=(20, 40)),
            write_type.assertion(entity, valid=(0, 100)),
        ])
    assert graph_db._temporal_sequence() == before


def test_other_kind_does_not_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, retractor = temporal_entity
    other = (
        EdgeVersionWrite.assertion(Edge(logical_id, "a", "b"), valid=(0, 100))
        if isinstance(entity, Node)
        else NodeVersionWrite.assertion(Node(logical_id), valid=(0, 100))
    )
    graph_db.commit_versions([other])
    before = graph_db._temporal_sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        getattr(graph_db, retractor)(logical_id, valid=(0, 100))
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        graph_db.commit_versions([other, write_type.retraction(logical_id, valid=(0, 100))])
    assert graph_db._temporal_sequence() == before


def test_unpublished_assertion_does_not_establish_identity(graph_db, temporal_entity):
    write_type, entity, logical_id, retractor = temporal_entity
    commit = graph_db.commit_versions([write_type.assertion(entity, valid=(0, 100))])
    graph_db.store.delete_metadata(graph_db._temporal_visible_key(commit.commit_id))
    before = graph_db._temporal_sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        getattr(graph_db, retractor)(logical_id, valid=(0, 100))
    assert graph_db._temporal_sequence() == before


def test_legacy_orphan_retraction_cannot_establish_identity(graph_db, temporal_entity, monkeypatch):
    _, _, logical_id, retractor = temporal_entity
    # Simulate a retraction-only identity written by releases before this fix.
    with monkeypatch.context() as patch:
        patch.setattr(GraphDB, "_require_temporal_retraction_identities", lambda self, ids: None)
        orphan = getattr(graph_db, retractor)(logical_id, valid=(0, 100))
    before = graph_db._temporal_sequence()
    with pytest.raises(TemporalVersionError, match="cannot retract nonexistent"):
        getattr(graph_db, retractor)(logical_id, supersedes_version_id=orphan.version_id)
    assert graph_db._temporal_sequence() == before
