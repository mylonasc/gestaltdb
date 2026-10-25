"""Unified model-output/logical audits retain exact version evidence."""

import json

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.inference import explain_inference, export_inference
from gestaltdb.rules import RuleError, RuleEvaluationLimitError
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


def prediction(graph, subject="alice", *, provenance=None):
    return graph.epistemic.assert_claim(
        subject=subject, predicate="PREDICTED_RISK", object="high",
        polarity="positive", agent="risk-model", confidence=0.8,
        provenance=provenance if provenance is not None else {
            "model_output": {"model_id": "gnn-risk", "model_version": "weights-7"}},
        valid=(0, 100))


def derive(graph):
    rule = graph.rules.create_rule("review", when=[("?s", "PREDICTED_RISK", "high")],
                             then=("?s", "REQUIRES_REVIEW", "yes"))
    graph.rules.run(as_of=10)
    conclusions = {v.claim.subject: v for v in graph.epistemic.iter_claims_as_of(
        valid_time=10, predicate="REQUIRES_REVIEW")}
    return rule, conclusions


def test_model_logical_export_roundtrip_and_html(graph):
    model = prediction(graph)
    rule, conclusions = derive(graph)
    report = explain_inference(graph, conclusions["alice"].logical_id, valid_time=10)
    value = json.loads(report.to_json())
    assert value == report.to_dict()
    assert not value["truncated"]
    model_node = next(n for n in value["nodes"] if n["id"] == model.version_id)
    assert model_node["kind"] == "model_output"
    assert model_node["claim"]["confidence"] == 0.8
    assert model_node["claim"]["provenance"]["model_output"]["model_version"] == "weights-7"
    assert value["justifications"] == [{"conclusion": conclusions["alice"].version_id,
                                        "rule": rule.version_id, "premises": [model.version_id]}]
    assert report.to_json() == explain_inference(
        graph, conclusions["alice"].logical_id, valid_time=10).to_json()
    value["nodes"].clear()
    assert report.to_dict()["nodes"]
    assert "Model scores are asserted metadata" in report.as_html()


def test_shared_rule_keeps_premise_groups_separate(graph):
    alice, bob = prediction(graph), prediction(graph, "bob")
    rule, conclusions = derive(graph)
    # Combine two legacy graphs sharing the same rule node: grouped exports
    # must not mistake all outgoing rule edges for each conclusion's proof.
    from dataclasses import replace
    first = graph.rules.explain(conclusions["alice"].logical_id, valid_time=10)
    second = graph.rules.explain(conclusions["bob"].logical_id, valid_time=10)
    nodes = {n.node_id: n for n in (*first.nodes, *second.nodes)}
    both = replace(first, nodes=tuple(nodes.values()), edges=(*first.edges, *second.edges))
    groups = export_inference(both).to_dict()["justifications"]
    assert {tuple(item["premises"]) for item in groups} == {(alice.version_id,), (bob.version_id,)}
    assert {item["rule"] for item in groups} == {rule.version_id}


def test_truncation_and_budgets_fail_closed(graph):
    prediction(graph)
    _, conclusions = derive(graph)
    claim_id = conclusions["alice"].logical_id
    truncated = explain_inference(graph, claim_id, valid_time=10, max_nodes=1)
    assert truncated.to_dict()["truncated"]
    assert truncated.to_dict()["justifications"] == []
    assert "TRUNCATED" in truncated.as_html()
    with pytest.raises(RuleEvaluationLimitError, match="max_edges"):
        explain_inference(graph, claim_id, valid_time=10, max_edges=1)
    with pytest.raises(RuleEvaluationLimitError, match="max_bytes"):
        explain_inference(graph, claim_id, valid_time=10, max_bytes=1)
    with pytest.raises(RuleError, match="positive integer"):
        explain_inference(graph, claim_id, valid_time=10, max_bytes=True)


def test_explicit_model_tag_and_safe_rendering(graph):
    plain = prediction(graph, provenance={"note": "<script>alert(1)</script>"})
    report = explain_inference(graph, plain.logical_id, valid_time=10)
    assert report.to_dict()["nodes"][0]["kind"] == "claim"
    assert "<script>" not in report.as_html()
    assert "&lt;script&gt;" in report.as_html()
    bad = prediction(graph, "bob", provenance={"model_output": {"model_id": "gnn"}})
    with pytest.raises(RuleError, match="model_version"):
        explain_inference(graph, bad.logical_id, valid_time=10)


def test_history_and_missing_roots(graph):
    original = prediction(graph)
    before = graph.temporal.get_temporal_commit(original.commit_id).system_time
    graph.epistemic.retract_claim(original.logical_id, valid=(0, 100))
    assert explain_inference(graph, original.logical_id, valid_time=10) is None
    historical = explain_inference(graph, original.logical_id, valid_time=10, system_time=before)
    assert historical.to_dict()["root_version_id"] == original.version_id


def test_alternative_supports_and_exact_old_prediction(graph):
    original = prediction(graph)
    _, conclusions = derive(graph)
    graph.rules.create_rule("second-review", when=[("?s", "PREDICTED_RISK", "high")],
                      then=("?s", "REQUIRES_REVIEW", "yes"))
    graph.rules.run(as_of=10)
    graph.epistemic.retract_claim(original.logical_id, valid=(0, 100))
    # No truth-maintenance pass was requested: the persisted conclusion's
    # old evidence must remain inspectable, never substituted with current data.
    report = explain_inference(graph, conclusions["alice"].logical_id, valid_time=10)
    groups = report.to_dict()["justifications"]
    assert len(groups) == 2
    assert all(item["premises"] == [original.version_id] for item in groups)
    assert len({item["rule"] for item in groups}) == 2
