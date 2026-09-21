import datetime

import pytest

from gestaltdb import RuleEvaluationLimitError
from gestaltdb.graphdb import GraphDB
from gestaltdb.rules import RuleError
from gestaltdb.serializers import JSONSerializer, MessagePackSerializer, PickleSerializer, ProtobufSerializer
from tests.test_temporal_as_of import _MetadataStore


@pytest.fixture
def graph():
    return GraphDB(_MetadataStore(), JSONSerializer())


def _claim(graph, subject, predicate, object, *, agent="source", valid=(0, 100), world="default"):
    return graph.assert_claim(
        subject=subject,
        predicate=predicate,
        object=object,
        polarity="positive",
        agent=agent,
        valid=valid,
        world=world,
    )


def test_unsafe_rules_are_rejected_before_catalog_persistence(graph):
    with pytest.raises(RuleError, match="unsafe"):
        graph.create_rule(
            "unsafe",
            when=[("?person", "WORKS_FOR", "?company")],
            then=("?person", "AFFILIATED_WITH", "?group"),
        )

    with pytest.raises(RuleError, match="predicates"):
        graph.create_rule(
            "variable-predicate",
            when=[("?subject", "?predicate", "?object")],
            then=("?subject", "COPY", "?object"),
        )

    assert list(graph.iter_rule_versions()) == []


def test_rule_catalog_is_immutable_versioned_and_system_time_selectable(graph):
    times = iter([
        datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
        datetime.datetime(2025, 1, 2, tzinfo=datetime.timezone.utc),
        datetime.datetime(2025, 1, 3, tzinfo=datetime.timezone.utc),
    ])
    graph._temporal_clock = lambda: next(times)
    first = graph.create_rule(
        "affiliation",
        when=[("?p", "WORKS_FOR", "?c")],
        then=("?p", "AFFILIATED_WITH", "?c"),
    )
    second = graph.create_rule(
        "affiliation",
        when=[("?p", "CONTRACTS_FOR", "?c")],
        then=("?p", "AFFILIATED_WITH", "?c"),
    )

    assert first.version_id != second.version_id
    assert graph.get_rule("affiliation").version_id == second.version_id
    assert graph.get_rule("affiliation", system_time=first.system_time).version_id == first.version_id
    assert [item.version_id for item in graph.iter_rule_versions("affiliation")] == [
        first.version_id,
        second.version_id,
    ]
    with pytest.raises(RuleError, match="already exists"):
        graph.create_rule(
            "duplicate-version",
            when=[("?p", "WORKS_FOR", "?c")],
            then=("?p", "AFFILIATED_WITH", "?c"),
            version_id=first.version_id,
        )
    assert len(tuple(graph.iter_rule_versions())) == 2


def test_rules_intersect_validity_and_deduplicate_conclusions_with_all_justifications(graph):
    employment = _claim(graph, "alice", "WORKS_FOR", "acme", valid=(0, 100))
    first_membership = _claim(
        graph, "acme", "MEMBER_OF", "industry", agent="registry-a", valid=(20, 80)
    )
    second_membership = _claim(
        graph, "acme", "MEMBER_OF", "industry", agent="registry-b", valid=(20, 80)
    )
    rule = graph.create_rule(
        "employment-implies-affiliation",
        when=[("?p", "WORKS_FOR", "?c"), ("?c", "MEMBER_OF", "?g")],
        then=("?p", "AFFILIATED_WITH", "?g"),
    )

    result = graph.run_rules(as_of=50)

    assert result.derived_count == 1
    assert result.commit_id is not None
    derived = result.versions[0]
    assert derived.valid.start.epoch_microseconds == 20
    assert derived.valid.end.epoch_microseconds == 80
    assert derived.claim.agent == "gestaltdb:rules"
    justifications = derived.claim.to_dict()["provenance"]["justifications"]
    assert {item["rule_version_id"] for item in justifications} == {rule.version_id}
    assert {tuple(item["premise_version_ids"]) for item in justifications} == {
        (employment.version_id, first_membership.version_id),
        (employment.version_id, second_membership.version_id),
    }
    assert [item.logical_id for item in graph.iter_claims_as_of(
        valid_time=50, predicate="AFFILIATED_WITH"
    )] == [derived.logical_id]

    repeated = graph.run_rules(as_of=50)
    assert repeated.derived_count == 0
    assert repeated.commit_id is None


def test_recursive_rules_reach_a_finite_semi_naive_fixpoint(graph):
    _claim(graph, "a", "PARENT_OF", "b")
    _claim(graph, "b", "PARENT_OF", "c")
    graph.create_rule(
        "parent-is-ancestor",
        when=[("?a", "PARENT_OF", "?b")],
        then=("?a", "ANCESTOR_OF", "?b"),
    )
    graph.create_rule(
        "ancestor-transitivity",
        when=[("?a", "ANCESTOR_OF", "?b"), ("?b", "PARENT_OF", "?c")],
        then=("?a", "ANCESTOR_OF", "?c"),
    )

    result = graph.run_rules(as_of=50, max_iterations=10)

    assert result.iterations == 3
    assert result.derived_count == 3
    assert {
        (version.claim.subject, version.claim.object)
        for version in graph.iter_claims_as_of(valid_time=50, predicate="ANCESTOR_OF")
    } == {("a", "b"), ("b", "c"), ("a", "c")}


def test_equivalent_temporal_conclusions_union_overlapping_support_intersections(graph):
    _claim(graph, "alice", "WORKS_FOR", "acme", valid=(-10, None))
    _claim(graph, "acme", "MEMBER_OF", "industry", agent="broad", valid=(0, 100))
    _claim(graph, "acme", "MEMBER_OF", "industry", agent="narrow", valid=(20, 80))
    graph.create_rule(
        "affiliation",
        when=[("?p", "WORKS_FOR", "?c"), ("?c", "MEMBER_OF", "?g")],
        then=("?p", "AFFILIATED_WITH", "?g"),
    )

    result = graph.run_rules(as_of=50)

    assert result.derived_count == 1
    assert result.versions[0].valid.start.epoch_microseconds == 0
    assert result.versions[0].valid.end.epoch_microseconds == 100
    assert len(result.versions[0].claim.provenance["justifications"]) == 2
    assert graph.get_claim_as_of(
        result.versions[0].logical_id, valid_time=99
    ).version_id == result.versions[0].version_id
    assert graph.get_claim_as_of(result.versions[0].logical_id, valid_time=100) is None


def test_rule_limits_fail_without_persisting_partial_derivations(graph):
    _claim(graph, "a", "PARENT_OF", "b")
    _claim(graph, "b", "PARENT_OF", "c")
    graph.create_rule(
        "parent-is-ancestor",
        when=[("?a", "PARENT_OF", "?b")],
        then=("?a", "ANCESTOR_OF", "?b"),
    )
    graph.create_rule(
        "ancestor-transitivity",
        when=[("?a", "ANCESTOR_OF", "?b"), ("?b", "PARENT_OF", "?c")],
        then=("?a", "ANCESTOR_OF", "?c"),
    )
    before, _ = graph._temporal_sequence()

    with pytest.raises(RuleEvaluationLimitError, match="max_iterations"):
        graph.run_rules(as_of=50, max_iterations=1)

    after, _ = graph._temporal_sequence()
    assert after == before
    assert list(graph.iter_claims_as_of(valid_time=50, predicate="ANCESTOR_OF")) == []


@pytest.mark.parametrize(
    "serializer",
    [PickleSerializer(), JSONSerializer(), MessagePackSerializer(), ProtobufSerializer()],
    ids=["pickle", "json", "messagepack", "protobuf"],
)
def test_rules_and_justifications_are_serializer_neutral(serializer):
    graph = GraphDB(_MetadataStore(), serializer)
    premise = _claim(graph, "a", "P", "b")
    rule = graph.create_rule("copy", when=[("?a", "P", "?b")], then=("?a", "Q", "?b"))

    derived = graph.run_rules(as_of=1).versions[0]
    loaded = graph.get_claim_version(derived.version_id)

    assert loaded.claim.to_dict() == derived.claim.to_dict()
    assert loaded.claim.to_dict()["provenance"]["justifications"] == [{
        "premise_version_ids": [premise.version_id],
        "rule_version_id": rule.version_id,
    }]


def test_rule_catalog_and_evaluation_work_on_available_backends(graph_db):
    _claim(graph_db, "a", "P", "b")
    rule = graph_db.create_rule(
        "copy", when=[("?a", "P", "?b")], then=("?a", "Q", "?b")
    )

    result = graph_db.run_rules(as_of=1)

    assert graph_db.get_rule("copy").version_id == rule.version_id
    assert [(item.claim.subject, item.claim.object) for item in graph_db.iter_claims_as_of(
        valid_time=1, predicate="Q"
    )] == [("a", "b")]
    assert result.derived_count == 1
