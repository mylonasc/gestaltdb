import datetime

import pytest

from gestaltdb import (
    ClaimStatus,
    ModalError,
    ModalEvaluationLimitError,
    ModalExpression,
    ModalOperator,
)
from gestaltdb.graphdb import GraphDB
from gestaltdb.serializers import JSONSerializer
from tests.test_temporal_as_of import _MetadataStore


@pytest.fixture
def graph():
    value = GraphDB(_MetadataStore(), JSONSerializer())
    value._backend_name = "leveldb"
    value._serializer_name = "json"
    value._database_id = "00000000-0000-0000-0000-000000000013"
    return value


def _claim(graph, *, world, polarity="positive", confidence=None, valid=(0, 100)):
    return graph.assert_claim(
        subject="bob",
        predicate="LOCATED_IN",
        object="paris",
        polarity=polarity,
        agent=f"source:{polarity}",
        confidence=confidence,
        world=world,
        valid=valid,
    )


def _access(graph, kind, target, *, agent="alice", from_world="actual", valid=(0, 100)):
    return graph.assert_world_accessibility(
        agent=agent,
        from_world=from_world,
        to_world=target,
        kind=kind,
        valid=valid,
    )


PROPOSITION = {"subject": "bob", "predicate": "LOCATED_IN", "object": "paris"}


@pytest.mark.parametrize(
    ("polarities", "expected"),
    [
        (("positive",), ClaimStatus.SUPPORTED),
        (("negative",), ClaimStatus.REFUTED),
        (("positive", "negative"), ClaimStatus.BOTH),
        ((), ClaimStatus.UNKNOWN),
    ],
)
def test_belief_returns_all_four_values_with_temporal_evidence(graph, polarities, expected):
    _access(graph, "belief", "believed")
    for polarity in polarities:
        _claim(graph, world="believed", polarity=polarity, confidence=0.8)

    result = graph.entails(
        "alice", PROPOSITION, "BELIEVES", world="actual", valid_time=50
    )

    assert result.status is expected
    assert result.confidence == (None if expected is ClaimStatus.UNKNOWN else 0.8)
    assert result.explanation["throughCommit"] == len(polarities) + 1
    assert result.explanation["evaluation"]["accessibility"][0]["validFrom"] == 0
    evidence = result.explanation["evaluation"]["children"][0]["evidence"]
    assert [item["polarity"] for item in evidence] == sorted(polarities)


def test_knowledge_and_belief_use_distinct_agent_frames(graph):
    _access(graph, "belief", "rumor")
    _access(graph, "knowledge", "verified")
    _claim(graph, world="rumor", polarity="positive")
    _claim(graph, world="verified", polarity="negative")

    belief = graph.entails("alice", PROPOSITION, "BELIEVES", world="actual", valid_time=10)
    knowledge = graph.entails("alice", PROPOSITION, "KNOWS", world="actual", valid_time=10)
    other_agent = graph.entails("charlie", PROPOSITION, "BELIEVES", world="actual", valid_time=10)

    assert belief.status is ClaimStatus.SUPPORTED
    assert knowledge.status is ClaimStatus.REFUTED
    assert other_agent.status is ClaimStatus.UNKNOWN


def test_possible_and_necessary_apply_paraconsistent_world_aggregation(graph):
    _access(graph, "modal", "one")
    _access(graph, "modal", "two")
    _claim(graph, world="one", polarity="positive", confidence=0.9)
    _claim(graph, world="two", polarity="negative", confidence=0.7)

    possible = graph.entails("alice", PROPOSITION, "POSSIBLE", world="actual", valid_time=10)
    necessary = graph.entails("alice", PROPOSITION, "NECESSARY", world="actual", valid_time=10)

    assert possible.status is ClaimStatus.SUPPORTED
    assert possible.confidence == 0.9
    assert necessary.status is ClaimStatus.REFUTED
    assert necessary.confidence == 0.7


def test_nested_connectives_do_not_explode_and_explanations_are_deterministic(graph):
    _access(graph, "belief", "rumor", agent="alice")
    _access(graph, "knowledge", "reported", agent="bob", from_world="rumor")
    _claim(graph, world="reported", polarity="positive")
    _claim(graph, world="reported", polarity="negative")
    nested = {
        "operator": "KNOWS",
        "agent": "bob",
        "formula": {
            "operator": "AND",
            "operands": [PROPOSITION, {"operator": "NOT", "formula": PROPOSITION}],
        },
    }

    first = graph.entails("alice", nested, "BELIEVES", world="actual", valid_time=10)
    second = graph.entails("alice", nested, "BELIEVES", world="actual", valid_time=10)
    unrelated = graph.entails(
        "alice",
        {"subject": "bob", "predicate": "IS_MAYOR_OF", "object": "paris"},
        "BELIEVES",
        world="actual",
        valid_time=10,
    )

    assert first.status is ClaimStatus.BOTH
    assert unrelated.status is ClaimStatus.UNKNOWN
    assert first.explanation == second.explanation


def test_temporal_and_system_horizons_pin_accessibility_and_claims(graph):
    times = iter([
        datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        datetime.datetime(2025, 1, 2, tzinfo=datetime.timezone.utc),
        datetime.datetime(2025, 1, 3, tzinfo=datetime.timezone.utc),
    ])
    graph._temporal_clock = lambda: next(times)
    access = _access(graph, "belief", "rumor", valid=(0, 20))
    claim = _claim(graph, world="rumor", valid=(0, 20))
    graph.retract_claim(
        claim.logical_id,
        supersedes_version_id=claim.version_id,
        valid=(0, 20),
    )

    historical = graph.entails(
        "alice", PROPOSITION, "BELIEVES", world="actual", valid_time=10,
        through_commit=claim.commit_id,
    )
    current = graph.entails("alice", PROPOSITION, "BELIEVES", world="actual", valid_time=10)
    known_at_claim = graph.entails(
        "alice", PROPOSITION, "BELIEVES", world="actual", valid_time=10,
        system_time=claim.system_time,
    )
    expired = graph.entails(
        "alice", PROPOSITION, "BELIEVES", world="actual", valid_time=20,
        through_commit=claim.commit_id,
    )

    assert historical.status is ClaimStatus.SUPPORTED
    assert historical.explanation["throughCommit"] == claim.commit_id
    assert current.status is ClaimStatus.UNKNOWN
    assert known_at_claim.status is ClaimStatus.SUPPORTED
    assert expired.status is ClaimStatus.UNKNOWN
    assert access.system_time < claim.system_time


def test_modal_facts_support_pre_epoch_and_open_validity(graph):
    _access(graph, "knowledge", "verified", valid=(-10, None))
    _claim(graph, world="verified", valid=(-10, None))

    before = graph.entails("alice", PROPOSITION, "KNOWS", world="actual", valid_time=-11)
    at_start = graph.entails("alice", PROPOSITION, "KNOWS", world="actual", valid_time=-10)
    later = graph.entails("alice", PROPOSITION, "KNOWS", world="actual", valid_time=10_000)

    assert before.status is ClaimStatus.UNKNOWN
    assert at_start.status is ClaimStatus.SUPPORTED
    assert later.status is ClaimStatus.SUPPORTED


def test_formula_validation_and_resource_limits_fail_closed(graph):
    _access(graph, "belief", "rumor")
    _claim(graph, world="rumor")

    with pytest.raises(ModalEvaluationLimitError, match="max_depth"):
        graph.entails(
            "alice",
            {"operator": "BELIEVES", "agent": "alice", "formula": PROPOSITION},
            "BELIEVES", world="actual", valid_time=10, max_depth=1,
        )
    with pytest.raises(ModalEvaluationLimitError, match="max_states"):
        graph.entails(
            "alice", PROPOSITION, "BELIEVES", world="actual", valid_time=10,
            max_states=1,
        )
    with pytest.raises(ModalError, match="unsupported fields"):
        graph.entails(
            "alice", {**PROPOSITION, "unsafe": True}, "BELIEVES",
            world="actual", valid_time=10,
        )
    cyclic = {"operator": "NOT"}
    cyclic["formula"] = cyclic
    with pytest.raises(ModalError, match="finite"):
        graph.entails("alice", cyclic, "BELIEVES", world="actual", valid_time=10)
    with pytest.raises(ModalError, match="at least two"):
        ModalExpression(ModalOperator.AND, operands=(ModalExpression.from_value(PROPOSITION),))


def test_explanation_preserves_rule_derivation_provenance(graph):
    _access(graph, "knowledge", "verified")
    graph.assert_claim(
        subject="bob", predicate="VISITS", object="paris", polarity="positive",
        agent="source", world="verified", valid=(0, 100),
    )
    graph.create_rule(
        "visits-implies-location",
        when=[("?person", "VISITS", "?place")],
        then=("?person", "LOCATED_IN", "?place"),
    )
    graph.run_rules(as_of=10, world="verified")

    result = graph.entails("alice", PROPOSITION, "KNOWS", world="actual", valid_time=10)
    evidence = result.explanation["evaluation"]["children"][0]["evidence"]

    assert result.status is ClaimStatus.SUPPORTED
    assert evidence[0]["provenance"]["derived_by"] == "gestaltdb.rules"
    assert evidence[0]["provenance"]["justifications"]


def test_cypher_kg_entails_is_allowlisted_and_returns_contracted_fields(graph):
    _access(graph, "knowledge", "verified")
    _claim(graph, world="verified", confidence=0.95)

    result = graph.query(
        "CALL kg.entails($agent, $claim, $mode, $options) "
        "YIELD status, confidence, explanation RETURN status, confidence, explanation",
        parameters={
            "agent": "alice",
            "claim": PROPOSITION,
            "mode": "KNOWS",
            "options": {"world": "actual", "validTime": 10, "maxDepth": 4},
        },
    )

    assert result.columns == ("status", "confidence", "explanation")
    assert result.records[0]["status"] == "supported"
    assert result.records[0]["confidence"] == 0.95
    assert result.records[0]["explanation"]["throughCommit"] == 2


def test_cypher_kg_entails_validates_yields_arguments_and_options(graph):
    with pytest.raises(ValueError, match="does not yield field"):
        graph.query(
            "CALL kg.entails('a', $p, 'KNOWS', $o) YIELD path RETURN path",
            parameters={"p": PROPOSITION, "o": {"world": "w", "validTime": 0}},
        )
    with pytest.raises(ValueError, match="expects agent"):
        graph.query("CALL kg.entails('a') YIELD status RETURN status")
    with pytest.raises(ValueError, match="alias more than once"):
        graph.query(
            "CALL kg.entails('a', $p, 'KNOWS', $o) "
            "YIELD status AS value, confidence AS value RETURN value",
            parameters={"p": PROPOSITION, "o": {"world": "w", "validTime": 0}},
        )
    with pytest.raises(ValueError, match="require world and validTime"):
        graph.query(
            "CALL kg.entails('a', $p, 'KNOWS', {}) YIELD status RETURN status",
            parameters={"p": PROPOSITION},
        )
