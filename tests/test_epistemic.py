from dataclasses import FrozenInstanceError

import pytest

from gestaltdb import Claim, ClaimObjectKind, ClaimPolarity, ClaimStatus
from gestaltdb.epistemic import claim_statement_id
from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.serializers import (
    JSONSerializer,
    MessagePackSerializer,
    PickleSerializer,
    ProtobufSerializer,
)
from gestaltdb.versioning import TemporalVersionError
from tests.test_temporal_as_of import _MetadataStore


@pytest.fixture
def graph():
    return GraphDB(_MetadataStore(), JSONSerializer())


def _assert_employment(graph, **kwargs):
    values = {
        "subject": "alice",
        "predicate": "WORKS_FOR",
        "object": "acme",
        "polarity": "positive",
        "agent": "source:hr-feed",
        "source": "hr-feed.csv",
        "confidence": 0.97,
        "world": "reported",
        "valid": (0, 100),
    }
    values.update(kwargs)
    return graph.assert_claim(**values)


def test_claim_identity_is_deterministic_and_payload_is_immutable():
    first = Claim(
        "alice",
        "AGE",
        42,
        "positive",
        "analyst",
        object_kind="literal",
        provenance={"sources": ["registry"], "line": 7},
    )
    second = Claim(
        "alice",
        "AGE",
        42,
        ClaimPolarity.POSITIVE,
        "analyst",
        object_kind=ClaimObjectKind.LITERAL,
        provenance={"line": 7, "sources": ["registry"]},
    )

    assert first.statement_id == second.statement_id == claim_statement_id(
        "alice", "AGE", 42, object_kind="literal"
    )
    assert first.claim_id == second.claim_id
    assert first.to_dict()["object"] == 42
    with pytest.raises(TypeError):
        first.provenance["line"] = 8
    with pytest.raises(FrozenInstanceError):
        first.confidence = 0.5


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), True, "high"])
def test_claim_confidence_validation_is_independent_of_polarity(confidence):
    with pytest.raises(TemporalVersionError, match="confidence"):
        Claim("alice", "P", "bob", "negative", "agent", confidence=confidence)

    denial = Claim("alice", "P", "bob", "negative", "agent", confidence=1.0)
    weak_support = Claim("alice", "P", "bob", "positive", "agent", confidence=0.0)
    assert denial.polarity is ClaimPolarity.NEGATIVE
    assert weak_support.polarity is ClaimPolarity.POSITIVE


def test_contradictory_sources_coexist_and_produce_four_valued_status(graph):
    positive = _assert_employment(graph)
    negative = _assert_employment(
        graph,
        polarity="negative",
        agent="source:investigator",
        source="interview-4",
        confidence=0.55,
    )

    assert positive.claim.statement_id == negative.claim.statement_id
    assert positive.logical_id != negative.logical_id
    assert graph.claim_status(
        "alice", "WORKS_FOR", "acme", valid_time=50, world="reported"
    ) is ClaimStatus.BOTH
    assert graph.claim_status(
        "alice", "WORKS_FOR", "acme", valid_time=50, source="hr-feed.csv"
    ) is ClaimStatus.SUPPORTED
    assert graph.claim_status(
        "alice", "WORKS_FOR", "other", valid_time=50
    ) is ClaimStatus.UNKNOWN
    assert [item.version_id for item in graph.iter_claims_as_of(
        valid_time=50, polarity="negative", agent="source:investigator"
    )] == [negative.version_id]


def test_claim_boundaries_corrections_retractions_and_system_time(graph):
    asserted = _assert_employment(graph, confidence=0.4, valid=(10, 30))
    corrected = graph.correct_claim(
        asserted.logical_id,
        supersedes_version_id=asserted.version_id,
        confidence=0.9,
        provenance={"reviewed": True},
        valid=(15, 25),
    )
    retracted = graph.retract_claim(
        asserted.logical_id,
        supersedes_version_id=corrected.version_id,
        valid=(20, 22),
        reason="withdrawn",
    )

    assert graph.get_claim_as_of(asserted.logical_id, valid_time=9) is None
    assert graph.get_claim_as_of(asserted.logical_id, valid_time=10).version_id == asserted.version_id
    assert graph.get_claim_as_of(asserted.logical_id, valid_time=16).claim.confidence == 0.9
    assert graph.get_claim_as_of(asserted.logical_id, valid_time=20) is None
    assert graph.get_claim_as_of(asserted.logical_id, valid_time=22).version_id == corrected.version_id
    assert graph.get_claim_as_of(asserted.logical_id, valid_time=25).version_id == asserted.version_id
    assert graph.get_claim_as_of(asserted.logical_id, valid_time=30) is None
    assert graph.get_claim_as_of(
        asserted.logical_id, valid_time=20, through_commit=corrected.commit_id
    ).version_id == corrected.version_id
    assert graph.get_claim_as_of(
        asserted.logical_id, valid_time=16, system_time=asserted.system_time
    ).version_id == asserted.version_id
    assert retracted.reason == "withdrawn"
    assert [version.version_id for version in graph.iter_claim_versions(asserted.logical_id)] == [
        asserted.version_id,
        corrected.version_id,
        retracted.version_id,
    ]


def test_claims_support_pre_epoch_and_reject_cross_kind_corrections(graph):
    claim = _assert_employment(graph, valid=(-10, 0))
    node = graph.put_node_version(Node(claim.logical_id), valid=(-10, 0))

    assert graph.get_claim_as_of(claim.logical_id, valid_time=-10).version_id == claim.version_id
    assert graph.get_claim_as_of(claim.logical_id, valid_time=0) is None
    with pytest.raises(TemporalVersionError, match="claim version"):
        graph.correct_claim(
            claim.logical_id,
            supersedes_version_id=node.version_id,
            confidence=0.5,
        )


def test_read_view_pins_claim_lookup_and_status(graph):
    graph._backend_name = "leveldb"
    graph._serializer_name = "json"
    graph._database_id = "00000000-0000-0000-0000-000000000001"
    positive = _assert_employment(graph, valid=(0, None))
    with graph.read_view(valid_time=10) as view:
        graph.retract_claim(
            positive.logical_id,
            supersedes_version_id=positive.version_id,
            valid=(0, None),
        )
        assert view.get_claim_as_of(positive.logical_id).version_id == positive.version_id
        assert view.claim_status("alice", "WORKS_FOR", "acme") is ClaimStatus.SUPPORTED
    assert graph.get_claim_as_of(positive.logical_id, valid_time=10) is None


def test_deferred_claim_indexes_fail_closed_and_rebuild_deterministically(graph):
    version = _assert_employment(graph, index_mode="defer")
    with pytest.raises(RuntimeError, match="temporal"):
        list(graph.iter_claims_as_of(valid_time=1))

    counts = graph.rebuild_temporal_indexes()

    assert counts["temporal_exact"] == 1
    assert graph.get_claim_as_of(version.logical_id, valid_time=1).version_id == version.version_id
    assert [item.logical_id for item in graph.iter_claims_as_of(
        valid_time=1, source="hr-feed.csv", world="reported"
    )] == [version.logical_id]
    assert graph.rebuild_temporal_indexes() == counts


@pytest.mark.parametrize(
    "serializer",
    [PickleSerializer(), JSONSerializer(), MessagePackSerializer(), ProtobufSerializer()],
    ids=["pickle", "json", "messagepack", "protobuf"],
)
def test_claim_payload_is_serializer_neutral(serializer):
    graph = GraphDB(_MetadataStore(), serializer)
    version = _assert_employment(
        graph,
        object={"amount": 7, "unit": "years"},
        object_kind="literal",
        provenance={"record": [1, 2]},
    )

    loaded = graph.get_claim_version(version.version_id)

    assert loaded.claim.to_dict() == version.claim.to_dict()
    assert loaded.payload_hash == version.payload_hash


def test_claim_indexes_work_on_available_backends(graph_db):
    version = _assert_employment(graph_db, valid=(0, None))

    loaded = list(graph_db.iter_claims_as_of(
        valid_time=0,
        statement_id=version.claim.statement_id,
        source="hr-feed.csv",
        agent="source:hr-feed",
        world="reported",
        polarity="positive",
    ))

    assert [item.version_id for item in loaded] == [version.version_id]
