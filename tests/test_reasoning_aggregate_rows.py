"""Aggregates consume logical body rows, not distinct source values."""

import pytest

from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import (
    AggregateRule, Aggregation, GenericEngine, GenericProgram, TypedAtom, TypedFact,
)


X, Y, Z, N, S = map(Variable, ("x", "y", "z", "n", "s"))


@pytest.mark.parametrize("stratified", [False, True])
@pytest.mark.parametrize("shape,grouped", [("single", False), ("repeated", False),
                                            ("joined", False), ("joined", True)])
def test_equal_source_values_keep_distinct_full_body_bindings(stratified, shape, grouped):
    atom = TypedAtom("p", [X, Y])
    body = [atom]
    if shape == "repeated":
        body.append(atom)
    elif shape == "joined":
        body.append(TypedAtom("q", [X, Z]))
    group = [X] if grouped else []
    rule = AggregateRule("stats", body, group,
                         [Aggregation("count", Y, N), Aggregation("sum", Y, S)],
                         TypedAtom("stats", [*group, N, S]))
    facts = [TypedFact("p", ["a", Literal("integer", 7)]),
             TypedFact("p", ["b", Literal("integer", 7)]),
             TypedFact("q", ["a", "alpha"]), TypedFact("q", ["a", "beta"]),
             TypedFact("q", ["b", "gamma"])]
    engine = GenericEngine()
    program = GenericProgram([], [rule])
    if stratified:
        result = engine.evaluate_stratified(program, facts, limits=ReasoningLimits()).result
    else:
        result = engine.evaluate(program, facts, limits=ReasoningLimits())
    if grouped:
        expected = [("a", 2), ("b", 1)]
    else:
        expected = [(None, 3 if shape == "joined" else 2)]
    output = {fact for fact in result.facts if fact.predicate == "stats"}
    assert output == {TypedFact("stats", [*([entity] if grouped else []),
                                          Literal("integer", count), Literal("integer", 7 * count)])
                      for entity, count in expected}
