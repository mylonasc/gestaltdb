"""Negation requires complete positive bindings, regardless of delta pivot."""

from itertools import permutations

import pytest

from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("blocked", [False, True])
def test_negation_over_independently_bound_variable(reverse, blocked):
    body = [TypedAtom("p", [X]), TypedAtom("q", [Y])]
    if reverse:
        body.reverse()
    rule = GenericRule("join", body, [], TypedAtom("out", [X, Y]),
                       [TypedAtom("blocked", [Y])])
    facts = [TypedFact("p", ["a"]), TypedFact("q", ["b"])]
    if blocked:
        facts.append(TypedFact("blocked", ["b"]))
    run = GenericEngine().evaluate_stratified(
        GenericProgram([rule], closed=["blocked"]), facts, limits=ReasoningLimits())
    assert (TypedFact("out", ["a", "b"]) in run.result) is not blocked


@pytest.mark.parametrize("body", list(permutations([
    TypedAtom("start", [X]), TypedAtom("edge", [X, Y]), TypedAtom("next", [Y, Z]),
])))
def test_connected_join_negation_is_independent_of_body_order_and_pivot(body):
    rule = GenericRule("join", body, [], TypedAtom("out", [X, Z]),
                       [TypedAtom("blocked", [Z])])
    facts = [TypedFact("start", ["a"]), TypedFact("edge", ["a", "b"]),
             TypedFact("next", ["b", "c"]), TypedFact("next", ["b", "d"]),
             TypedFact("blocked", ["d"])]
    run = GenericEngine().evaluate_stratified(
        GenericProgram([rule], closed=["blocked"]), facts, limits=ReasoningLimits())
    assert TypedFact("out", ["a", "c"]) in run.result
    assert TypedFact("out", ["a", "d"]) not in run.result
