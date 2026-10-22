"""Specialization preserves negative guards as well as positive atoms."""

import pytest

from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.typed import (
    GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact, expand_predicate_variables,
)


@pytest.mark.parametrize("predicates", [("p",), ("p", "q")])
def test_specialization_preserves_guards_and_stratified_results(predicates):
    x = Variable("x")
    body = [TypedAtom(Variable(name), [x]) for name in predicates]
    negated = [TypedAtom("blocked", [x]), TypedAtom("excluded", [x])]
    template = GenericRule("copy", body, [], TypedAtom("out", [x]), negated)
    rules = expand_predicate_variables(template, ["candidate", "vendor"])
    assert all(rule.negated == tuple(negated) for rule in rules)
    facts = [TypedFact("candidate", [value]) for value in ("a", "b", "c")]
    facts += [TypedFact("vendor", ["a"]), TypedFact("blocked", ["b"]), TypedFact("excluded", ["c"])]
    result = GenericEngine().evaluate_stratified(
        GenericProgram(rules, closed=["blocked", "excluded"]), facts,
        limits=ReasoningLimits()).result
    assert TypedFact("out", ["a"]) in result
    assert TypedFact("out", ["b"]) not in result
    assert TypedFact("out", ["c"]) not in result
