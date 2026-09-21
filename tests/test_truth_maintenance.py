import json

import pytest

from gestaltdb import ClaimExplanation, RuleEvaluationLimitError, TruthMaintenanceResult
from gestaltdb.graphdb import GraphDB, _TRUTH_MAINTENANCE_STATE_KEY
from gestaltdb.serializers import JSONSerializer
from tests.test_temporal_as_of import _MetadataStore


@pytest.fixture
def graph():
    return GraphDB(_MetadataStore(), JSONSerializer())


def _claim(graph, subject, predicate, object, *, agent="source", valid=(0, 100)):
    return graph.assert_claim(
        subject=subject,
        predicate=predicate,
        object=object,
        polarity="positive",
        agent=agent,
        valid=valid,
    )


def _copy_rule(graph, source="P", target="Q", rule_id="copy"):
    return graph.create_rule(
        rule_id,
        when=[("?subject", source, "?object")],
        then=("?subject", target, "?object"),
    )


def test_maintenance_removes_one_support_then_retracts_after_the_final_support(graph):
    first = _claim(graph, "a", "P", "b", agent="first")
    second = _claim(graph, "a", "P", "b", agent="second")
    _copy_rule(graph)
    initial = graph.maintain_truth(as_of=50)
    derived = initial.versions[0]

    graph.retract_claim(
        first.logical_id,
        supersedes_version_id=first.version_id,
        reason="withdrawn",
    )
    maintained = graph.maintain_truth()
    current = graph.get_claim_as_of(derived.logical_id, valid_time=50)

    assert isinstance(maintained, TruthMaintenanceResult)
    assert maintained.corrected_count == 1
    assert maintained.retracted_count == 0
    assert current.version_id == maintained.versions[0].version_id
    assert current.claim.provenance["justifications"][0]["premise_version_ids"] == (
        second.version_id,
    )

    graph.retract_claim(
        second.logical_id,
        supersedes_version_id=second.version_id,
        reason="withdrawn",
    )
    final = graph.maintain_truth()

    assert final.corrected_count == 0
    assert final.retracted_count == 1
    assert graph.get_claim_as_of(derived.logical_id, valid_time=50) is None
    assert graph.maintain_truth().commit_id is None


def test_historical_explanations_are_stable_after_support_changes(graph):
    first = _claim(graph, "a", "P", "b", agent="first")
    second = _claim(graph, "a", "P", "b", agent="second")
    rule = _copy_rule(graph)
    original = graph.maintain_truth(as_of=50).versions[0]
    original_system_time = original.system_time

    graph.retract_claim(first.logical_id, supersedes_version_id=first.version_id)
    corrected = graph.maintain_truth().versions[0]

    historical = graph.explain_claim(
        original.logical_id,
        valid_time=50,
        system_time=original_system_time,
    )
    current = graph.explain_claim(original.logical_id, valid_time=50)

    assert isinstance(historical, ClaimExplanation)
    assert historical.root_version_id == original.version_id
    assert current.root_version_id == corrected.version_id
    assert {node.node_id for node in historical.nodes} == {
        original.version_id,
        rule.version_id,
        first.version_id,
        second.version_id,
    }
    assert {node.node_id for node in current.nodes} == {
        corrected.version_id,
        rule.version_id,
        second.version_id,
    }
    assert graph.explain_claim("claim:missing", valid_time=50) is None


def test_maintenance_retracts_removed_segments_when_support_narrows_validity(graph):
    broad = _claim(graph, "a", "P", "b", agent="broad", valid=(0, 100))
    _claim(graph, "a", "P", "b", agent="narrow", valid=(20, 80))
    _copy_rule(graph)
    derived = graph.maintain_truth(as_of=50).versions[0]

    graph.retract_claim(broad.logical_id, supersedes_version_id=broad.version_id)
    result = graph.maintain_truth()

    assert result.corrected_count == 1
    assert len(result.versions) == 3
    assert graph.get_claim_as_of(derived.logical_id, valid_time=19) is None
    assert graph.get_claim_as_of(derived.logical_id, valid_time=20) is not None
    assert graph.get_claim_as_of(derived.logical_id, valid_time=79) is not None
    assert graph.get_claim_as_of(derived.logical_id, valid_time=80) is None


def test_maintenance_matches_a_fresh_full_recomputation_and_collapses_cycles(graph):
    premise = _claim(graph, "a", "P", "b")
    _copy_rule(graph, "P", "Q", "p-to-q")
    _copy_rule(graph, "Q", "P", "q-to-p")
    graph.maintain_truth(as_of=50)

    graph.retract_claim(premise.logical_id, supersedes_version_id=premise.version_id)
    result = graph.maintain_truth()

    assert result.retracted_count == 2
    assert list(graph.iter_claims_as_of(
        valid_time=50, agent="gestaltdb:rules"
    )) == []

    fresh = GraphDB(_MetadataStore(), JSONSerializer())
    _copy_rule(fresh, "P", "Q", "p-to-q")
    _copy_rule(fresh, "Q", "P", "q-to-p")
    assert fresh.run_rules(as_of=50).derived_count == 0


def test_rule_version_changes_incrementally_replace_conclusions(graph):
    premise = _claim(graph, "a", "P", "b")
    _copy_rule(graph, "P", "Q")
    old = graph.maintain_truth(as_of=50).versions[0]
    _copy_rule(graph, "P", "R")

    result = graph.maintain_truth()

    assert result.asserted_count == 1
    assert result.retracted_count == 1
    assert graph.get_claim_as_of(old.logical_id, valid_time=50) is None
    current = list(graph.iter_claims_as_of(
        valid_time=50, agent="gestaltdb:rules"
    ))
    assert [(item.claim.subject, item.claim.predicate, item.claim.object) for item in current] == [
        (premise.claim.subject, "R", premise.claim.object)
    ]
    state = json.loads(
        graph.store.get_metadata(_TRUTH_MAINTENANCE_STATE_KEY).decode("utf-8")
    )
    assert state["derived"][current[0].logical_id]["support_count"] == 1
    assert list(state["rule_dependents"].values()) == [[current[0].logical_id]]


def test_maintenance_limits_publish_no_partial_changes(graph):
    _claim(graph, "a", "P", "b")
    _copy_rule(graph, "P", "Q", "p-to-q")
    _copy_rule(graph, "Q", "R", "q-to-r")
    before, _ = graph._temporal_sequence()

    with pytest.raises(RuleEvaluationLimitError, match="max_iterations"):
        graph.maintain_truth(as_of=50, max_iterations=1)

    after, _ = graph._temporal_sequence()
    assert after == before
    assert list(graph.iter_claims_as_of(
        valid_time=50, agent="gestaltdb:rules"
    )) == []


def test_interrupted_dependency_index_publication_resumes_idempotently(graph):
    first = _claim(graph, "a", "P", "b", valid=(0, 10))
    _copy_rule(graph)
    graph.maintain_truth(as_of=5)
    second = _claim(graph, "c", "P", "d", valid=(20, 30))
    original_put = graph.store.put_metadata
    interrupted = False

    def put_metadata(key, value):
        nonlocal interrupted
        if key == _TRUTH_MAINTENANCE_STATE_KEY and not interrupted:
            interrupted = True
            raise RuntimeError("interrupted")
        return original_put(key, value)

    graph.store.put_metadata = put_metadata
    with pytest.raises(RuntimeError, match="interrupted"):
        graph.maintain_truth(as_of=25)

    resumed = graph.maintain_truth()

    assert resumed.commit_id is None
    assert len(list(graph.iter_claims_as_of(valid_time=5, agent="gestaltdb:rules"))) == 1
    current = list(graph.iter_claims_as_of(valid_time=25, agent="gestaltdb:rules"))
    assert [(item.claim.subject, item.claim.object) for item in current] == [
        (second.claim.subject, second.claim.object)
    ]
    assert graph.get_claim_as_of(first.logical_id, valid_time=5) is not None
    assert graph.store.get_metadata(_TRUTH_MAINTENANCE_STATE_KEY) is not None


def test_explanation_bounds_terminate_recursive_derivations(graph):
    _claim(graph, "a", "P", "b")
    _copy_rule(graph, "P", "Q", "p-to-q")
    _copy_rule(graph, "Q", "P", "q-to-p")
    derived = graph.maintain_truth(as_of=50).versions[0]

    explanation = graph.explain_claim(
        derived.logical_id,
        valid_time=50,
        max_depth=0,
    )

    assert explanation.truncated is True
    assert len(explanation.nodes) == 1
    with pytest.raises(ValueError, match="max_nodes"):
        graph.explain_claim(derived.logical_id, valid_time=50, max_nodes=0)
