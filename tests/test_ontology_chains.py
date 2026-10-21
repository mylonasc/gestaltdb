"""RE-08 M1 Horn-safe schema extensions with exact origins and grounding parity."""

import pytest

from gestaltdb.ontology import (
    IntersectionSubClassOf,
    Ontology,
    OntologyError,
    PropertyChain,
    Reflexive,
    SubClassOf,
    TYPE_PREDICATE,
    UnionSubClassOf,
    compile_ontology,
)
from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits
from tests.test_reasoning_engine import _reference


def evaluate(compiled, facts):
    return DatalogEngine().evaluate(compiled.program, facts, limits=ReasoningLimits(), collect_proofs=True)


def test_chain_intersection_union_and_reflexive_closure():
    plan = compile_ontology(Ontology("o", [
        PropertyChain("hasParent", "hasBrother", "hasUncle"),
        IntersectionSubClassOf(["Researcher", "Teacher"], "Professor"),
        UnionSubClassOf(["Cat", "Dog"], "Pet"),
        Reflexive("knows", "Person"),
    ]))
    assert {origin.axiom_id for origin in plan.origins} == {axiom.axiom_id for axiom in plan.ontology.axioms}
    assert len({origin.rule_id for origin in plan.origins}) == len(plan.program.rules) == 5
    facts = [Fact("alice", "hasParent", "bob"), Fact("bob", "hasBrother", "carol"),
             Fact("dave", TYPE_PREDICATE, "Researcher"), Fact("dave", TYPE_PREDICATE, "Teacher"),
             Fact("erin", TYPE_PREDICATE, "Researcher"),
             Fact("tom", TYPE_PREDICATE, "Cat"), Fact("jerry", TYPE_PREDICATE, "Dog"),
             Fact("mallory", TYPE_PREDICATE, "Person")]
    result = evaluate(plan, facts)
    assert set(result.facts) == _reference(plan.program, facts)
    assert not result.proofs.truncated
    assert Fact("alice", "hasUncle", "carol") in result
    assert Fact("dave", TYPE_PREDICATE, "Professor") in result
    assert Fact("erin", TYPE_PREDICATE, "Professor") not in result
    assert Fact("tom", TYPE_PREDICATE, "Pet") in result
    assert Fact("jerry", TYPE_PREDICATE, "Pet") in result
    assert Fact("mallory", "knows", "mallory") in result
    assert Fact("tom", "knows", "tom") not in result
    # Deterministic repeat with reversed input.
    assert evaluate(plan, list(reversed(facts))) == result


def test_member_forms_validate_and_canonicalize():
    assert IntersectionSubClassOf(["B", "A", "A"], "C").members == ("A", "B")
    assert UnionSubClassOf(["B", "A"], "C").members == ("A", "B")
    assert (IntersectionSubClassOf(["A", "B"], "C").axiom_id
            == IntersectionSubClassOf(["B", "A", "B"], "C").axiom_id)
    for make in [lambda: IntersectionSubClassOf([], "C"),
                 lambda: IntersectionSubClassOf(["A", ""], "C"),
                 lambda: IntersectionSubClassOf("AB", "C"),
                 lambda: UnionSubClassOf([], "C"),
                 lambda: UnionSubClassOf(["A"], ""),
                 lambda: PropertyChain("p", "", "q"),
                 lambda: PropertyChain(TYPE_PREDICATE, "p", "q"),
                 lambda: Reflexive("p", "")]:
        with pytest.raises(OntologyError):
            make()
    with pytest.raises(OntologyError, match="unsupported axiom"):
        compile_ontology(Ontology("o", [SubClassOf("A", "B"), "not-an-axiom"]))


def test_union_members_compile_to_distinct_origin_bound_rules():
    plan = compile_ontology(Ontology("o", [UnionSubClassOf(["A", "B"], "C")]))
    assert [rule.rule_id for rule in plan.program.rules] == [
        f"ontology:{plan.ontology.axioms[0].axiom_id}:union-0",
        f"ontology:{plan.ontology.axioms[0].axiom_id}:union-1",
    ]
    result = evaluate(plan, [Fact("n", TYPE_PREDICATE, "B")])
    assert Fact("n", TYPE_PREDICATE, "C") in result
    assert Fact("n", TYPE_PREDICATE, "A") not in result
