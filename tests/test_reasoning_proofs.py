"""Finite bounded witnesses, not a support-index or truth-maintenance service."""

from dataclasses import FrozenInstanceError
import random

import pytest

from gestaltdb.ontology.compiler import compile_ontology
from gestaltdb.ontology.model import Domain, Ontology, SubClassOf, TYPE_PREDICATE
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import (
    Atom, EvaluationResult, Fact, Program, ReasoningError, ReasoningLimitError,
    ReasoningLimits, Rule, Variable,
)
from gestaltdb.reasoning.proofs import Explanation, FactWitness, ProofTrace
from gestaltdb.reasoning.protocols import Evidence


X, Y = Variable("x"), Variable("y")


def _rule(source="p", target="q", name="copy"):
    return Rule(name, [Atom(X, source, Y)], Atom(X, target, Y))


def _run(rules=(), facts=(), *, collect_proofs=True, **limits):
    return DatalogEngine().evaluate(
        Program(rules), facts, limits=ReasoningLimits(**limits), collect_proofs=collect_proofs,
    )


def test_result_membership_and_immutable_filtered_lookups():
    a, b, c = Fact("a", "p", "b"), Fact("a", "q", "c"), Fact("b", "p", "c")
    result = _run(facts=[c, b, a], collect_proofs=False)
    assert a in result
    assert "a" not in result
    assert Fact("missing", "p", "c") not in result
    assert result.match() == (a, b, c)
    assert result.match(subject="a") == (a, b)
    assert result.match(subject="a", predicate="q", object="c") == (b,)
    assert result.match(predicate="p", object="c") == (c,)
    assert result.match(subject="missing") == ()
    assert result.match(subject="a", object="missing") == ()
    with pytest.raises(ReasoningError):
        result.match(predicate="")


def test_proofs_are_opt_in_and_never_change_closure_or_iterations():
    facts = [Fact("a", "p", "b")]
    off = _run([_rule()], facts, collect_proofs=False)
    on = _run([_rule()], facts)
    assert off.facts == on.facts
    assert off.iterations == on.iterations
    assert off.proofs is None
    with pytest.raises(ReasoningError, match="disabled"):
        off.explain(Fact("a", "q", "b"))
    with pytest.raises(KeyError):
        on.explain(Fact("a", "absent", "b"))


def test_exact_rules_ordered_premises_and_canonical_input_reference():
    p, q, r = Fact("a", "p", "b"), Fact("a", "q", "b"), Fact("a", "r", "b")
    rules = [_rule("p", "q", "1"), _rule("q", "r", "2")]
    facts = [Evidence(p, "version:2"), p, Evidence(p, "version:1")]
    result = _run(rules, facts)
    explanation = result.explain(r)
    assert not explanation.truncated
    assert explanation.nodes == (
        FactWitness(p, evidence=("version:1",)),
        FactWitness(q, rules[0], (p,)), FactWitness(r, rules[1], (q,)),
    )
    assert result == _run(list(reversed(rules)), list(reversed(facts)))
    assert result.proofs.program.digest == result.program_digest


def test_witnesses_preserve_body_positions_despite_join_reordering():
    z = Variable("z")
    rule = Rule("join", [Atom(X, "p", Y), Atom(Y, "q", z), Atom(Y, "tag", "yes"), Atom(X, "p", Y)],
                Atom(X, "out", z))
    facts = [Fact("a", "p", "b"), Fact("b", "q", "c"), Fact("b", "tag", "yes"),
             Fact("b", "q", "d"), Fact("noise", "q", "e")]
    result = _run([rule], facts)
    for end in ("c", "d"):
        root = Fact("a", "out", end)
        witness = next(node for node in result.explain(root).nodes if node.fact == root)
        assert witness.premises == (facts[0], Fact("b", "q", end), facts[2], facts[0])


def test_shared_ancestors_are_not_duplicated_and_cycles_keep_ground_support():
    p = Fact("a", "p", "b")
    rules = [_rule("p", "q", "1"), _rule("q", "p", "2"), _rule("p", "r", "3"),
             Rule("4", [Atom(X, "q", Y), Atom(X, "r", Y)], Atom(X, "out", Y))]
    explanation = _run(rules, [p]).explain(Fact("a", "out", "b"))
    assert not explanation.truncated
    assert len(explanation.nodes) == 4
    assert next(node for node in explanation.nodes if node.fact == p).rule is None
    assert len({node.fact for node in explanation.nodes}) == 4


def test_only_one_alternative_is_retained_without_claiming_all_support():
    rules = [_rule("p", "out", "1"), _rule("q", "out", "2")]
    result = _run(rules, [Fact("a", "p", "b"), Fact("a", "q", "b")])
    explanation = result.explain(Fact("a", "out", "b"))
    assert not explanation.truncated
    assert len(explanation.nodes) == 2
    assert explanation.nodes[-1].rule == rules[0]


def test_node_storage_cap_preserves_complete_closure_and_missing_witness_is_explicit():
    p, q = Fact("a", "p", "b"), Fact("a", "q", "b")
    result = _run([_rule()], [p], max_proof_nodes=1)
    assert result.facts == (p, q)
    assert result.proofs.truncated
    assert result.proofs.nodes == (FactWitness(p),)
    assert result.explain(q) == Explanation(q, (), True)
    # The global storage truncation flag is conservative even for a stored leaf.
    assert result.explain(p).truncated


def test_input_witness_selection_is_bounded_canonical_and_duplicate_insensitive():
    values = [Evidence(Fact(letter, "p", "b"), f"ref:{index}")
              for index, letter in enumerate("zyxcba")]
    values.extend([Evidence(Fact("a", "p", "b"), "ref:0"), Fact("a", "p", "b")])
    result = _run(facts=values, max_proof_nodes=2)
    assert [node.fact.subject for node in result.proofs.nodes] == ["a", "b"]
    assert result.proofs.nodes[0].evidence == ("ref:0",)
    rng = random.Random(4)
    for _ in range(10):
        rng.shuffle(values)
        assert _run(facts=values, max_proof_nodes=2) == result


def test_edge_budget_bounds_premise_occurrences_and_evidence_references():
    rule = Rule("repeat", [Atom(X, "p", Y)] * 3, Atom(X, "q", Y))
    inputs = [Evidence(Fact("a", "p", "b"), "version:1")]
    result = _run([rule], inputs, max_proof_edges=3)
    assert Fact("a", "q", "b") in result
    assert result.proofs.truncated
    assert len(result.proofs.nodes) == 1
    exact = _run([rule], inputs, max_proof_edges=4)
    assert not exact.proofs.truncated
    assert sum(len(node.premises) + len(node.evidence) for node in exact.proofs.nodes) == 4


def test_evidence_edge_truncation_and_large_body_do_not_truncate_closure():
    inputs = [Evidence(Fact(letter, "p", "b"), "v") for letter in "ab"]
    result = _run(facts=inputs, max_proof_edges=1)
    assert result.proofs.truncated
    assert sum(len(node.evidence) for node in result.proofs.nodes) == 1
    rule = Rule("long", [Atom("a", "p", "b")] * 1100, Atom("a", "q", "b"))
    result = _run([rule], [Fact("a", "p", "b")], max_proof_edges=2)
    assert Fact("a", "q", "b") in result
    assert result.proofs.truncated


def test_missing_stored_premise_is_reported_without_losing_later_closure():
    long = Rule("long", [Atom(X, "p", Y)] * 3, Atom(X, "q", Y))
    result = _run([long, _rule("q", "r", "next")], [Fact("a", "p", "b")], max_proof_edges=2)
    root = Fact("a", "r", "b")
    assert root in result
    explanation = result.explain(root)
    assert explanation.truncated
    assert len(explanation.nodes) == 1
    assert explanation.nodes[0].premises == (Fact("a", "q", "b"),)


def test_empty_trace_needs_no_storage_and_has_no_truncation():
    result = _run([_rule()])
    assert result.facts == ()
    assert result.proofs.nodes == ()
    assert not result.proofs.truncated


def test_per_query_bound_does_not_modify_stored_graph():
    rules = [_rule("p", "q", "1"), _rule("q", "r", "2")]
    result = _run(rules, [Fact("a", "p", "b")])
    root = Fact("a", "r", "b")
    small = result.explain(root, max_nodes=1)
    assert len(small.nodes) == 1
    assert small.nodes[0].fact == root
    assert small.truncated
    assert len(result.explain(root, max_nodes=3).nodes) == 3
    assert not result.proofs.truncated
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ReasoningError, match="max_nodes"):
            result.explain(root, max_nodes=bad)


def test_deep_explanations_and_validation_are_iterative():
    rules = [_rule(f"p{i}", f"p{i+1}", f"r{i}") for i in range(1100)]
    result = _run(rules, [Fact("a", "p0", "b")], max_iterations=1200)
    explanation = result.explain(Fact("a", "p1100", "b"), max_nodes=1200)
    assert len(explanation.nodes) == 1101
    assert not explanation.truncated


def test_exact_compiler_axioms_are_resolvable_without_importing_ontology_in_core():
    ontology = Ontology("o", [Domain("worksFor", "Researcher"), SubClassOf("Researcher", "Person")])
    compiled = compile_ontology(ontology)
    source = Fact("alice", "worksFor", "acme")
    result = DatalogEngine().evaluate(compiled.program, [Evidence(source, "claim-version:1")],
                                     limits=ReasoningLimits(), collect_proofs=True)
    explanation = result.explain(Fact("alice", TYPE_PREDICATE, "Person"))
    origins = {origin.rule_id: origin.axiom_id for origin in compiled.origins}
    axioms = {axiom.axiom_id: axiom for axiom in ontology.axioms}
    used = {axioms[origins[node.rule.rule_id]] for node in explanation.nodes if node.rule is not None}
    assert used == set(ontology.axioms)
    assert next(node for node in explanation.nodes if node.fact == source).evidence == ("claim-version:1",)
    assert compiled.revision_id == ontology.revision_id


def test_invalid_witnesses_and_cyclic_traces_are_rejected():
    p, q = Fact("a", "p", "b"), Fact("a", "q", "b")
    forward, reverse = _rule(), _rule("q", "p", "reverse")
    for build in (
        lambda: FactWitness("fact"), lambda: FactWitness(p, premises=[q]),
        lambda: FactWitness(p, evidence=["a", "b"]), lambda: FactWitness(q, forward, []),
        lambda: FactWitness(q, forward, [q]), lambda: FactWitness(p, forward, [p]),
        lambda: FactWitness(q, forward, [p], ["reference"]),
    ):
        with pytest.raises(ReasoningError):
            build()
    with pytest.raises(ReasoningError, match="acyclic"):
        ProofTrace(Program([forward, reverse]), [FactWitness(q, forward, [p]), FactWitness(p, reverse, [q])])
    with pytest.raises(ReasoningError, match="unique"):
        ProofTrace(Program(), [FactWitness(p), FactWitness(p)])
    with pytest.raises(ReasoningError, match="premises"):
        ProofTrace(Program([forward]), [FactWitness(q, forward, [p])])
    with pytest.raises(ReasoningError, match="belong"):
        ProofTrace(Program(), [FactWitness(p), FactWitness(q, forward, [p])])


def test_result_rejects_mismatched_program_or_extra_proof_facts():
    p = Fact("a", "p", "b")
    proof = ProofTrace(Program(), [FactWitness(p)])
    with pytest.raises(ReasoningError, match="closure"):
        EvaluationResult([], 0, Program().digest, proof)
    with pytest.raises(ReasoningError, match="program"):
        EvaluationResult([p], 0, Program([_rule()]).digest, proof)
    with pytest.raises(ReasoningError, match="cover"):
        EvaluationResult([p], 0, Program().digest, ProofTrace(Program(), []))
    rule = _rule()
    q = Fact("a", "q", "b")
    dangling = ProofTrace(Program([rule]), [FactWitness(q, rule, [p])], True)
    with pytest.raises(ReasoningError, match="closure"):
        EvaluationResult([q], 0, Program([rule]).digest, dangling)


def test_proof_values_copy_collections_and_are_immutable():
    fact = Fact("a", "p", "b")
    refs = ["v"]
    node = FactWitness(fact, evidence=refs)
    refs.clear()
    nodes = [node]
    trace = ProofTrace(Program(), nodes)
    nodes.clear()
    assert trace.nodes == (node,)
    assert node.evidence == ("v",)
    with pytest.raises(FrozenInstanceError):
        trace.truncated = True
    with pytest.raises(TypeError):
        trace._by_fact[fact] = node
    assert hash(trace)


def test_inference_bounds_remain_fail_closed_with_proofs_and_evidence():
    engine = DatalogEngine()
    program = Program([_rule()])
    fact = Fact("a", "p", "b")
    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        engine.evaluate(program, [Evidence(fact, "a"), Evidence(fact, "b")],
                        limits=ReasoningLimits(max_input_facts=1), collect_proofs=True)
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        engine.evaluate(program, [fact], limits=ReasoningLimits(max_facts=1), collect_proofs=True)
    assert not engine.evaluate(program, [fact], limits=ReasoningLimits(), collect_proofs=True).proofs.truncated
    with pytest.raises(ReasoningError, match="collect_proofs"):
        engine.evaluate(program, [], limits=ReasoningLimits(), collect_proofs="yes")


def test_named_evidence_is_accepted_without_collecting_proofs():
    fact = Fact("a", "p", "b")
    result = _run([_rule()], [Evidence(fact, "v1"), Evidence(fact, "v2")], collect_proofs=False)
    assert result.proofs is None
    assert result.facts == (fact, Fact("a", "q", "b"))


def test_generated_storage_budgets_preserve_closure_and_bound_every_trace():
    rng = random.Random(20261020)
    z = Variable("z")
    rules = [_rule("p", "q", "1"), _rule("q", "p", "2"),
             Rule("3", [Atom(X, "p", Y), Atom(Y, "p", z)], Atom(X, "p", z))]
    for _ in range(50):
        inputs = [Evidence(Fact(rng.choice("abcd"), "p", rng.choice("abcd")), f"ref:{i}")
                  for i in range(8)]
        expected = _run(rules, inputs, collect_proofs=False)
        limits = dict(max_proof_nodes=rng.randint(1, 12), max_proof_edges=rng.randint(1, 12))
        result = _run(rules, inputs, **limits)
        assert result.facts == expected.facts
        assert result.iterations == expected.iterations
        assert len(result.proofs.nodes) <= limits["max_proof_nodes"]
        assert sum(len(node.premises) + len(node.evidence) for node in result.proofs.nodes) <= limits["max_proof_edges"]
        assert result == _run(rules, list(reversed(inputs)), **limits)
        for fact in result.facts[:5]:
            explanation = result.explain(fact, max_nodes=1)
            assert len(explanation.nodes) <= 1
            assert not explanation.nodes or explanation.nodes[0].fact == fact
