"""Aggregate rules and their terminal round obey shared evaluation budgets."""

import pytest

from gestaltdb.reasoning.model import ReasoningLimitError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import (
    AggregateRule, Aggregation, GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact,
)


X, N = Variable("x"), Variable("n")


def program(positive=False):
    rules = [GenericRule("copy", [TypedAtom("p", [X])], [], TypedAtom("q", [X]))] if positive else []
    aggregates = [AggregateRule(str(i), [TypedAtom("q" if positive else "p", [X])], [],
                                [Aggregation("count", X, N)], TypedAtom(f"out{i}", [N]))
                  for i in range(2)]
    return GenericProgram(rules, aggregates)


@pytest.mark.parametrize("stratified", [False, True])
@pytest.mark.parametrize("positive", [False, True])
def test_aggregate_rules_count_toward_max_rules(stratified, positive):
    evaluate = GenericEngine().evaluate_stratified if stratified else GenericEngine().evaluate
    required = 3 if positive else 2
    with pytest.raises(ReasoningLimitError, match="max_rules"):
        evaluate(program(positive), [TypedFact("p", ["a"])], limits=ReasoningLimits(max_rules=required - 1))
    evaluate(program(positive), [TypedFact("p", ["a"])], limits=ReasoningLimits(max_rules=required))


@pytest.mark.parametrize("stratified", [False, True])
def test_aggregate_round_requires_remaining_iteration_allowance(stratified):
    evaluate = GenericEngine().evaluate_stratified if stratified else GenericEngine().evaluate
    with pytest.raises(ReasoningLimitError, match="max_iterations"):
        evaluate(program(True), [TypedFact("p", ["a"])], limits=ReasoningLimits(max_iterations=1))
    run = evaluate(program(True), [TypedFact("p", ["a"])], limits=ReasoningLimits(max_iterations=2))
    result = run.result if stratified else run
    assert result.iterations == 2
    assert len([fact for fact in result.facts if fact.predicate.startswith("out")]) == 2


@pytest.mark.parametrize("stratified", [False, True])
def test_all_aggregate_rules_share_one_terminal_round(stratified):
    evaluate = GenericEngine().evaluate_stratified if stratified else GenericEngine().evaluate
    run = evaluate(program(), [TypedFact("p", ["a"])], limits=ReasoningLimits(max_iterations=1))
    result = run.result if stratified else run
    assert result.iterations == 1
