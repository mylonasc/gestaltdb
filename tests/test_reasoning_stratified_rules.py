"""GraphDB stratified negation over explicit closed-world claim scopes."""

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.inference import explain_inference
from gestaltdb.rules import RuleAbsence, RuleError
from gestaltdb.serializers import JSONSerializer
from tests.test_temporal_as_of import _MetadataStore


@pytest.fixture(params=["memory", "lmdb"])
def graph(request, tmp_path):
    if request.param == "lmdb":
        pytest.importorskip("lmdb")
        db = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    else:
        db = GraphDB(_MetadataStore(), JSONSerializer())
    try:
        yield db
    finally:
        db.close()


def claim(graph, subject, predicate, object="yes", *, world="default", valid=(0, None)):
    return graph.assert_claim(
        subject=subject,
        predicate=predicate,
        object=object,
        polarity="positive",
        agent="source",
        world=world,
        valid=valid,
    )


def compliance_rule(graph):
    return graph.create_rule(
        "missing-certification",
        when=[("?s", "SHIPS", "?goods")],
        then=("?s", "REQUIRES_SAFETY_REVIEW", "yes"),
        not_when=[("?s", "CERTIFIED", "iso9001")],
        closed_world=["CERTIFIED"],
    )


def flags(graph, *, world=None):
    return list(graph.iter_claims_as_of(
        valid_time=10, predicate="REQUIRES_SAFETY_REVIEW", world=world
    ))


def test_negation_requires_closed_scope_and_bound_variables(graph):
    with pytest.raises(RuleError, match="closed-world"):
        graph.create_rule(
            "undeclared",
            when=[("?s", "SHIPS", "?g")],
            then=("?s", "REVIEW", "yes"),
            not_when=[("?s", "CERTIFIED", "iso9001")],
        )
    with pytest.raises(RuleError, match="unsafe negated"):
        graph.create_rule(
            "unbound",
            when=[("?s", "SHIPS", "?g")],
            then=("?s", "REVIEW", "yes"),
            not_when=[("?missing", "CERTIFIED", "iso9001")],
            closed_world=["CERTIFIED"],
        )
    with pytest.raises(RuleError, match="closed-world scope"):
        graph.create_rule(
            "string-scope",
            when=[("?s", "SHIPS", "?g")],
            then=("?s", "REVIEW", "yes"),
            not_when=[("?s", "CERTIFIED", "iso9001")],
            closed_world="CERTIFIED",
        )
    assert graph._load_rule_catalog() == ()


def test_negative_cycle_is_rejected_before_catalog_activation(graph):
    graph.create_rule(
        "a-rule",
        when=[("?x", "BASE", "?y")],
        then=("?x", "A", "?y"),
        not_when=[("?x", "B", "?y")],
        closed_world=["B"],
    )
    before = graph._load_rule_catalog()
    with pytest.raises(RuleError, match="negative dependency cycle"):
        graph.create_rule(
            "b-rule", when=[("?x", "A", "?y")], then=("?x", "B", "?y")
        )
    assert graph._load_rule_catalog() == before


def test_compliance_flag_and_truth_maintenance_retraction(graph):
    claim(graph, "supplier-1", "SHIPS", "widgets")
    compliance_rule(graph)
    first = graph.run_rules(as_of=10)
    assert first.derived_count == 1
    derived = flags(graph)
    assert [item.claim.subject for item in derived] == ["supplier-1"]

    certification = claim(graph, "supplier-1", "CERTIFIED", "iso9001")
    retracted = graph.maintain_truth(as_of=10)
    assert retracted.retracted_count == 1
    assert flags(graph) == []

    graph.retract_claim(
        certification.logical_id, valid=certification.valid, reason="certificate withdrawn"
    )
    restored = graph.maintain_truth(as_of=10)
    assert restored.asserted_count == 1
    assert [item.claim.subject for item in flags(graph)] == ["supplier-1"]


def test_absence_evidence_is_recorded_in_explanations_and_audit(graph):
    claim(graph, "supplier-1", "SHIPS", "widgets")
    rule = compliance_rule(graph)
    graph.run_rules(as_of=10)
    derived = flags(graph)[0]

    explanation = graph.explain_claim(derived.logical_id, valid_time=10)
    absences = [node for node in explanation.nodes if node.kind == "absence"]
    assert len(absences) == 1
    evidence = absences[0].value
    assert isinstance(evidence, RuleAbsence)
    assert evidence.position == 0
    assert evidence.predicate == "CERTIFIED"
    assert evidence.subject == "supplier-1"
    assert evidence.object == "iso9001"
    assert evidence.scope == ("CERTIFIED",)
    absent_edges = [
        edge for edge in explanation.edges if edge.relationship == "absent_premise"
    ]
    assert len(absent_edges) == 1
    assert absent_edges[0].source_id == rule.version_id
    assert absent_edges[0].target_id == absences[0].node_id
    assert absent_edges[0].premise_ordinal == 0

    report = explain_inference(graph, derived.logical_id, valid_time=10)
    value = report.to_dict()
    assert not value["truncated"]
    absence_nodes = [node for node in value["nodes"] if node["kind"] == "absence"]
    assert absence_nodes == [{
        "id": absences[0].node_id,
        "kind": "absence",
        "value": {
            "position": 0,
            "predicate": "CERTIFIED",
            "subject": "supplier-1",
            "object": "iso9001",
            "scope": ["CERTIFIED"],
        },
    }]
    justification = value["justifications"][0]
    assert justification["rule"] == rule.version_id
    assert justification["premises"] == [
        version.version_id
        for version in graph.iter_claims_as_of(
            valid_time=10, subject="supplier-1", predicate="SHIPS"
        )
    ]
    assert justification["absences"] == [absence_nodes[0]["value"]]


def test_negation_reads_completed_lower_strata(graph):
    claim(graph, "audited-co", "AUDIT_PASS", "ok")
    claim(graph, "audited-co", "SHIPS", "widgets")
    claim(graph, "unaudited-co", "SHIPS", "widgets")
    graph.create_rule(
        "audit-summary",
        when=[("?s", "AUDIT_PASS", "ok")],
        then=("?s", "AUDITED", "ok"),
    )
    graph.create_rule(
        "clear-shippers",
        when=[("?s", "SHIPS", "?goods")],
        then=("?s", "CLEAR_TO_SHIP", "yes"),
        not_when=[("?s", "AUDITED", "ok")],
        closed_world=["AUDITED"],
    )
    graph.run_rules(as_of=10)
    cleared = {
        version.claim.subject
        for version in graph.iter_claims_as_of(valid_time=10, predicate="CLEAR_TO_SHIP")
    }
    assert cleared == {"unaudited-co"}


def test_partial_negative_overlap_blocks_the_whole_derivation(graph):
    # The claim model stores one interval per conclusion. A negative fact that
    # only overlaps part of the positive interval therefore blocks derivation
    # rather than producing an unsound partial interval.
    claim(graph, "supplier-1", "SHIPS", "widgets", valid=(0, 100))
    claim(graph, "supplier-1", "CERTIFIED", "iso9001", valid=(10, 20))
    compliance_rule(graph)
    graph.run_rules(as_of=10)
    assert flags(graph) == []


def test_negation_is_world_local(graph):
    claim(graph, "supplier-1", "SHIPS", "widgets", world="w1")
    claim(graph, "supplier-1", "CERTIFIED", "iso9001", world="w2")
    compliance_rule(graph)
    graph.run_rules(as_of=10, world="w1")
    assert [item.claim.world for item in flags(graph, world="w1")] == ["w1"]


def test_repeated_run_and_maintenance_are_idempotent(graph):
    claim(graph, "supplier-1", "SHIPS", "widgets")
    compliance_rule(graph)
    graph.run_rules(as_of=10)
    second = graph.run_rules(as_of=10)
    assert second.derived_count == 0
    assert flags(graph)

    maintained = graph.maintain_truth(as_of=10)
    assert maintained.asserted_count == 0
    assert maintained.corrected_count == 0
    assert maintained.retracted_count == 0
    assert flags(graph)


def test_new_version_without_closed_scope_fails_closed_at_catalog_time(graph):
    claim(graph, "supplier-1", "SHIPS", "widgets")
    compliance_rule(graph)
    # Re-versioning the same rule without the closed-world declaration makes
    # the still-active negation undeclared; the run must fail, not assume empty.
    with pytest.raises(RuleError, match="closed-world"):
        graph.create_rule(
            "missing-certification",
            when=[("?s", "SHIPS", "?goods")],
            then=("?s", "REQUIRES_SAFETY_REVIEW", "yes"),
            not_when=[("?s", "CERTIFIED", "iso9001")],
        )


def test_stale_derived_fact_does_not_block_absence_after_retraction(graph):
    audit = claim(graph, "audited-co", "AUDIT_PASS", "ok")
    claim(graph, "audited-co", "SHIPS", "widgets")
    graph.create_rule(
        "audit-summary",
        when=[("?s", "AUDIT_PASS", "ok")],
        then=("?s", "AUDITED", "ok"),
    )
    graph.create_rule(
        "clear-shippers",
        when=[("?s", "SHIPS", "?goods")],
        then=("?s", "CLEAR_TO_SHIP", "yes"),
        not_when=[("?s", "AUDITED", "ok")],
        closed_world=["AUDITED"],
    )
    graph.run_rules(as_of=10)
    assert list(graph.iter_claims_as_of(valid_time=10, predicate="CLEAR_TO_SHIP")) == []

    graph.retract_claim(audit.logical_id, valid=audit.valid)
    graph.run_rules(as_of=10)
    cleared = {
        version.claim.subject
        for version in graph.iter_claims_as_of(valid_time=10, predicate="CLEAR_TO_SHIP")
    }
    # The stale AUDITED claim remains visible history, but it is not
    # authoritative input for the stratified closure.
    assert cleared == {"audited-co"}
    assert list(graph.iter_claims_as_of(valid_time=10, predicate="AUDITED"))
