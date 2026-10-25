"""Tests for reasoning-trace visualization (issue #398, result-viz)."""

from __future__ import annotations

import pytest

from gestaltdb.inference import export_inference
from gestaltdb.ontology.model import (
    Ontology,
    PropertyChain,
    SubClassOf,
    SubPropertyOf,
    UnionSubClassOf,
)
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.explanations import minimal_witness
from gestaltdb.reasoning.model import (
    Atom,
    Fact,
    Program,
    ReasoningLimits,
    Rule,
    Variable,
)
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
from gestaltdb.serializers import JSONSerializer
from gestaltdb.viz import (
    VizOptions,
    visualize_claim_explanation,
    visualize_inference_audit,
    visualize_ontology,
    visualize_proof_explanation,
    visualize_support_set,
)
from gestaltdb.viz.ir import VizCapExceededError
from gestaltdb.viz.reasoning import (
    claim_explanation_graph,
    inference_audit_graph,
    ontology_graph,
    proof_explanation_graph,
    support_set_graph,
)
from tests.test_temporal_as_of import _MetadataStore


def _graph():
    from gestaltdb.graphdb import GraphDB

    return GraphDB(_MetadataStore(), JSONSerializer())


def _rule_graph():
    graph = _graph()
    first = graph.epistemic.assert_claim(
        subject="a", predicate="P", object="b", polarity="positive", agent="first", valid=(0, 100))
    second = graph.epistemic.assert_claim(
        subject="a", predicate="P", object="b", polarity="positive", agent="second", valid=(0, 100))
    rule = graph.rules.create_rule(
        "copy", when=[("?s", "P", "?o")], then=("?s", "Q", "?o"))
    derived = graph.rules.maintain(as_of=50).versions[0]
    explanation = graph.rules.explain(derived.logical_id, valid_time=50)
    return graph, explanation, rule, (first, second, derived)


def test_claim_explanation_graph_topology_and_labels():
    graph, explanation, rule, _ = _rule_graph()
    try:
        viz = claim_explanation_graph(explanation)
        assert viz.meta["builder"] == "from_claim_explanation"
        assert viz.meta["root_version_id"] == explanation.root_version_id
        kinds = {node.id: node.labels[0] for node in viz.nodes}
        assert set(kinds.values()) == {"claim", "rule"}
        by_id = {node.id: node for node in viz.nodes}
        rule_node = by_id[rule.version_id]
        assert rule_node.properties["rule_id"] == "copy"
        assert rule_node.properties["label"] == "copy"
        derived_node = by_id[explanation.root_version_id]
        assert derived_node.properties["statement"] == "a Q b"
        assert derived_node.properties["label"] == "a Q b"
        assert viz.highlight_nodes == (explanation.root_version_id,)
        rels = sorted(edge.type for edge in viz.edges)
        assert rels == ["derived_by", "premise", "premise"]
        # Deterministic: rebuild from the same explanation.
        assert viz.to_json() == claim_explanation_graph(explanation).to_json()
        figure = visualize_claim_explanation(
            explanation, options=VizOptions(node_label_property="label", edge_labels="all"))
        svg = figure.as_svg()
        assert "a Q b" in svg and "copy" in svg
        assert "a Q b" in figure.as_html()
    finally:
        graph.close()


def test_claim_explanation_caps_and_source_truncation_flag():
    graph, explanation, _, _ = _rule_graph()
    try:
        with pytest.raises(VizCapExceededError):
            claim_explanation_graph(explanation, max_nodes=2, absolute_max_nodes=2)
        explanation_truncated = type(explanation)(
            explanation.claim_id, explanation.root_version_id,
            explanation.valid_time, explanation.system_time,
            explanation.nodes, explanation.edges, True)
        viz = claim_explanation_graph(explanation_truncated)
        assert viz.meta["source_truncated"] is True
    finally:
        graph.close()


def test_inference_audit_graph_matches_claim_graph():
    graph, explanation, _, _ = _rule_graph()
    try:
        audit = export_inference(explanation)
        viz = inference_audit_graph(audit)
        assert viz.meta["builder"] == "from_inference_audit"
        assert viz.meta["root_version_id"] == explanation.root_version_id
        assert {n.id for n in viz.nodes} == {n.id for n in claim_explanation_graph(explanation).nodes}
        figure = visualize_inference_audit(
            audit.to_dict(), options=VizOptions(node_label_property="label", edge_labels="all"))
        assert "a Q b" in figure.as_svg()
    finally:
        graph.close()


def _proof_fixture():
    x, y, z = Variable("X"), Variable("Y"), Variable("Z")
    program = Program([
        Rule("parent-ancestor", [Atom(x, "parent", y)], Atom(x, "ancestor", y)),
        Rule("transitive", [Atom(x, "ancestor", y), Atom(y, "ancestor", z)], Atom(x, "ancestor", z)),
    ])
    facts = [Fact("a", "parent", "b"), Fact("b", "parent", "c")]
    result = DatalogEngine().evaluate(
        program, facts, limits=ReasoningLimits(), collect_proofs=True)
    return result.explain(Fact("a", "ancestor", "c"))


def test_proof_explanation_graph_is_fact_tree():
    explanation = _proof_fixture()
    viz = proof_explanation_graph(explanation)
    assert viz.meta["builder"] == "from_proof_explanation"
    labels = {node.properties["statement"] for node in viz.nodes}
    assert "a ancestor c" in labels
    assert all(node.properties["label"] == node.properties["statement"] for node in viz.nodes)
    assert viz.highlight_nodes and len(viz.highlight_nodes) == 1
    # Premises point at conclusions; edge types are rule IDs.
    targets = {edge.target for edge in viz.edges}
    assert viz.highlight_nodes[0] in targets
    assert {edge.type for edge in viz.edges} <= {"parent-ancestor", "transitive"}
    figure = visualize_proof_explanation(
        explanation, options=VizOptions(node_label_property="label", edge_labels="all"))
    svg = figure.as_svg()
    assert "a ancestor c" in svg and "parent-ancestor" in svg


def test_support_set_graph_highlights_chosen_witness():
    from gestaltdb.reasoning.maintenance import rematerialize

    _X, _Y = Variable("X"), Variable("Y")
    program = GenericProgram([
        GenericRule("r1", [TypedAtom("parent", [_X, _Y])], (), TypedAtom("ancestor", [_X, _Y])),
        GenericRule("r2", [TypedAtom("parent", [_X, _Y])], (), TypedAtom("ancestor", [_X, _Y])),
    ])
    outcome = rematerialize(
        program, (TypedFact("parent", ["a", "b"]),), limits=ReasoningLimits())
    fact = TypedFact("ancestor", ["a", "b"])
    assert len([s for s in outcome.supports if s.fact == fact]) == 2
    witness = minimal_witness(fact, outcome.supports)
    viz = support_set_graph(fact, outcome.supports, witness)
    assert viz.meta["builder"] == "from_support_set"
    assert viz.meta["support_count"] == 2
    assert viz.meta["witness_kind"] == "chosen-minimal"
    assert len(viz.highlight_nodes) == 2  # conclusion + chosen support
    figure = visualize_support_set(
        fact, outcome.supports, witness,
        options=VizOptions(node_label_property="label", edge_labels="all"))
    assert viz.to_json() == support_set_graph(fact, outcome.supports, witness).to_json()
    assert "ancestor(a, b)" in figure.as_svg()


def test_ontology_graph_hierarchy_and_reification():
    ontology = Ontology("o1", [
        SubClassOf("Cat", "Animal"),
        SubClassOf("Dog", "Animal"),
        SubPropertyOf("likes", "knows"),
        UnionSubClassOf(("Cat", "Dog"), "Pet"),
        PropertyChain("a", "b", "c"),
    ])
    viz = ontology_graph(ontology)
    assert viz.meta["builder"] == "from_ontology"
    assert viz.meta["axiom_count"] == 5
    by_id = {node.id: node for node in viz.nodes}
    assert by_id["Cat"].labels == ("class",)
    assert by_id["likes"].labels == ("property",)
    assert by_id["Cat"].properties["label"] == "Cat"
    types = sorted(edge.type for edge in viz.edges)
    assert "rdfs:subClassOf" in types
    assert "unionSubClassOf" in types
    # Property chains reify into one axiom node with three edges.
    axiom_nodes = [node for node in viz.nodes if node.labels == ("axiom",)]
    assert len(axiom_nodes) == 1
    figure = visualize_ontology(ontology, options=VizOptions(node_label_property="label", edge_labels="all"))
    svg = figure.as_svg()
    assert "Cat" in svg and "rdfs:subClassOf" in svg


def test_reasoning_figures_default_to_tree_svg_layout():
    explanation = _proof_fixture()
    figure = visualize_proof_explanation(
        explanation, options=VizOptions(node_label_property="label", edge_labels="all"))
    assert figure.viz.meta["suggested_layout"] == "tree"
    assert figure.as_svg() == figure.as_svg(layout="tree")
    assert figure.as_svg() != figure.as_svg(layout="circle")
    graph, claim_explanation, _, _ = _rule_graph()
    try:
        claim_figure = visualize_claim_explanation(
            claim_explanation, options=VizOptions(node_label_property="label"))
        assert claim_figure.as_svg() == claim_figure.as_svg(layout="tree")
    finally:
        graph.close()


def test_reasoning_figures_reject_bad_inputs():
    with pytest.raises(TypeError):
        claim_explanation_graph(object())
    with pytest.raises(TypeError):
        inference_audit_graph(42)
    with pytest.raises(TypeError):
        proof_explanation_graph(object())
    with pytest.raises(TypeError):
        support_set_graph(TypedFact("p", ["a"]), [])
    with pytest.raises(TypeError):
        ontology_graph(object())
